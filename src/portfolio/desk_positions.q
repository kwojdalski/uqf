/ desk_positions.q - net FX exposure along whatever dimensions a desk
/ reports on (loads into .qf.port). Requires ccy.q (ccy_pair_legs) to be loaded first.
/ .
/ WHY THIS IS NOT positions.q, AND NOT allocation.q. Three position
/ modules sounds like two too many, so here is what each is for:
/ .
/   positions.q          a running book per SYM, weighted-average-cost, carrying
/                        realised P&L. One dimension, and P&L is the point.
/   allocation.q         (.qf.exec) lot matching, which recomputes from the trades
/                        under a chosen method. Any dimensions, but O(trades)
/                        every time.
/   desk_positions.q     a running NETTED book along declared dimensions. No lots,
/                        no average cost, no P&L - just what the desk is long and
/                        short, and in which currencies.
/ .
/ A risk engine asks a different question from a P&L engine. "What is the
/ EURUSD desk's net EUR exposure across every product it trades" does not
/ need to know which buy paid for which sell, and computing that to answer
/ it would be paying for a fact nobody asked for. So this nets, and it
/ nets along dimensions that are DECLARED rather than fixed at `sym: a
/ book keyed on (sym, product, book) is the shape a trading desk actually
/ reports, and positions.q cannot hold one.
/ .
/ WHAT A BOOK CARRIES. Two quantities, because an FX position is two
/ currencies and reporting only one of them is how a cross position hides:
/ .
/   base_qty   net base-currency units. Long 1mm EURUSD is +1,000,000 EUR.
/   quote_qty  net quote-currency cash. That same fill at 1.0850 is
/              -1,085,000 USD. Signed opposite base_qty, because buying
/              one currency is selling the other - that is the whole of
/              what an FX trade is, and ccy.q's ccy_pair_legs already says
/              so for a single position.
/ .
/ Both are running SUMS over fills, so quote_qty is the actual cash that
/ changed hands rather than base_qty revalued at some average. The two
/ differ as soon as a book has bought and sold, and the difference is
/ exactly the trading result - see break_even.
/ .
/ NO MARKS AND NO P&L, deliberately. Marking needs a price source, a
/ convention for a pair never quoted, and a decision about which currency
/ the answer is in - all of which .qpipe.job.posbook already makes for the P&L
/ view. Two modules marking the same book two ways would drift. Exposure
/ is a fact about the fills alone, and that is what this file computes.

\d .qf.port

/ The columns a fill table must carry, on top of the dimensions.
/ .
/ `side is +1 buy / -1 sell and `size is unsigned, the same convention
/ .qf.port.apply_fills and .qf.exec take - so one fill table feeds the book,
/ the attribution and the exposure without a translation step. A venue
/ that spells it `BUY/`SELL converts once, at the edge that reads it.
/ fxpositions1 feeds this module `executions`, the one fill tape posbook
/ reads too (#885).
required_cols:`sym`side`size`price

/ An empty book keyed on the given dimensions.
/ .
/ The dimensions are an ARGUMENT rather than a constant because that is
/ the whole point of this module, and they are carried in the book's own
/ key rather than remembered somewhere else - so a book and the dimensions
/ it is keyed on cannot come apart, and apply_desk_fills can read them back off
/ the book it was handed.
/ @param dims the grouping columns, e.g. `sym or `sym`product`book
/ @return an empty keyed table: dims -> base_qty, quote_qty, fill_count
/ @throws error when dims is empty - a book with no dimensions is one row and says nothing
/ @eg cols .qf.port.empty_desk_book[`sym`book] -> `sym`book`base_qty`quote_qty`fill_count
empty_desk_book:{[dims]
    d:(),dims;
    if[0=count d; '"empty_desk_book: a book needs at least one dimension to be keyed on"];
    if[not 11h=abs type d; '"empty_desk_book: dimensions must be column names, as symbols"];
    d xkey flip (d,`base_qty`quote_qty`fill_count)!
        ((count d)#enlist `symbol$()),(`float$();`float$();`long$())}

/ The dimensions a book is keyed on.
/ @param book a book from empty_desk_book
/ @return the dimension columns, in key order
/ @eg .qf.port.dimensions .qf.port.empty_desk_book[`sym`book] -> `sym`book
dimensions:{[book] keys book}

/ Net a batch of fills into a book.
/ .
/ The book is threaded in and out, never mutated in place - the convention
/ every module here follows, and what lets a batch against a given book
/ have an expected answer at all.
/ .
/ WHY THE BATCH IS AGGREGATED FIRST. A tick batch is many fills over few
/ dimension combinations, so netting the batch down before touching the
/ book turns one pass over the batch plus one over its distinct keys into
/ the whole cost. The alternative - folding fill by fill - is the shape
/ .qf.port.apply_fills has, and it is right there because average cost is
/ path-dependent. Netting is not: sums commute, so a batch has one answer
/ however it is ordered, and that is worth exploiting.
/ @param book the book to add to, keyed on its own dimensions
/ @param batch the fills: a table with sym, side, size, price and every
/   dimension (`batch`, because `fills` is a q builtin)
/ @return the updated book
/ @throws error naming any missing column, or counting fills with a null side, size or price
/ @eg exec base_qty from .qf.port.apply_desk_fills[.qf.port.empty_desk_book[`sym];([] sym:`EURUSD`EURUSD; side:1 -1; size:1000000 400000f; price:1.085 1.086)] -> enlist 600000f
apply_desk_fills:{[book;batch]
    dims:dimensions[book];
    if[not 98h=type batch; '"apply_desk_fills: fills must be a table"];
    .qf.util.require_cols[`apply_desk_fills;`fills;batch;distinct required_cols,dims];
    if[0=count batch; :book];
    / sum skips a null, so a null price would net into base_qty and not
    / quote_qty (a null size into neither, yet still count) - a book missing
    / its cash leg, and a break_even that looks fine (#1309). Refused.
    n_bad:sum (null batch`side) or (null batch`size) or null batch`price;
    if[n_bad;
        '"apply_desk_fills: ",string[n_bad]," fill(s) have a null side, size or price - a fill that cannot be netted must not be netted on one side only"];
    netted:?[batch; (); {x!x} dims;
        `base_qty`quote_qty`fill_count!(
            (sum;(*;`side;`size));
            (sum;(*;(neg;`side);(*;`size;`price)));
            (count;`i))];
    / Re-aggregate the union rather than plus-joining onto the book: pj
    / drops any key the LEFT table does not already have, so a fill in a
    / product the book has never seen would be silently discarded - and a
    / first fill in a new product is the most ordinary event there is.
    dims xkey ?[(0!book),0!netted; (); {x!x} dims;
        `base_qty`quote_qty`fill_count!((sum;`base_qty);(sum;`quote_qty);(sum;`fill_count))]}

/ The rate at which a book has neither made nor lost: the quote cash it
/ has taken in, per unit of base it is holding.
/ .
/ For a book that has only ever bought, this is the weighted-average price
/ it paid. For one that has bought and sold, it is not an average of
/ anything - it is the level at which closing out leaves the desk flat in
/ both currencies, which is the number a risk report wants and the one a
/ weighted average stops being once a position has turned over.
/ .
/ A flat book (no base left) has no break-even rate, and gets a null
/ rather than a division by zero: 0f%0f is 0n in q, but relying on that
/ would leave a reader guessing whether the zero was a price.
/ .
/ "Flat" is within flat_tolerance, not exactly 0f. base_qty is a sum of
/ floats, and fractional sizes rarely net to an exact zero: buying 0.1 and
/ 0.2 BTC and selling 0.3 leaves 5.6e-17, and dividing quote cash by that
/ published a break-even of -7.2e17 for a closed position (#811).
/ @param book a book from apply_desk_fills
/ @return the book with a break_even column added
/ @eg exec break_even from .qf.port.break_even .qf.port.apply_desk_fills[.qf.port.empty_desk_book[`sym];([] sym:enlist `EURUSD; side:enlist 1; size:enlist 1000000f; price:enlist 1.085)] -> enlist 1.085
break_even:{[book]
    / A local, not the global by name: inside qSQL KDB-X resolves a bare
    / global at the ROOT, where no flat_tolerance exists. The constant is
    / risk.q's - this file used to keep an alias of it, which became the same
    / name twice in one namespace when the two merged (#1411) - so it is named
    / in full, which is also how check_module_deps.py sees the dependency.
    tol:.qf.port.flat_tolerance;
    update break_even:?[tol>abs base_qty; 0n; (neg quote_qty)%base_qty] from book}

/ Net exposure per currency across a whole book.
/ .
/ The one number a risk desk actually runs on, and the reason base_qty and
/ quote_qty are both kept: a currency reached through several different
/ pairs nets into ONE figure. A desk long EURUSD and short EURGBP is not
/ long two things, it is long GBP and flat-ish EUR, and only a
/ per-currency netting says so.
/ .
/ This is .qf.port.ccy_exposure's question asked of a netted book rather than
/ an average-cost one. It decomposes through the same ccy.q primitive, so
/ the two agree about which currency is which; what differs is the input,
/ because a netted book already carries both legs and has no average price
/ to re-derive them from.
/ .
/ Raw currency units, not comparable across currencies - revalue with
/ .qf.port.ccy_exposure_in when one reporting number is wanted.
/ @param book a book from apply_desk_fills
/ @param group_cols the dimensions to report within, e.g. `book, or () for the whole desk
/ @return a table of group_cols plus ccy and amount
/ @eg exec amount from .qf.port.desk_ccy_exposure[.qf.port.apply_desk_fills[.qf.port.empty_desk_book[`sym];([] sym:enlist `EURUSD; side:enlist 1; size:enlist 1000000f; price:enlist 1.085)];()] -> 1000000 -1085000f
desk_ccy_exposure:{[book;group_cols]
    g:(),group_cols;
    rows:0!book;
    / Empty, but the SAME shape as a non-empty result: keyed on the group
    / columns (typed as the book has them) plus ccy, with amount - it used to
    / come back unkeyed, without the group columns at all.
    / Built from the book's own (empty, typed) columns rather than with ,'
    / over two empty tables, which PeachQ returns as () - this suite is in the
    / PeachQ-portable set.
    if[0=count rows; :(g,`ccy) xkey flip (g,`ccy`amount)!(rows g),(`symbol$();`float$())];
    legs:.qf.px.ccy_pair_legs each rows`sym;
    / Two rows out of every one in - the base leg and the quote leg - so
    / the group columns are taken twice, in the same order the amounts are.
    long_form:?[rows;();0b;(g,`sym)!g,`sym] ,' ([] ccy:legs`base; amount:rows`base_qty);
    long_form:long_form,?[rows;();0b;(g,`sym)!g,`sym] ,' ([] ccy:legs`quote; amount:rows`quote_qty);
    ?[long_form; (); {x!x} g,`ccy; enlist[`amount]!enlist (sum;`amount)]}

/ Roll a book up onto fewer dimensions.
/ .
/ Nets away the dimensions not asked for, rather than filtering to them -
/ a desk-level figure is the sum over the desk's products, not one of
/ them. The result is keyed on what was asked for, so it is a book of the
/ same kind and every function here still applies to it.
/ @param book a book from apply_desk_fills
/ @param group_cols the dimensions to keep - a subset of the book's own
/ @return a book keyed on group_cols
/ @throws error when a requested column is not one of the book's dimensions
/ @eg .qf.port.dimensions .qf.port.rollup[.qf.port.empty_desk_book[`sym`book];enlist `book] -> enlist `book
rollup:{[book;group_cols]
    g:(),group_cols;
    unknown:g where not g in dimensions book;
    if[count unknown;
        '"rollup: ",(", " sv string unknown)," is not a dimension of this book - it is keyed on ",
         ", " sv string dimensions book];
    g xkey ?[0!book; (); {x!x} g;
        `base_qty`quote_qty`fill_count!((sum;`base_qty);(sum;`quote_qty);(sum;`fill_count))]}

\d .
