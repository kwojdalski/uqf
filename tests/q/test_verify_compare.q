/ test_verify_compare.q - do the producers return the same rows? (.qetl.verify,
/ src/etl/core/verify_compare.q, #1851). (.qt.qetl.verify.compare).
/ .
/ The rule under test: rows are paired by business key and compared by value and
/ multiplicity, and equal counts never pass on their own.

.qt.qetl.verify.compare.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

.qt.qetl.verify.compare.rogue:`vcmp_rogue

.qt.qetl.verify.compare.forget:{[]
    `.qetl.job.stream.jobs set (enlist .qt.qetl.verify.compare.rogue) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set (enlist `vcmp_rogue1) _ .qetl.job.stream.procnames;
    .qetl.job.continuous.clear_cursor each `demo_deals_feed`vcmp_rogue;
    `.qetl.plant.equivalence_of set (enlist `demo_deals)!enlist `events;}

.qt.qetl.verify.compare.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";}

.qt.qetl.verify.compare.setUp_fresh:{[] .qt.qetl.verify.compare.forget[];}
.qt.qetl.verify.compare.tearDown_forget:{[] .qt.qetl.verify.compare.forget[];}

/ A third producer of demo_deals whose rows are the fixture's, changed by `shape`.
.qt.qetl.verify.compare.declare_rogue:{[shape]
    .qetl.job.stream.define[.qt.qetl.verify.compare.rogue;`procname`subscribe_to`outputs`period`poll!(
        `vcmp_rogue1;`symbol$();enlist `demo_deals;0D00:00:01;
        `fetch`normalize`next_cursor`source!(
            {[shape;c] shape select from .qpipe.source.demo_deals.fixture[] where deal_time>c}[shape];
            {[p] (cols .qetl.plant.published `demo_deals)#p};{[p] last p`deal_time};`demo_deals))]}

.qt.qetl.verify.compare.against_rogue:{[shape]
    .qt.qetl.verify.compare.declare_rogue shape;
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.compare.d 1;.qt.qetl.verify.compare.d 4];
    .qetl.verify.cross_mode[`demo_deals;c]}

/ --- compare_rows ----------------------------------------------------------

.qt.qetl.verify.compare.a:([] k:1 2 3 3j; v:1 2 3 3j; w:1.5 2 3 3f)

.qt.qetl.verify.compare.test_identical_rows_in_another_order_are_equal_as_events:{[t]
    b:reverse .qt.qetl.verify.compare.a;
    .qunit.assertEquals[count .qetl.verify.compare_rows[.qt.qetl.verify.compare.a;b;enlist `k;`symbol$();`events];0;"order is not a value"]};

.qt.qetl.verify.compare.test_missing_extra_changed_and_duplicated_are_each_detected:{[t]
    b:([] k:1 2 4 3 3 3j; v:1 9 4 3 3 3j; w:1.5 2 4 3 3 3f);
    r:.qetl.verify.compare_rows[.qt.qetl.verify.compare.a;b;enlist `k;`symbol$();`events];
    .qunit.assertEquals[(r`rowkey;r`kind;r`detail);
        (("k=4";"k=3";"k=2");`extra`duplicated`changed;("in the second producer only";"first producer 2 time(s), second 3";"v: 2 -> 9"));
        "each named by key, with what differs"];
    m:.qetl.verify.compare_rows[b;.qt.qetl.verify.compare.a;enlist `k;`symbol$();`events];
    .qunit.assertTrue[`missing in m`kind;"and a row only the reference holds is missing"]};

.qt.qetl.verify.compare.test_equal_counts_with_different_keys_do_not_pass:{[t]
    b:([] k:1 2 7 7j; v:1 2 3 3j; w:1.5 2 3 3f);
    r:.qetl.verify.compare_rows[.qt.qetl.verify.compare.a;b;enlist `k;`symbol$();`events];
    .qunit.assertEquals[(count .qt.qetl.verify.compare.a)=count b;1b;"four rows each"];
    .qunit.assertEquals[(r`rowkey;r`kind);(("k=3";"k=7");`missing`extra);"and not one pair of the last two"]};

.qt.qetl.verify.compare.test_a_destination_generated_column_is_left_out:{[t]
    a:([] k:1 2j; time:2026.01.01D00:00:00 2026.01.01D00:00:01; v:1 2j);
    b:([] k:1 2j; time:2026.05.05D00:00:00 2026.05.05D00:00:01; v:1 2j);
    .qunit.assertEquals[count .qetl.verify.compare_rows[a;b;enlist `k;`symbol$();`events];2;"stamped differently, they differ"];
    .qunit.assertEquals[count .qetl.verify.compare_rows[a;b;enlist `k;enlist `time;`events];0;"left out, they are equal"]};

.qt.qetl.verify.compare.test_floats_compare_within_the_transform_tolerance:{[t]
    a:([] k:enlist 1j; x:enlist 1.0f);
    .qunit.assertEquals[count .qetl.verify.compare_rows[a;([] k:enlist 1j; x:enlist 1.0000000000001f);enlist `k;`symbol$();`events];0;"rounding noise"];
    .qunit.assertEquals[count .qetl.verify.compare_rows[a;([] k:enlist 1j; x:enlist 1.001f);enlist `k;`symbol$();`events];1;"a real difference"]};

.qt.qetl.verify.compare.test_final_state_compares_only_the_last_row_per_key:{[t]
    a:([] k:1 1 2j; v:1 5 2j);
    b:([] k:1 2j; v:5 2j);
    .qunit.assertEquals[count .qetl.verify.compare_rows[a;b;enlist `k;`symbol$();`final_state];0;"the earlier revision is not compared"];
    .qunit.assertEquals[.qetl.verify.compare_rows[a;b;enlist `k;`symbol$();`revisions]`kind;`duplicated`changed;
        "revisions compare the whole sequence: one fewer, and the first is not the same"]};

.qt.qetl.verify.compare.test_a_composite_key_pairs_on_every_column:{[t]
    a:([] k:1 1j; s:`a`b; v:1 2j);
    b:([] k:1 1j; s:`a`b; v:1 3j);
    r:.qetl.verify.compare_rows[a;b;`k`s;`symbol$();`events];
    .qunit.assertEquals[(r`rowkey;r`detail);(enlist "k=1, s=`b";enlist "v: 2 -> 3");"the row keyed (1;b) changed"]};

/ --- cross_mode over a capture -------------------------------------------

.qt.qetl.verify.compare.test_the_demo_deals_pair_passes_on_the_same_window:{[t]
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.compare.d 1;.qt.qetl.verify.compare.d 4];
    r:.qetl.verify.cross_mode[`demo_deals;c];
    .qunit.assertEquals[(r`status;count r`findings);(`PASS;0);"the feed and the backfill agree"];
    .qunit.assertEquals[r`basis;"events on deal_id; compared deal_id, deal_time, sym, side, notional, rate; left out, generated by the destination: time; reference demo_deals_feed";
        "the basis says what was paired and what was left out"]};

.qt.qetl.verify.compare.test_same_counts_but_a_different_row_fails:{[t]
    r:.qt.qetl.verify.compare.against_rogue {[p] update deal_id:99 from p where deal_id=3};
    .qunit.assertEquals[r`status;`FAIL;"three rows each, and one is not the same deal"];
    .qunit.assertEquals[(r`findings)`kind;`missing`extra;"deal 3 is missing and deal 99 is extra"];
    .qunit.assertEquals[(r`findings)`producer;`vcmp_rogue`vcmp_rogue;"and the finding names the producer"]};

.qt.qetl.verify.compare.test_a_changed_value_fails_naming_the_column:{[t]
    r:.qt.qetl.verify.compare.against_rogue {[p] update rate:rate+0.5 from p where deal_id=2};
    .qunit.assertEquals[(r`status;(r`findings)`kind;(r`findings)`rowkey);(`FAIL;enlist `changed;enlist "deal_id=2")];
    .qunit.assertTrue[((first (r`findings)`detail) like "rate: 1.2631 -> 1.7631*");"the two values"]};

.qt.qetl.verify.compare.test_a_duplicated_row_fails:{[t]
    r:.qt.qetl.verify.compare.against_rogue {[p] (1#p),p};
    .qunit.assertEquals[(r`status;(r`findings)`kind;first (r`findings)`detail);(`FAIL;enlist `duplicated;"first producer 1 time(s), second 2")]};

.qt.qetl.verify.compare.test_an_empty_window_is_unverified_not_a_pass:{[t]
    c:.qetl.verify.capture[`demo_deals;2027.01.01D00:00:00;2027.01.02D00:00:00];
    r:.qetl.verify.cross_mode[`demo_deals;c];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"empty sample - no producer returned a row in the window");"nothing was compared"]};

.qt.qetl.verify.compare.test_one_producer_is_unverified_with_the_reason:{[t]
    p:.qetl.verify.producers `demo_deals;
    one:(`producers`captures)!(1#p;()!());
    r:.qetl.verify.cross_mode[`demo_deals;one];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"one producer (demo_deals_feed) - there is nothing to compare it with");"not a failure"]};

.qt.qetl.verify.compare.test_an_undeclared_basis_is_unverified:{[t]
    `.qetl.plant.equivalence_of set (`symbol$())!`symbol$();
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.compare.d 1;.qt.qetl.verify.compare.d 4];
    r:.qetl.verify.cross_mode[`demo_deals;c];
    .qunit.assertEquals[r`status;`UNVERIFIED;"a shared schema does not say what 'the same' means"];
    .qunit.assertTrue[(r`reason) like "equivalence basis undeclared*";"and the reason says so"]};

.qt.qetl.verify.compare.test_a_failed_capture_is_unverified_with_its_error:{[t]
    .qt.qetl.verify.compare.declare_rogue {[p] '"source unreachable"};
    c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.compare.d 1;.qt.qetl.verify.compare.d 4];
    r:.qetl.verify.cross_mode[`demo_deals;c];
    .qunit.assertEquals[(r`status;r`reason);(`UNVERIFIED;"capture of vcmp_rogue failed: source unreachable");"nothing can pass on rows that were not read"]};

/ A bounded capture whose window failed its check leaves `rows unset (#2401). It
/ was read as a producer that returned no rows: FAIL, every reference row
/ "missing" - deals 1 and 3 included - and the failure itself never named.
.qt.qetl.verify.compare.test_a_bounded_capture_with_a_failed_window_is_unverified:{[t]
    w:`demo_deals_backfill;
    keep:.qetl.job.bounded.worker_cfg w;
    chk:{[b] $[2 in b`deal_id; ([] check:enlist `bad_rate; status:enlist `fail; detail:enlist "rate"); .qetl.job.bounded.no_failures[]]};
    .qetl.job.bounded.worker_cfg[w]:@[keep;`check;:;chk];
    r:@[{c:.qetl.verify.capture[`demo_deals;.qt.qetl.verify.compare.d[1];.qt.qetl.verify.compare.d[4]]; .qetl.verify.cross_mode[`demo_deals;c]};::;{[e] e}];
    .qetl.job.bounded.worker_cfg[w]:keep;
    .qunit.assertEquals[(r`status;count r`findings);(`UNVERIFIED;0);"a failed capture is no sample, and no row is missing"];
    .qunit.assertTrue[(r`reason) like "capture of demo_deals_backfill failed 1 window(s): window failed data quality*";
        "the reason names the producer and its failed window"]};

.qt.qetl.verify.compare.test_equivalence_refuses_a_word_that_is_not_a_basis:{[t]
    .qunit.assertThrows[{.qetl.plant.equivalence[`demo_deals;`whatever]};::;"equivalence: demo_deals's basis `whatever is none of events, revisions, final_state";"named"]};

.qt.qetl.verify.compare.test_a_table_declares_its_basis_once:{[t]
    .qunit.assertThrows[{.qetl.plant.equivalence[`demo_deals;`events]};::;"equivalence: demo_deals already declares a basis";"a second declaration is refused"];
    .qunit.assertEquals[(.qetl.verify.basis `demo_deals;.qetl.verify.basis `no_such_table);(`events;`);"read back, null where none"]};
