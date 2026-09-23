// test_demo_tables.q - the tickerplant table declarations in
// scripts/processes/uqs_tables.q (.tabletest).
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
    system"l scripts/processes/uqs_tables.q";
    }

/ Every table the orchestrator's generated database.q is expected to carry.
/ A floor AND a list: a missing name is caught, and so is a new one added to
/ the q file without a thought about who consumes it.
expected:`quotes`wide_book`mkt_orderbook`databento_mbp10`databento_book`crypto_book`crypto_sim_fills`crypto_trades`trades`position`execution_quality`executions`marks`orders`fx_position`fx_limit_breach`cross_arbitrage`config_change`market_data`superbook`arbitrage`predictions`ccy_exposure`reference_data`order_routing`connections`economic_calendar

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
    ls:read0 `$":scripts/processes/uqs_tables.q";
    ls:ls where (not ls like "/*") and ls like "*:(*";
    asc `$ {x til x?":"} each ls}

/ A table's value, by name.
/ .
/ Exists because inside \d .tabletest a BARE `quotes` resolves to
/ .tabletest.quotes, which does not exist - the same namespace trap
/ .qmatz.ledger[] exists to avoid, and one this file hit on its first run.
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
    / off a quotes table, which leads with `time` because the tickerplant's
    / .u.upd requires the first column to be literally `time`. The library
    / demanded `ts` here until that was made one name tree-wide, so a
    / consumer had to rename at query time; none does now.
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


/ --- every job's declared shapes against the plant's ----------------------

/ A streaming job declares the shape of each table it exchanges with the
/ plant, so .qxf can refuse a mismatched batch at the boundary. When the
/ plant's column list changes and the job's declaration does not, the job
/ starts, subscribes, reports healthy and refuses every batch - which is
/ the failure the declaration exists to prevent, arriving silently.
/ .
/ WHICH COMPARISON APPLIES IS DERIVED, not listed. A job's own declaration
/ already says whether it reads a table or writes one:
/ .
/   subscribes  it receives rows as the plant delivers them, `time` first
/   publishes   it sends rows WITHOUT `time` - the plant stamps that
/ .
/ So the rule is `cols match` in the first case and `cols match 1_` in the
/ second, and a new job is covered the day it registers. This test used to
/ name one job (cryptoposbook1) and check its two inputs by hand; three
/ other jobs had the same exposure and no check. A gate written for an
/ instance is a gate that is true exactly once.

/ (job; table) pairs where a job declares a table sharing a plant table's
/ NAME without exchanging that table with the plant. Each needs a reason:
/ absent one, a collision is an accident waiting to mislead a reader.
/ ENLISTED. `((a;b;c))` is just `(a;b;c)` in q - a three-element list, not
/ a list of one triple - so a single entry without this reads as three
/ separate fields and matches nothing.
not_exchanged:enlist (`markout;`quotes;
    "the shape of the VENDORED `quote` table markout subscribes to (time, sym, bid, ask), held under the name its transform gives that input. The plant's own `quotes` is the depth-aware table with vector columns - a different thing that happens to be spelled plural too")

/ Private: one symbol per (job; table) pair, so a pair can be tested for
/ membership. q has no composite `in` over two columns - `([a;b]) in tbl`
/ is a `mismatch` - and rendering is clearer here than a keyed-table join.
pair:{[job;tbl] `$string[job],"/",string tbl}

/ The excused pairs, rendered.
excused:{[] pair .' 2#/:not_exchanged}

/ Every table a streaming job declares, with the role its own registration
/ implies.
job_tables:{[]
    raze {[job]
        ns:.qstream.namespace job;
        d:.qstream.declaration job;
        nms:key[ns] except `;
        nms:nms where {[ns;nm] 98h=type get ` sv ns,nm}[ns] each nms;
        ([] job:(count nms)#job; tbl:nms;
            role:{[d;nm] $[nm in d`subscribes;`subscribes;nm in d`publishes;`publishes;`internal]}[d] each nms)
      } each .qstream.registered[]}

test_every_job_declares_the_shape_the_plant_actually_carries:{[t]
    rows:select from .tabletest.job_tables[] where tbl in .tabletest.declared[];
    rows:select from rows where not .tabletest.pair'[job;tbl] in .tabletest.excused[];
    `.tabletest.bad set ();
    {[row]
        plant:cols .tabletest.tbl row`tbl;
        theirs:cols get ` sv (.qstream.namespace row`job),row`tbl;
        want:$[row[`role]=`publishes; 1_plant; plant];
        if[not theirs~want;
            `.tabletest.bad set .tabletest.bad,enlist
                (string[row`job],"/",string[row`tbl]," (",string[row`role],"): job has ",
                 (" " sv string theirs),", plant has ",(" " sv string want))]
      } each rows;
    .qunit.assertEquals[.tabletest.bad;();
        "every job's declared table matches the plant's, with `time` for one it subscribes to and without for one it publishes"]};

test_every_job_table_sharing_a_plant_name_is_exchanged_or_excused:{[t]
    / The loophole in the test above: it derives the comparison from the
    / role, and a table declared with NEITHER role is silently skipped. A
    / name collision is then invisible - which is how markout's `quotes`
    / (a different shape from the plant's `quotes`) went unnoticed.
    rows:select from .tabletest.job_tables[]
        where tbl in .tabletest.declared[], role=`internal;
    unexcused:select from rows where not .tabletest.pair'[job;tbl] in .tabletest.excused[];
    .qunit.assertEquals[count unexcused;0;
        "a job declaring a table named after a plant table, while neither subscribing nor publishing it, is a collision that needs a reason in not_exchanged"]};

test_the_exemptions_still_describe_something_real:{[t]
    / So the excuse list cannot rot into cover for the next real collision.
    live:.tabletest.job_tables[];
    stale:.tabletest.excused[] except .tabletest.pair'[live`job;live`tbl];
    .qunit.assertEquals[count stale;0;
        "every not_exchanged entry names a job and table that still exist - remove the entry rather than leaving a dead excuse"]};

bad:()
\d .
