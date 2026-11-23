// test_once.q - the --once contract: one unit of a job, then an outcome (.qetl.once,
// src/etl/core/once.q, bounded_once.q and stream_once.q, #2798).
//
// A runner ends the process, so the exit is replaced with a recorder: what the runner WOULD have
// exited with is read back as data. Everything else is the real thing - the real bounded
// lifecycle on demo_deals_backfill (windows d1..d4, with its real reaction), and real streaming
// jobs started on a fake transport that records the timers asked for and the publishes made.

.qt.qetl.once.d:{[n] 2026.09.10D00:00:00.000000000+(n*1D)}

/ the exits a runner made, in order, and the exit it replaced
.qt.qetl.once.exits:`long$()
.qt.qetl.once.saved_exit:{[c] }
.qt.qetl.once.saved_io:()!()

/ what the fake transport carried to the plant: table, rows
.qt.qetl.once.sent:([] tbl:`symbol$(); n:`long$())
.qt.qetl.once.timers_asked:`symbol$()
.qt.qetl.once.timer_calls:0
.qt.qetl.once.batches:0

.qt.qetl.once.jobs:`once_feed`once_timer`once_sub`once_boom
.qt.qetl.once.procs:`once_feed1`once_timer1`once_sub1`once_boom1

.qt.qetl.once.record_exit:{[c] `.qt.qetl.once.exits set .qt.qetl.once.exits,`long$c; c}

.qt.qetl.once.send:{[t;x] `.qt.qetl.once.sent upsert (t;count x); count x}

.qt.qetl.once.upstream:([] ts:.qt.qetl.once.d[1 2 3]; v:1.5 2.5 3.5)
.qt.qetl.once.after:{[cursor] select from .qt.qetl.once.upstream where ts>cursor}

.qt.qetl.once.declare:{[]
    .qetl.job.stream.define[`once_feed;`procname`subscribe_to`outputs`period`poll!(
        `once_feed1;`symbol$();enlist `once_out;0D00:00:01;
        `fetch`normalize`next_cursor`close!(.qt.qetl.once.after;{[page] select ts, v from page};{[page] last page`ts};{[] }))];
    .qetl.job.stream.define[`once_timer;`procname`subscribe_to`outputs`period`on_timer!(
        `once_timer1;`symbol$();enlist `once_out;0D00:00:01;{[] `.qt.qetl.once.timer_calls set 1+.qt.qetl.once.timer_calls})];
    .qetl.job.stream.define[`once_sub;`procname`subscribe_to`outputs`on_batch`period`on_timer!(
        `once_sub1;enlist `once_in;enlist `once_out;
        {[t;x] `.qt.qetl.once.batches set 1+.qt.qetl.once.batches; .qpipe.job.once_sub.publish[`once_out;x]};
        0D00:00:05;{[] `.qt.qetl.once.timer_calls set 1+.qt.qetl.once.timer_calls})];
    }

.qt.qetl.once.forget:{[]
    `.qetl.job.stream.jobs set .qt.qetl.once.jobs _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set .qt.qetl.once.procs _ .qetl.job.stream.procnames;
    .qetl.job.continuous.clear_cursor each .qt.qetl.once.jobs;}

/ A transport that carries nothing anywhere: the timers it is asked for and the publishes made
/ through it are recorded, and the handler the job subscribes with is kept to send a batch to.
.qt.qetl.once.live:{[t;x] }
.qt.qetl.once.transport:{[]
    `connect`publisher`subscribe`timer`recovery!(
        {[] };
        {[unused] .qt.qetl.once.send};
        {[tbls;h;replay] `.qt.qetl.once.live set h; ()};
        {[n;p;f] `.qt.qetl.once.timers_asked set .qt.qetl.once.timers_asked,n; ()};
        0b)}

.qt.qetl.once.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-once"];
    system"mkdir -p build/test-status-once";
    }

.qt.qetl.once.reset_recorders:{[]
    `.qt.qetl.once.exits set `long$();
    `.qt.qetl.once.sent set 0#.qt.qetl.once.sent;
    `.qt.qetl.once.timers_asked set `symbol$();
    `.qt.qetl.once.timer_calls set 0;
    `.qt.qetl.once.batches set 0;
    `.qt.qetl.once.live set {[t;x] };
    }

.qt.qetl.once.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    .qt.qetl.once.forget[];
    .qt.qetl.once.declare[];
    .qt.qetl.once.reset_recorders[];
    `.qt.qetl.once.saved_exit set .qetl.once.exit;
    .qetl.once.exit:.qt.qetl.once.record_exit;
    .qetl.job.bounded.once.withdraw[];
    .qetl.job.bounded.state.release_lock `demo_deals_backfill;
    .qetl.job.bounded.state.clear_checkpoint `demo_deals_backfill;
    `demo_deals set 0#.qpipe.source.demo_deals.fixture[];
    .qetl.run.release[];
    .qetl.reaction.reset_outcomes[];
    .qetl.reaction.history:.qetl.reaction.empty_history[];
    @[hdel;hsym `$.qetl.once.path `once_feed1;{[e] (::)}];
    `.qt.qetl.once.saved_io set .qetl.io.default;
    .qetl.io.default:`write`write_keyed!(.qetl.io.write_memory;.qetl.io.write_memory_keyed);
    }

.qt.qetl.once.tearDown_restore:{[]
    .qetl.once.exit:.qt.qetl.once.saved_exit;
    .qetl.io.default:.qt.qetl.once.saved_io;
    .qetl.job.bounded.once.withdraw[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qt.qetl.once.forget[];
    }

/ --- the vocabulary ---------------------------------------------------------------------------

.qt.qetl.once.test_each_outcome_has_its_own_exit_code:{[t]
    .qunit.assertEquals[.qetl.once.codes`done`failed`stopped`no_work;0 1 4 6i;"done 0, failed 1, stopped 4, no_work 6"];
    .qunit.assertEquals[count distinct value .qetl.once.codes;count .qetl.once.codes;"no two outcomes share a code"];
    .qunit.assertEquals[.qetl.once.codes`stopped;.qetl.job.bounded.stop.code;"stopped is the bounded run's own code, not a second number"];
    .qunit.assertEquals[(key .qetl.once.codes)~.qetl.once.outcomes;1b;"every outcome has a code"]};

.qt.qetl.once.test_the_wait_for_input_is_bounded_and_named:{[t]
    .qunit.assertEquals[.qetl.once.timeout_from_flags (`symbol$())!();.qetl.once.default_timeout;"a wait has a default"];
    .qunit.assertEquals[.qetl.once.timeout_from_flags .Q.opt ("-once_timeout";"90");0D00:01:30.000000000;"in seconds"];
    .qunit.assertThrows[.qetl.once.timeout_from_flags;.Q.opt ("-once_timeout";"0.0");"once: -once_timeout must be positive, got 0.0";"zero waits for nothing"];
    .qunit.assertThrows[.qetl.once.timeout_from_flags;.Q.opt ("-once_timeout";"soon");"once: -once_timeout must be a number of seconds, got \"soon\"";"a word is not a wait"];
    .qunit.assertThrows[.qetl.once.timeout_from_flags;.Q.opt ("-once_timeout";"999999");"once: -once_timeout 999999 is past the longest wait, 3600 seconds";"and it has a ceiling"]};

.qt.qetl.once.test_every_kind_names_a_runner_that_exists:{[t]
    .qunit.assertEquals[.qetl.once.unowned[];`symbol$();"each kind's termination is owned by a defined framework function"];
    .qunit.assertEquals[exec kind from .qetl.once.kinds;`poll`timer`subscriber`bounded`reaction;"the five units the contract names"];
    .qunit.assertEquals[exec waits from .qetl.once.kinds;00100b;"only a subscriber waits for input"]};

.qt.qetl.once.test_every_declared_job_resolves_to_a_kind_with_a_runner:{[t]
    d:.qetl.once.declared[];
    .qunit.assertEquals[count d;count .qetl.job.bounded.defined[],.qetl.job.stream.defined[];"every bounded worker and streaming job"];
    bad:exec job from d where not kind in exec kind from .qetl.once.kinds;
    .qunit.assertEquals[bad;`symbol$();"a declared job whose kind the contract does not know would have no --once"];
    .qunit.assertEquals[.qetl.once.kind_of each `once_feed`once_timer`once_sub`demo_deals_backfill;`poll`timer`subscriber`bounded;
        "a poll, a timer, a subscriber and a worker"];
    .qunit.assertThrows[.qetl.once.kind_of;`no_such_job;"once: no_such_job is not a declared streaming job, bounded worker or reaction";"an unknown job is refused by name"]};

.qt.qetl.once.test_a_reaction_is_a_kind_of_its_own:{[t]
    .qunit.assertEquals[.qetl.once.kind_of `rebuild_positions;`reaction;"a reaction has a unit under its parent, and no process of its own"];
    .qunit.assertEquals[.qetl.once.kinds[`reaction]`standalone;0b;"so it is never launched by itself"]};

/ --- the outcome file -------------------------------------------------------------------------

.qt.qetl.once.test_an_outcome_is_written_atomically_and_reads_back:{[t]
    ctx:.qetl.once.context[`once_feed1;`once_feed;`poll];
    .qetl.once.conclude[ctx;`no_work;"no_new_data";0;0;"cursor x"];
    doc:.j.k raze read0 hsym `$.qetl.once.path `once_feed1;
    .qunit.assertEquals[key doc;.qetl.once.fields;"every field, in the order q names them"];
    .qunit.assertEquals[(doc`outcome;doc`code;doc`reason;doc`kind;doc`job);("no_work";6f;"no_new_data";"poll";"once_feed");"what the launcher reads"];
    .qunit.assertEquals[.qt.qetl.once.exits;enlist 6;"and the process left with the same code"];
    .qunit.assertEquals[()~key hsym `$(.qetl.once.path `once_feed1),".tmp";1b;"no half-written file is left beside it"]};

.qt.qetl.once.test_an_unknown_outcome_is_refused:{[t]
    ctx:.qetl.once.context[`once_feed1;`once_feed;`poll];
    .qunit.assertThrows[.qetl.once.conclude[ctx;;"";0;0;""];`maybe;"once: unknown outcome maybe - expected one of done, failed, stopped, no_work";"an outcome outside the vocabulary"]};

/ --- a bounded worker: one planned window -------------------------------------------------------

.qt.qetl.once.run_worker:{[]
    .qpipe.job.demo_deals_backfill.init `source_version`range_from`range_to!(`v1;.qt.qetl.once.d[1];.qt.qetl.once.d[4]);
    .qpipe.job.demo_deals_backfill.run[]}

.qt.qetl.once.test_a_once_run_does_the_first_planned_window_only:{[t]
    .qetl.job.bounded.once.request `demo_deals_backfill;
    r:.qt.qetl.once.run_worker[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;1;0);"one window of three"];
    fv:`$"v1~fixture";
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;fv;.z.p;.qt.qetl.once.d[1];.qt.qetl.once.d[2]];1b;"the first window is covered"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`demo_deals;`;fv;.z.p;.qt.qetl.once.d[2];.qt.qetl.once.d[4]];0b;"the rest are not, so the next run plans them"];
    .qunit.assertEquals[r`cursor;.qt.qetl.once.d[2];"the checkpoint is the end of the window that ran"]};

.qt.qetl.once.test_successive_once_runs_walk_the_range_and_then_find_no_work:{[t]
    states:{[i] .qetl.job.bounded.once.request `demo_deals_backfill; r:.qt.qetl.once.run_worker[]; .qpipe.job.demo_deals_backfill.cleanup[]; r`state} each til 4;
    .qunit.assertEquals[states;`completed`completed`completed`idle;"three windows, one per run, then nothing left"];
    .qunit.assertEquals[.qetl.job.bounded.once.outcome each states;`done`done`done`no_work;"done, done, done, then no_work"]};

.qt.qetl.once.test_the_request_is_consumed_by_the_run_it_was_for:{[t]
    .qetl.job.bounded.once.request `demo_deals_backfill;
    .qt.qetl.once.run_worker[];
    .qunit.assertEquals[.qetl.job.bounded.once.target;`;"nothing is left asking for a second one-window run"];
    .qpipe.job.demo_deals_backfill.cleanup[];
    r:.qt.qetl.once.run_worker[];
    .qunit.assertEquals[r`windows_completed;2;"the next run, with no request, does the two windows that remain"]};

.qt.qetl.once.test_a_request_for_another_worker_leaves_the_windows_alone:{[t]
    w:([] range_from:.qt.qetl.once.d[1 2];range_to:.qt.qetl.once.d[2 3]);
    .qetl.job.bounded.once.request `some_other_worker;
    .qunit.assertEquals[count .qetl.job.bounded.once.limit[`demo_deals_backfill;w];2;"without --once nothing changes"];
    .qunit.assertEquals[.qetl.job.bounded.once.target;`some_other_worker;"and the other worker's request is still waiting"]};

.qt.qetl.once.test_the_windows_reaction_runs_once_under_its_parents_window:{[t]
    .qetl.job.bounded.once.request `demo_deals_backfill;
    .qt.qetl.once.run_worker[];
    h:select from .qetl.reaction.history where name=`rebuild_positions;
    .qunit.assertEquals[(count h;first h`outcome);(1;`ok);"one invocation, for the one window the parent ran"]};

.qt.qetl.once.test_a_stopped_once_run_is_stopped_and_not_done:{[t]
    .qunit.assertEquals[.qetl.job.bounded.once.outcome each `completed`idle`partial`failed`stopped;`done`no_work`failed`failed`stopped;
        "a partial run is a failure, as it is for an orchestrator"]};

.qt.qetl.once.test_the_bounded_outcome_reaches_the_file_and_the_exit:{[t]
    ctx:.qetl.once.context[`demo_deals_backfill1;`demo_deals_backfill;`bounded];
    .qetl.job.bounded.once.conclude[ctx;`state`windows_completed`rows_published!(`idle;0;0)];
    .qetl.job.bounded.once.conclude[ctx;`state`error!(`failed;"boom")];
    doc:.j.k raze read0 hsym `$.qetl.once.path `demo_deals_backfill1;
    @[hdel;hsym `$.qetl.once.path `demo_deals_backfill1;{[e] (::)}];
    .qunit.assertEquals[.qt.qetl.once.exits;6 1;"no_work then failed"];
    .qunit.assertEquals[(doc`outcome;doc`detail);("failed";"boom");"the failure carries the error"]};

/ A range the plan clips to nothing - wholly after now, so past what any source vouches for - is not
/ "covered": it is no_work (nothing to do NOW, exit 6, as an empty poll is) with its own reason,
/ never all_covered (#2929).
.qt.qetl.once.conclude_doc:{[r]
    ctx:.qetl.once.context[`demo_deals_backfill1;`demo_deals_backfill;`bounded];
    .qetl.job.bounded.once.conclude[ctx;r];
    doc:.j.k raze read0 hsym `$.qetl.once.path `demo_deals_backfill1;
    @[hdel;hsym `$.qetl.once.path `demo_deals_backfill1;{[e] (::)}];
    doc}

.qt.qetl.once.run_future:{[]
    f:`timestamp$.z.d+2;
    .qetl.job.bounded.once.request `demo_deals_backfill;
    .qpipe.job.demo_deals_backfill.init `source_version`range_from`range_to!(`v1;f;f+2D);
    .qpipe.job.demo_deals_backfill.run[]}

.qt.qetl.once.test_a_range_clipped_to_nothing_is_no_work_not_complete_never_all_covered:{[t]
    r:.qt.qetl.once.run_future[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`idle;0);"nothing planned: the range is after now"];
    doc:.qt.qetl.once.conclude_doc r;
    .qunit.assertEquals[(doc`outcome;doc`reason;doc`code);("no_work";"not_complete";6f);"no work now, and the reason says the range is not complete - not that it is covered"];
    .qunit.assertEquals[.qetl.job.bounded.read_state[`demo_deals_backfill;`clipped_to]<`timestamp$.z.d+2;1b;"the plan kept where it was cut"];
    .qunit.assertEquals[.qt.qetl.once.exits;enlist 6;"the no_work code"]};

.qt.qetl.once.test_a_covered_range_after_a_clipped_run_is_all_covered_again:{[t]
    .qt.qetl.once.run_future[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qt.qetl.once.run_worker[];
    .qpipe.job.demo_deals_backfill.cleanup[];
    .qetl.job.bounded.once.request `demo_deals_backfill;
    r:.qt.qetl.once.run_worker[];
    .qunit.assertEquals[r`state;`idle;"the range is covered"];
    .qunit.assertEquals[null .qetl.job.bounded.read_state[`demo_deals_backfill;`clipped_to];1b;"an uncut plan leaves no clip from the run before"];
    .qunit.assertEquals[(.qt.qetl.once.conclude_doc r)`reason;"all_covered";"so it is reported covered"]};

/ --- a polling feed: one poll -----------------------------------------------------------------

.qt.qetl.once.test_a_poll_publishes_one_page_and_exits_done:{[t]
    r:.qetl.job.stream.once.run[`once_feed;.qt.qetl.once.transport[];0D00:01:00];
    .qunit.assertEquals[(r`outcome;r`code;r`rows;r`units);(`done;0i;3;1);"one page, three rows"];
    .qunit.assertEquals[.qt.qetl.once.sent;([] tbl:enlist `once_out;n:enlist 3);"published once"];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor `once_feed;.qt.qetl.once.d[3];"and the cursor saved after it"];
    .qunit.assertEquals[.qt.qetl.once.exits;enlist 0;"the process left with the done code"]};

.qt.qetl.once.test_the_poll_timer_is_captured_and_never_scheduled:{[t]
    .qetl.job.stream.once.run[`once_feed;.qt.qetl.once.transport[];0D00:01:00];
    .qunit.assertEquals[.qt.qetl.once.timers_asked inter `once_feed;`symbol$();"the job's own timer did not reach the scheduler"]};

.qt.qetl.once.test_a_poll_with_nothing_new_is_no_work_and_publishes_nothing:{[t]
    .qetl.job.continuous.save_cursor[`once_feed;.qt.qetl.once.d[3]];
    r:.qetl.job.stream.once.run[`once_feed;.qt.qetl.once.transport[];0D00:01:00];
    .qunit.assertEquals[(r`outcome;r`code;r`reason;r`rows);(`no_work;6i;"no_new_data";0);"no page is not a failure and not a success"];
    .qunit.assertEquals[count .qt.qetl.once.sent;0;"nothing is fabricated"]};

.qt.qetl.once.test_a_poll_that_throws_is_failed_with_the_error:{[t]
    `.qetl.job.stream.jobs set .qetl.job.stream.jobs;
    d:.qetl.job.stream.def `once_feed;
    p:d`poll;
    p[`fetch]:{[c] '"upstream refused"};
    d[`poll]:p;
    d[`on_timer]:{[job;unused] .qetl.job.stream.tick job}[`once_feed];
    `.qetl.job.stream.jobs set .qetl.job.stream.jobs,enlist[`once_feed]!enlist enlist d;
    r:.qetl.job.stream.once.run[`once_feed;.qt.qetl.once.transport[];0D00:01:00];
    .qunit.assertEquals[(r`outcome;r`code;r`reason);(`failed;1i;"upstream refused");"the error is the reason"];
    .qunit.assertEquals[count .qt.qetl.once.sent;0;"and nothing was published"]};

/ --- a timer job: one cycle -----------------------------------------------------------------------

.qt.qetl.once.test_a_timer_cycle_runs_once_even_when_it_publishes_nothing:{[t]
    r:.qetl.job.stream.once.run[`once_timer;.qt.qetl.once.transport[];0D00:01:00];
    .qunit.assertEquals[(r`outcome;r`rows;r`units);(`done;0;1);"a cycle that found nothing to emit still ran"];
    .qunit.assertEquals[.qt.qetl.once.timer_calls;1;"the job's timer body ran exactly once"];
    .qunit.assertEquals[count .qt.qetl.once.sent;0;"and no row was made up"]};

/ --- a subscriber: one input batch ---------------------------------------------------------------

.qt.qetl.once.a_batch:{[] ([] sym:`EURUSD`GBPUSD; px:1.1 1.3)}

.qt.qetl.once.test_a_subscriber_waits_and_arms_a_timeout:{[t]
    r:.qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    .qunit.assertEquals[r;`waiting;"it returns to the event loop, which delivers the batch"];
    .qunit.assertEquals[.qt.qetl.once.timers_asked;enlist `once_sub_once_wait;"the one timer scheduled is the bounded wait"];
    .qunit.assertEquals[count .qt.qetl.once.exits;0;"and it has not ended"]};

.qt.qetl.once.test_the_first_live_batch_ends_the_run_done:{[t]
    .qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    .qt.qetl.once.live[`once_in;.qt.qetl.once.a_batch[]];
    doc:.j.k raze read0 hsym `$.qetl.once.path `once_sub1;
    @[hdel;hsym `$.qetl.once.path `once_sub1;{[e] (::)}];
    .qunit.assertEquals[(.qt.qetl.once.batches;.qt.qetl.once.exits);(1;enlist 0);"the batch was processed, then the process left done"];
    .qunit.assertEquals[(doc`outcome;doc`rows);("done";2f);"the rows it published are counted"];
    .qunit.assertEquals[.qt.qetl.once.timer_calls;0;"the job's own timer was not run to flush anything"]};

.qt.qetl.once.test_a_second_batch_does_not_end_a_second_run:{[t]
    .qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    .qt.qetl.once.live[`once_in;.qt.qetl.once.a_batch[]];
    .qetl.job.stream.once.expire[`once_sub;::];
    .qunit.assertEquals[.qt.qetl.once.exits;enlist 0;"the timeout after the batch ended nothing a second time"]};

.qt.qetl.once.test_a_replayed_batch_is_state_and_not_input:{[t]
    .qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    `.qetl.job.stream.replaying set 1b;
    .qt.qetl.once.live[`once_in;.qt.qetl.once.a_batch[]];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[(.qt.qetl.once.batches;count .qt.qetl.once.exits);(1;0);"the job rebuilt from it, and the run kept waiting"]};

.qt.qetl.once.test_a_batch_for_a_table_that_is_not_an_input_does_not_count:{[t]
    .qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    .qt.qetl.once.live[`once_other;.qt.qetl.once.a_batch[]];
    .qunit.assertEquals[count .qt.qetl.once.exits;0;"only the declared inputs are the unit"]};

.qt.qetl.once.test_no_input_before_the_deadline_is_no_work_with_its_own_reason:{[t]
    .qetl.job.stream.once.run[`once_sub;.qt.qetl.once.transport[];0D00:00:30];
    .qunit.assertEquals[.qetl.job.stream.once.expire[`once_sub;::];0b;"inside the timeout the wait goes on"];
    .qetl.job.stream.once.state[`deadline]:.z.p-0D00:00:01;
    .qunit.assertEquals[.qetl.job.stream.once.expire[`once_sub;::];1b;"past it, the wait ends"];
    doc:.j.k raze read0 hsym `$.qetl.once.path `once_sub1;
    @[hdel;hsym `$.qetl.once.path `once_sub1;{[e] (::)}];
    .qunit.assertEquals[(doc`outcome;doc`reason;doc`code);("no_work";"input_timeout";6f);"no_work, and the reason says it was the wait - not a cancelled query"];
    .qunit.assertEquals[.qt.qetl.once.timer_calls;0;"nothing was flushed to finish"]};

.qt.qetl.once.test_a_batch_the_job_throws_on_is_a_failure:{[t]
    .qetl.job.stream.define[`once_boom;`procname`subscribe_to`outputs`on_batch!(`once_boom1;enlist `once_in;enlist `once_out;{[t;x] '"bad row"})];
    .qetl.job.stream.once.run[`once_boom;.qt.qetl.once.transport[];0D00:00:30];
    .[.qt.qetl.once.live;(`once_in;.qt.qetl.once.a_batch[]);{[e] (::)}];
    doc:.j.k raze read0 hsym `$.qetl.once.path `once_boom1;
    @[hdel;hsym `$.qetl.once.path `once_boom1;{[e] (::)}];
    .qunit.assertEquals[(doc`outcome;doc`reason;.qt.qetl.once.exits);("failed";"bad row";enlist 1);"the error is the reason, and the code is failed's"]};

/ --- a failure to start ---------------------------------------------------------------------------

.qt.qetl.once.test_a_job_that_will_not_start_is_failed:{[t]
    tr:.qt.qetl.once.transport[];
    tr[`connect]:{[] '"no tickerplant"};
    r:.qetl.job.stream.once.run[`once_feed;tr;0D00:01:00];
    .qunit.assertEquals[(r`outcome;r`reason);(`failed;"start failed: no tickerplant");"a transport that will not connect is a failure, not a hang"]};

.qt.qetl.once.test_a_bounded_worker_is_refused_by_the_stream_runner:{[t]
    .qunit.assertThrows[.qetl.job.stream.once.run[`demo_deals_backfill;;0D00:01:00];.qt.qetl.once.transport[];
        "once: demo_deals_backfill is a bounded job, which the stream runner does not run";"each kind has one runner"]};

/ --- a real process: the exit is the outcome's code ----------------------------------------------
/ Every test above replaces the exit. This one does not: a process that runs a poll once and
/ leaves, as torq_stream.q's does, with the real `exit` and the real outcome file.

/ Run one poll in a child q, with the cursor already at `cursor_day` (0 for none); returns
/ (the exit code; the outcome document).
.qt.qetl.once.child:{[cursor_day]
    root:first system "mktemp -d";
    script:("\\l src/init.q";"\\l src/etl/init.q";
        "setenv[`UQF_STATUS_DIR;\"",root,"/st\"];";
        "system \"mkdir -p ",root,"/st\";";
        "d:{[n] 2026.09.10D00:00:00.000000000+n*1D};";
        "up:([] ts:d each 1 2 3; v:1.5 2.5 3.5);";
        "if[",string[cursor_day]," > 0; .qetl.job.continuous.save_cursor[`once_child;d ",string[cursor_day],"]];";
        ".qetl.job.stream.define[`once_child;`procname`subscribe_to`outputs`period`poll!(`once_child1;`symbol$();enlist `once_out;0D00:00:01;`fetch`normalize`next_cursor`close!({[c] select from up where ts>c};{[p] select ts, v from p};{[p] last p`ts};{[] }))];";
        "tr:`connect`publisher`subscribe`timer`recovery!({[] };{[u] {[t;x] count x}};{[a;h;r] ()};{[n;p;f] ()};0b);";
        ".qetl.job.stream.once.run[`once_child;tr;0D00:01:00];";
        "exit 99");
    (hsym `$root,"/child.q") 0: script;
    code:"J"$first system "env ",$[""~getenv `QCMD; "q"; getenv `QCMD]," ",root,"/child.q -q < /dev/null > ",root,"/out.txt 2>&1; echo $?";
    path:root,"/st/once_once_child1.json";
    (code;$[()~key hsym `$path; (); .j.k raze read0 hsym `$path])}

.qt.qetl.once.test_a_real_process_leaves_with_done_after_one_poll:{[t]
    r:.qt.qetl.once.child[0];
    .qunit.assertEquals[r 0;0;"the process exited with the done code, not the 99 after the runner"];
    .qunit.assertEquals[((r 1)`outcome;(r 1)`rows);("done";3f);"and wrote the outcome the launcher reads"]};

.qt.qetl.once.test_a_real_process_leaves_with_the_no_work_code_when_the_poll_is_empty:{[t]
    r:.qt.qetl.once.child[3];
    .qunit.assertEquals[r 0;6;"no page: exit 6, distinct from done and from failed"];
    .qunit.assertEquals[((r 1)`outcome;(r 1)`reason);("no_work";"no_new_data");"and says why"]};
