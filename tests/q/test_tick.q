// test_tick.q - tests for src/etl/core/tick.q. Load src/etl/core/tick.q,
// tests/lib/qunit.q and tests/lib/support.q before this file.


/ Where a recorder puts what it received. Reset per test, because the
/ plant is process-global state and a test that inherited the last one's
/ subscriptions would pass for the wrong reason.
.qt.qetl.tick.received:();

/ A sink that records instead of sending. This is the whole reason the
/ plant takes a callable rather than a raw handle: the tested path and the
/ shipped path are the same line of code.
.qt.qetl.tick.recorder:{[m] `.qt.qetl.tick.received set .qt.qetl.tick.received,enlist m; count m};

/ A fresh plant with two declared tables.
.qt.qetl.tick.setup:{[]
    .qetl.tick.reset[];
    `.qt.qetl.tick.received set ();
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qetl.tick.schema[`tt_quote;([] time:`timestamp$(); sym:`symbol$(); bid:`float$())];
    };

/ ---------------------------------------------------------------- SCHEMAS

.qt.qetl.tick.test_a_schema_must_lead_with_time:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([] sym:`symbol$(); px:`float$());
        "*first column must be `time*";
        "the plant stamps time at the front, so a schema without it describes rows nobody will receive"]};

.qt.qetl.tick.test_a_schema_must_be_an_empty_unkeyed_table:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([] time:enlist .z.p; sym:enlist `EURUSD);
        "*must be EMPTY*";"a schema declares a shape, not data"];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([sym:`symbol$()] time:`timestamp$());
        "*unkeyed*";"a tickerplant appends, so it has no use for a key"]};

/ ----------------------------------------------------------- SUBSCRIPTION

.qt.qetl.tick.test_subscribing_returns_the_schemas:{[t]
    .qt.qetl.tick.setup[];
    r:.qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qunit.assertEquals[key r;enlist `tt_trade;"the tables subscribed to"];
    .qunit.assertEquals[cols r`tt_trade;`time`sym`px;
        "with their shapes, so a subscriber can build its own tables without a second round trip racing the first batch"]};

.qt.qetl.tick.test_subscribing_to_everything:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertEquals[asc key .qetl.tick.subscribe[`;.qt.qetl.tick.recorder];`s#`tt_quote`tt_trade;
        "a lone backtick means every table the plant knows"]};

.qt.qetl.tick.test_a_batch_reaches_only_its_own_subscribers:{[t]
    / The bug this guards is a one-character one: `where tbl=tbl` inside a
    / qSQL clause compares the COLUMN with itself, is true for every row,
    / and sends every table to every subscriber.
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    .qetl.tick.publish[`tt_quote;([] sym:enlist `EURUSD; bid:enlist 1.0849)];
    .qunit.assertEquals[count .qt.qetl.tick.received;1;"the quote was published but nobody had asked for it"];
    .qunit.assertEquals[.qt.qetl.tick.received[0;1];`tt_trade;"and what arrived is the table that was subscribed to"]};

.qt.qetl.tick.test_subscribing_to_an_undeclared_table_is_refused:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertThrows[.qetl.tick.subscribe[;.qt.qetl.tick.recorder];`tt_nope;
        "*no schema for tt_nope*";
        "otherwise a typo in a table name is a subscriber that waits forever and reports healthy"]};

.qt.qetl.tick.test_a_sink_must_be_callable:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertThrows[.qetl.tick.subscribe[`tt_trade;];`notafunction;
        "*must be callable*";"a symbol cannot receive a batch"]};

.qt.qetl.tick.test_unsubscribing_stops_delivery:{[t]
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade`tt_quote;.qt.qetl.tick.recorder];
    .qunit.assertEquals[.qetl.tick.unsubscribe .qt.qetl.tick.recorder;2;"both subscriptions went"];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    .qunit.assertEmpty[.qt.qetl.tick.received;"and nothing arrives afterwards"]};

/ ------------------------------------------------------------- PUBLISHING

.qt.qetl.tick.test_the_plant_stamps_time_at_the_front:{[t]
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    batch:.qt.qetl.tick.received[0;2];
    .qunit.assertEquals[first cols batch;`time;
        "time leads, matching every schema in src/etl/plant_tables.q"];
    .qunit.assertEquals[count distinct exec time from batch;1;
        "one stamp for the batch, not one per row - the rows arrived together"]};

.qt.qetl.tick.test_a_publishers_own_time_is_stripped_and_replaced:{[t]
    / Stripped, not refused - matching .qtorq.publish, which strips before
    / forwarding to TorQ's stp1. The two paths must treat the same mistake
    / the same way, or a job developed against the TorQ stack fails on
    / first contact with this plant.
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    theirs:2020.01.01D00:00:00;
    .qetl.tick.publish[`tt_trade;([] time:enlist theirs; sym:enlist `EURUSD; px:enlist 1.085)];
    batch:.qt.qetl.tick.received[0;2];
    .qunit.assertEquals[cols batch;`time`sym`px;"the batch still has exactly its declared columns"];
    .qunit.assertTrue[theirs<first exec time from batch;
        "and `time` holds the plant's stamp, not the publisher's - the value is REPLACED, which is why a source's own event time must never be called `time`"]};

.qt.qetl.tick.test_a_column_list_batch_is_not_searched_for_a_time_column:{[t]
    / The strip must not evaluate `cols` on a list-of-columns batch. q's
    / `and` does not short-circuit, so the one-line spelling of this check
    / threw `type` on every feed in the tree - found live, not in review.
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];1;
        "a batch with no column names publishes without the strip looking for one"]};

.qt.qetl.tick.test_a_keyed_batch_is_refused:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_trade;];([sym:enlist `EURUSD] px:enlist 1.085);
        "*KEYED table*";
        "a tickerplant appends - upserting by key would silently drop the ticks that make a log a log"]};

.qt.qetl.tick.test_an_atom_column_is_refused:{[t]
    / The most common way a feed written against this shape goes wrong:
    / the row count comes from column length, so a one-row batch of atoms
    / reads as a one-COLUMN batch.
    .qt.qetl.tick.setup[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_trade;];(`EURUSD;1.085);
        "*is an ATOM*";"and it is caught at the door rather than landing sideways"]};

.qt.qetl.tick.test_columns_may_be_published_as_a_list:{[t]
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];1;"one row went"];
    .qunit.assertEquals[.qt.qetl.tick.received[0;2];
        ([] time:1#first exec time from .qt.qetl.tick.received[0;2]; sym:enlist `EURUSD; px:enlist 1.085);
        "and it arrives as a TABLE - a subscriber should not have to know how its upstream spelled the batch"]};

.qt.qetl.tick.test_a_column_list_needs_a_declared_schema:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_unknown;];(enlist `EURUSD;enlist 1.085);
        "*cannot name them*";
        "the plant has no way to name unlabelled columns for a table it was never told about"]};

.qt.qetl.tick.test_publishing_returns_the_row_count:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD`USDJPY; px:1.085 1.265 149.5)];3;
        "three rows in, three reported"]};

/ ------------------------------------------------------------------- LOG

/ A log directory of this test's own, removed and recreated per test so a
/ previous test's messages cannot be counted as this one's. Named for this
/ process (#1012): sessions and worktrees run the suite at once, and two runs
/ sharing one directory truncated or appended to each other's logs.
.qt.qetl.tick.log_dir:{[] "/tmp/uqf_ticktest_",string .z.i};

.qt.qetl.tick.afterNamespace_remove_the_log_dir:{[] system "rm -rf ",.qt.qetl.tick.log_dir[];}

.qt.qetl.tick.fresh_log:{[]
    system "rm -rf ",.qt.qetl.tick.log_dir[];
    .qetl.tick.reset[];
    `.qt.qetl.tick.received set ();
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qetl.tick.open_log[.qt.qetl.tick.log_dir[];`tt;2026.01.01];
    };

.qt.qetl.tick.test_a_new_log_starts_empty_and_counts_what_goes_in:{[t]
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    .qunit.assertEquals[.qetl.tick.msg_count;2;"two messages, not three rows - a message is a batch"]};

.qt.qetl.tick.test_reopening_a_log_sees_what_is_already_in_it:{[t]
    / The property that makes a restart mid-day recoverable: the messages
    / already written are the ones replay will hand back.
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    p:.qetl.tick.log_path;
    .qetl.tick.reset[];
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qunit.assertEquals[.qetl.tick.open_log[.qt.qetl.tick.log_dir[];`tt;2026.01.01];1;
        "reopening finds the message already there rather than truncating it"];
    .qunit.assertEquals[.qetl.tick.log_path;p;"and it is the same file"]};

.qt.qetl.tick.test_a_plant_with_no_log_still_publishes:{[t]
    / A service that genuinely does not need recovery should be able to
    / say so by not opening a log, rather than by writing to /dev/null.
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];1;
        "no log, and the batch still goes out"];
    .qunit.assertEquals[count .qt.qetl.tick.received;1;"to the subscriber that wanted it"]};

/ ---------------------------------------------------------------- REPLAY

.qt.qetl.tick.test_replay_hands_back_every_message_in_order:{[t]
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    `.qt.qetl.tick.seen set ();
    n:.qetl.tick.replay[.qetl.tick.log_path;{[t;r] `.qt.qetl.tick.seen set .qt.qetl.tick.seen,enlist (t;count r);}];
    .qunit.assertEquals[n;2;"both messages replayed"];
    .qunit.assertEquals[.qt.qetl.tick.seen;((`tt_trade;2);(`tt_trade;1));
        "in the order they were published, with their original batching"]};

.qt.qetl.tick.test_a_replayed_batch_is_a_table_like_a_live_one:{[t]
    / The bug this guards is subtle and only ever shows up after a
    / restart: if the log kept the raw column-list form a feed published
    / while subscribers received a table, a job's live path and its
    / recovery path would receive different shapes.
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];
    `.qt.qetl.tick.shapes set ();
    .qetl.tick.replay[.qetl.tick.log_path;{[t;r] `.qt.qetl.tick.shapes set .qt.qetl.tick.shapes,type r;}];
    .qunit.assertEquals[.qt.qetl.tick.shapes;enlist 98h;
        "published as columns, replayed as a table - the same thing a subscriber saw live"]};

/ #1071: the plant's half of a remote replay. Only the wanted tables come
/ back, in log order, and the caller is subscribed in the same call, so a
/ publish after it reaches the caller live and is not in what came back.
.qt.qetl.tick.test_replay_then_subscribe_hands_back_the_log_and_subscribes_in_one_call:{[t]
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_quote;([] sym:enlist `EURUSD; bid:enlist 1.084)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    m:.qetl.tick.replay_then_subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qunit.assertEquals[(first each m;count each last each m);(`tt_trade`tt_trade;2 1);
        "the wanted table's messages, in the order published, and no other table's"];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `AUDUSD; px:enlist 0.66)];
    .qunit.assertEquals[count .qt.qetl.tick.received;1;"and the caller now receives what is published live"]};

.qt.qetl.tick.test_replay_then_subscribe_with_no_log_only_subscribes:{[t]
    .qt.qetl.tick.setup[];
    .qunit.assertEquals[.qetl.tick.replay_then_subscribe[`tt_trade;.qt.qetl.tick.recorder];();"nothing to replay"];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    .qunit.assertEquals[count .qt.qetl.tick.received;1;"subscribed all the same"]};

.qt.qetl.tick.test_replaying_a_log_that_is_not_there_is_not_an_error:{[t]
    .qunit.assertEquals[.qetl.tick.replay[hsym `$"/tmp/uqf_ticktest_nonexistent";{[t;r] r}];0;
        "a first start has no log to replay, which is ordinary rather than exceptional"]};

.qt.qetl.tick.test_replay_restores_the_root_upd_it_found:{[t]
    / q's -11! calls `upd` at root by name, so replay has to install one.
    / Leaving it installed would send live traffic into recovery code.
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    `upd set {[tb;r] `.qt.qetl.tick.marker set `live};
    .qetl.tick.replay[.qetl.tick.log_path;{[tb;r] }];
    / `key `.`, not `key ` ` - the latter lists NAMESPACES, so it answers
    / 0b for a root variable that is sitting right there.
    .qunit.assertTrue[`upd in key `.;"the root upd is still there"];
    / `(get `upd)`, not a bare `upd`: this file used to run inside a `\d` block,
    / where an unqualified name resolves against .qt.qetl.tick first.
    (get `upd)[`x;()];
    .qunit.assertEquals[.qt.qetl.tick.marker;`live;"and it is the one that was there before, not the replay handler"];
    ![`.;();0b;enlist `upd]};

.qt.qetl.tick.test_replay_leaves_no_upd_behind_when_there_was_none:{[t]
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    if[`upd in key `.; ![`.;();0b;enlist `upd]];
    .qetl.tick.replay[.qetl.tick.log_path;{[tb;r] }];
    .qunit.assertFalse[`upd in key `.;
        "a process that had no root upd should not acquire one by replaying"]};

/ A log with two whole messages and then the start of a third that a crash
/ cut off mid-write (#1048). Closes the plant's handle first, so the junk is
/ appended to a file nothing else is writing to. Returns the log's path and
/ how many bytes the two whole messages take.
.qt.qetl.tick.torn_log:{[]
    .qt.qetl.tick.fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    p:.qetl.tick.log_path;
    good:hcount p;
    .qetl.tick.reset[];
    p 1: (read1 p),0x01020304050607;
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    (p;good)};

.qt.qetl.tick.test_reopening_a_torn_log_cuts_it_back_to_its_whole_messages:{[t]
    / -11!(-2) answers a torn log with (messages;valid bytes), not a count.
    / Storing that pair as msg_count and appending after the torn bytes put
    / every later message where no replay could reach it.
    pg:.qt.qetl.tick.torn_log[];
    n:.qetl.tick.open_log[.qt.qetl.tick.log_dir[];`tt;2026.01.01];
    .qunit.assertEquals[n;2;"the two whole messages, as a count rather than a (count;bytes) pair"];
    .qunit.assertEquals[.qetl.tick.msg_count;2;"msg_count is the count too"];
    .qunit.assertEquals[hcount first pg;last pg;"the torn bytes are cut off"]};

.qt.qetl.tick.test_messages_published_after_a_torn_log_is_reopened_replay:{[t]
    pg:.qt.qetl.tick.torn_log[];
    .qetl.tick.open_log[.qt.qetl.tick.log_dir[];`tt;2026.01.01];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `AUDUSD; px:enlist 0.66)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `NZDUSD; px:enlist 0.60)];
    .qunit.assertEquals[-11!(-2;first pg);4;"the log is whole again: four messages, no torn tail"];
    `.qt.qetl.tick.seen set ();
    n:.qetl.tick.replay[first pg;{[t;r] `.qt.qetl.tick.seen set .qt.qetl.tick.seen,enlist exec sym from r;}];
    .qunit.assertEquals[n;4;"all four replay"];
    .qunit.assertEquals[.qt.qetl.tick.seen;(`EURUSD`GBPUSD;enlist `USDJPY;enlist `AUDUSD;enlist `NZDUSD);
        "including the two written after the repair, in order"]};

.qt.qetl.tick.test_replaying_a_torn_log_hands_back_its_whole_messages:{[t]
    / A replaying job can start before the plant has reopened (and so
    / repaired) the log. It used to die with a bare 'type; it now gets the
    / whole messages, and leaves the file for the plant to repair.
    pg:.qt.qetl.tick.torn_log[];
    `.qt.qetl.tick.seen set ();
    n:.qetl.tick.replay[first pg;{[t;r] `.qt.qetl.tick.seen set .qt.qetl.tick.seen,enlist count r;}];
    .qunit.assertEquals[n;2;"the two whole messages"];
    .qunit.assertEquals[.qt.qetl.tick.seen;2 1;"replayed in order"];
    .qunit.assertEquals[-11!(-2;first pg);2,last pg;"replay reads the log; it does not rewrite it"]};

/ ----------------------------------------------------------------- RESET

.qt.qetl.tick.test_reset_forgets_everything:{[t]
    .qt.qetl.tick.setup[];
    .qetl.tick.subscribe[`tt_trade;.qt.qetl.tick.recorder];
    .qetl.tick.reset[];
    .qunit.assertEmpty[.qetl.tick.subscribers;"no subscriptions"];
    .qunit.assertEmpty[.qetl.tick.schemas;"no schemas"];
    .qunit.assertEquals[.qetl.tick.msg_count;0;"and no message count carried over"]};

.qt.qetl.tick.test_reset_forgets_which_day_the_closed_log_was_for:{[t]
    .qt.qetl.tick.fresh_log[];
    .qunit.assertEquals[.qetl.tick.plant_day[];2026.01.01;"an open log names its day"];
    .qetl.tick.reset[];
    .qunit.assertEquals[.qetl.tick.plant_day[];0Nd;"a reset plant has no day"];
    .qunit.assertEquals[.qetl.tick.day_log 2026.01.01;`;"and no log file for the day it had"];
    .qunit.assertFalse[.qetl.tick.has_log 2026.01.01;"and holds no log for it"]};
