/ uqs_row_ids.q - number market_data's rows once TorQ's end-of-day sort has put them in order (#2288).
/ .
/ A market_data row's `rid` is its position in its partition AS SORTED, so a consumer can read the
/ depth of a few matched books by indexing a mapped ladder with their rids - paying those rows'
/ pages - instead of scanning it. The rid cannot be known earlier: the sort (sort.csv: sym, source,
/ source_time with p# on sym) is what fixes a row's position, and the wdb appends to a partition
/ several times a day before it sorts. So the numbers are written AFTER the sort, by wrapping the
/ vendored `.sort.sorttab` (lib/torq is never edited): the original runs, then the column is
/ rewritten as til count.
/ .
/ Loaded AFTER ${KDBCODE}/processes/wdb.q into every process that runs `.sort.sorttab` - the wdb,
/ the sort process and the sort workers (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py).
/ uqf's own finish does the same for the partitions it writes (.qetl.io.assign_row_ids), and
/ `uqs data check --apply` for the partitions older than the column.
/ .
/ Which tables, and the column, are .qetl.plant.row_ids' (src/etl/plant_tables.q); this process
/ loads no plant, so the one table that has a row number is named here, and
/ python/uqs/tests/test_row_ids_overlay.py holds the two equal.

.qtorq.row_ids.tables:enlist[`market_data]!enlist `rid

/ Number the rows of some table directories in the order they are in now.
/ @param tbl the table
/ @param dirs the table's partition directories, file symbols
/ @return the number of directories numbered
.qtorq.row_ids.number:{[tbl;dirs]
    if[not tbl in key .qtorq.row_ids.tables; :0];
    col:.qtorq.row_ids.tables tbl;
    done:{[col;dloc]
        base:string dloc;
        if["/"=last base; base:-1_base];
        c:@[get;hsym `$base,"/.d";{[e] `symbol$()}];
        others:c except col;
        if[(not col in c) or 0=count others; :0b];
        (hsym `$base,"/",string col) set til count get hsym `$base,"/",string first others;
        1b}[col] each distinct (),dirs;
    sum done}

/ The vendored sort, kept; the wrapper below runs it and then numbers the rows.
if[not `uqs_sorttab in key `.sort; .sort.uqs_sorttab:.sort.sorttab];

/ TorQ's .sort.sorttab for one table (a table name, or a pair of the name and its directories),
/ then the row numbers of the tables that carry them.
/ @param d a table name or (table name; directories)
.sort.sorttab:{[d]
    .sort.uqs_sorttab d;
    if[2=count d; .qtorq.row_ids.number[first d;last d]];
    }
