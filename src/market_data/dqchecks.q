/ dqchecks.q - data-quality checks on market data: can the quotes table
/ itself be trusted? Each check returns one row per (entity, check) with a
/ status column, sorted so whatever needs attention sorts first, and
/ summarize_checks folds any number of them into one "what needs attention"
/ report. The same report-a-table-of-breaks shape as positions.q's
/ reconcile_trades.
/ .
/ DATA QUALITY ONLY. A limit a desk sets on its own activity - a position,
/ a currency exposure, a reject rate - is business logic, and belongs to
/ limits.q (.qf.port). These checks ask whether the data is fit to use, not
/ whether the desk is inside its limits. The position, exposure and
/ reject-ratio limit checks that used to live here, on a limit engine of
/ their own, are now ordinary .qf.port metrics (#412): two engines had
/ given different answers to the same question - one reported a missing
/ limit as a gap, the other left it unpoliced.
/ .
/ Every other module in this library throws immediately on a bad input
/ (require_depth_quotes, ccy_pair_symbol's length checks, ...). This one
/ never throws for a finding - a crossed book, a stale quote - since the
/ whole point is to surface many problems in one report rather than stop
/ at the first. It still throws for genuinely malformed input (a quotes
/ table missing required columns), same as everywhere else.
/ .
/ Requires microstructure.q (spread_bps, for check_market_data_quality) and
/ forwards.q (require_depth_quotes) to be loaded first.

\d .

/ Per-row market data sanity, built on microstructure.q's own spread_bps
/ (no re-derivation of top-of-book/mid here) - a negative spread means the
/ book is crossed (bid strictly above the ask - a locked book, bid = ask,
/ is not - a genuine data error, not just a quality concern; the rule is
/ .qf.mkt.crossed_top's, shared with the backfill gates, #1176), a spread wider than max_spread_bps is flagged
/ separately (thin/stale liquidity or a bad print, not necessarily wrong,
/ but worth a human's attention) so the two very different severities
/ don't collapse into one generic "bad" bucket.
/ .
/ A side whose best level is not usable (.qf.mkt.level_ok: a zero, negative,
/ null or infinite price) is ABSENT too, so a zero or negative ask is
/ `one_sided - flagged, not ok (#1200); all statuses and spread_bps come from
/ the same .qf.mkt.top_sides tops, with null spread_bps for such a row.
/ A side nobody quoted is ABSENT, not wrong: its spread is null, and a null
/ is neither crossed nor wide - it is `one_sided. Written the obvious way,
/ `spreads<0` called it crossed, because q orders a null below every number,
/ so a quote with an ask and no bid read as a data error (#546). .qf.mkt.crossed_top
/ owns that guard now, for every path that decides a crossed book.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
/   (see forwards.q's require_depth_quotes) - any row order, needn't be sorted
/ @param max_spread_bps a spread at or below this many bps is `ok; above
/   it (and non-negative) is `wide
/ @return a table time/sym/spread_bps/status (`ok`, `crossed`, `wide`, or
/   `one_sided` when a side is unquoted and the spread is null), sorted
/   status-ascending (`crossed`, `ok`, `one_sided`, `wide` - alphabetical,
/   not a severity ranking; `crossed` rows are the most urgent, so they land
/   first, which is what matters)
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.check_market_data_quality[quotes;5]
.qf.mkt.check_market_data_quality:{[quotes;max_spread_bps]
    .qf.util.require_depth_quotes[`check_market_data_quality;quotes];
    / ONE extraction of the top for every status (#1200): a side whose level 0
    / is not level_ok (zero, negative, null, infinite) is absent, so it is
    / one_sided - never ok, and never judged wide or crossed from a number
    / the rest of the library refuses to call a price.
    depth:quotes`bid_prices`bid_sizes`ask_prices`ask_sizes;
    book:`bid_prices`bid_sizes`ask_prices`ask_sizes!depth;
    t:.qf.mkt.top_sides flip book;
    bid0:t`bid;
    ask0:t`ask;
    one_sided:(null bid0) | null ask0;
    mid:0.5*bid0+ask0;
    spreads:10000*(ask0-bid0)%mid;
    / boolean-indexed status vector rather than a nested $[cond;a;b].
    / KDB-X supports a vector cond, so this is now a style choice rather
    / than a requirement - kept because `crossed`ok`wide idx is the same
    / index-a-status-vector idiom check_stale_quotes/reconcile_trades
    / already use for the 2-way case, generalized to 3 statuses via a
    / 0/1/2 index built from ordinary boolean arithmetic instead of $.
    is_crossed:(not one_sided) & .qf.mkt.crossed_top flip book;
    is_wide:spreads>max_spread_bps;
    / 0 crossed, 1 ok, 2 wide - and 3 one_sided, which overrides: a null
    / spread is never crossed (guarded above) or wide (null>x is false).
    idx:((1-is_crossed)*(1+is_wide)*not one_sided)+3*one_sided;
    result:([] time:quotes`time; sym:quotes`sym; spread_bps:spreads);
    result:update status:`crossed`ok`wide`one_sided idx from result;
    `status xasc result};

/ Per-sym staleness: how long ago was the last quote at or before as_of,
/ for every sym currently present in quotes? Scoped to syms quotes
/ actually has at least one row for - detecting a sym that should be
/ quoting but never ticks at all needs live process/feed monitoring (see
/ lib/torq/code/dqc/tableticking.q for that different, complementary
/ concern), which this pure-function library has no way to observe.
/ @param quotes table `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes -
/   any row order, needn't be sorted
/ @param as_of only consider quotes at or before this time
/ @param max_age a gap at or below this is `ok; above it is `stale
/ @return a table sym/last_ts/age/status (`ok` or `stale`), sorted
/   status-ascending (`stale` sorts before `ok`). A sym with rows but no
/   timed quote at or before as_of (all times null) is `stale`, with null
/   last_ts and age (#1277)
/ @throws error if quotes is missing a required column
/ @eg .qf.mkt.check_stale_quotes[quotes;.z.p;0D00:00:05]
.qf.mkt.check_stale_quotes:{[quotes;as_of;max_age]
    .qf.util.require_depth_quotes[`check_stale_quotes;quotes];
    / by before from, where after from - the canonical qSQL clause order,
    / kept because a reordered clause reads as a typo to anyone scanning it.
    / Compared through a local, never `where time<=as_of`: this tree's
    / derived tables (superbook, arbitrage) carry an `as_of` column, and
    / inside a where clause the column shadows the parameter - every row
    / would be compared with its own as_of instead of the cutoff.
    cutoff:as_of;
    / `max`, not `last`: the latest quote time whatever the row order. `last`
    / read the time of the last ROW, so a merged feed or an RDB+HDB union
    / out of order called a sym stale that had quoted seconds ago (#589).
    / A null time is not a quote time: it passes `time<=cutoff` (null sorts
    / below everything), and a sym with only null times would get last_ts
    / -0Wp and an age that overflows negative, reading `ok (#1277). So null
    / rows are excluded here, and a sym whose rows are ALL untimed is added
    / back with a null last_ts, which is `stale: no timed quote at or before
    / the cutoff. (Syms with only quotes after the cutoff stay absent.)
    latest:select last_ts:max time by sym from quotes where not null time, time<=cutoff;
    untimed:exec distinct sym from quotes where null time;
    untimed:untimed except exec sym from latest;
    result:([] sym:(exec sym from latest),untimed; last_ts:(exec last_ts from latest),(count untimed)#0Np);
    result:update age:cutoff-last_ts from result;
    / a null age (no timed quote) is stale; null>max_age is false, so say so.
    result:update status:`ok`stale (null last_ts) | age>max_age from result;
    / xdesc: `stale` before `ok`, as documented - xasc sorted them alphabetically.
    `status xdesc result};

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
/ @eg .qf.mkt.summarize_checks[((`stale;.qf.mkt.check_stale_quotes[quotes;.z.p;0D00:00:05]);(`spread;.qf.mkt.check_market_data_quality[quotes;5]))]
.qf.mkt.summarize_checks:{[named_checks]
    rows:raze {[pair]
        check_name:pair 0; t:pair 1;
        bad:select from t where status<>`ok;
        if[0=count bad; :0#([] check:`symbol$(); status:`symbol$(); detail:`char$())];
        ([] check:count[bad]#check_name; status:bad`status; detail:.qf.util.full each bad)
    } each named_checks;
    rows};

\d .
