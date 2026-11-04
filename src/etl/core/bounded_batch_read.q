/ bounded_batch_read.q - a batched window's source-side reads, and the bounded reuse between them
/ (.qetl.job.bounded.batching, #1921). The design record is bounded_batching.q's SOURCE-SIDE READS
/ and the source's side of the contract is source_batch_read.q.
/ .
/ THREE STEPS, in the order a window takes them:
/ .
/   fetch_window  the WINDOW read: primary rows and a presence summary of the references. What
/                 `ready` judges, over the whole window, before any batch is read.
/   begin_reads   at the start of the transform: drop whatever the reuse cache holds for any
/                 other window or source_version of this worker.
/   source_inputs one batch's inputs: its primary rows and the references its rows need, from
/                 the cache where it holds them and from the source (under the retry policy,
/                 as the window's own fetch is) where it does not.
/ .
/ THE REUSE CACHE. Rows of reference, kept per key, so a book one batch read can serve a later one
/ that needs it too (a leg shared by two pairs, a conversion pair every non-USD fill needs). It is
/ bounded by `reuse_rows`: the most reference rows it keeps once a batch has been read, least
/ recently used out first, and an entry bigger than the bound alone is never kept. A batch's own
/ rows are held by the batch whatever the bound, so the peak is one batch plus the bound.
/ .
/ It is KEYED by worker, source_version and the window's bounds. The version is in the key, and
/ not only in a comment, because the one thing reuse must never do is serve a row read for one
/ release of a source to a run of another: a hit needs all four to match, and begin_reads drops the
/ rest. Nothing in it outlives its window.

\d .

/ The reuse cache: one row per (worker; source_version; window; key), the rows held for that key as
/ a dictionary of supporting input name -> rows, n their count, and used the tick of their last use.
.qetl.job.bounded.batching.cache:([] worker:`symbol$(); version:`symbol$(); range_from:`timestamp$(); range_to:`timestamp$();
    item:`symbol$(); held:(); n:`long$(); used:`long$())

/ The cache's clock, and what the reads did: keys served from the cache (hits), keys read from the
/ source (reads), source calls (queries), entries evicted for the bound.
.qetl.job.bounded.batching.tick:0
.qetl.job.bounded.batching.counts:`hits`reads`queries`evicted!0 0 0 0

/ Forget the cache and its counts.
/ @return the zeroed counts
/ @eg .qetl.job.bounded.batching.reset_reads[]`hits  ->  0
.qetl.job.bounded.batching.reset_reads:{[]
    `.qetl.job.bounded.batching.cache set 0#.qetl.job.bounded.batching.cache;
    `.qetl.job.bounded.batching.tick set 0;
    `.qetl.job.bounded.batching.counts set `hits`reads`queries`evicted!0 0 0 0}

/ ------------------------------------------------------------- THE WINDOW READ

/ Fetch a window as the worker's declaration asks: whole (the source's query) or, for a worker whose
/ batches read the source themselves, as the source's `window` read - the primary rows and a
/ presence summary of the references.
/ @param worker the worker's name
/ @param h the open handle, or 0Ni on the fixture
/ @param from_ts inclusive lower bound
/ @param to_ts exclusive upper bound
/ @return (`live or `fixture; the window), as .qetl.source.fetch_window
/ @private
.qetl.job.bounded.batching.fetch_window:{[worker;h;from_ts;to_ts]
    cfg:.qetl.job.bounded.def worker;
    spec:cfg`batching;
    $[(not (::)~spec) and .qetl.job.bounded.batching.by_source[worker;spec];
        .qetl.source.fetch_window_via[cfg`source;h;from_ts;to_ts;(.qetl.source.batch_read cfg`source)`window];
        .qetl.source.fetch_window[cfg`source;h;from_ts;to_ts]]}

/ ------------------------------------------------------------- THE CACHE

/ Private: the cache rows that belong to a window of a worker at a version.
/ @param t the cache
/ @param sc the scope: worker, version, range_from, range_to
/ @return a boolean per row
/ @private
.qetl.job.bounded.batching.in_scope:{[t;sc]
    ((t`worker)=sc`worker) & ((t`version)=sc`version) & ((t`range_from)=sc`range_from) & (t`range_to)=sc`range_to}

/ Private: a window's cache scope.
/ @private
.qetl.job.bounded.batching.scope:{[worker;w]
    `worker`version`range_from`range_to!(worker;(.qetl.job.bounded.spec worker)`source_version;w`range_from;w`range_to)}

/ Start a window's reads: drop everything this worker's cache holds for any other window or
/ source_version. After it, a hit can only be a row read for this window of this version.
/ @param worker the worker's name
/ @param w the window
/ @return the number of entries dropped
/ @private
.qetl.job.bounded.batching.begin_reads:{[worker;w]
    t:.qetl.job.bounded.batching.cache;
    sc:.qetl.job.bounded.batching.scope[worker;w];
    stale:((t`worker)=worker) & not .qetl.job.bounded.batching.in_scope[t;sc];
    `.qetl.job.bounded.batching.cache set t where not stale;
    `long$sum stale}

/ Private: the rows the cache holds for some keys of a scope, refreshing their use.
/ @return a dictionary key -> held (name -> rows), of the keys it holds
/ @private
.qetl.job.bounded.batching.take:{[sc;want]
    t:.qetl.job.bounded.batching.cache;
    idx:where .qetl.job.bounded.batching.in_scope[t;sc] & (t`item) in want;
    if[0=count idx; :(`symbol$())!()];
    now:.qetl.job.bounded.batching.tick+1+til count idx;
    `.qetl.job.bounded.batching.tick set last now;
    `.qetl.job.bounded.batching.cache set @[t;`used;:;@[t`used;idx;:;now]];
    ((t`item) idx)!(t`held) idx}

/ Private: keep rows read from the source for later batches, within the bound.
/ .
/ An entry bigger than the bound is not kept at all; then the least recently used entries of
/ this worker go until what is held fits. Returns how many were evicted, so the bound is a thing
/ a test and a benchmark can see working.
/ @param sc the scope
/ @param bound reuse_rows
/ @param items the keys
/ @param held one dictionary of name -> rows per key
/ @return the number of entries evicted
/ @private
.qetl.job.bounded.batching.put:{[sc;bound;items;held]
    sizes:{sum count each value x} each held;
    ok:where sizes<=bound;
    if[0=count ok; :0];
    n:count ok;
    now:.qetl.job.bounded.batching.tick+1+til n;
    `.qetl.job.bounded.batching.tick set last now;
    t:.qetl.job.bounded.batching.cache;
    new:flip `worker`version`range_from`range_to`item`held`n`used!(
        n#sc`worker;n#sc`version;n#sc`range_from;n#sc`range_to;items ok;held ok;sizes ok;now);
    t:t,new;
    mine:where (t`worker)=sc`worker;
    ord:mine idesc (t`used) mine;
    keep:ord where (sums (t`n) ord)<=bound;
    drop:mine except keep;
    `.qetl.job.bounded.batching.cache set t where not (til count t) in drop;
    count drop}

/ ------------------------------------------------------------- A BATCH'S INPUTS

/ Private: the source's answer to one read of some keys, checked and split by key.
/ @param src the source's definition
/ @param br its batch_read
/ @param got what read returned
/ @param want the keys asked for
/ @return one dictionary of name -> rows per key, in the order of `keys`
/ @private
.qetl.job.bounded.batching.split_read:{[src;br;got;want]
    names:key src`supporting;
    if[not (99h=type got) and all names in key got;
        '"batch_read: read must return a dictionary of the supporting inputs ",(", " sv string names)];
    per:{[br;got;want;nm]
        tbl:got nm;
        kc:(br`keys) nm;
        if[not (98h=type tbl) and kc in cols tbl;
            '"batch_read: read's ",string[nm]," must be a table holding its key column ",string kc];
        stray:distinct (tbl kc) except want;
        if[count stray;
            '"batch_read: read returned ",string[nm]," rows for ",(", " sv string (3&count stray)#stray),$[3<count stray;", ...";""]," which were not asked for"];
        g:group tbl kc;
        {[tbl;g;k] tbl $[k in key g; g k; `long$()]}[tbl;g] each want}[br;got;want] each names;
    / per[i][j]: input i, key j -> one dictionary per key
    {[names;cuts] names!cuts}[names;] each flip per}

/ Private: read some keys from the source under the retry policy.
/ @private
.qetl.job.bounded.batching.read_keys:{[worker;w;want]
    cfg:.qetl.job.bounded.def worker;
    src:.qetl.source.def cfg`source;
    br:.qetl.source.batch_read cfg`source;
    h:.qetl.job.bounded.read_state[worker;`handle];
    r:.qetl.job.bounded.runtime.with_retry[.qetl.job.bounded.runtime.policy[];
        {[br;h;a;b;want;unused] br[`read][h;a;b;want]}[br;h;w`range_from;w`range_to;want]];
    if[`failed~r`state; '"reading the source failed after ",string[r`attempts]," attempt(s): ",$[10h=type r`error;r`error;-3!r`error]];
    `.qetl.job.bounded.batching.counts set @[.qetl.job.bounded.batching.counts;`queries;+;1];
    .qetl.job.bounded.batching.split_read[src;br;r`result;want]}

/ One batch's inputs, read from the source: its primary rows, and the references its rows need.
/ .
/ The keys come from the source's own `needs`. Those the reuse cache holds (this worker, version and
/ window) are not read again; the rest are read in one call and, when the worker declares reuse_rows,
/ kept for the batches after. The result is checked against the source's contract as a whole
/ window's would be: a source that gets a batch wrong fails the window, naming the batch.
/ @param worker the worker's name
/ @param spec its batching declaration
/ @param table_name the source's primary input
/ @param w the window
/ @param rows the batch's primary rows
/ @return the batch's inputs: a dictionary of the primary input and each supporting input
/ @throws error when the source cannot be read, or returns what its contract does not allow
/ @private
.qetl.job.bounded.batching.source_inputs:{[worker;spec;table_name;w;rows]
    cfg:.qetl.job.bounded.def worker;
    src:.qetl.source.def cfg`source;
    br:.qetl.source.batch_read cfg`source;
    want:distinct (),br[`needs] rows;
    if[not 11h=type want; '"batch_read: needs must return a symbol vector"];
    sc:.qetl.job.bounded.batching.scope[worker;w];
    reuse:spec[`reuse_rows]>0;
    hit:$[reuse; .qetl.job.bounded.batching.take[sc;want]; (`symbol$())!()];
    missing:want except key hit;
    fresh:$[count missing; .qetl.job.bounded.batching.read_keys[worker;w;missing]; ()];
    ev:$[reuse and count missing; .qetl.job.bounded.batching.put[sc;spec`reuse_rows;missing;fresh]; 0];
    `.qetl.job.bounded.batching.counts set .qetl.job.bounded.batching.counts + `hits`reads`queries`evicted!(count hit;count missing;0;ev);
    .qetl.log.dbg[worker;"batch read";`keys`cached`read`evicted!(count want;count hit;count missing;ev)];
    byitem:hit,missing!fresh;
    given:enlist[table_name]!enlist rows;
    sup:{[src;br;byitem;want;nm]
        c:(src[`supporting] nm);
        parts:{[byitem;nm;k] (byitem k) nm}[byitem;nm] each want;
        joined:$[count parts; raze parts; 0#c];
        ((br`sort) nm) xasc joined}[src;br;byitem;want] each key src`supporting;
    given:given,(key src`supporting)!sup;
    .qetl.source.validate[cfg`source;given];
    given}

\d .
