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

\d .
