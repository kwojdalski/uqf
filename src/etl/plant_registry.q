/ plant_registry.q - the .qetl.plant runtime: how a plant table is looked up,
/ and the registries its declarations fill.
/ .
/ src/etl/plant_tables.q declares the tickerplant tables; this file is the rest
/ of .qetl.plant - the lookups a job takes a table's shape from (names, schema,
/ shape, published, columns), the clock rule, the columns a table is read by,
/ the registries a declaration writes to (nested element types, sort rules,
/ equivalence bases) with the functions that write them, the page check, and
/ the vendored starter pack's tables, adopted so `.qetl.plant.vendored` is set
/ before any of this tree's own is declared.
/ .
/ ONE FILE UNTIL #2503. Every `uqs job new` that defines a table appends to
/ plant_tables.q, and the two together sat at the q-module-size cap, so the
/ first scaffold failed the gate on a file its author never edited. They split
/ along the one seam the file already had - the API first, the tables after -
/ so the declarations have the whole budget to grow into and this side changes
/ only when the API does.
/ .
/ LOAD THIS FIRST. plant_tables.q calls .qetl.plant.nested, .sorting and
/ .equivalence as it loads, and refuses to load without them; every loader of
/ plant_tables.q loads this file just before it (src/etl/init.q, the plant
/ schema and contract-surface exporters, scripts/gates/hdb_sort_lib.q). uqs's
/ schema fingerprint covers both files (uqs.model.plant_metadata), since what
/ the exporter reports depends on both.
/ .
/ A table may not take a name bound here: the exporter reports them as
/ `bindings`, and `uqs job new` refuses them (#1597).

\d .

/ Every plant table's name, in definition order.
/ @return the table names, as a symbol list
/ @eg 0<count .qetl.plant.names[]  ->  1b
.qetl.plant.names:{[] (key `.qetl.plant) where 98h=type each get each ` sv' `.qetl.plant,'key `.qetl.plant}

/ A plant table's schema: its empty table, `time` first.
/ @param t the table's name, as a symbol
/ @return the empty table
/ @throws error naming the table when the plant does not carry it
/ @eg cols .qetl.plant.schema `quote
.qetl.plant.schema:{[t]
    if[not t in .qetl.plant.names[];
        '"plant: no table ",string[t]," - neither src/etl/plant_tables.q nor the starter-pack schema ",
            .qetl.plant.vendored_path," defines it"];
    0#get ` sv `.qetl.plant,t}

/ A plant table's shape as a job holds it: the schema without its storage
/ attributes. `g#sym` is the plant's and the RDB's business - how a column is
/ indexed where it is stored - and a job's buffers and transform contracts
/ carry plain columns.
/ @param t the table's name, as a symbol
/ @return the empty table, time first, no attributes
/ @eg attr (.qetl.plant.shape `quote)`sym  ->  `
.qetl.plant.shape:{[t] s:.qetl.plant.schema[t]; @[s;cols s;`#]}

/ A plant table's shape without `time`: what a job publishes, since the
/ tickerplant stamps `time` itself (.u.upd, and .qetl.tick the same).
/ @param t the table's name, as a symbol
/ @return the empty table, without its time column
/ @eg `time in cols .qetl.plant.published `quote  ->  0b
.qetl.plant.published:{[t] (cols[s] except `time)#s:.qetl.plant.shape[t]}

/ Some of a plant table's columns, in the order given: the shape of a job
/ that reads only what it uses. A column the plant table does not carry is
/ refused, so a projection cannot quietly invent one.
/ @param t the table's name, as a symbol
/ @param cs the columns, as a symbol list
/ @return the empty table with those columns
/ @throws error naming a column the table does not carry
/ @eg cols .qetl.plant.columns[`quote;`time`sym`bid`ask]  ->  `time`sym`bid`ask
.qetl.plant.columns:{[t;cs]
    s:.qetl.plant.shape[t];
    if[count bad:cs where not cs in cols s;
        '"plant: ",string[t]," carries no ",(", " sv string bad)," - its schema is ",
            $[t in .qetl.plant.vendored; .qetl.plant.vendored_path; "src/etl/plant_tables.q"]];
    cs#s}

/ ------------------------------------------------- WHOSE CLOCK source_time IS

/ The clocks a row's source_time can be on (#1925), declared once: `venue, the
/ venue's own stamp of the event, and `receipt, the time this stack (or the
/ feed handler that fed it) received the event. Both canonical tables,
/ `executions` and `market_data`, carry one per row in `clock`. `unknown is
/ NOT in this list: it is what the history fill writes for a stored row it
/ cannot attribute (scripts/gates/hdb_clock_rules.csv), and a measure that
/ compares two clocks must refuse it, not pick one.
/ @eg .qetl.plant.clocks  ->  `venue`receipt
.qetl.plant.clocks:`venue`receipt

/ Refuse rows whose clock is not one of .qetl.plant.clocks: a mapping that did
/ not stamp it (null), or stamped something else (`unknown included). The
/ normalizers' checks call this first, so a row whose clock nobody vouches for
/ is withheld with the batch and logged, never published.
/ @param table_name the table the rows are bound for, for the message
/ @param rows a table with a clock column
/ @return rows, unchanged, when every clock is valid
/ @throws error naming the table, how many rows and the first offending clock
/ @eg .qetl.plant.require_clock[`executions;([] clock:`venue`receipt)]  ->  ([] clock:`venue`receipt)
.qetl.plant.require_clock:{[table_name;rows]
    if[not `clock in cols rows; '"clock: ",string[table_name]," rows carry no clock column - every mapping must stamp whose clock source_time is"];
    bad:not rows[`clock] in .qetl.plant.clocks;
    if[any bad;
        '"clock: ",string[table_name]," has ",string[sum bad]," row(s) whose clock is not `venue or `receipt (null, or `unknown) - first: ",
            $[null first rows[`clock] where bad; "null"; string first rows[`clock] where bad],
            "; a mapping must stamp whose clock source_time is"];
    rows}

/ ------------------------------------------------- HOW A TABLE IS READ
/ .
/ The columns a table is read by, declared once (#1869): the primary time
/ column and the instrument column that scripts/torqconfig/dataaccess/
/ tableproperties.csv gives getdata, and that uqs reads from q_facts.py. They
/ live with the schema because a plant table's shape is written here and nowhere
/ else, and a plant table is read by streaming jobs that have no worker; the
/ datasets a worker writes that are NOT plant tables take the same rule over
/ their output (.qetl.job.bounded.read_columns).

/ The time column every plant table is read by. Every table leads with it (the
/ tickerplant stamps it), and the HDB writer adds it to a batch that has none,
/ as a copy of the source's time column (.qetl.io.hdb_rows) - so a table that
/ carries another time too, as duckdb_deals does with `deal_time`, is still read
/ by this one.
.qetl.plant.time_column:`time

/ The tables whose instrument column is not `sym, and what it is. A table not
/ named here is read by `sym where it has one, else by none.
.qetl.plant.instrument_exceptions:(enlist `order_quality)!enlist `book

/ The instrument column of a table with these columns.
/ @param t the table's name
/ @param c the table's columns
/ @return the exception's column, `sym where c has one, else a null symbol
/ @throws error when the exception names a column c lacks
/ @eg .qetl.plant.instrument_in[`order_quality;`time`book`n]  ->  `book
.qetl.plant.instrument_in:{[t;c]
    if[t in key .qetl.plant.instrument_exceptions;
        e:.qetl.plant.instrument_exceptions t;
        if[not e in c; '"plant: instrument_exceptions names ",string[e]," for ",string[t],", which has no such column"];
        :e];
    $[`sym in c; `sym; `]}

/ The time column a plant table is read by.
/ @param t the table's name
/ @return .qetl.plant.time_column
/ @throws error when the table does not carry it
/ @eg .qetl.plant.read_time_column `orders  ->  `time
.qetl.plant.read_time_column:{[t]
    if[not .qetl.plant.time_column in cols .qetl.plant.schema t;
        '"plant: ",string[t]," has no ",string[.qetl.plant.time_column]," column to read it by"];
    .qetl.plant.time_column}

/ The instrument column a plant table is read by, or a null symbol.
/ @param t the table's name
/ @return its instrument column
/ @eg .qetl.plant.read_instrument_column `order_quality  ->  `book
.qetl.plant.read_instrument_column:{[t] .qetl.plant.instrument_in[t;cols .qetl.plant.schema t]}

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
.qetl.plant.elements:([table:`symbol$(); column:`symbol$()] element:`char$())

/ A table's nested columns: the ones its schema declares as ().
/ @param table_name the table, as a symbol
/ @return the columns, as a symbol list
/ @eg .qetl.plant.nested_columns `mkt_orderbook  ->  `bid_prices`ask_prices
.qetl.plant.nested_columns:{[table_name] exec c from 0!meta .qetl.plant.schema table_name where t=" "}

/ Declare what a table's nested columns hold. Call it after the table's line.
/ @param table_name the table, as a symbol
/ @param types column -> meta type character, e.g. `bid_prices`ask_prices!"FF";
/   " " for a column that holds any value
/ @return table_name
/ @throws error naming a column that is not nested, or a type that is not one
/ @eg .qetl.plant.nested[`mkt_orderbook;`bid_prices`ask_prices!"FF"]
.qetl.plant.nested:{[table_name;types]
    / Two checks, not one `and`: q evaluates both sides, and `value` on a
    / symbol looks up a variable of that name.
    if[not 99h=type types;
        '"nested: ",string[table_name],"'s element types must be a dictionary of column -> type character, e.g. `bid_prices`ask_prices!\"FF\""];
    if[not 10h=type value types;
        '"nested: ",string[table_name],"'s element types must be type characters, e.g. `bid_prices`ask_prices!\"FF\" - one column needs enlist: (enlist `route)!enlist \"S\""];
    ok:.qetl.plant.nested_columns[table_name];
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
.qetl.plant.undeclared:{[]
    / Cast, so "none" is `symbol$() - raze over empty results need not be.
    `symbol$raze {[t] cs:.qetl.plant.nested_columns[t] except exec column from .qetl.plant.elements where table=t;
        `$(string[t],"."),/:string cs} each .qetl.plant.own[]}

/ ------------------------------------------------------ SORT RULES (#1517)
/ .
/ How a table is laid out on disk at end of day is TorQ's sort.csv: the
/ columns it is sorted by, in order, and the attribute each one gets. A stack's
/ `default` rows fit the tables it was built for - sorted by sym, say - and
/ cannot be assumed to fit a table attached to it later. Nor can a column's
/ name stand in for the policy: holding `sym` and `time` says nothing about
/ how the table should be stored.
/ .
/ So a table that needs its own rule declares it here, beside the table, with
/ `sorting`: the sort columns in order, each with its attribute (` for none).
/ `uqs job attachment add` writes exactly these rows to the stack's sort.csv - and
/ refuses when the stack already has other rows for the table.

/ Declared sort rules: (table; column) -> attribute, in sort order.
.qetl.plant.sort_rules:([table:`symbol$(); column:`symbol$()] att:`symbol$())

/ Declare a table's TorQ sort rule. Call it after the table's line.
/ @param table_name the table, as a symbol
/ @param rule sort column -> attribute, in sort order: `sym`time!`p` sorts by
/   sym then time and puts p# on sym. Attributes are TorQ sort.csv's: ` p s g u
/ @return table_name
/ @throws error naming a column the table lacks or holds lists in, a column named
/   twice, an attribute TorQ does not take, p or s off the first column, or a
/   table declared twice
/ @eg .qetl.plant.sorting[`mkt_orderbook;`sym`time!`p`]
.qetl.plant.sorting:{[table_name;rule]
    / Two checks, not one `and`: q evaluates both sides.
    if[not 99h=type rule;
        '"sorting: ",string[table_name],"'s rule must be a dictionary of column -> attribute, e.g. `sym`time!`p` - one column needs enlist: (enlist `time)!enlist `"];
    if[not (11h=type key rule) and 11h=type value rule;
        '"sorting: ",string[table_name],"'s rule must map column symbols to attribute symbols, e.g. `sym`time!`p`"];
    m:0!meta .qetl.plant.schema table_name;
    if[count bad:(key rule) except m`c;
        '"sorting: ",string[table_name]," has no column ",", " sv string bad];
    if[count bad:(key rule) inter exec c from m where t=" ";
        '"sorting: ",string[table_name],"'s ",(", " sv string bad)," hold a list per row; a sort needs a scalar column"];
    if[not count[key rule]=count distinct key rule;
        '"sorting: ",string[table_name]," names a column twice"];
    if[count bad:(value rule) except ``p`s`g`u;
        '"sorting: ",string[table_name],": attribute ",(", " sv string bad)," is not one TorQ's sort.csv takes (` p s g u)"];
    if[any (1_value rule) in `p`s;
        '"sorting: ",string[table_name],": p and s hold only on the first sort column, ",string first key rule];
    if[table_name in exec table from .qetl.plant.sort_rules;
        '"sorting: ",string[table_name]," already has a sort rule"];
    `.qetl.plant.sort_rules upsert ([] table:(count rule)#table_name; column:key rule; att:value rule);
    table_name}

/ ------------------------------------------------------ EQUIVALENCE BASIS
/ .
/ Several jobs can fill one table - a polling feed and a backfill. They share its
/ schema, which says nothing about what "the same rows" means, and
/ `uqs job check` will not guess. A table that several jobs fill declares it here,
/ beside the table, with `equivalence`:
/   events       every row is a fact; producers must return the same multiset of rows
/   revisions    each row is a revision of a key; they must return the same revisions
/   final_state  only the last row per key matters
/ A table with none is not compared across producers: that reads UNVERIFIED.

/ The bases a table may declare.
.qetl.plant.bases:`events`revisions`final_state

/ Declared equivalence bases: table -> basis.
.qetl.plant.equivalence_of:(`symbol$())!`symbol$()

/ Declare what equivalence of a table's producers means. Call it after the table's line.
/ @param table_name the table, as a symbol
/ @param basis one of `events`revisions`final_state
/ @return table_name
/ @throws error naming a basis that is none of those, or a table declared twice
/ @eg .qetl.plant.bases  ->  `events`revisions`final_state
.qetl.plant.equivalence:{[table_name;basis]
    if[not basis in .qetl.plant.bases;
        '"equivalence: ",string[table_name],"'s basis ",.Q.s1[basis]," is none of ",", " sv string .qetl.plant.bases];
    if[table_name in key .qetl.plant.equivalence_of;
        '"equivalence: ",string[table_name]," already declares a basis"];
    .qetl.plant.equivalence_of[table_name]:basis;
    table_name}

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
.qetl.plant.problems:{[table_name;rows]
    want:.qetl.plant.published[table_name];
    rows:0!rows;
    if[not (cols want)~cols rows;
        :enlist string[table_name]," has columns ",(" " sv string cols rows),
            " - the plant takes ",(" " sv string cols want)];
    declared:exec column!element from .qetl.plant.elements where table=table_name;
    wt:exec c!t from meta want;
    gt:exec c!t from meta rows;
    raze .qetl.plant.column_problems[table_name;declared;count rows]'[cols want;wt cols want;gt cols want;rows cols want]}

/ Private: one column's problems, for `problems`.
/ @private
.qetl.plant.column_problems:{[table_name;declared;n;c;want;got;vals]
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
.qetl.plant.adopt_vendored:{[path]
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
.qetl.plant.vendored_path:$[count getenv`TORQAPPHOME; getenv[`TORQAPPHOME],"/database.q";
    "lib/torq-finance-starter-pack/database.q"];
.qetl.plant.vendored:.qetl.plant.adopt_vendored[.qetl.plant.vendored_path];

/ This tree's own plant tables: every one except the vendored three.
/ @return the table names
/ @eg `quote in .qetl.plant.own[]  ->  0b
.qetl.plant.own:{[] .qetl.plant.names[] except .qetl.plant.vendored}

/ Define root tables of these names, empty, from their schemas.
/ .
/ For a standalone process that HOLDS these tables - an example script, the
/ contract-surface exporter, a test of the tables themselves. Never for a job:
/ a process that loads src/ must not get root tables shadowing live ones,
/ which is why loading this file defines none.
/ @param ts the table names
/ @return ts
/ @eg .qetl.plant.materialise enlist `orders
.qetl.plant.materialise:{[ts] {[t] t set .qetl.plant.schema t} each ts; ts}
