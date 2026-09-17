// test_transform.q - tests for src/etl/core/transform.q (.qxf) and every
// transform registered with it, including the streaming jobs under src/etl/streaming/.
//
// The first test is the one that matters: it runs every registered
// transform's hand-written examples, so a job whose transform no longer
// produces its expected table fails the build. The rest pin the block's own
// refusals, each of which exists because the failure it prevents is silent.
//
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .xftest

quotes_schema:([] sym:`symbol$(); bid:`float$(); ask:`float$())
mid_schema:([] sym:`symbol$(); mid:`float$())
two_quotes:([] sym:`EURUSD`GBPUSD; bid:1.1 1.3; ask:1.2 1.4)
two_mids:([] sym:`EURUSD`GBPUSD; mid:1.15 1.35)

decl:{[fn;examples]
    `inputs`output`fn`examples!(enlist[`quotes]!enlist .xftest.quotes_schema;.xftest.mid_schema;fn;examples)}

mid_fn:{[q] select sym, mid:(bid+ask)%2 from q}

good_example:{[] enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;.xftest.two_mids)}

/ Test transforms are removed after each test, so the "every registered
/ transform passes" test sees only the shipped ones.
shipped:`symbol$()

beforeNamespace_remember:{[] `.xftest.shipped set key .qxf.registry;}

tearDown_forget:{[] .qxf.registry:(.xftest.shipped) # .qxf.registry;}

/ --- every registered transform -------------------------------------------

test_every_registered_transform_passes_its_examples:{[t]
    r:select from .qxf.verify_all[] where not passed;
    .qunit.assertEquals[count r;0;
        "every transform reproduces its expected tables, deterministically, and handles empty input: ",.Q.s1 r]};

test_the_stream_jobs_and_backfill_workers_all_declare_a_transform:{[t]
    want:`execution_quality`cross_quotes`position`mkt_orderbook`demo_deals_passthrough`demo_events_passthrough;
    .qunit.assertEquals[all want in key .qxf.registry;1b;"each shipped job's transform is registered"]};

/ A stream transform's output is published positionally by .u.upd, so its
/ columns must be the tickerplant table's, in order, minus the `time` .u.upd
/ stamps. Checked against scripts/uqf_stack_tables.q itself, read in a
/ scratch namespace so its root-level tables do not leak into other suites.
test_stream_outputs_match_the_tickerplant_tables:{[t]
    stack:.xftest.stack_tables[];
    bad:{[stack;nm]
        want:delete time from stack nm;
        got:.qxf.output_schema nm;
        $[(cols want)~cols got; (); enlist nm]
      }[stack] each `execution_quality`position`mkt_orderbook;
    .qunit.assertEquals[count raze bad;0;"each published transform output matches its table, column for column: ",.Q.s1 raze bad]};

stack_tables:{[]
    ls:read0 `$":scripts/uqf_stack_tables.q";
    nms:`execution_quality`position`mkt_orderbook;
    nms!{[ls;nm] line:first ls where ls like string[nm],":*"; value (1+line?":") _ line}[ls] each nms}

/ --- declaring --------------------------------------------------------------

test_a_well_formed_transform_registers:{[t]
    .qunit.assertEquals[.qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];`xf_mid;
        "a declaration with inputs, output, fn and a non-empty example registers"]};

test_a_transform_without_examples_is_refused:{[t]
    .qunit.assertError[{.qxf.define[`xf_noex;x]};.xftest.decl[.xftest.mid_fn;()];
        "a transform with no expected output asserts nothing"]};

test_a_transform_whose_examples_are_all_empty_is_refused:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.quotes_schema;.xftest.mid_schema);
    .qunit.assertError[{.qxf.define[`xf_empty;x]};.xftest.decl[.xftest.mid_fn;ex];
        "an example with no rows proves only the empty case, which verify runs anyway"]};

test_a_keyed_output_is_refused:{[t]
    d:.xftest.decl[.xftest.mid_fn;.xftest.good_example[]];
    d[`output]:1!.xftest.mid_schema;
    .qunit.assertError[{.qxf.define[`xf_keyed;x]};d;"a keyed table cannot be published to a tickerplant"]};

test_an_fn_of_the_wrong_arity_is_refused:{[t]
    .qunit.assertError[{.qxf.define[`xf_arity;x]};.xftest.decl[{[a;b] a};.xftest.good_example[]];
        "fn takes one argument per declared input"]};

test_an_example_of_the_wrong_shape_is_refused_at_declaration:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;([] sym:`EURUSD`GBPUSD; mid:1 2));
    .qunit.assertError[{.qxf.define[`xf_badex;x]};.xftest.decl[.xftest.mid_fn;ex];
        "an expected table of the wrong type is a broken example, caught before anything runs"]};

test_an_as_of_transform_needs_an_instant_in_every_example:{[t]
    d:.xftest.decl[{[q;at] .xftest.mid_fn q};.xftest.good_example[]];
    d[`as_of]:1b;
    .qunit.assertError[{.qxf.define[`xf_clock;x]};d;
        "an example of a clocked transform must say which instant it was written for"]};

/ --- applying ---------------------------------------------------------------

test_apply_returns_the_output:{[t]
    .qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];
    .qunit.assertEquals[.qxf.apply[`xf_mid;enlist[`quotes]!enlist .xftest.two_quotes];.xftest.two_mids;
        "apply runs fn over the named inputs"]};

test_apply_refuses_an_input_of_the_wrong_type:{[t]
    .qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];
    .qunit.assertError[{.qxf.apply[`xf_mid;x]};enlist[`quotes]!enlist update bid:1 2 from .xftest.two_quotes;
        "a long bid where a float is declared is refused at the boundary, not deep in the arithmetic"]};

test_apply_refuses_an_input_with_an_extra_column:{[t]
    .qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];
    .qunit.assertError[{.qxf.apply[`xf_mid;x]};enlist[`quotes]!enlist update venue:`a`b from .xftest.two_quotes;
        "the inputs say exactly what a transform reads"]};

test_apply_refuses_a_missing_input:{[t]
    .qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];
    .qunit.assertError[{.qxf.apply[`xf_mid;x]};enlist[`other]!enlist .xftest.two_quotes;
        "inputs are named, so a wrongly-named one is refused rather than read positionally"]};

test_apply_refuses_output_columns_out_of_order:{[t]
    .qxf.define[`xf_mid;.xftest.decl[.xftest.mid_fn;.xftest.good_example[]]];
    .qxf.registry[`xf_mid;`fn]:{[q] select mid:(bid+ask)%2, sym from q};
    .qunit.assertError[{.qxf.apply[`xf_mid;x]};enlist[`quotes]!enlist .xftest.two_quotes;
        ".u.upd publishes columns positionally, so order is part of the output schema"]};

test_a_clocked_transform_must_be_applied_with_an_instant:{[t]
    d:.xftest.decl[{[q;at] .xftest.mid_fn q};enlist (first .xftest.good_example[]),(enlist `as_of)!enlist 2026.09.17D10:00];
    d[`as_of]:1b;
    .qxf.define[`xf_clock;d];
    .qunit.assertError[{.qxf.apply[`xf_clock;x]};enlist[`quotes]!enlist .xftest.two_quotes;
        "a transform that needs an instant cannot silently be run without one"]};

/ --- verifying ----------------------------------------------------------------

test_verify_reports_a_wrong_expected_value_with_the_value:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;update mid:1.15 1.36 from .xftest.two_mids);
    .qxf.define[`xf_wrong;.xftest.decl[.xftest.mid_fn;ex]];
    r:.qxf.verify `xf_wrong;
    / Three separate likes: on this KDB-X build a pattern with more than one
    / `*` other than "*x*" throws 'nyi.
    d:first r`detail;
    .qunit.assertTrue[(not first r`passed) and all d like/: ("*row 1 of mid*";"*expected 1.36*";"*got 1.35*");
        "a mismatch names the row, the column, and both values"]};

test_verify_catches_a_clock_in_the_output:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;([] sym:`EURUSD`GBPUSD; at:2#2026.09.17D10:00));
    d:`inputs`output`fn`examples!(enlist[`quotes]!enlist .xftest.quotes_schema;([] sym:`symbol$(); at:`timestamp$());{[q] select sym, at:.z.p from q};ex);
    .qxf.define[`xf_now;d];
    .qunit.assertEquals[first (.qxf.verify `xf_now)`passed;0b;
        "a transform stamping .z.p cannot reproduce an expected table"]};

/ The repeat check, isolated: the first call matches exactly and only the
/ second differs, which is what a hidden counter or random draw looks like.
test_verify_catches_output_that_changes_between_calls:{[t]
    `.xftest.calls set 0;
    fn:{[q] `.xftest.calls set 1+.xftest.calls; update mid:mid+.xftest.calls-1 from .xftest.mid_fn q};
    .qxf.define[`xf_drift;.xftest.decl[fn;.xftest.good_example[]]];
    r:first .qxf.verify `xf_drift;
    .qunit.assertTrue[(not r`passed) and (r`detail) like "not deterministic*";
        "the same inputs must give the same table on a second call"]};

test_verify_runs_the_empty_case_every_transform_gets:{[t]
    fn:{[q] if[0=count q; '"empty"]; .xftest.mid_fn q};
    .qxf.define[`xf_noempty;.xftest.decl[fn;.xftest.good_example[]]];
    r:.qxf.verify `xf_noempty;
    .qunit.assertEquals[exec passed from r where example=`empty;enlist 0b;
        "a transform that throws on a window with no rows fails verification without anyone writing that example"]};

test_float_columns_compare_within_tolerance:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;update mid:mid+1e-12 from .xftest.two_mids);
    .qxf.define[`xf_close;.xftest.decl[.xftest.mid_fn;ex]];
    .qunit.assertEquals[all (.qxf.verify `xf_close)`passed;1b;
        "a hand-typed decimal within 1e-9 of the computed float matches"]};

test_float_columns_differing_beyond_tolerance_do_not_match:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;update mid:mid+1e-6 from .xftest.two_mids);
    .qxf.define[`xf_far;.xftest.decl[.xftest.mid_fn;ex]];
    .qunit.assertEquals[first (.qxf.verify `xf_far)`passed;0b;
        "the tolerance absorbs rounding, not a real difference"]};

test_require_verified_names_the_failing_example:{[t]
    ex:enlist `inputs`expected!(enlist[`quotes]!enlist .xftest.two_quotes;update mid:0 0f from .xftest.two_mids);
    .qxf.define[`xf_req;.xftest.decl[.xftest.mid_fn;ex]];
    err:@[.qxf.require_verified;`xf_req;{x}];
    .qunit.assertTrue[err like "transform xf_req failed example 0*";"the refusal names the transform and the example"]};

/ --- passthrough ------------------------------------------------------------

test_a_passthrough_returns_its_input:{[t]
    .qxf.passthrough[`xf_pass;`batch;.xftest.quotes_schema;.xftest.two_quotes];
    .qunit.assertEquals[.qxf.apply[`xf_pass;enlist[`batch]!enlist .xftest.two_quotes];.xftest.two_quotes;
        "a pass-through is an explicit, tested identity"]};

/ --- the posbook1 book round trip ----------------------------------------------

/ posbook1 rebuilds its book from the transform's output rather than having
/ the transform mutate a global, so the rebuild has to agree with folding
/ the same fills through .qpos directly.
test_next_book_matches_folding_the_fills_through_qpos:{[t]
    ex:first .qxf.registry[`position;`examples];
    i:ex`inputs;
    out:.qxf.apply[`position;i];
    book:.qsub.posbook.next_book[i`book;out];
    direct:0!.qpos.apply_fills[1!i`book;i`trades];
    .qunit.assertEquals[`sym xasc book;`sym xasc direct;"the book rebuilt from positions is the book .qpos folds to"]};

\d .
