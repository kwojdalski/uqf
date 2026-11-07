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

\d .
