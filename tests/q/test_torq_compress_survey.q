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
    `.qt.qtorq.compress.survey.saved set (.qetl.coverage.unreleased;.qtorq.reload_hdb);
    `.qt.qtorq.compress.survey.writing set ([] dataset:`symbol$(); date:`date$());
    `.qetl.coverage.unreleased set {[at] .qt.qtorq.compress.survey.writing};
    `.qt.qtorq.compress.survey.reloads set 0;
    `.qtorq.reload_hdb set {[] `.qt.qtorq.compress.survey.reloads set 1+.qt.qtorq.compress.survey.reloads; `registered`opened`reloaded!1 1 1};
    }

.qt.qtorq.compress.survey.tearDown_fresh:{[]
    `.qetl.coverage.unreleased set .qt.qtorq.compress.survey.saved 0;
    `.qtorq.reload_hdb set .qt.qtorq.compress.survey.saved 1;
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
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];`quote;0;()!()];
    .qunit.assertEquals[exec minage from c where table=`default;enlist .qtorq.compress.never;"the default reaches nothing"];
    .qunit.assertEquals[exec minage from c where table=`trade;enlist .qtorq.compress.never;"nor does a table not asked for"];
    .qunit.assertEquals[select from c where table=`quote;([] table:enlist `quote; minage:enlist 10i; column:`default; calgo:2i; cblocksize:17i; clevel:6i);
        "the table asked for takes a copy of the default row under its own name"]}

.qt.qtorq.compress.survey.test_older_than_raises_every_minage_and_lowers_none:{[t]
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];();14;()!()];
    .qunit.assertEquals[c`minage;14 14i;"each row at least 14 days"];
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];();3;()!()];
    .qunit.assertEquals[c`minage;10 10i;"a younger bound leaves the policy's"]}

.qt.qtorq.compress.survey.test_a_table_being_written_is_held_below_its_oldest_such_day:{[t]
    held:(enlist `quote)!enlist .qtorq.compress.today[]-40;
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];();0;held];
    .qunit.assertEquals[exec minage from c where table=`quote;enlist 41i;"only partitions older than the day 40 days ago"];
    .qunit.assertEquals[exec minage from c where table<>`quote;10 10i;"no other table is held"]}

.qt.qtorq.compress.survey.test_a_partition_that_is_not_a_date_or_month_is_refused_by_name:{[t]
    .qunit.assertThrows[.qtorq.compress.age_of;"x2026";"qtorq.compress: x2026 is not a date or month partition";"named"];
    .qunit.assertEquals[.qtorq.compress.age_of "2026.10.01";.qtorq.compress.today[]-2026.10.01;"a date's age in days"]}

/ --- survey ---------------------------------------------------------------

.qt.qtorq.compress.survey.test_the_survey_plans_estimates_and_writes_nothing:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    conf:.qt.qtorq.compress.survey.conf[root];
    b0:.qt.qtorq.compress.survey.bytes[root];
    s:.qtorq.compress.survey[root;conf;0W;();0;1000];
    .qunit.assertEquals[.qt.qtorq.compress.survey.bytes[root];b0;"the HDB is byte for byte as it was"];
    p:s`partitions;
    .qunit.assertEquals[asc distinct p`partition;asc .z.D-30 20;"the partitions minage 10 reaches"];
    .qunit.assertEquals[distinct p`status;enlist `act;"nothing is being written"];
    .qunit.assertEquals[asc first exec compress from p where table=`trade;asc `sym`qty`px`tags;"every column of trade, from the .d"];
    .qunit.assertTrue[all (p`bytes_after)<p`bytes_now;"the estimate is smaller, per partition"];
    .qunit.assertEquals[asc s[`sample]`table;`quote`trade;"one sample per table"];
    .qunit.assertEquals[distinct s[`sample]`partition;enlist .z.D-20;"from its newest planned partition"];
    .qunit.assertEquals[distinct s[`sample]`rows;enlist 1000;"of the rows asked for"];
    .qunit.assertEquals[s[`config]`table;`default`trade;"TorQ's own rows, as .cmp read them"]}

.qt.qtorq.compress.survey.test_a_day_being_written_is_held_and_the_newer_ones_deferred:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    conf:.qt.qtorq.compress.survey.conf[root];
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-30);
    p:.qtorq.compress.survey[root;conf;0W;();0;1000]`partitions;
    st:exec partition!status from p where table=`trade;
    .qunit.assertEquals[st .z.D-30 20;`held`deferred;"the day being written is held; the newer one waits for it"];
    .qunit.assertEquals[exec distinct status from p where table=`quote;enlist `act;"another table is not held"];
    .qunit.assertEquals[exec distinct table from s:.qtorq.compress.survey[root;conf;0W;();0;1000]`sample;enlist `quote;
        "and only what will run is sampled"]}

.qt.qtorq.compress.survey.test_a_table_not_asked_for_is_not_surveyed:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    s:.qtorq.compress.survey[root;.qt.qtorq.compress.survey.conf[root];0W;`quote;25;1000];
    .qunit.assertEquals[distinct s[`partitions]`table;enlist `quote;"quote alone"];
    .qunit.assertEquals[distinct s[`partitions]`partition;enlist .z.D-30;"25 days or older"]}

/ --- one partition ----------------------------------------------------------

.qt.qtorq.compress.survey.test_a_partition_run_reaches_that_partition_alone_and_reloads:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    conf:.qt.qtorq.compress.survey.conf[root];
    r:.qtorq.compress.run_partition[root;conf;();0;string .z.D-30];
    .qunit.assertEquals[distinct r`partition;enlist .z.D-30;"one partition"];
    .qunit.assertEquals[count r;7;"both tables' columns"];
    .qunit.assertTrue[all .qt.qtorq.compress.survey.compressed[root;.z.D-30;`trade];"compressed"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-20;`trade];"the next partition is not"];
    .qunit.assertEquals[.qt.qtorq.compress.survey.reloads;1;"the HDB is asked to reload"];
    .qunit.assertEquals[count .qtorq.compress.run_partition[root;conf;();0;string .z.D-30];0;"a second run changes nothing"]}

.qt.qtorq.compress.survey.test_a_partition_run_never_reaches_a_day_being_written:{[t]
    root:.qt.qtorq.compress.survey.hdb[];
    conf:.qt.qtorq.compress.survey.conf[root];
    `.qt.qtorq.compress.survey.writing set ([] dataset:enlist `trade; date:enlist .z.D-30);
    r:.qtorq.compress.run_partition[root;conf;();0;string .z.D-30];
    .qunit.assertEquals[distinct r`table;enlist `quote;"quote's partition only"];
    .qunit.assertTrue[not any .qt.qtorq.compress.survey.compressed[root;.z.D-30;`trade];"trade's is still being written"]}

.qt.qtorq.compress.survey.test_a_table_naming_only_columns_is_scoped_whole:{[t]
    / trade names only qty: its other columns take the stack's default row, which a
    / scope has to reach under trade's own name to hold or keep them
    c:.qtorq.compress.scope[.qt.qtorq.compress.survey.rows[];`trade;0;()!()];
    .qunit.assertEquals[exec minage from c where table=`trade, column=`default;enlist 10i;"trade's default columns are in"];
    .qunit.assertEquals[exec minage from c where table=`default;enlist .qtorq.compress.never;"and every other table is out"]}
