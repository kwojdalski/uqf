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

/ How many ladder rows this process has read through a rid, since it started: depth_at adds the rows it indexes. It
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
        got:{[m;rid;c] (m c) rid}[m;rid] each cs;
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

/ The depth of every book a LIST of reference variants matched, read once (#2289).
/ .
/ GATHER ONCE. A variant's SELECT is the as-of search (tob_at) of its probes against the flat top; many variants share
/ a SELECT, and many SELECTs match the same book. So this runs tob_at once per distinct (venue set, offset) - the stale
/ limit only filters the books afterwards - takes the DISTINCT books across all of them, and reads their ladders in ONE
/ depth_at call. The ladder columns are indexed by those books' rids and nothing else: the rows read are the distinct
/ books, however many variants asked, which is what the check at the end holds it to. Run where market_data is mapped.
/ @param arg a dictionary: probes (table sym, time), read_from, skew, groups (table venues, offset - `all or a symbol list, and a timespan) and cols (the ladder columns)
/ @return a dictionary: slice (the distinct books: sym, source, source_time, bid, ask, clock, rid, date, and the ladder columns), reads (ladder rows read)
/ @throws error when the rows read differ from the distinct books asked for
/ @eg count (.qetl.mapped.gather`probes`read_from`skew`groups`cols!(0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;([] venues:enlist `all; offset:enlist 0D);`bid_prices`ask_prices))`slice  ->  0
.qetl.mapped.gather:{[arg]
    one:{[arg;g]
        p:update time:time-g`offset from arg`probes;
        .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;(arg`read_from)-g`offset;arg`skew;g`venues)};
    parts:one[arg] each arg`groups;
    books:distinct raze parts;
    if[(count books)<>count select distinct date, rid from books; '"gather: the books are not distinct by (date; rid)"];
    / no depth variant among them: the flat top is the whole answer, no ladder is touched
    if[0=count arg`cols; :`slice`reads!(books;0)];
    before:.qetl.mapped.depth_reads;
    slice:.qetl.mapped.depth_at`books`cols!(books;arg`cols);
    reads:.qetl.mapped.depth_reads-before;
    if[reads<>count books; '"gather: read ",string[reads]," ladder rows for ",string[count books]," distinct books"];
    `slice`reads!(slice;reads)}

\d .
