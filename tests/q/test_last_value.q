/ test_last_value.q - the last_value streaming job (.last_valuetest).
/ .
/ The job keeps the newest top of book per sym and publishes what changed:
/ latest wins, an older book never overwrites a newer one, syms are
/ independent, and a replay rebuilds the state without publishing it again.

\d .last_valuetest

d:{[n] 2026.09.19D10:00:00.000000000+n*0D00:00:01}

/ One book per argument row: (sym; source; source_time; bid; ask). An empty
/ bid or ask is a withdrawn side.
book:{[rows]
    ladder:{[p] $[null p; `float$(); enlist p]};
    sizes:{[p] $[null p; `float$(); enlist 1e6]};
    ([] time:d[0]; sym:rows[;0]; source:rows[;1]; market:count[rows]#`fx; source_time:rows[;2];
        bid_prices:ladder each rows[;3]; bid_sizes:sizes each rows[;3];
        ask_prices:ladder each rows[;4]; ask_sizes:sizes each rows[;4])}

reset:{[]
    .qetl.job.stream.reset `last_value;
    .sjtest.reset[]}

/ crypto books: the same rows, on market `crypto
cbook:{[rows] update market:`crypto from book rows}

/ The latest row per sym, as the table a reader would see after `select by sym`.
latest:{[] 0!.qpipe.job.last_value.state}

test_the_top_of_a_book_is_its_level_0_bid_ask_and_mid:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[0];1.10;1.12)];
    r:first latest[];
    .qunit.assertEquals[r`sym`source`source_time`bid`ask;(`EURUSD;`LP_A;d[0];1.10;1.12);"the book's own identity and level 0"];
    .testutil.assertApprox[r`mid;1.11;1e-12;"halfway between bid and ask"]};

test_the_newest_book_wins_and_is_published:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[0];1.10;1.12)];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_B;d[1];1.20;1.22)];
    .qunit.assertEquals[(first latest[])`source`bid;(`LP_B;1.20);"the later source_time replaced the earlier, from whichever source"];
    .qunit.assertEquals[count latest[];1;"one row per sym, not a history"];
    .qunit.assertEquals[count .sjtest.published;2;"each change was published"]};

test_an_older_book_does_not_overwrite_a_newer_one:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[5];1.20;1.22)];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_B;d[2];1.00;1.02)];
    .qunit.assertEquals[(first latest[])`source`source_time`bid;(`LP_A;d[5];1.20);"the delayed book is dropped"];
    .qunit.assertEquals[count .sjtest.published;1;"and nothing was published for it"]};

test_within_one_batch_the_newest_source_time_wins_not_the_last_row:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book ((`EURUSD;`LP_A;d[3];1.30;1.32);(`EURUSD;`LP_B;d[1];1.00;1.02))];
    .qunit.assertEquals[(first latest[])`source;`LP_A;"a batch in the wrong order is still resolved by time"];
    .qunit.assertEquals[count first exec rows from .sjtest.published;1;"and publishes one row for the sym"]};

test_an_equal_source_time_follows_arrival_order:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book ((`EURUSD;`LP_A;d[1];1.10;1.12);(`EURUSD;`LP_B;d[1];1.20;1.22))];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[1];1.30;1.32)];
    .qunit.assertEquals[(first latest[])`source`bid;(`LP_A;1.30);"the last to arrive of equal times"]};

test_syms_are_independent:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book ((`EURUSD;`LP_A;d[5];1.10;1.12);(`GBPUSD;`LP_A;d[1];1.30;1.32))];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`GBPUSD;`LP_A;d[2];1.40;1.42)];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[3];0.90;0.92)];
    r:`sym xasc latest[];
    .qunit.assertEquals[r`sym;`EURUSD`GBPUSD;"both held"];
    .qunit.assertEquals[r`bid;1.10 1.40;"GBPUSD advanced; EURUSD's older book was dropped, regardless of GBPUSD's time"]};

test_a_withdrawn_side_keeps_the_last_price:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[0];1.10;1.12)];
    .qpipe.job.last_value.on_batch[`market_data;book ((`EURUSD;`LP_A;d[1];0n;1.12);(`EURUSD;`LP_B;d[2];1.15;0n))];
    .qunit.assertEquals[(first latest[])`source_time;d[0];"an empty ladder is a withdrawal, not a price of null"];
    .qunit.assertEquals[count .sjtest.published;1;"and is not published"]};

test_apply_is_the_identity_on_an_empty_batch_and_does_not_mutate_state:{[t]
    reset[];
    s:.qpipe.job.last_value.state;
    r:.qpipe.job.last_value.apply[s;.qpipe.job.last_value.tob;0#.qpipe.job.last_value.market_data];
    .qunit.assertEquals[r`state;s;"state unchanged"];
    .qunit.assertEquals[count r`changed;0;"nothing changed"];
    r:.qpipe.job.last_value.apply[s;.qpipe.job.last_value.tob;book enlist (`EURUSD;`LP_A;d[0];1.10;1.12)];
    .qunit.assertEquals[(count s;count r`state);(0;1);"apply returns the new state and leaves the old one"]};

test_a_replay_rebuilds_the_state_without_publishing_it_again:{[t]
    reset[];
    replayed:((`market_data;book enlist (`EURUSD;`LP_A;d[1];1.10;1.12));
        (`market_data;book enlist (`EURUSD;`LP_B;d[3];1.20;1.22));
        (`market_data;book enlist (`GBPUSD;`LP_A;d[2];1.30;1.32));
        (`market_data;book enlist (`EURUSD;`LP_A;d[2];1.00;1.02)));
    .qetl.job.stream.start[`last_value;.sjtest.fake_transport[`last_value;replayed]];
    .qunit.assertEquals[count .sjtest.published;0;"what the replay recomputed was published before the restart"];
    r:`sym xasc latest[];
    .qunit.assertEquals[(r`sym;r`source;r`bid);(`EURUSD`GBPUSD;`LP_B`LP_A;1.20 1.30);"the same state the live run held"];
    .sjtest.live[`market_data;book enlist (`EURUSD;`LP_A;d[1];0.5;0.6)];
    .qunit.assertEquals[count .sjtest.published;0;"a stale live book after the rebuild is still older than the rebuilt one"];
    .sjtest.live[`market_data;book enlist (`EURUSD;`LP_A;d[4];1.40;1.42)];
    .qunit.assertEquals[count .sjtest.published;1;"a newer live book publishes again"]};

/ The one rule for "the top of book" (#998): posbook, crypto_markout and
/ last_value read a book's sides through .qbook.top_sides. This drives one
/ market_data sequence, with a withdrawn side and a non-positive level,
/ through all three and holds them to it.
agreement_rows:{[]
    ([] time:7#d[0]; sym:7#`EURUSD; source:`a`b`c`d`e`f`g;
        market:7#`fx; source_time:7#d[0];
        bid_prices:(enlist 1.10;`float$();enlist 1.10;enlist 0f;enlist 1.10;enlist 0w;enlist 1.10);
        bid_sizes:7#enlist enlist 1e6;
        ask_prices:(enlist 1.12;enlist 1.12;`float$();enlist 1.12;enlist 1.12;enlist 1.12;enlist 0w);
        ask_sizes:7#enlist enlist 1e6)}

test_all_consumers_agree_on_a_withdrawn_or_non_positive_side:{[t]
    x:agreement_rows[];
    ref:.qbook.top_sides x;
    .qunit.assertEquals[ref`bid;1.10 0n 1.10 0n 1.10 0n 1.10;"an empty ladder, a zero level and an infinite one are all a null side (#1020)"];
    .qunit.assertEquals[ref`ask;1.12 1.12 0n 1.12 1.12 1.12 0n;"on either side"];
    pb:.qpipe.job.posbook.crypto_books update market:`crypto from x;
    .qunit.assertEquals[(pb`bid;pb`ask);(ref`bid;ref`ask);"posbook's crypto books"];
    mk:.qpipe.job.crypto_markout.top_of_book update venue:source from x;
    .qunit.assertEquals[(mk`bid;mk`ask);(ref`bid;ref`ask);"crypto_markout's top of book"];
    lv:.qpipe.job.last_value.tops x;
    both:where (not null ref`bid) & not null ref`ask;
    .qunit.assertEquals[lv`source;(x`source) both;"last_value keeps exactly the books with both sides"];
    .qunit.assertEquals[(.qpipe.job.posbook.book_mids x)`mid;lv`mid;"posbook's FX marks are last_value's mids"]};

test_no_consumer_re_derives_the_top_of_book:{[t]
    files:`$"src/etl/streaming/",/:("posbook";"crypto_markout";"last_value"),\:".q";
    src:{" " sv read0 hsym x} each files;
    .qunit.assertTrue[not any src like\: "*first each bid_prices*";"a consumer reads level 0 itself"];
    .qunit.assertTrue[not any src like\: "*first px*";"a consumer reads a side itself"]};

/ #990: a crypto sym publishes the cross-venue reference crypto_markout and
/ posbook price from - the best bid and best ask over live venues - not the
/ last venue's own book.
test_a_crypto_sym_publishes_the_best_bid_and_ask_across_venues:{[t]
    reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`binance;d[0];100.;102.)];
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`okx;d[1];101.;103.)];
    r:first latest[];
    .qunit.assertEquals[r`bid`ask`mid;101 102 101.5;"best bid okx, best ask binance"];
    .qunit.assertEquals[(r`source;r`source_time);(`venues;d[1]);"a reference, as of the newest venue"];
    want:.qmicro.best_mid_across_venues[select time:source_time, sym, venue:source, bid, ask from
        ([] sym:2#s; source:`binance`okx; source_time:d 0 1; bid:100 101.; ask:102 103.);
        ([] sym:enlist s; time:enlist d 1);.qmicro.reference_max_age];
    .qunit.assertEquals[r`mid;first want;"the shared reference, the one crypto_markout prices from"]};

test_a_stale_venue_drops_out_of_the_crypto_reference:{[t]
    reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`binance;d[0];100.;102.)];
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`okx;d[10];99.;103.)];
    .qunit.assertEquals[(first latest[])`bid`ask;99 103.;"binance's book is older than reference_max_age"]};

test_an_older_crypto_book_does_not_rewind_its_venue:{[t]
    reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`binance;d[2];100.;102.)];
    .qpipe.job.last_value.on_batch[`market_data;cbook enlist (s;`binance;d[1];90.;92.)];
    .qunit.assertEquals[(first latest[])`bid`ask;100 102.;"the venue's newer book stands"]};

test_fx_is_still_last_source_wins:{[t]
    reset[];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;d[0];1.10;1.14)];
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_B;d[1];1.11;1.15)];
    .qunit.assertEquals[(first latest[])`bid`ask`source;(1.11;1.15;`LP_B);"FX unchanged by #990"]};

/ What test_job_output_contracts.q drives last_value with, so the table it
/ publishes is held to its plant table by name, order and type.
contract_driver:{[]
    .qetl.job.stream.reset `last_value;
    .qpipe.job.last_value.on_batch[`market_data;book enlist (`EURUSD;`LP_A;.last_valuetest.d[0];1.10;1.12)]}

\d .
