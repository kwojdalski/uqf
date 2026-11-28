/ mapped_depth.q - the depth of a few matched books, read through their row numbers (.qetl.mapped.depth_at, #2288).
/ .
/ The one reader of market_data's ladders that is meant to exist, and deliberately NOT part of the markout path: a markout
/ prices from the flat top (mapped_books.q) and never touches a ladder - scripts/gates/tests/test_check_markout_mapped.py
/ (scripts/gates/policy/markout_mapped.py) refuses a markout file that names one, or this function. It is the other half of
/ the `rid` column: an aj returns the rid of each book it matched, and a consumer that wants the depth of THOSE books
/ indexes the mapped ladder with them, paying only those rows' pages and never a scan of the ladder.
/ .
/ Run in the process that maps market_data, like tob_at.

\d .

/ What market_data stores: each column and the absolute type of its values, 0 for a nested column (a per-row list, the four
/ ladders). The ONE place a reference's declared columns and match conditions are checked against; tests/q/test_variant_columns.q holds
/ it equal to the plant's own declaration (.qetl.plant.market_data, src/etl/plant_tables.q), which loads after this file.
.qetl.mapped.stored:`time`sym`source`market`source_time`bid_prices`bid_sizes`ask_prices`ask_sizes`clock`bid`ask`rid`two_sided_rid!12 11 11 11 12 0 0 0 0 11 9 9 7 7h

/ market_data's declared sort (.qetl.plant.sorting): the columns its rows are written in order of, the first with p#. The one place
/ the markout path names the table to read the order of; call it in a process that loaded the plant.
/ @return the sort columns in order: the equality keys, then the as-of column
/ @eg .qetl.mapped.market_order[]  ->  `sym`source`source_time
.qetl.mapped.market_order:{[] .qetl.mapped.order_of `market_data}

/ The conditions market_data declares (.qetl.plant.condition): what a variant's match may name besides an equality key.
/ @return the condition names, empty when the plant is not loaded
/ @eg .qetl.mapped.market_conditions[]  ->  enlist `two_sided
.qetl.mapped.market_conditions:{[] $[`conditions in key `.qetl.plant; exec name from .qetl.plant.conditions where table=`market_data; `symbol$()]}

/ The columns a match reads of the books beyond the order's: for each declared condition it names, the columns the condition reads;
/ an equality key is a column of the order and adds none.
/ @param columns the keys of a variant's match
/ @return the columns the conditions among them read
/ @eg .qetl.mapped.condition_reads enlist `two_sided  ->  `bid`ask
.qetl.mapped.condition_reads:{[columns]
    c:columns inter .qetl.mapped.market_conditions[];
    distinct raze {[n] .qetl.plant.condition_reads[`market_data;first exec expr from .qetl.plant.conditions where table=`market_data, name=n]} each c}

/ The conditions of market_data whose pointers books can be given: those whose columns they carry.
/ @param tob the books
/ @return condition names
/ @eg .qetl.mapped.computable ([] bid:1 2f; ask:2 3f)  ->  enlist `two_sided
.qetl.mapped.computable:{[tob] c:.qetl.mapped.market_conditions[]; c where {[tob;n] all .qetl.mapped.condition_reads[enlist n] in cols tob}[tob] each c}

/ Books in memory with the pointer columns of some of market_data's declared conditions: a column the books already carry is
/ left alone (a mapped partition's, a gathered slice's), and one they lack is derived by the same pass the writer makes, over
/ the books as arranged - a live buffer, a hand-built table, the slice. The pointer of a row is a position in THESE books.
/ @param order market_data's declared sort (a table name, or the columns)
/ @param tob the books, arranged by it
/ @param names the conditions wanted
/ @return the books with a `<name>_rid` column for each
/ @throws error naming a column the books lack that a condition reads
/ @eg cols .qetl.mapped.with_pointers[`market_data;.qetl.mapped.arrange[`market_data;([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1f; ask:enlist 2f; clock:enlist `v)];enlist `two_sided]  ->  `sym`source`source_time`bid`ask`clock`two_sided_rid
.qetl.mapped.with_pointers:{[order;tob;names]
    o:.qetl.mapped.order_of order;
    todo:names where not (.qetl.plant.pointer_col each names) in cols tob;
    {[o;tob;n]
        expr:first exec expr from .qetl.plant.conditions where table=`market_data, name=n;
        if[count miss:.qetl.plant.condition_reads[`market_data;expr] except cols tob; '"mapped: the books lack ",(", " sv string miss),", which condition ",string[n]," reads"];
        ptr:.qetl.mapped.pointer[.qetl.mapped.plain each tob each -1_o;.qetl.plant.condition_ok[tob;expr]];
        @[tob;.qetl.plant.pointer_col n;:;ptr]}[o]/[tob;todo]}

/ The columns market_data holds a list of per row: the ladders, read through a rid and never searched.
.qetl.mapped.nested:where 0=.qetl.mapped.stored

/ How many rows of columns this process has read through a rid, since it started: depth_at adds the rows it indexes. It
/ is the gather's proof that it read the books it was given and nothing else (gather, #2289).
.qetl.mapped.depth_reads:0

/ The depth of some matched books, read through their row numbers: `rid` is a book's position in its
/ partition, so a mapped depth column indexed by it pays only those rows' pages - never the day's.
/ .
/ A rid is only as good as the partition that wrote it, so each read is held to what the flat top says:
/ a book whose flat bid (or ask) is a price must have it as the first level of the ladder read at its rid. A
/ partition whose rid does not line up with its rows (written by something that did not assign it) fails
/ here, loudly, instead of returning another row's depth.
/ .
/ Run in the process that maps market_data, like tob_at.
/ @param arg a dictionary: books (table date, rid, bid, ask - as tob_at returns them) and cols (the depth
/   columns to read, e.g. `bid_prices`bid_sizes)
/ @return the books with the depth columns appended, in the books' order
/ @throws error when a book's rid does not point at the row its flat top describes
/ @eg count .qetl.mapped.depth_at[`books`cols!(0#([] date:`date$(); rid:`long$(); bid:`float$(); ask:`float$());`bid_prices`ask_prices)]  ->  0
.qetl.mapped.depth_at:{[arg]
    b:arg`books;
    cs:(),arg`cols;
    if[0=count b; :b,'flip cs!count[cs]#enlist ()];
    read:{[b;cs;d]
        ix:where (b`date)=d;
        / date-only: every column stays mapped, and indexing it by rid reads those rows' pages
        wanted:distinct `bid`ask,cs;
        m:?[`market_data;enlist (=;`date;d);0b;wanted!wanted];
        rid:(b`rid) ix;
        .qetl.mapped.depth_reads+:count rid;
        lie:"depth_at: a rid does not point at the row its flat top describes in the partition of ",string[d]," - its rid column was not assigned by the sort";
        / a rid past the partition's end, or null, is not a row
        if[not all (not null rid) & (rid>=0) & rid<count m; 'lie];
        got:{[m;rid;c] .qetl.mapped.plain (m c) rid}[m;rid] each cs;
        / the row at rid must be the book the aj matched: the same flat top ...
        same:{[x;y] (null x) | x=y};
        if[not all same[(m`bid) rid;(b`bid) ix] & same[(m`ask) rid;(b`ask) ix]; 'lie];
        / ... and the ladder read there must start at that top
        top:{[side;cs;got;ix;b]
            c:`$side,"_prices";
            $[c in cs; all (null (b`$side) ix) | (first each got cs?c)=(b`$side) ix; 1b]}[;cs;got;ix;b] each ("bid";"ask");
        if[not all top; 'lie];
        `ix`rows!(ix;flip cs!got)};
    parts:read[b;cs] each asc distinct b`date;
    ix:raze parts@\:`ix;
    rows:raze parts@\:`rows;
    b,'rows iasc ix}

/ The columns of every book a LIST of reference variants matched, read once (#2289, #3000).
/ .
/ GATHER ONCE. A variant's SELECT is the as-of search (tob_at) of its probes against the flat top; many variants share
/ a SELECT, and many SELECTs match the same book. So this runs tob_at once per distinct SELECT group - a venue set, an offset and,
/ for a variant with a match, the match and its staleness (without one the staleness only filters the books afterwards, so it is
/ not part of the group) - takes the DISTINCT books across all of them, and reads the columns the variants declared in ONE depth_at
/ call. Those columns are indexed by the books' rids and nothing else: the rows read are the distinct books, however many variants
/ asked and however many columns, which is what the check at the end holds it to. Run where market_data is mapped.
/ @param arg a dictionary: order (market_data's declared sort), probes (table sym, time), read_from, skew, groups (a list of SELECT groups, each (venues; offset; match; limit): `all or a symbol list, a timespan, a match dictionary or an empty one, a timespan) and cols (the columns to read beyond the flat top: ladders, market, time ...)
/ @return a dictionary: slice (the distinct books: sym, source, source_time, bid, ask, clock, rid, date, and the columns asked), reads (rows read of the extra columns)
/ @throws error when the rows read differ from the distinct books asked for
/ @eg count (.qetl.mapped.gather`order`probes`read_from`skew`groups`cols!(`sym`source`source_time;0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;enlist (`all;0D;(`symbol$())!();0Nn);`bid_prices`ask_prices))`slice  ->  0
.qetl.mapped.gather:{[arg]
    one:{[arg;g]
        p:update time:time-g 1 from arg`probes;
        a:`order`probes`read_from`skew`venues!(arg`order;p;(arg`read_from)-g 1;arg`skew;g 0);
        if[count g 2; a,:`match`limit!(g 2;g 3)];
        .qetl.mapped.tob_at a};
    parts:one[arg] each arg`groups;
    books:distinct raze parts;
    if[(count books)<>count select distinct date, rid from books; '"gather: the books are not distinct by (date; rid)"];
    / what the flat top already holds is not read again
    want:(distinct (),arg`cols) except cols books;
    / nothing beyond the flat top asked for: the flat top is the whole answer, no other column is touched
    if[0=count want; :`slice`reads!(books;0)];
    before:.qetl.mapped.depth_reads;
    slice:.qetl.mapped.depth_at`books`cols!(books;want);
    reads:.qetl.mapped.depth_reads-before;
    if[reads<>count books; '"gather: read ",string[reads]," rows of the extra columns for ",string[count books]," distinct books"];
    `slice`reads!(slice;reads)}

\d .
