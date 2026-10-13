/ crypto_book_1s.q - the one-second book snapshot, shared by the live job and its backfill twin (#1222).
/ .
/ crypto_book_1s (streaming, a bars job) and crypto_book_1s_backfill (bounded)
/ both write crypto_book_1s under one key, (venue;sym;bar_start), and a
/ backfill REPLACES the live job's rows. So they pick the snapshot with this
/ one function, and cut windows with the one `width`, here.

\d .qpipe.transform.crypto_book_1s

/ The snapshot width. The live job declares it and the twin cuts by it.
width:0D00:00:01

/ How long after a second's end a book update may arrive and still amend its
/ snapshot. The recorder stamps source_time at the venue and the update reaches
/ the plant a few tens of milliseconds later, so half a second covers ordinary
/ jitter without making the newest snapshot wait long; an update later than
/ that is dropped and logged by the bars kind, not folded in.
lateness:0D00:00:00.5

/ The columns that decide which of two updates in one second is the later: the
/ venue's clock first, then the book's own content. Two updates of one
/ (venue, sym) at the SAME source_time are told apart by content - an arbitrary
/ but fixed rule - rather than by arrival order, so the snapshot does not depend
/ on how the rows were delivered, live or from the HDB.
order_by:`source_time`bid_prices`bid_sizes`ask_prices`ask_sizes

/ One snapshot per (venue, sym) per window: the book state of the update with
/ the greatest source_time, ties broken by order_by (the greatest content).
/ A window with no update has no row.
/ @param x book updates carrying venue, sym, source_time, the four ladders and bar_start
/ @return the snapshots, in crypto_book_1s's columns without time
/ @eg count .qpipe.transform.crypto_book_1s.snapshots ([] source_time:2#2026.10.09D10:00:05; venue:2#`a; sym:2#`BTC; bid_prices:2#enlist 1 2f; bid_sizes:2#enlist 1 1f; ask_prices:2#enlist 3 4f; ask_sizes:2#enlist 1 1f; bar_start:2#2026.10.09D10:00:05)  ->  1
snapshots:{[x]
    shape:.qetl.plant.published `crypto_book_1s;
    if[0=count x; :shape];
    x:order_by xasc x;
    b:0!select source_time:last source_time, bid_prices:last bid_prices, bid_sizes:last bid_sizes,
        ask_prices:last ask_prices, ask_sizes:last ask_sizes by venue, sym, bar_start from x;
    `bar_start`venue`sym xasc (cols shape) xcols b}

\d .
