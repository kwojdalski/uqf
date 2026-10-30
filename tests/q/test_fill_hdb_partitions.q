// test_fill_hdb_partitions.q - scripts/gates/fill_hdb_partitions.q, the HDB
// repair behind `uqs data hdb-check --fix` and `--fix-hdb` (.qt.qproc.fillhdb).
//
// The script is a thin schema-driven wrapper around KX's dbmaint.q
// (lib/kx-dbmaint, #1423). It exits when it has run, so it cannot be loaded:
// each test builds a small HDB on disk, writes the schema the script should
// repair it to, and runs it as a child q (QCMD, as test_torq_backfill.q does).
// The assertions read the bytes back, which is the only place a repair that
// "worked" can be told from one that wrote something unreadable.
//
// Most of these pin behaviour the script already had before it called dbmaint:
// the refactor's promise is that the database it leaves behind is the same.
// The ones that could not pass before are the refusals - a directory that is
// not a table, a missing dbmaint, a destructive dbmaint call.


/ A scratch directory.
/ @return the directory, as a string
.qt.qproc.fillhdb.scratch:{[] first system "mktemp -d"}

/ The scratch root as a file symbol.
.qt.qproc.fillhdb.sym_of:{[p] hsym `$p}

/ Every regular file under a directory, with its bytes.
/ @param root the directory, as a string
/ @return a dictionary of file path to bytes
.qt.qproc.fillhdb.snapshot:{[root]
    fs:system "find ",root," -type f | sort";
    fs!read1 each hsym each `$fs}

/ Run the repair as a child q.
/ @param root the HDB root, as a string
/ @param schema the declared schema, as text
/ @param script the script to run, as a path
/ @return (output lines; exit code)
.qt.qproc.fillhdb.run_script:{[root;schema;script]
    sf:root,".database.q";
    (hsym `$sf) 0: enlist schema;
    out:root,".out.txt";
    / `env` first: q's `system` takes a command beginning with a q command letter
    / (here `q`) as its own, and runs nothing
    cmd:"env ",.qt.support.q_interpreter[]," ",script," ",root," ",sf,
        " < /dev/null > ",out," 2>&1; echo $?";
    code:"J"$first system cmd;
    (read0 hsym `$out;code)}

/ The repair, as the CLI runs it.
.qt.qproc.fillhdb.run:{[root;schema] .qt.qproc.fillhdb.run_script[root;schema;"scripts/gates/fill_hdb_partitions.q"]}

/ The partition's table directory.
.qt.qproc.fillhdb.dir:{[root;part;t] ` sv (.Q.par[hsym `$root;part;t];`)}

/ Write one table into one partition the way a writer does.
.qt.qproc.fillhdb.put:{[root;part;t;data]
    d:.qt.qproc.fillhdb.dir[root;part;t];
    / PeachQ's set does not create the partition directory above the table
    system "mkdir -p ",1_string d;
    d set .Q.en[hsym `$root;data]}

/ The declared schema used by most tests: `book` grew four columns after the
/ first partition was written, `other` is an older table.
.qt.qproc.fillhdb.schema:
    "book:([] time:`timestamp$(); sym:`g#`symbol$(); px:`float$(); venue:`symbol$(); bids:(); qty:`long$(); flag:`boolean$())\nother:([] time:`timestamp$(); v:`float$())\n"

/ An HDB where partition 1 predates venue, bids, qty and flag; partition 2
/ has them all; partition 3 holds no `other`.
.qt.qproc.fillhdb.build:{[]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    old:([] time:3#t0; sym:`EUR`USD`GBP; px:1.5 2.5 3.5);
    full:([] time:2#t0; sym:`EUR`JPY; px:4.5 5.5; venue:`LMAX`EBS; bids:(1 2f;3 4f); qty:7 8j; flag:10b);
    .qt.qproc.fillhdb.put[root;2026.01.01;`book;old];
    .qt.qproc.fillhdb.put[root;2026.01.01;`other;([] time:3#t0; v:1 2 3f)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`book;full];
    .qt.qproc.fillhdb.put[root;2026.01.02;`other;([] time:2#t0; v:4 5f)];
    .qt.qproc.fillhdb.put[root;2026.01.03;`book;full];
    root}

.qt.qproc.fillhdb.parts:2026.01.01 2026.01.02 2026.01.03

/ The columns `.d` lists for a table in a partition.
.qt.qproc.fillhdb.cols_of:{[root;part;t] get ` sv .qt.qproc.fillhdb.dir[root;part;t],`.d}

/ --- what is repaired ------------------------------------------------------

.qt.qproc.fillhdb.test_a_partially_missing_schema_is_made_whole:{[t]
    root:.qt.qproc.fillhdb.build[];
    r:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    .qunit.assertEquals[r 1;0;"the repair exits clean"];
    declared:`book`other!(`time`sym`px`venue`bids`qty`flag;`time`v);
    pairs:.qt.qproc.fillhdb.parts cross key declared;
    got:{[root;pr] .qt.qproc.fillhdb.cols_of[root;pr 0;pr 1]}[root] each pairs;
    want:declared {x 1} each pairs;
    .qunit.assertEquals[got;want;
        "every partition holds every declared table with every declared column in `.d`"];
    rows:{[root;p] count get ` sv .qt.qproc.fillhdb.dir[root;p;`book],`time}[root] each .qt.qproc.fillhdb.parts;
    lens:{[root;p] count get ` sv .qt.qproc.fillhdb.dir[root;p;`book],`venue}[root] each .qt.qproc.fillhdb.parts;
    .qunit.assertEquals[lens;rows;"a filled column is as long as the partition it is in"]};

.qt.qproc.fillhdb.test_a_table_absent_from_every_partition_is_written_without_a_reference:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    .qt.qproc.fillhdb.put[root;2026.01.01;`book;([] time:2#t0; sym:`EUR`USD; px:1 2f)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`book;([] time:1#t0; sym:enlist `EUR; px:enlist 3f)];
    schema:"book:([] time:`timestamp$(); sym:`symbol$(); px:`float$())\nnewtab:([] time:`timestamp$(); sym:`symbol$(); size:`long$(); note:())\n";
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"no partition holds newtab, and the repair still exits clean"];
    ok:{[root;p] (`time`sym`size`note~.qt.qproc.fillhdb.cols_of[root;p;`newtab]) and
        0=count get ` sv .qt.qproc.fillhdb.dir[root;p;`newtab],`size}[root] each 2026.01.01 2026.01.02;
    .qunit.assertEquals[ok;11b;"newtab exists in each partition, with its declared columns and no rows"]};

.qt.qproc.fillhdb.test_a_directory_that_is_not_a_table_is_left_alone_and_the_run_fails:{[t]
    root:.qt.qproc.fillhdb.build[];
    / a directory of the right name holding no .d is not a table this script may write into
    system "mkdir -p ",root,"/2026.01.01/newtab";
    (hsym `$root,"/2026.01.01/newtab/keep.txt") 0: enlist "stranger";
    before:.qt.qproc.fillhdb.snapshot[root,"/2026.01.01/newtab"];
    r:.qt.qproc.fillhdb.run[root;"newtab:([] time:`timestamp$())\n"];
    .qunit.assertEquals[r 1;1;"a database that is still not whole exits 1"];
    .qunit.assertTrue[any (r 0) like "*no .d*";"and names what it left alone"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root,"/2026.01.01/newtab"];before;
        "the stranger directory is byte-for-byte what it was"]};

.qt.qproc.fillhdb.test_a_directory_that_is_not_a_partition_is_ignored:{[t]
    root:.qt.qproc.fillhdb.build[];
    (hsym `$root,"/2026.01.01.bak/keep") set 1 2 3;
    (hsym `$root,"/12") set 1 2 3;
    / book is whole here; only `other` is short, in the third partition
    r:.qt.qproc.fillhdb.run[root;"book:([] time:`timestamp$(); sym:`symbol$(); px:`float$())\nother:([] time:`timestamp$(); v:`float$())\n"];
    .qunit.assertEquals[r 1;0;"clean"];
    .qunit.assertEquals[key hsym `$root,"/2026.01.01.bak";enlist `keep;"a lookalike directory gained nothing"];
    .qunit.assertEquals[get hsym `$root,"/12";1 2 3;"and a file named like an int partition is not written into"]};

/ --- what is preserved -----------------------------------------------------

.qt.qproc.fillhdb.test_existing_columns_and_rows_are_byte_identical_after_the_repair:{[t]
    root:.qt.qproc.fillhdb.build[];
    before:.qt.qproc.fillhdb.snapshot[root];
    dfiles:(key before) where (key before) like "*/.d";
    before_d:{get hsym `$x} each dfiles;
    r:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    .qunit.assertEquals[r 1;0;"clean"];
    after:.qt.qproc.fillhdb.snapshot[root];
    / `.d` is APPENDED to, so it is judged as a prefix; every other file that existed must not move
    same:(key before) except dfiles;
    .qunit.assertEquals[after same;before same;
        "every column file and the sym file that existed before is byte-identical afterwards"];
    / the symbols read back from the old partition are the symbols that were written
    sy:get hsym `$root,"/sym";
    old:sy@get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`book],`sym;
    .qunit.assertEquals[old;`EUR`USD`GBP;"the old partition's symbols still decode to what was written"];
    after_d:{get hsym `$x} each dfiles;
    kept:all {[a;b] a~(count a)#b}'[before_d;after_d];
    .qunit.assertTrue[kept;"each .d keeps the columns it had, in order, and is only appended to"]};

.qt.qproc.fillhdb.test_a_historical_table_and_column_the_schema_no_longer_declares_is_kept:{[t]
    root:.qt.qproc.fillhdb.build[];
    t0:2026.01.01D00:00:00.000000000;
    .qt.qproc.fillhdb.put[root;2026.01.01;`retired;([] time:2#t0; gone:1 2f)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`retired;([] time:1#t0; gone:3f)];
    before:.qt.qproc.fillhdb.snapshot[root,"/2026.01.01/retired"];
    / book no longer declares px; retired is not declared at all
    schema:"book:([] time:`timestamp$(); sym:`symbol$())\n";
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root,"/2026.01.01/retired"];before;"the retired table is untouched"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`book];`time`sym`px;
        "a column the schema dropped is still listed, and nothing was deleted"]};

/ --- what is written -------------------------------------------------------

.qt.qproc.fillhdb.test_every_declared_type_is_filled_with_its_own_null:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    .qt.qproc.fillhdb.put[root;2026.01.01;`wide;([] time:3#t0; sym:`a`b`c)];
    types:`p`f`j`i`h`e`b`x`c`d`t`n`m`u`v;
    empties:(`timestamp$();`float$();`long$();`int$();`short$();`real$();`boolean$();`byte$();`char$();
        `date$();`time$();`timespan$();`month$();`minute$();`second$());
    names:`$"c_",/:string types;
    decl:"wide:([] time:`timestamp$(); sym:`symbol$()",(raze {"; ",string[x],":",.Q.s1 y}'[names;empties]),")\n";
    r:.qt.qproc.fillhdb.run[root;decl];
    .qunit.assertEquals[r 1;0;"clean"];
    files:{[root;c] get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`wide],c}[root] each names;
    .qunit.assertEquals[files;3#/:empties;
        "each column is the declared type's null, three times - the partition's own row count"];
    .qunit.assertEquals[type each files;type each 3#/:empties;"and has the declared type, not a general list"]};

.qt.qproc.fillhdb.test_a_nested_column_is_filled_with_empty_lists_of_the_partitions_length:{[t]
    root:.qt.qproc.fillhdb.build[];
    .qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    bids:get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`book],`bids;
    .qunit.assertEquals[count bids;3;"one entry per row"];
    .qunit.assertEquals[count each bids;0 0 0;"each entry an empty list - not a null, not a missing row"]};

.qt.qproc.fillhdb.test_a_filled_symbol_column_is_enumerated_against_the_hdb_sym_file:{[t]
    root:.qt.qproc.fillhdb.build[];
    before:read1 hsym `$root,"/sym";
    .qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    filled:get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`book],`venue;
    .qunit.assertEquals[type filled;20h;"an enumeration - a raw symbol vector would map and be unreadable by anything else"];
    .qunit.assertEquals[`sym;key filled;"enumerated over the HDB's own sym domain"];
    .qunit.assertTrue[all null filled;"every row null"];
    .qunit.assertEquals[read1 hsym `$root,"/sym";before;"a null needs no new symbol, so the sym file is not rewritten"];
    live:get ` sv .qt.qproc.fillhdb.dir[root;2026.01.02;`book],`venue;
    .qunit.assertEquals[(get hsym `$root,"/sym")@live;`LMAX`EBS;"and a populated partition's venue still decodes"]};

.qt.qproc.fillhdb.test_the_d_file_lists_columns_in_declared_order_and_stays_a_symbol_list:{[t]
    root:.qt.qproc.fillhdb.build[];
    / the old partition holds time sym px, in that order; the repair appends in declared order
    .qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    d:.qt.qproc.fillhdb.cols_of[root;2026.01.01;`book];
    .qunit.assertEquals[d;`time`sym`px`venue`bids`qty`flag;"existing order kept, missing columns appended as declared"];
    .qunit.assertEquals[type d;11h;".d is a plain symbol list"];
    .qunit.assertEquals[count distinct d;count d;"no column is listed twice"]};

/ --- idempotency -----------------------------------------------------------

.qt.qproc.fillhdb.test_repeating_a_successful_repair_changes_nothing:{[t]
    root:.qt.qproc.fillhdb.build[];
    r1:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    .qunit.assertEquals[r1 1;0;"clean"];
    after1:.qt.qproc.fillhdb.snapshot[root];
    / age every file and directory, so any write by the second run is visible as a fresh mtime
    system "find ",root," -exec touch -t 200001010000 {} +";
    r2:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.schema];
    .qunit.assertEquals[r2 1;0;"clean again"];
    .qunit.assertTrue[any (r2 0) like "*already rectangular - nothing to do*";"it says it had nothing to do"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after1;"no file's bytes changed"];
    fresh:system "find ",root," -mtime -1";
    .qunit.assertEquals[fresh;();"and no file or directory was written, whatever its bytes"]};

.qt.qproc.fillhdb.test_repeating_a_table_only_repair_changes_nothing:{[t]
    root:.qt.qproc.fillhdb.build[];
    schema:"book:([] time:`timestamp$(); sym:`symbol$(); px:`float$())\nother:([] time:`timestamp$(); v:`float$())\nnewtab:([] time:`timestamp$())\n";
    .qunit.assertEquals[.qt.qproc.fillhdb.run[root;schema] 1;0;"clean"];
    after1:.qt.qproc.fillhdb.snapshot[root];
    system "find ",root," -exec touch -t 200001010000 {} +";
    r2:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertTrue[any (r2 0) like "*already rectangular - nothing to do*";"nothing to do the second time"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after1;"no file's bytes changed"];
    .qunit.assertEquals[system "find ",root," -mtime -1";();"and nothing was written, whatever its bytes"]};

/ --- what is refused -------------------------------------------------------

.qt.qproc.fillhdb.test_a_column_of_another_type_is_neither_touched_nor_hidden:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    / qty is stored long and declared float: a migration, not a gap. Nothing is missing, so
    / the only thing the repair could do here is wrongly rewrite the column.
    .qt.qproc.fillhdb.put[root;2026.01.01;`book;([] time:2#t0; sym:`EUR`USD; qty:7 8j)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`book;([] time:1#t0; sym:enlist `EUR; qty:enlist 9j)];
    schema:"book:([] time:`timestamp$(); sym:`symbol$(); qty:`float$())\n";
    before:.qt.qproc.fillhdb.snapshot[root,"/2026.01.02"];
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"the filler has nothing to fill there"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root,"/2026.01.02"];before;"the mistyped partition is byte-identical"];
    / the partitions it reads are named to it: hdb_layout.q's, in `uqs data check` (#1783)
    (hsym `$root,".partitions") 0: (root,"/2026.01.01";root,"/2026.01.02");
    c:system "env ",.qt.support.q_interpreter[]," scripts/gates/hdb_column_types.q ",root,
        ".partitions ",root,".database.q -q < /dev/null";
    .qunit.assertTrue[any c like "type_change|",root,"/2026.01.02|book|qty|j|f";
        "hdb_column_types.q reports it: stored as j, declared f"]};

.qt.qproc.fillhdb.test_the_destructive_dbmaint_operations_are_not_reachable:{[t]
    code:" " sv {$[("/"=first x) or 0=count x; ""; x]} each read0 `:scripts/gates/fill_hdb_partitions.q;
    banned:`addtable`addcol`add1table`deletecol`delete1col`castcol`renamecol`rename1col`rentable`ren1table`copycol`copy1col`fncol`fn1col`fixtable`fix1table`reordercols`setattrcol`clearattrcol;
    hit:banned where {[code;b] 0<count ss[code;string b]}[code] each banned;
    .qunit.assertEquals[hit;`symbol$();"the script calls no dbmaint operation that rewrites, drops or blindly adds"];
    .qunit.assertTrue[0<count ss[code;"add1col"];"and does call the per-directory column add"]};

.qt.qproc.fillhdb.test_a_tree_without_the_vendored_dbmaint_is_refused_by_name:{[t]
    tree:.qt.qproc.fillhdb.scratch[];
    system "mkdir -p ",tree,"/scripts/gates";
    system "cp scripts/gates/fill_hdb_partitions.q ",tree,"/scripts/gates/";
    root:.qt.qproc.fillhdb.build[];
    r:.qt.qproc.fillhdb.run_script[root;.qt.qproc.fillhdb.schema;tree,"/scripts/gates/fill_hdb_partitions.q"];
    .qunit.assertTrue[any (r 0) like "*no dbmaint.q at*";"it says what is missing, rather than failing on an undefined name"];
    .qunit.assertEquals[r 1;1;"and exits 1: a repair that never ran must not read as one that did"]};

.qt.qproc.fillhdb.test_the_repair_loads_dbmaint_from_a_copy_of_the_tree_laid_out_like_a_release:{[t]
    tree:.qt.qproc.fillhdb.scratch[];
    system "mkdir -p ",tree,"/scripts/gates ",tree,"/lib/kx-dbmaint";
    system "cp scripts/gates/fill_hdb_partitions.q ",tree,"/scripts/gates/";
    system "cp lib/kx-dbmaint/* ",tree,"/lib/kx-dbmaint/";
    root:.qt.qproc.fillhdb.build[];
    / from a directory that is not the tree: the script finds dbmaint by its own path, not the cwd
    r:.qt.qproc.fillhdb.run_script[root;"book:([] time:`timestamp$(); sym:`symbol$(); px:`float$())\nnewtab:([] time:`timestamp$())\n";tree,"/scripts/gates/fill_hdb_partitions.q"];
    .qunit.assertEquals[r 1;0;"a release-shaped tree repairs"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`newtab];enlist `time;"and writes the table dbmaint's allpaths named"]};
