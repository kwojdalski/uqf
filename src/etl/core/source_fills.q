/ source_fills.q - a declared, generic fills source (.qetl.source.fills, #1981).
/ .
/ WHAT THIS IS. A fills drop - a csv file or an ODBC table, from a broker, a
/ venue's drop copy or an exchange report - declared once by a column MAPPING
/ and read as the canonical shape the `executions` table carries. It owns no
/ vendor, no venue and no asset class: an equity site, a futures site and a
/ crypto site declare the same thing with different column names. The bundled
/ instance is src/etl/sources/eq_fills.q.
/ .
/ THE DECLARATION. .qetl.source.fills.declare takes a name and a dictionary:
/ .
/   transport    `csv (the credential is the file's path) or `odbc (a DuckDB-dialect
/                connection string; see sql_for)
/   table_name   the table the source reads. A csv file is one table: the name is
/                only what the live check calls it
/   columns      the MAPPING: canonical field -> the source's column. venue,
/                fill_id, sym, side, size, price and source_time are required; fee and
/                fee_ccy are optional, and an unmapped one reads as null, never as
/                zero - "no fee reported" and "no fee charged" are different facts
/   sides        the SIDE ENCODING: every spelling the source uses for a side ->
/                1 (buy) or -1 (sell), matched without case. It must cover both
/                directions. There is no default: a column that reads B/S, buy/sell,
/                1/-1 or 1/2 is the source's to say, and a guess is a flipped sign
/                on every fill, which nothing downstream can see
/   clock        whose clock source_time is (#1925): `venue, the exchange's own stamp,
/                or `receipt, the time the sender (a broker, the stack) received the
/                fill. The markout core refuses to score a fill against a reference on
/                the other clock, so this is the one declaration that decides what
/                the fill can be scored against, and it is never defaulted
/   tz           `UTC. The only value accepted; see below
/ .
/ Everything is refused at DECLARATION, naming the fault, before a window is read:
/ a missing required mapping (all of them at once), a field that is not one of the
/ canonical ones, an unknown clock, a zone other than UTC, a side encoding that is
/ malformed or covers only one direction, and a column or table name that is not a
/ plain identifier (the names are spliced into the odbc statement, so they are the
/ one thing here that could carry SQL; the bounds go through .qetl.io.odbc.literal).
/ .
/ WHY UTC ONLY. The framework can convert a zoned source (source_zones.q), and for
/ most sources that is right. For fills it is not: a fill's time is compared to a
/ book's, to the millisecond, across a daylight-saving change, and a local wall clock
/ is ambiguous for an hour twice a year. A feed that cannot give UTC is converted
/ upstream. A csv time that carries an offset other than zero is refused for the
/ same reason: the file says UTC in the declaration and something else in the data.
/ .
/ WHAT A WINDOW RETURNS. source_time, venue, fill_id, sym, side, size, price, fee,
/ fee_ccy and clock, typed as `columns` and `types` below, sorted by source_time,
/ venue and fill_id so a window is the same table on every fetch. (venue; fill_id)
/ is the row key: a venue's fill id is unique within the venue.
/ .
/ A ROW THAT CANNOT BE READ REFUSES THE WINDOW. An empty or date-only time, a
/ size or price that is not a number, a side the encoding does not name, an empty
/ venue, fill id or symbol: each throws, naming the data row. The alternative -
/ dropping the row - would let the coverage ledger claim a window complete that is
/ missing a fill. A time that cannot be read refuses the whole file, because
/ without it the row cannot be placed in any window.

\d .

/ ----------------------------------------------------------------- CONTRACT

/ The fields a mapping must name.
.qetl.source.fills.required:`venue`fill_id`sym`side`size`price`source_time

/ The fields a mapping may name. Unmapped, they read as null.
.qetl.source.fills.optional:`fee`fee_ccy

/ The clocks source_time may be on (#1925).
.qetl.source.fills.clocks:`venue`receipt

/ The transports a fills source may be read over.
.qetl.source.fills.transports:`csv`odbc

/ What a window returns, in this order, and the q type of each.
.qetl.source.fills.columns:`source_time`venue`fill_id`sym`side`size`price`fee`fee_ccy`clock
.qetl.source.fills.types:"psssjfffss"

/ The declarations .qetl.source.fills.declare reads. All are required.
.qetl.source.fills.declarations:`transport`table_name`columns`sides`clock`tz

/ The declared fills sources: name -> transport, table, mapping (canonical field
/ -> source column), sides (lower-cased spelling -> 1 or -1) and clock. Keyed and
/ declared with its columns, so a second source does not collapse the first (#512).
.qetl.source.fills.registry:([name:`symbol$()] transport:`symbol$(); table_name:`symbol$(); mapping:(); sides:(); clock:`symbol$())

/ Is this a plain identifier: a letter or underscore, then letters, digits and underscores.
/ @param name a symbol
/ @return 1b when it is
/ @eg .qetl.source.fills.identifier `trade_ts  ->  1b
/ @eg .qetl.source.fills.identifier `$"a b; drop"  ->  0b
.qetl.source.fills.identifier:{[name]
    s:string name;
    (0<count s) and (not (first s) in .Q.n) and all s in .Q.an,"_"}

/ A declared value as it reads in a message: a symbol bare, anything else as q shows it.
/ @param x any value
/ @return a string
/ @eg .qetl.source.fills.shown `UTC  ->  "UTC"
/ @eg .qetl.source.fills.shown 3  ->  enlist "3"
.qetl.source.fills.shown:{[x] $[-11h=type x; string x; -3!x]}

/ Refuse a mapping that cannot be a fills mapping, naming every fault of one kind at once.
/ @param who the source's name, for the message
/ @param m the mapping
/ @return the mapping, when it is a valid one
/ @throws error naming the missing required mapping(s), the unknown field(s), or the bad column name(s)
/ @private
.qetl.source.fills.require_mapping:{[who;m]
    / q's `and` does not short-circuit, so each test is its own if
    if[not 99h=type m; '"fills: ",who,"'s columns must be a dictionary from field to source column"];
    if[(0<count m) and not 11h=type key m; '"fills: ",who,"'s columns must be a dictionary from field to source column"];
    miss:.qetl.source.fills.required except key m;
    if[count miss; '"fills: ",who," is missing required column mapping(s): ",", " sv string miss];
    extra:(key m) except .qetl.source.fills.required,.qetl.source.fills.optional;
    if[count extra;
        '"fills: ",who," maps unknown field(s): ",(", " sv string extra)," - the fields are ",", " sv string .qetl.source.fills.required,.qetl.source.fills.optional];
    if[not 11h=type value m; '"fills: ",who,"'s columns must name each source column as a symbol"];
    bad:where not .qetl.source.fills.identifier each value m;
    if[count bad; '"fills: ",who," maps ",(string (key m) first bad)," to ",(.qetl.source.fills.shown (value m) first bad),", which is not a plain column name"];
    dup:(value m) where 1<(count each group value m) (value m);
    if[count dup; '"fills: ",who," maps more than one field to the column ",string first dup];
    m}

/ Refuse a side encoding that is not a spelling -> 1 or -1 dictionary covering both directions.
/ @param who the source's name, for the message
/ @param s the encoding
/ @return the encoding with its spellings lower-cased
/ @throws error when it is malformed, spells one side two ways once case is folded, or names one direction only
/ @private
.qetl.source.fills.require_sides:{[who;s]
    shape:"fills: ",who,"'s sides must be a non-empty dictionary from each source spelling (symbols) to 1 or -1";
    / q's `and` does not short-circuit, so each test is its own if
    if[not 99h=type s; 'shape];
    if[not (0<count s) and (11h=type key s) and 7h=type value s; 'shape];
    if[any null key s; '"fills: ",who,"'s sides names a null spelling"];
    if[not all (value s) in 1 -1; '"fills: ",who,"'s sides must map every spelling to 1 (buy) or -1 (sell)"];
    k:`$/:lower each string key s;
    if[(count distinct k)<count k; '"fills: ",who,"'s sides spells one side two ways once case is folded"];
    if[not (1 in value s) and -1 in value s; '"fills: ",who,"'s sides must cover both a buy (1) and a sell (-1)"];
    k!value s}

/ Declare a fills source: validate the mapping, register it, and return the parts of the
/ source declaration it fixes. The caller adds `query` and `fixture` (two short lambdas that
/ call .qetl.source.fills.read and its fixture) and passes the result to .qetl.source.define.
/ @param source the source's name, a lower_snake_case symbol
/ @param decl a dictionary of transport, table_name, columns, sides, clock and tz (see the header)
/ @return a dictionary of the source declaration's keys this fixes: source, table_name, time_column,
/   row_key, columns, types, tz, transport and raw
/ @throws error naming the fault: a missing declaration, a missing or unknown mapping, an unknown
/   transport or clock, a zone other than UTC, a bad side encoding or a column that is not a plain name
.qetl.source.fills.declare:{[source;decl]
    who:string source;
    if[not (-11h=type source) and .qetl.source.fills.identifier source; '"fills: a source's name is a plain symbol"];
    if[not 99h=type decl; '"fills: ",who," must be declared by a dictionary of ",", " sv string .qetl.source.fills.declarations];
    miss:.qetl.source.fills.declarations except key decl;
    if[count miss; '"fills: ",who," is missing declaration(s): ",", " sv string miss];
    if[not (-11h=type decl`transport) and (decl`transport) in .qetl.source.fills.transports;
        '"fills: ",who,"'s transport must be one of ",", " sv string .qetl.source.fills.transports];
    if[not (-11h=type decl`table_name) and .qetl.source.fills.identifier decl`table_name;
        '"fills: ",who,"'s table_name must be a plain table name"];
    m:.qetl.source.fills.require_mapping[who;decl`columns];
    if[not (-11h=type decl`clock) and (decl`clock) in .qetl.source.fills.clocks;
        '"fills: ",who,"'s clock must be ",(" or " sv string .qetl.source.fills.clocks),", not ",.qetl.source.fills.shown decl`clock];
    if[not `UTC~decl`tz;
        '"fills: ",who,"'s time must be UTC, not ",(.qetl.source.fills.shown decl`tz)," - a fill is compared to a book to the millisecond, so a local clock is converted upstream"];
    s:.qetl.source.fills.require_sides[who;decl`sides];
    `.qetl.source.fills.registry upsert (source;decl`transport;decl`table_name;enlist m;enlist s;decl`clock);
    `source`table_name`time_column`row_key`columns`types`tz`transport`raw!(
        source;decl`table_name;`source_time;`venue`fill_id;.qetl.source.fills.columns;.qetl.source.fills.types;`UTC;decl`transport;
        enlist[decl`table_name]!enlist flip (distinct value m)!count[distinct value m]#enlist ())}

/ ------------------------------------------------------------------- ODBC

/ The statement for one window of an ODBC fills source, in the DuckDB dialect (the one driver
/ this tree runs against, as duckdb_deals does). Every field is selected under its canonical
/ name, so the result needs no renaming. source_time is read as epoch nanoseconds, because KX's
/ client returns a SQL timestamp as a millisecond datetime and the contract refuses that; the
/ numbers are cast to DOUBLE, because a DECIMAL arrives as whatever the driver likes. The bounds
/ are longs through .qetl.io.odbc.literal, half-open on the source's time column; the column and
/ table names are the declaration's, checked to be plain identifiers when it was made.
/ @param source a declared fills source
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the SELECT statement text
/ @eg .qetl.source.fills.sql_for[`eq_fills;2026.02.25D00:00:00.000000000;2026.02.26D00:00:00.000000000]
.qetl.source.fills.sql_for:{[source;range_from;range_to]
    r:.qetl.source.fills.registry source;
    m:first r`mapping;
    num:{[m;f] "CAST(",string[m f]," AS DOUBLE) AS ",string f}[m];
    txt:{[m;f] string[m f]," AS ",string f}[m];
    sel:enlist "epoch_ns(",string[m`source_time],") AS source_time";
    sel,:txt each `venue`fill_id`sym`side;
    sel,:num each `size`price;
    sel,:num each (enlist `fee) inter key m;
    sel,:txt each (enlist `fee_ccy) inter key m;
    t:string m`source_time;
    "SELECT ",(", " sv sel)," FROM ",string[r`table_name],
        " WHERE ",t," >= ",.qetl.io.odbc.duckdb_timestamp[range_from],
        " AND ",t," < ",.qetl.io.odbc.duckdb_timestamp[range_to],
        " ORDER BY ",t,", ",string[m`venue],", ",string m`fill_id}

/ ----------------------------------------------------------------- READING

/ A window with no fills, in the contract's columns and types.
/ @return an empty table
/ @eg count .qetl.source.fills.empty[]  ->  0
.qetl.source.fills.empty:{[] flip .qetl.source.fills.columns!.qetl.source.fills.types$\:()}

/ Each value of a column as trimmed text, whatever the driver or the file handed over: strings
/ stay, numbers and chars are rendered.
/ @param col a column
/ @return a list of strings
/ @eg .qetl.source.fills.texts (" a ";7)  ->  (enlist "a";enlist "7")
.qetl.source.fills.texts:{[col] trim each {$[10h=type x; x; string x]} each col}

/ A column as symbols: text, trimmed. `cased` keeps the case (a fill id's case means something);
/ otherwise it is folded to upper case, the library's convention for a symbol.
/ @param cased 1b to keep case
/ @param col a column
/ @return a symbol vector, null where the value is empty
/ @eg .qetl.source.fills.symbols[0b;("aapl ";"")]  ->  `AAPL`
.qetl.source.fills.symbols:{[cased;col]
    if[0=count col; :`symbol$()];
    t:.qetl.source.fills.texts col;
    $[cased; `$/:t; `$/:upper each t]}

/ Refuse the first row of a column a predicate flags, naming the field and the data row.
/ `rows` is each flagged position's data row number: its row in the file for a csv source
/ (#2403), where a window is only some of the file's rows, and its position for ODBC.
/ @private
.qetl.source.fills.refuse:{[who;field;flags;rows;what] .qetl.source.fills.refuse_in[who;field;flags;rows;what;"the window"]}

/ Private: refuse, saying where the flagged rows were counted - "the file" for a check over
/ every row of a csv, which counted rows outside the window as in it (#2551).
/ @private
.qetl.source.fills.refuse_in:{[who;field;flags;rows;what;scope]
    bad:where flags;
    if[count bad; '"fills: ",who," data row ",string[rows first bad]," ",what," (",string[field],"; ",string[count bad]," row(s) in ",scope,")"];
    1b}

/ Assemble a window from its typed fields: apply the side encoding, refuse what cannot be read,
/ stamp the clock and order the rows.
/ @param source a declared fills source
/ @param d a dictionary of the window's columns: source_time (timestamps), venue, fill_id, sym and
/   fee_ccy (symbols, null for none), side (text), size, price and fee (floats, null for none)
/ @param rows each row's data row number, for a refusal: its row in the file for a csv source
/ @return the window in the contract's columns, sorted by source_time, venue and fill_id
/ @throws error naming the first data row that has a null time, venue, fill id, symbol, size or price, or a side the encoding does not name
/ @private
.qetl.source.fills.assemble:{[source;d;rows]
    who:string source;
    r:.qetl.source.fills.registry source;
    sd:`$/:lower each d`side;
    side:(first r`sides) sd;
    .qetl.source.fills.refuse[who;`source_time;null d`source_time;rows;"has no readable time"];
    .qetl.source.fills.refuse[who;`venue;null d`venue;rows;"has an empty venue"];
    .qetl.source.fills.refuse[who;`fill_id;null d`fill_id;rows;"has an empty fill id"];
    .qetl.source.fills.refuse[who;`sym;null d`sym;rows;"has an empty symbol"];
    .qetl.source.fills.refuse[who;`side;null side;rows;"has a side the encoding does not name"];
    .qetl.source.fills.refuse[who;`size;null d`size;rows;"has a size that is not a number"];
    .qetl.source.fills.refuse[who;`price;null d`price;rows;"has a price that is not a number"];
    n:count d`source_time;
    t:([] source_time:d`source_time; venue:d`venue; fill_id:d`fill_id; sym:d`sym; side:side;
          size:d`size; price:d`price; fee:d`fee; fee_ccy:d`fee_ccy; clock:n#r`clock);
    `source_time`venue`fill_id xasc t}

/ The fields of the csv rows at `keep`, parsed: the dictionary assemble takes.
/ @param source a declared fills source
/ @param raw the csv rows as text (.qetl.source.csv_rows)
/ @param ts every row's time
/ @param keep the indexes of the rows in the window
/ @return a dictionary of source_time, venue, fill_id, sym, side, size, price, fee and fee_ccy
/ @throws error when a fee is present and not a number
/ @private
.qetl.source.fills.csv_fields:{[source;raw;ts;keep]
    m:first (.qetl.source.fills.registry source)`mapping;
    pick:{[raw;m;keep;f] (raw m f) keep}[raw;m;keep];
    mapped:{[m;f] f in key m}[m];
    fee:$[mapped `fee; .qetl.coerce.to_float each pick `fee; (count keep)#0n];
    if[mapped `fee;
        .qetl.source.fills.refuse[string source;`fee;(null fee) & 0<count each .qetl.source.fills.texts pick `fee;1+keep;"has a fee that is not a number"]];
    `source_time`venue`fill_id`sym`side`size`price`fee`fee_ccy!(
        ts keep;
        .qetl.source.fills.symbols[1b;pick `venue];
        .qetl.source.fills.symbols[1b;pick `fill_id];
        .qetl.source.fills.symbols[0b;pick `sym];
        .qetl.source.fills.texts pick `side;
        .qetl.coerce.to_float each pick `size;
        .qetl.coerce.to_float each pick `price;
        fee;
        $[mapped `fee_ccy; .qetl.source.fills.symbols[0b;pick `fee_ccy]; (count keep)#`])}

/ One window of a csv fills source from the file's rows: those in [range_from; range_to), typed.
/ @param source a declared fills source (transport csv)
/ @param raw the file's rows as text (.qetl.source.csv_rows)
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the window in the contract's columns
/ @throws error when the rows lack a mapped column, a time cannot be read or carries an offset, or
/   a row in the window cannot be read (see assemble)
.qetl.source.fills.window_rows:{[source;raw;range_from;range_to]
    who:string source;
    r:.qetl.source.fills.registry source;
    m:first r`mapping;
    absent:(distinct value m) except cols raw;
    if[count absent; '"fills: ",who,"'s file lacks the mapped column(s): ",", " sv string absent];
    tc:raw m`source_time;
    ts:.qetl.coerce.to_timestamp each tc;
    .qetl.source.fills.refuse_in[who;`source_time;null ts;1+til count ts;"has a time that is empty, date-only or malformed";"the file"];
    off:last each .qetl.coerce.utc_offset each tc;
    .qetl.source.fills.refuse_in[who;`source_time;off<>0D;1+til count ts;"carries a UTC offset, but the source is declared UTC";"the file"];
    keep:where (ts>=range_from) & ts<range_to;
    if[0=count keep; :.qetl.source.fills.empty[]];
    / a refusal names the row by its place in the file, not in the window
    .qetl.source.fills.assemble[source;.qetl.source.fills.csv_fields[source;raw;ts;keep];1+keep]}

/ One window of a fills source, over its own transport: a csv file is sent through the traced
/ .qetl.source.csv, an ODBC handle through .qetl.io.odbc.run_sql.
/ @param source a declared fills source
/ @param h the file as a file symbol (csv) or an ODBC handle (odbc)
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the window in the contract's columns
.qetl.source.fills.read:{[source;h;range_from;range_to]
    if[not ((.qetl.source.fills.registry source)`transport)=`csv; :.qetl.source.fills.read_odbc[source;h;range_from;range_to]];
    .qetl.source.csv[h;.qetl.source.fills.window_rows[source;;;];range_from;range_to]}

/ One window of an ODBC fills source, as the driver returns it for sql_for's statement.
/ @param source a declared fills source (transport odbc)
/ @param h an ODBC handle from .qetl.io.odbc.open
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the window in the contract's columns
/ @private
.qetl.source.fills.read_odbc:{[source;h;range_from;range_to]
    .qetl.source.fills.adapt[source;.qetl.io.odbc.run_sql[h;.qetl.source.fills.sql_for[source;range_from;range_to]]]}

/ The driver's table for sql_for's statement, in the contract's types.
/ @param source a declared fills source
/ @param raw the table .qetl.io.odbc.run_sql returns: source_time as epoch nanoseconds, the text fields as text
/ @return the window in the contract's columns
/ @throws error when a row cannot be read (see assemble)
.qetl.source.fills.adapt:{[source;raw]
    n:count raw;
    if[0=n; :.qetl.source.fills.empty[]];
    d:`source_time`venue`fill_id`sym`side`size`price`fee`fee_ccy!(
        1970.01.01D00:00+`long$raw`source_time;
        .qetl.source.fills.symbols[1b;raw`venue];
        .qetl.source.fills.symbols[1b;raw`fill_id];
        .qetl.source.fills.symbols[0b;raw`sym];
        .qetl.source.fills.texts raw`side;
        `float$raw`size;`float$raw`price;
        $[`fee in cols raw; `float$raw`fee; n#0n];
        $[`fee_ccy in cols raw; .qetl.source.fills.symbols[0b;raw`fee_ccy]; n#`]);
    .qetl.source.fills.assemble[source;d;1+til n]}

\d .
