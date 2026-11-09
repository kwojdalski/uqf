/ test_source_fills.q - the generic fills source (.qetl.source.fills) and the csv
/ transport under it (.qetl.source.csv_*), #1981.
/ .
/ Everything is deterministic: the csv files are written under build/ by the tests, the
/ ODBC driver is a table the test hands to `adapt`, and the sample drop is
/ tests/q/data/eq_fills.csv. Every refusal asserts its exact text, because the text is what
/ the declaring site reads.

.qt.qetl.source.fills.dir:"build/test-fills"

/ A complete mapping of canonical field -> a source's column, to vary.
.qt.qetl.source.fills.map:{[] `venue`fill_id`sym`side`size`price`source_time`fee`fee_ccy!`ex`id`tk`bs`qty`px`ts`fee`fee_ccy}

/ A complete, valid declaration to vary.
.qt.qetl.source.fills.decl:{[]
    `transport`table_name`columns`sides`clock`tz!(`csv;`fills;.qt.qetl.source.fills.map[];`B`S!1 -1;`venue;`UTC)}

/ A csv file in the test directory, one line per element of `lines`.
.qt.qetl.source.fills.file:{[name;lines]
    system "mkdir -p ",.qt.qetl.source.fills.dir;
    p:hsym `$.qt.qetl.source.fills.dir,"/",name;
    p 0: lines;
    p}

/ The header of the test mapping's csv.
.qt.qetl.source.fills.header:"ts,ex,id,tk,bs,qty,px,fee,fee_ccy"

/ Declare a csv source over the test mapping under `name`, then run f on its handle's file.
.qt.qetl.source.fills.with_source:{[name;lines;f]
    p:.qt.qetl.source.fills.file["t.csv";enlist[.qt.qetl.source.fills.header],lines];
    .qetl.source.fills.declare[name;.qt.qetl.source.fills.decl[]];
    r:@[f;p;{(`threw;x)}];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist name);0b;`symbol$()];
    r}

.qt.qetl.source.fills.day:{[n] 2026.02.25D00:00:00.000000000+n*1D}

.qt.qetl.source.fills.good_row:"2026-02-25T14:30:00.020000Z,XNAS,a1,aapl,B,100,271.60,0.35,USD"

.qt.qetl.source.fills.window:{[name;p] .qetl.source.fills.read[name;p;.qt.qetl.source.fills.day[0];.qt.qetl.source.fills.day[1]]}

/ ------------------------------------------------------------ declaration

.qt.qetl.source.fills.test_a_valid_declaration_fixes_the_contract:{[t]
    r:.qetl.source.fills.declare[`t_fills;.qt.qetl.source.fills.decl[]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills);0b;`symbol$()];
    .qunit.assertEquals[r`columns;`source_time`venue`fill_id`sym`side`size`price`fee`fee_ccy`clock;"the canonical columns, in order"];
    .qunit.assertEquals[(r`types;r`time_column;r`row_key;r`tz);("psssjfffss";`source_time;`venue`fill_id;`UTC);"types, window column, key and zone"];
    .qunit.assertEquals[cols first value r`raw;`ex`id`tk`bs`qty`px`ts`fee`fee_ccy;"the live check reads the source's own column names"]};

.qt.qetl.source.fills.test_a_missing_declaration_is_named:{[t]
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];`transport`table_name!(`csv;`fills);
        "fills: t_fills is missing declaration(s): columns, sides, clock, tz";"every missing key at once"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];1;
        "fills: t_fills must be declared by a dictionary of transport, table_name, columns, sides, clock, tz";"not a dictionary"]};

.qt.qetl.source.fills.test_missing_required_mappings_are_refused_together:{[t]
    d:@[.qt.qetl.source.fills.decl[];`columns;{`size`price _ x}];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];d;
        "fills: t_fills is missing required column mapping(s): size, price";"both at once, in the canonical order"];
    d:@[.qt.qetl.source.fills.decl[];`columns;{`source_time _ x}];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];d;
        "fills: t_fills is missing required column mapping(s): source_time";"the time is required"]};

.qt.qetl.source.fills.test_the_optional_fee_fields_may_be_left_out:{[t]
    d:@[.qt.qetl.source.fills.decl[];`columns;{`fee`fee_ccy _ x}];
    r:.qetl.source.fills.declare[`t_fills;d];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills);0b;`symbol$()];
    .qunit.assertEquals[cols first value r`raw;`ex`id`tk`bs`qty`px`ts;"only the required columns are read"]};

.qt.qetl.source.fills.test_an_unknown_field_is_refused:{[t]
    d:@[.qt.qetl.source.fills.decl[];`columns;{x,enlist[`colour]!enlist `c}];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];d;
        "fills: t_fills maps unknown field(s): colour - the fields are venue, fill_id, sym, side, size, price, source_time, fee, fee_ccy";
        "a typo is not silently ignored"]};

.qt.qetl.source.fills.test_an_unknown_clock_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`clock;:;`exchange];
        "fills: t_fills's clock must be venue or receipt, not exchange";"only the two clocks the core knows"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`clock;:;`unknown];
        "fills: t_fills's clock must be venue or receipt, not unknown";"unknown is a clock the core never scores"]};

.qt.qetl.source.fills.test_a_zone_other_than_utc_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`tz;:;`$"America/New_York"];
        "fills: t_fills's time must be UTC, not America/New_York - a fill is compared to a book to the millisecond, so a local clock is converted upstream";
        "the zone is declared and must be UTC"]};

.qt.qetl.source.fills.test_a_bad_side_encoding_is_refused:{[t]
    sd:{[d;s] @[d;`sides;:;s]};
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];sd[.qt.qetl.source.fills.decl[];`B`S!2 -1];
        "fills: t_fills's sides must map every spelling to 1 (buy) or -1 (sell)";"a spelling mapped to 2"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];sd[.qt.qetl.source.fills.decl[];`B`BUY!1 1];
        "fills: t_fills's sides must cover both a buy (1) and a sell (-1)";"a buy-only encoding"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];sd[.qt.qetl.source.fills.decl[];`B`b`S!1 1 -1];
        "fills: t_fills's sides spells one side two ways once case is folded";"B and b are one spelling"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];sd[.qt.qetl.source.fills.decl[];"BS"];
        "fills: t_fills's sides must be a non-empty dictionary from each source spelling (symbols) to 1 or -1";"not a dictionary"];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];sd[.qt.qetl.source.fills.decl[];(`symbol$())!`long$()];
        "fills: t_fills's sides must be a non-empty dictionary from each source spelling (symbols) to 1 or -1";"an empty one"]};

.qt.qetl.source.fills.test_numeric_side_spellings_are_symbols_too:{[t]
    r:@[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`sides;:;(`$'("1";"2"))!1 -1];{(`threw;x)}];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills);0b;`symbol$()];
    .qunit.assertEquals[r`transport;`csv;"a source that spells its sides 1 and 2 declares them as symbols"]};

.qt.qetl.source.fills.test_a_column_that_is_not_a_plain_name_is_refused:{[t]
    d:@[.qt.qetl.source.fills.decl[];`columns;{@[x;`price;:;`$"px; DROP TABLE fills"]}];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];d;
        "fills: t_fills maps price to px; DROP TABLE fills, which is not a plain column name";"the names are spliced into SQL"];
    d:@[.qt.qetl.source.fills.decl[];`columns;{@[x;`price;:;`ex]}];
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];d;
        "fills: t_fills maps more than one field to the column ex";"two fields cannot read one column"]};

.qt.qetl.source.fills.test_an_unknown_transport_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`transport;:;`ipc];
        "fills: t_fills's transport must be one of csv, odbc";"fills come from a file or a table"]};

.qt.qetl.source.fills.test_a_refused_declaration_registers_nothing:{[t]
    @[.qetl.source.fills.declare[`t_fills];@[.qt.qetl.source.fills.decl[];`clock;:;`x];{(::)}];
    .qunit.assertFalse[`t_fills in exec name from .qetl.source.fills.registry;"the registry is written last"]};

.qt.qetl.source.fills.test_the_bundled_source_is_declared_on_the_venue_clock:{[t]
    .qunit.assertEquals[(.qetl.source.fills.registry `eq_fills)`clock;`venue;"the exchange's own time, the clock the equity books are on"];
    .qunit.assertTrue[.qetl.source.validate[`eq_fills;.qpipe.source.eq_fills.fixture[]];"the fixture satisfies the contract"]};

/ --------------------------------------------------------------- the csv

.qt.qetl.source.fills.test_a_missing_file_is_refused_at_open:{[t]
    .qunit.assertThrows[.qetl.source.csv_path;"build/test-fills/no-such.csv";"csv: build/test-fills/no-such.csv is not a file that exists";"connect fails, not the first window"];
    .qunit.assertThrows[.qetl.source.csv_path;"";"csv: the credential is the path of a csv file";"an empty credential"];
    .qunit.assertThrows[.qetl.source.csv_path;"build";"csv: build is not a file that exists";"a directory is not a file"]};

.qt.qetl.source.fills.test_the_header_is_the_live_checks_metadata:{[t]
    p:.qt.qetl.source.fills.file["h.csv";(.qt.qetl.source.fills.header;.qt.qetl.source.fills.good_row)];
    m:.qetl.source.csv_meta[p];
    .qunit.assertEquals[m`c;`ts`ex`id`tk`bs`qty`px`fee`fee_ccy;"one row per header field"];
    .qunit.assertEquals[m`t;9#" ";"typed general: a csv holds text"]};

.qt.qetl.source.fills.test_a_row_with_the_wrong_field_count_is_refused:{[t]
    p:.qt.qetl.source.fills.file["w.csv";(.qt.qetl.source.fills.header;.qt.qetl.source.fills.good_row;"2026-02-25T14:31:00Z,XNAS,a2,aapl,B,100,271.60,0.35")];
    .qunit.assertThrows[.qetl.source.csv_rows;p;"csv: build/test-fills/w.csv line 3 has 8 fields, the header has 9 (a quoted comma is not supported)";
        "a short row is refused, not read with its fields shifted"]};

/ The line named is the row's line in the file (#2404): a blank line above it was
/ skipped before counting, and the message named the blank line instead.
.qt.qetl.source.fills.test_a_bad_row_after_a_blank_line_is_named_by_its_own_line:{[t]
    p:.qt.qetl.source.fills.file["blank.csv";("a,b";"1,2";"";"3,4,5")];
    .qunit.assertThrows[.qetl.source.csv_rows;p;"csv: build/test-fills/blank.csv line 4 has 3 fields, the header has 2*";
        "line 4, not the blank line 3"]};

.qt.qetl.source.fills.test_the_sample_drop_reads_as_the_fixture:{[t]
    r:.qetl.source.fills.read[`eq_fills;`:tests/q/data/eq_fills.csv;.qt.qetl.source.fills.day[0];.qt.qetl.source.fills.day[2]];
    .qunit.assertEquals[r;.qpipe.source.eq_fills.fixture[];"the file the fixture is the parse of, in contract types"]};

.qt.qetl.source.fills.test_a_window_is_half_open_on_source_time:{[t]
    f:{[n;p] .qetl.source.fills.read[n;p;2026.02.25D14:30:00.500000000;2026.02.25D20:59:55.000000000]};
    r:f[`eq_fills;`:tests/q/data/eq_fills.csv];
    .qunit.assertEquals[r`fill_id;`x0002`x0003;"the fill AT the lower bound is in; the fill AT the upper bound is out"]};

.qt.qetl.source.fills.test_an_empty_window_is_the_contracts_empty_table:{[t]
    r:.qetl.source.fills.read[`eq_fills;`:tests/q/data/eq_fills.csv;2030.01.01D00:00;2030.01.02D00:00];
    .qunit.assertEquals[(count r;.qetl.source.type_chars r);(0;"psssjfffss");"no rows, the declared types"]};

.qt.qetl.source.fills.test_the_side_encoding_is_applied_without_case:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;(.qt.qetl.source.fills.good_row;"2026-02-25T14:31:00Z,XNAS,a2,AAPL,s,10,271.70,0.1,USD");{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r`side;1 -1;"B is a buy and s is a sell"];
    .qunit.assertEquals[r`sym;`AAPL`AAPL;"symbols are upper-cased"]};

.qt.qetl.source.fills.test_a_side_the_encoding_does_not_name_refuses_the_window:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist "2026-02-25T14:31:00Z,XNAS,a2,AAPL,X,10,271.70,0.1,USD";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 1 has a side the encoding does not name (side; 1 row(s) in the window)");"never read as a sell"]};

.qt.qetl.source.fills.test_a_price_that_is_not_a_number_refuses_the_window:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;(.qt.qetl.source.fills.good_row;"2026-02-25T14:31:00Z,XNAS,a2,AAPL,B,10,n/a,0.1,USD");{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 2 has a price that is not a number (price; 1 row(s) in the window)");"the row is named"]};

.qt.qetl.source.fills.test_a_date_only_time_refuses_the_file:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist "2026-02-25,XNAS,a2,AAPL,B,10,271.7,0.1,USD";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 1 has a time that is empty, date-only or malformed (source_time; 1 row(s) in the window)");"a date is not midnight"]};

.qt.qetl.source.fills.test_a_time_with_an_offset_refuses_the_file:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist "2026-02-25T15:30:00+01:00,XNAS,a2,AAPL,B,10,271.7,0.1,USD";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 1 carries a UTC offset, but the source is declared UTC (source_time; 1 row(s) in the window)");"declared UTC, so not converted"];
    ok:.qt.qetl.source.fills.with_source[`t_fills;("2026-02-25T14:30:00+00:00,XNAS,a1,AAPL,B,10,271.7,0.1,USD";"2026-02-25T14:31:00Z,XNAS,a2,AAPL,B,10,271.7,0.1,USD");{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[ok`source_time;2026.02.25D14:30:00.000000000 2026.02.25D14:31:00.000000000;"a zero offset and Z are UTC"]};

/ #2548: a padded time parsed, but the offset check read the leading space as the clock's.
.qt.qetl.source.fills.test_a_time_with_a_leading_space_is_read_as_utc:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist " 2026-02-25T14:31:00.020000,XNAS,a2,AAPL,B,10,271.7,0.1,USD";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r`source_time;enlist 2026.02.25D14:31:00.020000000;"read, not refused as carrying an offset"];
    .qunit.assertEquals[.qetl.coerce.utc_offset " 2026-02-25T14:30:00.5";("2026-02-25T14:30:00.5";0D);"no offset, as to_timestamp reads it"]};

.qt.qetl.source.fills.test_an_empty_fill_id_refuses_the_window:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist "2026-02-25T14:31:00Z,XNAS,,AAPL,B,10,271.7,0.1,USD";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 1 has an empty fill id (fill_id; 1 row(s) in the window)");"a fill with no id cannot be keyed"]};

/ A refusal names the row by its place in the file, not in the window (#2403): the
/ window here starts at the file's second data row, so the bad third row was
/ named "data row 2" - a row that is fine.
.qt.qetl.source.fills.test_a_refusal_names_the_row_in_the_file_not_in_the_window:{[t]
    p:.qt.qetl.source.fills.file["rows.csv";(
        "exec_time,exchange,exec_id,ticker,buy_sell,qty,px,commission,commission_ccy";
        "2026-02-24T14:30:00.000000Z,XNAS,x0001,AAPL,B,100,271.60,0.35,USD";
        "2026-02-25T14:30:00.000000Z,XNAS,x0002,META,S,10,643.20,0.10,USD";
        "2026-02-25T15:30:00.000000Z,XNAS,x0003,MSFT,X,10,400.00,0.10,USD")];
    .qunit.assertThrows[.qt.qetl.source.fills.window[`eq_fills;];p;
        "fills: eq_fills data row 3 has a side the encoding does not name*";"the side's row, as the file numbers it"];
    r:.qt.qetl.source.fills.with_source[`t_fills;(
        "2026-02-24T14:31:00Z,XNAS,a0,AAPL,B,10,271.7,0.1,USD";
        .qt.qetl.source.fills.good_row;
        "2026-02-25T14:31:00Z,XNAS,a2,AAPL,B,10,271.7,abc,USD");{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r;(`threw;"fills: t_fills data row 3 has a fee that is not a number (fee; 1 row(s) in the window)");"and so is a fee's"]};

.qt.qetl.source.fills.test_a_malformed_row_outside_the_window_does_not_refuse_it:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;(.qt.qetl.source.fills.good_row;"2026-02-26T14:31:00Z,XNAS,a2,AAPL,X,10,n/a,0.1,USD");{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[r`fill_id;enlist `a1;"only the window's rows are held to the fields"]};

.qt.qetl.source.fills.test_a_file_without_a_mapped_column_is_refused:{[t]
    p:.qt.qetl.source.fills.file["m.csv";("ts,ex,id,tk,bs,qty,fee,fee_ccy";"2026-02-25T14:30:00Z,XNAS,a1,AAPL,B,10,0.1,USD")];
    .qetl.source.fills.declare[`t_fills;.qt.qetl.source.fills.decl[]];
    r:@[.qetl.source.fills.read[`t_fills;p;.qt.qetl.source.fills.day[0];];.qt.qetl.source.fills.day[1];{(`threw;x)}];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills);0b;`symbol$()];
    .qunit.assertEquals[r;(`threw;"fills: t_fills's file lacks the mapped column(s): px");"named, before any row is read"]};

.qt.qetl.source.fills.test_an_unmapped_fee_reads_as_null_not_zero:{[t]
    p:.qt.qetl.source.fills.file["n.csv";("ts,ex,id,tk,bs,qty,px";"2026-02-25T14:30:00Z,XNAS,a1,AAPL,B,10,271.7")];
    .qetl.source.fills.declare[`t_fills;@[.qt.qetl.source.fills.decl[];`columns;{`fee`fee_ccy _ x}]];
    r:.qetl.source.fills.read[`t_fills;p;.qt.qetl.source.fills.day[0];.qt.qetl.source.fills.day[1]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills);0b;`symbol$()];
    .qunit.assertEquals[(r`fee;r`fee_ccy);(enlist 0n;enlist `);"no fee reported is not a fee of zero"]};

.qt.qetl.source.fills.test_crlf_line_endings_are_read:{[t]
    r:.qt.qetl.source.fills.with_source[`t_fills;enlist .qt.qetl.source.fills.good_row,"\r";{.qt.qetl.source.fills.window[`t_fills;x]}];
    .qunit.assertEquals[(r`fill_id;r`fee_ccy);(enlist `a1;enlist `USD);"a carriage return is not part of the last field"]};

/ -------------------------------------------------------------------- odbc

.qt.qetl.source.fills.odbc_decl:{[] @[.qt.qetl.source.fills.decl[];`transport`table_name;:;(`odbc;`exec_report)]}

/ What KX's ODBC client returns for sql_for's statement: epoch nanoseconds, text as strings, fee DECIMAL cast to float.
.qt.qetl.source.fills.driver_rows:{[]
    ([] source_time:1772029800020000000 1772029800500000000;
        venue:("XNAS";"XNAS"); fill_id:("x0001";"x0002"); sym:("aapl";"meta"); side:("B";"S");
        size:100 10f; price:271.6 643.2; fee:0.35 0.1; fee_ccy:("USD";"USD"))}

.qt.qetl.source.fills.test_the_statement_selects_canonical_names_and_a_half_open_window:{[t]
    .qetl.source.fills.declare[`t_fills_sql;.qt.qetl.source.fills.odbc_decl[]];
    sql:.qetl.source.fills.sql_for[`t_fills_sql;2026.02.25D00:00:00.000000000;2026.02.26D00:00:00.000000000];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    .qunit.assertEquals[sql;
        "SELECT epoch_ns(ts) AS source_time, ex AS venue, id AS fill_id, tk AS sym, bs AS side, CAST(qty AS DOUBLE) AS size, CAST(px AS DOUBLE) AS price, CAST(fee AS DOUBLE) AS fee, fee_ccy AS fee_ccy FROM exec_report WHERE ts >= make_timestamp_ns(1771977600000000000) AND ts < make_timestamp_ns(1772064000000000000) ORDER BY ts, ex, id";
        "the source's own names aliased to the canonical ones; >= the lower bound and < the upper; epoch nanoseconds so the driver's millisecond datetime is never met"]};

.qt.qetl.source.fills.test_driver_rows_become_the_contract:{[t]
    .qetl.source.fills.declare[`t_fills_sql;.qt.qetl.source.fills.odbc_decl[]];
    r:.qetl.source.fills.adapt[`t_fills_sql;.qt.qetl.source.fills.driver_rows[]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    .qunit.assertEquals[r`source_time;2026.02.25D14:30:00.020000000 2026.02.25D14:30:00.500000000;"epoch nanoseconds, exactly"];
    .qunit.assertEquals[(r`venue;r`sym;r`side;r`clock);(`XNAS`XNAS;`AAPL`META;1 -1;`venue`venue);"text becomes symbols, B/S becomes 1/-1, the clock is stamped"];
    .qunit.assertEquals[.qetl.source.type_chars r;"psssjfffss";"the declared types"]};

.qt.qetl.source.fills.test_driver_rows_agree_with_the_csv_reading_of_the_same_fills:{[t]
    .qetl.source.fills.declare[`t_fills_sql;.qt.qetl.source.fills.odbc_decl[]];
    o:.qetl.source.fills.adapt[`t_fills_sql;.qt.qetl.source.fills.driver_rows[]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    c:2#.qpipe.source.eq_fills.fixture[];
    .qunit.assertEquals[@[o;`source_time;`#];@[c;`source_time;`#];"the two transports give one table for one set of fills (the fixture's first two)"]};

.qt.qetl.source.fills.test_an_unnamed_side_from_the_driver_refuses_the_window:{[t]
    .qetl.source.fills.declare[`t_fills_sql;.qt.qetl.source.fills.odbc_decl[]];
    r:@[.qetl.source.fills.adapt[`t_fills_sql];update side:("B";"Q") from .qt.qetl.source.fills.driver_rows[];{(`threw;x)}];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    .qunit.assertEquals[r;(`threw;"fills: t_fills_sql data row 2 has a side the encoding does not name (side; 1 row(s) in the window)");"Q is neither"]};

.qt.qetl.source.fills.test_numeric_sides_and_fill_ids_from_the_driver_are_read:{[t]
    .qetl.source.fills.declare[`t_fills_sql;@[.qt.qetl.source.fills.odbc_decl[];`sides;:;(`$'("1";"2"))!1 -1]];
    r:.qetl.source.fills.adapt[`t_fills_sql;update side:1 2, fill_id:7 8 from .qt.qetl.source.fills.driver_rows[]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    .qunit.assertEquals[(r`side;r`fill_id);(1 -1;`$'("7";"8"));"an integer side and an integer id are rendered, then matched"]};

.qt.qetl.source.fills.test_an_odbc_window_sends_the_statement_and_adapts_the_answer:{[t]
    .qetl.source.fills.declare[`t_fills_sql;.qt.qetl.source.fills.odbc_decl[]];
    keep:.qetl.io.odbc.run_sql;
    `.qt.qetl.source.fills.sent set ();
    .qetl.io.odbc.run_sql:{[h;sql] `.qt.qetl.source.fills.sent set sql; .qt.qetl.source.fills.driver_rows[]};
    r:@[{.qetl.source.fills.read[`t_fills_sql;42i;x 0;x 1]};(.qt.qetl.source.fills.day[0];.qt.qetl.source.fills.day[1]);{(`threw;x)}];
    .qetl.io.odbc.run_sql:keep;
    sql:.qetl.source.fills.sql_for[`t_fills_sql;.qt.qetl.source.fills.day[0];.qt.qetl.source.fills.day[1]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_fills_sql);0b;`symbol$()];
    .qunit.assertEquals[.qt.qetl.source.fills.sent;sql;"the statement sql_for builds is the one sent"];
    .qunit.assertEquals[r`fill_id;`x0001`x0002;"and the rows come back in the contract's shape"]};
