/ asof.q - the as-of lookup: for each event row, the prevailing reference row.
/ .
/ ONE RULE, SHARED. A streaming job that marks a fill to a mid, and a
/ markout that reads the quote at trade_time+horizon, both ask the same
/ question: "what was the latest reference row for this key at or before
/ this instant, and how old is it?". Each used to answer it by hand, with
/ its own sortedness, its own idea of "too old" and its own "no quote yet"
/ (#1223). This is the answer once, as a PURE function: no state, no I/O, so
/ a live job and the backfill that re-derives its rows run the same code.
/ The stateful buffer a live job keeps its reference rows in is .qetl.enrich.
/ .
/ A reference row with a null ref_time prevails at no instant and is ignored.
/ .
/ NO LOOK-AHEAD. A reference row stamped after the event is never attached,
/ whatever its arrival order. The comparison is ref_time <= event_time, so a
/ reference row stamped EXACTLY at the event does count (aj's rule).

\d .

/ The two columns a lookup adds after the attached ones.
.qf.mkt.result_cols:`staleness`status

/ Private: the names a spec must carry.
/ @private
.qf.mkt.spec_keys:`keys`ref_time`event_time`attach`tolerance

/ Attach the prevailing reference row to each event.
/ .
/ Result: the events, in their own order and with every column they came
/ with, then the attached columns (renamed as the spec says), then
/ `staleness` (event_time - ref_time, a timespan, null when nothing
/ prevails) and `status`:
/   `ok      a reference row prevails and is no older than the tolerance;
/            exactly AT the tolerance is still ok (age <= tolerance)
/   `stale   one prevails but is older than the tolerance - the attached
/            values are still there, so the caller decides whether to use them
/   `missing no reference row at or before the event for its key: every
/            attached column is null, staleness is null
/ A null tolerance (0Nn) means no limit: nothing is ever stale.
/ .
/ The reference table need not be sorted - this sorts its own copy by
/ (keys, ref_time), stably, so of two rows with the same ref_time the later
/ one in the table prevails. The events need not be sorted either.
/ @param refs reference rows: the key columns, ref_time and every column to attach
/ @param events the events: the key columns (same names) and event_time
/ @param spec dict: `keys (symbol list), `ref_time (refs' time column),
/   `event_time (events' time column), `attach (a dict new name!refs column,
/   or a symbol list to attach under their own names), `tolerance (timespan or 0Nn)
/ @return events, then attached columns, staleness, status
/ @throws error naming every missing spec key, or every missing column of refs
/   or events, a result column that clashes with an events column, or a
/   negative tolerance
/ @eg .qf.mkt.lookup[([] sym:`a`a; time:2026.10.01D10:00:00 2026.10.01D10:00:05; mid:1 2f);([] sym:enlist `a; time:enlist 2026.10.01D10:00:07);`keys`ref_time`event_time`attach`tolerance!(enlist `sym;`time;`time;enlist[`mid]!enlist `mid;0D00:00:10)]  ->  ([] sym:enlist `a; time:enlist 2026.10.01D10:00:07; mid:enlist 2f; staleness:enlist 0D00:00:02; status:enlist `ok)
.qf.mkt.lookup:{[refs;events;spec]
    miss:.qf.mkt.spec_keys except key spec;
    if[count miss; '"asof.lookup: spec is missing ",", " sv string miss];
    ks:(),spec`keys;
    attach:spec`attach;
    if[not 99h=type attach; attach:(attach,())!attach,()];
    tol:spec`tolerance;
    if[not (abs type tol) in 16 -16h; '"asof.lookup: tolerance must be a timespan (0Nn for none)"];
    if[(not null tol)&tol<0D; '"asof.lookup: tolerance must not be negative"];
    .qf.util.require_cols[`qasof_lookup;`refs;refs;distinct ks,spec[`ref_time],value attach];
    .qf.util.require_cols[`qasof_lookup;`events;events;distinct ks,spec`event_time];
    clash:((key attach),.qf.mkt.result_cols) inter cols events;
    if[count clash; '"asof.lookup: result columns already in events: ",", " sv string clash];
    n:count attach;
    / Temporary names (qasof_*) are not ones a caller names a column.
    tmp:`$"qasof_a",/:string til n;
    tt:`qasof_t;
    rt:`qasof_rt;
    names:(key attach),.qf.mkt.result_cols;
    / No events: the typed empty shape, built without aj or `,'` - PeachQ gives
    / () for an aj on an empty left and for `,'` of two empty tables.
    if[0=count events;
        :flip (flip events),names!((0#'refs each value attach),(enlist `timespan$()),enlist `symbol$())];
    r:?[refs;();0b;((ks,tt,rt,tmp)!(ks,spec[`ref_time],spec[`ref_time],value attach))];
    / A null ref_time sorts FIRST, so aj would match it for any event with no
    / earlier valid row and attach its values while status says missing (#1251):
    / a row with no time prevails at no instant, so it is dropped.
    r:r where not null r tt;
    r:(ks,tt) xasc r;
    probe:?[events;();0b;((ks,tt)!(ks,spec`event_time))];
    j:aj[ks,tt;probe;r];
    staleness:(events spec`event_time)-j rt;
    status:?[null j rt;`missing;?[(not null tol)&staleness>tol;`stale;`ok]];
    flip (flip events),names!(j each tmp),(enlist staleness),enlist status}

\d .
