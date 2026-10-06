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
    h:plant `trades`quote`fx_position;
    .qunit.assertEquals[.qtorq.assert_publishable[h;`fx_position`quote];
        `fx_position`quote;
        "the declared tables are handed back unchanged, so the call can sit in a chain"]};

/ A job that publishes ONE table declares it as an atom, and `except` takes
/ a list on the left - so the unnormalised form was a 'type, and every feed
/ plus posbook would have failed to start on an assertion written to
/ protect them. Caught by this test before it reached a process.
test_one_declared_table_is_an_atom_not_a_one_element_list:{[t]
    h:plant `trades`quote`fx_position;
    .qunit.assertEquals[.qtorq.assert_publishable[h;`fx_position];
        `fx_position;
        "an atom is accepted and handed back as it came"]};

test_every_declared_table_is_checked_not_just_the_first:{[t]
    h:plant `trades`quote`fx_position;
    .qunit.assertThrows[{.qtorq.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_limit_breach*";
        "the second of two declared tables is missing and must be named"]};

test_the_refusal_names_every_missing_table_not_only_one:{[t]
    h:plant `trades`quote;
    .qunit.assertThrows[{.qtorq.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_position, fx_limit_breach*";
        "both missing tables are listed, so one restart fixes both"]};

/ The regression, stated as itself: fxpositions1's two tables against a
/ plant carrying the nineteen a generated database.q used to define.
test_the_fx_positions_pair_against_a_plant_that_forgot_them:{[t]
    h:plant `crypto_book`crypto_trades`eq_orderbook`execution_quality`executions`mkt_orderbook`orders`position`quote`quotes`trade`trades`wide_orderbook;
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
    .qunit.assertEquals[n;0;"nothing reloaded"];
    .qunit.assertTrue[any .pipetest.errs_seen like "hdb reload: could not open a handle to every registered hdb*";
        "the refusal is an error naming its likely cause"]};

test_no_hdb_running_is_not_an_error:{[t]
    saved:.qetl.log.err;
    .qetl.log.err:{[c;m;d] .pipetest.errs_seen,:enlist m};
    .pipetest.errs_seen:();
    n:.qtorq.reload_handles[0;()];
    .qetl.log.err:saved;
    .qunit.assertEquals[(n;count .pipetest.errs_seen);(0;0);"none registered: nothing to do, nothing wrong"]};

test_every_opened_hdb_is_asked_to_reload_today:{[t]
    .pipetest.reload_seen:();
    n:.qtorq.reload_handles[2;(.pipetest.hdb_ok;.pipetest.hdb_ok)];
    .qunit.assertEquals[(n;.pipetest.reload_seen);(2;2#enlist (`reload;.z.d));"both reloaded, with today's date"]};

test_a_reload_that_throws_counts_as_not_reloaded:{[t]
    saved:.qetl.log.err;
    .qetl.log.err:{[c;m;d] .pipetest.errs_seen,:enlist m};
    .pipetest.errs_seen:();
    n:.qtorq.reload_handles[2;(.pipetest.hdb_ok;.pipetest.hdb_broken)];
    .qetl.log.err:saved;
    .qunit.assertEquals[(n;.pipetest.errs_seen);(1;enlist "hdb reload failed");"one reloaded, one logged as failed"]};

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
    .qunit.assertEquals[n;2;"both registered HDBs are counted and reloaded - no hdb_type error"];
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

test_no_fallback_to_adopt_is_reported_not_guessed:{[t]
    .qunit.assertEquals[.qtorq.credential_from[();`:/no/such/metrics.txt];`source`userpass!(`missing;`);
        "missing, so the caller warns rather than connecting as torquser"]};
