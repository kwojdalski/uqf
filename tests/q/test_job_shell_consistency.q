/ test_job_shell_consistency.q - the two job shells and the three event-time
/ kinds take the same deployment facts the same way (#2115, #2116).
/ .
/ procname and ns: .qetl.job.bounded.define and .qetl.job.stream.define (and the
/ kinds that forward to it) default procname to `<job>1`, refuse a supplied ns
/ naming the derived namespace, and refuse a procname another declared job
/ already runs, at declaration, across both shells.
/ .
/ lateness: bars, rolling and horizon spell the bound `lateness`, default it to
/ 0D when event_time is `time, require it for a source stamp, and refuse the
/ old horizon key max_lateness naming the new one.

.qt.qetl.job.consistency.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qetl.job.consistency]}
.qt.qetl.job.consistency.afterNamespace_restore_jobs:{[] .qt.qetl.job.consistency.fresh[]}

/ Both registries as the suite found them. Every test that defines a job starts
/ here: the tests share one process, and a procname one of them claimed would
/ otherwise be refused to the next.
.qt.qetl.job.consistency.fresh:{[]
    .qt.support.restore_stream_jobs[`.qt.qetl.job.consistency];
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;.qt.qetl.job.consistency.workers];}

/ every bounded worker this suite may define
.qt.qetl.job.consistency.workers:`cs_w1`cs_w2`cs_w3`cs_w4

/ A valid bounded declaration over the shipped demo source.
.qt.qetl.job.consistency.worker_decl:{[ds] `source`outputs`width`transform!(`demo_deals;enlist ds;1D;`demo_deals_passthrough)}

/ A valid minimal streaming declaration.
.qt.qetl.job.consistency.stream_decl:{[] `subscribe_to`outputs`on_batch!(enlist `cs_in;enlist `cs_out;{[t;x] count x})}

/ --- procname --------------------------------------------------------------

.qt.qetl.job.consistency.test_procname_defaults_to_the_job_name_plus_1_in_both_shells:{[t]
    .qt.qetl.job.consistency.fresh[];
    .qetl.job.bounded.define[`cs_w1;.qt.qetl.job.consistency.worker_decl[`cs_ds1]];
    .qetl.job.stream.define[`cs_s1;.qt.qetl.job.consistency.stream_decl[]];
    .qunit.assertEquals[(.qetl.job.bounded.def `cs_w1)`procname;`cs_w11;"a bounded worker"];
    .qunit.assertEquals[(.qetl.job.stream.def `cs_s1)`procname;`cs_s11;"a streaming job"];
    .qunit.assertEquals[.qetl.job.default_procname `cs_s1;`cs_s11;"one function names the default"]};

.qt.qetl.job.consistency.test_a_procname_that_is_not_a_symbol_is_refused_in_both_shells:{[t]
    .qunit.assertThrows[.qetl.job.bounded.define[`cs_w1];@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`procname;:;"cs_p"];
        "*cs_w1's procname must be a symbol*";"bounded"];
    .qunit.assertThrows[.qetl.job.stream.define[`cs_s1];@[.qt.qetl.job.consistency.stream_decl[];`procname;:;"cs_p"];
        "*cs_s1's procname must be a symbol*";"streaming"]};

.qt.qetl.job.consistency.test_two_bounded_workers_cannot_share_a_procname:{[t]
    .qt.qetl.job.consistency.fresh[];
    .qetl.job.bounded.define[`cs_w1;@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`procname;:;`cs_shared]];
    .qunit.assertThrows[.qetl.job.bounded.define[`cs_w2];@[.qt.qetl.job.consistency.worker_decl[`cs_ds2];`procname;:;`cs_shared];
        "*define: cs_w2 claims procname cs_shared which cs_w1 already runs*";"refused at define, not at init"];
    .qunit.assertFalse[`cs_w2 in .qetl.job.bounded.defined[];"and nothing is registered"]};

.qt.qetl.job.consistency.test_a_worker_redeclared_under_its_own_name_does_not_clash_with_itself:{[t]
    .qt.qetl.job.consistency.fresh[];
    d:@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`procname;:;`cs_shared];
    .qetl.job.bounded.define[`cs_w1;d];
    .qetl.job.bounded.define[`cs_w1;d];
    .qunit.assertEquals[(.qetl.job.bounded.def `cs_w1)`procname;`cs_shared;"a re-definition replaces the row"]};

.qt.qetl.job.consistency.test_a_streaming_job_cannot_take_a_workers_procname:{[t]
    .qt.qetl.job.consistency.fresh[];
    .qetl.job.bounded.define[`cs_w1;@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`procname;:;`cs_shared]];
    .qunit.assertThrows[.qetl.job.stream.define[`cs_s1];@[.qt.qetl.job.consistency.stream_decl[];`procname;:;`cs_shared];
        "*define: cs_s1 claims procname cs_shared which cs_w1 already runs*";"stream after bounded"]};

.qt.qetl.job.consistency.test_a_worker_cannot_take_a_streaming_jobs_procname:{[t]
    .qt.qetl.job.consistency.fresh[];
    .qetl.job.stream.define[`cs_s1;@[.qt.qetl.job.consistency.stream_decl[];`procname;:;`cs_shared]];
    .qunit.assertThrows[.qetl.job.bounded.define[`cs_w1];@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`procname;:;`cs_shared];
        "*define: cs_w1 claims procname cs_shared which cs_s1 already runs*";"bounded after stream"];
    .qunit.assertThrows[.qetl.job.stream.define[`cs_s2];@[.qt.qetl.job.consistency.stream_decl[];`procname;:;`cs_shared];
        "*define: cs_s2 claims procname cs_shared which cs_s1 already runs*";"stream after stream"]};

.qt.qetl.job.consistency.test_the_default_procname_is_held_to_uniqueness_too:{[t]
    .qt.qetl.job.consistency.fresh[];
    .qetl.job.stream.define[`cs_w2;@[.qt.qetl.job.consistency.stream_decl[];`procname;:;`cs_w31]];
    .qunit.assertThrows[.qetl.job.bounded.define[`cs_w3];.qt.qetl.job.consistency.worker_decl[`cs_ds3];
        "*claims procname cs_w31 which cs_w2 already runs*";"a defaulted name clashes like a declared one"]};

/ --- ns ----------------------------------------------------------------------

.qt.qetl.job.consistency.test_a_supplied_ns_is_refused_in_both_shells_naming_the_derived_one:{[t]
    .qunit.assertThrows[.qetl.job.bounded.define[`cs_w1];@[.qt.qetl.job.consistency.worker_decl[`cs_ds1];`ns;:;`.qfoo];
        "*cs_w1's namespace is derived - .qpipe.job.cs_w1 - not configured; drop the ns key*";"bounded"];
    .qunit.assertThrows[.qetl.job.stream.define[`cs_s1];@[.qt.qetl.job.consistency.stream_decl[];`ns;:;`.qfoo];
        "*cs_s1's namespace is derived - .qpipe.job.cs_s1 - not configured; drop the ns key*";"streaming"];
    .qunit.assertFalse[`cs_s1 in .qetl.job.stream.defined[];"nothing is registered"]};

.qt.qetl.job.consistency.test_ns_is_not_a_required_declaration_of_a_streaming_job:{[t]
    .qunit.assertFalse[`ns in .qetl.job.stream.required_declarations;"required and refused at once is the contradiction"];
    .qunit.assertFalse[`procname in .qetl.job.stream.required_declarations;"procname is defaulted, as a worker's is"]};

/ --- lateness ----------------------------------------------------------------

/ kind -> its define, the registered declaration of a shipped job of that kind, and its keys
.qt.qetl.job.consistency.kinds:{[]
    ([kind:`bars`rolling`horizons]
        shipped:`exec_bars`order_quality`fx_markout;
        define:(.qetl.job.stream.at_bars;.qetl.job.stream.at_rolling;.qetl.job.stream.at_horizons);
        def:(.qetl.job.stream.bars.def;.qetl.job.stream.rolling.def;.qetl.job.stream.horizons.def);
        required:(.qetl.job.stream.bars.required_keys;.qetl.job.stream.rolling.required_keys;.qetl.job.stream.horizons.required_keys);
        optional:(.qetl.job.stream.bars.optional_keys;.qetl.job.stream.rolling.optional_keys;.qetl.job.stream.horizons.optional_keys))}

/ A shipped job's declaration as its kind accepts it (the registry also holds the defaults define filled in).
.qt.qetl.job.consistency.clone:{[kind;nm]
    k:.qt.qetl.job.consistency.kinds[] kind;
    / Only keys the shipped job declares: `#` on an absent key yields a null,
    / which an optional key's check then refuses (`q`, #2747).
    s:(k`def) k`shipped;
    d:(key[s] inter (k`required),(k`optional) except `on_start`check`on_fail)#s;
    @[d;`procname;:;`$string[nm],"1"]}

.qt.qetl.job.consistency.test_every_event_time_kind_spells_the_bound_lateness:{[t]
    req:exec required from .qt.qetl.job.consistency.kinds[];
    opt:exec optional from .qt.qetl.job.consistency.kinds[];
    .qunit.assertTrue[all `lateness in/: req;"each requires it (the default fills it in for `time)"];
    .qunit.assertFalse[any `max_lateness in/: req,'opt;"and none still accepts the old horizon spelling"]};

.qt.qetl.job.consistency.test_a_receipt_time_event_time_defaults_lateness_to_zero:{[t]
    w:"x";
    .qunit.assertEquals[(.qetl.job.stream.with_lateness[w;enlist[`event_time]!enlist `time])`lateness;0D;"explicit `time"];
    .qunit.assertEquals[(.qetl.job.stream.with_lateness[w;enlist[`by]!enlist `sym])`lateness;0D;"event_time defaults to `time"];
    .qunit.assertFalse[`lateness in key .qetl.job.stream.with_lateness[w;enlist[`event_time]!enlist `source_time];
        "a source stamp gets no default: the kind's required-key check refuses it"];
    .qunit.assertEquals[(.qetl.job.stream.with_lateness[w;`event_time`lateness!(`time;0D00:00:05)])`lateness;0D00:00:05;"a declared bound is kept"]};

.qt.qetl.job.consistency.test_every_kind_requires_lateness_for_a_source_stamp:{[t]
    {[kind]
        d:`lateness _ @[.qt.qetl.job.consistency.clone[kind;`cs_l1];`event_time;:;`source_time];
        .qunit.assertThrows[((.qt.qetl.job.consistency.kinds[] kind)`define)[`cs_l1;];d;
            "*job cs_l1 is missing lateness*";string[kind]," with a source-stamp event_time"]} each `bars`rolling`horizons}

.qt.qetl.job.consistency.test_every_kind_defaults_lateness_for_receipt_time:{[t]
    .qt.qetl.job.consistency.fresh[];
    {[kind]
        d:`lateness _ @[.qt.qetl.job.consistency.clone[kind;`cs_l2];`event_time;:;`time];
        r:@[((.qt.qetl.job.consistency.kinds[] kind)`define)[`cs_l2;];d;{[e] e}];
        msg:$[10h=type r; r; ""];
        .qunit.assertFalse[msg like "*missing lateness*";string[kind]," does not demand what its default supplies"]} each `bars`rolling`horizons}

.qt.qetl.job.consistency.test_every_kind_refuses_the_old_horizon_spelling_naming_the_new_one:{[t]
    {[kind]
        d:@[.qt.qetl.job.consistency.clone[kind;`cs_l3];`max_lateness;:;0D00:00:30];
        .qunit.assertThrows[((.qt.qetl.job.consistency.kinds[] kind)`define)[`cs_l3;];d;
            "*cs_l3 declares max_lateness, which is now lateness*";string[kind]]} each `bars`rolling`horizons}

.qt.qetl.job.consistency.test_every_kind_refuses_a_negative_lateness:{[t]
    {[kind]
        d:@[.qt.qetl.job.consistency.clone[kind;`cs_l4];`lateness;:;-0D00:00:01];
        .qunit.assertThrows[((.qt.qetl.job.consistency.kinds[] kind)`define)[`cs_l4;];d;
            "*lateness must be a timespan*";string[kind]]} each `bars`rolling`horizons}

/ A kind's procname is defaulted by the shell it forwards to.
.qt.qetl.job.consistency.test_a_kind_without_a_procname_runs_as_the_job_name_plus_1:{[t]
    .qt.qetl.job.consistency.fresh[];
    d:`procname`on_start`check`on_fail _ .qt.qetl.job.consistency.clone[`bars;`cs_b1];
    .qetl.job.stream.at_bars[`cs_b1;d];
    .qunit.assertEquals[(.qetl.job.stream.def `cs_b1)`procname;`cs_b11;"bars"]}
