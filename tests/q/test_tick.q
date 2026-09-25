// test_tick.q - tests for src/etl/core/tick.q. Load src/etl/core/tick.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .ticktest

/ Where a recorder puts what it received. Reset per test, because the
/ plant is process-global state and a test that inherited the last one's
/ subscriptions would pass for the wrong reason.
received:();

/ A sink that records instead of sending. This is the whole reason the
/ plant takes a callable rather than a raw handle: the tested path and the
/ shipped path are the same line of code.
recorder:{[m] `.ticktest.received set .ticktest.received,enlist m; count m};

/ A fresh plant with two declared tables.
setup:{[]
    .qetl.tick.reset[];
    `.ticktest.received set ();
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qetl.tick.schema[`tt_quote;([] time:`timestamp$(); sym:`symbol$(); bid:`float$())];
    };

/ ---------------------------------------------------------------- SCHEMAS

test_a_schema_must_lead_with_time:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([] sym:`symbol$(); px:`float$());
        "*first column must be `time*";
        "the plant stamps time at the front, so a schema without it describes rows nobody will receive"]};

test_a_schema_must_be_an_empty_unkeyed_table:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([] time:enlist .z.p; sym:enlist `EURUSD);
        "*must be EMPTY*";"a schema declares a shape, not data"];
    .qunit.assertThrows[.qetl.tick.schema[`tt_x;];([sym:`symbol$()] time:`timestamp$());
        "*unkeyed*";"a tickerplant appends, so it has no use for a key"]};

/ ----------------------------------------------------------- SUBSCRIPTION

test_subscribing_returns_the_schemas:{[t]
    setup[];
    r:.qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qunit.assertEquals[key r;enlist `tt_trade;"the tables subscribed to"];
    .qunit.assertEquals[cols r`tt_trade;`time`sym`px;
        "with their shapes, so a subscriber can build its own tables without a second round trip racing the first batch"]};

test_subscribing_to_everything:{[t]
    setup[];
    .qunit.assertEquals[asc key .qetl.tick.subscribe[`;.ticktest.recorder];`s#`tt_quote`tt_trade;
        "a lone backtick means every table the plant knows"]};

test_a_batch_reaches_only_its_own_subscribers:{[t]
    / The bug this guards is a one-character one: `where tbl=tbl` inside a
    / qSQL clause compares the COLUMN with itself, is true for every row,
    / and sends every table to every subscriber.
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    .qetl.tick.publish[`tt_quote;([] sym:enlist `EURUSD; bid:enlist 1.0849)];
    .qunit.assertEquals[count .ticktest.received;1;"the quote was published but nobody had asked for it"];
    .qunit.assertEquals[.ticktest.received[0;1];`tt_trade;"and what arrived is the table that was subscribed to"]};

test_subscribing_to_an_undeclared_table_is_refused:{[t]
    setup[];
    .qunit.assertThrows[.qetl.tick.subscribe[;.ticktest.recorder];`tt_nope;
        "*no schema for tt_nope*";
        "otherwise a typo in a table name is a subscriber that waits forever and reports healthy"]};

test_a_sink_must_be_callable:{[t]
    setup[];
    .qunit.assertThrows[.qetl.tick.subscribe[`tt_trade;];`notafunction;
        "*must be callable*";"a symbol cannot receive a batch"]};

test_unsubscribing_stops_delivery:{[t]
    setup[];
    .qetl.tick.subscribe[`tt_trade`tt_quote;.ticktest.recorder];
    .qunit.assertEquals[.qetl.tick.unsubscribe .ticktest.recorder;2;"both subscriptions went"];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    .qunit.assertEmpty[.ticktest.received;"and nothing arrives afterwards"]};

/ ------------------------------------------------------------- PUBLISHING

test_the_plant_stamps_time_at_the_front:{[t]
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    batch:.ticktest.received[0;2];
    .qunit.assertEquals[first cols batch;`time;
        "time leads, matching every schema in scripts/processes/uqs_tables.q"];
    .qunit.assertEquals[count distinct exec time from batch;1;
        "one stamp for the batch, not one per row - the rows arrived together"]};

test_a_publishers_own_time_is_stripped_and_replaced:{[t]
    / Stripped, not refused - matching .qtorq.publish, which strips before
    / forwarding to TorQ's stp1. The two paths must treat the same mistake
    / the same way, or a job developed against the TorQ stack fails on
    / first contact with this plant.
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    theirs:2020.01.01D00:00:00;
    .qetl.tick.publish[`tt_trade;([] time:enlist theirs; sym:enlist `EURUSD; px:enlist 1.085)];
    batch:.ticktest.received[0;2];
    .qunit.assertEquals[cols batch;`time`sym`px;"the batch still has exactly its declared columns"];
    .qunit.assertTrue[theirs<first exec time from batch;
        "and `time` holds the plant's stamp, not the publisher's - the value is REPLACED, which is why a source's own event time must never be called `time`"]};

test_a_column_list_batch_is_not_searched_for_a_time_column:{[t]
    / The strip must not evaluate `cols` on a list-of-columns batch. q's
    / `and` does not short-circuit, so the one-line spelling of this check
    / threw `type` on every feed in the tree - found live, not in review.
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];1;
        "a batch with no column names publishes without the strip looking for one"]};

test_a_keyed_batch_is_refused:{[t]
    setup[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_trade;];([sym:enlist `EURUSD] px:enlist 1.085);
        "*KEYED table*";
        "a tickerplant appends - upserting by key would silently drop the ticks that make a log a log"]};

test_an_atom_column_is_refused:{[t]
    / The most common way a feed written against this shape goes wrong:
    / the row count comes from column length, so a one-row batch of atoms
    / reads as a one-COLUMN batch.
    setup[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_trade;];(`EURUSD;1.085);
        "*is an ATOM*";"and it is caught at the door rather than landing sideways"]};

test_columns_may_be_published_as_a_list:{[t]
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];1;"one row went"];
    .qunit.assertEquals[.ticktest.received[0;2];
        ([] time:1#first exec time from .ticktest.received[0;2]; sym:enlist `EURUSD; px:enlist 1.085);
        "and it arrives as a TABLE - a subscriber should not have to know how its upstream spelled the batch"]};

test_a_column_list_needs_a_declared_schema:{[t]
    .qetl.tick.reset[];
    .qunit.assertThrows[.qetl.tick.publish[`tt_unknown;];(enlist `EURUSD;enlist 1.085);
        "*cannot name them*";
        "the plant has no way to name unlabelled columns for a table it was never told about"]};

test_publishing_returns_the_row_count:{[t]
    setup[];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD`USDJPY; px:1.085 1.265 149.5)];3;
        "three rows in, three reported"]};

/ ------------------------------------------------------------------- LOG

/ A log directory of this test's own, under the repository's own tmp
/ convention, removed and recreated per test so a previous run's messages
/ cannot be counted as this one's.
log_dir:{[] "/tmp/uqf_ticktest"};

fresh_log:{[]
    system "rm -rf ",log_dir[];
    .qetl.tick.reset[];
    `.ticktest.received set ();
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qetl.tick.open_log[log_dir[];`tt;2026.01.01];
    };

test_a_new_log_starts_empty_and_counts_what_goes_in:{[t]
    fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    .qunit.assertEquals[.qetl.tick.msg_count;2;"two messages, not three rows - a message is a batch"]};

test_reopening_a_log_sees_what_is_already_in_it:{[t]
    / The property that makes a restart mid-day recoverable: the messages
    / already written are the ones replay will hand back.
    fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    p:.qetl.tick.log_path;
    .qetl.tick.reset[];
    .qetl.tick.schema[`tt_trade;([] time:`timestamp$(); sym:`symbol$(); px:`float$())];
    .qunit.assertEquals[.qetl.tick.open_log[log_dir[];`tt;2026.01.01];1;
        "reopening finds the message already there rather than truncating it"];
    .qunit.assertEquals[.qetl.tick.log_path;p;"and it is the same file"]};

test_a_plant_with_no_log_still_publishes:{[t]
    / A service that genuinely does not need recovery should be able to
    / say so by not opening a log, rather than by writing to /dev/null.
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qunit.assertEquals[.qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];1;
        "no log, and the batch still goes out"];
    .qunit.assertEquals[count .ticktest.received;1;"to the subscriber that wanted it"]};

/ ---------------------------------------------------------------- REPLAY

test_replay_hands_back_every_message_in_order:{[t]
    fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:`EURUSD`GBPUSD; px:1.085 1.265)];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `USDJPY; px:enlist 149.5)];
    `.ticktest.seen set ();
    n:.qetl.tick.replay[.qetl.tick.log_path;{[t;r] `.ticktest.seen set .ticktest.seen,enlist (t;count r);}];
    .qunit.assertEquals[n;2;"both messages replayed"];
    .qunit.assertEquals[.ticktest.seen;((`tt_trade;2);(`tt_trade;1));
        "in the order they were published, with their original batching"]};

test_a_replayed_batch_is_a_table_like_a_live_one:{[t]
    / The bug this guards is subtle and only ever shows up after a
    / restart: if the log kept the raw column-list form a feed published
    / while subscribers received a table, a job's live path and its
    / recovery path would receive different shapes.
    fresh_log[];
    .qetl.tick.publish[`tt_trade;(enlist `EURUSD;enlist 1.085)];
    `.ticktest.shapes set ();
    .qetl.tick.replay[.qetl.tick.log_path;{[t;r] `.ticktest.shapes set .ticktest.shapes,type r;}];
    .qunit.assertEquals[.ticktest.shapes;enlist 98h;
        "published as columns, replayed as a table - the same thing a subscriber saw live"]};

test_replaying_a_log_that_is_not_there_is_not_an_error:{[t]
    .qunit.assertEquals[.qetl.tick.replay[hsym `$"/tmp/uqf_ticktest_nonexistent";{[t;r] r}];0;
        "a first start has no log to replay, which is ordinary rather than exceptional"]};

test_replay_restores_the_root_upd_it_found:{[t]
    / q's -11! calls `upd` at root by name, so replay has to install one.
    / Leaving it installed would send live traffic into recovery code.
    fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    `upd set {[tb;r] `.ticktest.marker set `live};
    .qetl.tick.replay[.qetl.tick.log_path;{[tb;r] }];
    / `key `.`, not `key ` ` - the latter lists NAMESPACES, so it answers
    / 0b for a root variable that is sitting right there.
    .qunit.assertTrue[`upd in key `.;"the root upd is still there"];
    / `(get `upd)`, not a bare `upd`: this file runs inside \d .ticktest,
    / where an unqualified name resolves against .ticktest first.
    (get `upd)[`x;()];
    .qunit.assertEquals[.ticktest.marker;`live;"and it is the one that was there before, not the replay handler"];
    ![`.;();0b;enlist `upd]};

test_replay_leaves_no_upd_behind_when_there_was_none:{[t]
    fresh_log[];
    .qetl.tick.publish[`tt_trade;([] sym:enlist `EURUSD; px:enlist 1.085)];
    if[`upd in key `.; ![`.;();0b;enlist `upd]];
    .qetl.tick.replay[.qetl.tick.log_path;{[tb;r] }];
    .qunit.assertFalse[`upd in key `.;
        "a process that had no root upd should not acquire one by replaying"]};

/ ----------------------------------------------------------------- RESET

test_reset_forgets_everything:{[t]
    setup[];
    .qetl.tick.subscribe[`tt_trade;.ticktest.recorder];
    .qetl.tick.reset[];
    .qunit.assertEmpty[.qetl.tick.subscribers;"no subscriptions"];
    .qunit.assertEmpty[.qetl.tick.schemas;"no schemas"];
    .qunit.assertEquals[.qetl.tick.msg_count;0;"and no message count carried over"]};

\d .
