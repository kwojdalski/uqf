/ test_horizon.q - .qetl.job.stream.horizons: a job that evaluates each event
/ once its horizon has passed (#945).
/ .
/ A small transform of its own - each event priced at the latest reference
/ mid at or before event time + 1s - so the shell is tested apart from the
/ markouts that use it (test_stream_job.q, test_crypto_markout.q).


/ The jobs this suite defines are process-global; leave the registry as found (#1375).
.qt.qetl.job.stream.horizons.beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.qt.qetl.job.stream.horizons]}
.qt.qetl.job.stream.horizons.afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.qt.qetl.job.stream.horizons]}

.qt.qetl.job.stream.horizons.t0:2026.10.09D10:00:00

.qt.qetl.job.stream.horizons.events:([] time:`timestamp$(); sym:`symbol$(); px:`float$())
.qt.qetl.job.stream.horizons.refs:([] time:`timestamp$(); sym:`symbol$(); mid:`float$())
.qt.qetl.job.stream.horizons.priced:([] sym:`symbol$(); event_time:`timestamp$(); px:`float$(); ref:`float$())

/ the as-of mid at time+1s, per event
.qt.qetl.job.stream.horizons.price:{[e;r]
    q:select sym, time:time+0D00:00:01, event_time:time, px from e;
    j:aj[`sym`time;q;`sym`time xasc r];
    select sym, event_time, px, ref:mid from j}

.qt.qetl.job.stream.horizons.define_transform:{[]
    if[`horizontest_px in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_px;`inputs`output`fn`examples!(
        `events`refs!(.qt.qetl.job.stream.horizons.events;.qt.qetl.job.stream.horizons.refs);
        .qt.qetl.job.stream.horizons.priced;
        .qt.qetl.job.stream.horizons.price;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .qt.qetl.job.stream.horizons.t0; sym:enlist `a; px:enlist 1f);
                ([] time:.qt.qetl.job.stream.horizons.t0+0D00:00:00.5 0D00:00:05; sym:`a`a; mid:1.5 9f));
            ([] sym:enlist `a; event_time:enlist .qt.qetl.job.stream.horizons.t0; px:enlist 1f; ref:enlist 1.5)))];
    }

.qt.qetl.job.stream.horizons.decl:{[] `procname`events`reference`transform`publishes`horizon`period`ephemeral!(
    `hzt1;`hz_events;`hz_refs;`horizontest_px;`hz_priced;0D00:00:01;0D00:00:01;"a test fixture")}

/ one process per job, as .qetl.job.stream.define insists
.qt.qetl.job.stream.horizons.decl_for:{[nm] @[.qt.qetl.job.stream.horizons.decl[];`procname;:;`$string[nm],"1"]}

.qt.qetl.job.stream.horizons.sent:()

/ A horizon job named nm, its publish wired to a recorder.
.qt.qetl.job.stream.horizons.job:{[nm;extra]
    .qt.qetl.job.stream.horizons.define_transform[];
    .qetl.job.stream.at_horizons[nm;.qt.qetl.job.stream.horizons.decl_for[nm],extra];
    `.qt.qetl.job.stream.horizons.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.horizons.sent,:enlist (t;x); count x}];
    nm}

.qt.qetl.job.stream.horizons.ns:{[nm;k] get ` sv (.qetl.job.stream.namespace nm),k}

.qt.qetl.job.stream.horizons.ev:{[s;tm] ([] time:enlist tm; sym:enlist s; px:enlist 1f)}
.qt.qetl.job.stream.horizons.rf:{[s;tms;mids] ([] time:tms; sym:(count tms)#s; mid:mids)}

/ --- declaring -----------------------------------------------------------

.qt.qetl.job.stream.horizons.test_a_declaration_missing_a_key_is_refused_by_name:{[t]
    .qt.qetl.job.stream.horizons.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_a;];`horizon _ .qt.qetl.job.stream.horizons.decl[];
        "*horizon job hz_a is missing horizon*";"the key it lacks"]};

.qt.qetl.job.stream.horizons.test_a_one_input_transform_is_refused:{[t]
    .qt.qetl.job.stream.horizons.define_transform[];
    if[not `horizontest_one in .qetl.transform.defined[];
        .qetl.transform.define[`horizontest_one;`inputs`output`fn`examples!(
            enlist[`events]!enlist .qt.qetl.job.stream.horizons.events;.qt.qetl.job.stream.horizons.events;{[e] e};
            enlist `inputs`expected!(enlist[`events]!enlist .qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0];.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]))]];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_b;];@[.qt.qetl.job.stream.horizons.decl[];`transform;:;`horizontest_one];
        "*takes 1 inputs - it takes two: the events, then the reference*";"events, then reference"]};

.qt.qetl.job.stream.horizons.test_a_key_the_reference_does_not_carry_is_refused:{[t]
    .qt.qetl.job.stream.horizons.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_c;];.qt.qetl.job.stream.horizons.decl[],enlist[`by]!enlist `venue;
        "*by names venue, which the reference input does not carry*";"by is a reference column"]};

.qt.qetl.job.stream.horizons.test_a_refused_streaming_registration_leaves_no_horizon_job:{[t]
    .qt.qetl.job.stream.horizons.job[`hz_l;()!()];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_m;];.qt.qetl.job.stream.horizons.decl_for[`hz_l];
        "*claims procname hz_l1 which hz_l already runs*";"the streaming registry refuses it"];
    .qunit.assertFalse[`hz_m in .qetl.job.stream.horizons.defined[];"and nothing half-registered is left"]};

.qt.qetl.job.stream.horizons.test_an_unknown_key_is_refused:{[t]
    .qt.qetl.job.stream.horizons.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_d;];.qt.qetl.job.stream.horizons.decl[],enlist[`widht]!enlist 1;
        "*declares widht, which a horizon job does not take*";"a typo is not ignored"]};

.qt.qetl.job.stream.horizons.test_it_registers_a_streaming_job_with_its_edges:{[t]
    .qt.qetl.job.stream.horizons.job[`hz_e;()!()];
    d:.qetl.job.stream.def `hz_e;
    .qunit.assertEquals[d`subscribe_to;`hz_events`hz_refs;"events then reference"];
    .qunit.assertEquals[d`publishes;enlist `hz_priced;"the declared output"]};

/ --- running --------------------------------------------------------------

.qt.qetl.job.stream.horizons.test_an_event_waits_for_its_horizon:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_f;()!()];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0;enlist 1f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"half a second is not the horizon"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"one publish once it has passed"];
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.ns[nm;`pending];0;"and the event leaves the queue"]};

.qt.qetl.job.stream.horizons.test_keep_filters_both_tables:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_g;enlist[`keep]!enlist {[x] x[`sym]=`a}];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`b;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`b;enlist .qt.qetl.job.stream.horizons.t0;enlist 1f]];
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.ns[nm;`pending];count .qt.qetl.job.stream.horizons.ns[nm;`history]);0 0;"nothing kept for b"]};

.qt.qetl.job.stream.horizons.test_a_batch_is_projected_onto_the_transform_s_columns:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_h;()!()];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;update extra:`x from .qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    .qunit.assertEquals[cols .qt.qetl.job.stream.horizons.ns[nm;`pending];cols .qt.qetl.job.stream.horizons.events;"the wire's extra column is not buffered"]};

.qt.qetl.job.stream.horizons.test_without_max_age_the_history_keeps_the_as_of_row_per_key:{[t]
    / an old quote for a, newer ones for a, and an old one for b; a fill
    / waiting at t0+10s must still find each key's latest row at or before it
    nm:.qt.qetl.job.stream.horizons.job[`hz_i;()!()];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:02 0D00:00:04;1 2 3f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`b;enlist .qt.qetl.job.stream.horizons.t0;enlist 7f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:01]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`b;.qt.qetl.job.stream.horizons.t0+0D00:00:10]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:05;
    h:.qt.qetl.job.stream.horizons.ns[nm;`history];
    .qunit.assertEquals[exec mid from h where sym=`a;enlist 3f;"a: only its latest row before the waiting fill"];
    .qunit.assertEquals[exec mid from h where sym=`b;enlist 7f;"b: its one row is still b's as-of answer"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:11;
    .qunit.assertEquals[exec ref from last last .qt.qetl.job.stream.horizons.sent;enlist 7f;"so b's fill is priced as if nothing was evicted"]};

.qt.qetl.job.stream.horizons.test_with_max_age_the_history_drops_what_is_too_old_to_count:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_j;enlist[`max_age]!enlist 0D00:00:02];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:09;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:10]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:05;
    .qunit.assertEquals[exec time from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist .qt.qetl.job.stream.horizons.t0+0D00:00:09;"t0 is 10s before the fill, past max_age"]};

.qt.qetl.job.stream.horizons.test_the_history_is_trimmed_while_no_event_waits:{[t]
    / quotes keep arriving and no fill does: the history must not grow
    nm:.qt.qetl.job.stream.horizons.job[`hz_n;()!()];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:01 0D00:00:02;1 2 3f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:10;
    .qunit.assertEquals[exec mid from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist 3f;"only a's as-of row for a fill that could still arrive"]};

.qt.qetl.job.stream.horizons.test_a_publish_that_throws_leaves_the_events_queued:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_k;()!()];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    .qunit.assertThrows[.qt.qetl.job.stream.horizons.ns[nm;`score_ready];.qt.qetl.job.stream.horizons.t0+0D00:00:02;"plant down";"the error is not swallowed here"];
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.ns[nm;`pending];1;"so the next tick tries it again"]};

/ --- event time, reference readiness, windows, identity, expiry (#952) -----
/ .
/ A transform whose events carry the instant they HAPPENED (source_time) and
/ an id, so the plant's receipt stamp and the event's own clock can differ.

.qt.qetl.job.stream.horizons.sevents:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); id:`symbol$(); px:`float$())
.qt.qetl.job.stream.horizons.spriced:([] sym:`symbol$(); id:`symbol$(); px:`float$(); ref:`float$())

.qt.qetl.job.stream.horizons.sprice:{[e;r]
    q:select sym, time:source_time+0D00:00:01, id, px from e;
    j:aj[`sym`time;q;`sym`time xasc r];
    select sym, id, px, ref:mid from j}

.qt.qetl.job.stream.horizons.define_src_transform:{[]
    if[`horizontest_src in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_src;`inputs`output`fn`examples!(
        `events`refs!(.qt.qetl.job.stream.horizons.sevents;.qt.qetl.job.stream.horizons.refs);
        .qt.qetl.job.stream.horizons.spriced;
        .qt.qetl.job.stream.horizons.sprice;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:30; source_time:enlist .qt.qetl.job.stream.horizons.t0;
                    sym:enlist `a; id:enlist `e1; px:enlist 1f);
                ([] time:.qt.qetl.job.stream.horizons.t0+0D00:00:00.5 0D00:00:05; sym:`a`a; mid:1.5 9f));
            ([] sym:enlist `a; id:enlist `e1; px:enlist 1f; ref:enlist 1.5)))];
    }

.qt.qetl.job.stream.horizons.sjob:{[nm;extra]
    .qt.qetl.job.stream.horizons.define_src_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[nm];`transform;:;`horizontest_src];
    .qetl.job.stream.at_horizons[nm;d,extra];
    `.qt.qetl.job.stream.horizons.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.horizons.sent,:enlist (t;x); count x}];
    nm}

/ an event that happened at `at`, received 30s later
.qt.qetl.job.stream.horizons.sev:{[s;at;i] ([] time:enlist at+0D00:00:30; source_time:enlist at; sym:enlist s; id:enlist i; px:enlist 1f)}

.qt.qetl.job.stream.horizons.test_maturity_is_measured_on_event_time_not_receipt:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s1;enlist[`event_time]!enlist `source_time];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:01.5;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:02;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"due at source_time+1s, not 30s later on receipt"]};

.qt.qetl.job.stream.horizons.test_reference_readiness_waits_for_the_reference_to_advance:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s2;`event_time`ready_on!(`source_time;`reference)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:00.5;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"an hour of wall time, but the reference stops before T+1s"];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0+0D00:00:01.2;enlist 3f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"once it has advanced through the horizon"]};

.qt.qetl.job.stream.horizons.test_a_missing_cross_leg_holds_the_event_then_expires_it_with_the_reason:{[t]
    / the event needs keys a and b; only a is ever quoted
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s3;`event_time`ready_on`legs`expire_after!(`source_time;`reference;{[e] (count e)#enlist `a`b};0D00:00:05)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:02;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.ns[nm;`pending];1;"leg b has no reference, so it waits"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:06;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"never scored with a leg missing"];
    gone:.qt.qetl.job.stream.horizons.ns[nm;`expired];
    .qunit.assertEquals[(count gone;count .qt.qetl.job.stream.horizons.ns[nm;`pending]);1 0;"given up on after expire_after"];
    .qunit.assertEquals[first gone`reason;`$"no reference for b";"and the diagnostic names the leg"]};

.qt.qetl.job.stream.horizons.test_a_negative_window_keeps_its_history_and_an_older_anchor:{[t]
    / the window reaches 60s back: quotes at T-60s and T-30s must survive
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s4;`event_time`ready_on`lookback!(`source_time;`reference;0D00:01)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:02 -0D00:01 -0D00:00:30 -0D00:00:01;1 2 3 4f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;
    kept:exec time from .qt.qetl.job.stream.horizons.ns[nm;`history];
    .qunit.assertTrue[all (.qt.qetl.job.stream.horizons.t0+-0D00:01 -0D00:00:30) in kept;"T-60s and T-30s are inside the window"];
    .qunit.assertTrue[(.qt.qetl.job.stream.horizons.t0-0D00:02) in kept;"and T-120s is a's as-of anchor before it"]};

.qt.qetl.job.stream.horizons.test_an_event_without_an_anchor_before_its_window_is_not_ready:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s5;`event_time`ready_on`lookback!(`source_time;`reference;0D00:01)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:30 0D00:00:02;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"nothing at or before T-60s"]};

.qt.qetl.job.stream.horizons.test_a_redelivered_event_is_scored_once:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s6;`event_time`identity!(`source_time;`id)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:02;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;update px:5f from .qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    .qunit.assertEquals[exec px from .qt.qetl.job.stream.horizons.ns[nm;`pending];enlist 5f;"one pending, the last delivery"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:02;
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.ns[nm;`pending];0;"redelivered after it was scored: dropped"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[count raze last each .qt.qetl.job.stream.horizons.sent;1;"one output row, not two"]};

.qt.qetl.job.stream.horizons.test_a_scored_identity_is_forgotten_after_remember:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s7;`event_time`identity`remember!(`source_time;`id;0D00:01)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:02;
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:02;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.ns[nm;`completed];0;"the ledger of scored ids is bounded too"]};

.qt.qetl.job.stream.horizons.test_a_failed_publish_remembers_nothing:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_s8;`event_time`identity!(`source_time;`id)];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    .qunit.assertThrows[.qt.qetl.job.stream.horizons.ns[nm;`score_ready];.qt.qetl.job.stream.horizons.t0+0D00:00:02;"plant down";"surfaced"];
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.ns[nm;`pending];count .qt.qetl.job.stream.horizons.ns[nm;`completed]);1 0;
        "still queued, and not marked scored - so a retry is not suppressed"]};

.qt.qetl.job.stream.horizons.test_two_jobs_on_the_same_tables_keep_their_own_state:{[t]
    / a long and a wide job over one tape
    a:.qt.qetl.job.stream.horizons.sjob[`hz_s9;enlist[`event_time]!enlist `source_time];
    b:.qt.qetl.job.stream.horizons.sjob[`hz_s10;`event_time`identity!(`source_time;`id)];
    (.qt.qetl.job.stream.horizons.ns[a;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.ns[a;`pending];count .qt.qetl.job.stream.horizons.ns[b;`pending]);1 0;"a batch to one is not the other's"];
    (.qt.qetl.job.stream.horizons.ns[b;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[a;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:02;
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.ns[a;`pending];count .qt.qetl.job.stream.horizons.ns[b;`pending]);0 1;"scoring one leaves the other queued"]};

.qt.qetl.job.stream.horizons.test_the_new_keys_are_refused_when_malformed:{[t]
    .qt.qetl.job.stream.horizons.define_src_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[`hz_s11];`transform;:;`horizontest_src];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_s11;];d,enlist[`event_time]!enlist `when;
        "*events input carries no when*";"an event_time the events lack"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_s11;];d,enlist[`ready_on]!enlist `soon;
        "*ready_on must be one of wall, reference*";"an unknown rule"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_s11;];d,enlist[`legs]!enlist {[e] e};
        "*declares legs, which only ready_on `reference reads*";"legs without reference readiness"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_s11;];d,enlist[`expire_after]!enlist 0D00:00:00.5;
        "*expire_after must be a timespan longer than the horizon*";"expiring before due"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_s11;];d,enlist[`remember]!enlist 0D01;
        "*remember without identity*";"remember with nothing to remember"]};

/ --- eviction under late events and max_age (#958, #959) ------------------

.qt.qetl.job.stream.horizons.late_refs:{[] .qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+0D00:00:00 0D00:00:00.5 0D00:00:05 0D00:00:20;1 1.5 9 20f]}

.qt.qetl.job.stream.horizons.test_a_late_event_finds_its_reference_after_an_idle_tick:{[t]
    / received 30s after it happened; an idle tick at t0+25s must not have evicted t0's rows
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_l1;`event_time`max_lateness!(`source_time;0D00:01)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.late_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:25;
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:30;
    .qunit.assertEquals[exec ref from last last .qt.qetl.job.stream.horizons.sent;enlist 1.5;"the answer the full history gives"]};

.qt.qetl.job.stream.horizons.test_the_default_lateness_bound_covers_a_source_time_event:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_l2;enlist[`event_time]!enlist `source_time];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.late_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:25;
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:30;
    .qunit.assertEquals[exec ref from last last .qt.qetl.job.stream.horizons.sent;enlist 1.5;"no declared bound: the documented default applies"]};

.qt.qetl.job.stream.horizons.test_a_late_event_is_not_falsely_expired_under_reference_readiness:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_l3;`event_time`ready_on`expire_after!(`source_time;`reference;0D00:01)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.late_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:25;
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:30;
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.sent;count .qt.qetl.job.stream.horizons.ns[nm;`expired]);1 0;"scored, not given up on"]};

.qt.qetl.job.stream.horizons.test_the_lateness_bound_still_trims_the_history:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_l4;`event_time`max_lateness!(`source_time;0D00:00:02)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.late_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:25;
    .qunit.assertEquals[exec time from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist .qt.qetl.job.stream.horizons.t0+0D00:00:20;"only the as-of row below now-horizon-lateness"]};

.qt.qetl.job.stream.horizons.test_a_receipt_time_job_has_no_lateness:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_l5;()!()];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def nm)`max_lateness;0D;"event_time is `time"]};

.qt.qetl.job.stream.horizons.test_max_lateness_must_be_a_non_negative_timespan:{[t]
    .qt.qetl.job.stream.horizons.define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_l6;];.qt.qetl.job.stream.horizons.decl_for[`hz_l6],enlist[`max_lateness]!enlist -0D00:00:01;
        "*max_lateness must be a timespan*";"negative"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_l6;];.qt.qetl.job.stream.horizons.decl_for[`hz_l6],enlist[`max_lateness]!enlist 5;
        "*max_lateness must be a timespan*";"not a timespan"]};

/ the anchor is at t0-2s, past max_age 1s; the reference reaches t0+1s only after a tick
.qt.qetl.job.stream.horizons.anchor_run:{[nm;tick]
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:02 0D00:00:00.5;1 1.5]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    if[tick; (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.6];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0+0D00:00:01.2;enlist 3f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:02;
    exec ref from last last .qt.qetl.job.stream.horizons.sent}

.qt.qetl.job.stream.horizons.test_max_age_with_reference_readiness_keeps_the_anchor_across_a_tick:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_m1;`ready_on`max_age`expire_after!(`reference;0D00:00:01;0D00:01)];
    .qunit.assertEquals[.qt.qetl.job.stream.horizons.anchor_run[nm;1b];enlist 1.5;"scored as the no-tick run is"];
    nm2:.qt.qetl.job.stream.horizons.job[`hz_m2;`ready_on`max_age`expire_after!(`reference;0D00:00:01;0D00:01)];
    .qunit.assertEquals[.qt.qetl.job.stream.horizons.anchor_run[nm2;0b];enlist 1.5;"the control: no intermediate tick"]};

.qt.qetl.job.stream.horizons.test_max_age_without_reference_readiness_still_drops_everything_old:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_m3;enlist[`max_age]!enlist 0D00:00:01];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:02 0D00:00:00.5;1 1.5]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.6;
    .qunit.assertEquals[exec time from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;"wall readiness: unchanged"]};

/ --- the as-of row kept by eviction is the latest by reference time (#1283) ---

.qt.qetl.job.stream.horizons.trefs:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); mid:`float$())

/ each event priced at the latest mid, by the reference's own clock, at or before event time
.qt.qetl.job.stream.horizons.rtprice:{[e;r]
    q:select sym, source_time:time, event_time:time, px from e;
    j:aj[`sym`source_time;q;`sym`source_time xasc r];
    select sym, event_time, px, ref:mid from j}

.qt.qetl.job.stream.horizons.define_rt_transform:{[]
    if[`horizontest_rt in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_rt;`inputs`output`fn`examples!(
        `events`refs!(.qt.qetl.job.stream.horizons.events;.qt.qetl.job.stream.horizons.trefs);
        .qt.qetl.job.stream.horizons.priced;
        .qt.qetl.job.stream.horizons.rtprice;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .qt.qetl.job.stream.horizons.t0; sym:enlist `a; px:enlist 1f);
                ([] time:enlist .qt.qetl.job.stream.horizons.t0; source_time:enlist .qt.qetl.job.stream.horizons.t0; sym:enlist `a; mid:enlist 1.5));
            ([] sym:enlist `a; event_time:enlist .qt.qetl.job.stream.horizons.t0; px:enlist 1f; ref:enlist 1.5)))];
    }

.qt.qetl.job.stream.horizons.rtjob:{[nm;extra]
    .qt.qetl.job.stream.horizons.define_rt_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[nm];`transform`reference_time;:;(`horizontest_rt;`source_time)];
    .qetl.job.stream.at_horizons[nm;d,extra];
    `.qt.qetl.job.stream.horizons.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.horizons.sent,:enlist (t;x); count x}];
    nm}

/ two quotes delivered out of source_time order: the 10:00:00 one first, a delayed 09:59:00 one second
.qt.qetl.job.stream.horizons.rt_quotes:{[] ([] time:.qt.qetl.job.stream.horizons.t0+0D00:00:01 0D00:00:02; source_time:.qt.qetl.job.stream.horizons.t0+0D00:00:00 -0D00:01:00; sym:`a`a; mid:1.1 0.9)}

.qt.qetl.job.stream.horizons.test_eviction_keeps_the_latest_reference_by_reference_time_not_arrival:{[t]
    nm:.qt.qetl.job.stream.horizons.rtjob[`hz_r1;enlist[`horizon]!enlist 0D00:00:10];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rt_quotes[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;([] time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:30; sym:enlist `a; px:enlist 1f)];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:31;
    .qunit.assertEquals[exec source_time from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist .qt.qetl.job.stream.horizons.t0;"the 10:00:00 quote, not the last-arrived 09:59:00 one"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:41;
    .qunit.assertEquals[exec ref from last last .qt.qetl.job.stream.horizons.sent;enlist 1.1;"the answer the full history gives"]};

.qt.qetl.job.stream.horizons.test_max_age_reference_readiness_keeps_the_latest_anchor_by_reference_time:{[t]
    nm:.qt.qetl.job.stream.horizons.rtjob[`hz_r2;`horizon`ready_on`max_age`expire_after!(0D00:00:10;`reference;0D00:00:05;0D00:05)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rt_quotes[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;([] time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:30; sym:enlist `a; px:enlist 1f)];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:31;
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;([] time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:41; source_time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:41; sym:enlist `a; mid:enlist 3f)];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:42;
    .qunit.assertEquals[exec ref from last last .qt.qetl.job.stream.horizons.sent;enlist 1.1;"the anchor is the latest quote by source_time"]};

/ --- ready_on `both, and retention per source (#1395) -----------------------
/ .
/ `wall` alone scores on the plant clock, `reference` alone on the reference's
/ own coverage. A job that must see both - the horizon has elapsed AND the
/ reference reaches it - declares `both`.

.qt.qetl.job.stream.horizons.test_both_waits_for_the_wall_even_when_the_reference_already_covers_it:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_b1;`event_time`ready_on!(`source_time;`both)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:01.2;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"covered through T+1s, but only half a second has elapsed"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"scored once the horizon has elapsed too"]};

.qt.qetl.job.stream.horizons.test_both_waits_for_the_reference_even_when_the_wall_has_elapsed:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_b2;`event_time`ready_on!(`source_time;`both)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:00.5;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"an hour on the wall clock, but the reference stops at T+0.5s"];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0+0D00:00:01.2;enlist 3f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"scored once the reference has advanced through the horizon"]};

.qt.qetl.job.stream.horizons.test_both_expires_an_uncovered_event_with_the_coverage_reason:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_b3;`event_time`ready_on`expire_after!(`source_time;`both;0D00:00:05)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:00.5;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[(count .qt.qetl.job.stream.horizons.sent;count .qt.qetl.job.stream.horizons.ns[nm;`pending]);0 1;"waiting, not yet given up on"];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:06;
    gone:.qt.qetl.job.stream.horizons.ns[nm;`expired];
    .qunit.assertEquals[(count gone;count .qt.qetl.job.stream.horizons.sent);1 0;"given up on, never scored"];
    .qunit.assertTrue[(string first gone`reason) like "reference has not reached*";
        "the wall had elapsed, so the reason is the reference's coverage"]};

.qt.qetl.job.stream.horizons.test_both_reads_legs_like_reference:{[t]
    nm:.qt.qetl.job.stream.horizons.sjob[`hz_b4;`event_time`ready_on`legs!(`source_time;`both;{[e] (count e)#enlist `a`b})];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:02;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.sev[`a;.qt.qetl.job.stream.horizons.t0;`e1]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"leg b has no reference"];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`b;.qt.qetl.job.stream.horizons.t0+-0D00:00:01 0D00:00:02;1 2f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:03;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"both legs anchored and advanced, and the wall has elapsed"]};

.qt.qetl.job.stream.horizons.test_both_is_refused_without_a_one_column_by:{[t]
    .qt.qetl.job.stream.horizons.define_src_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[`hz_b5];`transform;:;`horizontest_src];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_b5;];d,`event_time`ready_on`by!(`source_time;`both;`sym`mid);
        "*ready_on `both needs a one-column by*";"the key its legs name"]};

/ a reference quoted by two sources
.qt.qetl.job.stream.horizons.mrefs:([] time:`timestamp$(); sym:`symbol$(); source:`symbol$(); mid:`float$())

.qt.qetl.job.stream.horizons.mprice:{[e;r]
    q:select sym, time:time+0D00:00:01, event_time:time, px from e;
    j:aj[`sym`time;q;`sym`time xasc r];
    select sym, event_time, px, ref:mid from j}

.qt.qetl.job.stream.horizons.define_ms_transform:{[]
    if[`horizontest_ms in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_ms;`inputs`output`fn`examples!(
        `events`refs!(.qt.qetl.job.stream.horizons.events;.qt.qetl.job.stream.horizons.mrefs);
        .qt.qetl.job.stream.horizons.priced;
        .qt.qetl.job.stream.horizons.mprice;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .qt.qetl.job.stream.horizons.t0; sym:enlist `a; px:enlist 1f);
                ([] time:enlist .qt.qetl.job.stream.horizons.t0+0D00:00:00.5; sym:enlist `a; source:enlist `s1; mid:enlist 1.5));
            ([] sym:enlist `a; event_time:enlist .qt.qetl.job.stream.horizons.t0; px:enlist 1f; ref:enlist 1.5)))];
    }

.qt.qetl.job.stream.horizons.msjob:{[nm;extra]
    .qt.qetl.job.stream.horizons.define_ms_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[nm];`transform;:;`horizontest_ms];
    .qetl.job.stream.at_horizons[nm;d,extra];
    `.qt.qetl.job.stream.horizons.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .qt.qetl.job.stream.horizons.sent,:enlist (t;x); count x}];
    nm}

/ source s1 last quoted a at T-8s, s2 at T-7s, then both older rows
.qt.qetl.job.stream.horizons.ms_refs:{[] ([] time:.qt.qetl.job.stream.horizons.t0+-0D00:00:10 -0D00:00:09 -0D00:00:08 -0D00:00:07; sym:4#`a; source:`s1`s2`s1`s2; mid:1 2 3 4f)}

.qt.qetl.job.stream.horizons.test_retain_by_keeps_the_latest_pre_cutoff_row_of_every_source:{[t]
    nm:.qt.qetl.job.stream.horizons.msjob[`hz_t1;enlist[`retain_by]!enlist `sym`source];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.ms_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;
    .qunit.assertEquals[asc exec mid from .qt.qetl.job.stream.horizons.ns[nm;`history];3 4f;"s1's T-8s book and s2's T-7s book: each source's as-of answer"]};

.qt.qetl.job.stream.horizons.test_without_retain_by_only_the_latest_row_per_key_is_kept:{[t]
    nm:.qt.qetl.job.stream.horizons.msjob[`hz_t2;()!()];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.ms_refs[]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:00.5;
    .qunit.assertEquals[exec mid from .qt.qetl.job.stream.horizons.ns[nm;`history];enlist 4f;"the control: by alone drops s1's quiet book"]};

.qt.qetl.job.stream.horizons.test_retain_by_is_refused_when_malformed:{[t]
    .qt.qetl.job.stream.horizons.define_ms_transform[];
    d:@[.qt.qetl.job.stream.horizons.decl_for[`hz_t3];`transform;:;`horizontest_ms];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_t3;];d,enlist[`retain_by]!enlist `sym`feed;
        "*retain_by names feed, which the reference input does not carry*";"a reference column"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_t3;];d,enlist[`retain_by]!enlist enlist `source;
        "*retain_by must include by (sym)*";"it extends by, it does not replace it"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_t3;];d,enlist[`retain_by]!enlist 5;
        "*retain_by must be reference column names, symbols*";"symbols"]};

/ --- the quality gate (#1232) -----------------------------------------------

/ A quality gate that names the whole batch as offending when flagged, and
/ passes when not (#1232: the kind forwards check/on_fail to define).
.qt.qetl.job.stream.horizons.gate:{[flag;x] n:$[flag;1;0]; ([] check:n#`always; status:n#`bad; detail:n#enlist "flagged")}

.qt.qetl.job.stream.horizons.test_a_horizon_job_can_declare_a_check_that_withholds_a_score:{[t]
    nm:.qt.qetl.job.stream.horizons.job[`hz_chk;`check`on_fail!(.qt.qetl.job.stream.horizons.gate[1b];`hold)];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0;enlist 1f]];
    (.qt.qetl.job.stream.horizons.ns[nm;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[nm;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;0;"the failing check withheld the score"];
    .qunit.assertEquals[(.qetl.stream_health.of nm)`failed;1;"and counted it"];
    ok:.qt.qetl.job.stream.horizons.job[`hz_chk_ok;`check`on_fail!(.qt.qetl.job.stream.horizons.gate[0b];`hold)];
    (.qt.qetl.job.stream.horizons.ns[ok;`on_batch])[`hz_refs;.qt.qetl.job.stream.horizons.rf[`a;enlist .qt.qetl.job.stream.horizons.t0;enlist 1f]];
    (.qt.qetl.job.stream.horizons.ns[ok;`on_batch])[`hz_events;.qt.qetl.job.stream.horizons.ev[`a;.qt.qetl.job.stream.horizons.t0]];
    (.qt.qetl.job.stream.horizons.ns[ok;`score_ready]) .qt.qetl.job.stream.horizons.t0+0D00:00:01;
    .qunit.assertEquals[count .qt.qetl.job.stream.horizons.sent;1;"a passing check lets it through"]};
