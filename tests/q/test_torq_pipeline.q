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
