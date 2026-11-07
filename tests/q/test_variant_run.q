/ test_variant_run.q - a list of reference variants over one batch (src/etl/core/variant_run.q, .qetl.variant, #2289).
/ .
/ The claims, each held to an identity and not to "it ran":
/   * evaluating a LIST gives, for each variant, exactly what .qetl.reference.at gives it alone - sharing the SELECT
/     and the transforms changes the cost and not one price
/   * a plan shares what is shareable: a thousand variants over two venue sets are two SELECT groups
/   * variant #1 is bit-identical, and keeps its config_id; every other variant has its own, which resolves to a
/     snapshot that records it
/   * promoting a variant to stored flat columns gives bit-identical prices (Tier 1)
/   * the live path, evaluating over a buffer in arrival order, prices what the gathered path does
/ The mapped-HDB side (one gather, flat in the variant count) is tests/q/test_variant_scale.q.

.qt.qetl.variant.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-variant-run"];
    system"mkdir -p build/test-status-variant-run";
    }

.qt.qetl.variant.setUp_fresh:{[] .qt.support.reset_snapshots[];}

/ Books of three sources on two clocks across ten minutes, each with a ladder of 0 to 4 levels, a tenth withdrawn.
.qt.qetl.variant.books:{[n;seed]
    system "S ",string seed;
    levels:n?0 1 2 3 4;
    px:1+n?1f;
    bid_prices:{[p;l] p-0.0001*til l}'[px;levels];
    ask_prices:{[p;l] p+0.0002+0.0001*til l}'[px;levels];
    md:([] sym:n?`EURUSD`GBPUSD`USDJPY; source:n?`a`b`c`d; source_time:2026.09.17D10:00:00+n?600000000000;
        bid_prices:bid_prices; bid_sizes:{[l] 1e5*1+l?9}each levels; ask_prices:ask_prices; ask_sizes:{[l] 1e5*1+l?9}each levels);
    md:update bid:{$[count x; first x; 0n]} each bid_prices, ask:{$[count x; first x; 0n]} each ask_prices from md;
    md:update clock:?[source in `a`b;`venue;`receipt] from md;
    md}

.qt.qetl.variant.targets:{[m;seed]
    system "S ",string seed;
    ([] sym:m?`EURUSD`GBPUSD`USDJPY`NOBOOK; time:2026.09.17D10:00:00+m?700000000000; clock:m?`venue`receipt)}

/ The variants a list may be evaluated over the plain books: every registered one but the promoted forms, which read
/ stored columns (a documented example registers one and leaves it).
.qt.qetl.variant.declared:{[] n where not (string each n:key .qetl.reference.variants) like "*_flat"}

/ Take test variants out of the registry again.
.qt.qetl.variant.forget:{[names] `.qetl.reference.variants set names _ .qetl.reference.variants}

/ A spread of declared variants for the grid: every starter, with different selects, parameters and combines.
.qt.qetl.variant.grid:{[]
    d:`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`top;`best;(`symbol$())!());
    / registered one at a time: a list of same-keyed dictionaries would be flipped into a table
    reg:{[d;n;ch] .qetl.reference.register[n;d,ch]; n}[d];
    (reg[`grid_0;`transform`combine!(`micro;`best)];
     reg[`grid_1;`venues`transform`combine!(`a`b;`micro;`wmean)];
     reg[`grid_2;`stale`offset`transform`combine`params!(0D00:00:30;0D00:00:02;`vwap;`median;enlist[`qty]!enlist 150000f)];
     reg[`grid_3;`transform`combine`params!(`vwap;`best;enlist[`qty]!enlist 300000f)];
     reg[`grid_4;`venues`transform`combine`params!(enlist `c;`topn_w;`wmean;enlist[`n]!enlist 2)];
     reg[`grid_5;`offset`transform`combine`params!(0D00:00:03;`topn_w;`median;enlist[`n]!enlist 4)];
     reg[`grid_6;`transform`combine!(`mid_sized;`wmean)])}

.qt.qetl.variant.test_a_list_prices_each_variant_exactly_as_that_variant_alone:{[t]
    md:.qt.qetl.variant.books[3000;21];
    tg:.qt.qetl.variant.targets[1500;22];
    sl:.qetl.variant.slice md;
    extra:.qt.qetl.variant.grid[];
    names:.qt.qetl.variant.declared[];
    {[names;sl;tg;ma]
        all_:.qetl.variant.evaluate[names;sl;ma;tg`sym;tg`time;tg`clock];
        {[all_;sl;tg;ma;n]
            alone:.qetl.reference.at[n;sl;ma;tg`sym;tg`time;tg`clock];
            .qunit.assertEquals[all_ n;alone;"the list gives ",string[n]," what it gets alone, max_age ",string ma]}[all_;sl;tg;ma] each names}[names;sl;tg] each 0D00:00:05 0D00:01:00 0Wn;
    r:.qetl.variant.evaluate[names;sl;0D00:01:00;tg`sym;tg`time;tg`clock];
    priced:sum {not all null ((x`same)`mid)} each value r;
    .qt.qetl.variant.forget extra;
    .qunit.assertTrue[priced>=count[names]-1;"the fixture prices instants under (almost) every variant: ",string[priced]," of ",string count names]};

.qt.qetl.variant.test_variant_one_through_a_list_is_bit_identical_to_the_core_alone:{[t]
    md:.qt.qetl.variant.books[3000;23];
    tg:.qt.qetl.variant.targets[1500;24];
    sl:.qetl.variant.slice md;
    flat:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from md;
    in_list:(.qetl.variant.evaluate[.qt.qetl.variant.declared[];sl;0D00:00:05;tg`sym;tg`time;tg`clock])`best_top;
    alone:.qetl.reference.at[`best_top;flat;0D00:00:05;tg`sym;tg`time;tg`clock];
    .qunit.assertEquals[in_list;alone;"variant #1 over the gathered slice equals variant #1 over the bare flat top"]};

.qt.qetl.variant.test_a_plan_shares_the_select_and_the_transform_between_variants:{[t]
    d:`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`vwap;`best;enlist[`qty]!enlist 1e5);
    / 120 variants: three venue sets, three offsets, 120 quantities
    nm:`$"plan_",/:string til 120;
    {[d;n;v;o;q] .qetl.reference.register[n;d,`venues`offset`params!(v;o;enlist[`qty]!enlist q)]}[d]'[nm;120#(`all;enlist `a;enlist `b);120#0D 0D00:00:01 0D00:00:02;1e5*1+til 120];
    p:.qetl.variant.plan[nm;0D00:00:05];
    g:.qetl.variant.mapped_groups p;
    .qt.qetl.variant.forget nm;
    .qunit.assertEquals[count p;120;"one plan row per variant"];
    .qunit.assertTrue[(count distinct p`select_id)<=9;"however many variants, the SELECTs are the distinct (venues; staleness; offset)"];
    .qunit.assertEquals[count g;count distinct flip (p`venues;p`offset);"and the market is asked once per (venue set; offset)"];
    .qunit.assertEquals[count distinct p`transform_id;120;"each quantity is its own transform"]};

.qt.qetl.variant.test_the_limit_reaches_back_for_a_variant_staler_than_the_spec:{[t]
    p:.qetl.variant.plan[enlist `best_top;0D00:00:05];
    .qunit.assertEquals[.qetl.variant.reads_from[p;0D00:00:05;2026.09.17D10:00:00];2026.09.17D10:00:00;"the spec's own limit reads from where it always did"];
    .qetl.reference.register[`stale_eg;`venues`stale`offset`transform`combine!(`all;0D00:01:00;0D;`top;`best)];
    q:.qetl.variant.plan[`best_top`stale_eg;0D00:00:05];
    .qt.qetl.variant.forget enlist `stale_eg;
    .qunit.assertEquals[.qetl.variant.reads_from[q;0D00:00:05;2026.09.17D10:00:00];2026.09.17D09:59:05;"a minute-old book is a reference to it: 55s earlier"]};

.qt.qetl.variant.test_promoting_a_variant_to_stored_columns_gives_identical_values:{[t]
    md:.qt.qetl.variant.books[3000;25];
    tg:.qt.qetl.variant.targets[1500;26];
    sl:.qetl.variant.slice md;
    names:`best_top`micro_median`vwap_1m_best`sizew_mid`median_top`depth3_wmean;
    .qetl.reference.register[`vwap_small;`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`vwap;`best;enlist[`qty]!enlist 100000f)];
    names,:`vwap_small;
    check:{[sl;tg;n]
        promoted:.qetl.variant.promote n;
        flat:.qetl.variant.flat[n;sl];
        a:.qetl.reference.at[n;sl;0D00:01:00;tg`sym;tg`time;tg`clock];
        b:.qetl.reference.at[promoted;flat;0D00:01:00;tg`sym;tg`time;tg`clock];
        .qunit.assertEquals[b;a;string[n]," read from stored columns is the value it computes"];
        .qunit.assertFalse[all null (a`same)`mid;string[n]," prices something, so the comparison is not null against null"]}[sl;tg];
    check each names;
    .qt.qetl.variant.forget (`$(string each names),\:"_flat"),enlist `vwap_small;
    .qunit.assertEquals[(.qetl.variant.flat[`depth3_wmean;sl]) `bid;{x} (.qetl.reference.transform[`topn_w][`fn][sl;enlist[`n]!enlist 3])`bid;"the stored column IS the transform's answer"]};

.qt.qetl.variant.test_the_live_path_evaluates_the_same_variants_over_a_buffer_in_arrival_order:{[t]
    md:.qt.qetl.variant.books[2000;27];
    tg:.qt.qetl.variant.targets[800;28];
    arrival:`source_time xasc md;
    names:.qt.qetl.variant.declared[];
    live:.qetl.variant.live[names;arrival;0D00:01:00;tg`sym;tg`time;tg`clock];
    gathered:.qetl.variant.evaluate[names;.qetl.variant.slice md;0D00:01:00;tg`sym;tg`time;tg`clock];
    .qunit.assertEquals[live;gathered;"a buffer in arrival order and the gathered slice price every declared variant alike"]};

.qt.qetl.variant.test_a_depth_variant_over_books_without_ladders_is_refused_by_name:{[t]
    flat:.qetl.mapped.arrange ([] sym:enlist `EURUSD; source:enlist `a; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1.0; ask:enlist 2.0; clock:enlist `venue);
    .qunit.assertThrows[.qetl.variant.evaluate[`micro_median;flat;0Wn;enlist `EURUSD;enlist 2026.09.17D10:00:01;];enlist `venue;"reference: micro_median reads depth, so the books must carry*";"naming the variant and what the books lack"]};

.qt.qetl.variant.test_the_long_layout_has_a_variant_key_and_one_row_per_variant_and_target:{[t]
    md:.qt.qetl.variant.books[500;29];
    sl:.qetl.variant.slice md;
    r:.qetl.variant.evaluate[`best_top`micro_median`median_top;sl;0D00:01:00;4#`EURUSD;4#2026.09.17D10:05:00;4#`venue];
    l:.qetl.variant.long r;
    .qunit.assertEquals[cols l;`variant`row`bid`ask`mid;"variant first, then the target and its price"];
    .qunit.assertEquals[((exec count i by variant from l)`best_top`micro_median`median_top);3#4;"one row per variant per target"];
    .qunit.assertEquals[(select from l where variant=`micro_median)`mid;(r[`micro_median]`same)`mid;"each variant's rows are its own"]};

/ --- the configuration each variant has ------------------------------------------------------------------

.qt.qetl.variant.test_each_variant_has_its_own_config_id_and_variant_one_keeps_the_id_it_had:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    names:.qt.qetl.variant.declared[];
    ids:.qpipe.transform.markout_variants.ids[dom;sp;names];
    plain:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp]];
    .qunit.assertEquals[ids`best_top;plain;"variant #1's id is the id the spec had before variants existed"];
    .qunit.assertEquals[count distinct value ids;count names;"and every variant has an id of its own"];
    / the same declaration in another place yields the same id: it is the content's digest
    .qunit.assertEquals[.qpipe.transform.markout_variants.ids[dom;sp;`micro_median];enlist[`micro_median]!enlist ids`micro_median;"stable"]};

.qt.qetl.variant.test_a_result_row_resolves_through_its_config_id_to_the_variant_that_scored_it:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    names:`best_top`vwap_1m_best`depth3_wmean;
    pinned:.qpipe.transform.markout_variants.pin[dom;sp;names;"variants test"];
    .qunit.assertEquals[pinned;.qpipe.transform.markout_variants.ids[dom;sp;names];"pinning records the id every row will carry"];
    c:.qetl.snapshot.content_of pinned`vwap_1m_best;
    .qunit.assertEquals[(c`reference)`name;`vwap_1m_best;"the snapshot names the variant"];
    .qunit.assertEquals[((c`reference)`params)`qty;1000000f;"and its parameters"];
    .qunit.assertFalse[`reference in key .qetl.snapshot.content_of pinned`best_top;"variant #1 adds nothing to its snapshot"];
    / the markout: one result per variant, each row carrying its variant's id
    tm:2026.09.17D10:00:00;
    md:.qetl.variant.slice ([] sym:4#`$"BTC-USDT"; source:`a`b`a`b; source_time:(tm-0D00:00:01;tm-0D00:00:01;tm+0D00:00:09;tm+0D00:00:09);
        bid:61990 61995 62010 62015f; ask:62010 62005 62030 62025f; clock:4#`venue;
        bid_prices:(enlist 61990f;enlist 61995f;enlist 62010f;enlist 62015f); bid_sizes:(enlist 1f;enlist 1f;enlist 1f;enlist 1f);
        ask_prices:(enlist 62010f;enlist 62005f;enlist 62030f;enlist 62025f); ask_sizes:(enlist 1f;enlist 1f;enlist 1f;enlist 1f));
    e:([] source_time:enlist tm; venue:enlist `x; sym:enlist `$"BTC-USDT"; side:enlist 1; size:enlist 0.5; price:enlist 61998f; fill_id:enlist `c1; clock:enlist `venue);
    out:.qpipe.transform.markout_variants.calc[dom;sp;e;md;names];
    .qunit.assertEquals[distinct out`variant;names;"every variant scored"];
    .qunit.assertEquals[(exec first config_id by variant from out)names;pinned names;"each row's config_id is its variant's"];
    alone:.qpipe.transform.markout_core.calc[dom;sp;e;md];
    .qunit.assertEquals[(cols .qpipe.transform.markout_core.result)#select from out where variant=`best_top;alone;"variant #1's rows are the core's own, to the bit"];
    / a hand check: variant #1 at +10s is 62015/62025, mid 62020; a size of 1 on each level makes vwap_1m null, so its rows are unavailable
    .qunit.assertEquals[first exec ref_t1 from out where variant=`best_top, kind=`total;62020f;"best bid 62015, best ask 62025"];
    .qunit.assertTrue[all null exec ref_t1 from out where variant=`vwap_1m_best;"a size no book can fill prices nothing"];
    .qunit.assertEquals[first exec ref_t1 from out where variant=`depth3_wmean, kind=`total;62020f;"one level each: the size-weighted mid of the two venues, 62020"]};
