/ markout_core.q - the cross-asset markout core (.qpipe.transform.markout_core, #1929).
/ .
/ WHAT THIS IS. One function of two canonical inputs - `executions` and
/ `market_data` - that scores every execution at every declared horizon: where
/ the reference stood at the execution and at the horizon, how far the market
/ moved in the fill's favour in price units, what that is worth in the
/ instrument's quote currency, and what it is worth in a reporting currency.
/ It is the contract docs/architecture/markout-contract.md fixes (#1923), and
/ every domain (FX, crypto, equity) plugs into it rather than copying it.
/ .
/ WHAT IT DOES NOT KNOW. No source, no vendor, no table and no asset class.
/ How an instrument is priced - its quote currency and the market_data legs its
/ reference is chained from - is a DOMAIN's: the core calls the domain's
/ resolver and never parses a symbol. tests/q/test_markout_core.q refuses this
/ file if a name from an implementation appears in it.
/ .
/ A DOMAIN is a dictionary of functions (open question 15: an argument, so the
/ spec stays plain data a job can declare and serialise):
/ .
/   resolve    required  {[spec;syms]} -> table sym, ok, quote_ccy, legs, invert,
/                        one row per sym, in order. legs is the market_data syms
/                        the reference is chained from, in chain order; invert
/                        aligns with legs. Pure: a function of the instrument and
/                        the spec, never of which books exist, so readiness and
/                        scoring agree.
/   convert    required  {[spec;quote_ccys;report_ccy]} -> table ok, leg, invert,
/                        one row per quote currency: the market_data sym whose
/                        book gives report_ccy per quote_ccy, and whether it is
/                        inverted to do so.
/   keys       required  the spec keys the domain owns (symbols, possibly none).
/   validate   optional  {[spec]} -> spec, throwing on a domain key it refuses.
/   available  optional  {[spec;frame]} -> a boolean per (execution, horizon):
/                        0b marks the pair unpriced for a domain reason.
/   attribute  optional  {[spec;frame;legs;books]} -> table ri, kind, step, move
/                        and optionally status: extra component rows (FX's legs
/                        and residual). ri indexes the frame; books is the dict
/                        at0, at1 of per-leg tables bid, ask (oriented), mid
/                        (the leg's own) at the execution and at the horizon. The
/                        core adds the money, the conversion and the statuses.
/ .
/ THE RESULT. One long table, one row per (execution, horizon, component), with
/ the contract's core columns in the contract's order (result below) and kind and
/ step after horizon. The core's own components are `total` (side*(ref_t1-price))
/ and `execution` (side*(ref_t0-price)); a domain adds more through attribute.
/ (venue; fill_id; horizon; kind; step) is the key, with no nulls in it.
/ .
/ CLOCKS. An execution is scored only against references on its own clock; there
/ is no override. A leg whose only book within max_age is on another clock makes
/ the row `clock_mismatch`; an execution whose clock is null, `unknown or not a
/ clock at all is excluded with the reason no_clock.
/ .
/ PROPOSED, NOT APPROVED. The contract's open questions are not decided. Every
/ choice that depends on one lives in exactly one name below, so a different
/ answer is a change to that name and nowhere else:
/ .
/   1 2 3  core_cols and long_key_cols (the column list is `result`)
/   5      contract_version, and the name of that column in `result`
/   6      the report_amount line of `finish`
/   7      the rates spec key (core_keys)
/   8      required_keys
/   9      rate_max_age
/   10     rate_same_clock
/   11     legs_of (the conversion leg joins the readiness legs)
/   12     mismatch_status
/   13     exclude_bad_size
/   14     refuse_undeclared
/   15     the domain argument of calc and declare
/ .
/ The transform is verified by its examples (worked by hand) in the suite.

\d .

/ The executions columns the core reads. clock is #1925's: `venue or `receipt.
.qpipe.transform.markout_core.executions_in:([] source_time:`timestamp$(); venue:`symbol$(); sym:`symbol$(); side:`long$(); size:`float$(); price:`float$(); fill_id:`symbol$(); clock:`symbol$())

/ The market_data columns the core reads: the sources' books, one row per snapshot, and the clock each is stamped on.
.qpipe.transform.markout_core.market_data_in:([] source_time:`timestamp$(); sym:`symbol$(); source:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:(); clock:`symbol$())

/ The names a declared markout transform reads its two inputs under. They are not
/ `executions` and `market_data` yet: an input with a plant table's name is held
/ to that table's columns (tests/q/test_plant_tables.q), and clock is #1925's.
/ Once the plant carries clock these can take the plant's names; the transform
/ takes its inputs positionally, so nothing else changes.
.qpipe.transform.markout_core.input_names:`markout_executions`markout_market_data

/ The contract version (open question 5: a long starting at 1, apart from FX's
/ own `version`). A change to anything the contract fixes bumps it.
.qpipe.transform.markout_core.contract_version:1

/ The clocks a row may be on. Anything else - null, `unknown, a typo - is never scored.
.qpipe.transform.markout_core.clocks:`venue`receipt

/ The contract's core columns, in the contract's order (open question 1: a
/ subsequence, with domain columns between them). kind and step are
/ long_key_cols: required in every long dataset, not core (open question 3).
.qpipe.transform.markout_core.core_cols:`time`source_time`clock`venue`fill_id`sym`side`size`horizon`price`quote_ccy`ref_t0`ref_t1`move`quote_amount`report_ccy`rate`rate_source`rate_sym`rate_time`rate_timing`report_amount`status`contract_version

/ The long layout's two extra columns, placed after horizon.
.qpipe.transform.markout_core.long_key_cols:`kind`step

/ The core's result: one row per (execution, horizon, component).
.qpipe.transform.markout_core.result:([] time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`symbol$(); side:`long$(); size:`float$();
    horizon:`timespan$(); kind:`symbol$(); step:`long$(); price:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); quote_amount:`float$();
    report_ccy:`symbol$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); rate_timing:`symbol$(); report_amount:`float$();
    status:`symbol$(); contract_version:`long$())

/ The status a row takes when its only reference is on another clock (open
/ question 12). Set it to `unavailable to count the mismatch in the run's facts
/ instead of naming it on the row.
.qpipe.transform.markout_core.mismatch_status:`clock_mismatch

/ Refuse an execution with a null size (open question 13). Off: today's FX
/ scoring keeps such a row, and money for it is null.
.qpipe.transform.markout_core.exclude_bad_size:0b

/ Refuse a spec key neither the core nor the domain declares (open question 14).
.qpipe.transform.markout_core.refuse_undeclared:1b

/ Must the conversion book be on the execution's clock too (open question 10)?
/ Yes: a missing same-clock rate is `no_rate.
.qpipe.transform.markout_core.rate_same_clock:1b

/ The oldest a conversion book may be and still set a rate (open question 9).
/ @param spec the validated spec
/ @return a timespan: the spec's max_age; a separate rate age would be another spec key
/ @eg .qpipe.transform.markout_core.rate_max_age[enlist[`max_age]!enlist 0D00:00:05]  ->  0D00:00:05
.qpipe.transform.markout_core.rate_max_age:{[spec] spec`max_age}

/ The source name a rate carries when it is declared, not read from a book.
.qpipe.transform.markout_core.fixed_source:`fixed

/ ------------------------------------------------------------------- SPEC

/ The keys the core owns, and the ones a spec must carry (open questions 7, 8).
.qpipe.transform.markout_core.core_keys:`horizons`max_age`report_ccy`rates`rate_timing
.qpipe.transform.markout_core.required_keys:`horizons`report_ccy`rate_timing

/ What a spec leaves out: max_age is the shared .qf.mkt.reference_max_age (#1410),
/ and no rates means every quote currency but the reporting one is `no_rate.
.qpipe.transform.markout_core.spec_defaults:`max_age`rates!(.qf.mkt.reference_max_age;(`symbol$())!())

/ A rate source: one declared in the spec's rates, as `source`sym (read from market_data) or a float (fixed).
/ @param entry a value of the spec's rates
/ @return `market, `fixed or ` for neither
/ @eg .qpipe.transform.markout_core.rate_kind[`a`USDJPY]  ->  `market
.qpipe.transform.markout_core.rate_kind:{[entry]
    $[11h=type entry; $[(2=count entry) and not any null entry; `market; `];
      -9h=type entry; $[(entry>0f) and entry<0w; `fixed; `];
      `]}

/ Refuse a spec the core cannot honour, naming the first problem, and complete it.
/ .
/ The core's keys are checked here and the domain's by its own validate. A
/ conversion sym the domain's convert refuses is a spec error, so a misspelt
/ pair fails at declaration instead of becoming `no_rate on every row.
/ @param domain the domain dictionary
/ @param spec dict horizons, report_ccy, rate_timing and optionally max_age, rates and the domain's keys
/ @return the spec with the defaults filled in
/ @throws error naming what is wrong
/ @eg .qpipe.transform.markout_core.validate[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec]`report_ccy  ->  `USD
.qpipe.transform.markout_core.validate:{[domain;spec]
    if[not 99h=type spec; '"markout_core: spec must be a dictionary"];
    if[not (99h=type domain) and all `resolve`convert`keys in key domain;
        '"markout_core: a domain must carry resolve, convert and keys"];
    miss:.qpipe.transform.markout_core.required_keys except key spec;
    if[count miss; '"markout_core: spec is missing ",", " sv string miss];
    extra:(key spec) except .qpipe.transform.markout_core.core_keys,domain`keys;
    if[.qpipe.transform.markout_core.refuse_undeclared and count extra;
        '"markout_core: spec has undeclared key(s) ",", " sv string extra];
    spec:.qpipe.transform.markout_core.spec_defaults,spec;
    / the domain completes its own keys first: its conversion reads them (an FX convention) when the rates are checked below
    if[`validate in key domain; spec:domain[`validate] spec];
    hs:spec`horizons;
    if[not (16h=type hs) and 0<count hs; '"markout_core: horizons must be a non-empty list of timespans, e.g. 0D00:00:01 0D00:00:10"];
    if[any null hs; '"markout_core: horizons must not contain a null timespan"];
    if[not (count hs)=count distinct hs; '"markout_core: horizons must be distinct"];
    if[not (-16h=type spec`max_age) and spec[`max_age]>0D; '"markout_core: max_age must be a positive timespan, 0Wn for as of however old"];
    if[not (-11h=type spec`report_ccy) and not null spec`report_ccy; '"markout_core: report_ccy must be a non-null symbol"];
    if[not (-11h=type spec`rate_timing) and spec[`rate_timing] in `execution`horizon; '"markout_core: rate_timing must be `execution or `horizon"];
    rt:spec`rates;
    if[not (99h=type rt) and (11h=type key rt) or 0=count rt; '"markout_core: rates must be a dictionary from quote currency to rate source"];
    kinds:.qpipe.transform.markout_core.rate_kind each value rt;
    bad:where null kinds;
    if[count bad; '"markout_core: rates[",string[(key rt) first bad],"] must be `source`sym or a positive, finite float"];
    mk:where kinds=`market;
    {[domain;spec;q;sym]
        c:domain[`convert][spec;enlist q;spec`report_ccy];
        if[not (first c`ok) and sym=first c`leg;
            '"markout_core: rates[",string[q],"]: the domain's conversion does not accept ",string sym]
      }[domain;spec] ./: flip ((key rt) mk;last each (value rt) mk);
    spec}

/ ------------------------------------------------------------- RESOLVER

/ Private: the domain's resolution of some instruments, checked for shape and
/ with `usable` added: ok, a quote currency and at least one leg.
/ @private
.qpipe.transform.markout_core.resolution:{[domain;spec;syms]
    r:domain[`resolve][spec;syms];
    if[not (98h=type r) and all `sym`ok`quote_ccy`legs`invert in cols r; '"markout_core: resolve must return a table sym, ok, quote_ccy, legs, invert"];
    if[not (r`sym)~syms; '"markout_core: resolve must return one row per instrument, in order"];
    update usable:ok and (not null quote_ccy) and 0<count each legs from r}

/ Private: the market_data a quote currency's rate is read from - `source`sym -
/ or an empty list: none when it is the reporting currency, undeclared, or fixed.
/ @private
.qpipe.transform.markout_core.market_rate:{[rt;rep;q]
    $[(q<>rep) and (q in key rt) and `market=.qpipe.transform.markout_core.rate_kind[rt q]; rt q; `symbol$()]}

/ The conversion pairs a set of executions is converted through: the market_data
/ sym of every rate read from a book, for the quote currencies the executions
/ are quoted in. A fixed rate, a quote currency equal to the reporting one and
/ one with no declared rate have none. The ONE list the live job's legs callback,
/ the bounded source's symbol filter (#1926's extension point) and the readiness
/ gate (unmet) all draw on.
/ @param domain the domain dictionary
/ @param spec the spec
/ @param pending executions, at least sym
/ @return table quote_ccy, source, sym: one row per rate book the executions need
/ @eg .qpipe.transform.markout_core.conversion_pairs[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;([] sym:enlist `USDJPY)]  ->  ([] quote_ccy:enlist `JPY; source:enlist `a; sym:enlist `USDJPY)
.qpipe.transform.markout_core.conversion_pairs:{[domain;spec;pending]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    r:.qpipe.transform.markout_core.resolution[domain;spec;distinct pending`sym];
    qs:distinct (r`quote_ccy) where r`usable;
    m:.qpipe.transform.markout_core.market_rate[spec`rates;spec`report_ccy;] each qs;
    has:2=count each m;
    ([] quote_ccy:qs where has; source:`symbol$(m[;0]) where has; sym:`symbol$(m[;1]) where has)}

/ The legs readiness waits on, per execution: the resolver's legs, plus the
/ conversion leg when the rate is read from market_data (open question 11). The
/ same list serves the live job's legs callback and keep filter and a bounded
/ source's symbol filter.
/ @param domain the domain dictionary
/ @param spec the spec
/ @param pending executions, at least sym
/ @return a list of symbol lists, one per execution; an unpriceable instrument has none
/ @eg .qpipe.transform.markout_core.legs_of[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;([] sym:enlist `USDJPY)]  ->  enlist enlist `USDJPY
.qpipe.transform.markout_core.legs_of:{[domain;spec;pending]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    u:distinct pending`sym;
    r:.qpipe.transform.markout_core.resolution[domain;spec;u];
    rt:spec`rates;
    conv:{[rt;rep;q] 1_ .qpipe.transform.markout_core.market_rate[rt;rep;q]}[rt;spec`report_ccy];
    per:{[usable;l;c] $[usable; distinct l,c; `symbol$()]}'[r`usable;r`legs;conv each r`quote_ccy];
    per u?pending`sym}

/ What a window still lacks before it can be scored, for a source's `ready` hook
/ (#1927): a list of strings, () when ready.
/ .
/ A route leg with no book is a legitimate `unavailable row, so the route legs are
/ held to the existing rule only: scoreable executions and NO books at all is
/ unmet. A conversion pair is held to more. With no book of the declared source
/ for the pair, every row quoted in that currency would be `no_rate, and a
/ window claimed as covered would keep them for good; so each rate book the
/ window needs must exist (open question 11: the gate asks for the book, not that
/ it reaches the horizon).
/ @param domain the domain dictionary
/ @param spec the spec
/ @param exe the window's executions
/ @param md the window's market_data, at least sym and source
/ @return a list of strings, one per unmet requirement
/ @eg .qpipe.transform.markout_core.unmet[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;0#.qpipe.transform.markout_core.executions_in;0#.qpipe.transform.markout_core.market_data_in]  ->  ()
.qpipe.transform.markout_core.unmet:{[domain;spec;exe;md]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    exe:exe where null .qpipe.transform.markout_core.why[domain;spec;exe];
    if[0=count exe; :()];
    legs:distinct raze .qpipe.transform.markout_core.legs_of[domain;spec;exe];
    if[0=count md; :enlist "market_data holds no books for the ",string[count legs]," pair(s) the window's ",string[count exe]," execution(s) are scored against"];
    cp:.qpipe.transform.markout_core.conversion_pairs[domain;spec;exe];
    if[0=count cp; :()];
    held:{[md;src;sy] any (md[`source]=src) and md[`sym]=sy}[md]'[cp`source;cp`sym];
    gone:cp where not held;
    {[g] "market_data holds no book of ",string[g`source]," for conversion pair ",string[g`sym]," (the rate of ",string[g`quote_ccy],")"} each gone}

/ ------------------------------------------------------------- SCORING

/ Why each execution cannot be scored, or null where it can: no route (the
/ resolver cannot price it), no identity, no time, no clock, a side other than
/ 1 or -1, a price that is not positive and finite, and optionally a null size.
/ @param domain the domain dictionary
/ @param spec the validated spec
/ @param exe executions
/ @return a symbol per execution, null when it can be scored
/ @eg last .qpipe.transform.markout_core.why[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;([] source_time:2#2026.09.17D10:00:00; venue:`a`a; sym:`USDJPY`NOPE; side:1 1; size:2#1f; price:2#150f; fill_id:`x`y; clock:2#`venue)]  ->  `no_route
.qpipe.transform.markout_core.why:{[domain;spec;exe]
    n:count exe;
    syms:distinct exe`sym;
    r:.qpipe.transform.markout_core.resolution[domain;spec;syms];
    routable:(syms!r`usable) exe`sym;
    / the reasons, last to first, so the earliest that applies is the one left
    masks:(
        (`no_route; not routable);
        (`no_identity; (null exe`fill_id) or null exe`venue);
        (`no_time; null exe`source_time);
        (`no_clock; not (exe`clock) in .qpipe.transform.markout_core.clocks);
        (`bad_side; not (exe`side) in 1 -1);
        (`bad_price; not (exe[`price]>0f) and exe[`price]<0w);
        (`bad_size; .qpipe.transform.markout_core.exclude_bad_size and null exe`size));
    f:{[why;m] @[why;where m 1;:;m 0]};
    f/[n#`;reverse masks]}

/ The executions scoring would exclude, with the reason, so a bounded run can
/ count them and a live one can log them.
/ @param domain the domain dictionary
/ @param spec the spec
/ @param exe executions
/ @return table fill_id, venue, reason
/ @eg .qpipe.transform.markout_core.unscoreable[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;([] source_time:enlist 2026.09.17D10:00:00; venue:`a; sym:`NOPE; side:1; size:1f; price:150f; fill_id:`x; clock:`venue)]  ->  ([] fill_id:enlist `x; venue:enlist `a; reason:enlist `no_route)
.qpipe.transform.markout_core.unscoreable:{[domain;spec;exe]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    w:.qpipe.transform.markout_core.why[domain;spec;exe];
    bad:where not null w;
    ([] fill_id:(exe`fill_id) bad; venue:(exe`venue) bad; reason:w bad)}

/ The last delivery of each identity, in the order those deliveries arrived.
/ @param exe executions
/ @return executions, one per (venue; fill_id)
/ @eg count .qpipe.transform.markout_core.last_delivery[([] venue:`a`a`b; fill_id:`x`x`x)]  ->  2
.qpipe.transform.markout_core.last_delivery:{[exe]
    ids:`$string[exe`venue],'"|",'string exe`fill_id;
    exe asc last each value group ids}

/ The level-0 top of each source's book, one row per snapshot: the input of
/ .qf.mkt.best_across_venues. A side the source withdrew is null, which is how a
/ withdrawal outlives the older book.
/ @param md market_data rows
/ @return table time, sym, venue (the source), clock, bid, ask
/ @eg count .qpipe.transform.markout_core.tops[0#.qpipe.transform.markout_core.market_data_in]  ->  0
.qpipe.transform.markout_core.tops:{[md]
    md:md where not null md`source_time;
    s:.qf.mkt.top_sides md;
    ([] time:md`source_time; sym:md`sym; venue:md`source; clock:md`clock; bid:s`bid; ask:s`ask)}

/ One leg's book at each instant, oriented. An absent leg (a route shorter than
/ the longest) is the identity: bid and ask 1, no mid.
/ @param tob tops of market_data
/ @param max_age the oldest a source's book may be and still count
/ @param syms the leg per row, null where the route has no such leg
/ @param inverted whether the route inverts that leg, per row
/ @param times the instant per row
/ @return table bid, ask (oriented), mid (the leg's own, un-oriented)
/ @eg .qpipe.transform.markout_core.leg_at[0#.qpipe.transform.markout_core.tops[0#.qpipe.transform.markout_core.market_data_in];0Wn;enlist `EURUSD;enlist 0b;enlist 2026.09.17D10:00:00]`mid  ->  enlist 0n
.qpipe.transform.markout_core.leg_at:{[tob;max_age;syms;inverted;times]
    n:count syms;
    live:where not null syms;
    b:.qf.mkt.best_across_venues[tob;([] sym:syms live; time:times live);max_age];
    flip_it:inverted live;
    bid:@[n#1f;live;:;?[flip_it;1f%b`ask;b`bid]];
    ask:@[n#1f;live;:;?[flip_it;1f%b`bid;b`ask]];
    mid:@[n#0n;live;:;b`mid];
    ([] bid:bid; ask:ask; mid:mid)}

/ The mid of a chain of oriented legs: half the sum of the products of their bids and asks.
/ @param books a list of tables bid, ask, one per leg
/ @return the chain's mid, a float per row; null when any leg has no book
/ @eg .qpipe.transform.markout_core.chain_mid[enlist ([] bid:1.1 1.2; ask:1.3 1.4)]  ->  1.2 1.3
.qpipe.transform.markout_core.chain_mid:{[books]
    bids:{x`bid} each books;
    asks:{x`ask} each books;
    0.5*(prd bids)+prd asks}

/ Private: every leg's oriented book at the given instants. Rows are grouped by
/ their clock and each group reads only books on that clock; same = 0b reads
/ every book, which is how a mismatch is told from a gap.
/ @private
.qpipe.transform.markout_core.leg_books:{[tob;max_age;lg;times;clk;same]
    n:count times;
    depth:count lg`legs;
    groups:$[same; value group clk; enlist til n];
    one:{[tob;max_age;lg;times;clk;same;idx]
        tb:$[same; select from tob where clock=clk first idx; tob];
        {[tb;max_age;lg;times;idx;k] .qpipe.transform.markout_core.leg_at[tb;max_age;((lg`legs) k) idx;((lg`invs) k) idx;times idx]}[tb;max_age;lg;times;idx] each til count lg`legs
      }[tob;max_age;lg;times;clk;same] each groups;
    / scatter each group's rows back into row order, leg by leg
    {[n;groups;one;k]
        col:{[n;groups;one;k;c] @[n#0n;raze groups;:;raze {[k;c;o] (o k) c}[k;c] each one]}[n;groups;one;k];
        ([] bid:col`bid; ask:col`ask; mid:col`mid)}[n;groups;one] each til depth}

/ Private: apply one patch - the rows, rates, sources, syms and times a group
/ of rows found - to the rate columns.
/ @private
.qpipe.transform.markout_core.patch:{[b;p]
    ix:p 0;
    `rate`rate_source`rate_sym`rate_time!(@[b`rate;ix;:;p 1];@[b`rate_source;ix;:;p 2];@[b`rate_sym;ix;:;p 3];@[b`rate_time;ix;:;p 4])}

/ Private: the patches for the rows whose quote currency is c: a declared fixed
/ rate is one patch; a market rate is one per clock group, from the mid of the
/ oriented conversion book of the declared source at the group's instants.
/ @private
.qpipe.transform.markout_core.rate_patches:{[domain;spec;tob;q;clk;times;c]
    rt:spec`rates;
    idx:where q=c;
    kind:$[c in key rt; .qpipe.transform.markout_core.rate_kind[rt c]; `];
    k:count idx;
    if[kind=`fixed; :enlist (idx;k#`float$rt c;k#.qpipe.transform.markout_core.fixed_source;k#`;k#0Np)];
    if[not kind=`market; :()];
    cv:domain[`convert][spec;enlist c;spec`report_ccy];
    if[not first cv`ok; :()];
    gs:$[.qpipe.transform.markout_core.rate_same_clock; value group clk idx; enlist til k];
    .qpipe.transform.markout_core.market_patch[spec;tob;((rt c) 0;(rt c) 1;first cv`invert);clk;times;idx] each gs}

/ Private: one clock group's market rate patch.
/ @private
.qpipe.transform.markout_core.market_patch:{[spec;tob;def;clk;times;idx;g]
    src:def 0;
    sy:def 1;
    inverted:def 2;
    ix:idx g;
    k:count ix;
    tb:select from tob where venue=src, sym=sy;
    if[.qpipe.transform.markout_core.rate_same_clock; tb:select from tb where clock=clk first ix];
    if[0=count tb; :(`long$();`float$();`symbol$();`symbol$();`timestamp$())];
    tb:`sym`time xasc select sym, time, bid, ask, quoted:time from tb;
    j:aj[`sym`time;([] sym:k#sy; time:times ix);tb];
    live:(not null j`quoted) and (.qpipe.transform.markout_core.rate_max_age[spec]>=(j`time)-j`quoted) and (not null j`bid) and not null j`ask;
    ob:?[inverted;1f%j`ask;j`bid];
    oa:?[inverted;1f%j`bid;j`ask];
    mid:0.5*ob+oa;
    (ix where live;mid where live;(sum live)#src;(sum live)#sy;(j`quoted) where live)}

/ Private: the conversion rate of each row at the given instants, with where it
/ came from. A quote currency equal to the reporting one has rate 1; a declared
/ fixed rate is itself; a market rate is the mid of the oriented conversion
/ book of the declared source, on the execution's clock when rate_same_clock.
/ @private
.qpipe.transform.markout_core.rates_at:{[domain;spec;tob;q;clk;times]
    n:count q;
    b:`rate`rate_source`rate_sym`rate_time!(n#0n;n#`;n#`;n#0Np);
    own:where q=spec`report_ccy;
    k:count own;
    b:.qpipe.transform.markout_core.patch[b;(own;k#1f;k#.qpipe.transform.markout_core.fixed_source;k#`;k#0Np)];
    us:distinct q where not q=spec`report_ccy;
    ps:raze .qpipe.transform.markout_core.rate_patches[domain;spec;tob;q;clk;times;] each us;
    .qpipe.transform.markout_core.patch/[b;ps]}

/ Private: one row per (execution, horizon), execution-major: the execution, its
/ resolution, the horizon and the two instants.
/ @private
.qpipe.transform.markout_core.frame:{[domain;spec;exe]
    hs:spec`horizons;
    n:count exe;
    m:count hs;
    rows:exe "j"$raze m#'til n;
    r:.qpipe.transform.markout_core.resolution[domain;spec;distinct exe`sym];
    ix:(r`sym)?rows`sym;
    hz:hs (n*m)#til m;
    t0:rows`source_time;
    ([] ri:til n*m; time:t0+0D|max hs; source_time:t0; clock:rows`clock; venue:rows`venue; fill_id:rows`fill_id; sym:rows`sym;
        side:rows`side; size:rows`size; horizon:hz; price:rows`price; quote_ccy:(r`quote_ccy) ix;
        legs:(r`legs) ix; invs:(r`invert) ix; t0:t0; t1:t0+hz)}

/ Private: the k-th leg of each row and whether it is inverted, for every k up
/ to the longest chain; null and unflipped past a shorter chain's end.
/ @private
.qpipe.transform.markout_core.chain_legs:{[fr]
    depth:max count each fr`legs;
    pick:{[col;fill;k;rows] {[fill;k;l] $[k<count l; l k; fill]}[fill;k] each rows col}[;;;fr];
    `legs`invs!(pick[`legs;`;] each til depth;pick[`invs;0b;] each til depth)}

/ Private: why a row's reference is missing - `ok when present, the mismatch
/ status when only another clock has a book, `unavailable otherwise.
/ @private
.qpipe.transform.markout_core.cause:{[ref;ref_any;avail]
    ?[not avail; `unavailable;
      ?[not null ref; `ok;
        ?[not null ref_any; .qpipe.transform.markout_core.mismatch_status; `unavailable]]]}

/ Private: the money, the conversion and the status of every component row.
/ @private
.qpipe.transform.markout_core.finish:{[spec;fr;rt;c]
    f:fr c`ri;
    r:(key rt)!(value rt)@\:c`ri;
    qa:(f`size)*c`move;
    priced:((c`cause)=`ok) and not null r`rate;
    / open question 6: only report_amount is null without a rate; quote_amount needs none
    ra:?[priced; qa*r`rate; 0n];
    st:?[(c`cause)<>`ok; c`cause; ?[null r`rate; `no_rate; c`dstatus]];
    ([] time:f`time; source_time:f`source_time; clock:f`clock; venue:f`venue; fill_id:f`fill_id; sym:f`sym; side:f`side; size:f`size;
        horizon:f`horizon; kind:c`kind; step:c`step; price:f`price; quote_ccy:f`quote_ccy; ref_t0:f`ref_t0; ref_t1:f`ref_t1;
        move:c`move; quote_amount:qa; report_ccy:(count c)#spec`report_ccy; rate:r`rate; rate_source:r`rate_source;
        rate_sym:r`rate_sym; rate_time:r`rate_time; rate_timing:(count c)#spec`rate_timing; report_amount:ra;
        status:st; contract_version:(count c)#.qpipe.transform.markout_core.contract_version)}

/ Score executions against market_data at every horizon of the spec.
/ .
/ Pure: a function of its four arguments. The same call serves a live job and a
/ bounded window, so the two cannot score differently.
/ @param domain the domain dictionary (resolve, convert, keys and optionally validate, available, attribute)
/ @param spec dict horizons, report_ccy, rate_timing and optionally max_age, rates and the domain's keys
/ @param exe executions: source_time, venue, sym, side, size, price, fill_id, clock
/ @param md market_data: source_time, sym, source, the four ladders and clock
/ @return the core's result, in execution order then horizon order then component order
/ @throws error when the spec or the domain is malformed or an input lacks a column
/ @eg count .qpipe.transform.markout_core.calc[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;0#.qpipe.transform.markout_core.executions_in;0#.qpipe.transform.markout_core.market_data_in]  ->  0
.qpipe.transform.markout_core.calc:{[domain;spec;exe;md]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    .qf.util.require_cols[`markout_core;`executions;exe;cols .qpipe.transform.markout_core.executions_in];
    .qf.util.require_cols[`markout_core;`market_data;md;cols .qpipe.transform.markout_core.market_data_in];
    exe:.qpipe.transform.markout_core.last_delivery[exe where null .qpipe.transform.markout_core.why[domain;spec;exe]];
    if[0=count exe; :0#.qpipe.transform.markout_core.result];
    fr:.qpipe.transform.markout_core.frame[domain;spec;exe];
    lg:.qpipe.transform.markout_core.chain_legs[fr];
    tob:.qpipe.transform.markout_core.tops[md];
    ma:spec`max_age;
    bk0:.qpipe.transform.markout_core.leg_books[tob;ma;lg;fr`t0;fr`clock;1b];
    bk1:.qpipe.transform.markout_core.leg_books[tob;ma;lg;fr`t1;fr`clock;1b];
    ref0:.qpipe.transform.markout_core.chain_mid[bk0];
    ref1:.qpipe.transform.markout_core.chain_mid[bk1];
    any0:.qpipe.transform.markout_core.chain_mid[.qpipe.transform.markout_core.leg_books[tob;ma;lg;fr`t0;fr`clock;0b]];
    any1:.qpipe.transform.markout_core.chain_mid[.qpipe.transform.markout_core.leg_books[tob;ma;lg;fr`t1;fr`clock;0b]];
    fr:update ref_t0:ref0, ref_t1:ref1 from fr;
    avail:$[`available in key domain; domain[`available][spec;fr]; (count fr)#1b];
    k0:.qpipe.transform.markout_core.cause[ref0;any0;avail];
    k1:.qpipe.transform.markout_core.cause[ref1;any1;avail];
    side:`float$fr`side;
    mv:{[cause;m] ?[cause=`ok; m; 0n]};
    n:count fr;
    c:([] ri:til n; ord:n#0; kind:n#`total; step:n#0; move:mv[k1;side*ref1-fr`price]; cause:k1; dstatus:n#`ok),
      ([] ri:til n; ord:n#1; kind:n#`execution; step:n#0; move:mv[k0;side*ref0-fr`price]; cause:k0; dstatus:n#`ok);
    if[`attribute in key domain;
        a:domain[`attribute][spec;fr;lg;`at0`at1!(bk0;bk1)];
        if[not (98h=type a) and all `ri`kind`step`move in cols a; '"markout_core: attribute must return a table ri, kind, step, move"];
        / an attributed row the reference could not price takes its pair's cause
        ca:?[(k1 a`ri)<>`ok; k1 a`ri; ?[null a`move; `unavailable; `ok]];
        ds:$[`status in cols a; a`status; (count a)#`ok];
        c,:([] ri:a`ri; ord:(count a)#2; kind:a`kind; step:"j"$a`step; move:?[ca=`ok;"f"$a`move;0n]; cause:ca; dstatus:ds)];
    c:`ri`ord xasc c;
    rt:.qpipe.transform.markout_core.rates_at[domain;spec;tob;fr`quote_ccy;fr`clock;$[spec[`rate_timing]=`execution; fr`t0; fr`t1]];
    (cols .qpipe.transform.markout_core.result)#.qpipe.transform.markout_core.finish[spec;fr;rt;c]}

/ Refuse a result written under another contract version (a consumer pins one).
/ @param rows a markout dataset's rows
/ @param version the contract version the consumer reads
/ @return the rows
/ @throws error when any row carries another version
/ @eg count .qpipe.transform.markout_core.require_version[0#.qpipe.transform.markout_core.result;1]  ->  0
.qpipe.transform.markout_core.require_version:{[rows;version]
    other:distinct (rows`contract_version) except version;
    if[count other; '"markout_core: contract version ",(", " sv string other)," is not the pinned ",string version];
    rows}

/ Declare a markout transform of a domain under a spec.
/ .
/ The spec is validated here, so a misspelt key or a refused conversion pair
/ fails when the job is declared, not when its first window runs.
/ @param name the transform's name
/ @param domain the domain dictionary
/ @param spec the spec
/ @param examples hand-worked examples (`inputs`expected dicts) of executions and market_data
/ @return name
/ @throws error naming what is wrong with the spec or the declaration
/ @eg .qpipe.transform.markout_core.declare[`markout_core_eg;.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;.qpipe.transform.markout_core.fixture_examples[]]  ->  `markout_core_eg
.qpipe.transform.markout_core.declare:{[name;domain;spec;examples]
    spec:.qpipe.transform.markout_core.validate[domain;spec];
    .qetl.transform.define[name;`inputs`output`fn`examples!(
        (.qpipe.transform.markout_core.input_names)!(.qpipe.transform.markout_core.executions_in;.qpipe.transform.markout_core.market_data_in);
        .qpipe.transform.markout_core.result;
        .qpipe.transform.markout_core.calc[domain;spec;;];
        examples)]}

/ ------------------------------------------------------ A TABLE-DRIVEN DOMAIN

/ A domain declared as data: every instrument's quote currency, legs and
/ inversion, and every conversion pair's leg, in two tables. It is how an
/ instrument whose symbol does not spell its currency - an equity - is priced
/ (the contract: declared per instrument), and how the examples below run
/ without the core knowing any asset class.
/ @param inst table sym, quote_ccy, legs, invert
/ @param conv table quote_ccy, report_ccy, leg, invert
/ @return a domain dictionary with resolve, convert and no keys
/ @eg .qpipe.transform.markout_core.table_domain[([] sym:enlist `ACME; quote_ccy:enlist `USD; legs:enlist enlist `ACME; invert:enlist enlist 0b);0#([] quote_ccy:`symbol$(); report_ccy:`symbol$(); leg:`symbol$(); invert:`boolean$())][`resolve][()!();enlist `ACME]`quote_ccy  ->  enlist `USD
.qpipe.transform.markout_core.table_domain:{[inst;conv]
    resolve:{[inst;spec;syms]
        pick:{[inst;s] i:(inst`sym)?s; $[i<count inst; (1b;(inst`quote_ccy) i;(inst`legs) i;(inst`invert) i); (0b;`;`symbol$();`boolean$())]}[inst];
        rows:pick each syms;
        ([] sym:syms; ok:`boolean$rows[;0]; quote_ccy:`symbol$rows[;1]; legs:rows[;2]; invert:rows[;3])}[inst];
    convert:{[conv;spec;qs;rep]
        pick:{[conv;rep;q]
            r:select from conv where quote_ccy=q, report_ccy=rep;
            $[count r; (1b;first r`leg;first r`invert); (0b;`;0b)]}[conv;rep];
        rows:pick each qs;
        ([] ok:`boolean$rows[;0]; leg:`symbol$rows[;1]; invert:`boolean$rows[;2])}[conv];
    `resolve`convert`keys!(resolve;convert;`symbol$())}

/ ------------------------------------------------------------- THE EXAMPLES

/ The fixture instruments and conversions of the examples: a currency pair, a
/ crypto pair and an equity, declared as data. Nothing in the core is told
/ which is which.
.qpipe.transform.markout_core.fixture_domain:.qpipe.transform.markout_core.table_domain[
    ([] sym:`USDJPY,`$("BTC-USDT";"ACME");
        quote_ccy:`JPY`USDT`USD;
        legs:(enlist `USDJPY;enlist `$"BTC-USDT";enlist `ACME);
        invert:(enlist 0b;enlist 0b;enlist 0b));
    ([] quote_ccy:enlist `JPY; report_ccy:enlist `USD; leg:enlist `USDJPY; invert:enlist 1b)]

/ The fixture spec: report in USD; JPY is converted at the book of USDJPY on
/ source a at the execution, and USDT is declared worth exactly 1 USD.
.qpipe.transform.markout_core.fixture_spec:`horizons`report_ccy`rate_timing`rates!(enlist 0D00:00:10;`USD;`execution;`JPY`USDT!(`a`USDJPY;1f))

/ The fixture examples, see the arithmetic above the declaration below.
/ @return the declared transform's examples
/ @eg count .qpipe.transform.markout_core.fixture_examples[]  ->  3
.qpipe.transform.markout_core.fixture_examples:{[]
    t:2026.09.17D10:00:00;
    bk:{[tm;sym;src;clk;bid;ask] ([] source_time:enlist tm; sym:enlist sym; source:enlist src; bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6; clock:enlist clk)};
    fx_e:([] source_time:enlist t; venue:enlist `a; sym:enlist `USDJPY; side:enlist 1; size:enlist 1e6; price:enlist 150f; fill_id:enlist `fx1; clock:enlist `venue);
    fx_m:(bk[t-0D00:00:01;`USDJPY;`a;`venue;150f;150f]),(bk[t+0D00:00:09;`USDJPY;`a;`venue;150.05;150.05]);
    cr_e:([] source_time:enlist t; venue:enlist `x; sym:enlist `$"BTC-USDT"; side:enlist 1; size:enlist 0.5; price:enlist 61998f; fill_id:enlist `cr1; clock:enlist `venue);
    cr_m:raze (bk[t-0D00:00:01;`$"BTC-USDT";`a;`venue;61990f;62010f];bk[t-0D00:00:01;`$"BTC-USDT";`b;`venue;61995f;62005f];
        bk[t+0D00:00:09;`$"BTC-USDT";`a;`venue;62010f;62030f];bk[t+0D00:00:09;`$"BTC-USDT";`b;`venue;62015f;62025f]);
    eq_e:([] source_time:enlist t; venue:enlist `n; sym:enlist `ACME; side:enlist -1; size:enlist 100f; price:enlist 50.5; fill_id:enlist `eq1; clock:enlist `receipt);
    eq_m:(bk[t-0D00:00:01;`ACME;`q;`receipt;49.75;50.25]),(bk[t+0D00:00:09;`ACME;`q;`receipt;50.5;51f]);
    / the expected rows share their columns; only the numbers differ
    row:{[d]
        ([] time:2#d[`t]+0D00:00:10; source_time:2#d`t; clock:2#d`clock; venue:2#d`venue; fill_id:2#d`id; sym:2#d`sym; side:2#d`side; size:2#d`size;
            horizon:2#0D00:00:10; kind:`total`execution; step:2#0; price:2#d`price; quote_ccy:2#d`qccy; ref_t0:2#d`r0; ref_t1:2#d`r1;
            move:d`moves; quote_amount:(d`size)*d`moves; report_ccy:2#`USD; rate:2#d`rate; rate_source:2#d`rsrc; rate_sym:2#d`rsym; rate_time:2#d`rtime;
            rate_timing:2#`execution; report_amount:(d`rate)*(d`size)*d`moves; status:2#`ok; contract_version:2#1)};
    / USDJPY buy of 1,000,000 at 150.00; the reference is 150.00 at the execution and 150.05 at +10s
    / (the book of +9s is within max_age): total move 0.05 JPY, 50,000 JPY, at 1/150.00 USD per JPY.
    / The move is written as 150.05-150 because 0.05 is not what that subtraction yields in a float.
    fx_x:row`t`clock`venue`id`sym`side`size`price`qccy`r0`r1`moves`rate`rsrc`rsym`rtime!(t;`venue;`a;`fx1;`USDJPY;1;1e6;150f;`JPY;150f;150.05;(150.05-150f),0f;1f%150f;`a;`USDJPY;t-0D00:00:01);
    / BTC-USDT buy of 0.5 at 61998: best bid 61995 and best ask 62005 across a and b give 62000 at the
    / execution; 62015 and 62025 give 62020 at +10s. Moves 22 and 2 USDT; USDT is declared 1 USD.
    cr_x:row`t`clock`venue`id`sym`side`size`price`qccy`r0`r1`moves`rate`rsrc`rsym`rtime!(t;`venue;`x;`cr1;`$"BTC-USDT";1;0.5;61998f;`USDT;62000f;62020f;22 2f;1f;`fixed;`;0Np);
    / ACME sell of 100 at 50.5 on the receipt clock: mid 50.00 at the execution and 50.75 at +10s. Moves
    / -(50.75-50.5) = -0.25 and -(50-50.5) = 0.5; USD is the reporting currency, so the rate is 1.
    eq_x:row`t`clock`venue`id`sym`side`size`price`qccy`r0`r1`moves`rate`rsrc`rsym`rtime!(t;`receipt;`n;`eq1;`ACME;-1;100f;50.5;`USD;50f;50.75;-0.25 0.5;1f;`fixed;`;0Np);
    (`inputs`expected!(.qpipe.transform.markout_core.input_names!(fx_e;fx_m);fx_x);
     `inputs`expected!(.qpipe.transform.markout_core.input_names!(cr_e;cr_m);cr_x);
     `inputs`expected!(.qpipe.transform.markout_core.input_names!(eq_e;eq_m);eq_x))}

\d .

/ The core as a declared transform over the fixture domain and spec, so its
/ examples are verified on every run of the suite. A domain's job declares its
/ own with the same function.
/ .
/ Worked by hand, each at the single horizon of 10s with max_age 5s.
/ .
/ A USDJPY buy of 1,000,000 at 150.00 (the contract's worked example). The
/ reference is 150.00 at the execution (a locked book 1s earlier) and 150.05 at
/ +10s (a locked book 9s on). The move is 0.05 JPY and the money 50,000 JPY; the
/ execution's own move is 0. JPY converts through the inverted USDJPY book of
/ source a at the execution, 1/150.00 USD per JPY, so the total is 333.33 USD.
/ .
/ A BTC-USDT buy of 0.5 at 61998 across venues a and b. At the execution the best
/ bid is 61995 (b) and the best ask 62005 (b): mid 62000. At +10s 62015 and
/ 62025: mid 62020. The total move is 22 and the execution's 2 USDT; the money 11
/ and 1 USDT, and USDT is declared worth 1 USD.
/ .
/ An ACME sell of 100 at 50.5, a fixture equity on the receipt clock. The mid is
/ 50.00 at the execution and 50.75 at +10s: the total move is -(50.75-50.5) =
/ -0.25 and the execution's +0.5, worth -25 and +50 USD. Its quote currency is the
/ reporting one, so the rate is 1: the core assumes nothing about currency pairs.
.qpipe.transform.markout_core.declare[`markout_core;.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;.qpipe.transform.markout_core.fixture_examples[]];
