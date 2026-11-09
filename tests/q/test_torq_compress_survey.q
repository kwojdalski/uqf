/ test_torq_compress_survey.q - what `uqs data compress` asks of TorQ's .cmp (.qt.qtorq.compress.survey, #2147, #2148).
/ .
/ The real .cmp runs, loaded per test by the .qtorq.compress suite's own fixture
/ (.qt.fixture.torq_cmp, test_torq_compress.q). What is under test is the scope - that
/ a run reaches the tables, the ages and the partitions it was given and no others,
/ with nothing still being written among them - and the survey's estimate, which
/ compresses a copy and leaves the HDB as it was.
/ .
/ The coverage ledger is replaced for each test by the days it is given, so the hold
/ is exercised without a worker; .qetl.coverage.unreleased has its own cases in
/ test_materialisation.q. KDB-X only for every case that builds an HDB: the pinned
/ PeachQ cannot write the fixture's nested enumerated column (#2153).

.qt.qtorq.compress.survey.n:20000

/ The coverage ledger's answer for this test: (dataset; date) pairs still being written.
.qt.qtorq.compress.survey.writing:([] dataset:`symbol$(); date:`date$())

.qt.qtorq.compress.survey.setUp_fresh:{[]
    .qt.fixture.torq_cmp.load[];
    `.qt.qtorq.compress.survey.saved set (.qetl.coverage.unreleased;.qtorq.reload_hdb;.qtorq.compress.apply_plan);
    `.qt.qtorq.compress.survey.writing set ([] dataset:`symbol$(); date:`date$());
    `.qetl.coverage.unreleased set {[at] .qt.qtorq.compress.survey.writing};
    `.qt.qtorq.compress.survey.reloads set 0;
    `.qtorq.reload_hdb set {[] `.qt.qtorq.compress.survey.reloads set 1+.qt.qtorq.compress.survey.reloads; `registered`opened`reloaded!1 1 1};
    }

.qt.qtorq.compress.survey.tearDown_fresh:{[]
    `.qetl.coverage.unreleased set .qt.qtorq.compress.survey.saved 0;
    `.qtorq.reload_hdb set .qt.qtorq.compress.survey.saved 1;
    `.qtorq.compress.apply_plan set .qt.qtorq.compress.survey.saved 2;
    .qt.fixture.torq_cmp.unload[];
    }

/ A fresh HDB: trade and quote on 30, 20 and 2 days ago, with a sym file.
.qt.qtorq.compress.survey.days:{[] .z.D-30 20 2}

.qt.qtorq.compress.survey.hdb:{[]
    root:`$":",first system"mktemp -d";
    n:.qt.qtorq.compress.survey.n;
    {[root;n;d]
        (` sv root,`$string[d],"/trade/") set .Q.en[root;([] sym:n?`aa`bb`cc; qty:til n; px:n#1.5; tags:n#enlist `x`y)];
        (` sv root,`$string[d],"/quote/") set .Q.en[root;([] sym:n?`aa`bb; bid:n#1.25; ask:n#1.5)];
        }[root;n] each .qt.qtorq.compress.survey.days[];
    root}

/ A config: everything after 10 days at gzip; trade's qty at qipc.
.qt.qtorq.compress.survey.conf:{[root]
    f:` sv root,`cfg.csv;
    f 0: ("table,minage,column,calgo,cblocksize,clevel";"default,10,default,2,17,6";"trade,10,qty,1,17,0");
    f}

/ Every regular file under a directory with its bytes.
.qt.qtorq.compress.survey.bytes:{[dir]
    fs:system"find ",(1_string dir)," -type f | sort";
    fs!read1 each hsym each `$fs}

.qt.qtorq.compress.survey.part:{[root;d;t] ` sv root,(`$string d),t}

.qt.qtorq.compress.survey.compressed:{[root;d;t]
    p:.qt.qtorq.compress.survey.part[root;d;t];
    {[p;c] 0<count -21!` sv p,c}[p] each get ` sv p,`.d}

/ --- scope ----------------------------------------------------------------

.qt.qtorq.compress.survey.rows:{[]
    ([] table:`default`trade; minage:10 10i; column:`default`qty; calgo:2 1i; cblocksize:17 17i; clevel:6 0i)}

.qt.qtorq.compress.survey.test_a_table_alone_shuts_the_default_and_keeps_its_own_settings:{[t]
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];`quote;0;0b];
    .qunit.assertEquals[exec minage from c where table=`default;enlist .qtorq.compress.never;"the default reaches nothing"];
    .qunit.assertEquals[exec minage from c where table=`trade;enlist .qtorq.compress.never;"nor does a table not asked for"];
    .qunit.assertEquals[select from c where table=`quote;([] table:enlist `quote; minage:enlist 10i; column:`default; calgo:2i; cblocksize:17i; clevel:6i);
        "the table asked for takes a copy of the default row under its own name"]}

.qt.qtorq.compress.survey.test_a_table_naming_only_columns_is_scoped_whole:{[t]
    / trade names only qty: its other columns take the stack's default row, which a
    / scope has to reach under trade's own name to keep them
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];`trade;0;0b];
    .qunit.assertEquals[exec minage from c where table=`trade, column=`default;enlist 10i;"trade's default columns are in"];
    .qunit.assertEquals[exec minage from c where table=`default;enlist .qtorq.compress.never;"and every other table is out"]}

.qt.qtorq.compress.survey.test_older_than_raises_every_minage_and_lowers_none:{[t]
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];();14;0b];
    .qunit.assertEquals[c`minage;14 14i;"each row at least 14 days"];
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];();3;0b];
    .qunit.assertEquals[c`minage;10 10i;"a younger bound leaves the policy's"]}

.qt.qtorq.compress.survey.test_naming_days_sets_the_policys_minage_aside:{[t]
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];`trade;0;1b];
    .qunit.assertEquals[exec minage from c where table=`trade;0 0i;"every age the days name"];
    .qunit.assertEquals[exec minage from c where table=`default;enlist .qtorq.compress.never;"a table not asked for stays out"]}

.qt.qtorq.compress.survey.test_the_policy_minage_is_the_column_then_table_then_default_row:{[t]
    c:.qt.qtorq.compress.survey.rows[],([] table:`quote`trade; minage:3 14i; column:`default`px; calgo:2 2i; cblocksize:17 17i; clevel:6 6i);
    .qunit.assertEquals[.qtorq.compress.policy_minage[c]'[`trade`quote`orders;`px`bid`x];14 3 10i;"its own row first"]}

.qt.qtorq.compress.survey.test_a_partition_that_is_not_a_date_or_month_is_refused_by_name:{[t]
    .qunit.assertThrows[.qtorq.compress.partition_of;"x2026";"qtorq.compress: x2026 is not a date or month partition";"named"];
    .qunit.assertEquals[.qtorq.compress.partition_of each ("2026-10-01";"2026.10.01";"2026-10");(2026.10.01;2026.10.01;2026.10m);"q's and ISO's spellings"]}

.qt.qtorq.compress.survey.test_a_change_cmp_would_refuse_is_refused_by_cmp:{[t]
    .qunit.assertThrows[.qtorq.compress.check_change;(enlist `calgo)!enlist 4;"*incorrect compression algo*";".cmp.checkcsv's own words"];
    .qunit.assertThrows[.qtorq.compress.check_change;(enlist `algo)!enlist 2;"qtorq.compress: a change sets calgo, cblocksize or clevel, not algo";"named"]}

/ --- survey ---------------------------------------------------------------

.qt.qtorq.compress.survey.survey:{[root;only;older;days]
    .qtorq.compress.survey[root;.qt.qtorq.compress.survey.conf[root];only;older;days;()!();1000]}

.qt.qtorq.compress.survey.test_the_survey_plans_estimates_and_writes_nothing:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    conf:.qt.qtorq.compress.survey.conf[root];
    b0:.qt.qtorq.compress.survey.bytes[root];
    s:.qtorq.compress.survey[root;conf;();0;();()!();1000];
    .qunit.assertEquals[.qt.qtorq.compress.survey.bytes[root];b0;"the HDB is byte for byte as it was"];
    p:s`partitions;
    .qunit.assertEquals[asc distinct p`partition;asc .z.D-30 20;"the partitions minage 10 reaches"];
    .qunit.assertEquals[distinct p`status;enlist `act;"nothing is being written"];
    .qunit.assertEquals[distinct p`early;enlist 0b;"none younger than the policy"];
    .qunit.assertEquals[asc first exec compress from p where table=`trade;asc `sym`qty`px`tags;"every column of trade, from the .d"];
    .qunit.assertTrue[all (p`bytes_after)<p`bytes_now;"the estimate is smaller, per partition"];
    .qunit.assertEquals[asc s[`sample]`table;`quote`trade;"one sample per table"];
    .qunit.assertEquals[distinct s[`sample]`partition;enlist .z.D-20;"from its newest planned partition"];
    .qunit.assertEquals[distinct s[`sample]`rows;enlist 1000;"of the rows asked for"];
    .qunit.assertEquals[s[`config]`table;`default`trade;"TorQ's own rows, as .cmp read them"]}

.qt.qtorq.compress.survey.test_the_dry_run_shows_exactly_the_days_named:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    p:.qt.qtorq.compress.survey.survey[root;();0;2#.z.D-20]`partitions;
    .qunit.assertEquals[distinct p`partition;enlist .z.D-20;"one day"];
    p:.qt.qtorq.compress.survey.survey[root;();0;(.z.D-25;0Nd)]`partitions;
    .qunit.assertEquals[asc distinct p`partition;.z.D-20 2;"from a day on, younger than minage included"];
    .qunit.assertEquals[exec distinct early from p where partition=.z.D-2;enlist 1b;"and said to be younger than the policy"];
    .qunit.assertEquals[exec distinct policy_minage from p where partition=.z.D-2;enlist 10i;"whose minage is 10"]}

.qt.qtorq.compress.survey.test_a_day_being_written_is_held:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-30);
    s:.qt.qtorq.compress.survey.survey[root;();0;()];
    st:exec partition!status from s[`partitions] where table=`trade;
    .qunit.assertEquals[st .z.D-30 20;`held`act;"the day being written is held; the others are not"];
    .qunit.assertEquals[exec distinct status from s[`partitions] where table=`quote;enlist `act;"another table is not held"];
    .qunit.assertEquals[exec distinct partition from s[`sample] where table=`trade;enlist .z.D-20;"only what will run is sampled"]}

.qt.qtorq.compress.survey.test_a_table_not_asked_for_is_not_surveyed:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    s:.qt.qtorq.compress.survey.survey[root;`quote;25;()];
    .qunit.assertEquals[distinct s[`partitions]`table;enlist `quote;"quote alone"];
    .qunit.assertEquals[distinct s[`partitions]`partition;enlist .z.D-30;"25 days or older"]}

.qt.qtorq.compress.survey.test_a_decompression_is_estimated_at_its_full_size:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    .qtorq.compress.apply_partition[root;.qt.qtorq.compress.survey.conf[root];`quote;0;();()!();string .z.D-30];
    f:` sv root,`plain.csv;
    f 0: ("table,minage,column,calgo,cblocksize,clevel";"default,10,default,0,0,0");
    p:select from .qtorq.compress.survey[root;f;`quote;0;();()!();1000][`partitions] where partition=.z.D-30;
    .qunit.assertEquals[asc first p`decompress;`ask`bid`sym;"every compressed column comes back"];
    .qunit.assertEquals[first p`bytes_after;sum hcount each {[root;c] ` sv root,(`$string .z.D-30),`quote,c}[root] each `sym`bid`ask;
        "at the uncompressed bytes -21! records, not today's"]}

/ --- one partition ----------------------------------------------------------

.qt.qtorq.compress.survey.run:{[root;only;days;change;d]
    .qtorq.compress.apply_partition[root;.qt.qtorq.compress.survey.conf[root];only;0;days;change;string d]}

.qt.qtorq.compress.survey.test_a_partition_run_reaches_that_partition_alone:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    r:.qt.qtorq.compress.survey.run[root;();();()!();.z.D-30];
    .qunit.assertEquals[distinct r`partition;enlist .z.D-30;"one partition"];
    .qunit.assertEquals[count r;7;"both tables' columns"];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;.z.D-30;`trade];"compressed"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-20;`trade];"the next partition is not"];
    .qunit.assertEquals[.qt.qtorq.compress.survey.reloads;0;"the caller reloads, once"];
    .qunit.assertEquals[count .qt.qtorq.compress.survey.run[root;();();()!();.z.D-30];0;"a second run changes nothing"]}

.qt.qtorq.compress.survey.test_a_named_day_younger_than_minage_is_compressed_and_no_other:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    d:.z.D-2;
    r:.qt.qtorq.compress.survey.run[root;`trade;2#d;()!();d];
    .qunit.assertEquals[distinct r`partition;enlist d;"the day named, 2 days old under a minage of 10"];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;d;`trade];"compressed"];
    .qunit.assertTrue[not any raze .qt.qtorq.compress.survey.compressed[root;;`trade] each .z.D-30 20;"no other day"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;d;`quote];"no other table"]}

.qt.qtorq.compress.survey.test_a_partition_run_never_reaches_a_day_being_written:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-30);
    r:.qt.qtorq.compress.survey.run[root;();2#.z.D-30;()!();.z.D-30];
    .qunit.assertEquals[distinct r`table;enlist `quote;"quote's partition only"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-30;`trade];"trade's is still being written"]}

.qt.qtorq.compress.survey.test_a_one_off_algorithm_rides_on_the_plans_rows:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    r:.qt.qtorq.compress.survey.run[root;`quote;();`calgo`cblocksize`clevel!1 17 0;.z.D-30];
    .qunit.assertEquals[count r;3;"quote's columns"];
    p:.qt.qtorq.compress.survey.part[root;.z.D-30;`quote];
    .qunit.assertEquals[distinct {[p;c] (-21!` sv p,c)`algorithm}[p] each `sym`bid`ask;enlist 1i;"qipc, not the policy's gzip"]}

/ --- the scheduled run (housekeeping1's hdbcompress, #2176) -----------------

.qt.qtorq.compress.survey.scheduled:{[root] .qtorq.compress.run_held[root;.qt.qtorq.compress.survey.conf[root]]}

.qt.qtorq.compress.survey.test_the_scheduled_run_never_compresses_a_day_being_written:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-30);
    held:.qt.qtorq.compress.survey.part[root;.z.D-30;`trade];
    b0:.qt.qtorq.compress.survey.bytes[held];
    r:.qt.qtorq.compress.survey.scheduled[root];
    .qunit.assertEquals[.qt.qtorq.compress.survey.bytes[held];b0;"the day being written is byte for byte as it was"];
    .qunit.assertEquals[exec count i from r where table=`trade, partition=.z.D-30;0;"and no row of the run names it"];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;.z.D-20;`trade];"a released day of the same table is compressed"];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;.z.D-30;`quote];"as is another table on the held day"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-2;`trade];"a day younger than minage is not"];
    .qunit.assertEquals[.qt.qtorq.compress.survey.reloads;1;"the HDB is reloaded once"]}

.qt.qtorq.compress.survey.test_the_scheduled_run_reads_the_hold_again_at_each_partition:{[t]
    / a backfill claims 20 days ago after the run has planned it and compressed 30 days ago
    root:.qt.qtorq.compress.survey.hdb[];
    `.qtorq.compress.apply_plan set {[f;p]
        r:f p;
        `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-20);
        r}[.qtorq.compress.apply_plan];
    r:.qt.qtorq.compress.survey.scheduled[root];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;.z.D-30;`trade];"the partition run before the claim is compressed"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-20;`trade];"the one claimed since is left alone"];
    .qunit.assertEquals[exec distinct partition from r where table=`trade;enlist .z.D-30;"and the run says so"]}

.qt.qtorq.compress.survey.test_the_scheduled_run_holds_the_hdb_write_lock_for_each_partition:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    `.qt.qtorq.compress.survey.locked set ();
    `.qtorq.compress.apply_plan set {[root;f;p]
        `.qt.qtorq.compress.survey.locked set .qt.qtorq.compress.survey.locked,not ()~key hsym `$.qetl.io.hdb_lock root;
        f p}[root;.qtorq.compress.apply_plan];
    .qt.qtorq.compress.survey.scheduled[root];
    .qunit.assertEquals[.qt.qtorq.compress.survey.locked;011b;"the empty shape outside it, then each partition under it"]}

.qt.qtorq.compress.survey.test_the_scheduled_run_selects_what_uqs_data_compress_runs:{[t]
    / the same HDB twice, one day being written: `uqs data compress --apply`'s path on
    / one (the survey, then apply_partition on each partition it marks act), the
    / schedule's on the other
    a:.qt.qtorq.compress.survey.hdb[];
    b:`$":",first system"mktemp -d";
    system"cp -R ",(1_string a),"/. ",1_string b;
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-20);
    s:.qt.qtorq.compress.survey.survey[a;();0;()];
    todo:asc exec distinct partition from s[`partitions] where status=`act, 0<count each (compress,'decompress);
    cli:raze {[a;d] .qtorq.compress.apply_partition[a;.qt.qtorq.compress.survey.conf[a];();0;();()!();string d]}[a] each todo;
    hk:.qt.qtorq.compress.survey.scheduled[b];
    shape:{[t] `table`partition`column xasc select table, partition, column, action from t};
    .qunit.assertEquals[shape hk;shape cli;"the same files, changed the same way"];
    .qunit.assertTrue[0<count hk;"and something was changed"]}
