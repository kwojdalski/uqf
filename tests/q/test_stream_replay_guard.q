/ test_stream_replay_guard.q - a batch that cannot be applied while a job catches
/ up from a log is quarantined and the catch-up carries on (#2195); a job that
/ is no longer subscribed says so at its next beat (#2201); and a logged
/ message is named by the schema its log was written under (#2195).
/ .
/ The fake transport delivers a log the way a runner does, as (table; rows)
/ through the handler start gives it, so what is tested is the shell's guard,
/ not a mock of it.

/ A log of four batches, the second of which on_batch rejects.
.qt.qetl.stream_replay_guard.log:{[]
    mk:{[s;q] ([] time:enlist .z.p; sym:enlist s; qty:enlist q)};
    ((`fills;mk[`EURUSD;1f]);(`fills;mk[`GBPUSD;0n]);(`fills;mk[`USDJPY;3f]);(`fills;mk[`AUDUSD;4f]))}

.qt.qetl.stream_replay_guard.define:{[]
    .qt.qetl.stream_replay_guard.undefine[];
    `.qpipe.job.rpg.book set ([] sym:`symbol$(); qty:`float$());
    `.qpipe.job.rpg.on_batch set {[t;x] if[any null x`qty; '"bad qty"]; `.qpipe.job.rpg.book insert (`sym`qty#x);};
    .qetl.job.stream.define[`rpg;`procname`subscribe_to`outputs`on_batch`replay`state!(`rpg1;enlist `fills;enlist `rpg_out;.qpipe.job.rpg.on_batch;1b;enlist `book)];
    `.qetl.job.stream.quarantined set 0#.qetl.job.stream.quarantined;
    delete from `.qetl.stream_health.batches where job=`rpg;}

.qt.qetl.stream_replay_guard.undefine:{[]
    `.qetl.job.stream.jobs set (enlist `rpg) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set (enlist `rpg1) _ .qetl.job.stream.procnames;
    `.qetl.job.stream.trapping set 0b;
    `.qetl.job.stream.replaying set 0b;}

/ A transport replaying `msgs` through the handler start gives it, a rejected message included.
.qt.qetl.stream_replay_guard.transport:{[msgs;subscribed]
    `connect`publisher`subscribe`timer`recovery`subscribed!(
        {[] };
        {[unused] {[t;x] count x}};
        {[msgs;tbls;h;replay] if[replay; {[h;m] h . m}[h] each msgs];}[msgs];
        {[n;p;f] };
        0b;
        subscribed)}

.qt.qetl.stream_replay_guard.test_a_poison_batch_is_quarantined_and_the_replay_continues:{[t]
    .qt.qetl.stream_replay_guard.define[];
    .qetl.job.stream.start[`rpg;.qt.qetl.stream_replay_guard.transport[.qt.qetl.stream_replay_guard.log[];{[] 1b}]];
    .qunit.assertEquals[exec sym from .qpipe.job.rpg.book;`EURUSD`USDJPY`AUDUSD;
        "the batches after the rejected one are applied, as they were live"];
    q:.qetl.job.stream.quarantined;
    .qunit.assertEquals[(count q;first q`table;first q`position;first q`error);(1;`fills;2;"bad qty");
        "one batch quarantined, with its table, its position in the log and the error"];
    .qunit.assertEquals[(first q`rows)`sym;enlist `GBPUSD;"the quarantine holds the rows"];
    .qunit.assertEquals[.qetl.stream_health.of[`rpg]`failed;1;"and the failure is counted, so the job does not read healthy"];
    .qunit.assertFalse[.qetl.job.stream.trapping;"trapping ends with the replay: a live failure raises as before"];
    .qt.qetl.stream_replay_guard.undefine[]};

.qt.qetl.stream_replay_guard.test_a_live_batch_that_fails_still_raises:{[t]
    .qt.qetl.stream_replay_guard.define[];
    .qetl.job.stream.start[`rpg;.qt.qetl.stream_replay_guard.transport[();{[] 1b}]];
    .qunit.assertThrows[{[x] .qetl.job.stream.tolerant[`rpg;.qetl.job.stream.handler`rpg;`fills;x]};.qt.qetl.stream_replay_guard.log[][1;1];"bad qty";
        "live, the error reaches the caller"];
    .qt.qetl.stream_replay_guard.undefine[]};

.qt.qetl.stream_replay_guard.test_a_job_that_restarts_against_the_same_log_starts_again:{[t]
    .qt.qetl.stream_replay_guard.define[];
    tr:.qt.qetl.stream_replay_guard.transport[.qt.qetl.stream_replay_guard.log[];{[] 1b}];
    .qetl.job.stream.start[`rpg;tr];
    `.qpipe.job.rpg.book set 0#.qpipe.job.rpg.book;
    .qetl.job.stream.start[`rpg;tr];
    .qunit.assertEquals[count .qpipe.job.rpg.book;3;"the second start replays the same poison message and comes up with the same state"];
    .qt.qetl.stream_replay_guard.undefine[]};

.qt.qetl.stream_replay_guard.test_an_unsubscribed_job_is_recorded_failing_at_its_beat:{[t]
    delete from `.qetl.stream_health.batches where job=`rpgw;
    .qunit.assertTrue[.qetl.job.stream.watch_subscription[`rpgw;enlist[`subscribed]!enlist {[] 1b}];"subscribed: nothing recorded"];
    .qunit.assertEquals[.qetl.stream_health.of[`rpgw]`failed;0;"healthy"];
    .qunit.assertFalse[.qetl.job.stream.watch_subscription[`rpgw;enlist[`subscribed]!enlist {[] 0b}];"inactive subscription reported"];
    .qunit.assertEquals[(.qetl.stream_health.of[`rpgw]`failed;.qetl.stream_health.of[`rpgw]`last_error);
        (1;`$"not subscribed: the tickerplant subscription is inactive");"counted as a failed beat, with the reason"];
    .qunit.assertTrue[.qetl.job.stream.watch_subscription[`rpgw;()!()];"a transport that cannot say is taken as subscribed"]};

/ --- naming a logged message from the schema its log was written under ----

.qt.qetl.stream_replay_guard.old:([] time:`timestamp$(); sym:`symbol$(); qty:`float$())
.qt.qetl.stream_replay_guard.new:([] time:`timestamp$(); sym:`symbol$(); qty:`float$(); venue:`symbol$())

.qt.qetl.stream_replay_guard.test_messages_are_named_by_the_schema_they_were_written_under:{[t]
    cands:(.qt.qetl.stream_replay_guard.old;.qt.qetl.stream_replay_guard.new);
    ts:enlist 2026.11.06D10:00:00;
    r1:.qtorq.name_columns[cands;0;(ts;enlist `EURUSD;enlist 1f)];
    .qunit.assertEquals[(r1 0;cols r1 1);(0;`time`sym`qty);"a message of three columns is the old schema's"];
    r2:.qtorq.name_columns[cands;r1 0;(ts;enlist `EURUSD;enlist 1f;enlist `lp)];
    .qunit.assertEquals[(r2 0;cols r2 1);(1;`time`sym`qty`venue);"four columns, the new schema's"];
    .qunit.assertThrows[.qtorq.name_columns[cands;0;];(ts;enlist 1f);"*no schema recorded*";
        "a shape no recorded schema describes is refused, never padded or truncated"];
    .qunit.assertThrows[.qtorq.name_columns[cands;1;];(ts;enlist `EURUSD;enlist 1f);"*no schema recorded*";
        "and the search never goes back past the schema already in force"]};

/ A resubscription's replay delivers only what is newer than the last message received.
.qt.qetl.stream_replay_guard.test_a_resubscription_replay_delivers_each_message_once:{[t]
    .qtorq.sub_cands[`fills]:enlist .qt.qetl.stream_replay_guard.old;
    .qtorq.sub_sch_at[`fills]:0;
    `.qt.qetl.stream_replay_guard.got set ();
    h:{[t;x] `.qt.qetl.stream_replay_guard.got set .qt.qetl.stream_replay_guard.got,exec qty from x};
    tm:{[n] 2026.11.06D10:00:00+n*0D00:00:01};
    msg:{[tm;n] (enlist tm n;enlist `EURUSD;enlist "f"$n)}[tm];
    `.qtorq.sub_hwm set (enlist `fills)!enlist tm 0;
    `.qtorq.sub_since set (`symbol$())!`timestamp$();
    .qtorq.sub_upd[`rpg;h;`fills;msg 1];
    `.qtorq.sub_since set .qtorq.sub_hwm;
    / the outage: the log holds 0 and 1 (received) and 2 and 3 (missed); 1 was received, so hwm is 1
    .qtorq.sub_upd[`rpg;h;`fills;msg 0];
    .qtorq.sub_upd[`rpg;h;`fills;msg 1];
    .qtorq.sub_upd[`rpg;h;`fills;msg 2];
    .qtorq.sub_upd[`rpg;h;`fills;msg 3];
    `.qtorq.sub_since set (`symbol$())!`timestamp$();
    .qunit.assertEquals[.qt.qetl.stream_replay_guard.got;1 2 3f;"the received message once, the missed ones once, the replayed duplicates not at all"];
    .qunit.assertEquals[.qtorq.sub_hwm`fills;tm 3;"and the high-water mark is the last message"]};
