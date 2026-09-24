/ dqchecks.q - data-quality checks on market data: can the quotes table
/ itself be trusted? Each check returns one row per (entity, check) with a
/ status column, sorted so whatever needs attention sorts first, and
/ summarize_checks folds any number of them into one "what needs attention"
/ report. The same report-a-table-of-breaks shape as positions.q's
/ reconcile_trades.
/ .
/ DATA QUALITY ONLY. A limit a desk sets on its own activity - a position,
/ a currency exposure, a reject rate - is business logic, and belongs to
/ limits.q (.qlimit). These checks ask whether the data is fit to use, not
/ whether the desk is inside its limits. The position, exposure and
/ reject-ratio limit checks that used to live here, on a limit engine of
/ their own, are now ordinary .qlimit metrics (#412): two engines had
/ given different answers to the same question - one reported a missing
/ limit as a gap, the other left it unpoliced.
/ .
/ Every other module in this library throws immediately on a bad input
/ (require_quotes_cols, ccy_pair_symbol's length checks, ...). This one
/ never throws for a finding - a crossed book, a stale quote - since the
/ whole point is to surface many problems in one report rather than stop
/ at the first. It still throws for genuinely malformed input (a quotes
/ table missing required columns), same as everywhere else.
/ .
/ Requires microstructure.q (spread_bps, for check_market_data_quality) and
/ forwards.q (require_quotes_cols) to be loaded first.

\d .qdqc

/ Per-row market data sanity, built on microstructure.q's own spread_bps
/ (no re-derivation of top-of-book/mid here) - a negative spread means the
/ book is crossed (bid at or through the ask, a genuine data error, not
/ just a quality concern), a spread wider than max_spread_bps is flagged
/ separately (thin/stale liquidity or a bad print, not necessarily wrong,
/ but worth a human's attention) so the two very different severities
/ don't collapse into one generic "bad" bucket.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/   (see forwards.q's require_quotes_cols) - any row order, needn't be sorted
/ @param max_spread_bps a spread at or below this many bps is `ok; above
/   it (and non-negative) is `wide
/ @return a table time/sym/spread_bps/status (`ok`, `crossed`, or `wide`),
/   sorted status-ascending (`crossed` sorts first, then `ok`, then `wide`
/   - alphabetical, not a severity ranking; `crossed` rows are the most
/   urgent, so they land first, which is what matters)
/ @throws error if quotes is missing a required column
/ @eg .qdqc.check_market_data_quality[quotes;5]
check_market_data_quality:{[quotes;max_spread_bps]
    .qfwd.require_quotes_cols[`check_market_data_quality;quotes];
    spreads:.qmicro.spread_bps[quotes`bid_prices;quotes`ask_prices];
    / boolean-indexed status vector rather than a nested $[cond;a;b].
    / KDB-X supports a vector cond, so this is now a style choice rather
    / than a requirement - kept because `crossed`ok`wide idx is the same
    / index-a-status-vector idiom check_stale_quotes/reconcile_trades
    / already use for the 2-way case, generalized to 3 statuses via a
    / 0/1/2 index built from ordinary boolean arithmetic instead of $.
    is_crossed:spreads<0;
    is_wide:spreads>max_spread_bps;
    idx:(1-is_crossed)*(1+is_wide);
    result:([] time:quotes`time; sym:quotes`sym; spread_bps:spreads);
    result:update status:`crossed`ok`wide idx from result;
    `status xasc result};

/ Per-sym staleness: how long ago was the last quote at or before at_time,
/ for every sym currently present in quotes? Scoped to syms quotes
/ actually has at least one row for - detecting a sym that should be
/ quoting but never ticks at all needs live process/feed monitoring (see
/ lib/torq/code/dqc/tableticking.q for that different, complementary
/ concern), which this pure-function library has no way to observe.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes,
/   sorted `sym`time xasc (same as every other as-of lookup in this library
/   - see forwards.q's cross_book_at)
/ @param at_time only consider quotes at or before this time
/ @param max_age a gap at or below this is `ok; above it is `stale
/ @return a table sym/last_ts/age/status (`ok` or `stale`), sorted
/   status-ascending (`stale` sorts before `ok`)
/ @throws error if quotes is missing a required column
/ @eg .qdqc.check_stale_quotes[quotes;.z.p;0D00:00:05]
check_stale_quotes:{[quotes;at_time;max_age]
    .qfwd.require_quotes_cols[`check_stale_quotes;quotes];
    / by before from, where after from - the canonical qSQL clause order,
    / kept because a reordered clause reads as a typo to anyone scanning it.
    latest:select last_ts:last time by sym from quotes where time<=at_time;
    result:([] sym:exec sym from latest; last_ts:exec last_ts from latest);
    result:update age:at_time-last_ts from result;
    result:update status:`ok`stale (age>max_age) from result;
    `status xasc result};

/ Flattens a list of already-run check tables (any of the above, or a
/ caller's own) into one "what needs attention" report - every row whose
/ status isn't `ok, human-readable, across however many different checks
/ were run. Deliberately doesn't try to normalize the different checks'
/ columns into one shared shape (a stale quote's age isn't comparable to a
/ crossed book's spread_bps) - detail is a rendered string of the whole
/ offending row instead, so nothing about why it triggered is lost.
/ @param named_checks a list of (check_name;check_table) pairs - each
/   check_table must have a `status` column, whatever else it has is
/   folded into detail as-is
/ @return a table check/status/detail, one row per non-`ok row across
/   every check supplied, in the order given
/ @eg .qdqc.summarize_checks[((`stale;.qdqc.check_stale_quotes[quotes;.z.p;0D00:00:05]);(`spread;.qdqc.check_market_data_quality[quotes;5]))]
summarize_checks:{[named_checks]
    rows:raze {[pair]
        check_name:pair 0; t:pair 1;
        bad:select from t where status<>`ok;
        if[0=count bad; :0#([] check:`symbol$(); status:`symbol$(); detail:`char$())];
        ([] check:count[bad]#check_name; status:bad`status; detail:.Q.s1 each bad)
    } each named_checks;
    rows};

\d .
