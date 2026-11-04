/ eq_domain.q - the equity domain module of the markout contract (.qpipe.transform.eq_domain, #1939).
/ .
/ WHAT THIS IS. What the markout core (markout_core.q, #1929) needs to know
/ about an EQUITY and nothing else: a domain dictionary (resolve, convert, keys,
/ validate, available). It owns no queue, no clock rule, no horizon expansion
/ and no dataset - those are the core's, and there is no equity dataset or job
/ yet, because no equity fills source exists (#1939). docs/architecture/
/ markout-contract.md is the contract; fx_domain.q is the FX counterpart.
/ .
/ THE RESOLVER. An equity is a single leg: its reference is the book of its
/ own symbol, never inverted. Its symbol does not spell its currency (AAPL,
/ VOD), so the quote currency is DECLARED: the spec's `quote_ccy` for every
/ instrument (default USD), overridden per instrument by `quote_ccys`, a
/ dictionary from symbol to currency. Any non-null symbol is `ok`; whether a
/ book exists for it is the core's to find out, not the resolver's.
/ .
/ THE BENCHMARK is the mid, declared as the spec's `benchmark` key (default and
/ only value `mid). It is the core's reference - the mid of the book's top
/ level on the execution's own clock - and this module does not restate it; the
/ key exists so the choice is written in the spec rather than implied, and so a
/ benchmark the core cannot read (a last trade, a VWAP) is refused at declaration
/ instead of silently being a mid.
/ .
/ THE CALENDAR: SESSIONS AND HALTS, declared as data in the spec.
/ .
/   sessions   table sym, open_time, close_time: when an instrument trades. A
/              null sym is every instrument; an instrument is looked up in its
/              own rows and the null ones. Half-open, [open_time; close_time):
/              the instant of the close is NOT in the session.
/   halts      table sym, halt_from, halt_to, half-open and null-sym as above.
/ .
/ A (execution, horizon) pair is `unavailable` - through the core's optional
/ `available` hook, so both its rows carry a null move and that status, whatever
/ books exist - unless BOTH instants lie in one session:
/ .
/   open_time <= t0  and  t1 < close_time        for some session of the instrument
/ .
/ A horizon that crosses the close, or lands exactly on it, is unavailable: the
/ book after the close is the next session's opening, and the move to it is an
/ overnight gap, not execution quality. A fill outside every session is
/ unavailable for the same reason. A pair is also unavailable when a halt
/ overlaps the closed span [t0; t1] (halt_from <= t1 and halt_to > t0): a book
/ read across a halt is the last quote before it or the reopening one, and
/ neither is where the market was.
/ .
/ The calendar has no default. A spec that omits `sessions` is refused, because
/ the alternative - treating every instant as in session - quietly scores
/ overnight gaps. To say "always open" declare one session wide enough.
/ .
/ THE CONVERSION. A quote currency other than the reporting one is read from the
/ market_data sym QUOTEREPORT (GBPUSD for GBP into USD), never inverted, when the
/ spec's `rates` names a source for it; a fixed rate is the spec's, never assumed.
/ A book quoted the other way round (USDGBP) is not supported here and would be a
/ spec error at declaration.

\d .

/ ------------------------------------------------------------------- SPEC

/ What an equity spec leaves out: USD quotes, no per-instrument overrides, the
/ mid as the benchmark and no halts. `sessions` is deliberately absent.
.qpipe.transform.eq_domain.defaults:`quote_ccy`quote_ccys`benchmark`halts!(`USD;(`symbol$())!`symbol$();`mid;([] sym:`symbol$(); halt_from:`timestamp$(); halt_to:`timestamp$()))

/ The benchmarks the core can read. The core's reference is a mid.
.qpipe.transform.eq_domain.benchmarks:enlist `mid

/ Is the table a calendar with these timestamp columns, every interval non-empty and non-null.
/ @param tbl the table
/ @param from_c the interval start column
/ @param to_c the interval end column
/ @return a boolean
/ @eg .qpipe.transform.eq_domain.interval_table[([] sym:enlist `; halt_from:enlist 2026.09.17D15:00:00; halt_to:enlist 2026.09.17D15:05:00);`halt_from;`halt_to]  ->  1b
.qpipe.transform.eq_domain.interval_table:{[tbl;from_c;to_c]
    if[not 98h=type tbl; :0b];
    if[not all (`sym,from_c,to_c) in cols tbl; :0b];
    if[not (11h=type tbl`sym) and (12h=type tbl from_c) and 12h=type tbl to_c; :0b];
    if[0=count tbl; :1b];
    (not any null tbl from_c) and (not any null tbl to_c) and all (tbl to_c)>tbl from_c}

/ Refuse equity spec keys the domain cannot honour, naming the first problem.
/ @param spec dict, at least sessions
/ @return the spec
/ @throws error naming what is wrong
/ @eg count .qpipe.transform.eq_domain.require_spec[.qpipe.transform.eq_domain.defaults,enlist[`sessions]!enlist ([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00; close_time:enlist 2026.09.17D20:00:00)]  ->  5
.qpipe.transform.eq_domain.require_spec:{[spec]
    if[not 99h=type spec; '"eq_domain: spec must be a dictionary"];
    miss:`sessions`quote_ccy`quote_ccys`benchmark`halts except key spec;
    if[count miss; '"eq_domain: spec is missing ",", " sv string miss];
    if[not (-11h=type spec`quote_ccy) and not null spec`quote_ccy; '"eq_domain: quote_ccy must be a non-null symbol"];
    qs:spec`quote_ccys;
    plain:(99h=type qs) and (0=count qs) or (11h=type key qs) and 11h=type value qs;
    if[not plain; '"eq_domain: quote_ccys must be a dictionary from symbol to currency"];
    if[any null key qs; '"eq_domain: quote_ccys must not name a null symbol"];
    if[any null value qs; '"eq_domain: quote_ccys must not map to a null currency"];
    if[not (-11h=type spec`benchmark) and spec[`benchmark] in .qpipe.transform.eq_domain.benchmarks;
        '"eq_domain: benchmark must be one of ",", " sv string .qpipe.transform.eq_domain.benchmarks];
    if[not .qpipe.transform.eq_domain.interval_table[spec`sessions;`open_time;`close_time];
        '"eq_domain: sessions must be a table sym, open_time, close_time with timestamps and every close after its open"];
    if[not .qpipe.transform.eq_domain.interval_table[spec`halts;`halt_from;`halt_to];
        '"eq_domain: halts must be a table sym, halt_from, halt_to with timestamps and every halt_to after its halt_from"];
    spec}

/ Complete and validate an equity spec (the domain's `validate`).
/ @param spec dict, at least sessions
/ @return the spec with the defaults filled in
/ @throws error naming what is wrong
/ @eg first key .qpipe.transform.eq_domain.complete[enlist[`sessions]!enlist ([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00; close_time:enlist 2026.09.17D20:00:00)]  ->  `quote_ccy
.qpipe.transform.eq_domain.complete:{[spec]
    if[not 99h=type spec; '"eq_domain: spec must be a dictionary"];
    .qpipe.transform.eq_domain.require_spec[.qpipe.transform.eq_domain.defaults,spec]}

/ ------------------------------------------------------------------ RESOLVER

/ The resolver: how each instrument is priced. Single leg, never inverted, and a
/ quote currency that is declared rather than parsed.
/ @param spec the spec; quote_ccy and quote_ccys are read when present
/ @param syms instruments
/ @return table sym, ok, quote_ccy, legs, invert, one row per sym, in order
/ @eg exec quote_ccy from .qpipe.transform.eq_domain.resolve[enlist[`quote_ccys]!enlist (enlist `VOD)!enlist `GBP;`AAPL`VOD]  ->  `USD`GBP
.qpipe.transform.eq_domain.resolve:{[spec;syms]
    syms,:();
    s:.qpipe.transform.eq_domain.defaults,spec;
    declared:(s`quote_ccys) syms;
    q:?[null declared;s`quote_ccy;declared];
    ([] sym:syms; ok:not null syms; quote_ccy:q; legs:enlist each syms; invert:(count syms)#enlist enlist 0b)}

/ The conversion leg of each quote currency: the market_data sym QUOTEREPORT,
/ never inverted. A quote currency equal to the reporting one needs none, and
/ the core never asks.
/ @param spec the spec (unused: the leg is a function of the two currencies)
/ @param quote_ccys symbol list
/ @param report_ccy the reporting currency
/ @return table ok, leg, invert, one row per quote currency
/ @eg .qpipe.transform.eq_domain.convert[()!();`GBP`;`USD]`leg  ->  `GBPUSD`
.qpipe.transform.eq_domain.convert:{[spec;quote_ccys;report_ccy]
    ok:(not null quote_ccys) & (not null report_ccy) & quote_ccys<>report_ccy;
    leg:?[ok;`$(string quote_ccys),\:string report_ccy;`];
    ([] ok:ok; leg:leg; invert:(count quote_ccys)#0b)}

/ ------------------------------------------------------------------ CALENDAR

/ The rows of a calendar table that apply to an instrument: its own and the null ones.
/ @param tbl a calendar table with a sym column
/ @param s the instrument
/ @return the applicable rows
/ @eg count .qpipe.transform.eq_domain.rows_for[([] sym:`A``B; n:1 2 3);`A]  ->  2
.qpipe.transform.eq_domain.rows_for:{[tbl;s] tbl where (null tbl`sym) | (tbl`sym)=s}

/ Do both instants lie in one session of the instrument: open_time <= t0 and t1 < close_time.
/ @param sessions the sessions table
/ @param s the instrument
/ @param t0 the execution instant
/ @param t1 the horizon instant
/ @return a boolean
/ @eg .qpipe.transform.eq_domain.in_session[([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00; close_time:enlist 2026.09.17D20:00:00);`A;2026.09.17D19:59:50;2026.09.17D19:59:55]  ->  1b
.qpipe.transform.eq_domain.in_session:{[sessions;s;t0;t1]
    r:.qpipe.transform.eq_domain.rows_for[sessions;s];
    started:(r`open_time)<=t0;
    unended:(r`close_time)>t1;
    any started & unended}

/ Does a halt overlap the closed span [t0; t1]: halt_from <= t1 and halt_to > t0.
/ @param halts the halts table
/ @param s the instrument
/ @param t0 the execution instant
/ @param t1 the horizon instant
/ @return a boolean
/ @eg .qpipe.transform.eq_domain.halted[([] sym:enlist `A; halt_from:enlist 2026.09.17D15:00:10; halt_to:enlist 2026.09.17D15:00:20);`A;2026.09.17D15:00:00;2026.09.17D15:00:30]  ->  1b
.qpipe.transform.eq_domain.halted:{[halts;s;t0;t1]
    r:.qpipe.transform.eq_domain.rows_for[halts;s];
    begun:(r`halt_from)<=t1;
    unlifted:(r`halt_to)>t0;
    any begun & unlifted}

/ The availability hook of the domain dictionary: a boolean per (execution,
/ horizon) pair of the core's frame, 0b where the calendar rules the pair out.
/ @param spec the validated spec, at least sessions and halts
/ @param fr the core's frame: sym, t0 and t1 are read
/ @return a boolean per frame row
/ @eg .qpipe.transform.eq_domain.available[`sessions`halts!(([] sym:enlist `; open_time:enlist 2026.09.17D13:30:00; close_time:enlist 2026.09.17D20:00:00);0#.qpipe.transform.eq_domain.defaults`halts);([] sym:`A`A; t0:2#2026.09.17D19:59:50; t1:2026.09.17D19:59:55 2026.09.17D20:00:20)]  ->  10b
.qpipe.transform.eq_domain.available:{[spec;fr]
    one:{[spec;s;t0;t1]
        ins:.qpipe.transform.eq_domain.in_session[spec`sessions;s;t0;t1];
        hlt:.qpipe.transform.eq_domain.halted[spec`halts;s;t0;t1];
        ins & not hlt}[spec];
    "b"$one'[fr`sym;fr`t0;fr`t1]}

/ The equity domain as the core takes it: resolver, conversion, the spec keys it
/ owns, their validation and the calendar's availability rule. No attribution:
/ a single-leg instrument has no legs to split a move over.
/ The revision of the rules that price an equity: its declared quote currency,
/ the session and halt availability rule, the conversion pair. Recorded by the
/ configuration snapshot (#1999); the calendar itself is a spec key and is recorded
/ with the spec.
.qpipe.transform.eq_domain.revision:`eq_r1

.qpipe.transform.eq_domain.domain:`resolve`convert`keys`validate`available`name`revision`datasets!(
    .qpipe.transform.eq_domain.resolve;
    .qpipe.transform.eq_domain.convert;
    `quote_ccy`quote_ccys`benchmark`sessions`halts;
    .qpipe.transform.eq_domain.complete;
    .qpipe.transform.eq_domain.available;
    `eq;
    .qpipe.transform.eq_domain.revision;
    `symbol$())

\d .
