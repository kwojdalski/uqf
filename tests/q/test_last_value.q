/ test_last_value.q - the last_value streaming job (.qt.qpipe.job.last_value).
/ .
/ The job keeps the newest top of book per sym and publishes what changed:
/ latest wins, an older book never overwrites a newer one, syms are
/ independent, and a replay rebuilds the state without publishing it again.


.qt.qpipe.job.last_value.d:{[n] 2026.09.19D10:00:00.000000000+n*0D00:00:01}

/ One book per argument row: (sym; source; source_time; bid; ask). An empty
/ bid or ask is a withdrawn side.
.qt.qpipe.job.last_value.book:{[rows]
    ladder:{[p] $[null p; `float$(); enlist p]};
    sizes:{[p] $[null p; `float$(); enlist 1e6]};
    ([] time:.qt.qpipe.job.last_value.d[0]; sym:rows[;0]; source:rows[;1]; market:count[rows]#`fx; source_time:rows[;2];
        bid_prices:ladder each rows[;3]; bid_sizes:sizes each rows[;3];
        ask_prices:ladder each rows[;4]; ask_sizes:sizes each rows[;4])}

.qt.qpipe.job.last_value.reset:{[]
    .qetl.job.stream.reset `last_value;
    .qt.qetl.job.stream.reset[]}

/ crypto books: the same rows, on market `crypto
.qt.qpipe.job.last_value.cbook:{[rows] update market:`crypto from .qt.qpipe.job.last_value.book rows}

/ The latest row per sym, as the table a reader would see after `select by sym`.
.qt.qpipe.job.last_value.latest:{[] 0!.qpipe.job.last_value.state}

.qt.qpipe.job.last_value.test_the_top_of_a_book_is_its_level_0_bid_ask_and_mid:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12)]];
    r:first .qt.qpipe.job.last_value.latest[];
    .qunit.assertEquals[r`sym`source`source_time`bid`ask;(`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12);"the book's own identity and level 0"];
    .qt.support.assertApprox[r`mid;1.11;1e-12;"halfway between bid and ask"]};

.qt.qpipe.job.last_value.test_the_newest_book_wins_and_is_published:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[1];1.20;1.22)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`source`bid;(`LP_B;1.20);"the later source_time replaced the earlier, from whichever source"];
    .qunit.assertEquals[count .qt.qpipe.job.last_value.latest[];1;"one row per sym, not a history"];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;2;"each change was published"]};

.qt.qpipe.job.last_value.test_an_older_book_does_not_overwrite_a_newer_one:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[5];1.20;1.22)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[2];1.00;1.02)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`source`source_time`bid;(`LP_A;.qt.qpipe.job.last_value.d[5];1.20);"the delayed book is dropped"];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;1;"and nothing was published for it"]};

.qt.qpipe.job.last_value.test_within_one_batch_the_newest_source_time_wins_not_the_last_row:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[((`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[3];1.30;1.32);(`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[1];1.00;1.02))]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`source;`LP_A;"a batch in the wrong order is still resolved by time"];
    .qunit.assertEquals[count first exec rows from .qt.qetl.job.stream.published;1;"and publishes one row for the sym"]};

.qt.qpipe.job.last_value.test_an_equal_source_time_follows_arrival_order:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[((`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];1.10;1.12);(`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[1];1.20;1.22))]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];1.30;1.32)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`source`bid;(`LP_A;1.30);"the last to arrive of equal times"]};

.qt.qpipe.job.last_value.test_syms_are_independent:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[((`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[5];1.10;1.12);(`GBPUSD;`LP_A;.qt.qpipe.job.last_value.d[1];1.30;1.32))]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`GBPUSD;`LP_A;.qt.qpipe.job.last_value.d[2];1.40;1.42)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[3];0.90;0.92)]];
    r:`sym xasc .qt.qpipe.job.last_value.latest[];
    .qunit.assertEquals[r`sym;`EURUSD`GBPUSD;"both held"];
    .qunit.assertEquals[r`bid;1.10 1.40;"GBPUSD advanced; EURUSD's older book was dropped, regardless of GBPUSD's time"]};

.qt.qpipe.job.last_value.test_a_withdrawn_side_keeps_the_last_price:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[((`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];0n;1.12);(`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[2];1.15;0n))]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`source_time;.qt.qpipe.job.last_value.d[0];"an empty ladder is a withdrawal, not a price of null"];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;1;"and is not published"]};

.qt.qpipe.job.last_value.test_apply_is_the_identity_on_an_empty_batch_and_does_not_mutate_state:{[t]
    .qt.qpipe.job.last_value.reset[];
    s:.qpipe.job.last_value.state;
    r:.qpipe.job.last_value.apply[s;.qpipe.job.last_value.tob;0#.qpipe.job.last_value.market_data];
    .qunit.assertEquals[r`state;s;"state unchanged"];
    .qunit.assertEquals[count r`changed;0;"nothing changed"];
    r:.qpipe.job.last_value.apply[s;.qpipe.job.last_value.tob;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12)]];
    .qunit.assertEquals[(count s;count r`state);(0;1);"apply returns the new state and leaves the old one"]};

.qt.qpipe.job.last_value.test_a_replay_rebuilds_the_state_without_publishing_it_again:{[t]
    .qt.qpipe.job.last_value.reset[];
    replayed:((`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];1.10;1.12)]);
        (`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[3];1.20;1.22)]);
        (`market_data;.qt.qpipe.job.last_value.book[enlist (`GBPUSD;`LP_A;.qt.qpipe.job.last_value.d[2];1.30;1.32)]);
        (`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[2];1.00;1.02)]));
    .qetl.job.stream.start[`last_value;.qt.qetl.job.stream.fake_transport[`last_value;replayed]];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;0;"what the replay recomputed was published before the restart"];
    r:`sym xasc .qt.qpipe.job.last_value.latest[];
    .qunit.assertEquals[(r`sym;r`source;r`bid);(`EURUSD`GBPUSD;`LP_B`LP_A;1.20 1.30);"the same state the live run held"];
    .qt.qetl.job.stream.live[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];0.5;0.6)]];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;0;"a stale live book after the rebuild is still older than the rebuilt one"];
    .qt.qetl.job.stream.live[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[4];1.40;1.42)]];
    .qunit.assertEquals[count .qt.qetl.job.stream.published;1;"a newer live book publishes again"]};

/ The one rule for "the top of book" (#998): posbook and last_value read a
/ market_data book's sides through .qf.mkt's per-market extractions, and
/ crypto_markout a crypto_book's through .qf.mkt.top_sides. This drives one
/ sequence, with a withdrawn side and a non-positive level, through all of
/ them and holds them to it.
.qt.qpipe.job.last_value.agreement_rows:{[]
    ([] time:7#.qt.qpipe.job.last_value.d[0]; sym:7#`EURUSD; source:`a`b`c`d`e`f`g;
        market:7#`fx; source_time:7#.qt.qpipe.job.last_value.d[0];
        bid_prices:(enlist 1.10;`float$();enlist 1.10;enlist 0f;enlist 1.10;enlist 0w;enlist 1.10);
        bid_sizes:7#enlist enlist 1e6;
        ask_prices:(enlist 1.12;enlist 1.12;`float$();enlist 1.12;enlist 1.12;enlist 1.12;enlist 0w);
        ask_sizes:7#enlist enlist 1e6)}

.qt.qpipe.job.last_value.test_all_consumers_agree_on_a_withdrawn_or_non_positive_side:{[t]
    rows:.qt.qpipe.job.last_value.agreement_rows[];
    ref:.qf.mkt.top_sides rows;
    .qunit.assertEquals[ref`bid;1.10 0n 1.10 0n 1.10 0n 1.10;"an empty ladder, a zero level and an infinite one are all a null side (#1020)"];
    .qunit.assertEquals[ref`ask;1.12 1.12 0n 1.12 1.12 1.12 0n;"on either side"];
    v:.qf.mkt.venue_tops update market:`crypto from rows;
    .qunit.assertEquals[(v`bid;v`ask);(ref`bid;ref`ask);"a venue's top keeps a withdrawn side, null"];
    mk:.qetl.plant.with_flat update clock:`venue from rows;
    .qunit.assertEquals[(mk`bid;mk`ask);(ref`bid;ref`ask);"the flat top every mapping stores, which the markout core scores from"];
    own:.qf.mkt.own_book_mids rows;
    both:where (not null ref`bid) & not null ref`ask;
    .qunit.assertEquals[own`source;(rows`source) both;"a book's own mid needs both sides"]};

.qt.qpipe.job.last_value.test_a_market_with_no_price_rule_is_refused_by_name:{[t]
    rows:update market:`rates from .qt.qpipe.job.last_value.agreement_rows[];
    .qunit.assertThrows[.qf.mkt.venue_tops;rows;"*no pricing rule for market rates*";"not priced by whichever branch a consumer wrote"];
    .qunit.assertThrows[.qf.mkt.own_book_mids;rows;"*no pricing rule for market rates*";"on either extraction"]};

/ #2417: the databento sidecar publishes equity books onto market_data. A batch
/ holding one used to be refused whole by posbook and last_value, FX books included.
.qt.qpipe.job.last_value.test_an_equity_book_is_priced_on_its_own_book_by_both_jobs:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qetl.job.stream.reset `posbook;
    md:.qetl.plant.with_flat ([] sym:`EURUSD`AAPL; source:`LP_A`XNAS; market:`fx`equity;
        source_time:2#.qt.qpipe.job.last_value.d[0];
        bid_prices:(enlist 1.1;enlist 271.5); bid_sizes:(enlist 1e6;enlist 100f);
        ask_prices:(enlist 1.1002;enlist 271.6); ask_sizes:(enlist 1e6;enlist 100f); clock:`receipt`venue);
    .qpipe.job.posbook.on_batch[`market_data;md];
    .qt.support.assertApprox[.qpipe.job.posbook.fx_marks[.qpipe.job.posbook.mid_history;([] time:2#.qt.qpipe.job.last_value.d[1]; sym:`EURUSD`AAPL)];1.1001 271.55;1e-9;
        "posbook holds the FX mid and the equity book's own mid"];
    r:.qpipe.job.last_value.apply[.qpipe.job.last_value.state;.qpipe.job.last_value.tob;md];
    c:`sym xasc r`changed;
    .qunit.assertEquals[c`sym`source;(`AAPL`EURUSD;`XNAS`LP_A);"last_value publishes both books"];
    .qt.support.assertApprox[c`mid;271.55 1.1001;1e-9;"each on its own mid"]};

/ #998: posbook marks and last_value publishes by the same rule, so they
/ agree when a venue withdraws a side and another goes stale. last_value
/ used to drop a book with an empty side, which left that venue's previous
/ bid counting in its reference while posbook's no longer did.
.qt.qpipe.job.last_value.test_posbook_and_last_value_agree_on_every_market:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qetl.job.stream.reset `posbook;
    s:`$"BTC-USDT";
    batches:(
        .qt.qpipe.job.last_value.cbook[((s;`binance;.qt.qpipe.job.last_value.d[0];100.;101.);(s;`okx;.qt.qpipe.job.last_value.d[0];99.;102.);(s;`kraken;.qt.qpipe.job.last_value.d[-10];105.;106.))];
        .qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[1];0n;101.)];
        .qt.qpipe.job.last_value.book[((`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12);(`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[1];0n;1.12))]);
    {.qpipe.job.last_value.on_batch[`market_data;x]; .qpipe.job.posbook.on_batch[`market_data;x]} each batches;
    lv:exec sym!mid from .qt.qpipe.job.last_value.latest[];
    pb:first .qpipe.job.posbook.crypto_marks[.qpipe.job.posbook.crypto_tob;([] time:enlist .qt.qpipe.job.last_value.d[1]; sym:enlist s)];
    .qunit.assertEquals[pb;100f;"posbook: binance's bid withdrawn, kraken stale - okx 99 against binance 101"];
    .qunit.assertEquals[lv s;pb;"last_value publishes the mark posbook uses"];
    .qunit.assertEquals[(.qt.qetl.job.stream.pb_latest_mid`EURUSD;lv`EURUSD);1.11 1.11;"FX: the withdrawn book leaves the last mid standing in both"]};

.qt.qpipe.job.last_value.test_no_consumer_re_derives_the_top_of_book:{[t]
    files:`$"src/etl/streaming/",/:("posbook";"crypto_markout";"last_value"),\:".q";
    src:{" " sv read0 hsym x} each files;
    .qunit.assertTrue[not any src like\: "*first each bid_prices*";"a consumer reads level 0 itself"];
    .qunit.assertTrue[not any src like\: "*first px*";"a consumer reads a side itself"]};

/ #990: a crypto sym publishes the cross-venue reference crypto_markout and
/ posbook price from - the best bid and best ask over live venues - not the
/ last venue's own book.
.qt.qpipe.job.last_value.test_a_crypto_sym_publishes_the_best_bid_and_ask_across_venues:{[t]
    .qt.qpipe.job.last_value.reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[0];100.;102.)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`okx;.qt.qpipe.job.last_value.d[1];101.;103.)]];
    r:first .qt.qpipe.job.last_value.latest[];
    .qunit.assertEquals[r`bid`ask`mid;101 102 101.5;"best bid okx, best ask binance"];
    .qunit.assertEquals[(r`source;r`source_time);(`venues;.qt.qpipe.job.last_value.d[1]);"a reference, as of the newest venue"];
    want:.qf.mkt.best_mid_across_venues[select time:source_time, sym, venue:source, bid, ask from
        ([] sym:2#s; source:`binance`okx; source_time:.qt.qpipe.job.last_value.d[0 1]; bid:100 101.; ask:102 103.);
        ([] sym:enlist s; time:enlist .qt.qpipe.job.last_value.d 1);.qf.mkt.reference_max_age];
    .qunit.assertEquals[r`mid;first want;"the shared reference, the one crypto_markout prices from"]};

.qt.qpipe.job.last_value.test_a_stale_venue_drops_out_of_the_crypto_reference:{[t]
    .qt.qpipe.job.last_value.reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[0];100.;102.)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`okx;.qt.qpipe.job.last_value.d[10];99.;103.)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`bid`ask;99 103.;"binance's book is older than reference_max_age"]};

.qt.qpipe.job.last_value.test_an_older_crypto_book_does_not_rewind_its_venue:{[t]
    .qt.qpipe.job.last_value.reset[];
    s:`$"BTC-USDT";
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[2];100.;102.)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[1];90.;92.)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`bid`ask;100 102.;"the venue's newer book stands"]};

/ #1088: FX's rule is the newest book's own mid, and "newest" is the
/ source's clock, not arrival: a late, older book moves neither posbook's
/ mark nor last_value's row - one rule, .qf.mkt.newer_by_sym.
.qt.qpipe.job.last_value.test_a_late_older_fx_book_moves_neither_mark:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qetl.job.stream.reset `posbook;
    both:{[x] .qpipe.job.last_value.on_batch[`market_data;x]; .qpipe.job.posbook.on_batch[`market_data;x]};
    both .qt.qpipe.job.last_value.book enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[2];1.20;1.22);
    both .qt.qpipe.job.last_value.book enlist (`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[1];1.00;1.02);
    lv:(exec sym!mid from .qt.qpipe.job.last_value.latest[])`EURUSD;
    .qt.support.assertApprox[.qt.qetl.job.stream.pb_latest_mid`EURUSD;1.21;1e-12;"posbook keeps the newer book's mid"];
    .qunit.assertEquals[.qt.qetl.job.stream.pb_latest_mid`EURUSD;lv;"the mark last_value publishes"]};

.qt.qpipe.job.last_value.test_fx_is_still_last_source_wins:{[t]
    .qt.qpipe.job.last_value.reset[];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.14)]];
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_B;.qt.qpipe.job.last_value.d[1];1.11;1.15)]];
    .qunit.assertEquals[(first .qt.qpipe.job.last_value.latest[])`bid`ask`source;(1.11;1.15;`LP_B);"FX unchanged by #990"]};

/ What test_job_output_contracts.q drives last_value with, so the table it
/ publishes is held to its plant table by name, order and type.
.qt.qpipe.job.last_value.contract_driver:{[]
    .qetl.job.stream.reset `last_value;
    .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.book[enlist (`EURUSD;`LP_A;.qt.qpipe.job.last_value.d[0];1.10;1.12)]]}

/ #2656: last_value and posbook each hold their own reference age, read when
/ used, defaulted once from .qf.mkt.reference_max_age. Tuning one moves
/ neither the other nor the library constant.
.qt.qpipe.job.last_value.test_last_value_and_posbook_hold_separate_reference_ages:{[t]
    lib:.qf.mkt.reference_max_age;
    .qunit.assertEquals[(.qpipe.job.last_value.reference_max_age;.qpipe.job.posbook.reference_max_age);(lib;lib);"both default to the library's value"];
    s:`$"BTC-USDT";
    / binance quotes 10s before okx: stale at the 5s default, live at 20s
    feed:{[s] .qt.qpipe.job.last_value.reset[];
        .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`binance;.qt.qpipe.job.last_value.d[0];100.;104.)]];
        .qpipe.job.last_value.on_batch[`market_data;.qt.qpipe.job.last_value.cbook[enlist (s;`okx;.qt.qpipe.job.last_value.d[10];99.;103.)]];
        (first .qt.qpipe.job.last_value.latest[])`bid};
    tob:.qetl.enrich.add[.qpipe.job.posbook.crypto_tob;([] sym:2#s; venue:`binance`okx; time:.qt.qpipe.job.last_value.d[0 10]; bid:100 99f; ask:104 103f)];
    fill:([] time:enlist .qt.qpipe.job.last_value.d[10]; sym:enlist s);
    base:(feed s;first .qpipe.job.posbook.crypto_marks[tob;fill]);
    `.qpipe.job.last_value.reference_max_age set 0D00:00:20;
    lv:(feed s;first .qpipe.job.posbook.crypto_marks[tob;fill]);
    `.qpipe.job.last_value.reference_max_age set lib;
    `.qpipe.job.posbook.reference_max_age set 0D00:00:20;
    pb:(feed s;first .qpipe.job.posbook.crypto_marks[tob;fill]);
    `.qpipe.job.posbook.reference_max_age set lib;
    .qunit.assertEquals[base;(99f;101f);"at the default only okx is live"];
    .qunit.assertEquals[lv;(100f;101f);"last_value's age widened: it sees binance, posbook's mark does not move"];
    .qunit.assertEquals[pb;(99f;101.5);"posbook's age widened: its mark sees binance, last_value does not"];
    .qunit.assertEquals[.qf.mkt.reference_max_age;lib;"and the library constant was never the setting"]};
