/ test_fx_source_adapters.q - the two source adapters
/ (src/etl/transforms/fx_source_adapters.q, .qpipe.transform.fx_source_adapters,
/ #1395) and the claim they exist to make: two differently named and shaped
/ sources, normalized, are the same inputs to the markout calculation and give
/ the same markouts, with no change to the calculation.
/ .
/ The calculation and the stack's existing mappings (executions_from_trades,
/ market_data_from_quote) are used as they are.

\d .fxadaptertest

/ The jobs this suite defines are process-global; leave the registries as found.
beforeNamespace_save_jobs:{[] .testutil.save_stream_jobs[`.fxadaptertest]}
afterNamespace_restore_jobs:{[] .testutil.restore_stream_jobs[`.fxadaptertest]}

t:2026.09.17D10:00:00.000000000

tape:{[] .qpipe.transform.fx_source_adapters.deal_tape}
levels:{[] .qpipe.transform.fx_source_adapters.book_levels}

/ one deal-tape row
deal:{[id;oid;tm;code;ex;sd;px;qtys]
    ([] id:enlist id; order_id:enlist oid; deal_time:enlist tm; security_code:enlist code;
        venue_code:enlist `fx; exec_type:enlist ex; side:enlist sd; price:enlist px;
        last_qty:enlist qtys 0; cum_qty:enlist qtys 1)}

/ one book-level row
lvl:{[tm;inst;feed;cat;stl;sd;n;pq]
    ([] ts:enlist tm; instrument:enlist inst; feed:enlist feed; category:enlist cat; settle:enlist stl;
        side:enlist sd; lvl:enlist n; px:enlist pq 0; qty:enlist pq 1)}

from_tape:{[b] .qetl.transform.apply[`executions_from_deal_tape;enlist[`deal_tape]!enlist b]}
from_levels:{[b] .qetl.transform.apply[`market_data_from_book_levels;enlist[`book_levels]!enlist b]}

eurusd:`$"EUR/USD"

/ --- the declared transforms ------------------------------------------------

test_both_adapters_are_verified_transforms:{[tc]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `executions_from_deal_tape;"the deal tape's worked examples and its empty case"];
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `market_data_from_book_levels;"the book levels' worked examples and their empty case"]};

/ The stack's own mappings beside the adapters, in normalizers of this suite's own:
/ define refuses a mapping whose output drifts by a column or a type, so defining
/ them is the check that each adapter produces the canonical table.
ensure_normalizers:{[]
    if[not `fxa_exec in .qetl.job.stream.normalizer.defined[];
        .qetl.job.stream.normalize[`fxa_exec;`procname`output`input!(`fxaexec1;.qpipe.job.executions.executions;
            `deal_tape`trades!`executions_from_deal_tape`executions_from_trades)]];
    if[not `fxa_md in .qetl.job.stream.normalizer.defined[];
        .qetl.job.stream.normalize[`fxa_md;`procname`output`input!(`fxamd1;.qpipe.job.market_data.market_data;
            `book_levels`quote!`market_data_from_book_levels`market_data_from_quote)]];
    }

test_the_shell_holds_each_adapter_to_the_canonical_table:{[tc]
    ensure_normalizers[];
    .qunit.assertTrue[`fxa_exec in .qetl.job.stream.normalizer.defined[];"executions, beside the stack's own trades mapping"];
    .qunit.assertTrue[`fxa_md in .qetl.job.stream.normalizer.defined[];"market_data, beside the stack's own quote mapping"]};

/ --- eligibility -----------------------------------------------------------------

/ one order, 1,000,000 EUR in three events: two fills of 600k and 400k, and the
/ order-status rows that carry the running total
order_events:{[]
    raze (deal[1001;7;t+0D00:00:00;eurusd;`status;`B;1.1001;(0n;0f)];
          deal[1002;7;t+0D00:00:01;eurusd;`fill;`B;1.1001;(6e5;6e5)];
          deal[1003;7;t+0D00:00:01;eurusd;`status;`B;1.1001;(0n;6e5)];
          deal[1004;7;t+0D00:00:02;eurusd;`fill;`B;1.1002;(4e5;1e6)];
          deal[1005;7;t+0D00:00:02;eurusd;`status;`B;1.1002;(0n;1e6)])}

test_only_individual_fills_are_executions_not_cumulative_order_updates:{[tc]
    r:from_tape[order_events[]];
    .qunit.assertEquals[r`fill_id;`1002`1004;"the two fills, not the three status rows"];
    .qunit.assertEquals[r`size;6e5 4e5;"each at its own last_qty"]};

test_an_orders_fills_sum_to_its_final_cumulative_quantity:{[tc]
    e:order_events[];
    final:exec max cum_qty from e where order_id=7;
    .qunit.assertEquals[sum (from_tape[e])`size;final;"summing fills counts the order once; summing cum_qty rows would not"]};

test_cancels_and_other_instruments_are_dropped_not_refused:{[tc]
    e:raze (deal[1;1;t;eurusd;`cancel;`B;1.1;(1e6;0f)];deal[2;1;t;`AAPL;`fill;`B;190f;(100f;100f)];deal[3;1;t;eurusd;`fill;`S;1.1;(1e5;1e5)]);
    .qunit.assertEquals[(from_tape[e])`fill_id;enlist `3;"a cancel and an equity are not executions"]};

test_book_levels_of_another_category_or_settlement_are_not_reference_liquidity:{[tc]
    b:raze (lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.0999;1e6)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;0;(1.1001;1e6)];
            lvl[t;eurusd;`LPA;`PARTIAL;`SP;`bid;0;(1.0500;9e6)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`1W;`ask;0;(1.0900;9e6)];
            lvl[t;eurusd;`LPA;`FULL_AMOUNT;`T2;`ask;1;(1.1003;2e6)]);
    r:from_levels[b];
    .qunit.assertEquals[(count r;first r`bid_prices;first r`ask_prices);(1;enlist 1.0999;1.1001 1.1003);
        "the partial-amount bid and the forward ask are not levels; the T2 spot ask is"]};

test_a_snapshot_with_no_eligible_level_is_not_published:{[tc]
    b:lvl[t;eurusd;`LPA;`PARTIAL;`SP;`bid;0;(1.05;1e6)];
    .qunit.assertEquals[count from_levels[b];0;"nothing eligible, nothing published"]};

/ --- sides and instruments --------------------------------------------------------

test_sides_are_signed_the_way_the_stack_signs_them:{[tc]
    e:raze (deal[1;1;t;eurusd;`fill;`B;1.1;(1e5;1e5)];deal[2;1;t;eurusd;`fill;`S;1.1;(1e5;1e5)]);
    .qunit.assertEquals[(from_tape[e])`side;1 -1;"B is +1 (buy), S is -1 (sell)"]};

test_instrument_spellings_become_the_canonical_pair:{[tc]
    codes:(`$"EUR/USD";`$"eur-usd";`EURUSD;`$"usd_jpy";`$"JPY USD");
    e:raze {[c] deal[1;1;t;c;`fill;`B;1.1;(1e5;1e5)]} each codes;
    .qunit.assertEquals[(from_tape[e])`sym;`EURUSD`EURUSD`EURUSD`USDJPY`JPYUSD;"separators and case are the source's spelling; direction is kept as the source gave it"]};

/ --- times and identifiers ---------------------------------------------------------

test_the_source_time_is_the_sources_own_and_the_plant_stamp_is_not_read:{[tc]
    ensure_normalizers[];
    e:deal[1;1;t+0D00:00:07;eurusd;`fill;`B;1.1;(1e5;1e5)];
    later:t+1D;
    r:.qetl.job.stream.normalizer.normalize[`fxa_exec;`deal_tape;update time:later from e];
    .qunit.assertEquals[r`source_time;enlist t+0D00:00:07;"deal_time, even when the plant's receipt time says otherwise"]};

test_a_nineteen_digit_id_is_kept_exactly_and_neighbours_stay_distinct:{[tc]
    e:raze (deal[1234567890123456789;1;t;eurusd;`fill;`B;1.1;(1e5;1e5)];deal[1234567890123456790;1;t;eurusd;`fill;`B;1.1;(1e5;1e5)]);
    r:from_tape[e];
    .qunit.assertEquals[string r`fill_id;("1234567890123456789";"1234567890123456790");"every digit"];
    .qunit.assertEquals[1234567890123456789f;1234567890123456790f;"(as floats, these would be one id)"]};

/ --- reshaping the book -----------------------------------------------------------

test_levels_become_a_snapshot_best_first_whatever_order_they_arrive_in:{[tc]
    b:raze (lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;1;(1.0998;2e6)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;1;(1.1002;2e6)];
            lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.0999;1e6)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;0;(1.1001;1e6)]);
    r:first from_levels[b];
    .qunit.assertEquals[(r`bid_prices;r`bid_sizes;r`ask_prices;r`ask_sizes);(1.0999 1.0998;1e6 2e6;1.1001 1.1002;1e6 2e6);"bids falling, asks rising, sizes with their prices"];
    .qunit.assertEquals[(r`source;r`market;r`source_time);(`LPA;`fx;t);"the feed is the source; the time is the feed's own"]};

test_an_emptied_side_is_an_empty_ladder_not_a_price:{[tc]
    b:raze (lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(0n;0f)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;0;(1.1005;1e6)]);
    r:first from_levels[b];
    .qunit.assertEquals[(count r`bid_prices;r`ask_prices);(0;enlist 1.1005);"the marker row empties the bid side and is not a level"];
    .qunit.assertTrue[null first (.qbook.top_sides enlist r)`bid;"and top-of-book reads no bid"]};

test_a_side_the_snapshot_does_not_list_is_empty_too:{[tc]
    r:first from_levels[lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;0;(1.1005;1e6)]];
    .qunit.assertEquals[count r`bid_prices;0;"an absent side is a withdrawn side"]};

test_two_feeds_and_two_instants_are_separate_snapshots:{[tc]
    b:raze (lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.0999;1e6)];lvl[t;eurusd;`LPB;`FULL_AMOUNT;`SP;`bid;0;(1.0998;1e6)];
            lvl[t+0D00:00:01;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.1000;1e6)]);
    r:from_levels[b];
    .qunit.assertEquals[(r`source;r`source_time);(`LPA`LPA`LPB;t,(t+0D00:00:01),t);"one snapshot per (pair, feed, time)"]};

/ --- empty and malformed inputs -----------------------------------------------------

test_empty_inputs_give_the_empty_canonical_table:{[tc]
    .qunit.assertEquals[from_tape[tape[]];0#.qpipe.job.executions.executions;"no deals"];
    .qunit.assertEquals[from_levels[levels[]];0#.qpipe.job.market_data.market_data;"no levels"]};

test_a_fill_that_cannot_be_represented_is_refused_by_name_and_count:{[tc]
    .qunit.assertThrows[from_tape;deal[1;1;t;eurusd;`fill;`X;1.1;(1e5;1e5)];
        "*1 fill/level row(s) with a side other than B or S - first: `X*";"an unknown side code"];
    .qunit.assertThrows[from_tape;deal[1;1;t;eurusd;`fill;`B;0f;(1e5;1e5)];
        "*with a price that is not positive and finite - first: 0f*";"a zero price"];
    .qunit.assertThrows[from_tape;deal[1;1;t;eurusd;`fill;`B;1.1;(0n;1e5)];
        "*with a last_qty that is not positive and finite*";"a fill with no quantity"];
    .qunit.assertThrows[from_tape;deal[0N;9;t;eurusd;`fill;`B;1.1;(1e5;1e5)];
        "*with no id - first: 9*";"a fill with no id, shown by its order"];
    .qunit.assertThrows[from_tape;raze (deal[1;1;t;eurusd;`fill;`X;1.1;(1e5;1e5)];deal[2;1;t;eurusd;`fill;`Y;1.1;(1e5;1e5)]);
        "*2 fill/level row(s) with a side*";"every offender is counted"]};

test_a_level_that_cannot_be_represented_is_refused:{[tc]
    .qunit.assertThrows[from_levels;lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`mid;0;(1.1;1e6)];
        "*with a side other than bid or ask*";"an unknown side"];
    .qunit.assertThrows[from_levels;lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(-1.1;1e6)];
        "*with a price that is not positive and finite*";"a negative price"];
    .qunit.assertThrows[from_levels;lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.1;-5f)];
        "*with a negative quantity*";"a negative size"]};

test_an_ineligible_row_is_not_judged_malformed:{[tc]
    / a forward with no price at all is not this contract's business, so it is not an error either
    b:raze (lvl[t;eurusd;`LPA;`FULL_AMOUNT;`1W;`bid;0;(0n;0n)];lvl[t;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(1.0999;1e6)]);
    .qunit.assertEquals[count from_levels[b];1;"dropped before it is checked"]};

\

/ --- two differently shaped sources, one set of inputs, one set of markouts ---------
/ .
/ The same market, written twice. Fixture A is a deal tape and a level-per-row book:
/ slash-spelled instruments, B/S codes, a 19-digit-style id, order-status rows beside
/ the fills, cancelled and equity rows, other categories and forwards in the book.
/ Fixture B is the stack's own demo tape - trades and quote - which normalize through
/ mappings that already exist. Neither knows the other.

eq_exec_a:{[]
    raze (deal[9001;1;t;eurusd;`status;`B;1.1001;(0n;0f)];
          deal[9001;1;t;eurusd;`fill;`B;1.1001;(1e6;1e6)];
          deal[9002;2;t+0D00:00:02;eurusd;`fill;`S;1.1002;(5e5;5e5)];
          deal[9003;2;t+0D00:00:02;eurusd;`cancel;`S;1.1002;(5e5;5e5)];
          deal[9004;3;t+0D00:00:02;`AAPL;`fill;`B;190f;(10f;10f)];
          deal[9002;2;t+0D00:00:02;eurusd;`status;`S;1.1002;(0n;5e5)])}

eq_exec_b:{[]
    ([] time:t,t+0D00:00:02; sym:`EURUSD`EURUSD; side:1 -1; trade_price:1.1001 1.1002; size:1e6 5e5;
        pip_factor:10000 10000; fill_id:`9001`9002)}

/ (instant seconds after t; source; bid; ask)
eq_books:{[]
    ((-1f;`LPA;1.0999;1.1001);(0.5;`LPA;1.1004;1.1006);(5f;`LPA;1.1009;1.1011);(0.5;`LPB;1.1003;1.1007);(8f;`LPB;1.1008;1.1010))}

eq_book_a:{[]
    one:{[b]
        tm:t+`long$1e9*b 0;
        raze (lvl[tm;eurusd;b 1;`FULL_AMOUNT;`SP;`bid;0;(b 2;1e6)];lvl[tm;eurusd;b 1;`FULL_AMOUNT;`SP;`ask;0;(b 3;1e6)];
              lvl[tm;eurusd;b 1;`PARTIAL;`SP;`bid;0;(b[2]-0.01;9e6)];lvl[tm;eurusd;b 1;`FULL_AMOUNT;`1M;`ask;0;(b[3]-0.01;9e6)])};
    (raze one each eq_books[]),lvl[t;`AAPL;`LPA;`FULL_AMOUNT;`SP;`bid;0;(190f;1f)]}

eq_quote_b:{[]
    b:eq_books[];
    ([] time:t+`long$1e9*b[;0]; sym:(count b)#`EURUSD; bid:b[;2]; ask:b[;3]; bsize:(count b)#1000000; asize:(count b)#1000000; src:b[;1])}

norm_a:{[] ensure_normalizers[]; (.qetl.job.stream.normalizer.normalize[`fxa_exec;`deal_tape;eq_exec_a[]];.qetl.job.stream.normalizer.normalize[`fxa_md;`book_levels;eq_book_a[]])}
norm_b:{[] ensure_normalizers[]; (.qetl.job.stream.normalizer.normalize[`fxa_exec;`trades;eq_exec_b[]];.qetl.job.stream.normalizer.normalize[`fxa_md;`quote;eq_quote_b[]])}

/ the scoring columns, in a canonical order
scored_cols:{[e] `source_time`venue`sym`side`size`price`fill_id#e}
by_key:{[m] `sym`source`source_time xasc m}

test_two_sources_normalize_to_equivalent_executions:{[tc]
    a:norm_a[];
    b:norm_b[];
    .qunit.assertEquals[count a 0;2;"two executions: the fills, not the status, cancelled or equity rows"];
    .qunit.assertEquals[`source_time xasc a 0;`source_time xasc b 0;"the same canonical executions, row for row and column for column"]};

test_two_sources_normalize_to_equivalent_market_data:{[tc]
    a:norm_a[];
    b:norm_b[];
    .qunit.assertEquals[count a 1;5;"five snapshots: the partial, forward and equity levels are not among them"];
    .qunit.assertEquals[by_key a 1;by_key b 1;"the same snapshots"]};

test_two_sources_give_the_same_markouts_with_the_one_calculation:{[tc]
    a:norm_a[];
    b:norm_b[];
    sp:.qpipe.transform.fx_markout.default_spec;
    ra:.qpipe.transform.fx_markout.calc[sp;a 0;a 1];
    rb:.qpipe.transform.fx_markout.calc[sp;b 0;b 1];
    .qunit.assertTrue[0<count ra;"scored"];
    .qunit.assertEquals[ra;rb;"identical results from the same function"];
    / and the numbers are the hand-worked ones: e1 bought at 1.1001, +1s best mid 1.1005 -> 4 pips
    .qunit.assertEquals[first exec pips from ra where fill_id=`9001, horizon=0D00:00:01, kind=`total;4f;"10000*(1.1005-1.1001)"]};

test_a_withdrawn_side_reads_the_same_through_both_shapes:{[tc]
    / LPA withdraws its bid at t+3s: fixture A as an emptied-side marker, fixture B as a quote with no bid
    ensure_normalizers[];
    tm:t+0D00:00:03;
    a:.qetl.job.stream.normalizer.normalize[`fxa_md;`book_levels;raze (lvl[tm;eurusd;`LPA;`FULL_AMOUNT;`SP;`bid;0;(0n;0f)];lvl[tm;eurusd;`LPA;`FULL_AMOUNT;`SP;`ask;0;(1.1012;1e6)])];
    b:.qetl.job.stream.normalizer.normalize[`fxa_md;`quote;([] time:enlist tm; sym:`EURUSD; bid:enlist 0n; ask:enlist 1.1012; bsize:enlist 0; asize:enlist 1000000; src:`LPA)];
    ta:.qpipe.transform.fx_markout.tops[a];
    tb:.qpipe.transform.fx_markout.tops[b];
    .qunit.assertEquals[ta;tb;"no bid, an ask of 1.1012, from both"];
    .qunit.assertTrue[null first ta`bid;"and the missing bid is null, not a price"]};

\d .
