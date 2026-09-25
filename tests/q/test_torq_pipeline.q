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
// .qpipe is part of what the whole suite runs against, not something this
// file has to bring in.

\d .pipetest

/ A stand-in tickerplant that defines exactly these tables.
plant:{[known] {[known;query] $[query~"tables[]"; known; '"unexpected query: ",query]}[known]}

test_a_declared_table_the_plant_defines_is_accepted:{[t]
    h:plant `trades`quote`fx_position;
    .qunit.assertEquals[.qpipe.assert_publishable[h;`fx_position`quote];
        `fx_position`quote;
        "the declared tables are handed back unchanged, so the call can sit in a chain"]};

/ A job that publishes ONE table declares it as an atom, and `except` takes
/ a list on the left - so the unnormalised form was a 'type, and every feed
/ plus posbook would have failed to start on an assertion written to
/ protect them. Caught by this test before it reached a process.
test_one_declared_table_is_an_atom_not_a_one_element_list:{[t]
    h:plant `trades`quote`fx_position;
    .qunit.assertEquals[.qpipe.assert_publishable[h;`fx_position];
        `fx_position;
        "an atom is accepted and handed back as it came"]};

test_every_declared_table_is_checked_not_just_the_first:{[t]
    h:plant `trades`quote`fx_position;
    .qunit.assertThrows[{.qpipe.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_limit_breach*";
        "the second of two declared tables is missing and must be named"]};

test_the_refusal_names_every_missing_table_not_only_one:{[t]
    h:plant `trades`quote;
    .qunit.assertThrows[{.qpipe.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*fx_position, fx_limit_breach*";
        "both missing tables are listed, so one restart fixes both"]};

/ The regression, stated as itself: fxpositions1's two tables against a
/ plant carrying the nineteen a generated database.q used to define.
test_the_fx_positions_pair_against_a_plant_that_forgot_them:{[t]
    h:plant `crypto_book`crypto_trades`databento_book`execution_quality`executions`marks`mkt_orderbook`orders`position`quote`quotes`trade`trades`wide_book;
    .qunit.assertThrows[{.qpipe.assert_publishable[x;`fx_position`fx_limit_breach]};h;
        "*discarded without an error*";
        "the message says what happens to the rows, not just that a table is absent"]};

/ A feed that publishes nothing must not be made to ask the plant anything:
/ cross1 declares no publishes, and a round trip for an empty list would be
/ a query per process start for no answer.
test_a_job_that_publishes_nothing_asks_the_plant_nothing:{[t]
    h:{[query] '"the plant must not be queried for an empty publish list"};
    .qunit.assertEquals[.qpipe.assert_publishable[h;`symbol$()];
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
    .qpipe.install_period_handlers[];
    .qunit.assertEquals[type @[get;`endofperiod;`missing];100h;
        "the plant sends (`endofperiod;x;y;z) to a ROOT name, like upd"];
    .qunit.assertEquals[type @[get;`endofday;`missing];100h;
        "and (`endofday;x;y) to another"]};

test_the_period_handlers_take_what_the_plant_sends:{[t]
    .qpipe.install_period_handlers[];
    / .stpps.endp sends three arguments and .stpps.end two; a handler of
    / the wrong arity throws exactly like a missing one, and into the same
    / log nobody reads.
    .qunit.assertEquals[count (value get `endofperiod)1;3;
        "endofperiod takes currentperiod, nextperiod and data"];
    .qunit.assertEquals[count (value get `endofday)1;2;
        "endofday takes the date and data"]};

test_installing_them_reports_what_it_defined:{[t]
    .qunit.assertEquals[.qpipe.install_period_handlers[];`endofperiod`endofday;
        "so a caller can see which names were claimed at root"]};

/ --- the bookkeeping the adapter does on every batch -----------------------

/ These need no tickerplant, which is the point: they sat uncovered next to
/ three .qpipe entries that genuinely do need one (#462), and the tempting
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
    ms:.qpipe.elapsed_ms .z.p-0D00:00:02;
    .qunit.assertEquals[type ms;-7h;"a long, which is what the log fields take"];
    .qunit.assertTrue[(ms>=2000) and ms<10000;
        "two seconds ago reads as ~2000 ms"]};

test_record_received_counts_a_table_batch_by_its_rows:{[t]
    saved:.qpipe.received;
    `.qpipe.received set (`symbol$())!`long$();
    n1:.qpipe.record_received[`trades;([] sym:`EURUSD`GBPUSD)];
    n2:.qpipe.record_received[`trades;([] sym:enlist `EURUSD)];
    total:.qpipe.received `trades;
    `.qpipe.received set saved;
    .qunit.assertEquals[(n1;n2);(2;1);"each call returns its OWN batch's rows, not the total"];
    .qunit.assertEquals[total;3;"and the counter accumulates across batches"]};

test_record_received_counts_the_columns_form_by_its_first_column:{[t]
    / A batch is a table (98h) or a list of column vectors, and the second is
    / what a feed sends. Counting that with `count x` gives the number of
    / COLUMNS - a small plausible number, wrong on every batch, and wrong in
    / a direction nothing downstream would notice.
    saved:.qpipe.received;
    `.qpipe.received set (`symbol$())!`long$();
    n:.qpipe.record_received[`quote;(`EURUSD`GBPUSD`USDJPY;1.1 1.2 150.0;1.2 1.3 150.1)];
    `.qpipe.received set saved;
    .qunit.assertEquals[n;3;"three columns of three rows is three rows, not three columns"]};

test_record_received_starts_a_table_at_zero_not_at_null:{[t]
    / `0^received t` on a table never seen before. The typed empty dictionary
    / that relies on is the one the coverage tool corrupted in #460, which is
    / how that bug reached q-unit at all - see test_coverage_tool.q.
    saved:.qpipe.received;
    `.qpipe.received set (`symbol$())!`long$();
    n:.qpipe.record_received[`never_seen_before;([] a:enlist 1)];
    total:.qpipe.received `never_seen_before;
    `.qpipe.received set saved;
    .qunit.assertEquals[(n;total);(1;1);"the first batch counts as one, not as null"]};

test_apply_verbose_reports_the_flag_this_process_was_started_with:{[t]
    / Restores the logging state it may change: debug_enabled is a process
    / global, and a suite that left DBG on would change what every later
    / suite prints.
    saved:.qlog.debug_enabled;
    on:.qpipe.apply_verbose[];
    .qlog.debug saved;
    .qunit.assertEquals[on;`verbose in key .Q.opt .z.x;
        "it reports whether -verbose was on this process's own start line"];
    .qunit.assertEquals[.qlog.debug_enabled;saved;"and this test left the setting as it found it"]};
