/ plant_tables.q - the tickerplant tables the uqf stack publishes into
/ (.qetl.plant), and the one place each table's schema is written.
/ .
/ THESE ARE q TABLES, AND THIS IS WHERE THEY LIVE. They used to be Python
/ string literals in uqs/model/schemas.py, which meant q source that
/ no q parser ever read until stp1 started: a typo surfaced as a failed
/ tickerplant rather than a failed commit, check_q_traps.py never scanned
/ them (it globs `git ls-files '*.q'`), and the contract surface listed four
/ tables when the system had thirteen.
/ .
/ HOW THIS FILE IS USED, which is two ways on purpose:
/ .
/   read as TEXT  uqs's _generated_schema_content() appends
/                 these definitions to a copy of the vendored database.q and
/                 points stp1's -schemafile at the copy. The vendored file is
/                 never edited.
/   LOADED as q   tests/q/test_demo_tables.q loads it and checks the tables
/                 parse and carry the columns their consumers expect, and
/                 scripts/generate/export_contract_surface.q loads it so these tables
/                 appear in the contract surface alongside the ETL ledgers.
/ .
/ So the definitions are checked by a q parser on every commit, which is the
/ property that was missing.
/ .
/ THE SCHEMA OF A PLANT TABLE IS WRITTEN HERE AND NOWHERE ELSE. A table is
/ written by one job and read by others (executions: the normalizer writes it,
/ posbook reads it), so no job owns its shape. Jobs take it from here with
/ .qetl.plant.schema rather than declaring their own copy - a copy is a second
/ place for the shape to be wrong, and kafka_flow's hand-written buffer once
/ typed trade_id as a symbol the plant carries as a long.
/ .
/ NAMESPACED in q, BARE on the plant. Loaded as q - src/etl/init.q loads it -
/ every definition lands in .qetl.plant, so a process that loads src/ gets no
/ root tables of these names to shadow, or be mistaken for, live data. Read as
/ text, uqs copies each `name:([]...)` line into stp1's schema file, where it
/ is a bare table name, the form the vendored database.q uses.
/ .
/ The lookups come FIRST and the tables last, with no closing `\d .`: `uqs job
/ new` appends a new table's line to the end of this file, and \l restores the
/ caller's namespace itself, so an appended table still lands in .qetl.plant.
/ .
/ Definition order is immaterial to q - these are independent declarations.

\d .qetl.plant

/ Every plant table's name, in definition order.
/ @return the table names, as a symbol list
/ @eg 0<count .qetl.plant.names[]  ->  1b
names:{[] (key `.qetl.plant) where 98h=type each get each ` sv' `.qetl.plant,'key `.qetl.plant}

/ A plant table's schema: its empty table, `time` first.
/ @param t the table's name, as a symbol
/ @return the empty table
/ @throws error naming the table when the plant does not carry it
/ @eg cols .qetl.plant.schema `quote
schema:{[t]
    if[not t in names[];
        '"plant: no table ",string[t]," - neither src/etl/plant_tables.q nor the starter-pack schema ",
            vendored_path," defines it"];
    0#get ` sv `.qetl.plant,t}

/ A plant table's shape as a job holds it: the schema without its storage
/ attributes. `g#sym` is the plant's and the RDB's business - how a column is
/ indexed where it is stored - and a job's buffers and transform contracts
/ carry plain columns.
/ @param t the table's name, as a symbol
/ @return the empty table, time first, no attributes
/ @eg attr (.qetl.plant.shape `quote)`sym  ->  `
shape:{[t] s:schema[t]; @[s;cols s;`#]}

/ A plant table's shape without `time`: what a job publishes, since the
/ tickerplant stamps `time` itself (.u.upd, and .qetl.tick the same).
/ @param t the table's name, as a symbol
/ @return the empty table, without its time column
/ @eg `time in cols .qetl.plant.published `quote  ->  0b
published:{[t] (cols[s] except `time)#s:shape[t]}

/ Some of a plant table's columns, in the order given: the shape of a job
/ that reads only what it uses. A column the plant table does not carry is
/ refused, so a projection cannot quietly invent one.
/ @param t the table's name, as a symbol
/ @param cs the columns, as a symbol list
/ @return the empty table with those columns
/ @throws error naming a column the table does not carry
/ @eg cols .qetl.plant.columns[`quote;`time`sym`bid`ask]  ->  `time`sym`bid`ask
columns:{[t;cs]
    s:shape[t];
    if[count bad:cs where not cs in cols s;
        '"plant: ",string[t]," carries no ",(", " sv string bad)," - its schema is ",
            $[t in vendored; vendored_path; "src/etl/plant_tables.q"]];
    cs#s}

/ ------------------------------------------------------ NESTED COLUMNS
/ .
/ A column declared `()` holds a list per row - a ladder of prices, a route
/ of symbols - and an empty `()` has no element type: meta reads it as " "
/ whatever it will carry. So the empty schema alone cannot say whether a
/ populated column is right. Compared against it exactly, every valid ladder
/ failed ("F" is not " "); treated as a wildcard, a column of atoms where
/ vectors belong would pass. Neither is a contract.
/ .
/ The rest of the contract is declared here, beside each table, with
/ `nested`: the meta type a populated row carries - "F" float vectors, "C"
/ strings, "S" symbol lists, "P" timestamp lists - or " " for a column that
/ holds any value on purpose (config_change's old and new). Every nested
/ column of this tree's own tables must be declared; `undeclared` lists the
/ ones that are not, and a test holds it empty.

/ Declared element types: (table; column) -> meta type character.
elements:([table:`symbol$(); column:`symbol$()] element:`char$())

/ A table's nested columns: the ones its schema declares as ().
/ @param table_name the table, as a symbol
/ @return the columns, as a symbol list
/ @eg .qetl.plant.nested_columns `mkt_orderbook  ->  `bid_prices`ask_prices
nested_columns:{[table_name] exec c from 0!meta schema table_name where t=" "}

/ Declare what a table's nested columns hold. Call it after the table's line.
/ @param table_name the table, as a symbol
/ @param types column -> meta type character, e.g. `bid_prices`ask_prices!"FF";
/   " " for a column that holds any value
/ @return table_name
/ @throws error naming a column that is not nested, or a type that is not one
/ @eg .qetl.plant.nested[`mkt_orderbook;`bid_prices`ask_prices!"FF"]
nested:{[table_name;types]
    / Two checks, not one `and`: q evaluates both sides, and `value` on a
    / symbol looks up a variable of that name.
    if[not 99h=type types;
        '"nested: ",string[table_name],"'s element types must be a dictionary of column -> type character, e.g. `bid_prices`ask_prices!\"FF\""];
    if[not 10h=type value types;
        '"nested: ",string[table_name],"'s element types must be type characters, e.g. `bid_prices`ask_prices!\"FF\" - one column needs enlist: (enlist `route)!enlist \"S\""];
    ok:nested_columns[table_name];
    if[count bad:(key types) except ok;
        '"nested: ",string[table_name]," has no nested column ",(", " sv string bad),
         " - nested columns are the ones declared as (): ",$[count ok; ", " sv string ok; "it has none"]];
    if[count bad:(key types) where not (value types) in upper .Q.t;
        '"nested: ",string[table_name],"'s ",(", " sv string bad)," must be a meta type character such as \"F\", \"C\" or \"S\", or \" \" for any value"];
    `.qetl.plant.elements upsert ([] table:(count types)#table_name; column:key types; element:value types);
    table_name}

/ This tree's own nested columns with no declared element type.
/ @return a symbol list of `table.column`, empty when every one is declared
/ @eg .qetl.plant.undeclared[]  ->  `symbol$()
undeclared:{[]
    / Cast, so "none" is `symbol$() - raze over empty results need not be.
    `symbol$raze {[t] cs:nested_columns[t] except exec column from elements where table=t;
        `$(string[t],"."),/:string cs} each own[]}

/ What is wrong with `rows` as a page for plant table `table_name`: its
/ columns and their order against what a job publishes there, each column's
/ type, and each nested column's every row against its declared element type
/ - every row, not just the first, which is all meta looks at.
/ .
/ An empty table passes a nested column vacuously: there are no rows to hold
/ the wrong thing, and an empty () column has no type to compare. Its column
/ names and order are still checked.
/ @param table_name the plant table, as a symbol
/ @param rows the rows a job would publish onto it
/ @return messages naming the table and column, empty when the page fits
/ @throws error when the plant carries no such table
/ @eg .qetl.plant.problems[`mkt_orderbook;.qetl.plant.published `mkt_orderbook]  ->  ()
problems:{[table_name;rows]
    want:published[table_name];
    rows:0!rows;
    if[not (cols want)~cols rows;
        :enlist string[table_name]," has columns ",(" " sv string cols rows),
            " - the plant takes ",(" " sv string cols want)];
    declared:exec column!element from elements where table=table_name;
    wt:exec c!t from meta want;
    gt:exec c!t from meta rows;
    raze column_problems[table_name;declared;count rows]'[cols want;wt cols want;gt cols want;rows cols want]}

/ Private: one column's problems, for `problems`.
/ @private
column_problems:{[table_name;declared;n;c;want;got;vals]
    name:string[table_name],".",string c;
    if[not want=" ";
        :$[(got=want) or (n=0) and got=" "; ();
            enlist name," is typed \"",got,"\" - the plant takes \"",want,"\""]];
    if[not c in key declared;
        :enlist name," is a nested column with no declared element type - declare it with .qetl.plant.nested in src/etl/plant_tables.q"];
    e:declared c;
    if[e=" "; :()];
    bad:where not (.Q.t?lower e)=type each vals;
    $[count bad;
        enlist name," row ",string[first bad]," holds a value of type ",string[type vals first bad],
            "h - each row must be a \"",e,"\" list (type ",string[.Q.t?lower e],"h)",
            $[1<count bad; ", and ",string[count bad]," rows are wrong in all"; ""];
        ()]}

/ The vendored starter pack's own plant tables - quote, trade, packets - read
/ from its database.q rather than copied here. uqs builds stp1's schema file
/ from that file plus this one, so both are the plant; a copy of the vendored
/ definitions would be a second place for them to drift, and that file is
/ never edited. Each `name:([]...)` line is split at its first colon and set
/ by name: `value` on a whole assignment statement throws 'nyi from inside a
/ lambda on this build (see .qtorq.safe_timer).
/ @param path the vendored database.q, relative to the repository root
/ @return the table names registered
adopt_vendored:{[path]
    / A tree loaded without the vendored pack - a minimal copy, as the scaffold
    / tests make - has no vendored tables to register, which is not an error.
    if[()~key hsym `$path; :`symbol$()];
    lines:read0 hsym `$path;
    / A plain prefix test, not a `like` pattern: KDB-X's like throws 'nyi on a
    / bracketed `[` class and past two wildcards.
    defs:lines where {[l] (0<count l) and (first[l] within "az") and "([]"~3#(1+l?":")_l} each lines;
    {[d] i:d?":"; (` sv `.qetl.plant,`$i#d) set value (i+1)_d; `$i#d} each defs}

/ The vendored tables' names, so a caller can tell them from this tree's own.
/ The starter pack is $TORQAPPHOME's when that is set - uqs sets it for every
/ process it starts, and a deployment points it at an existing install that
/ the release does not ship (#773) - and the vendored copy otherwise.
/ vendored_path is kept so an error can name the schema a table was missing
/ from: on a deployment that is a managed install's, not this tree's (#902).
vendored_path:$[count getenv`TORQAPPHOME; getenv[`TORQAPPHOME],"/database.q";
    "lib/torq-finance-starter-pack/database.q"];
vendored:adopt_vendored[vendored_path];

/ This tree's own plant tables: every one except the vendored three.
/ @return the table names
/ @eg `quote in .qetl.plant.own[]  ->  0b
own:{[] names[] except vendored}

/ Define root tables of these names, empty, from their schemas.
/ .
/ For a standalone process that HOLDS these tables - an example script, the
/ contract-surface exporter, a test of the tables themselves. Never for a job:
/ a process that loads src/ must not get root tables shadowing live ones,
/ which is why loading this file defines none.
/ @param ts the table names
/ @return ts
/ @eg .qetl.plant.materialise enlist `orders
materialise:{[ts] {[t] t set schema t} each ts; ts}

/ The order book shape every pricing and execution function in src/ expects:
/ vector-valued price and size columns, one row per (time, sym). Matches
/ forwards.q's require_depth_quotes exactly, so a row published here is
/ usable by cross_book_at with no reshaping.
/ .
/ That sentence was FALSE for as long as it stood, and is worth keeping the
/ history of. require_depth_quotes demanded `ts` while this table led with
/ `time` - which .u.upd requires of every table's first column - so
/ cross_book_at refused a real fx_orderbook table outright, and the claim of "no
/ reshaping" went unchallenged because nothing ever called a pricing
/ function with a tickerplant table. scripts/examples/scenario_example.q
/ now does, on every commit, and the timestamp column is `time everywhere.
fx_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
nested[`fx_orderbook;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ Every venue's book, FX and crypto, with its source and original timestamp
/ kept across normalization. posbook1 marks to its level-0 mids; superbook1
/ merges the FX ones.
market_data:([]time:`timestamp$(); sym:`g#`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
nested[`market_data;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ Complete per-pair snapshots; level provenance stays aligned with prices and sizes.
superbook:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); bid_prices:(); bid_sizes:(); bid_sources:(); bid_times:(); ask_prices:(); ask_sizes:(); ask_sources:(); ask_times:())
nested[`superbook;`bid_prices`bid_sizes`bid_sources`bid_times`ask_prices`ask_sizes`ask_sources`ask_times!"FFSPFFSP"];

/ Status snapshots, including active=0b to clear an earlier gross opportunity.
arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); buy_source:`symbol$(); sell_source:`symbol$(); ask:`float$(); bid:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$())

/ Synthetic-versus-direct cross opportunities: one pair priced against a
/ route through others (EURJPY against EURUSD x USDJPY). `route` carries the
/ legs so a reader can see what to trade, and `skew` how far apart they were
/ quoted - a synthetic price is only as fresh as its stalest leg. Like
/ `arbitrage`, an append-only status history: read the latest row per sym
/ BEFORE filtering on active.
cross_arbitrage:([]time:`timestamp$(); sym:`g#`symbol$(); as_of:`timestamp$(); active:`boolean$(); direction:`symbol$(); route:(); direct_price:`float$(); synthetic_price:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$(); fully_filled:`boolean$(); skew:`timespan$())
nested[`cross_arbitrage;(enlist `route)!enlist "S"];

/ An audit trail of runtime configuration changes (.qetl.cfg.audit). `old` is
/ empty on a name's first observation, which is the row that says what the
/ process STARTED with. Values are -3! renderings, because one column has to
/ hold a timespan, a float and a symbol list. WHO made a change is not here:
/ join to TorQ's own usage log at the same timestamp, which records .z.u,
/ .z.a and the command text for every incoming query.
config_change:([]time:`timestamp$(); owner:`g#`symbol$(); name:`symbol$(); old:(); new:(); as_of:`timestamp$())
nested[`config_change;`old`new!"  "];

/ A deliberately "incorrectly-shaped" wide book: one scalar column per level
/ rather than vector columns, which is the shape real venue feeds arrive in
/ and the input src/market_data/book.q exists to fold.
/ .
/ ELEVEN LEVELS, and the columns are written out rather than generated. The
/ Python version built this string with a comprehension over
/ WIDE_BOOK_LEVELS; a generated schema is harder to read than the 22 lines
/ it saves, and a reader checking whether bids10 exists should be able to
/ look. The bids<n>/asks<n> naming is not free choice - it matches
/ .qbook.derive_level_groups' prefix-plus-contiguous-digit-suffix
/ convention, which is what folds these into bid_prices/ask_prices.
wide_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bids0:`float$();bids1:`float$();bids2:`float$();bids3:`float$();bids4:`float$();bids5:`float$();bids6:`float$();bids7:`float$();bids8:`float$();bids9:`float$();bids10:`float$();asks0:`float$();asks1:`float$();asks2:`float$();asks3:`float$();asks4:`float$();asks5:`float$();asks6:`float$();asks7:`float$();asks8:`float$();asks9:`float$();asks10:`float$())

/ vectorize1's output: wide_orderbook's bids*/asks* folded into vector columns by
/ .qbook.book_from_wide_levels, then republished onto the tickerplant - an
/ ordinary database table flowing through rdb1/wdb1/hdb, not private state
/ on vectorize1's own process.
mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())
nested[`mkt_orderbook;`bid_prices`ask_prices!"FF"];

/ Databento MBP-10 as the live feed handler publishes it - the source
/ contract's own fields, so a live row and an ODBC-backfilled row are the
/ same shape by construction. Written by the external Python feed handler
/ rather than by any q process (see external/databento_feed.py), the same way
/ crypto_book below is written by cryptorust.
/ .
/ Forty per-level columns because that is what Databento sends; folding
/ them into four vectors is databento1's job, not this table's.
databento_mbp10:([]time:`timestamp$(); ts_event:`timestamp$(); sym:`g#`symbol$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_px_00:`float$(); bid_sz_00:`long$(); ask_px_00:`float$(); ask_sz_00:`long$(); bid_px_01:`float$(); bid_sz_01:`long$(); ask_px_01:`float$(); ask_sz_01:`long$(); bid_px_02:`float$(); bid_sz_02:`long$(); ask_px_02:`float$(); ask_sz_02:`long$(); bid_px_03:`float$(); bid_sz_03:`long$(); ask_px_03:`float$(); ask_sz_03:`long$(); bid_px_04:`float$(); bid_sz_04:`long$(); ask_px_04:`float$(); ask_sz_04:`long$(); bid_px_05:`float$(); bid_sz_05:`long$(); ask_px_05:`float$(); ask_sz_05:`long$(); bid_px_06:`float$(); bid_sz_06:`long$(); ask_px_06:`float$(); ask_sz_06:`long$(); bid_px_07:`float$(); bid_sz_07:`long$(); ask_px_07:`float$(); ask_sz_07:`long$(); bid_px_08:`float$(); bid_sz_08:`long$(); ask_px_08:`float$(); ask_sz_08:`long$(); bid_px_09:`float$(); bid_sz_09:`long$(); ask_px_09:`float$(); ask_sz_09:`long$())

/ The folded book, republished by databento1 - the shape .qbook and
/ .qcross.cross_book_at read. Carries `ts_event` as well as `time`: the
/ tickerplant stamps `time` on receipt, and a book that knew only when it
/ ARRIVED could not tell a stale feed from a fast one.
eq_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); ts_event:`timestamp$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
nested[`eq_orderbook;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FJFJ"];

/ Client FX flow as the external Kafka consumer publishes it - one row per
/ consumed record, in the source contract's own field order. Written by
/ python/uqs/src/uqs/external/kafka_feed.py rather than by any q process,
/ the same way databento_mbp10 above and crypto_book below are.
/ .
/ `partition` and `offset` are the Kafka coordinates of the record, and they
/ are columns rather than consumer-side bookkeeping BECAUSE the plant is the
/ thing that has to survive a redelivery. At-least-once delivery means a
/ rebalance or a restart can replay a record this table has already seen;
/ kafka1 drops those by comparing against a per-partition high-water mark,
/ which it can only do if the coordinates travel with the row.
/ .
/ `broker_time` is the broker's own timestamp, kept for the reason
/ databento_mbp10 keeps ts_event: `time` says when we heard about the
/ record, not when it happened, and their difference is the lag.
kafka_client_flow:([]time:`timestamp$(); broker_time:`timestamp$(); partition:`long$(); offset:`long$(); sym:`g#`symbol$(); side:`symbol$(); qty:`float$(); price:`float$(); client:`symbol$(); trade_id:`long$())

/ Written by the external cryptorust recorder rather than by any
/ scripts/torq_*.q process - see start_crypto_recorder. Carries `venue`
/ because a crypto book is venue-specific in a way an FX book here is not.
/ .
/ `time` is THIS stack's receipt stamp and `source_time` is the venue's own,
/ as for market_data and kafka_client_flow. They were one column until the
/ two were found to be conflated: marks read `time` and published it AS
/ source_time, so the plant's clock reached a downstream consumer wearing the
/ venue's name, and no reader could tell them apart. cryptorust's BookUpdate
/ has carried the venue stamp all along - see kdb_market_data_recorder.rs.
crypto_book:([]time:`timestamp$(); source_time:`timestamp$(); venue:`g#`symbol$(); sym:`g#`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
nested[`crypto_book;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ Simulated fills from cryptorust's OMS. Distinct from crypto_trades below,
/ which carries real fills: conflating simulated and real execution in one
/ table is how a P&L number stops meaning anything.
crypto_sim_fills:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); realized_delta_pnl:`float$())

/ Real fills, via cryptorust's get_recent_real_fills. Normalised to the
/ trades shape below - side as a long, not the raw "buy"/"sell" string - so
/ execution analytics reads one convention.
crypto_trades:([]time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); fee:`float$(); fee_currency:`symbol$(); exchange_fill_id:`symbol$())

/ The trades shape src/execution/execution.q's markout family consumes
/ (sym/time/side/trade_price/pip_factor), so demo_markout1 and posbook1 read
/ rows off this table with zero reshaping.
trades:([]time:`timestamp$(); sym:`g#`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$())

/ posbook1's output: weighted-average-cost positions, republished onto the
/ tickerplant like mkt_orderbook rather than kept as process-private state.
position:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$(); mark_price:`float$(); unrealized_pnl:`float$(); total_pnl:`float$())

/ posbook1's book as a day opens (#943): published at end of day into the
/ new day's log, and restored from by a restart's replay, so positions carry
/ across days and a restart agrees with a process left running.
position_open:([]time:`timestamp$(); sym:`g#`symbol$(); qty:`float$(); avg_price:`float$(); realized_pnl:`float$())

/ demo_markout1's output: per-trade execution quality at each horizon.
demo_execution_quality:([]time:`timestamp$(); sym:`g#`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())

/ executions1's output: every fill the stack carries, as one tape (#885). The
/ `executions` normalizer (src/etl/streaming/executions.q) maps `trades`,
/ `crypto_trades` and the filled rows of `orders` onto this; posbook1 and
/ fxpositions1 read it and nothing else for fills, so desk exposure and P&L
/ net the same population. book and product are the desk's dimensions,
/ null for a fill that carries none. source_time is the source's own stamp,
/ `time` the plant's on the normalized row. Named `executions` because
/ `fills` is a q builtin.
executions:([]time:`timestamp$(); source_time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); side:`long$(); size:`float$(); price:`float$(); fee:`float$(); fee_ccy:`symbol$(); fill_id:`symbol$(); book:`symbol$(); product:`symbol$())

/ exec_bars1's output: time bars of `executions` (#946), one row per sym per
/ window [bar_start, bar_start+width), on the fill's source_time. open and close
/ are the first and last fill by source_time, vwap is size-weighted, volume the
/ summed size. A window with no fills has no row. `time` is the plant's, on
/ publication; the backfill twin writes the window's end.
exec_bar:([]time:`timestamp$(); sym:`g#`symbol$(); bar_start:`timestamp$(); open:`float$(); high:`float$(); low:`float$(); close:`float$(); volume:`float$(); vwap:`float$(); trades:`long$())

/ fx_orders_feed's output: order flow, most of which never becomes a fill.
/ Wider than `trades` because a position keyed on more than sym needs the
/ dimensions to arrive with the order. The executions normalizer keeps the
/ filled ones, by order_status.
orders:([]time:`timestamp$(); order_id:`long$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); side:`long$(); size:`float$(); price:`float$(); order_status:`symbol$())

/ fxpositions1's snapshot: net exposure per (sym, book, product), the
/ whole book on every timer tick rather than only what moved.
fx_position:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); base_qty:`float$(); quote_qty:`float$(); fill_count:`long$(); break_even:`float$())

/ fxpositions1's book as a day opens (#943), as position_open is posbook1's.
fx_position_open:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); base_qty:`float$(); quote_qty:`float$(); fill_count:`long$())

/ fxpositions1's alerts: one row per limit newly crossed, throttled so a
/ standing breach does not republish on every tick.
fx_limit_breach:([]time:`timestamp$(); sym:`g#`symbol$(); book:`symbol$(); product:`symbol$(); metric:`symbol$(); observed:`float$(); cap:`float$(); severity:`symbol$(); utilisation:`float$())

/ ------------------------------------------------- declared, not yet produced
/ .
/ Six shapes for parts of an eFX system this tree has not built. They came
/ from env/, which carried them as `.envschema` reference scaffolding that
/ nothing loaded and no lane ran; env/ was deleted because three of its
/ eleven tables named real tickerplant tables while disagreeing with them on
/ columns, and a reference model that contradicts the real thing is worse
/ than none. These six had no counterpart, so they are kept - here, in the
/ one file that declares what the tickerplant carries, rather than in a
/ second model beside it.
/ .
/ THEY ARE THE ONLY TABLES HERE WITH NO PRODUCER, in-tree or external, and
/ that is a deliberate exception rather than an oversight. Every other table
/ in this file is published by a process, by cryptorust's recorder or by a
/ Python feed handler. These are declared so the shape is agreed and
/ reviewable before anything fills them - which is the trade being made
/ against `pipeline-philosophy.md` §3, "nothing exists that does nothing".
/ .
/ What each still needs, so the gap is legible rather than implied:
/ .
/   ccy_exposure     .qpos.ccy_exposure_in already computes this. Needs a job
/                    that runs it over `position` and `market_data` and
/                    publishes the result - the nearest of the six.
/   reference_data   .qccy.ccy_pair_legs already derives base/quote. Needs a
/                    loader, and a decision on where the static data lives.
/   connections      VENUE connections, not process ones: .qetl.hb and
/                    `uqs summary` already answer process liveness, and this
/                    must not become a second spelling of that.
/   predictions      needs a model. Nothing in this tree produces one.
/   order_routing    needs a router. Nothing in this tree routes.
/   economic_calendar needs an external data source and a licence for it.
/ .
/ `time` first in every one of them, including where env/ used `ts`: the
/ tickerplant's .u.upd requires the first column to be literally `time`, so a
/ shape that disagrees could never be published even once something produced
/ it.

/ Model output, one row per (time, sym, horizon_ms, model). horizon_ms
/ matches the horizon convention markout_at_horizons uses.
predictions:([]time:`timestamp$(); sym:`g#`symbol$(); horizon_ms:`long$(); model:`symbol$(); predicted_mid:`float$(); confidence:`float$())

/ Net exposure per currency at an instant, revalued into one reporting
/ currency - .qpos.ccy_exposure_in's output shape, with the time and the
/ reporting currency it was computed against.
ccy_exposure:([]time:`timestamp$(); ccy:`g#`symbol$(); amount:`float$(); reporting_ccy:`symbol$(); reporting_amount:`float$())

/ Static instrument reference, one row per pair. base_ccy/quote_ccy are
/ .qccy.ccy_pair_legs' field names, so a row here feeds it unchanged.
reference_data:([]time:`timestamp$(); sym:`g#`symbol$(); base_ccy:`symbol$(); quote_ccy:`symbol$(); pip_factor:`long$(); min_size:`float$(); active:`boolean$())

/ Routing decisions, one row per (order, venue). NOT 1:1 with `orders` - an
/ order can split across venues, which is the whole reason this is its own
/ table rather than columns on that one.
order_routing:([]time:`timestamp$(); order_id:`long$(); venue:`symbol$(); routed_size:`float$(); routing_reason:`symbol$())

/ Venue connection registry - one row per venue link, not per process.
/ Process liveness is .qetl.hb's heartbeat and `uqs summary`; this is the
/ upstream side, which nothing in this tree talks to yet.
connections:([]time:`timestamp$(); venue:`g#`symbol$(); host:`symbol$(); port:`long$(); status:`symbol$(); last_heartbeat:`timestamp$())

/ Scheduled macro releases, one row per event. `actual` is null until the
/ event fires, which is what distinguishes a forecast row from a fired one.
economic_calendar:([]time:`timestamp$(); event_id:`long$(); ccy:`symbol$(); event_name:`symbol$(); importance:`symbol$(); forecast:`float$(); previous:`float$(); actual:`float$())

/ duckdb_deals_backfill1's target: one mock FX deal read from DuckDB over ODBC.
/ deal_time is when it was dealt; time, as on every plant table, is the plant's.
duckdb_deals:([]time:`timestamp$(); deal_time:`timestamp$(); deal_id:`long$(); sym:`g#`symbol$(); side:`symbol$(); notional:`float$(); rate:`float$())

/ kafka_flow1's output. <one line: what a row means>
client_flow:([]time:`timestamp$(); broker_time:`timestamp$(); sym:`g#`symbol$(); side:`symbol$(); qty:`float$(); price:`float$(); client:`symbol$(); trade_id:`long$(); partition:`long$(); offset:`long$())

/ crypto_market_data_backfill1's target: one recorded crypto book-and-trade
/ update, replayed from cryptorust's data/live.duckdb. Carries BOTH
/ clocks - source_time is the venue's stamp, local_time the recorder's, and
/ `time` is the plant's - because the lag between them is what a recorded
/ capture exists to measure, and crypto_book above has room for none of it.
crypto_market_data:([]time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); source_time:`timestamp$(); local_time:`timestamp$(); is_snapshot:`boolean$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:(); latency_ms:`float$(); latency_min_ms:`float$(); latency_count:`long$(); trade_price:`float$(); trade_size:`float$(); trade_side:`symbol$())
nested[`crypto_market_data;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FFFF"];

/ rebuild_positions' output (a reaction on demo_deals): the net notional per
/ pair for each published window, written through demo_deals_backfill's own
/ IO manager - so into the HDB under `uqs backfill`. `time` is the window's
/ start, which is what the HDB writer partitions by.
deal_positions:([]time:`timestamp$(); sym:`g#`symbol$(); window:`timestamp$(); net_notional:`float$(); deals:`long$())

/ hdb_transfer_backfill1's target. <one line: what a row means>
trades_copy:([]time:`timestamp$(); trade_id:`long$(); sym:`g#`symbol$(); price:`float$(); size:`long$(); side:`symbol$(); notional:`float$())

/ crypto_markout1's output. <one line: what a row means>
crypto_execution_quality:([]time:`timestamp$(); sym:`g#`symbol$(); venue:`symbol$(); fill_id:`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); side:`long$(); trade_price:`float$(); ref_price:`float$(); markout_bps:`float$())

/ last_value1's output. <one line: what a row means>
last_value:([]time:`timestamp$(); sym:`g#`symbol$(); market:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); mid:`float$())
