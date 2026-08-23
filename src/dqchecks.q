/ dqchecks.q - data quality / risk limit checks that turn other modules'
/ computations into an "is this actually fine?" report: one row per
/ (entity, check), a status column, sorted so whatever needs attention
/ sorts first. Mirrors positions.q's reconcile_trades - the same
/ report-a-table-of-breaks shape, generalized beyond just book-vs-reference
/ reconciliation to per-entity threshold checks (position/currency-exposure/
/ VaR limits) and quotes-table sanity (crossed books, outlier spreads,
/ stale quotes).
/ .
/ Deliberately doesn't overlap with what other modules already do:
/ positions.q's reconcile_trades already covers "does my book match a
/ reference" - this module never re-derives a position book itself, it
/ only ever checks a caller-supplied metric/exposure/spread against a
/ caller-supplied limit. Every src/*.q function elsewhere in this library
/ throws immediately on a bad input (require_quotes_cols, ccy_pair_symbol's
/ length checks, ...) - this module is the opposite: it never throws for a
/ business-level problem (a breached limit, a stale quote), since the
/ whole point is to surface many possible problems in one report rather
/ than stopping at the first one. It still throws for genuinely malformed
/ input (a quotes table missing required columns), same as everywhere else.
/ .
/ Requires microstructure.q (spread_bps, for check_market_data_quality) and
/ forwards.q (require_quotes_cols) to be loaded first.

\d .qdqc

/ Private: metric vs. limit -> a 3-way status. `unmonitored (limit is null)
/ is deliberately distinct from `ok - a limit nobody configured is itself
/ an actionable gap, not a silent pass; surfacing it is the point of a
/ dedicated status rather than defaulting an unset limit to "no limit" (=
/ always ok) or "zero limit" (= always breached), either of which would
/ hide the real issue (nobody decided a limit for this entity yet).
/ @param metric the quantity being checked (already abs'd/signed however
/   the caller wants it interpreted - this only ever compares against a
/   non-negative limit)
/ @param limit the configured limit for this entity, or 0Nf if unconfigured
/ @return `ok, `breach, or `unmonitored
status_for:{[metric;limit] $[null limit;`unmonitored;metric>limit;`breach;`ok]};

/ Private: the configured limit for one entity out of a limits table - 0Nf
/ if that entity has no row in limits. Bracket-indexes limits by key_col
/ (a column-name symbol, e.g. `sym or `ccy) rather than using a qSQL
/ `where` clause, so the entity-id parameter can be named anything without
/ the "local variable shadows the table's own column of the same name"
/ hazard a `where col=col`-style clause would hit if they were named alike
/ (see microstructure.q's quotes_for_sym, which sidesteps the same hazard
/ by naming its parameter target_sym rather than sym).
/ @param limits a table with an entity-id column (named per key_col) and a
/   `limit` column
/ @param key_col the entity-id column's name, e.g. `sym or `ccy
/ @param id the entity to look up
/ @return limits' configured limit for id, or 0Nf if id isn't in limits
limit_for:{[limits;key_col;id]
    ids:limits[key_col];
    matches:where ids=id;
    $[0=count matches; 0Nf; first limits[`limit] matches]};

/ Generic per-entity threshold check: is each row's metric within its
/ configured limit? The shared engine every named check below (position
/ notional, currency exposure, and - by direct use, no wrapper needed -
/ VaR) is a thin wrapper over: build entity/metric, call this.
/ @param metrics a table with an entity-id column (named per key_col) and
/   a `metric` column - already abs'd/signed however the caller wants it
/   interpreted, see status_for
/ @param limits a table with the same entity-id column and a `limit`
/   column; an entity present in metrics but missing from limits gets
/   status `unmonitored (see status_for)
/ @param key_col the shared entity-id column's name, e.g. `sym or `ccy
/ @return metrics with a `limit` and `status` column appended (`ok`,
/   `breach`, or `unmonitored`), sorted status-ascending (`breach` sorts
/   before `ok` and `unmonitored` alphabetically, so the actionable rows
/   land first)
/ @eg .qdqc.check_limit[([] sym:`EURUSD`GBPUSD; metric:1200000 400000f); ([] sym:enlist `EURUSD; limit:enlist 1000000f); `sym]
check_limit:{[metrics;limits;key_col]
    result:0!metrics;
    ids:result[key_col];
    / .qdqc.limit_for/.qdqc.status_for, not bare - confirmed under real
    / KDB-X that an update clause's per-row expression resolves bare
    / same-namespace function names against the root context, not the
    / enclosing function's own namespace, so an unqualified reference
    / here throws even though the exact same bare call works fine outside
    / a select/update clause.
    result:update limit:.qdqc.limit_for[limits;key_col;] each ids from result;
    result:update status:.qdqc.status_for'[metric;limit] from result;
    `status xasc result};

/ Position notional (abs qty, matching risk.q's pnl's own notional
/ convention - see positions.q's unrealized_pnl) vs. a per-sym limit -
/ check_limit specialized to a position book's own shape, so callers don't
/ have to hand-build the entity/metric table themselves.
/ @param pos a position book (see positions.q's empty_book)
/ @param limits a table `sym`limit - the max notional (abs qty, base
/   currency units) tolerated per sym
/ @return see check_limit - columns sym/metric/limit/status
/ @eg .qdqc.check_position_notional_limits[pos;([] sym:enlist `EURUSD; limit:enlist 1000000f)]
check_position_notional_limits:{[pos;limits]
    metrics:([] sym:exec sym from pos; metric:abs exec qty from pos);
    check_limit[metrics;limits;`sym]};

/ Net currency exposure (see positions.q's ccy_exposure_in, already
/ revalued into one reporting currency - a limit only means anything once
/ every currency's exposure is expressed the same way) vs. a per-currency
/ limit, stated in that same reporting currency - check_limit specialized
/ to ccy_exposure_in's own output shape.
/ @param exposure ccy_exposure_in's output - a table with at least
/   ccy/reporting_amount columns
/ @param limits a table `ccy`limit - the max net exposure (in the same
/   reporting currency exposure was revalued into) tolerated per currency
/ @return see check_limit - columns ccy/metric/limit/status
/ @eg .qdqc.check_ccy_exposure_limits[.qpos.ccy_exposure_in[pos;quotes;`USD;.z.p];([] ccy:enlist `EUR; limit:enlist 5000000f)]
check_ccy_exposure_limits:{[exposure;limits]
    metrics:([] ccy:exposure`ccy; metric:abs exposure`reporting_amount);
    check_limit[metrics;limits;`ccy]};

/ Per-row market data sanity, built on microstructure.q's own spread_bps
/ (no re-derivation of top-of-book/mid here) - a negative spread means the
/ book is crossed (bid at or through the ask, a genuine data error, not
/ just a quality concern), a spread wider than max_spread_bps is flagged
/ separately (thin/stale liquidity or a bad print, not necessarily wrong,
/ but worth a human's attention) so the two very different severities
/ don't collapse into one generic "bad" bucket.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/   (see forwards.q's require_quotes_cols) - any row order, needn't be sorted
/ @param max_spread_bps a spread at or below this many bps is `ok; above
/   it (and non-negative) is `wide
/ @return a table ts/sym/spread_bps/status (`ok`, `crossed`, or `wide`),
/   sorted status-ascending (`crossed` sorts first, then `ok`, then `wide`
/   - see the note on sort order in check_limit; `crossed` rows are the
/   most urgent, so this is the right order even though it isn't a strict
/   severity ranking for all three)
/ @throws error if quotes is missing a required column
/ @eg .qdqc.check_market_data_quality[quotes;5]
check_market_data_quality:{[quotes;max_spread_bps]
    .qfwd.require_quotes_cols[`check_market_data_quality;quotes];
    spreads:.qmicro.spread_bps[quotes`bid_prices;quotes`ask_prices];
    / boolean-indexed status vector, not nested vectorized $[cond;a;b] -
    / PeachQ's $ only handles a scalar cond, not a per-row cond vector
    / (confirmed: throws 'type on a vector cond even though real kdb+
    / supports it) - `crossed`ok`wide idx is the same
    / index-a-status-vector idiom check_stale_quotes/reconcile_trades
    / already use for the 2-way case, generalized to 3 statuses via a
    / 0/1/2 index built from ordinary boolean arithmetic instead of $.
    is_crossed:spreads<0;
    is_wide:spreads>max_spread_bps;
    idx:(1-is_crossed)*(1+is_wide);
    result:([] ts:quotes`ts; sym:quotes`sym; spread_bps:spreads);
    result:update status:`crossed`ok`wide idx from result;
    `status xasc result};

/ Per-sym staleness: how long ago was the last quote at or before at_time,
/ for every sym currently present in quotes? Scoped to syms quotes
/ actually has at least one row for - detecting a sym that should be
/ quoting but never ticks at all needs live process/feed monitoring (see
/ lib/torq/code/dqc/tableticking.q for that different, complementary
/ concern), which this pure-function library has no way to observe.
/ @param quotes table `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes,
/   sorted `sym`ts xasc (same as every other as-of lookup in this library
/   - see forwards.q's cross_book_at)
/ @param at_time only consider quotes at or before this time
/ @param max_age a gap at or below this is `ok; above it is `stale
/ @return a table sym/last_ts/age/status (`ok` or `stale`), sorted
/   status-ascending (`stale` sorts before `ok`)
/ @throws error if quotes is missing a required column
/ @eg .qdqc.check_stale_quotes[quotes;.z.p;0D00:00:05]
check_stale_quotes:{[quotes;at_time;max_age]
    .qfwd.require_quotes_cols[`check_stale_quotes;quotes];
    / by before from, where after from - PeachQ's parser requires this
    / canonical clause order; real kdb+ tolerates from/by reordered but
    / PeachQ (this repo's other targeted interpreter) does not.
    latest:select last_ts:last ts by sym from quotes where ts<=at_time;
    result:([] sym:exec sym from latest; last_ts:exec last_ts from latest);
    result:update age:at_time-last_ts from result;
    result:update status:`ok`stale (age>max_age) from result;
    `status xasc result};

/ Flattens a list of already-run check tables (any of the above, or a
/ caller's own) into one "what needs attention" report - every row whose
/ status isn't `ok, human-readable, across however many different checks
/ were run. Deliberately doesn't try to normalize the different checks'
/ columns into one shared shape (a position-limit breach's notional isn't
/ comparable to a market-data check's spread_bps) - detail is a rendered
/ string of the whole offending row instead, so nothing about why it
/ triggered is lost.
/ @param named_checks a list of (check_name;check_table) pairs - each
/   check_table must have a `status` column, whatever else it has is
/   folded into detail as-is
/ @return a table check/status/detail, one row per non-`ok row across
/   every check supplied, in the order given
/ @eg .qdqc.summarize_checks[((`position_limits;.qdqc.check_position_notional_limits[pos;limits]);(`spread;.qdqc.check_market_data_quality[quotes;5]))]
summarize_checks:{[named_checks]
    rows:raze {[pair]
        check_name:pair 0; t:pair 1;
        bad:select from t where status<>`ok;
        if[0=count bad; :0#([] check:`symbol$(); status:`symbol$(); detail:`char$())];
        ([] check:count[bad]#check_name; status:bad`status; detail:.Q.s1 each bad)
    } each named_checks;
    rows};

\d .
