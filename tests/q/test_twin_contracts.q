/ test_twin_contracts.q - a backfill that declares itself a twin of a live job is
/ held to it whenever both declarations load (.qetl.twin, #2103).
/ .
/ `uqs job new --kind backfill --twin-of JOB` read the live job's output ONCE, when
/ it wrote the files. These cases are the later half: the live job changes, and the
/ declared twin is refused at load - with no CLI in the way, and even when the two
/ tables have identical columns, which is what hid the drift.
/ .
/ Everything here is a throwaway pair (twin_live, twin_backfill) over plant tables
/ made for the suite, so no real job is touched.

.qt.qetl.twin.beforeNamespace_define:{[]
    .qt.support.save_stream_jobs[`.qt.qetl.twin];
    / Two tables with IDENTICAL columns: the drift a schema check cannot see. A third with another shape.
    .qetl.plant.twin_marks:([] time:`timestamp$(); px:`float$());
    .qetl.plant.twin_marks_v2:([] time:`timestamp$(); px:`float$());
    .qetl.plant.twin_wide:([] time:`timestamp$(); px:`float$(); qty:`long$());
    .qetl.source.define[`twin_src;
        `source`table_name`time_column`row_key`columns`types`query`fixture`tz!
        (`twin_src;`twin_events;`time;`time;`time`px;"pf";
         {[h;range_from;range_to] ([] time:enlist range_from; px:enlist 1f)};
         {[] ([] time:enlist 2026.09.01D00:00; px:enlist 0f)};
         `UTC)];
    in_:enlist[`batch]!enlist ([] time:`timestamp$(); px:`float$());
    out:([] time:`timestamp$(); px:`float$());
    ex:{[i;o] enlist `inputs`expected!(enlist[`batch]!enlist i;o)};
    one:([] time:enlist 2026.09.01D00:00; px:enlist 2f);
    .qetl.transform.define[`twin_pass;`inputs`output`fn`examples!(in_;out;{[batch] batch};ex[one;one])];
    .qetl.transform.define[`twin_other;`inputs`output`fn`examples!(in_;out;{[batch] update px:px*1f from batch};ex[one;one])];
    .qetl.transform.define[`twin_long;`inputs`output`fn`examples!(in_;([] time:`timestamp$(); px:`long$());
        {[batch] select time, px:`long$px from batch};ex[one;([] time:enlist 2026.09.01D00:00; px:enlist 2)])];
    }

.qt.qetl.twin.afterNamespace_restore:{[]
    .qt.support.restore_stream_jobs[`.qt.qetl.twin];
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;`twin_backfill`twin_backfill_b];
    ![`.qetl.plant;();0b;`twin_marks`twin_marks_v2`twin_wide];
    }

/ Each test starts with neither the live job nor a twin.
.qt.qetl.twin.setUp_clear:{[]
    .qt.support.restore_stream_jobs[`.qt.qetl.twin];
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;`twin_backfill`twin_backfill_b];
    }

/ The live job, publishing `outs` and applying `t` (` for none).
.qt.qetl.twin.live:{[outs;t]
    d:`procname`subscribe_to`outputs`period`on_timer!(`twinlive1;`symbol$();outs;0D00:00:01;{[] ::});
    .qetl.job.stream.define[`twin_live;$[null t; d; d,enlist[`transform]!enlist t]]}

/ The backfill: it fills `ds`, applies `t`, and also declares `extra`.
.qt.qetl.twin.worker:{[ds;t;extra]
    .qetl.job.bounded.define[`twin_backfill;(`source`outputs`width`transform!(`twin_src;enlist ds;1D;t)),extra]}

/ The report's rows for the pair under test: the tree's own twins are in it too.
.qt.qetl.twin.mine:{[] select from .qetl.twin.report[] where worker=`twin_backfill}

.qt.qetl.twin.twin_of:{[] enlist[`twin_of]!enlist `twin_live}

.qt.qetl.twin.shared:{[] `twin_of`twin_calculation!(`twin_live;`shared)}

/ --- a declared twin, in either load order -----------------------------------

.qt.qetl.twin.test_a_declared_twin_is_recorded_with_its_live_job:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;.qt.qetl.twin.shared[]];
    c:.qetl.job.bounded.def `twin_backfill;
    .qunit.assertEquals[(c`twin_of;c`twin_calculation);(`twin_live;`shared);"the declaration is kept on the worker"];
    r:.qt.qetl.twin.mine[];
    .qunit.assertEquals[exec worker from r;enlist `twin_backfill;"the report lists the declared twin"];
    .qunit.assertEquals[(first r`live;first r`output;first r`calculation);(`twin_live;`twin_marks;`shared);"with its live job, output and calculation"]};

.qt.qetl.twin.test_the_order_the_two_declarations_load_in_does_not_matter:{[t]
    / The tree loads workers first; a test, or a bundle, may load either.
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;.qt.qetl.twin.shared[]];
    .qunit.assertEquals[first (.qt.qetl.twin.mine[])`calculation;`unchecked;"until the live job loads the twin cannot be judged"];
    .qunit.assertEquals[.qetl.twin.unresolved[];enlist `twin_backfill;"and it is listed as waiting for it"];
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    .qunit.assertEquals[.qetl.twin.unresolved[];`symbol$();"the live job resolves it"];
    .qunit.assertEquals[first (.qt.qetl.twin.mine[])`calculation;`shared;"and the pair is shared"]};

/ --- drift: the live destination moves ---------------------------------------

.qt.qetl.twin.test_a_live_destination_change_is_refused_when_the_live_job_loads:{[t]
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;.qt.qetl.twin.twin_of[]];
    e:.qunit.assertThrows[.qt.qetl.twin.live[;`twin_pass];enlist `twin_marks_v2;"twin: twin_backfill is declared a twin of twin_live but it writes twin_marks, which twin_live does not list among its outputs (twin_marks_v2)*";
        "the live job now publishes twin_marks_v2, and the backfill still writes twin_marks"];
    .qunit.assertTrue[e like "*even when another table has the same columns*";"the message says identical columns do not excuse it"];
    .qunit.assertFalse[`twin_live in key .qetl.job.stream.jobs;"the refused declaration is not left registered"]};

.qt.qetl.twin.test_a_backfill_that_writes_a_table_the_live_job_does_not_publish_is_refused:{[t]
    / The other load order: the live job is there first, so the worker's own define refuses.
    .qt.qetl.twin.live[enlist `twin_marks_v2;`twin_pass];
    .qunit.assertThrows[.qt.qetl.twin.worker[`twin_marks;`twin_pass;];.qt.qetl.twin.twin_of[];"twin: twin_backfill is declared a twin of twin_live but it writes twin_marks, which twin_live does not list among its outputs (twin_marks_v2)*";
        "identical columns, different table: refused"];
    .qunit.assertFalse[`twin_backfill in .qetl.job.bounded.defined[];"and the worker is not registered"]};

.qt.qetl.twin.test_following_the_live_job_to_its_new_table_is_accepted:{[t]
    .qt.qetl.twin.live[enlist `twin_marks_v2;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks_v2;`twin_pass;.qt.qetl.twin.twin_of[]];
    .qunit.assertEquals[(.qetl.job.bounded.def `twin_backfill)`dataset;`twin_marks_v2;"updating the twin's outputs repairs the pair"]};

/ --- the dataset contract ----------------------------------------------------

.qt.qetl.twin.test_a_twin_whose_transform_output_breaks_the_dataset_contract_is_refused:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`];
    e:.qunit.assertThrows[.qt.qetl.twin.worker[`twin_marks;`twin_long;];.qt.qetl.twin.twin_of[];"twin: twin_backfill is declared a twin of twin_live but *";
        "px as a long where the table keeps a float"];
    .qunit.assertTrue[e like "*twin_marks.px is typed*";"the column and the plant's type are named: ",e]};

.qt.qetl.twin.test_a_twin_whose_columns_differ_from_the_table_is_refused:{[t]
    / The twin's transform builds (time; px); the live table also has qty.
    .qt.qetl.twin.live[enlist `twin_wide;`];
    e:.qunit.assertThrows[.qt.qetl.twin.worker[`twin_wide;`twin_pass;];.qt.qetl.twin.twin_of[];"twin: twin_backfill is declared a twin of twin_live but *";
        "a missing column"];
    .qunit.assertTrue[e like "*twin_wide has columns px - the plant takes px qty*";"the columns are named: ",e]};

.qt.qetl.twin.test_a_twin_of_a_table_the_plant_does_not_carry_is_refused:{[t]
    .qt.qetl.twin.live[enlist `twin_nowhere;`];
    e:.qunit.assertThrows[.qt.qetl.twin.worker[`twin_nowhere;`twin_pass;];.qt.qetl.twin.twin_of[];"twin: *";
        "no plant table, no contract to hold the transform to"];
    .qunit.assertTrue[e like "*twin_nowhere is not a plant table*";"named: ",e]};

/ --- the calculation ---------------------------------------------------------

.qt.qetl.twin.test_a_promised_shared_transform_that_differs_is_refused:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    e:.qunit.assertThrows[.qt.qetl.twin.worker[`twin_marks;`twin_other;];.qt.qetl.twin.shared[];"twin: *";
        "the promise names one calculation and the twin applies another"];
    .qunit.assertTrue[e like "*promises a shared calculation (twin_calculation `shared) but applies twin_other where twin_live applies twin_pass*";"both transforms are named: ",e]};

.qt.qetl.twin.test_a_promised_shared_transform_needs_the_live_job_to_declare_one:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`];
    e:.qunit.assertThrows[.qt.qetl.twin.worker[`twin_marks;`twin_pass;];.qt.qetl.twin.shared[];"twin: *";
        "nothing to share"];
    .qunit.assertTrue[e like "*twin_live declares no transform*";"named: ",e]};

.qt.qetl.twin.test_a_pair_without_a_shared_transform_is_allowed_but_not_established:{[t]
    / The usual case: a twin reads the HDB through its own transform.
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks;`twin_other;.qt.qetl.twin.twin_of[]];
    r:.qt.qetl.twin.mine[];
    .qunit.assertEquals[first r`calculation;`not_established;"different transforms: equivalence is not claimed"];
    .qunit.assertEquals[first r`note;"calculation equivalence not established";"and the report says so in words"];
    .qunit.assertEquals[(first r`twin_transform;first r`live_transform);`twin_other`twin_pass;"showing both transforms"]};

.qt.qetl.twin.test_a_live_job_with_no_transform_is_not_established_either:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`];
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;.qt.qetl.twin.twin_of[]];
    .qunit.assertEquals[first (.qt.qetl.twin.mine[])`calculation;`not_established;"matching columns are not matching arithmetic"]};

.qt.qetl.twin.test_the_same_transform_is_shared_even_when_not_promised:{[t]
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;.qt.qetl.twin.twin_of[]];
    .qunit.assertEquals[first (.qt.qetl.twin.mine[])`calculation;`shared;"one registered transform is one calculation"]};

/ --- several outputs ---------------------------------------------------------

.qt.qetl.twin.test_a_twin_of_a_multi_output_job_names_the_output_it_repairs:{[t]
    .qt.qetl.twin.live[`twin_marks`twin_marks_v2;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks_v2;`twin_pass;.qt.qetl.twin.twin_of[]];
    .qunit.assertEquals[first (.qt.qetl.twin.mine[])`output;`twin_marks_v2;"the second of two outputs"];
    .qunit.assertThrows[.qt.qetl.twin.worker[`twin_wide;`twin_pass;];.qt.qetl.twin.twin_of[];"*does not list among its outputs (twin_marks, twin_marks_v2)*";
        "a table that is neither is refused, naming both"]};

.qt.qetl.twin.test_dropping_one_of_two_outputs_refuses_the_twin_that_repaired_it:{[t]
    .qt.qetl.twin.live[`twin_marks`twin_marks_v2;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks_v2;`twin_pass;.qt.qetl.twin.twin_of[]];
    / the process restarts: the worker loads first, then the edited live job
    .qt.support.restore_stream_jobs[`.qt.qetl.twin];
    .qunit.assertThrows[.qt.qetl.twin.live[;`twin_pass];enlist `twin_marks;"*writes twin_marks_v2, which twin_live does not list among its outputs (twin_marks)*";
        "the live job stops publishing what the twin repairs"]};

/ --- only a declared twin is a twin ------------------------------------------

.qt.qetl.twin.test_sharing_a_dataset_does_not_make_a_twin:{[t]
    / A worker that writes a live job's table without declaring twin_of is another producer,
    / not a twin: nothing here checks it, whatever table or transform it uses.
    .qt.qetl.twin.live[enlist `twin_marks;`twin_pass];
    .qt.qetl.twin.worker[`twin_marks;`twin_long;()!()];
    .qunit.assertEquals[count .qt.qetl.twin.mine[];0;"no declared twin, so no record"];
    .qunit.assertEquals[.qetl.twin.unresolved[];`symbol$();"and nothing waiting"]};

/ --- the declaration itself --------------------------------------------------

.qt.qetl.twin.test_a_stored_declaration_without_a_twin_defines_another_worker:{[t]
    / worker_cfg keeps "no twin" as a null symbol, and tests and tools define a worker from a stored row.
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;()!()];
    d:(`ns`procname`note) _ .qt.support.declaration `twin_backfill;
    .qunit.assertEquals[(d`twin_of;d`twin_calculation);(`;`);"the stored row says no twin with nulls"];
    .qetl.job.bounded.define[`twin_backfill_b;@[d;`outputs;:;enlist `twin_marks_v2]];
    .qunit.assertEquals[(.qetl.job.bounded.def `twin_backfill_b)`twin_of;`;"and it is not read as a twin of nothing"]};

.qt.qetl.twin.test_a_malformed_twin_declaration_is_refused_by_name:{[t]
    w:.qt.qetl.twin.worker[`twin_marks;`twin_pass;];
    .qunit.assertThrows[w;enlist[`twin_of]!enlist "twin_live";"define: twin_backfill's twin_of must be a symbol*";"a string"];
    .qunit.assertThrows[w;enlist[`twin_of]!enlist `twin_backfill;"define: twin_backfill's twin_of names itself*";"itself"];
    .qunit.assertThrows[w;enlist[`twin_calculation]!enlist `shared;"define: twin_backfill declares twin_calculation without twin_of*";"a promise about nothing"];
    .qunit.assertThrows[w;`twin_of`twin_calculation!(`twin_live;`same);"define: twin_backfill's twin_calculation must be one of shared*";"a promise q cannot hold"]};

.qt.qetl.twin.test_a_twin_of_a_bounded_worker_is_refused:{[t]
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;()!()];
    e:.qunit.assertThrows[{.qetl.job.bounded.define[`twin_backfill_b;`source`outputs`width`transform`twin_of!(`twin_src;enlist `twin_marks_v2;1D;`twin_pass;`twin_backfill)]};::;"twin: *";"a worker is not a live job"];
    .qunit.assertTrue[e like "*twin_backfill, which is a bounded worker*";"named: ",e]};

.qt.qetl.twin.test_a_twin_naming_no_loaded_job_is_refused_by_a_full_load:{[t]
    .qt.qetl.twin.worker[`twin_marks;`twin_pass;enlist[`twin_of]!enlist `twin_lvie];
    .qunit.assertThrows[{.qetl.twin.require_resolved[]};::;"twin: twin_backfill declares twin_of twin_lvie which is no loaded streaming job*";
        "a misspelt twin_of would otherwise never be checked"]};

/ --- the tree's own twins ----------------------------------------------------

.qt.qetl.twin.test_every_pair_in_the_tree_is_declared_and_resolves:{[t]
    r:.qetl.twin.report[];
    .qunit.assertTrue[(count r)>=9;"the tree declares its twins"];
    .qunit.assertEquals[.qetl.twin.unresolved[];`symbol$();"every declared twin names a loaded job"];
    .qunit.assertEquals[exec worker from r where calculation=`unchecked;`symbol$();"and every live job is loaded"];
    .qunit.assertTrue[`hdb_fx_markouts_backfill in r`worker;"the markout backfills among them"];
    .qunit.assertEquals[exec live from r where worker=`hdb_fx_markouts_backfill;enlist `fx_markout;"each naming its live job"];
    .qunit.assertEquals[exec calculation from r where worker=`demo_deals_backfill;enlist `shared;"the pair that really shares a transform says so"]};

.qt.qetl.twin.test_a_worker_that_only_shares_a_dataset_is_not_a_declared_twin:{[t]
    / eq_fills_backfill writes the executions the normalizer publishes, from a csv drop: another producer.
    .qunit.assertTrue[`eq_fills_backfill in .qetl.uptime.twins `executions;"it does write the job's table"];
    .qunit.assertFalse[`eq_fills_backfill in (.qetl.twin.report[])`worker;"and is not a declared twin"]};
