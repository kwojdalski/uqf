/ variant_run.q - a LIST of reference variants over one batch: gather once, evaluate many (.qetl.variant, #2289).
/ .
/ Reference prices may be wanted in a thousand ways, and a stored column per way is impossible at the size market
/ data runs to. A variant is SELECT x TRANSFORM x COMBINE (reference_variant.q), so a list of them factors:
/ .
/   1 SELECT     per DISTINCT (venue set, staleness, offset): the as-of search, which returns the books (by rid).
/                Run where market_data is mapped (.qetl.mapped.gather); here, against the gathered slice.
/   2 DEDUPE     the distinct books across every variant - many variants need the same few.
/   3 GATHER     the ladders of those books, read ONCE through their rids: a slice of k rows x depth.
/   4 TRANSFORM  every distinct (transform; parameters), once, vectorised over the slice (a flat one over the books
/                a SELECT matched).
/   5 COMBINE    each variant's, across the books matched to each instant.
/ .
/ COST. The market is touched in steps 1-3 and only through the flat keys and the deduped rids, so it is the batch's
/ (fills x horizons x venues) and does not depend on how many variants there are. A thousand variants over a batch cost
/ one gather and then step 4's per-transform and step 5's per-variant arithmetic over k rows - and that is all that
/ grows. tests/q/test_variant_scale.q measures it.
/ .
/ THE SLICE. What step 3 returns, arranged and numbered: `rid` is the row's own index, so a transform's answer for the
/ whole slice is read for the books a variant matched by indexing it with their rids. A live job's bounded buffer is the
/ same table by another road (`live`), so the live path evaluates the same declared variants.
/ .
/ A variant is added by declaring it (src/etl/variants/*.q): nothing here changes.

\d .

/ The ladder columns a depth transform reads, and the gather brings.
.qetl.variant.depth_cols:`bid_prices`bid_sizes`ask_prices`ask_sizes

/ How far past the spec's max_age a variant's own staleness may reach back, at most, when the books are read.
.qetl.variant.reach_cap:7D

/ A symbol that tells one value from every other: the digest of its serialisation. Lists of dictionaries, which a plan
/ must tell apart, are not safely compared with find on every q, so the plan compares these.
/ @param x any value
/ @return a symbol of 32 hex digits
/ @eg count string .qetl.variant.key_of enlist[`qty]!enlist 1e5  ->  32
.qetl.variant.key_of:{[x] `$raze string md5 "c"$-8!x}

/ The variants as a plan: one row each, with the SELECT and TRANSFORM it asks for, and the ids of the distinct
/ SELECT group and TRANSFORM it shares with others.
/ @param names registered variant names
/ @param max_age the spec's reference age limit, where a variant's stale is `spec
/ @return table name, venues, limit, offset, transform, needs, select_id, transform_id
/ @throws error when a name is not registered
/ @eg (.qetl.variant.plan[`best_top`best_top;0D00:00:05])`select_id  ->  0 0
.qetl.variant.plan:{[names;max_age]
    defs:.qetl.reference.def each names;
    lim:{[ma;d] $[`spec~d`stale; ma; d`stale]}[max_age] each defs;
    sk:.qetl.variant.key_of each {[d;l] (d`venues;l;d`offset)}'[defs;lim];
    tk:.qetl.variant.key_of each {[d] (d`transform;d`params)} each defs;
    / the parameters stay in the definitions: a column of same-keyed dictionaries would be flipped into a table
    ([] name:names; venues:defs@\:`venues; limit:lim; offset:defs@\:`offset; transform:defs@\:`transform;
        needs:{[t] (.qetl.reference.transform t)`needs} each defs@\:`transform; select_id:(distinct sk)?sk; transform_id:(distinct tk)?tk)}

/ The SELECT groups the market is asked about: one per distinct (venue set; offset). The staleness does not
/ change WHICH book is the latest at an instant, only whether it is used, so it is not a group of the mapped read.
/ @param plan a plan
/ @return table venues, offset
/ @eg count .qetl.variant.mapped_groups .qetl.variant.plan[`best_top`best_top;0D00:00:05]  ->  1
.qetl.variant.mapped_groups:{[plan]
    kk:.qetl.variant.key_of each {(x;y)}'[plan`venues;plan`offset];
    ix:kk?distinct kk;
    ([] venues:(plan`venues) ix; offset:(plan`offset) ix)}

/ The oldest book any variant of the list can use, given the oldest the spec's own would: a variant whose staleness
/ is longer than the spec's reaches further back (to at most reach_cap further).
/ @param plan a plan
/ @param max_age the spec's reference age limit
/ @param read_from the oldest source_time a book may have for the spec's own age limit
/ @return a timestamp, no later than read_from
/ @eg .qetl.variant.reads_from[.qetl.variant.plan[enlist `best_top;0D00:00:05];0D00:00:05;2026.09.17D10:00:00]  ->  2026.09.17D10:00:00.000000000
.qetl.variant.reads_from:{[plan;max_age;read_from]
    extra:(max plan`limit) - max_age;
    read_from-.qetl.variant.reach_cap&0D|extra}

/ The books the ladders are for, as the table a depth transform reads: arranged by sym, source, source_time, with
/ p# on sym, and `rid` renumbered to the row's own index. date and the partition's rid stay behind.
/ @param books a gathered slice, or a live buffer: sym, source, source_time, bid, ask, clock (and ladders)
/ @return the books arranged, rid being 0, 1, 2, ...
/ @eg (.qetl.variant.slice ([] sym:`b`a; source:`x`x; source_time:2#2026.09.17D10:00:00; bid:1 2f; ask:2 3f; clock:2#`venue))`rid  ->  0 1
.qetl.variant.slice:{[books]
    keep:(cols books) except `rid`date;
    update rid:til count i from .qetl.mapped.arrange keep#books}

/ Gather the books a list of variants needs, once: the HDB runs every SELECT group, dedupes the books and reads their
/ ladders through their rids (.qetl.mapped.gather); this returns the slice, in memory.
/ @param h the handle to the process that maps market_data
/ @param probes table sym, time - the instants priced
/ @param read_from the oldest source_time a book may have under the spec's own age limit
/ @param skew days either side of a time's date its books may be stored
/ @param names the variants
/ @param max_age the spec's reference age limit
/ @return a dictionary: slice (the books, arranged, rid their own index), reads (ladder rows read), groups (SELECT groups run)
/ @eg count .qetl.variant.read[{value x};0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;enlist `best_top;0D00:00:05]`slice  ->  0
.qetl.variant.read:{[h;probes;read_from;skew;names;max_age]
    p:.qetl.variant.plan[distinct (),names;max_age];
    g:.qetl.variant.mapped_groups p;
    ladders:$[any `depth=p`needs; .qetl.variant.depth_cols; `symbol$()];
    arg:`probes`read_from`skew`groups`cols!(probes;.qetl.variant.reads_from[p;max_age;read_from];skew;g;ladders);
    r:.qetl.source.ipc_call[h;{[a] .qetl.mapped.gather a};enlist arg];
    `slice`reads`groups!(.qetl.variant.slice r`slice;r`reads;count g)}

/ Private: the first variant of each id in a plan's id column, in id order.
/ @private
.qetl.variant.reps:{[ids] {[ids;g] first where g=ids}[ids] each til 1+max -1,ids}

/ Private: one depth transform, once, over the whole slice - or () for a transform of the flat top.
/ @private
.qetl.variant.over_slice:{[tob;names;defs;t;r]
    if[not `depth=.qetl.reference.transform[t]`needs; :()];
    books:.qetl.reference.require_slice[names r;tob];
    .qetl.reference.transform[t][`fn][books;(defs r)`params]}

/ Private: one flat transform over the books one SELECT matched - or () where it matched nothing or the transform reads depth.
/ @private
.qetl.variant.over_matched:{[j;defs;t;r]
    if[(() ~ j) or `depth=.qetl.reference.transform[t]`needs; :()];
    .qetl.reference.transform[t][`fn][j;(defs r)`params]}

/ Every variant of a list at each target (sym; time), over books already in memory.
/ .
/ One SELECT per distinct (venues; staleness; offset); one depth transform per distinct (transform; parameters),
/ over the whole slice; one flat transform per distinct (SELECT; transform; parameters), over the books that SELECT
/ matched; one combine per variant. The books are read as they are, never sorted or copied.
/ @param names the variants
/ @param tob the books: arranged; a slice (rid their own index, with ladders) where any variant reads depth
/ @param max_age the spec's reference age limit
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return a dictionary variant -> `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target, as .qetl.reference.at
/ @throws error when a variant reads depth and tob is not a slice
/ @eg ((.qetl.variant.evaluate[enlist `best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue])`best_top)[`same]`mid  ->  enlist 0n
.qetl.variant.evaluate:{[names;tob;max_age;syms;times;clocks]
    names:distinct (),names;
    n:count syms;
    none:`same`any!2#enlist ([] bid:n#0n; ask:n#0n; mid:n#0n);
    p:.qetl.variant.plan[names;max_age];
    defs:.qetl.reference.def each names;
    / 1 SELECT, once per group
    js:{[defs;tob;ma;sy;tm;ck;r] .qetl.reference.chosen[defs r;tob;ma;sy;tm;ck]}[defs;tob;max_age;syms;times;clocks] each .qetl.variant.reps p`select_id;
    / 4 TRANSFORM: a depth one once over the slice, a flat one once per SELECT that uses it
    tr:.qetl.variant.reps p`transform_id;
    dpx:{[tob;names;defs;p;r] .qetl.variant.over_slice[tob;names;defs;p[`transform] r;r]}[tob;names;defs;p] each tr;
    / one flat transform per (SELECT; TRANSFORM) pair, the pair being one number
    pid:(p`select_id)+(1+max p`select_id)*p`transform_id;
    pairs:distinct pid;
    fpx:{[js;defs;p;pid;q] r:first where pid=q; .qetl.variant.over_matched[js p[`select_id] r;defs;p[`transform] r;r]}[js;defs;p;pid] each pairs;
    / 5 COMBINE, per variant
    c:`js`dpx`fpx`pairs`pid`defs`p`none`n!(js;dpx;fpx;pairs;pid;defs;p;none;n);
    one:{[c;i]
        p:c`p;
        j:(c`js) p[`select_id] i;
        if[() ~ j; :c`none];
        px:$[`depth=p[`needs] i; (c`dpx)[p[`transform_id] i] j`rid; (c`fpx) (c`pairs)?(c`pid) i];
        .qetl.reference.folded[(c`defs) i;j;px;c`n]}[c];
    names!one each til count names}

/ The same variants over a live job's bounded buffer: the buffer arranged and numbered as a gathered slice is,
/ then evaluated. A variant that reads depth needs the buffer to carry the ladders.
/ @param names the variants
/ @param buffer the live buffer: sym, source, source_time, bid, ask, clock (and ladders), in any order
/ @param max_age the spec's reference age limit
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return as evaluate
/ @eg count .qetl.variant.live[enlist `best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]  ->  1
.qetl.variant.live:{[names;buffer;max_age;syms;times;clocks]
    .qetl.variant.evaluate[names;.qetl.variant.slice buffer;max_age;syms;times;clocks]}

/ TIER 1. A hot variant is stored instead of recomputed: its transform's answer is written beside each book as flat
/ columns (bid, ask and the weight w) - what an ingest or a backfill would put in the table - and a promoted variant,
/ the same SELECT and COMBINE over the `stored` transform, reads them. The columns are made by the variant's OWN
/ transform function, so promoting changes where the value lives and not what it is; tests/q/test_variant_run.q holds the
/ two bit-identical.
/ @param name a registered variant
/ @param slice the books, with ladders where the variant's transform reads depth
/ @return the books with bid, ask and w replaced by the variant's transform's answer for each, ladders dropped
/ @throws error when the transform reads depth and the books are not a slice
/ @eg (.qetl.variant.flat[`best_top;.qetl.variant.slice ([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1f; ask:enlist 2f; clock:enlist `venue)])`w  ->  enlist 1f
.qetl.variant.flat:{[name;slice]
    d:.qetl.reference.def name;
    t:.qetl.reference.transform d`transform;
    if[`depth~t`needs; .qetl.reference.require_slice[name;slice]];
    px:t[`fn][slice;d`params];
    keep:(cols slice) except .qetl.variant.depth_cols,`bid`ask`w;
    update `p#sym from (keep#slice),'([] bid:px`bid; ask:px`ask; w:$[`w in cols px; px`w; (count px)#1f])}

/ Register the promoted form of a variant: the same SELECT and COMBINE, reading the columns flat gives.
/ @param name a registered variant
/ @return the promoted variant's name, `<name>_flat`
/ @throws error when name is not registered
/ @eg .qetl.variant.promote `best_top  ->  `best_top_flat
.qetl.variant.promote:{[name]
    d:.qetl.reference.def name;
    .qetl.reference.register[`$(string name),"_flat";`venues`stale`offset`transform`combine!(d`venues;d`stale;d`offset;`stored;d`combine)]}

/ The results of evaluate as one long table with a `variant` key: variant, row (the target's index), bid, ask, mid -
/ the books on the target's own clock.
/ @param res the dictionary evaluate returns
/ @return the long table, variants in the dictionary's order, targets in theirs
/ @eg count .qetl.variant.long enlist[`v]!enlist `same`any!2#enlist ([] bid:2#1f; ask:2#2f; mid:2#1.5)  ->  2
.qetl.variant.long:{[res]
    raze {[nm;r] t:r`same; (([] variant:(count t)#nm; row:til count t)),'t}'[key res;value res]}

\d .
