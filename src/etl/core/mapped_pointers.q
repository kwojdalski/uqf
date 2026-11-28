/ mapped_pointers.q - the pointer columns of a table's declared conditions, as the partition writers store them (.qetl.mapped.pointers, #3000).
/ .
/ A table declares a condition (.qetl.plant.condition, src/etl/plant_registry.q); every writer that numbers a partition's rows
/ after the sort - uqf's finish (io_hdb.q), TorQ's end-of-day sort (scripts/processes/uqs_row_ids.q) and `uqs data check
/ --apply` - also writes each condition's `<name>_rid` column here, from the SAME function, so the three cannot disagree. A row's
/ pointer is the position of the latest row of its run (the same equality keys of the declared sort) at or before it that
/ passes the condition, null if none: .qetl.mapped.pointer, one vectorised pass over the partition's columns.
/ .
/ WHAT IT READS. The sort's equality keys and the columns the condition's expression reads, whole, once per partition - the
/ writer already holds the partition's sort in one pass, and the search then reads nothing but the pointer. An enumerated key is
/ compared as its enumeration indices, so no domain is needed to write; a column the expression reads is decoded, which needs the
/ HDB's `sym` domain loaded, as any enumerated read does.
/ .
/ Run where the plant is loaded. This file is loaded by src/etl/init.q and, with plant_registry.q, plant_tables.q and mapped_books.q,
/ by the processes that run TorQ's sort (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py).

\d .

/ A table directory as a file symbol, whether it was given with a trailing slash or without.
/ @param dir a file symbol
/ @return the directory without a trailing slash
/ @private
.qetl.mapped.pointers.base:{[dir] s:string dir; $[(1<count s) and "/"=last s; -1_s; s]}

/ One column of a table directory, as the pointer pass reads it: an enumeration as its indices when it is a key, decoded when a
/ condition reads it.
/ @private
.qetl.mapped.pointers.col:{[base;decode;c]
    v:get hsym `$base,"/",string c;
    $[(type v) within 20 76h; $[decode; value v; "j"$v]; v]}

/ The pointer columns of a table directory, computed from its rows as they now lie.
/ @param dir the table directory, a file symbol
/ @param tbl the table
/ @return a dictionary `<name>_rid -> a long per row; empty when the table declares no condition
/ @eg .qetl.mapped.pointers.compute[`:/no/such/dir;`no_such_table]  ->  (`symbol$())!()
.qetl.mapped.pointers.compute:{[dir;tbl]
    conds:select name, expr from 0!.qetl.plant.conditions where table=tbl;
    if[0=count conds; :(`symbol$())!()];
    base:.qetl.mapped.pointers.base dir;
    ks:-1_exec column from .qetl.plant.sort_rules where table=tbl;
    keycols:.qetl.mapped.pointers.col[base;0b] each ks;
    one:{[tbl;base;keycols;e]
        reads:.qetl.plant.condition_reads[tbl;e];
        rows:flip reads!.qetl.mapped.pointers.col[base;1b] each reads;
        .qetl.mapped.pointer[keycols;.qetl.plant.condition_ok[rows;e]]}[tbl;base;keycols];
    (.qetl.plant.pointer_col each conds`name)!one each conds`expr}

/ Write the pointer columns of a partition whose rows are in their final order: the columns the table declares and `.d` holds.
/ @param dir the table directory (a staged copy, or a partition), a file symbol
/ @param tbl the table
/ @return the columns written
/ @eg .qetl.mapped.pointers.write[`:/no/such/dir;`no_such_table]  ->  `symbol$()
.qetl.mapped.pointers.write:{[dir;tbl]
    p:.qetl.mapped.pointers.compute[dir;tbl];
    if[0=count p; :`symbol$()];
    base:.qetl.mapped.pointers.base dir;
    have:get hsym `$base,"/.d";
    p:(key[p] inter have)#p;
    {[base;c;v] (hsym `$base,"/",string c) set v}[base]'[key p;value p];
    key p}

/ Why a partition's pointer columns are not what its rows say they are, or "" when they are (or the table has none).
/ @param dir the table directory, a file symbol
/ @param tbl the table
/ @return "" or the first column that disagrees
/ @eg .qetl.mapped.pointers.reason[`:/no/such/dir;`no_such_table]  ->  ""
.qetl.mapped.pointers.reason:{[dir;tbl]
    p:.qetl.mapped.pointers.compute[dir;tbl];
    if[0=count p; :""];
    base:.qetl.mapped.pointers.base dir;
    have:get hsym `$base,"/.d";
    bad:(key p) where {[base;have;c;v] $[c in have; not v~get hsym `$base,"/",string c; 0b]}[base;have]'[key p;value p];
    $[count bad; (string first bad)," does not hold each row's pointer"; ""]}

\d .
