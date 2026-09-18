// test_demo_tables.q - the tickerplant table declarations in
// scripts/processes/uqf_stack_tables.q (.tabletest).
//
// These definitions used to be Python string literals. Nothing parsed them
// as q until stp1 started, so a typo was a failed tickerplant rather than a
// failed commit. This file is the parser: loading the q file here means a
// malformed declaration fails the suite.
//
// It also holds the shapes to the functions that consume them. A table whose
// columns no longer match what src/ expects is not a syntax error - it is a
// feed publishing rows nothing downstream can read, which is the failure the
// column assertions below exist to catch.

\d .tabletest

/ Loaded here rather than in run_tests.q's list: this is the only suite that
/ needs these tables in scope, and defining nine top-level tables for every
/ other suite would put names in the root namespace that nothing else wants.
beforeNamespace_load:{[]
    system"l scripts/processes/uqf_stack_tables.q";
    }

/ Every table the orchestrator's generated database.q is expected to carry.
/ A floor AND a list: a missing name is caught, and so is a new one added to
/ the q file without a thought about who consumes it.
expected:`quotes`wide_book`mkt_orderbook`crypto_book`crypto_sim_fills`crypto_trades`trades`position`execution_quality

/ The names THIS FILE declares, read back out of it.
/ .
/ Not `tables \`` - the whole suite runs in one process, so that would also
/ return etl_coverage, worker_heartbeat and every fixture any other file
/ created, and the "no undeclared table" check below would fail on tables
/ that have nothing to do with the tickerplant.
/ .
/ The pattern deliberately contains no brackets: q's `like` treats "[...]" as
/ a CHARACTER CLASS, so the obvious "*:([]*" is an empty class and throws
/ rather than matching the literal text it looks like it matches.
declared:{[]
    ls:read0 `$":scripts/processes/uqf_stack_tables.q";
    ls:ls where (not ls like "/*") and ls like "*:(*";
    asc `$ {x til x?":"} each ls}

/ A table's value, by name.
/ .
/ Exists because inside \d .tabletest a BARE `quotes` resolves to
/ .tabletest.quotes, which does not exist - the same namespace trap
/ .qcov.ledger[] exists to avoid, and one this file hit on its first run.
/ .
/ `value nm`, not `value ` sv `,nm`: joining an empty symbol yields `.quotes`
/ with a LEADING DOT, which is a different name again and resolves to
/ nothing. A plain symbol already looks the table up at the root.
tbl:{[nm] value nm}

test_every_expected_table_is_declared:{[t]
    .qunit.assertEquals[(asc expected) except .tabletest.declared[];`symbol$();
        "every table the demo publishes into is declared in the q file"]};

test_no_undeclared_table_appears:{[t]
    / The other direction. A table added to the q file that nothing here
    / knows about is either a new capability nobody wired up, or a stray
    / definition - both worth one line of thought before it ships.
    .qunit.assertEquals[.tabletest.declared[] except expected;`symbol$();
        "the q file declares exactly the tables this test knows about"]};

/ --- shapes their consumers depend on ------------------------------------

test_quotes_matches_the_shape_pricing_expects:{[t]
    / src/pricing/forwards.q's require_quotes_cols reads these five columns
    / off a quotes table. `time` rather than `ts` because the tickerplant's
    / .u.upd requires the first column to be literally `time`; consumers
    / rename at query time.
    .qunit.assertEquals[cols .tabletest.tbl `quotes;
        `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes;
        "quotes carries the vector-column shape cross_book_at consumes"]};

test_trades_matches_the_shape_the_markout_family_expects:{[t]
    / src/execution/execution.q's markout_at_horizons input shape.
    .qunit.assertEquals[cols .tabletest.tbl `trades;
        `time`sym`side`trade_price`size`pip_factor;
        "trades carries the columns markout_at_horizons reads"]};

test_wide_book_levels_are_contiguous_from_zero:{[t]
    / .qbook.derive_level_groups finds levels by a prefix plus a CONTIGUOUS
    / digit suffix. A gap - bids0..bids4, bids6 - silently yields a shorter
    / book rather than an error, so the numbering is load-bearing.
    bids:`$"bids",/:string til 11;
    asks:`$"asks",/:string til 11;
    .qunit.assertEquals[cols .tabletest.tbl `wide_book;`time`sym,bids,asks;
        "wide_book numbers both sides 0..10 with no gap, as derive_level_groups requires"]};

test_wide_book_is_the_unfolded_counterpart_of_mkt_orderbook:{[t]
    / vectorize1 folds one into the other, so the pair only makes sense if
    / the target has the vector columns the fold produces.
    .qunit.assertEquals[cols .tabletest.tbl `mkt_orderbook;`time`sym`bid_prices`ask_prices;
        "mkt_orderbook is the folded shape .qbook.book_from_wide_levels writes"]};

test_the_two_fill_tables_do_not_collapse_into_one:{[t]
    / crypto_sim_fills is simulated, crypto_trades is real. Conflating them
    / is how a P&L number stops meaning anything, so they must stay distinct
    / tables - not merely distinct names for the same shape.
    .qunit.assertEquals[`crypto_sim_fills`crypto_trades in .tabletest.declared[];
        11b;"simulated and real fills are separate tables"]};

test_every_table_leads_with_time:{[t]
    / The tickerplant's .u.upd stamps and requires a first column literally
    / named `time`. A table that leads with anything else is published
    / wrongly, and the symptom appears in the RDB rather than at publish.
    leads:{first cols .tabletest.tbl x} each expected;
    .qunit.assertEquals[distinct leads;enlist `time;
        "every published table leads with `time, as .u.upd requires"]};

test_every_table_is_empty_as_declared:{[t]
    / These are declarations, not fixtures. A definition that arrived with
    / rows would seed the tickerplant with data nobody published.
    .qunit.assertEquals[distinct {count .tabletest.tbl x} each expected;enlist 0;
        "every declaration is an empty typed table"]};

\d .
