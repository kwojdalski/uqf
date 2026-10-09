/ test_horizon.q - .qetl.job.stream.horizons: a job that evaluates each event
/ once its horizon has passed (#945).
/ .
/ A small transform of its own - each event priced at the latest reference
/ mid at or before event time + 1s - so the shell is tested apart from the
/ markouts that use it (test_stream_job.q, test_crypto_markout.q).

\d .horizontest

t0:2026.10.09D10:00:00

events:([] time:`timestamp$(); sym:`symbol$(); px:`float$())
refs:([] time:`timestamp$(); sym:`symbol$(); mid:`float$())
priced:([] sym:`symbol$(); event_time:`timestamp$(); px:`float$(); ref:`float$())

/ the as-of mid at time+1s, per event
price:{[e;r]
    q:select sym, time:time+0D00:00:01, event_time:time, px from e;
    j:aj[`sym`time;q;`sym`time xasc r];
    select sym, event_time, px, ref:mid from j}

define_transform:{[]
    if[`horizontest_px in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_px;`inputs`output`fn`examples!(
        `events`refs!(.horizontest.events;.horizontest.refs);
        .horizontest.priced;
        .horizontest.price;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .horizontest.t0; sym:enlist `a; px:enlist 1f);
                ([] time:.horizontest.t0+0D00:00:00.5 0D00:00:05; sym:`a`a; mid:1.5 9f));
            ([] sym:enlist `a; event_time:enlist .horizontest.t0; px:enlist 1f; ref:enlist 1.5)))];
    }

decl:{[] `procname`events`reference`transform`publishes`horizon`period!(
    `hzt1;`hz_events;`hz_refs;`horizontest_px;`hz_priced;0D00:00:01;0D00:00:01)}

/ one process per job, as .qetl.job.stream.define insists
decl_for:{[nm] @[decl[];`procname;:;`$string[nm],"1"]}

sent:()

/ A horizon job named nm, its publish wired to a recorder.
job:{[nm;extra]
    define_transform[];
    .qetl.job.stream.at_horizons[nm;decl_for[nm],extra];
    `.horizontest.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .horizontest.sent,:enlist (t;x); count x}];
    nm}

ns:{[nm;k] get ` sv (.qetl.job.stream.namespace nm),k}

ev:{[s;tm] ([] time:enlist tm; sym:enlist s; px:enlist 1f)}
rf:{[s;tms;mids] ([] time:tms; sym:(count tms)#s; mid:mids)}

/ --- declaring -----------------------------------------------------------

test_a_declaration_missing_a_key_is_refused_by_name:{[t]
    define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_a;];`horizon _ decl[];
        "*horizon job hz_a is missing horizon*";"the key it lacks"]};

test_a_one_input_transform_is_refused:{[t]
    define_transform[];
    if[not `horizontest_one in .qetl.transform.defined[];
        .qetl.transform.define[`horizontest_one;`inputs`output`fn`examples!(
            enlist[`events]!enlist .horizontest.events;.horizontest.events;{[e] e};
            enlist `inputs`expected!(enlist[`events]!enlist .horizontest.ev[`a;.horizontest.t0];.horizontest.ev[`a;.horizontest.t0]))]];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_b;];@[decl[];`transform;:;`horizontest_one];
        "*takes 1 inputs - it takes two: the events, then the reference*";"events, then reference"]};

test_a_key_the_reference_does_not_carry_is_refused:{[t]
    define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_c;];decl[],enlist[`by]!enlist `venue;
        "*by names venue, which the reference input does not carry*";"by is a reference column"]};

test_a_refused_streaming_registration_leaves_no_horizon_job:{[t]
    job[`hz_l;()!()];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_m;];decl_for[`hz_l];
        "*claims procname hz_l1 which hz_l already runs*";"the streaming registry refuses it"];
    .qunit.assertFalse[`hz_m in .qetl.job.stream.horizons.defined[];"and nothing half-registered is left"]};

test_an_unknown_key_is_refused:{[t]
    define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_d;];decl[],enlist[`widht]!enlist 1;
        "*declares widht, which a horizon job does not take*";"a typo is not ignored"]};

test_it_registers_a_streaming_job_with_its_edges:{[t]
    job[`hz_e;()!()];
    d:.qetl.job.stream.def `hz_e;
    .qunit.assertEquals[d`subscribe_to;`hz_events`hz_refs;"events then reference"];
    .qunit.assertEquals[d`publishes;enlist `hz_priced;"the declared output"]};

/ --- running --------------------------------------------------------------

test_an_event_waits_for_its_horizon:{[t]
    nm:job[`hz_f;()!()];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;enlist t0;enlist 1f]];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0]];
    (ns[nm;`score_ready]) t0+0D00:00:00.5;
    .qunit.assertEquals[count sent;0;"half a second is not the horizon"];
    (ns[nm;`score_ready]) t0+0D00:00:01;
    .qunit.assertEquals[count sent;1;"one publish once it has passed"];
    .qunit.assertEquals[count ns[nm;`pending];0;"and the event leaves the queue"]};

test_keep_filters_both_tables:{[t]
    nm:job[`hz_g;enlist[`keep]!enlist {[x] x[`sym]=`a}];
    (ns[nm;`on_batch])[`hz_events;ev[`b;t0]];
    (ns[nm;`on_batch])[`hz_refs;rf[`b;enlist t0;enlist 1f]];
    .qunit.assertEquals[(count ns[nm;`pending];count ns[nm;`history]);0 0;"nothing kept for b"]};

test_a_batch_is_projected_onto_the_transform_s_columns:{[t]
    nm:job[`hz_h;()!()];
    (ns[nm;`on_batch])[`hz_events;update extra:`x from ev[`a;t0]];
    .qunit.assertEquals[cols ns[nm;`pending];cols events;"the wire's extra column is not buffered"]};

test_without_max_age_the_history_keeps_the_as_of_row_per_key:{[t]
    / an old quote for a, newer ones for a, and an old one for b; a fill
    / waiting at t0+10s must still find each key's latest row at or before it
    nm:job[`hz_i;()!()];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:02 0D00:00:04;1 2 3f]];
    (ns[nm;`on_batch])[`hz_refs;rf[`b;enlist t0;enlist 7f]];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0+0D00:00:01]];
    (ns[nm;`on_batch])[`hz_events;ev[`b;t0+0D00:00:10]];
    (ns[nm;`score_ready]) t0+0D00:00:05;
    h:ns[nm;`history];
    .qunit.assertEquals[exec mid from h where sym=`a;enlist 3f;"a: only its latest row before the waiting fill"];
    .qunit.assertEquals[exec mid from h where sym=`b;enlist 7f;"b: its one row is still b's as-of answer"];
    (ns[nm;`score_ready]) t0+0D00:00:11;
    .qunit.assertEquals[exec ref from last last sent;enlist 7f;"so b's fill is priced as if nothing was evicted"]};

test_with_max_age_the_history_drops_what_is_too_old_to_count:{[t]
    nm:job[`hz_j;enlist[`max_age]!enlist 0D00:00:02];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:09;1 2f]];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0+0D00:00:10]];
    (ns[nm;`score_ready]) t0+0D00:00:05;
    .qunit.assertEquals[exec time from ns[nm;`history];enlist t0+0D00:00:09;"t0 is 10s before the fill, past max_age"]};

test_the_history_is_trimmed_while_no_event_waits:{[t]
    / quotes keep arriving and no fill does: the history must not grow
    nm:job[`hz_n;()!()];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:01 0D00:00:02;1 2 3f]];
    (ns[nm;`score_ready]) t0+0D00:00:10;
    .qunit.assertEquals[exec mid from ns[nm;`history];enlist 3f;"only a's as-of row for a fill that could still arrive"]};

test_a_publish_that_throws_leaves_the_events_queued:{[t]
    nm:job[`hz_k;()!()];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0]];
    .qunit.assertThrows[ns[nm;`score_ready];t0+0D00:00:02;"plant down";"the error is not swallowed here"];
    .qunit.assertEquals[count ns[nm;`pending];1;"so the next tick tries it again"]};

/ --- event time, reference readiness, windows, identity, expiry (#952) -----
/ .
/ A transform whose events carry the instant they HAPPENED (source_time) and
/ an id, so the plant's receipt stamp and the event's own clock can differ.

sevents:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); id:`symbol$(); px:`float$())
spriced:([] sym:`symbol$(); id:`symbol$(); px:`float$(); ref:`float$())

sprice:{[e;r]
    q:select sym, time:source_time+0D00:00:01, id, px from e;
    j:aj[`sym`time;q;`sym`time xasc r];
    select sym, id, px, ref:mid from j}

define_src_transform:{[]
    if[`horizontest_src in .qetl.transform.defined[]; :()];
    .qetl.transform.define[`horizontest_src;`inputs`output`fn`examples!(
        `events`refs!(.horizontest.sevents;.horizontest.refs);
        .horizontest.spriced;
        .horizontest.sprice;
        enlist `inputs`expected!(
            `events`refs!(([] time:enlist .horizontest.t0+0D00:00:30; source_time:enlist .horizontest.t0;
                    sym:enlist `a; id:enlist `e1; px:enlist 1f);
                ([] time:.horizontest.t0+0D00:00:00.5 0D00:00:05; sym:`a`a; mid:1.5 9f));
            ([] sym:enlist `a; id:enlist `e1; px:enlist 1f; ref:enlist 1.5)))];
    }

sjob:{[nm;extra]
    define_src_transform[];
    d:@[decl_for[nm];`transform;:;`horizontest_src];
    .qetl.job.stream.at_horizons[nm;d,extra];
    `.horizontest.sent set ();
    .qetl.job.stream.wire[nm;{[t;x] .horizontest.sent,:enlist (t;x); count x}];
    nm}

/ an event that happened at `at`, received 30s later
sev:{[s;at;i] ([] time:enlist at+0D00:00:30; source_time:enlist at; sym:enlist s; id:enlist i; px:enlist 1f)}

test_maturity_is_measured_on_event_time_not_receipt:{[t]
    nm:sjob[`hz_s1;enlist[`event_time]!enlist `source_time];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:01.5;1 2f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:02;
    .qunit.assertEquals[count sent;1;"due at source_time+1s, not 30s later on receipt"]};

test_reference_readiness_waits_for_the_reference_to_advance:{[t]
    nm:sjob[`hz_s2;`event_time`ready_on!(`source_time;`reference)];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:00.5;1 2f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D01;
    .qunit.assertEquals[count sent;0;"an hour of wall time, but the reference stops before T+1s"];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;enlist t0+0D00:00:01.2;enlist 3f]];
    (ns[nm;`score_ready]) t0+0D01;
    .qunit.assertEquals[count sent;1;"once it has advanced through the horizon"]};

test_a_missing_cross_leg_holds_the_event_then_expires_it_with_the_reason:{[t]
    / the event needs keys a and b; only a is ever quoted
    nm:sjob[`hz_s3;`event_time`ready_on`legs`expire_after!(`source_time;`reference;{[e] (count e)#enlist `a`b};0D00:00:05)];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+-0D00:00:01 0D00:00:02;1 2f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:03;
    .qunit.assertEquals[count ns[nm;`pending];1;"leg b has no reference, so it waits"];
    (ns[nm;`score_ready]) t0+0D00:00:06;
    .qunit.assertEquals[count sent;0;"never scored with a leg missing"];
    gone:ns[nm;`expired];
    .qunit.assertEquals[(count gone;count ns[nm;`pending]);1 0;"given up on after expire_after"];
    .qunit.assertEquals[first gone`reason;`$"no reference for b";"and the diagnostic names the leg"]};

test_a_negative_window_keeps_its_history_and_an_older_anchor:{[t]
    / the window reaches 60s back: quotes at T-60s and T-30s must survive
    nm:sjob[`hz_s4;`event_time`ready_on`lookback!(`source_time;`reference;0D00:01)];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+-0D00:02 -0D00:01 -0D00:00:30 -0D00:00:01;1 2 3 4f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:00.5;
    kept:exec time from ns[nm;`history];
    .qunit.assertTrue[all (t0+-0D00:01 -0D00:00:30) in kept;"T-60s and T-30s are inside the window"];
    .qunit.assertTrue[(t0-0D00:02) in kept;"and T-120s is a's as-of anchor before it"]};

test_an_event_without_an_anchor_before_its_window_is_not_ready:{[t]
    nm:sjob[`hz_s5;`event_time`ready_on`lookback!(`source_time;`reference;0D00:01)];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+-0D00:00:30 0D00:00:02;1 2f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:03;
    .qunit.assertEquals[count sent;0;"nothing at or before T-60s"]};

test_a_redelivered_event_is_scored_once:{[t]
    nm:sjob[`hz_s6;`event_time`identity!(`source_time;`id)];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+0D00:00:00 0D00:00:02;1 2f]];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`on_batch])[`hz_events;update px:5f from sev[`a;t0;`e1]];
    .qunit.assertEquals[exec px from ns[nm;`pending];enlist 5f;"one pending, the last delivery"];
    (ns[nm;`score_ready]) t0+0D00:00:02;
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    .qunit.assertEquals[count ns[nm;`pending];0;"redelivered after it was scored: dropped"];
    (ns[nm;`score_ready]) t0+0D00:00:03;
    .qunit.assertEquals[count raze last each sent;1;"one output row, not two"]};

test_a_scored_identity_is_forgotten_after_remember:{[t]
    nm:sjob[`hz_s7;`event_time`identity`remember!(`source_time;`id;0D00:01)];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:02;
    (ns[nm;`score_ready]) t0+0D00:02;
    .qunit.assertEquals[count ns[nm;`completed];0;"the ledger of scored ids is bounded too"]};

test_a_failed_publish_remembers_nothing:{[t]
    nm:sjob[`hz_s8;`event_time`identity!(`source_time;`id)];
    .qetl.job.stream.wire[nm;{[t;x] '"plant down"}];
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    .qunit.assertThrows[ns[nm;`score_ready];t0+0D00:00:02;"plant down";"surfaced"];
    .qunit.assertEquals[(count ns[nm;`pending];count ns[nm;`completed]);1 0;
        "still queued, and not marked scored - so a retry is not suppressed"]};

test_two_jobs_on_the_same_tables_keep_their_own_state:{[t]
    / a long and a wide job over one tape
    a:sjob[`hz_s9;enlist[`event_time]!enlist `source_time];
    b:sjob[`hz_s10;`event_time`identity!(`source_time;`id)];
    (ns[a;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    .qunit.assertEquals[(count ns[a;`pending];count ns[b;`pending]);1 0;"a batch to one is not the other's"];
    (ns[b;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[a;`score_ready]) t0+0D00:00:02;
    .qunit.assertEquals[(count ns[a;`pending];count ns[b;`pending]);0 1;"scoring one leaves the other queued"]};

test_the_new_keys_are_refused_when_malformed:{[t]
    define_src_transform[];
    d:@[decl_for[`hz_s11];`transform;:;`horizontest_src];
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

late_refs:{[] rf[`a;t0+0D00:00:00 0D00:00:00.5 0D00:00:05 0D00:00:20;1 1.5 9 20f]}

test_a_late_event_finds_its_reference_after_an_idle_tick:{[t]
    / received 30s after it happened; an idle tick at t0+25s must not have evicted t0's rows
    nm:sjob[`hz_l1;`event_time`max_lateness!(`source_time;0D00:01)];
    (ns[nm;`on_batch])[`hz_refs;late_refs[]];
    (ns[nm;`score_ready]) t0+0D00:00:25;
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:30;
    .qunit.assertEquals[exec ref from last last sent;enlist 1.5;"the answer the full history gives"]};

test_the_default_lateness_bound_covers_a_source_time_event:{[t]
    nm:sjob[`hz_l2;enlist[`event_time]!enlist `source_time];
    (ns[nm;`on_batch])[`hz_refs;late_refs[]];
    (ns[nm;`score_ready]) t0+0D00:00:25;
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:30;
    .qunit.assertEquals[exec ref from last last sent;enlist 1.5;"no declared bound: the documented default applies"]};

test_a_late_event_is_not_falsely_expired_under_reference_readiness:{[t]
    nm:sjob[`hz_l3;`event_time`ready_on`expire_after!(`source_time;`reference;0D00:01)];
    (ns[nm;`on_batch])[`hz_refs;late_refs[]];
    (ns[nm;`score_ready]) t0+0D00:00:25;
    (ns[nm;`on_batch])[`hz_events;sev[`a;t0;`e1]];
    (ns[nm;`score_ready]) t0+0D00:00:30;
    .qunit.assertEquals[(count sent;count ns[nm;`expired]);1 0;"scored, not given up on"]};

test_the_lateness_bound_still_trims_the_history:{[t]
    nm:sjob[`hz_l4;`event_time`max_lateness!(`source_time;0D00:00:02)];
    (ns[nm;`on_batch])[`hz_refs;late_refs[]];
    (ns[nm;`score_ready]) t0+0D00:00:25;
    .qunit.assertEquals[exec time from ns[nm;`history];enlist t0+0D00:00:20;"only the as-of row below now-horizon-lateness"]};

test_a_receipt_time_job_has_no_lateness:{[t]
    nm:job[`hz_l5;()!()];
    .qunit.assertEquals[(.qetl.job.stream.horizons.def nm)`max_lateness;0D;"event_time is `time"]};

test_max_lateness_must_be_a_non_negative_timespan:{[t]
    define_transform[];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_l6;];decl_for[`hz_l6],enlist[`max_lateness]!enlist -0D00:00:01;
        "*max_lateness must be a timespan*";"negative"];
    .qunit.assertThrows[.qetl.job.stream.at_horizons[`hz_l6;];decl_for[`hz_l6],enlist[`max_lateness]!enlist 5;
        "*max_lateness must be a timespan*";"not a timespan"]};

/ the anchor is at t0-2s, past max_age 1s; the reference reaches t0+1s only after a tick
anchor_run:{[nm;tick]
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+-0D00:00:02 0D00:00:00.5;1 1.5]];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0]];
    if[tick; (ns[nm;`score_ready]) t0+0D00:00:00.6];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;enlist t0+0D00:00:01.2;enlist 3f]];
    (ns[nm;`score_ready]) t0+0D00:00:02;
    exec ref from last last sent}

test_max_age_with_reference_readiness_keeps_the_anchor_across_a_tick:{[t]
    nm:job[`hz_m1;`ready_on`max_age`expire_after!(`reference;0D00:00:01;0D00:01)];
    .qunit.assertEquals[anchor_run[nm;1b];enlist 1.5;"scored as the no-tick run is"];
    nm2:job[`hz_m2;`ready_on`max_age`expire_after!(`reference;0D00:00:01;0D00:01)];
    .qunit.assertEquals[anchor_run[nm2;0b];enlist 1.5;"the control: no intermediate tick"]};

test_max_age_without_reference_readiness_still_drops_everything_old:{[t]
    nm:job[`hz_m3;enlist[`max_age]!enlist 0D00:00:01];
    (ns[nm;`on_batch])[`hz_refs;rf[`a;t0+-0D00:00:02 0D00:00:00.5;1 1.5]];
    (ns[nm;`on_batch])[`hz_events;ev[`a;t0]];
    (ns[nm;`score_ready]) t0+0D00:00:00.6;
    .qunit.assertEquals[exec time from ns[nm;`history];enlist t0+0D00:00:00.5;"wall readiness: unchanged"]};

\d .
