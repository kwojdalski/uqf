// test_attached_readers.q - the check an attached backfill makes of its HDB readers before
// readiness advances (#1773): scripts/dev/attached_readers.q. Load qunit.q and support.q first.
//
// The reader-side lambda is run in process against a real partitioned directory built with .Q.dpft,
// which is what it sees over IPC on a TorQ HDB; .Q.pv is set by hand to what a loaded database holds.

system "l scripts/dev/attached_readers.q";

.qt.qetl.attached.root:`:build/test_attached_readers

.qt.qetl.attached.cols:`time`sym`price

.qt.qetl.attached.types:"psf"

.qt.qetl.attached.d:{[n] 2026.09.10+n}

// One date's partition of table pb: the expected shape, or without price, or with price a long,
// or holding a different table altogether.
.qt.qetl.attached.write:{[d;kind]
    t:([] time:2#`timestamp$d; sym:`A`B; price:1.5 2.5);
    t:$[kind~`ok; t; kind~`no_price; `time`sym#t; kind~`long_price; update price:1 2 from t; kind~`other; ([] time:2#`timestamp$d; sym:`A`B; qty:1 2); '"kind"];
    tab:$[kind~`other; `other; `pb];
    tab set t;
    .Q.dpft[.qt.qetl.attached.root;d;`sym;tab];
    ![`.;();0b;enlist tab]}

// A database with a partition per (offset; kind) pair, and .Q.pv as a loaded database would hold it.
.qt.qetl.attached.world:{[spec]
    system "rm -rf build/test_attached_readers; mkdir -p build/test_attached_readers";
    {.qt.qetl.attached.write[.qt.qetl.attached.d x 0;x 1]} each spec;
    `.Q.pv set asc .qt.qetl.attached.d spec[;0];
    }

.qt.qetl.attached.inspect:{[dates]
    .qdev.attached.inspect[.qt.qetl.attached.root;`pb;dates;.qt.qetl.attached.cols;.qt.qetl.attached.types]}

.qt.qetl.attached.tearDown_pv:{[] system "rm -rf build/test_attached_readers"; `.Q.pv set `date$();}

.qt.qetl.attached.test_a_database_holding_every_covered_date_has_no_problem:{[t]
    .qt.qetl.attached.world[((0;`ok);(1;`ok);(2;`ok))];
    .qunit.assertEquals[.qt.qetl.attached.inspect[.qt.qetl.attached.d 0 2];();"complete partitions serve the covered dates"]};

// acceptance 1: the table is visible (the latest partition holds it) but a covered older date lacks it
.qt.qetl.attached.test_a_covered_older_date_without_the_table_is_named:{[t]
    .qt.qetl.attached.world[((0;`other);(2;`ok))];
    r:.qt.qetl.attached.inspect[.qt.qetl.attached.d 0];
    .qunit.assertEquals[r;enlist "2026.09.10: no pb directory";"the date and the table are named"]};

// acceptance 2: a covered date lacks a required column, or holds it with another type
.qt.qetl.attached.test_a_covered_date_missing_a_column_is_named:{[t]
    .qt.qetl.attached.world[((0;`no_price);(2;`ok))];
    .qunit.assertEquals[.qt.qetl.attached.inspect[.qt.qetl.attached.d 0];enlist "2026.09.10: pb lacks column(s) price";"the missing column is named"]};

.qt.qetl.attached.test_a_covered_date_with_an_incompatible_column_type_is_named:{[t]
    .qt.qetl.attached.world[((0;`long_price);(2;`ok))];
    .qunit.assertEquals[.qt.qetl.attached.inspect[.qt.qetl.attached.d 0];enlist "2026.09.10: pb has incompatible type for price";"the column is named"]};

.qt.qetl.attached.test_every_failing_date_is_reported_together:{[t]
    .qt.qetl.attached.world[((0;`other);(1;`no_price);(2;`ok))];
    r:.qt.qetl.attached.inspect[.qt.qetl.attached.d 0 1 2];
    .qunit.assertEquals[r;("2026.09.10: no pb directory";"2026.09.11: pb lacks column(s) price");"both are reported, not the first only"]};

// the contract is a query over the covered dates: an older date nobody covered may be bare
.qt.qetl.attached.test_an_uncovered_older_date_is_not_checked:{[t]
    .qt.qetl.attached.world[((0;`other);(1;`ok);(2;`ok))];
    .qunit.assertEquals[.qt.qetl.attached.inspect[.qt.qetl.attached.d 1 2];();"only the covered dates are promised"]};

.qt.qetl.attached.test_a_latest_partition_without_the_table_is_named:{[t]
    .qt.qetl.attached.world[((0;`ok);(2;`other))];
    r:.qt.qetl.attached.inspect[.qt.qetl.attached.d 0];
    .qunit.assertEquals[r;enlist "latest partition 2026.09.12 has no pb directory, so the database does not list the table";"the table list comes from the latest partition"]};

.qt.qetl.attached.test_a_database_with_no_partitions_is_refused_whole:{[t]
    `.Q.pv set `date$();
    .qunit.assertEquals[.qt.qetl.attached.inspect[.qt.qetl.attached.d 0];enlist "no date partitions are mapped: not a partitioned database";"nothing to serve from"]};

.qt.qetl.attached.test_the_expected_shape_puts_time_first_and_skips_general_columns:{[t]
    s:.qdev.attached.shape_of[([] sym:`symbol$(); bid_prices:(); price:`float$())];
    .qunit.assertEquals[s;`columns`types!(`time`sym`bid_prices`price;"ps f");"time is prepended; a nested column's type is blank"]};

.qt.qetl.attached.test_readers_are_named_by_name_host_and_port:{[t]
    r:.qdev.attached.readers enlist[`readers]!enlist enlist "hdb1@localhost:6001,hdb2@otherhost:6002";
    .qunit.assertEquals[(r`name;r`host;r`port);(`hdb1`hdb2;`localhost`otherhost;6001 6002);"each reader is name@host:port"]};

.qt.qetl.attached.test_no_readers_is_an_empty_typed_table:{[t]
    r:.qdev.attached.readers enlist[`readers]!enlist enlist "_none_";
    .qunit.assertEquals[(count r;type r`name);(0;11h);"typed, so name reads as a symbol list either way"]};

.qt.qetl.attached.test_with_no_readers_nothing_is_asked_and_nothing_fails:{[t]
    .qdev.attached.readers_named:.qdev.attached.readers enlist[`readers]!enlist enlist "_none_";
    .qdev.attached.dataset:`pb;
    .qdev.attached.reach_user:"nobody";
    r:.qdev.attached.reload_all[`date$()];
    .qunit.assertEquals[(r`registered;r`reloaded;count r`failed);(0;0;0);"there is no reader to fall short"]};

.qt.qetl.attached.test_the_reload_interval_defaults_to_thirty_seconds:{[t]
    .qunit.assertEquals[.qdev.attached.reload_interval[];0D00:00:30;"unset or invalid hdb_reload_seconds means 30"]};
