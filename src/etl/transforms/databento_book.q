/ databento_book.q - the shared MBP-10 fold used by live and bounded jobs.
/ The source contract and worked example belong to the transform, independently
/ of either job lifecycle.

\d .qpipe.transform.databento_book

/ --- the transform ---------------------------------------------------------

/ The source contract, as the empty table the transform reads.
contract:flip .qpipe.source.databento_mbp10.columns!{[c] c$()} each .qpipe.source.databento_mbp10.types

book:([] time:`timestamp$(); sym:`symbol$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Private: one side and quantity's ten level columns, as a vector per row.
/ `flip` over the columns rather than `each` over rows: one pass per column.
fold:{[batch;prefix] flip batch `$prefix,/:.qpipe.source.databento_mbp10.levels}

/ Fold Databento's per-level columns into level-0-first vectors.
/ .
/ time and sym are renamed to this library's convention; everything else
/ passes through. An empty batch yields a typed empty book - flip over ten
/ empty columns would otherwise give a list with no rows to type.
/ @param batch MBP-10 records in the source contract's shape
/ @return one book row per record
to_book:{[batch]
    if[0=count batch; :.qpipe.transform.databento_book.book];
    ([] time:batch`ts_event; sym:batch`symbol; action:batch`action; side:batch`side;
        price:batch`price; size:batch`size; sequence:batch`sequence;
        bid_prices:fold[batch;"bid_px_"]; bid_sizes:fold[batch;"bid_sz_"];
        ask_prices:fold[batch;"ask_px_"]; ask_sizes:fold[batch;"ask_sz_"])}

/ The example is the source's own fixture, and the expected book is written
/ out: the fixture's levels step one cent from the touch and 100 in size.
example_book:{[]
    f:.qpipe.source.databento_mbp10.fixture[];
    px:{[touch;dir] touch+dir*0.01*til 10};
    sz:{[s] s+100*til 10};
    ([] time:f`ts_event; sym:`AAPL`AAPL`META`META; action:`A`T`A`C; side:`B`N`A`A;
        price:271.2 271.65 643.48 643.48; size:200 21 10 10; sequence:28151775 28158778 29559422 29563793;
        bid_prices:(px[271.45;-1];px[271.45;-1];px[642f;-1];px[642f;-1]);
        bid_sizes:(sz 500;sz 479;sz 100;sz 100);
        ask_prices:(px[271.66;1];px[271.66;1];px[643f;1];px[643.01;1]);
        ask_sizes:(sz 500;sz 479;sz 100;sz 100))}

.qetl.transform.define[`databento_book;`inputs`output`fn`examples!(
    enlist[`batch]!enlist contract;
    book;
    to_book;
    enlist `inputs`expected!(enlist[`batch]!enlist .qpipe.source.databento_mbp10.fixture[];example_book[]))];

\d .
