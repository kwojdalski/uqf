// test_stack_tables.q - the tickerplant table declarations in
// src/etl/plant_tables.q (.qt.qetl.plant.tables).
//
// These definitions used to be Python string literals. Nothing parsed them
// as q until stp1 started, so a typo was a failed tickerplant rather than a
// failed commit. The runner loads the q file (src/etl/init.q), so a
// malformed declaration fails the run before any suite does.
//
// It also holds the shapes to the functions that consume them. A table whose
// columns no longer match what src/ expects is not a syntax error - it is a
// feed publishing rows nothing downstream can read, which is the failure the
// column assertions below exist to catch.


/ Materialised here rather than for every suite: this is the only suite that
/ needs these tables in scope, and defining them as top-level tables for every
/ other suite would put names in the root namespace that nothing else wants.
/ .
/ The file itself is NOT loaded again: src/etl/init.q has loaded it - so a
/ malformed declaration already fails the run - and a second load redefined
/ every .qetl.plant function after the coverage gate had instrumented it, and
/ emptied the registries other suites declare into. .qetl.plant.equivalence
/ then read as never entered, though test_verify_compare.q enters it (#1891).
.qt.qetl.plant.tables.beforeNamespace_load:{[]
    `.qt.qetl.plant.tables.made set .qetl.plant.own[] except tables `.;
    .qetl.plant.materialise .qetl.plant.own[];
    }

/ And dropped again after, the ones this suite made: a root demo_deals left
/ behind is where the memory io manager writes, so a later verify suite's
/ backfill was refused for a `time` column its batch does not carry (#1891).
.qt.qetl.plant.tables.afterNamespace_drop:{[]
    {[t] ![`.;();0b;enlist t]} each .qt.qetl.plant.tables.made;
    }

/ Every table the orchestrator's generated database.q is expected to carry.
/ A floor AND a list: a missing name is caught, and so is a new one added to
/ the q file without a thought about who consumes it.
.qt.qetl.plant.tables.expected:`fx_orderbook`wide_orderbook`mkt_orderbook`kafka_message`kafka_dead_letter`crypto_book`crypto_sim_fills`crypto_trades`trades`position`executions`orders`fx_position`fx_limit_breach`fx_position_open`position_open`cross_arbitrage`config_change`market_data`superbook`arbitrage`predictions`ccy_exposure`reference_data`order_routing`connections`economic_calendar`duckdb_deals`client_flow`deal_positions`trades_copy`crypto_markout`exec_bar`crypto_book_1s`book_liquidity_1m`last_value`order_quality`order_outcomes`fx_markout`fx_markout_wide`demo_deals

/ Tables an installed sidecar bundle added, read from its ledger
/ (src/etl/installed_bundles.json). The bundle defines and describes them
/ itself, so they are not this list's to hold; a checkout with no bundle
/ installed has no ledger, and nothing is exempt.
.qt.qetl.plant.tables.bundled:{[] f:`:src/etl/installed_bundles.json;
    $[()~key f; `symbol$(); `symbol$(),`$raze {x`tables} each value .j.k raze read0 f]}

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
.qt.qetl.plant.tables.declared:{[]
    ls:read0 `$":src/etl/plant_tables.q";
    / The file defines each table qualified from the root (.qetl.plant.quote:...).
    ls:{$[x like ".qetl.plant.*"; 12_x; x]} each ls;
    / `name:([]` exactly, as uqs and .qetl.plant.adopt_vendored read it: a
    / looser "*:(*" also caught the keyed `elements:([table:...]...)` lookup.
    ls:ls where (not ls like "/*") and {[l] (0<count l) and "([]"~3#(1+l?":")_l} each ls;
    asc `$ {x til x?":"} each ls}

/ A table's value, by name.
/ .
/ Exists because inside the old `\d` block of this suite a BARE `fx_orderbook` resolves to
/ .qt.qetl.plant.tables.fx_orderbook, which does not exist - the same namespace trap
/ .qetl.coverage.ledger[] exists to avoid, and one this file hit on its first run.
/ .
/ `value nm`, not `value ` sv `,nm`: joining an empty symbol yields `.fx_orderbook`
/ with a LEADING DOT, which is a different name again and resolves to
/ nothing. A plain symbol already looks the table up at the root.
.qt.qetl.plant.tables.tbl:{[nm] value nm}

.qt.qetl.plant.tables.test_every_expected_table_is_declared:{[t]
    .qunit.assertEquals[(asc .qt.qetl.plant.tables.expected) except .qt.qetl.plant.tables.declared[];`symbol$();
        "every table the demo publishes into is declared in the q file"]};

.qt.qetl.plant.tables.test_no_undeclared_table_appears:{[t]
    / The other direction. A table added to the q file that nothing here
    / knows about is either a new capability nobody wired up, or a stray
    / definition - both worth one line of thought before it ships.
    / The failure is the prompt (#352), so it says what to do: `uqs job new`
    / adds its table to `expected` itself, and a table added by hand needs
    / the same one line once someone has decided it should ship.
    .qunit.assertEquals[.qt.qetl.plant.tables.declared[] except .qt.qetl.plant.tables.expected,.qt.qetl.plant.tables.bundled[];`symbol$();
        "a table in src/etl/plant_tables.q that this test does not know - if it is meant to ship, add it to `expected` in tests/q/test_stack_tables.q; if not, remove it"]};

/ --- shapes their consumers depend on ------------------------------------

.qt.qetl.plant.tables.test_fx_orderbook_matches_the_shape_pricing_expects:{[t]
    / .qf.util.require_depth_quotes reads these five columns
    / off a quotes table, which leads with `time` because the tickerplant's
    / .u.upd requires the first column to be literally `time`. The library
    / demanded `ts` here until that was made one name tree-wide, so a
    / consumer had to rename at query time; none does now.
    .qunit.assertEquals[cols .qt.qetl.plant.tables.tbl `fx_orderbook;
        `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes;
        "fx_orderbook carries the vector-column shape cross_book_at consumes"]};

.qt.qetl.plant.tables.test_trades_matches_the_shape_the_markout_family_expects:{[t]
    / src/execution/execution.q's markout_at_horizons input shape.
    .qunit.assertEquals[cols .qt.qetl.plant.tables.tbl `trades;
        `time`sym`side`trade_price`size`pip_factor`fill_id;
        "trades carries the columns markout_at_horizons reads"]};

.qt.qetl.plant.tables.test_wide_book_levels_are_contiguous_from_zero:{[t]
    / .qf.mkt.derive_level_groups finds levels by a prefix plus a CONTIGUOUS
    / digit suffix. A gap - bids0..bids4, bids6 - silently yields a shorter
    / book rather than an error, so the numbering is load-bearing.
    bids:`$"bids",/:string til 11;
    asks:`$"asks",/:string til 11;
    .qunit.assertEquals[cols .qt.qetl.plant.tables.tbl `wide_orderbook;`time`sym,bids,asks;
        "wide_orderbook numbers both sides 0..10 with no gap, as derive_level_groups requires"]};

.qt.qetl.plant.tables.test_wide_book_is_the_unfolded_counterpart_of_mkt_orderbook:{[t]
    / vectorize1 folds one into the other, so the pair only makes sense if
    / the target has the vector columns the fold produces.
    .qunit.assertEquals[cols .qt.qetl.plant.tables.tbl `mkt_orderbook;`time`sym`bid_prices`ask_prices;
        "mkt_orderbook is the folded shape .qf.mkt.book_from_wide_levels writes"]};

.qt.qetl.plant.tables.test_the_two_fill_tables_do_not_collapse_into_one:{[t]
    / crypto_sim_fills is simulated, crypto_trades is real. Conflating them
    / is how a P&L number stops meaning anything, so they must stay distinct
    / tables - not merely distinct names for the same shape.
    .qunit.assertEquals[`crypto_sim_fills`crypto_trades in .qt.qetl.plant.tables.declared[];
        11b;"simulated and real fills are separate tables"]};

.qt.qetl.plant.tables.test_every_table_leads_with_time:{[t]
    / The tickerplant's .u.upd stamps and requires a first column literally
    / named `time`. A table that leads with anything else is published
    / wrongly, and the symptom appears in the RDB rather than at publish.
    leads:{first cols .qt.qetl.plant.tables.tbl x} each .qt.qetl.plant.tables.expected;
    .qunit.assertEquals[distinct leads;enlist `time;
        "every published table leads with `time, as .u.upd requires"]};

.qt.qetl.plant.tables.test_every_table_is_empty_as_declared:{[t]
    / These are declarations, not fixtures. A definition that arrived with
    / rows would seed the tickerplant with data nobody published.
    .qunit.assertEquals[distinct {count .qt.qetl.plant.tables.tbl x} each .qt.qetl.plant.tables.expected;enlist 0;
        "every declaration is an empty typed table"]};


/ --- every job's declared shapes against the plant's ----------------------

/ A streaming job declares the shape of each table it exchanges with the
/ plant, so .qetl.transform can refuse a mismatched batch at the boundary. When the
/ plant's column list changes and the job's declaration does not, the job
/ starts, subscribes, reports healthy and refuses every batch - which is
/ the failure the declaration exists to prevent, arriving silently.
/ .
/ WHICH COMPARISON APPLIES IS DERIVED, not listed. A job's own declaration
/ already says whether it reads a table or writes one:
/ .
/   subscribe_to  it receives rows as the plant delivers them, `time` first
/   publishes     it sends rows WITHOUT `time` - the plant stamps that
/ .
/ So the rule is `cols match` in the first case and `cols match 1_` in the
/ second, and a new job is covered the day it registers. This test used to
/ name one job (cryptoposbook1) and check its two inputs by hand; three
/ other jobs had the same exposure and no check. A gate written for an
/ instance is a gate that is true exactly once.

/ (job; table) pairs where a job declares a table sharing a plant table's
/ NAME without exchanging that table with the plant. Each needs a reason:
/ absent one, a collision is an accident waiting to mislead a reader.
/ ENLISTED when it holds one entry. `((a;b;c))` is just `(a;b;c)` in q - a
/ three-element list, not a list of one triple - so a single entry without it
/ reads as three separate fields and matches nothing.
/ .
/ Empty. markout's `quotes` input was here while the plant's depth table was
/ also called `quotes`; renaming that table fx_orderbook ended the collision.
.qt.qetl.plant.tables.not_exchanged:()

/ Private: one symbol per (job; table) pair, so a pair can be tested for
/ membership. q has no composite `in` over two columns - `([a;b]) in tbl`
/ is a `mismatch` - and rendering is clearer here than a keyed-table join.
.qt.qetl.plant.tables.pair:{[job;tbl] `$string[job],"/",string tbl}

/ The excused pairs, rendered.
.qt.qetl.plant.tables.excused:{[] .qt.qetl.plant.tables.pair .' 2#/:.qt.qetl.plant.tables.not_exchanged}

/ Every table a streaming job declares, with the role its own registration
/ implies.
.qt.qetl.plant.tables.job_tables:{[]
    raze {[job]
        ns:.qetl.job.stream.namespace job;
        d:.qetl.job.stream.def job;
        nms:key[ns] except `;
        nms:nms where {[ns;nm] 98h=type get ` sv ns,nm}[ns] each nms;
        ([] job:(count nms)#job; tbl:nms;
            role:{[d;nm] $[nm in d`subscribe_to;`subscribe_to;nm in d`publishes;`publishes;`internal]}[d] each nms)
      } each .qetl.job.stream.defined[]}

.qt.qetl.plant.tables.test_every_job_declares_the_shape_the_plant_actually_carries:{[t]
    rows:select from .qt.qetl.plant.tables.job_tables[] where tbl in .qt.qetl.plant.tables.declared[];
    rows:select from rows where not .qt.qetl.plant.tables.pair'[job;tbl] in .qt.qetl.plant.tables.excused[];
    `.qt.qetl.plant.tables.bad set ();
    {[row]
        plant:cols .qt.qetl.plant.tables.tbl row`tbl;
        theirs:cols get ` sv (.qetl.job.stream.namespace row`job),row`tbl;
        want:$[row[`role]=`publishes; 1_plant; plant];
        if[not theirs~want;
            `.qt.qetl.plant.tables.bad set .qt.qetl.plant.tables.bad,enlist
                (string[row`job],"/",string[row`tbl]," (",string[row`role],"): job has ",
                 (" " sv string theirs),", plant has ",(" " sv string want))]
      } each rows;
    .qunit.assertEquals[.qt.qetl.plant.tables.bad;();
        "every job's declared table matches the plant's, with `time` for one it subscribes to and without for one it publishes"]};

.qt.qetl.plant.tables.test_every_job_table_sharing_a_plant_name_is_exchanged_or_excused:{[t]
    / The loophole in the test above: it derives the comparison from the
    / role, and a table declared with NEITHER role is silently skipped. A
    / name collision is then invisible - which is how markout's `quotes`
    / (a different shape from the plant's depth table, then also `quotes`)
    / went unnoticed.
    rows:select from .qt.qetl.plant.tables.job_tables[]
        where tbl in .qt.qetl.plant.tables.declared[], role=`internal;
    unexcused:select from rows where not .qt.qetl.plant.tables.pair'[job;tbl] in .qt.qetl.plant.tables.excused[];
    .qunit.assertEquals[count unexcused;0;
        "a job declaring a table named after a plant table, while neither subscribing nor publishing it, is a collision that needs a reason in not_exchanged"]};

.qt.qetl.plant.tables.test_the_exemptions_still_describe_something_real:{[t]
    / So the excuse list cannot rot into cover for the next real collision.
    live:.qt.qetl.plant.tables.job_tables[];
    stale:.qt.qetl.plant.tables.excused[] except .qt.qetl.plant.tables.pair'[live`job;live`tbl];
    .qunit.assertEquals[count stale;0;
        "every not_exchanged entry names a job and table that still exist - remove the entry rather than leaving a dead excuse"]};

.qt.qetl.plant.tables.bad:()

/ ------------------------------------------------- how a table is read (#1869)

.qt.qetl.plant.tables.test_every_table_is_read_by_the_time_the_plant_stamps:{[t]
    .qunit.assertEquals[distinct .qetl.plant.read_time_column each .qetl.plant.own[];enlist `time;"one time column, `time, for every plant table"]};

.qt.qetl.plant.tables.test_a_table_is_read_by_sym_unless_q_declares_another:{[t]
    .qunit.assertEquals[.qetl.plant.read_instrument_column `orders;`sym;"sym where the table has one"];
    .qunit.assertEquals[.qetl.plant.read_instrument_column `order_quality;`book;"order_quality's declared exception: it has no sym"];
    .qunit.assertEquals[.qetl.plant.read_instrument_column `config_change;`;"none where it has neither"]};

.qt.qetl.plant.tables.test_every_exception_names_a_column_its_table_has:{[t]
    .qunit.assertTrue[all {[tbl] .qetl.plant.instrument_exceptions[tbl] in cols .qetl.plant.schema tbl} each key .qetl.plant.instrument_exceptions;"each declared exception is in its schema"]};

.qt.qetl.plant.tables.test_an_exception_to_a_missing_column_is_refused:{[t]
    keep:.qetl.plant.instrument_exceptions;
    .qetl.plant.instrument_exceptions:(enlist `orders)!enlist `no_such_col;
    r:@[{.qetl.plant.read_instrument_column `orders};::;{(`threw;x)}];
    .qetl.plant.instrument_exceptions:keep;
    .qunit.assertEquals[r;(`threw;"plant: instrument_exceptions names no_such_col for orders, which has no such column");"a rename fails here, not at query time"]};

/ --- whose clock source_time is (#1925) -------------------------------------

.qt.qetl.plant.tables.test_both_canonical_tables_end_in_a_symbol_clock_column:{[t]
    / LAST, so a partition the history fill appended `clock to has the same column
    / order as one written from this declaration, and no positional read moves.
    {[tbl] s:.qetl.plant.schema tbl;
        .qunit.assertEquals[last cols s;`clock;string[tbl]," ends in clock"];
        .qunit.assertEquals[type s`clock;11h;string[tbl],"'s clock is a symbol"]} each `executions`market_data;
    .qunit.assertEquals[.qetl.plant.clocks;`venue`receipt;"the two clocks a source_time can be on; `unknown is the history fill's, and is refused"]};
