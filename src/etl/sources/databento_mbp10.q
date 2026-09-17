/ databento_mbp10.q - Databento MBP-10 order-book records over ODBC
/ (.qsdbn).
/ .
/ The first source in this tree reached through ODBC and carrying real market
/ data: Databento's MBP-10 schema (market by price, ten levels) for US
/ equities, loaded into DuckDB by scripts/dump_databento_duckdb.py. Each row
/ is one book event - an add, cancel or trade - with the book's top ten levels
/ on each side AFTER that event.
/ .
/ The credential, UQF_SOURCE_CRED_DATABENTO_MBP10, is an ODBC connection
/ string, e.g. "DRIVER=DuckDB;Database=/path/databento.duckdb;
/ access_mode=READ_ONLY". scripts/odbc_rosetta.sh sets it up on macOS.
/ .
/ WHAT THE DRIVER GETS WRONG, AND THE QUERY FIXES
/ .
/   ts_event    KX's ODBC client returns a SQL timestamp as a q DATETIME -
/               milliseconds, as a float. Databento stamps nanoseconds, and a
/               datetime window column is exactly what L-03 refuses. So the
/               SQL selects epoch_ns(ts_event), a BIGINT, and the query turns
/               it back into a timestamp exactly.
/   size, *_sz  UINTEGER in DuckDB, which the driver hands over as a SIGNED
/               32-bit int: anything above 2,147,483,647 would come back
/               negative. Cast to BIGINT in the SQL.
/   sequence    the same, and more likely to reach it: it is a feed-wide
/               counter, not a per-symbol one.
/   symbol, action, side  arrive as strings; cast to symbols here.
/ .
/ The window bounds are compared as make_timestamp_ns(<epoch ns>), not by
/ applying epoch_ns() to the column: a function on the column stops DuckDB
/ skipping row groups by their min/max, so every window would scan every row.

\d .qsdbn

source_name:`databento_mbp10

/ "00".."09", Databento's level suffixes.
levels:{-2#"0",string x} each til 10

/ Per level: bid price, bid size, ask price, ask size. Counts (bid_ct_*)
/ are not read - nothing downstream uses them (fields are what you READ).
level_fields:raze {`$("bid_px_";"bid_sz_";"ask_px_";"ask_sz_"),\:x} each levels

fields:`ts_event`symbol`action`side`price`size`sequence,level_fields
types:"psssfjj",raze 10#enlist "fjfj"

table:`mbp10
target:`databento_book
time_field:`ts_event

/ NOT Databento's documented key, and deliberately said so. (symbol,
/ ts_event, sequence) is the obvious candidate and is not unique - one event
/ can emit several records, and 1,557,920 of the 46,038,453 rows loaded
/ share it. Adding action, side, price and size made every row distinct in
/ that load (2026-09-17). An observation about this data, then, not a
/ guarantee from the vendor: re-check it on a new extract before relying on
/ it to deduplicate.
row_key:`symbol`ts_event`sequence`action`side`price`size

tz:`UTC
transport:`odbc

/ Private: the SQL expression selecting each field, in `fields` order.
select_list:{[]
    exprs:{[f]
        s:string f;
        $[f=`ts_event; "epoch_ns(ts_event) AS ts_event";
          (f in `size`sequence) or s like "*_sz_*"; "CAST(",s," AS BIGINT) AS ",s;
          s]} each .qsdbn.fields;
    ", " sv exprs}

/ Private: a timestamp as DuckDB epoch nanoseconds, through .qodbc.literal.
epoch_ns_literal:{[ts] "make_timestamp_ns(",.qodbc.literal["j"$ts-1970.01.01D00:00],")"}

/ Private: the driver's table in the declared types.
adapt:{[raw]
    t:update ts_event:1970.01.01D00:00+ts_event from raw;
    t:@[t;`symbol`action`side;{`$x}];
    `ts_event`symbol`action`side`price`size`sequence xcols t}

/ One window of records, half-open [range_from;range_to) on ts_event (ETL-08),
/ built through .qodbc's one escape function (E-08) and returned in the
/ declared types. Ordered so a window is the same table on every fetch.
/ @param h an ODBC handle from .qodbc.open
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the records in the window, in the contract's shape
query:{[h;range_from;range_to]
    sql:"SELECT ",select_list[]," FROM mbp10",
        " WHERE ts_event >= ",epoch_ns_literal[range_from],
        " AND ts_event < ",epoch_ns_literal[range_to],
        " ORDER BY symbol, ts_event, sequence";
    adapt .qodbc.run_sql[h;sql]}

/ Private: one fixture row's ten levels, flattened in level_fields order.
/ Prices step one cent away from the touch per level, sizes by 100.
fixture_levels:{[bid;ask;sz] raze {[bid;ask;sz;i] (bid-0.01*i;sz+100*i;ask+0.01*i;sz+100*i)}[bid;ask;sz] each til 10}

/ Four records for two symbols, taken from the shape of the real data: an
/ add on each side and a trade, with the touch moving between them.
fixture:{[]
    base:([] ts_event:2026.02.25D14:30:00.016039462 2026.02.25D14:30:00.024566996 2026.02.25D14:30:00.003676482 2026.02.25D14:30:01.000000000;
        symbol:`AAPL`AAPL`META`META;
        action:`A`T`A`C;
        side:`B`N`A`A;
        price:271.2 271.65 643.48 643.48;
        size:200 21 10 10;
        sequence:28151775 28158778 29559422 29563793);
    lv:flip level_fields!raze each flip (fixture_levels[271.45;271.66;500];fixture_levels[271.45;271.66;479];fixture_levels[642;643.;100];fixture_levels[642;643.01;100]);
    base,'lv}

.qsrc.register[source_name;
    `source`table`target`time_field`row_key`fields`types`query`fixture`tz`transport!
    (source_name;table;target;time_field;row_key;fields;types;query;fixture;tz;transport)];

\d .
