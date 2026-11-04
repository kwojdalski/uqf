/ source_batch_read.q - a source that reads a window's references a batch at a time (.qetl.source, #1921).
/ .
/ Part of the external-source contract: same namespace as source_registry.q, loaded after
/ source_fetch.q. Declared by a source, used by a bounded worker whose `batching` says
/ `source_read:`batch (src/etl/core/bounded_batching.q).
/ .
/ WHY. A window is fetched once, and for a source with supporting inputs that once is the
/ expensive part: every book the window's executions could need, however the compute is
/ batched afterwards. Batching the compute bounded the transform's working set and left the
/ read, and the peak memory with it, at the whole window.
/ .
/ WHAT THE SOURCE DECLARES. `batch_read` is a dictionary of five keys, all required:
/ .
/   window  {[h;range_from;range_to] -> inputs}. The window's PRIMARY rows, as `query` returns
/           them, and its supporting inputs as a PRESENCE SUMMARY: at least one row for every
/           key the window's rows need that the reference holds, and no more than that is
/           required. It is what the `ready` hook judges, over the whole window, before any
/           batch is read, so readiness keeps its meaning. A source whose `ready` looks at
/           more than the presence of a key cannot summarise and does not declare this.
/   needs   {[rows] -> keys}. The keys of the reference the primary `rows` need: for the FX
/           markouts, reference_read's `symbols`, which includes the conversion pairs.
/   read    {[h;range_from;range_to;keys] -> inputs}. The supporting inputs for exactly
/           these keys over the window, as `query` would return them for the whole window,
/           restricted to those keys. A key with no rows contributes none, and that is an
/           answer (it is cached as one).
/   keys    supporting input name -> the column holding its key, for every supporting input
/   sort    supporting input name -> the column a batch's rows are sorted on once the
/           per-key reads are put together (the order `query` returns them in)
/ .
/ The pieces are the source's: the framework joins them and adds the memory-bounded,
/ versioned reuse between batches (bounded_batch_read.q). UTC sources only: a zoned
/ source's bounds are padded and narrowed in fetch_window, and a per-batch read has no such step.

\d .

/ The keys a batch_read declaration carries.
.qetl.source.batch_read_keys:`window`needs`read`keys`sort

/ A source's batch_read, or a refusal naming it.
/ @param source a registered source name
/ @return the dict window, needs, read, keys, sort
/ @throws error when the source declares no batch_read
/ @eg key .qetl.source.batch_read `hdb_fx_markouts  ->  `window`needs`read`keys`sort
.qetl.source.batch_read:{[source]
    br:(.qetl.source.def[source])`batch_read;
    if[not 99h=type br;
        '"batch_read: ",string[source]," declares no batch_read - its query reads a window whole"];
    br}

/ Private: refuse a malformed batch_read, naming every fault at once.
/ @param source the source's name
/ @param br the declaration
/ @param decl the source's whole declaration, as given to define
/ @private
.qetl.source.check_batch_read:{[source;br;decl]
    who:"define: ",string[source],"'s batch_read ";
    sup:$[`supporting in key decl; decl`supporting; ()!()];
    if[0=count sup; 'who,"needs supporting inputs: there is no reference to read a batch at a time"];
    if[not `UTC~decl`tz; 'who,"is for a UTC source: a zoned source's bounds are padded and narrowed in fetch_window, which a per-batch read has no step for"];
    if[not 99h=type br; 'who,"must be a dict of ",", " sv string .qetl.source.batch_read_keys];
    miss:.qetl.source.batch_read_keys except key br;
    if[count miss; 'who,"is missing ",", " sv string miss];
    extra:(key br) except .qetl.source.batch_read_keys;
    if[count extra; 'who,"declares ",(", " sv string extra),", which it does not take"];
    bad:();
    arity:{[f;n] (100h=type f) and n=count (value f) 1};
    if[not arity[br`window;3]; bad,:enlist "window (a lambda taking handle, range_from and range_to)"];
    if[not arity[br`needs;1]; bad,:enlist "needs (a lambda taking the primary rows)"];
    if[not arity[br`read;4]; bad,:enlist "read (a lambda taking handle, range_from, range_to and the keys)"];
    names:key sup;
    bykey:{[names;d] $[(99h=type d) and (11h=type key d) and 11h=type value d; (asc key d)~asc names; 0b]};
    if[not bykey[names;br`keys]; bad,:enlist "keys (a symbol per supporting input: the column holding its key)"];
    if[not bykey[names;br`sort]; bad,:enlist "sort (a symbol per supporting input: the column its rows are sorted on)"];
    if[count bad; 'who,"has malformed ",", " sv bad];
    chk:{[sup;label;d] {[sup;label;nm;c] $[c in cols sup nm; (); enlist label," ",string[nm],".",string c]}[sup;label]'[key d;value d]};
    away:(raze chk[sup;"keys";br`keys]),raze chk[sup;"sort";br`sort];
    if[count away; 'who,"names a column the supporting input does not hold: ",", " sv away];
    }

\d .
