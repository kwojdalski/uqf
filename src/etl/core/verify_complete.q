/ verify_complete.q - is every source record in the destination, and nothing more?
/ (.qetl.verify, #1851).
/ .
/ Two producers can agree with each other and with the schema while both omit the
/ same record, so completeness is judged against the SOURCE, read on its own: the
/ source's registered adapter over the window (.qetl.source.fetch_window, before
/ any transform), against the destination as a reader returns it. The reader is a
/ function [dataset; from; to] -> rows - a site gives the combined RDB+HDB (below),
/ a test gives a table - so the comparison does not depend on how rows are fetched.
/ .
/ WHAT IS RECONCILED is an explicit scope (column=value pairs) and an explicit
/ half-open time range on the source's time column; both are applied to both sides.
/ Missing records, extra records and - for an `events` dataset - repeated ones are
/ each a FAIL.
/ .
/ WHEN IT CANNOT BE JUDGED it says UNVERIFIED and why: no scope, no reader, a scope
/ column the source or destination lacks, a window past the source's
/ complete_through, a live source that declares none (an open source has no end to
/ reconcile against), a source that returned nothing in scope, a reader that threw.

\d .

/ A destination reader over the gateway's combined RDB+HDB: .dataaccess.getdata
/ under the query policies, through an open IPC handle.
/ .
/ The request names the table and a time range only; the scope and the business
/ window are applied after, in q, on the source's own time column. The plant's
/ `time` is when a row arrived, not when the deal happened, so the request is
/ widened by `pad` past the window's end to take rows a feed stamped late.
/ @param h an open handle to the gateway
/ @param pad how far past the window's end to ask, a timespan
/ @return a function [dataset; from; to] -> the destination's rows
/ @eg type .qetl.verify.getdata_reader[0i;1D]  ->  104h
.qetl.verify.getdata_reader:{[h;pad]
    {[h;pad;dataset;from_ts;to_ts]
        h(`.dataaccess.getdata;`tablename`starttime`endtime!(dataset;from_ts;to_ts+pad))}[h;pad]}

/ Private: the rows of a table inside the scope and the half-open window.
/ @private
.qetl.verify.in_scope:{[rows;scope;time_col;from_ts;to_ts]
    keep:(rows[time_col]>=from_ts) & rows[time_col]<to_ts;
    rows where {[rows;keep;c;v] keep & rows[c] in (),v}[rows]/[keep;key scope;value scope]}

/ A scope's text values typed as their columns are, so `sym=0005` is the symbol `0005
/ on a symbol column and `day=2026.09.13` a date on a date column - not a value guessed
/ from how its text looks (#2473). A value that is already typed is left as it is.
/ @param m the dataset's schema, as meta returns it
/ @param scope a dict of column -> value, a value given as text typed here
/ @return the scope, each text value an atom of its column's type
/ @throws error naming the column, for a value its type refuses
/ @eg .qetl.verify.typed_scope[meta ([] sym:`$(); day:`date$());`sym`day!("0005";"2026.09.13")]  ->  `sym`day!(`0005;2026.09.13)
.qetl.verify.typed_scope:{[m;scope]
    types:exec c!t from m;
    one:{[types;c;v]
        if[not 10h=abs type v; :v];
        .[.qetl.compare.typed;(types c;(),v);{[c;e] '"scope ",string[c],": ",e}[c]]}[types];
    (key scope)!one'[key scope;value scope]}

/ Private: the source's rows for a window, read through its own adapter, and what that
/ read rests on. Opens a live source for the read and closes it.
/ @return a dict rows, live, cutoff (the source's complete_through), closed (1b when the
/   source declares a cutoff of its own, or is a fixture)
/ @private
.qetl.verify.read_source:{[source;from_ts;to_ts]
    live:.qetl.source.has_credentials source;
    tr:.qetl.source.for_source source;
    h:$[live; (tr`open) .qetl.source.require_credentials source; 0Ni];
    out:.[{[source;h;live;f;t]
            edge:.qetl.source.complete_through_of[source;h;live];
            `rows`cutoff!(.qetl.source.primary[source;last .qetl.source.fetch_window[source;h;f;t]];edge)};
        (source;h;live;from_ts;to_ts);{[e] (`read_failed;e)}];
    if[live; @[tr`close;h;{[e] ::}]];
    if[(0h=type out) and `read_failed~first out; '"reading ",string[source]," failed: ",last out];
    declared:not (.qetl.source.def[source]`complete_through)~.qetl.source.optional_declarations`complete_through;
    out,`live`declared!(live;declared)}

/ Is every source record in scope and range in the destination, and no other?
/ @param dataset the dataset
/ @param scope a dict of column -> value (or values), applied to both sides; empty is refused as
/   UNVERIFIED. A value given as text is typed by its column (typed_scope)
/ @param from_ts the window's first instant
/ @param to_ts the instant after its last
/ @param reader a function [dataset; from; to] -> the destination's rows, or (::) for none
/ @return a dict of status (`PASS`FAIL`UNVERIFIED), reason, findings (rowkey, kind, detail:
/   `missing from the destination, `extra in it, `duplicated), basis (text: the source,
/   its cutoff and what was read) and counts (source, destination)
/ @eg .qetl.verify.completeness[`demo_deals;enlist[`sym]!enlist `EURUSD;2026.09.11D00:00:00.000000000;2026.09.16D00:00:00.000000000;{[d;f;t] .qpipe.source.demo_deals.fixture[]}]`status  ->  `PASS
.qetl.verify.completeness:{[dataset;scope;from_ts;to_ts;reader]
    unv:{[r] `status`reason`findings`basis`counts!(`UNVERIFIED;r;.qetl.verify.no_differences[];"";0 0)};
    if[0=count scope; :unv "no scope given - a completeness check reconciles an explicit scope (column=value) and range"];
    if[(::)~reader; :unv "no destination reader - the destination's RDB and HDB were not read"];
    .qetl.coverage.require_interval[from_ts;to_ts];
    p:.qetl.verify.producers dataset;
    sources:distinct (p`source) where not null p`source;
    if[0=count sources; :unv "no producer of ",string[dataset]," names a source, so there is nothing independent to count"];
    if[1<count sources; :unv "producers read different sources (",(", " sv string sources),") - one reconciliation cannot judge both"];
    source:first sources;
    d:.qetl.source.def source;
    dest:.qetl.verify.destination dataset;
    if[(::)~dest; :unv "the plant declares no table ",string[dataset],", so its destination has no schema to read"];
    ks:(),d`row_key;
    tc:d`time_column;
    wanted:(key scope),ks,tc;
    if[count lost:wanted where not wanted in d`columns; :unv "the source does not carry ",(", " sv string lost)];
    if[count lost:wanted where not wanted in cols dest`published; :unv "the destination does not carry ",(", " sv string lost)];
    scope:.qetl.verify.typed_scope[meta dest`published;scope];
    src:.[.qetl.verify.read_source;(source;from_ts;to_ts);{[e] e}];
    if[10h=type src; :unv src];
    if[(not src`declared) and src`live; :unv "the live source declares no complete_through, so it has no known end to reconcile against"];
    if[to_ts>src`cutoff; :unv "the window ends ",string[to_ts]," but the source is complete only to ",string src`cutoff];
    rows:.qetl.verify.in_scope[src`rows;scope;tc;from_ts;to_ts];
    got:.[reader;(dataset;from_ts;to_ts);{[e] "reading the destination failed: ",e}];
    if[10h=type got; :unv got];
    if[not 98h=type got; :unv "the destination reader returned ",string[type got],"h, not a table"];
    mine:.qetl.verify.in_scope[got;scope;tc;from_ts;to_ts];
    counts:(count rows;count mine);
    if[0=counts 0;
        if[0=counts 1; :@[unv "empty sample - the source holds no record in this scope and range";`counts;:;counts]]];
    basis:.qetl.verify.basis dataset;
    / presence for a dataset that revises rows; multiplicity only where each row is a fact
    found:.qetl.verify.compare_rows[ks#rows;ks#mine;ks;`symbol$();$[basis=`events; `events; `final_state]];
    found:update detail:{ssr[ssr[ssr[x;"in the first producer only";"in the source only"];"in the second producer only";"in the destination only"];"first producer";"source"]} each detail from found;
    found:update detail:{ssr[x;", second ";", destination "]} each detail from found;
    text:string[source],", ",$[src`live; "live"; "fixture"],", complete through ",$[0Wp=src`cutoff; "(no cutoff: closed data)"; string src`cutoff],
        $[src`declared; " (declared)"; " (not declared: closed by default)"],
        "; scope ",.Q.s1[scope]," on ",string[tc]," in [",string[from_ts],"; ",string[to_ts],
        "); keyed on ",(", " sv string ks),"; destination as the reader returned it";
    `status`reason`findings`basis`counts!($[count found; `FAIL; `PASS];"";found;text;counts)}

\d .
