// test_transform.q - tests for src/etl/core/transform.q (.qetl.transform) and every
// transform registered with it, including the streaming jobs under src/etl/streaming/.
//
// The first test is the one that matters: it runs every registered
// transform's hand-written examples, so a job whose transform no longer
// produces its expected table fails the build. The rest pin the block's own
// refusals, each of which exists because the failure it prevents is silent.
//
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/support.q
// before this file.


.qt.qetl.transform.quotes_schema:([] sym:`symbol$(); bid:`float$(); ask:`float$())
.qt.qetl.transform.mid_schema:([] sym:`symbol$(); mid:`float$())
.qt.qetl.transform.two_quotes:([] sym:`EURUSD`GBPUSD; bid:1.1 1.3; ask:1.2 1.4)
.qt.qetl.transform.two_mids:([] sym:`EURUSD`GBPUSD; mid:1.15 1.35)

.qt.qetl.transform.decl:{[fn;examples]
    `inputs`output`fn`examples!(enlist[`quotes]!enlist .qt.qetl.transform.quotes_schema;.qt.qetl.transform.mid_schema;fn;examples)}

.qt.qetl.transform.mid_fn:{[q] select sym, mid:(bid+ask)%2 from q}

.qt.qetl.transform.good_example:{[] enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;.qt.qetl.transform.two_mids)}

/ Test transforms are removed after each test, so the "every registered
/ transform passes" test sees only the shipped ones.
.qt.qetl.transform.shipped:`symbol$()

.qt.qetl.transform.beforeNamespace_remember:{[] `.qt.qetl.transform.shipped set .qetl.transform.defined[];}

.qt.qetl.transform.tearDown_forget:{[] .qt.support.drop_rows[`.qetl.transform.registry;.qetl.transform.defined[] except .qt.qetl.transform.shipped];}

/ --- every registered transform -------------------------------------------

.qt.qetl.transform.test_every_registered_transform_passes_its_examples:{[t]
    r:select from .qetl.transform.verify_all[] where not passed;
    .qunit.assertEquals[count r;0;
        "every transform reproduces its expected tables, deterministically, and handles empty input: ",.Q.s1 r]};

.qt.qetl.transform.test_the_stream_jobs_and_backfill_workers_all_declare_a_transform:{[t]
    want:`demo_execution_quality`cross_quotes`position`mkt_orderbook`demo_deals_passthrough`demo_events_passthrough;
    .qunit.assertEquals[all want in .qetl.transform.defined[];1b;"each shipped job's transform is registered"]};

/ A stream transform's output is published positionally by .u.upd, so its
/ columns must be the tickerplant table's, in order, minus the `time` .u.upd
/ stamps. Checked against src/etl/plant_tables.q itself, read in a
/ scratch namespace so its root-level tables do not leak into other suites.
.qt.qetl.transform.test_stream_outputs_match_the_tickerplant_tables:{[t]
    stack:.qt.qetl.transform.stack_tables[];
    bad:{[stack;nm]
        want:delete time from stack nm;
        got:.qetl.transform.output_schema nm;
        $[(cols want)~cols got; (); enlist nm]
      }[stack] each `demo_execution_quality`position`mkt_orderbook;
    .qunit.assertEquals[count raze bad;0;"each published transform output matches its table, column for column: ",.Q.s1 raze bad]};

.qt.qetl.transform.stack_tables:{[]
    ls:read0 `$":src/etl/plant_tables.q";
    nms:`demo_execution_quality`position`mkt_orderbook;
    nms!{[ls;nm] line:first ls where ls like string[nm],":*"; value (1+line?":") _ line}[ls] each nms}

/ --- declaring --------------------------------------------------------------

.qt.qetl.transform.test_a_well_formed_transform_registers:{[t]
    .qunit.assertEquals[.qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];`xf_mid;
        "a declaration with inputs, output, fn and a non-empty example registers"]};

.qt.qetl.transform.test_a_transform_without_examples_is_refused:{[t]
    .qunit.assertError[{.qetl.transform.define[`xf_noex;x]};.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;()];
        "a transform with no expected output asserts nothing"]};

.qt.qetl.transform.test_a_transform_whose_examples_are_all_empty_is_refused:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.quotes_schema;.qt.qetl.transform.mid_schema);
    .qunit.assertError[{.qetl.transform.define[`xf_empty;x]};.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex];
        "an example with no rows proves only the empty case, which verify runs anyway"]};

.qt.qetl.transform.test_a_keyed_output_is_refused:{[t]
    d:.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]];
    d[`output]:1!.qt.qetl.transform.mid_schema;
    .qunit.assertError[{.qetl.transform.define[`xf_keyed;x]};d;"a keyed table cannot be published to a tickerplant"]};

.qt.qetl.transform.test_an_fn_of_the_wrong_arity_is_refused:{[t]
    .qunit.assertError[{.qetl.transform.define[`xf_arity;x]};.qt.qetl.transform.decl[{[a;b] a};.qt.qetl.transform.good_example[]];
        "fn takes one argument per declared input"]};

.qt.qetl.transform.test_an_example_of_the_wrong_shape_is_refused_at_declaration:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;([] sym:`EURUSD`GBPUSD; mid:1 2));
    .qunit.assertError[{.qetl.transform.define[`xf_badex;x]};.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex];
        "an expected table of the wrong type is a broken example, caught before anything runs"]};

.qt.qetl.transform.test_an_as_of_transform_needs_an_instant_in_every_example:{[t]
    d:.qt.qetl.transform.decl[{[q;at] .qt.qetl.transform.mid_fn q};.qt.qetl.transform.good_example[]];
    d[`as_of]:1b;
    .qunit.assertError[{.qetl.transform.define[`xf_clock;x]};d;
        "an example of a clocked transform must say which instant it was written for"]};

/ --- applying ---------------------------------------------------------------

.qt.qetl.transform.test_apply_returns_the_output:{[t]
    .qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];
    .qunit.assertEquals[.qetl.transform.apply[`xf_mid;enlist[`quotes]!enlist .qt.qetl.transform.two_quotes];.qt.qetl.transform.two_mids;
        "apply runs fn over the named inputs"]};

.qt.qetl.transform.test_apply_refuses_an_input_of_the_wrong_type:{[t]
    .qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];
    .qunit.assertError[{.qetl.transform.apply[`xf_mid;x]};enlist[`quotes]!enlist update bid:1 2 from .qt.qetl.transform.two_quotes;
        "a long bid where a float is declared is refused at the boundary, not deep in the arithmetic"]};

.qt.qetl.transform.test_apply_refuses_an_input_with_an_extra_column:{[t]
    .qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];
    .qunit.assertError[{.qetl.transform.apply[`xf_mid;x]};enlist[`quotes]!enlist update venue:`a`b from .qt.qetl.transform.two_quotes;
        "the inputs say exactly what a transform reads"]};

.qt.qetl.transform.test_apply_refuses_a_missing_input:{[t]
    .qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];
    .qunit.assertError[{.qetl.transform.apply[`xf_mid;x]};enlist[`other]!enlist .qt.qetl.transform.two_quotes;
        "inputs are named, so a wrongly-named one is refused rather than read positionally"]};

.qt.qetl.transform.test_apply_refuses_output_columns_out_of_order:{[t]
    .qetl.transform.define[`xf_mid;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;.qt.qetl.transform.good_example[]]];
    .qt.support.set_field[`.qetl.transform.registry;`xf_mid;`fn;{[q] select mid:(bid+ask)%2, sym from q}];
    .qunit.assertError[{.qetl.transform.apply[`xf_mid;x]};enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;
        ".u.upd publishes columns positionally, so order is part of the output schema"]};

.qt.qetl.transform.test_a_clocked_transform_must_be_applied_with_an_instant:{[t]
    d:.qt.qetl.transform.decl[{[q;at] .qt.qetl.transform.mid_fn q};enlist (first .qt.qetl.transform.good_example[]),(enlist `as_of)!enlist 2026.09.17D10:00];
    d[`as_of]:1b;
    .qetl.transform.define[`xf_clock;d];
    .qunit.assertError[{.qetl.transform.apply[`xf_clock;x]};enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;
        "a transform that needs an instant cannot silently be run without one"]};

/ --- verifying ----------------------------------------------------------------

.qt.qetl.transform.test_verify_reports_a_wrong_expected_value_with_the_value:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;update mid:1.15 1.36 from .qt.qetl.transform.two_mids);
    .qetl.transform.define[`xf_wrong;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex]];
    r:.qetl.transform.verify `xf_wrong;
    / Three separate likes: on this KDB-X build a pattern with more than one
    / `*` other than "*x*" throws 'nyi.
    d:first r`detail;
    .qunit.assertTrue[(not first r`passed) and all d like/: ("*row 1 of mid*";"*expected 1.36*";"*got 1.35*");
        "a mismatch names the row, the column, and both values"]};

.qt.qetl.transform.test_verify_catches_a_clock_in_the_output:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;([] sym:`EURUSD`GBPUSD; at:2#2026.09.17D10:00));
    d:`inputs`output`fn`examples!(enlist[`quotes]!enlist .qt.qetl.transform.quotes_schema;([] sym:`symbol$(); at:`timestamp$());{[q] select sym, at:.z.p from q};ex);
    .qetl.transform.define[`xf_now;d];
    .qunit.assertEquals[first (.qetl.transform.verify `xf_now)`passed;0b;
        "a transform stamping .z.p cannot reproduce an expected table"]};

/ The repeat check, isolated: the first call matches exactly and only the
/ second differs, which is what a hidden counter or random draw looks like.
.qt.qetl.transform.test_verify_catches_output_that_changes_between_calls:{[t]
    `.qt.qetl.transform.calls set 0;
    fn:{[q] `.qt.qetl.transform.calls set 1+.qt.qetl.transform.calls; update mid:mid+.qt.qetl.transform.calls-1 from .qt.qetl.transform.mid_fn q};
    .qetl.transform.define[`xf_drift;.qt.qetl.transform.decl[fn;.qt.qetl.transform.good_example[]]];
    r:first .qetl.transform.verify `xf_drift;
    .qunit.assertTrue[(not r`passed) and (r`detail) like "not deterministic*";
        "the same inputs must give the same table on a second call"]};

.qt.qetl.transform.test_verify_runs_the_empty_case_every_transform_gets:{[t]
    fn:{[q] if[0=count q; '"empty"]; .qt.qetl.transform.mid_fn q};
    .qetl.transform.define[`xf_noempty;.qt.qetl.transform.decl[fn;.qt.qetl.transform.good_example[]]];
    r:.qetl.transform.verify `xf_noempty;
    .qunit.assertEquals[exec passed from r where example=`empty;enlist 0b;
        "a transform that throws on a window with no rows fails verification without anyone writing that example"]};

.qt.qetl.transform.test_float_columns_compare_within_tolerance:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;update mid:mid+1e-12 from .qt.qetl.transform.two_mids);
    .qetl.transform.define[`xf_close;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex]];
    .qunit.assertEquals[all (.qetl.transform.verify `xf_close)`passed;1b;
        "a hand-typed decimal within 1e-9 of the computed float matches"]};

.qt.qetl.transform.test_float_columns_differing_beyond_tolerance_do_not_match:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;update mid:mid+1e-6 from .qt.qetl.transform.two_mids);
    .qetl.transform.define[`xf_far;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex]];
    .qunit.assertEquals[first (.qetl.transform.verify `xf_far)`passed;0b;
        "the tolerance absorbs rounding, not a real difference"]};

.qt.qetl.transform.test_require_verified_names_the_failing_example:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .qt.qetl.transform.two_quotes;update mid:0 0f from .qt.qetl.transform.two_mids);
    .qetl.transform.define[`xf_req;.qt.qetl.transform.decl[.qt.qetl.transform.mid_fn;ex]];
    err:@[.qetl.transform.require_verified;`xf_req;{x}];
    .qunit.assertTrue[err like "transform xf_req failed example 0*";"the refusal names the transform and the example"]};

/ --- passthrough ------------------------------------------------------------

.qt.qetl.transform.test_a_passthrough_returns_its_input:{[t]
    .qetl.transform.passthrough[`xf_pass;`batch;.qt.qetl.transform.quotes_schema;.qt.qetl.transform.two_quotes];
    .qunit.assertEquals[.qetl.transform.apply[`xf_pass;enlist[`batch]!enlist .qt.qetl.transform.two_quotes];.qt.qetl.transform.two_quotes;
        "a pass-through is an explicit, tested identity"]};

/ --- the posbook1 book round trip ----------------------------------------------

/ posbook1 rebuilds its book from the transform's output rather than having
/ the transform mutate a global, so the rebuild has to agree with folding
/ the same fills through .qf.port directly.
.qt.qetl.transform.test_next_book_matches_folding_the_fills_through_qpos:{[t]
    ex:first (.qetl.transform.def `position)`examples;
    i:ex`inputs;
    out:.qetl.transform.apply[`position;i];
    book:.qpipe.job.posbook.next_book[i`book;out];
    direct:0!.qf.port.apply_fills[1!i`book;i`batch];
    .qunit.assertEquals[`sym xasc book;`sym xasc direct;"the book rebuilt from positions is the book .qf.port folds to"]};
