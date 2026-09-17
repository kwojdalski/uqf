/ databento_book_backfill.q - Databento MBP-10 into an order-book table
/ (.qwrk.databento_book_backfill).
/ .
/ A declaration over the generic shell (#124), over the first ODBC source.
/ Its transform is the first in this tree that changes the shape of what it
/ fetched: Databento's forty per-level columns fold into four level-0-first
/ vectors, the book shape .qfwd.cross_book_at and .qbook already read.
/ .
/ Windows are ten minutes. The opening ten minutes of 2026-02-25 alone is
/ 778,176 rows across six symbols, fetched in about ten seconds over ODBC
/ under Rosetta; a wider window makes a failed window expensive to redo and
/ holds more of the book in memory at once.

\d .qwrk.databento_book_backfill

worker_name:`databento_book_backfill

/ --- the contract's required globals (ETL-01, ETL-02) --------------------

source_version:`;
range_from:0Np;
range_to:0Np;

/ An ODBC handle is a long, not an int; null either way until init.
handle:0Ni;

progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ --- the contract's required methods, delegated -------------------------

/ Ordinary names in this namespace that happen to delegate. A worker needing
/ a genuinely different publish path defines its own here and the shell does
/ not object - .qbw is a default, not an owner.
spec:{[] .qbw.spec worker_name}
init:{[run_spec] .qbw.init[worker_name;run_spec]}
plan:{[cursor] .qbw.plan[worker_name;cursor]}
fetch:{[from_ts;to_ts] .qbw.fetch[worker_name;from_ts;to_ts]}
publish:{[batch] .qbw.publish[worker_name;batch]}
checkpoint:{[cursor] .qbw.checkpoint[worker_name;cursor]}
run:{[] .qbw.run worker_name}
cleanup:{[] .qbw.cleanup worker_name}

/ --- the transform ---------------------------------------------------------

/ The source contract, as the empty table the transform reads.
contract:flip .qsdbn.fields!{[c] c$()} each .qsdbn.types

book:([] time:`timestamp$(); sym:`symbol$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$();
    bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Private: one side and quantity's ten level columns, as a vector per row.
/ `flip` over the columns rather than `each` over rows: one pass per column.
fold:{[batch;prefix] flip batch `$prefix,/:.qsdbn.levels}

/ Fold Databento's per-level columns into level-0-first vectors.
/ .
/ time and sym are renamed to this library's convention; everything else
/ passes through. An empty batch yields a typed empty book - flip over ten
/ empty columns would otherwise give a list with no rows to type.
/ @param batch MBP-10 records in the source contract's shape
/ @return one book row per record
to_book:{[batch]
    if[0=count batch; :.qwrk.databento_book_backfill.book];
    ([] time:batch`ts_event; sym:batch`symbol; action:batch`action; side:batch`side;
        price:batch`price; size:batch`size; sequence:batch`sequence;
        bid_prices:fold[batch;"bid_px_"]; bid_sizes:fold[batch;"bid_sz_"];
        ask_prices:fold[batch;"ask_px_"]; ask_sizes:fold[batch;"ask_sz_"])}

/ The example is the source's own fixture, and the expected book is written
/ out: the fixture's levels step one cent from the touch and 100 in size.
example_book:{[]
    f:.qsdbn.fixture[];
    px:{[touch;dir] touch+dir*0.01*til 10};
    sz:{[s] s+100*til 10};
    ([] time:f`ts_event; sym:`AAPL`AAPL`META`META; action:`A`T`A`C; side:`B`N`A`A;
        price:271.2 271.65 643.48 643.48; size:200 21 10 10; sequence:28151775 28158778 29559422 29563793;
        bid_prices:(px[271.45;-1];px[271.45;-1];px[642f;-1];px[642f;-1]);
        bid_sizes:(sz 500;sz 479;sz 100;sz 100);
        ask_prices:(px[271.66;1];px[271.66;1];px[643f;1];px[643.01;1]);
        ask_sizes:(sz 500;sz 479;sz 100;sz 100))}

.qxf.define[`databento_book;`inputs`output`fn`examples!(
    enlist[`batch]!enlist contract;
    book;
    to_book;
    enlist `inputs`expected!(enlist[`batch]!enlist .qsdbn.fixture[];example_book[]))];

/ --- the data-quality gate ------------------------------------------------

/ Refuse a window whose book is crossed or whose price is not positive.
/ .
/ Both are conditions this data does not contain, not merely rare ones:
/ across all 46,038,453 rows loaded, no record had bid_px_00 >= ask_px_00, a
/ missing level, or a price <= 0. A single venue's own book cannot be
/ crossed after an event is applied, so one that is means the levels were
/ mis-folded - exactly the mistake the transform could make.
/ @param batch the transformed book
/ @return a table check/status/detail, one row per offending record
quality_check:{[batch]
    if[0=count batch; :.qbw.no_failures[]];
    crossed:select from batch where (first each bid_prices)>=first each ask_prices;
    bad_price:select from batch where not price>0;
    raze {[nm;t]
        if[0=count t; :.qbw.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.Q.s1 each select time, sym, sequence from t)
      }'[`crossed_top_of_book`nonpositive_price;(crossed;bad_price)]}

\d .

.qbw.define[`databento_book_backfill;
    `source`dataset`width`transform`check!
    (`databento_mbp10;`databento_book;0D00:10:00;`databento_book;.qwrk.databento_book_backfill.quality_check)];
