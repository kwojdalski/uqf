// test_torq_pipeline.q - the TorQ adapter's startup assertions (.pipetest).
//
// scripts/processes/torq_pipeline.q is the ONE place a pipeline meets TorQ,
// and most of it cannot be exercised without a live stack: it opens
// handles, subscribes and installs timers. `assert_publishable` can, and is
// the piece worth holding, because the failure it guards is the one that
// leaves no trace anywhere.
//
// A tickerplant handed `.u.upd` for a table it does not define discards the
// rows. It does not throw, the publisher logs nothing, and the plant logs
// nothing - so a service can publish a correct answer onto a table that
// does not exist for as long as anyone leaves it running. fxpositions1 did
// exactly that, every five seconds, until #287.
//
// The handle is a FUNCTION here. `assert_publishable` uses it as `h"tables[]"`,
// which is how a q IPC handle is called, so a lambda taking that string
// stands in for a whole tickerplant.

// torq_pipeline.q is loaded by run_tests.q itself, alongside src/init.q -
// .qtorq is part of what the whole suite runs against, not something this
// file has to bring in.

\d .pipetest

/ A stand-in tickerplant that defines exactly these tables.
plant:{[known] {[known;query] $[query~"tables[]"; known; '"unexpected query: ",query]}[known]}

test_a_declared_table_the_plant_defines_is_accepted:{[t]
    h:plant[`trades`quote`fx_position];
    .qunit.assertEquals[.qtorq.assert_publishable[h;`fx_position`quote];
        `fx_position`quote;
        "the declared tables are handed back unchanged, so the call can sit in a chain"]};

/ A job that publishes ONE table declares it as an atom, and `except` takes
/ a list on the left - so the unnormalised form was a 'type, and every feed
/ plus posbook would have failed to start on an assertion written to
/ protect them. Caught by this test before it reached a process.
test_one_declared_table_is_an_atom_not_a_one_element_list:{[t]
    h:plant[`trades`quote`fx_position];
    .qunit.assertEquals[.qtorq.assert_publishable[h;`fx_position];
        `fx_position;
        "an atom is accepted and handed back as it came"]};

test_every_declared_table_is_checked_not_just_the_first:{[t]
    h:plant[`trades`quote`fx_position];
    .qunit.assertThrows[{.qtorq.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_limit_breach*";
        "the second of two declared tables is missing and must be named"]};

test_the_refusal_names_every_missing_table_not_only_one:{[t]
    h:plant[`trades`quote];
    .qunit.assertThrows[{.qtorq.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_position, fx_limit_breach*";
        "both missing tables are listed, so one restart fixes both"]};

/ The regression, stated as itself: fxpositions1's two tables against a
/ plant carrying the nineteen a generated database.q used to define.
test_the_fx_positions_pair_against_a_plant_that_forgot_them:{[t]
    h:plant[`crypto_book`crypto_trades`eq_orderbook`demo_execution_quality`executions`mkt_orderbook`orders`position`quote`quotes`trade`trades`wide_orderbook];
    .qunit.assertThrows[{.qtorq.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*discarded without an error*";
        "the message says what happens to the rows, not just that a table is absent"]};

/ A feed that publishes nothing must not be made to ask the plant anything:
/ cross1 declares no publishes, and a round trip for an empty list would be
/ a query per process start for no answer.
test_a_job_that_publishes_nothing_asks_the_plant_nothing:{[t]
    h:{[query] '"the plant must not be queried for an empty publish list"};
    .qunit.assertEquals[.qtorq.assert_publishable[h;`symbol$()];
        `symbol$();
        "no publishes means no round trip"]};

/ --- the handlers the plant calls on a period boundary -------------------

/ These exist because every uqf process was throwing `'endofperiod` once a
/ period into its own stderr log, where nothing looks (#298). What makes
/ them worth a test is that the FIRST version defined them under the wrong
/ names - `.endofperiod` rather than root `endofperiod` - which is
/ resolvable, greppable, and never called by anything. Asserting the root
/ name is the only thing that separates the two.
test_the_period_handlers_land_at_root_where_the_plant_calls_them:{[t]
    .qtorq.install_period_handlers[];
    .qunit.assertEquals[type @[get;`endofperiod;`missing];100h;
        "the plant sends (`endofperiod;x;y;z) to a ROOT name, like upd"];
    .qunit.assertEquals[type @[get;`endofday;`missing];100h;
        "and (`endofday;x;y) to another"]};

test_the_period_handlers_take_what_the_plant_sends:{[t]
    .qtorq.install_period_handlers[];
    / .stpps.endp sends three arguments and .stpps.end two; a handler of
    / the wrong arity throws exactly like a missing one, and into the same
    / log nobody reads.
    .qunit.assertEquals[count (value get `endofperiod)1;3;
        "endofperiod takes currentperiod, nextperiod and data"];
    .qunit.assertEquals[count (value get `endofday)1;2;
        "endofday takes the date and data"]};

test_installing_them_reports_what_it_defined:{[t]
    .qunit.assertEquals[.qtorq.install_period_handlers[];`endofperiod`endofday;
        "so a caller can see which names were claimed at root"]};

/ --- the bookkeeping the adapter does on every batch -----------------------

/ These need no tickerplant, which is the point: they sat uncovered next to
/ three .qtorq entries that genuinely do need one (#462), and the tempting
/ fix was to write all of them into coverage_baseline.txt together. That list
/ means "nothing covers this ON PURPOSE", so putting a pure millisecond
/ conversion into it would have been a lie that got harder to notice every
/ time someone read past it.

test_elapsed_ms_is_milliseconds_not_nanoseconds_or_seconds:{[t]
    / The unit is the only thing here that can be wrong, and it is wrong
    / silently: every log line this feeds carries a plausible number either
    / way. A lower bound with a generous ceiling rather than an equality -
    / .z.p advances between building t0 and reading it, so an exact match
    / would be a flake waiting for a slow machine. 2000 <= x < 10000 admits
    / neither 2 (seconds) nor 2000000000 (nanoseconds).
    ms:.qtorq.elapsed_ms .z.p-0D00:00:02;
    .qunit.assertEquals[type ms;-7h;"a long, which is what the log fields take"];
    .qunit.assertTrue[(ms>=2000) and ms<10000;
        "two seconds ago reads as ~2000 ms"]};

test_record_received_counts_a_table_batch_by_its_rows:{[t]
    saved:.qtorq.received;
    `.qtorq.received set (`symbol$())!`long$();
    n1:.qtorq.record_received[`trades;([] sym:`EURUSD`GBPUSD)];
    n2:.qtorq.record_received[`trades;([] sym:enlist `EURUSD)];
    total:.qtorq.received `trades;
    `.qtorq.received set saved;
    .qunit.assertEquals[(n1;n2);(2;1);"each call returns its OWN batch's rows, not the total"];
    .qunit.assertEquals[total;3;"and the counter accumulates across batches"]};

test_record_received_counts_the_columns_form_by_its_first_column:{[t]
    / A batch is a table (98h) or a list of column vectors, and the second is
    / what a feed sends. Counting that with `count x` gives the number of
    / COLUMNS - a small plausible number, wrong on every batch, and wrong in
    / a direction nothing downstream would notice.
    saved:.qtorq.received;
    `.qtorq.received set (`symbol$())!`long$();
    n:.qtorq.record_received[`quote;(`EURUSD`GBPUSD`USDJPY;1.1 1.2 150.0;1.2 1.3 150.1)];
    `.qtorq.received set saved;
    .qunit.assertEquals[n;3;"three columns of three rows is three rows, not three columns"]};

test_record_received_starts_a_table_at_zero_not_at_null:{[t]
    / `0^received t` on a table never seen before. The typed empty dictionary
    / that relies on is the one the coverage tool corrupted in #460, which is
    / how that bug reached q-unit at all - see test_coverage_tool.q.
    saved:.qtorq.received;
    `.qtorq.received set (`symbol$())!`long$();
    n:.qtorq.record_received[`never_seen_before;([] a:enlist 1)];
    total:.qtorq.received `never_seen_before;
    `.qtorq.received set saved;
    .qunit.assertEquals[(n;total);(1;1);"the first batch counts as one, not as null"]};

test_apply_verbose_reports_the_flag_this_process_was_started_with:{[t]
    / Restores the logging state it may change: debug_enabled is a process
    / global, and a suite that left DEBUG on would change what every later
    / suite prints.
    saved:.qetl.log.debug_enabled;
    on:.qtorq.apply_verbose[];
    .qetl.log.debug saved;
    .qunit.assertEquals[on;`verbose in key .Q.opt .z.x;
        "it reports whether -verbose was on this process's own start line"];
    .qunit.assertEquals[.qetl.log.debug_enabled;saved;"and this test left the setting as it found it"]};

/ --- reload reporting and the outbound credential (backfill -> HDB) ----

/ A stand-in HDB handle: records each message it is sent.
reload_seen:();
hdb_ok:{[msg] .pipetest.reload_seen,:enlist msg; ::};
hdb_broken:{[msg] '"reload exploded"};

/ The case that logged `hdbs=0` before: two HDBs registered, neither handle
/ opened (refused with `access`). Nothing reloads, and it is now an ERROR, not
/ an INFO indistinguishable from "no HDB running".
test_registered_hdbs_that_refused_reload_nothing_and_say_so:{[t]
    saved:.qetl.log.err;
    .qetl.log.err:{[c;m;d] .pipetest.errs_seen,:enlist m};
    .pipetest.errs_seen:();
    n:.qtorq.reload_handles[2;()];
    .qetl.log.err:saved;
    .qunit.assertEquals[n;`registered`opened`reloaded!2 0 0;"two registered, none reloaded - owed (#1084)"];
    .qunit.assertTrue[any .pipetest.errs_seen like "hdb reload: could not open a handle to every registered hdb*";
        "the refusal is an error naming its likely cause"]};

test_no_hdb_running_is_not_an_error:{[t]
    saved:.qetl.log.err;
    .qetl.log.err:{[c;m;d] .pipetest.errs_seen,:enlist m};
    .pipetest.errs_seen:();
    n:.qtorq.reload_handles[0;()];
    .qetl.log.err:saved;
    .qunit.assertEquals[(n;count .pipetest.errs_seen);(`registered`opened`reloaded!0 0 0;0);"none registered: nothing to do, nothing wrong"]};

test_every_opened_hdb_is_asked_to_reload_today:{[t]
    .pipetest.reload_seen:();
    n:.qtorq.reload_handles[2;(.pipetest.hdb_ok;.pipetest.hdb_ok)];
    .qunit.assertEquals[(n`reloaded;.pipetest.reload_seen);(2;2#enlist (`reload;.z.d));"both reloaded, with today's date"]};

test_a_reload_that_throws_counts_as_not_reloaded:{[t]
    saved:.qetl.log.err;
    .qetl.log.err:{[c;m;d] .pipetest.errs_seen,:enlist m};
    .pipetest.errs_seen:();
    n:.qtorq.reload_handles[2;(.pipetest.hdb_ok;.pipetest.hdb_broken)];
    .qetl.log.err:saved;
    .qunit.assertEquals[(n;.pipetest.errs_seen);(`registered`opened`reloaded!2 2 1;enlist "hdb reload failed");"one reloaded, one logged as failed"]};

/ reload_hdb itself, not only reload_handles: the tests above never reached
/ its own lookup, and that lookup is what failed - `where proctype=hdb_type`
/ inside an exec does not find .qtorq.hdb_type from a .qtorq function, so
/ every backfill's on_ready logged error="hdb_type" after writing its rows.
/ TorQ's server registry and connection lookup are stood in for, and put
/ back (or removed, when this process had none) whatever the call does.
test_reload_hdb_asks_every_registered_hdb_and_nothing_else:{[t]
    had:@[{key x};`.servers;{[e] `symbol$()}];
    keep:{[had;n] $[n in had; (1b;.servers n); (0b;::)]}[had] each `SERVERS`getservers;
    `.servers.SERVERS set ([] procname:`hdb1`hdb2`rdb1; proctype:`hdb`hdb`rdb; w:3#0Ni);
    `.pipetest.asked set ();
    / Parameter names that are not builtins: `by` (a qSQL keyword) and `attr`
    / each throw 'match as a parameter name.
    `.servers.getservers set {[field;wanted;attrs;autoopen;onlyone]
        .pipetest.asked,:enlist (field;wanted);
        ([] w:$[wanted~`hdb; (.pipetest.hdb_ok;.pipetest.hdb_ok); ()])};
    .pipetest.reload_seen:();
    n:@[.qtorq.reload_hdb;::;{[e] `threw,e}];
    {[nm;k] $[first k; (` sv `.servers,nm) set last k; ![`.servers;();0b;enlist nm]]}'[`SERVERS`getservers;keep];
    .qunit.assertEquals[n;`registered`opened`reloaded!2 2 2;"both registered HDBs are counted and reloaded - no hdb_type error"];
    .qunit.assertEquals[.pipetest.asked;enlist (`proctype;`hdb);"and only HDBs are looked up, not the rdb"];
    .qunit.assertEquals[.pipetest.reload_seen;2#enlist (`reload;.z.d);"each is sent today's reload"]};

/ Temp password files for the credential tests.
pwfile:{[name;line] f:hsym `$(first system"mktemp -d"),"/",name; f 0: enlist line; f}

test_a_process_with_no_password_file_of_its_own_adopts_the_fallback:{[t]
    fb:pwfile["metrics.txt";"metrics:pass"];
    c:.qtorq.credential_from[`:/no/such/backfill.txt`:/no/such/backfilldeals1.txt;fb];
    .qunit.assertEquals[c;`source`userpass!(`adopted;`$"metrics:pass");"the ETL identity the access list accepts"]};

test_a_password_file_of_its_own_stands:{[t]
    own:pwfile["backfill.txt";"backfill:secret"];
    fb:pwfile["metrics.txt";"metrics:pass"];
    c:.qtorq.credential_from[(own;`:/no/such/backfilldeals1.txt);fb];
    .qunit.assertEquals[c;`source`userpass!(`own;`);"a deployment's own file is not overridden"]};

/ load_source_settings asks TorQ which sources.csv its config layers select
/ (.proc.getconfigfile) and loads it (#718). TorQ is stood in for here: the
/ function is one call to the layer lookup and one to .qetl.source, and only
/ the lookup needs a TorQ process.
with_config_file:{[path;f]
    had:`getconfigfile in key `.proc;
    old:$[had; .proc.getconfigfile; ::];
    .proc.getconfigfile:{[path;name] enlist path}[path];
    r:@[f;::;{[e] (`threw;e)}];
    .qetl.source.clear_settings[];
    $[had; .proc.getconfigfile:old; ![`.proc;();0b;enlist `getconfigfile]];
    r}

test_load_source_settings_loads_the_file_the_config_layers_select:{[t]
    f:hsym `$(first system"mktemp -d"),"/sources.csv";
    f 0: ("source,transport,setting,secret_env";"demo_deals,ipc,localhost:5010,");
    / Two statements, not (load[];configured[]): a list's items evaluate right
    / to left, so that would ask what is configured before anything loaded.
    r:.pipetest.with_config_file[f;{n:.qtorq.load_source_settings[]; (n;.qetl.source.configured[])}];
    .qunit.assertEquals[r;(1;enlist `demo_deals);"one source configured, from the selected file"]};

test_load_source_settings_with_no_file_in_any_layer_configures_nothing:{[t]
    r:.pipetest.with_config_file[`:/no/such/dir/sources.csv;{.qtorq.load_source_settings[]}];
    .qunit.assertEquals[r;0;"no file is not an error: every source runs as before"]};

/ TorQ's .proc.getconfig[path;2] returns PLAIN symbols (no leading colon), and
/ key of one is () even when the file exists (#1305).
test_a_password_file_given_as_torqs_plain_symbol_stands:{[t]
    own:pwfile["backfill.txt";"backfill:secret"];
    plain:`$1_string own;
    .qunit.assertEquals[key plain;();"precondition: key sees nothing at a colon-less path"];
    fb:pwfile["metrics.txt";"metrics:pass"];
    c:.qtorq.credential_from[enlist plain;fb];
    .qunit.assertEquals[c;`source`userpass!(`own;`);"an existing file stands whichever way the path is spelled"]};

test_no_fallback_to_adopt_is_reported_not_guessed:{[t]
    .qunit.assertEquals[.qtorq.credential_from[();`:/no/such/metrics.txt];`source`userpass!(`missing;`);
        "missing, so the caller warns rather than connecting as torquser"]};

/ --- earlier_days: the logs of the days a carrying job was down (#1072) -----

/ A stand-in plant log root in TorQ's segmented layout: <root>/<plant>_<date>/,
/ one log per table and period, named <plant>_<table><time>, and a `stpmeta`
/ table listing them. Each message is what .u.upd received - (`upd;table;
/ list of columns), `time` first - appended the way the plant appends, so
/ -11! reads it back.
.pipetest.write_log:{[dir;name;msgs]
    p:` sv dir,name;
    p set ();
    h:hopen p;
    {[h;m] h enlist (`upd;m 0;m 1)}[h] each msgs;
    hclose h;
    p}

.pipetest.plant_root:{[]
    root:hsym `$first system "mktemp -d";
    d1:` sv root,`stp1_2026.10.08;
    d2:` sv root,`stp1_2026.10.09;
    system "mkdir -p ",1_string d1;
    system "mkdir -p ",1_string d2;
    / day one: positions rolled over two periods, plus a table nobody wants
    a:.pipetest.write_log[d1;`stp1_pos20261008000000;enlist (`pos;(enlist 2026.10.08D01:00:00;enlist `EURUSD;enlist 5e5))];
    b:.pipetest.write_log[d1;`stp1_pos20261008120000;enlist (`pos;(enlist 2026.10.08D13:00:00;enlist `EURUSD;enlist 1e5))];
    c:.pipetest.write_log[d1;`stp1_fill20261008000000;enlist (`fill;(enlist 2026.10.08D02:00:00;enlist `GBPUSD;enlist 2e5))];
    other_log:.pipetest.write_log[d1;`stp1_other20261008000000;enlist (`other;(enlist 2026.10.08D03:00:00;enlist `X;enlist 1e0))];
    listing:([] seq:1 2 1 1i; logname:a,b,c,other_log; tbls:(enlist `pos;enlist `pos;enlist `fill;enlist `other);
        schema:.pipetest.schema_of each (enlist `pos;enlist `pos;enlist `fill;enlist `other));
    (` sv d1,`stpmeta) set listing;
    / day two: only a fill; its stpmeta lists a log that was lost
    e:.pipetest.write_log[d2;`stp1_fill20261009000000;enlist (`fill;(enlist 2026.10.09D02:00:00;enlist `GBPUSD;enlist 3e5))];
    (` sv d2,`stpmeta) set ([] seq:1 1i; logname:e,` sv d2,`stp1_pos_lost; tbls:(enlist `fill;enlist `pos); schema:.pipetest.schema_of each (enlist `fill;enlist `pos));
    root}

/ What stpmeta records beside a log: its tables, each as an empty table.
.pipetest.schema_of:{[tbls] (),tbls!{[t] ([] time:`timestamp$(); sym:`symbol$(); qty:`float$())} each (),tbls}

test_the_plants_day_directory_is_procname_underscore_date:{[t]
    .qunit.assertEquals[.qtorq.day_dir[`$"/data/tplogs";`stp1;2026.10.09];`$":/data/tplogs/stp1_2026.10.09";"stplog.q createdld"];
    .qunit.assertEquals[.qtorq.day_dir[`$":/data/tplogs";`stp1;2026.10.09];`$":/data/tplogs/stp1_2026.10.09";"a root already a file symbol is not hsym'd twice"]};

test_earlier_days_returns_the_wanted_tables_across_days_in_order_as_tables:{[t]
    root:.pipetest.plant_root[];
    m:.qtorq.earlier_days[root;`stp1;`pos`fill;2026.10.08;2026.10.09];
    .qunit.assertEquals[m[;0];`pos`fill`pos`fill;
        "day one by time (pos 01:00, fill 02:00, pos 13:00), then day two - and the unwanted table never"];
    .qunit.assertEquals[m 0;(`pos;([] time:enlist 2026.10.08D01:00:00; sym:enlist `EURUSD; qty:enlist 5e5));
        "the columns a log holds come back named, as the table a live batch is"];
    .qunit.assertEquals[sum (raze m[;1])`qty;sum 5e5 2e5 1e5 3e5;"nothing dropped or doubled"]};

test_earlier_days_reads_only_the_days_asked_for:{[t]
    root:.pipetest.plant_root[];
    m:.qtorq.earlier_days[root;`stp1;enlist `pos;2026.10.09;2026.10.09];
    .qunit.assertEquals[m;();"day two lost its pos log, and day one was not asked for"]};

test_earlier_days_returns_what_exists_when_a_log_or_a_day_is_missing:{[t]
    root:.pipetest.plant_root[];
    m:.qtorq.earlier_days[root;`stp1;`pos`fill;2026.10.07;2026.10.10];
    .qunit.assertEquals[count m;4;"days 07 and 10 have no directory, one log of 09 is gone - the other four messages are still there"]};

test_earlier_days_with_no_log_root_is_empty_not_an_error:{[t]
    .qunit.assertEquals[.qtorq.earlier_days[`:/no/such/root;`stp1;enlist `pos;2026.10.08;2026.10.09];();"nothing to replay"]};

test_earlier_days_leaves_the_root_upd_alone:{[t]
    root:.pipetest.plant_root[];
    `upd set {[t;x] `mine};
    .qtorq.earlier_days[root;`stp1;enlist `pos;2026.10.08;2026.10.08];
    r:(get `upd)[1;2];
    ![`.;();0b;enlist `upd];
    .qunit.assertEquals[r;`mine;"the job's own upd is put back after the logs are read"]};

/ --- the recorded schema names an old log's columns (#1205) -------------------

/ A one-day plant root with one pos log, a given recorded schema and message.
.pipetest.one_log_root:{[schema;msg;withschema]
    root:hsym `$first system "mktemp -d";
    d:` sv root,`stp1_2026.10.08;
    system "mkdir -p ",1_string d;
    a:.pipetest.write_log[d;`stp1_pos20261008000000;enlist (`pos;msg)];
    (` sv d,`stpmeta) set $[withschema;
        ([] seq:enlist 1i; logname:enlist a; tbls:enlist enlist `pos; schema:enlist (enlist `pos)!enlist schema);
        ([] seq:enlist 1i; logname:enlist a; tbls:enlist enlist `pos)];
    root}

test_a_table_whose_columns_changed_between_days_is_named_by_each_days_schema:{[t]
    root:hsym `$first system "mktemp -d";
    d1:` sv root,`stp1_2026.10.08;
    d2:` sv root,`stp1_2026.10.09;
    system "mkdir -p ",1_string d1;
    system "mkdir -p ",1_string d2;
    / day one: time, sym, qty. A release then reorders them and adds a column.
    s1:([] time:`timestamp$(); sym:`symbol$(); qty:`float$());
    s2:([] time:`timestamp$(); qty:`float$(); sym:`symbol$(); venue:`symbol$());
    a:.pipetest.write_log[d1;`stp1_pos20261008000000;enlist (`pos;(enlist 2026.10.08D01:00:00;enlist `EURUSD;enlist 5e5))];
    b:.pipetest.write_log[d2;`stp1_pos20261009000000;enlist (`pos;(enlist 2026.10.09D01:00:00;enlist 7e5;enlist `GBPUSD;enlist `lse))];
    (` sv d1,`stpmeta) set ([] seq:enlist 1i; logname:enlist a; tbls:enlist enlist `pos; schema:enlist (enlist `pos)!enlist s1);
    (` sv d2,`stpmeta) set ([] seq:enlist 1i; logname:enlist b; tbls:enlist enlist `pos; schema:enlist (enlist `pos)!enlist s2);
    m:.qtorq.earlier_days[root;`stp1;enlist `pos;2026.10.08;2026.10.09];
    .qunit.assertEquals[m 0;(`pos;([] time:enlist 2026.10.08D01:00:00; sym:enlist `EURUSD; qty:enlist 5e5));
        "the old log is named as it was written"];
    .qunit.assertEquals[m 1;(`pos;([] time:enlist 2026.10.09D01:00:00; qty:enlist 7e5; sym:enlist `GBPUSD; venue:enlist `lse));
        "and the new one by its own schema, with the extra column - not by either day's guess"]};

test_a_log_that_disagrees_with_its_recorded_schema_is_refused_with_both_counts:{[t]
    s:([] time:`timestamp$(); sym:`symbol$(); qty:`float$());
    root:.pipetest.one_log_root[s;(enlist 2026.10.08D01:00:00;enlist `EURUSD);1b];
    .qunit.assertThrows[{.qtorq.earlier_days[x;`stp1;enlist `pos;2026.10.08;2026.10.08]};root;
        "*recorded 3 columns for pos (time, sym, qty) but its messages hold 2*";
        "never padded or truncated"]};

test_an_stpmeta_with_no_schema_is_refused_not_guessed_from_the_plant:{[t]
    s:([] time:`timestamp$(); sym:`symbol$(); qty:`float$());
    root:.pipetest.one_log_root[s;(enlist 2026.10.08D01:00:00;enlist `EURUSD;enlist 5e5);0b];
    .qunit.assertThrows[{.qtorq.earlier_days[x;`stp1;enlist `pos;2026.10.08;2026.10.08]};root;
        "*records no schema*";
        "loud, naming the file"]};

/ --- the plant's day and its logs, for catch_up (#1211) ------------------------

/ A plant that answers for `root` while its current day directory is `dldir`.
.pipetest.ask:{[root;dldir] {[r;d;unused] (r;`stp1;d)}[root;dldir]}

test_the_day_a_plant_directory_is_for_is_the_date_it_ends_in:{[t]
    .qunit.assertEquals[.qtorq.dir_day `$":/data/tplogs/stp1_2026.10.09";2026.10.09;"the plant's own naming"]};

test_plant_day_is_the_day_of_the_plants_current_log_directory:{[t]
    root:.pipetest.plant_root[];
    hooks:.qtorq.plant_hooks[.pipetest.ask[root;` sv root,`stp1_2026.10.08];`pos`fill];
    .qunit.assertEquals[hooks[`plant_day][];2026.10.08;"asked of the plant, not read off the job's clock"]};

test_has_log_is_true_for_a_day_with_a_log_and_false_for_one_without:{[t]
    root:.pipetest.plant_root[];
    hooks:.qtorq.plant_hooks[.pipetest.ask[root;` sv root,`stp1_2026.10.09];`pos`fill];
    .qunit.assertEquals[hooks[`has_log] 2026.10.08;1b;"day one has its logs"];
    .qunit.assertEquals[hooks[`has_log] 2026.10.07;0b;"no directory, no log"];
    h2:.qtorq.plant_hooks[.pipetest.ask[root;` sv root,`stp1_2026.10.09];enlist `pos];
    .qunit.assertEquals[h2[`has_log] 2026.10.09;0b;"day two's only pos log was lost: the file must be there, not just listed"]};

/ The #1190 case on TorQ: the job's clock is a day ahead of a plant that has
/ not rolled. Logs for the 8th and the 9th, plant still on the 9th, marker the
/ 7th: the 9th is the log `subscribe` replays, so catch_up reads the 8th only.
test_a_torq_restart_between_local_midnight_and_the_plant_roll_replays_each_log_once:{[t]
    root:.pipetest.plant_root[];
    hooks:.qtorq.plant_hooks[.pipetest.ask[root;` sv root,`stp1_2026.10.09];`pos`fill];
    p:.qetl.job.stream.carry_marker_path `fx_positions;
    system "mkdir -p ",.qetl.job.bounded.state.lock_dir[];
    system "rm -f ",p,"*";
    .qetl.job.bounded.state.durable_set[p;([] last_end_of_day:enlist 2026.10.07)];
    .pipetest.replayed:();
    h:{[tb;x] .pipetest.replayed,:enlist (tb;count x)};
    n:.qetl.job.stream.catch_up[`fx_positions;hooks;`pos`fill;h];
    system "rm -f ",p,"*";
    .qetl.job.stream.reset `fx_positions;
    .qunit.assertEquals[n;3;"day one's three messages (pos, fill, pos); day two is the live log's"];
    .qunit.assertEquals[sum .pipetest.replayed[;1];3;"each row once"]};

/ --- the hooks exist before connect has run (#1219) -------------------------

/ The TorQ stream process's transport, loaded from the process file itself:
/ the definitions between its switch into .qproc.stream and the switch back,
/ without the lines that start the process. Each line starting in a letter (a
/ top-level definition) is
/ qualified instead of entered by a context switch, so it loads the same
/ where nested contexts are not supported.
.pipetest.load_stream_transport:{[]
    ls:read0 `:scripts/processes/torq_stream.q;
    / the first definition; a flattened copy has no context switch to find
    from_i:first where ls like "*which_job:{*";
    to_i:first where (ls like "?d .") and (til count ls)>from_i;
    body:from_i _ to_i # ls;
    body:{$[(first x) in .Q.a; ".qproc.stream.",x; x]} each body;
    f:hsym `$(first system "mktemp -d"),"/stream_transport.q";
    f 0: body;
    system "l ",1_string f}

test_a_transport_built_before_connect_has_its_recovery_hooks_and_they_answer_after:{[t]
    .pipetest.load_stream_transport[];
    / as at process start: nothing has set the handle
    if[`h in key `.qproc.stream; delete h from `.qproc.stream];
    root:.pipetest.plant_root[];
    dir:` sv root,`stp1_2026.10.09;
    saved:.qtorq.connect_etl;
    `.pipetest.connected set 0;
    .qtorq.connect_etl:{[root;dir;job] `.pipetest.connected set 1+.pipetest.connected; {[root;dir;q] (root;`stp1;dir)}[root;dir]}[root;dir];
    subs:.qetl.job.stream.subscriptions;
    .qetl.job.stream.subscriptions:{[job] `pos`fill};  / the tables this stand-in plant logs
    tr:.qproc.stream.transport `fx_positions;
    .qetl.job.stream.subscriptions:subs;
    .qunit.assertEquals[.pipetest.connected;0;"building the transport does not connect: start does, after on_start"];
    .qunit.assertEquals[all `earlier_days`plant_day`has_log in key tr;1b;"the hooks are there at build time, with the handle unset"];
    / connect, which is the only thing that sets the handle
    tr[`connect][];
    .qtorq.connect_etl:saved;
    .qunit.assertEquals[.pipetest.connected;1;"connect connects, once"];
    .qunit.assertEquals[tr[`plant_day][];2026.10.09;"asked of the plant now that there is one"];
    .qunit.assertEquals[tr[`has_log] 2026.10.08;1b;"and the plant's day-one logs are there"];
    .qunit.assertEquals[tr[`has_log] 2026.10.07;0b;"and none for a day without a directory"];
    .qunit.assertEquals[count tr[`earlier_days][`pos`fill;2026.10.08;2026.10.08];3;"and the earlier days read from them"]}

test_a_plant_that_cannot_answer_costs_the_call_not_the_job:{[t]
    saved:.qetl.log.warn;
    .qetl.log.warn:{[c;m;d] .pipetest.warned,:enlist m};
    .pipetest.warned:();
    hooks:.qtorq.plant_hooks[{[unused] '"no .stplg.dldir"};enlist `pos];
    r:(hooks[`earlier_days][`pos;2026.10.08;2026.10.08]; hooks[`plant_day][]; hooks[`has_log] 2026.10.08);
    .qetl.log.warn:saved;
    .qunit.assertEquals[r;(();.z.D;0b);"no earlier days, the job's own clock, no log"];
    .qunit.assertEquals[count .pipetest.warned;3;"each costing a warning"]}
