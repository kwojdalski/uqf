// test_allocation.q - tests for src/portfolio/allocation.q. Load
// src/portfolio/risk.q, src/portfolio/positions.q, src/portfolio/allocation.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .alloctest

/ Two buys then one sell that closes exactly one of them. The prices are
/ 1, 2 and 4 so that every P&L in this file is exactly representable in
/ binary and the methods can be compared with = rather than a tolerance -
/ a matching engine's disagreements should be whole lots, not rounding.
mk_two_lots:{[]
    ([] time:2026.01.01D09:00:00 2026.01.01D10:00:00 2026.01.01D11:00:00;
        sym:3#`EURUSD; side:1 1 -1; size:3#1000000f; trade_price:1.0 2.0 4.0)}

/ The same book, flattened: a second sell takes out the remaining lot, so
/ every method has to end up with the same total realised P&L.
mk_flat:{[] mk_two_lots[],([] time:enlist 2026.01.01D12:00:00;
    sym:enlist `EURUSD; side:enlist -1; size:enlist 1000000f; trade_price:enlist 8.0)}

/ ------------------------------------------------------------ THE ORACLE

test_weighted_reproduces_the_position_book:{[t]
    / .qpos is the existing authority on weighted-average-cost realised
    / P&L, so the weighted method has to agree with it exactly. If it does
    / not, the engine is wrong - not .qpos.
    trades:mk_flat[];
    book:.qpos.apply_fills[.qpos.empty_book[];trades];
    ledger:.qalloc.allocate[trades;`weighted];
    .testutil.assertApprox[sum exec pnl from ledger; (book`EURUSD)`realized_pnl; 1e-9;
        "weighted total realised P&L equals .qpos.apply_fills' realized_pnl"]};

test_weighted_residual_matches_the_position_book:{[t]
    trades:mk_two_lots[];
    book:.qpos.apply_fills[.qpos.empty_book[];trades];
    left:.qalloc.residual[trades;`weighted];
    .testutil.assertApprox[first exec side*qty from left; (book`EURUSD)`qty; 1e-9;
        "weighted leaves the same signed quantity open as .qpos carries"];
    .testutil.assertApprox[first exec price from left; (book`EURUSD)`avg_price; 1e-9;
        "and at the same weighted-average price"]};

/ ---------------------------------------------------------- THE METHODS

test_fifo_closes_the_oldest_lot:{[t]
    r:.qalloc.allocate[mk_two_lots[];`fifo];
    .qunit.assertEquals[exec open_id from r; enlist 0; "fifo matched the first buy"];
    .qunit.assertEquals[exec pnl from r; enlist 3000000f;
        "1mm bought at 1.0 and sold at 4.0 realises 3mm"]};

test_lifo_closes_the_newest_lot:{[t]
    r:.qalloc.allocate[mk_two_lots[];`lifo];
    .qunit.assertEquals[exec open_id from r; enlist 1; "lifo matched the second buy"];
    .qunit.assertEquals[exec pnl from r; enlist 2000000f;
        "1mm bought at 2.0 and sold at 4.0 realises 2mm"]};

test_weighted_closes_against_the_running_average:{[t]
    r:.qalloc.allocate[mk_two_lots[];`weighted];
    .qunit.assertEquals[exec open_price from r; enlist 1.5;
        "the two buys merged into one lot at their size-weighted average"];
    .qunit.assertEquals[exec pnl from r; enlist 2500000f;
        "and the sell realises against that average, between fifo's and lifo's answers"]};

test_hifo_closes_the_dearest_lot_and_lofo_the_cheapest:{[t]
    trades:mk_two_lots[];
    .qunit.assertEquals[exec open_price from .qalloc.allocate[trades;`hifo]; enlist 2.0;
        "hifo reached past the oldest lot for the dearer one"];
    .qunit.assertEquals[exec open_price from .qalloc.allocate[trades;`lofo]; enlist 1.0;
        "lofo took the cheaper one"]};

test_every_method_realises_the_same_total_once_flat:{[t]
    / The methods disagree about WHICH trade earned the money, never about
    / how much there was. A method that fails this is inventing P&L.
    trades:mk_flat[];
    totals:{[trades;m] sum exec pnl from .qalloc.allocate[trades;m]}[trades;] each `fifo`lifo`hifo`lofo`weighted;
    .qunit.assertEquals[count distinct totals; 1;
        "fifo, lifo, hifo, lofo and weighted all realise the same total on a flat book"];
    .testutil.assertApprox[first totals; 9000000f; 1e-9;
        "2mm bought for 3mm and sold for 12mm realises 9mm, however it is sliced"]};

test_no_method_leaves_anything_open_once_flat:{[t]
    trades:mk_flat[];
    left:{[trades;m] count .qalloc.residual[trades;m]}[trades;] each `fifo`lifo`hifo`lofo`weighted;
    .qunit.assertEquals[distinct left; enlist 0; "a flat book has no residual under any method"]};

/ ------------------------------------------------------------- MATCHING

test_a_partial_close_consumes_part_of_a_lot:{[t]
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:2#`EURUSD;
        side:1 -1; size:1000000 400000f; trade_price:1.0 2.0);
    r:.qalloc.run[trades;`fifo];
    .qunit.assertEquals[exec qty from r`matches; enlist 400000f; "only the closed slice is matched"];
    .qunit.assertEquals[exec qty from r`residual; enlist 600000f; "the rest of the lot stays open"];
    .qunit.assertEquals[exec price from r`residual; enlist 1.0;
        "at its original price - a partial close does not re-price what is left"]};

test_one_close_can_consume_several_lots:{[t]
    trades:mk_two_lots[],([] time:enlist 2026.01.01D12:00:00; sym:enlist `EURUSD;
        side:enlist -1; size:enlist 500000f; trade_price:enlist 8.0);
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[count r; 2; "the two sells produced two match rows"];
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00 2026.01.01D11:00:00;
        sym:3#`EURUSD; side:1 1 -1; size:1000000 1000000 2000000f; trade_price:1.0 2.0 4.0);
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[count r; 2; "one sell big enough for both lots produced one row per lot"];
    .qunit.assertEquals[exec open_id from r; 0 1; "in fifo order"];
    .qunit.assertEquals[sum exec pnl from r; 5000000f; "3mm from the first lot plus 2mm from the second"]};

test_a_flip_closes_everything_and_opens_the_excess:{[t]
    / The .qpos convention: a reversing trade closes the old side and opens
    / the new one at the one execution price it actually happened at.
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:2#`EURUSD;
        side:1 -1; size:1000000 3000000f; trade_price:1.0 2.0);
    r:.qalloc.run[trades;`fifo];
    .qunit.assertEquals[exec qty from r`matches; enlist 1000000f; "the whole long was closed"];
    left:r`residual;
    .qunit.assertEquals[exec side from left; enlist -1; "and the book flipped short"];
    .qunit.assertEquals[exec qty from left; enlist 2000000f; "by the excess"];
    .qunit.assertEquals[exec price from left; enlist 2.0; "at the flipping trade's own price"]};

test_a_short_book_realises_on_the_way_down:{[t]
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:2#`EURUSD;
        side:-1 1; size:2#1000000f; trade_price:4.0 1.0);
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[exec open_side from r; enlist -1; "the opening trade was the sell"];
    .qunit.assertEquals[exec pnl from r; enlist 3000000f;
        "sold at 4.0 and bought back at 1.0 - a profit, not a loss"]};

test_trades_are_matched_in_time_order_not_row_order:{[t]
    / Deliberately out of order, the way a booking system hands them over.
    trades:([] time:2026.01.01D11:00:00 2026.01.01D09:00:00 2026.01.01D10:00:00;
        sym:3#`EURUSD; side:-1 1 1; size:3#1000000f; trade_price:4.0 1.0 2.0);
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[exec open_price from r; enlist 1.0;
        "fifo matched the earliest buy by time, which is row 1 of the input"];
    .qunit.assertEquals[exec open_id from r; enlist 1;
        "and trade_id numbers the rows AS SUPPLIED, so the id still points at that row"]};

test_a_supplied_trade_id_is_kept:{[t]
    trades:update trade_id:`a`b`c from mk_two_lots[];
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[exec open_id from r; enlist `a; "the caller's own ids came through"];
    .qunit.assertEquals[exec close_id from r; enlist `c; "on both sides of the match"]};

/ -------------------------------------------------------------- BUCKETS

test_symbols_are_matched_separately:{[t]
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00 2026.01.01D11:00:00;
        sym:`EURUSD`GBPUSD`EURUSD; side:1 1 -1; size:3#1000000f; trade_price:1.0 2.0 4.0);
    r:.qalloc.allocate[trades;`fifo];
    .qunit.assertEquals[count r; 1; "the GBPUSD buy cannot be closed by a EURUSD sell"];
    .qunit.assertEquals[exec sym from r; enlist `EURUSD; "the one match is the EURUSD pair"]};

test_by_splits_one_symbol_into_independent_queues:{[t]
    / Two strategies trading the same pair. Matching across them would
    / credit one strategy's sell against the other's buy, which is the
    / single most expensive mistake an attribution run can make.
    trades:([] time:2026.01.01D09:00:00 2026.01.01D10:00:00 2026.01.01D11:00:00;
        sym:3#`EURUSD; strategy:`carry`momentum`carry; side:1 1 -1;
        size:3#1000000f; trade_price:1.0 2.0 4.0);
    pooled:.qalloc.allocate[trades;`fifo];
    split:.qalloc.allocate[trades;`method`by!(`fifo;`sym`strategy)];
    .qunit.assertEquals[exec open_price from pooled; enlist 1.0;
        "pooled, the carry sell closed whichever lot was oldest"];
    .qunit.assertEquals[exec strategy from split; enlist `carry;
        "split, the carry sell can only close carry's own buy"];
    .qunit.assertEquals[exec open_id from split; enlist 0; "which is the trade it opened"];
    .qunit.assertEquals[count .qalloc.residual[trades;`method`by!(`fifo;`sym`strategy)]; 1;
        "leaving momentum's buy open, matched against nothing"]};

/ -------------------------------------------------------------- FILTERS

test_universe_filters_before_matching_and_changes_the_answer:{[t]
    / Dropping the first buy from the universe means the sell has a
    / different lot to hit. This is the filter that rewrites history.
    trades:mk_two_lots[];
    r:.qalloc.allocate[trades;`method`universe!(`fifo;.qalloc.by_cols[enlist[`trade_price]!enlist 2.0 4.0])];
    .qunit.assertEquals[exec open_price from r; enlist 2.0;
        "with the 1.0 buy excluded, fifo's oldest lot is the 2.0 one"];
    .qunit.assertEquals[exec pnl from r; enlist 2000000f; "so the attributed P&L changes too"]};

test_where_filters_after_matching_and_does_not:{[t]
    / The same slice, reached the other way round: match everything, then
    / look at part of it. The numbers are the ones the full book produced.
    trades:mk_two_lots[],([] time:enlist 2026.01.01D12:00:00; sym:enlist `GBPUSD;
        side:enlist 1; size:enlist 1000000f; trade_price:enlist 1.0);
    full:.qalloc.allocate[trades;`fifo];
    sliced:.qalloc.allocate[trades;`method`where!(`fifo;.qalloc.by_cols[enlist[`sym]!enlist `EURUSD])];
    .qunit.assertEquals[sliced; select from full where sym=`EURUSD;
        "where is a view of the ledger, not a rerun of the matching"]};

test_by_cols_accepts_atoms_and_lists:{[t]
    tbl:([] a:1 2 3; b:`x`y`z);
    .qunit.assertEquals[count .qalloc.by_cols[enlist[`a]!enlist 2] tbl; 1; "an atom matches one row"];
    .qunit.assertEquals[count .qalloc.by_cols[enlist[`a]!enlist 1 3] tbl; 2; "a list matches several"];
    .qunit.assertEquals[count .qalloc.by_cols[`a`b!(1 2;enlist `y)] tbl; 1;
        "two entries are combined with and, not or"];
    .qunit.assertEquals[.qalloc.by_cols[()!()] tbl; tbl; "an empty filter keeps everything"]};

/ --------------------------------------------------- CARRIED POSITIONS

/ Yesterday: two buys, nothing closed. Today: one sell that reaches back
/ into them. The pair is the whole reason opening exists.
mk_yesterday:{[]
    ([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:2#`EURUSD;
        side:1 1; size:2#1000000f; trade_price:1.0 2.0)}

mk_today:{[]
    ([] time:enlist 2026.01.02D09:00:00; sym:enlist `EURUSD;
        side:enlist -1; size:enlist 1500000f; trade_price:enlist 4.0)}

test_without_an_opening_position_a_close_wrongly_opens_a_short:{[t]
    / Not a bug being asserted - the baseline the opening option exists to
    / correct. A day's trades read on their own genuinely do look like this.
    r:.qalloc.run[mk_today[];`fifo];
    .qunit.assertEmpty[r`matches; "a sell with no lots to hit realises nothing"];
    .qunit.assertEquals[exec side from r`residual; enlist -1;
        "and is read as opening a short, which is what carrying the position in fixes"]};

test_an_opening_position_is_closed_by_todays_trades:{[t]
    carried:.qalloc.residual[mk_yesterday[];`fifo];
    r:.qalloc.run[mk_today[];`method`opening!(`fifo;carried)];
    m:r`matches;
    .qunit.assertEquals[count m; 2; "the sell reached across both carried lots"];
    .qunit.assertEquals[exec open_price from m; 1.0 2.0; "oldest first, as fifo promises"];
    .qunit.assertEquals[exec qty from m; 1000000 500000f; "taking all of one and half the other"];
    .qunit.assertEquals[sum exec pnl from m; 4000000f;
        "3mm on the lot bought at 1.0, 1mm on the half-lot bought at 2.0"];
    .qunit.assertEquals[exec qty from r`residual; enlist 500000f; "leaving half a lot open"]};

test_opening_lots_sit_at_the_front_of_the_queue:{[t]
    / A carried lot is older than anything that traded today, so fifo must
    / reach it first and lifo must reach it last.
    carried:.qalloc.residual[mk_yesterday[];`fifo];
    today:([] time:2026.01.02D09:00:00 2026.01.02D10:00:00; sym:2#`EURUSD;
        side:1 -1; size:2#1000000f; trade_price:3.0 4.0);
    opts:`method`opening!(`fifo;carried);
    .qunit.assertEquals[first exec open_price from .qalloc.allocate[today;opts];1.0;
        "fifo took yesterday's oldest lot, not today's buy"];
    .qunit.assertEquals[first exec open_price from .qalloc.allocate[today;@[opts;`method;:;`lifo]];3.0;
        "lifo took today's buy, the newest thing in the queue"]};

test_a_carried_position_with_no_trades_still_appears:{[t]
    / A bucket that exists only in the opening position is still a
    / position. Losing it would understate the book by whatever was not
    / traded that day, which is usually most of it.
    carried:.qalloc.residual[mk_yesterday[];`fifo];
    carried:carried,update sym:`GBPUSD from carried;
    left:.qalloc.residual[mk_today[];`method`opening!(`fifo;carried)];
    .qunit.assertEquals[exec sym from left; `EURUSD`GBPUSD`GBPUSD;
        "GBPUSD traded nothing today and is carried through untouched"];
    .qunit.assertEquals[sum exec qty from left where sym=`GBPUSD; 2000000f;
        "at its full size"]};

test_a_day_split_in_two_attributes_the_same_as_one_run:{[t]
    / The composition property that makes carrying forward worth doing:
    / matching a book in two halves, feeding the first half's residual in
    / as the second half's opening, has to give the same ledger as
    / matching it in one go.
    trades:mk_yesterday[],mk_today[];
    whole:.qalloc.allocate[trades;`fifo];
    first_half:.qalloc.run[mk_yesterday[];`fifo];
    second_half:.qalloc.allocate[mk_today[];`method`opening!(`fifo;first_half`residual)];
    .qunit.assertEquals[sum exec pnl from (first_half`matches),second_half; sum exec pnl from whole;
        "split in two, the realised total is the same"];
    .qunit.assertEquals[exec open_price from second_half; exec open_price from whole;
        "and the same lots were closed at the same prices"]};

test_a_malformed_opening_position_is_refused:{[t]
    .qunit.assertThrows[.qalloc.allocate[mk_today[];]; `method`opening!(`fifo;([] sym:enlist `EURUSD));
        "*opening is missing required column(s)*"; "a lot needs a qty, a price and a side"];
    .qunit.assertThrows[.qalloc.allocate[mk_today[];]; `method`opening!(`fifo;`notatable);
        "*opening must be a table*"; "and it has to be a table of them"];
    .qunit.assertThrows[.qalloc.allocate[mk_today[];];
        `method`opening!(`fifo;([] sym:enlist `EURUSD; qty:enlist 1f; price:enlist 1f; side:enlist 0));
        "*must be 1 (long) or -1 (short)*"; "a lot with no side is not a position"]};

/ ------------------------------------------------------------- AS OF

test_asof_stops_the_clock:{[t]
    trades:mk_two_lots[];
    .qunit.assertEmpty[.qalloc.allocate[trades;`method`asof!(`fifo;2026.01.01D10:30:00)];
        "nothing had been realised by 10:30 - the sell had not happened yet"];
    .qunit.assertEquals[count .qalloc.residual[trades;`method`asof!(`fifo;2026.01.01D10:30:00)]; 2;
        "but both buys were open"];
    .qunit.assertEquals[count .qalloc.allocate[trades;`method`asof!(`fifo;2026.01.01D11:00:00)]; 1;
        "asof is inclusive, so the 11:00 sell counts at 11:00"]};

test_asof_composes_with_universe:{[t]
    / universe picks the trades that count, asof stops the clock - in that
    / order, so a trade excluded by the universe is not resurrected by a
    / later asof.
    trades:mk_two_lots[];
    r:.qalloc.residual[trades;`method`universe`asof!(`fifo;
        .qalloc.by_cols[enlist[`trade_price]!enlist 2.0 4.0]; 2026.01.01D10:30:00)];
    .qunit.assertEquals[exec price from r; enlist 2.0;
        "the 1.0 buy was filtered out, and only the 2.0 buy was open at 10:30"]};

test_position_at_tracks_the_book_through_the_day:{[t]
    trades:mk_two_lots[];
    at:{[trades;ts] first exec qty from .qalloc.position_at[trades;`fifo;ts]}[trades;];
    .qunit.assertEquals[at 2026.01.01D09:30:00; 1000000f; "long 1mm after the first buy"];
    .qunit.assertEquals[at 2026.01.01D10:30:00; 2000000f; "long 2mm after the second"];
    .qunit.assertEquals[at 2026.01.01D11:30:00; 1000000f; "back to 1mm after the sell"]};

test_position_agrees_with_the_position_book:{[t]
    / The derived book and the tracked book are the same object computed
    / two ways. Under weighted they must agree exactly, and if they ever
    / stop, .qpos is right and this is wrong.
    trades:mk_flat[],mk_two_lots[];
    derived:.qalloc.position[.qalloc.residual[trades;`weighted];`sym];
    tracked:.qpos.apply_fills[.qpos.empty_book[];trades];
    .testutil.assertApprox[first exec qty from derived; (tracked`EURUSD)`qty; 1e-9;
        "the open lots add up to the position book's qty"];
    .testutil.assertApprox[first exec avg_price from derived; (tracked`EURUSD)`avg_price; 1e-9;
        "at the position book's average price"]};

test_position_reports_a_short_as_a_negative_quantity:{[t]
    trades:([] time:enlist 2026.01.01D09:00:00; sym:enlist `EURUSD;
        side:enlist -1; size:enlist 1000000f; trade_price:enlist 1.0);
    .qunit.assertEquals[first exec qty from .qalloc.position[.qalloc.residual[trades;`fifo];`sym]; -1000000f;
        "a short book is a negative position, as .qpos spells it too"]};

/ -------------------------------------------------------------- ROLLUPS

test_by_trade_attributes_the_same_money_twice:{[t]
    trades:mk_flat[];
    ledger:.qalloc.allocate[trades;`fifo];
    r:.qalloc.by_trade ledger;
    total:sum exec pnl from ledger;
    .testutil.assertApprox[sum exec closing_pnl from r; total; 1e-9;
        "closing_pnl sums to the book's realised P&L"];
    .testutil.assertApprox[sum exec opening_pnl from r; total; 1e-9;
        "and so does opening_pnl - they are two views of the same money"];
    .qunit.assertEquals[count r; 4; "every trade appears, as an opener or a closer or both"]};

test_by_trade_zero_fills_a_trade_that_only_opened:{[t]
    r:.qalloc.by_trade .qalloc.allocate[mk_two_lots[];`fifo];
    .qunit.assertEquals[first exec closing_pnl from r where trade_id=0; 0f;
        "the opening buy closed nothing, which is a zero rather than a null"];
    .qunit.assertEquals[first exec opening_pnl from r where trade_id=0; 3000000f;
        "but it is credited with what its lot eventually earned"]};

test_summary_groups_the_ledger:{[t]
    trades:mk_two_lots[],([] time:enlist 2026.01.01D12:00:00; sym:enlist `EURUSD;
        side:enlist -1; size:enlist 1000000f; trade_price:enlist 8.0);
    r:.qalloc.summary[.qalloc.allocate[trades;`fifo];`sym];
    .qunit.assertEquals[count r; 1; "one symbol, one row"];
    .qunit.assertEquals[first exec matches from r; 2; "counting the matches, not the trades"];
    .qunit.assertEquals[first exec pnl from r; 9000000f; "and summing their P&L"]};

test_unrealised_marks_what_is_still_open:{[t]
    left:.qalloc.residual[mk_two_lots[];`fifo];
    r:.qalloc.unrealised[left; enlist[`EURUSD]!enlist 5.0];
    .qunit.assertEquals[exec unrealised_pnl from r; enlist 3000000f;
        "the open 1mm lot at 2.0, marked at 5.0"]};

/ ------------------------------------------------------- THE REGISTRY

test_a_desk_can_register_its_own_method:{[t]
    / The point of the open/pick factoring: a new method is two existing
    / pieces and a name, with no change to the engine.
    .qalloc.register[`alloctest_second; `open`pick`why!(.qalloc.append_lot; {[lots] 1}; "the second lot")];
    trades:mk_two_lots[];
    .qunit.assertEquals[exec open_id from .qalloc.allocate[trades;`alloctest_second]; enlist 1;
        "the custom pick chose the lot it said it would"];
    .qunit.assertTrue[`alloctest_second in .qalloc.registered[];
        "and the method shows up in the registry"]};

test_why_is_optional_and_always_stored:{[t]
    .qalloc.register[`alloctest_nowhy; `open`pick!(.qalloc.append_lot; .qalloc.pick_first)];
    .qunit.assertEquals[.qalloc.method[`alloctest_nowhy]`why; "";
        "a method registered without a description still has the key, so every stored method is the same shape"]};

test_a_malformed_method_is_refused_at_registration:{[t]
    .qunit.assertThrows[.qalloc.require_method; enlist[`open]!enlist .qalloc.append_lot;
        "*is missing*"; "a method without pick is refused"];
    .qunit.assertThrows[.qalloc.require_method; `open`pick`extra!(.qalloc.append_lot;.qalloc.pick_first;1);
        "*unknown matching method key*"; "an unrecognised key is a typo, not an extension point"];
    .qunit.assertThrows[.qalloc.require_method; `open`pick!(.qalloc.append_lot;42);
        "*must both be functions*"; "pick has to be a function"];
    .qunit.assertThrows[.qalloc.require_method; 42; "*must be a dictionary*"; "and the method has to be a dict"]};

test_an_unregistered_method_names_what_is_available:{[t]
    .qunit.assertThrows[.qalloc.method; `nonesuch; "*not a registered matching method*";
        "looking up a method that does not exist says so"]};

/ ------------------------------------------------------------- REFUSALS

test_a_missing_column_is_named:{[t]
    .qunit.assertThrows[.qalloc.allocate[;`fifo]; ([] time:enlist 2026.01.01D09:00:00; sym:enlist `EURUSD);
        "*trades is missing required column(s)*"; "the trades table has to carry the columns the engine reads"]};

test_an_unknown_option_is_refused:{[t]
    .qunit.assertThrows[.qalloc.allocate[mk_two_lots[];]; `method`filter!(`fifo;.qalloc.all_rows);
        "*unknown option*"; "a mistyped option that silently did nothing would be worse than an error"]};

test_a_non_function_filter_is_refused:{[t]
    .qunit.assertThrows[.qalloc.allocate[mk_two_lots[];]; `method`where!(`fifo;`EURUSD);
        "*must each be a function*"; "a filter is a function, even when by_cols wrote it"]};

test_opts_must_be_a_dict_or_a_method_name:{[t]
    .qunit.assertThrows[.qalloc.allocate[mk_two_lots[];]; "fifo";
        "*dictionary of options*"; "a string is neither"]};

test_trades_must_be_a_table:{[t]
    .qunit.assertThrows[.qalloc.allocate[;`fifo]; enlist[`sym]!enlist `EURUSD;
        "*must be a table*"; "one trade is a one-row table, not a dict"]};

/ ----------------------------------------------------------- EMPTY BOOK

test_no_trades_gives_an_empty_ledger_of_the_right_shape:{[t]
    trades:0#mk_two_lots[];
    r:.qalloc.run[trades;`fifo];
    .qunit.assertEmpty[r`matches; "no trades, no matches"];
    .qunit.assertEmpty[r`residual; "and nothing open"];
    .qunit.assertTrue[all `sym`open_id`close_id`qty`pnl in cols r`matches;
        "but the ledger still has its columns, so a downstream select does not fail on an empty day"]};

test_a_universe_that_excludes_everything_is_the_empty_book:{[t]
    r:.qalloc.allocate[mk_two_lots[];`method`universe!(`fifo;.qalloc.by_cols[enlist[`sym]!enlist `NOPE])];
    .qunit.assertEmpty[r; "filtering the universe down to nothing matches nothing"]};

\d .
