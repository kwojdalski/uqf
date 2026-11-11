// test_fill_hdb_partitions.q - scripts/hdb/hdb_fill.q, the HDB
// repair behind `uqs data check --apply` and `uqs deploy push --fix-hdb` (.qt.qproc.fillhdb).
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
.qt.qproc.fillhdb.run:{[root;schema] .qt.qproc.fillhdb.run_script[root;schema;"scripts/hdb/hdb_fill.q"]}

/ The repair with the markout configuration migration asked for (`uqs data check --apply`, #1999),
/ recording its snapshots in `stat`, a status directory.
/ @param root the HDB root, as a string
/ @param schema the declared schema, as text
/ @param stat the status directory the snapshot store lives in
/ @return (output lines; exit code)
.qt.qproc.fillhdb.run_migrating:{[root;schema;stat]
    sf:root,".database.q";
    (hsym `$sf) 0: enlist schema;
    out:root,".out.txt";
    cmd:"env UQF_STATUS_DIR=",stat," ",.qt.support.q_interpreter[]," scripts/hdb/hdb_fill.q ",root," ",sf,
        " migrate < /dev/null > ",out," 2>&1; echo $?";
    code:"J"$first system cmd;
    (read0 hsym `$out;code)}

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

/ --- the clock column is attributed, not nulled (#1925) ---------------------

/ The clock a partition's rows hold, decoded against the HDB's sym file.
.qt.qproc.fillhdb.clock_of_rows:{[root;part;t]
    (get hsym `$root,"/sym") get ` sv .qt.qproc.fillhdb.dir[root;part;t],`clock}

.qt.qproc.fillhdb.test_a_market_datas_clock_is_derived_from_market_and_source:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    / one partition per row kind: the plant's own FX feeds, a crypto venue, a source the
    / rules do not name, and a book with no market recorded at all
    .qt.qproc.fillhdb.put[root;2026.01.01;`market_data;([] time:4#t0; sym:`EURUSD`EURUSD`BTCUSD`EURUSD; source:`UQFFX`UQFDEPTH`binance_spot`LP_X; market:`fx`fx`crypto`fx)];
    schema:"market_data:([] time:`timestamp$(); sym:`symbol$(); source:`symbol$(); market:`symbol$(); clock:`symbol$())\n";
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.clock_of_rows[root;2026.01.01;`market_data];`receipt`receipt`venue`unknown;
        "the plant's FX feeds are receipt, any crypto source is venue, and a source no rule names is unknown - never null"];
    .qunit.assertEquals[type get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`market_data],`clock;20h;"an enumerated symbol column"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`market_data];`time`sym`source`market`clock;"clock is appended to .d"];
    .qunit.assertTrue[any (r 0) like "*market_data: clock from the rules*";"and the run says how many rows it attributed"]};

.qt.qproc.fillhdb.test_an_executions_clock_is_derived_from_venue:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    .qt.qproc.fillhdb.put[root;2026.01.01;`executions;([] time:4#t0; sym:`EURUSD`BTCUSD`BTCUSD`EURUSD; venue:`fx`binance_spot`bybit_spot`other)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`executions;([] time:1#t0; sym:enlist `EURUSD; venue:enlist `)];
    schema:"executions:([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); clock:`symbol$())\n";
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.clock_of_rows[root;2026.01.01;`executions];`receipt`receipt`receipt`unknown;
        "FX and the crypto venues the plant polls are receipt; a venue no rule names is unknown"];
    .qunit.assertEquals[.qt.qproc.fillhdb.clock_of_rows[root;2026.01.02;`executions];enlist `unknown;"a fill with no venue is unknown, not null"]};

.qt.qproc.fillhdb.test_a_partition_older_than_its_key_columns_is_unknown_and_a_second_run_changes_nothing:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    / predates `market` AND `source`: nothing to attribute the clock from
    .qt.qproc.fillhdb.put[root;2026.01.01;`market_data;([] time:2#t0; sym:`EURUSD`GBPUSD)];
    schema:"market_data:([] time:`timestamp$(); sym:`symbol$(); source:`symbol$(); market:`symbol$(); clock:`symbol$())\n";
    .qunit.assertEquals[(.qt.qproc.fillhdb.run[root;schema]) 1;0;"clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.clock_of_rows[root;2026.01.01;`market_data];`unknown`unknown;"no key columns: every row unknown"];
    after1:.qt.qproc.fillhdb.snapshot[root];
    r2:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertTrue[any (r2 0) like "*already rectangular - nothing to do*";"nothing to do the second time"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after1;"the attributed column is not rewritten"]};

/ --- crypto_trades.source_time: history has no venue time (#1936) ------------

.qt.qproc.fillhdb.test_a_crypto_trades_partition_older_than_source_time_gets_null_not_a_copy_of_time:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D00:00:00.000000000;
    / one partition recorded before the column existed, one after it, with the venue's own (earlier) time
    .qt.qproc.fillhdb.put[root;2026.01.01;`crypto_trades;([] time:2#t0; sym:2#`$"BTC-USDT"; venue:2#`binance_spot)];
    .qt.qproc.fillhdb.put[root;2026.01.02;`crypto_trades;([] time:1#t0+0D01; source_time:enlist t0+0D00:59:59.5; sym:enlist `$"BTC-USDT"; venue:enlist `binance_spot)];
    schema:"crypto_trades:([] time:`timestamp$(); source_time:`timestamp$(); sym:`symbol$(); venue:`symbol$())\n";
    r:.qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[r 1;0;"clean"];
    st:{[root;part] get ` sv .qt.qproc.fillhdb.dir[root;part;`crypto_trades],`source_time}[root];
    .qunit.assertEquals[st 2026.01.01;2#0Np;"the old partition has NO venue time: null, which the executions mapping reads as the receipt clock"];
    .qunit.assertTrue[not (st 2026.01.01)~(get ` sv .qt.qproc.fillhdb.dir[root;2026.01.01;`crypto_trades],`time);"and never a copy of the receipt time, which would pass for a venue stamp"];
    .qunit.assertEquals[st 2026.01.02;enlist t0+0D00:59:59.5;"a partition that already has the venue's time keeps it"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`crypto_trades];`time`sym`venue`source_time;"appended to .d"];
    after1:.qt.qproc.fillhdb.snapshot[root];
    .qt.qproc.fillhdb.run[root;schema];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after1;"and a second run changes nothing"]};

/ --- the FX markout tables gain the markout contract's columns, derived (#1935) -------

/ A column of a partition's table, decoded against the HDB's sym file when it is a symbol column.
.qt.qproc.fillhdb.col_of:{[root;part;t;c]
    v:get ` sv .qt.qproc.fillhdb.dir[root;part;t],c;
    $[20h=type v; (get hsym `$root,"/sym") v; v]}

/ A USDPLN sell at 3.99 and a EURUSD buy at 1.1001 as the long table stored them before the contract:
/ every row of an (execution, horizon) repeats the fill, and price_t0/price_t1 are per component.
.qt.qproc.fillhdb.old_long:{[]
    t0:2026.01.01D10:00:10.000000000;
    ([] time:7#t0; source_time:7#t0-0D00:00:10; venue:7#`a; fill_id:(5#`y1),2#`x1; sym:(5#`USDPLN),2#`EURUSD; side:(5#-1),2#1; size:7#1e6;
        horizon:7#0D00:00:01; kind:`total`execution`leg`leg`residual`total`execution; step:0 0 1 2 0 0 0;
        leg:``,`EURUSD`EURPLN,```; invert:0b,0b,1b,0b,0b,0b,0b;
        price_t0:3.99 3.99 1.25 5 0n 1.1001 1.1001; price_t1:5.5 4 1 5.5 0n 1.1005 1.1;
        pips:-15100 -100 -10000 -5000 0 4 -1f; status:`ok`ok`ok`ok`reconciled`ok`ok; pip_factor:(5#10000),2#10000; version:7#`proposed_v1)}

.qt.qproc.fillhdb.fx_schema:"fx_markout:([] time:`timestamp$(); source_time:`timestamp$(); clock:`symbol$(); venue:`symbol$(); fill_id:`symbol$(); sym:`symbol$(); side:`long$(); size:`float$(); horizon:`timespan$(); kind:`symbol$(); step:`long$(); leg:`symbol$(); invert:`boolean$(); price:`float$(); price_t0:`float$(); price_t1:`float$(); quote_ccy:`symbol$(); ref_t0:`float$(); ref_t1:`float$(); move:`float$(); pips:`float$(); pip_factor:`long$(); pip_value:`float$(); quote_amount:`float$(); rate:`float$(); rate_source:`symbol$(); rate_sym:`symbol$(); rate_time:`timestamp$(); report_amount:`float$(); status:`symbol$(); config_id:`symbol$(); version:`symbol$())\n"

.qt.qproc.fillhdb.test_fx_markouts_gain_the_contracts_columns_derived_from_the_stored_rows:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    .qt.qproc.fillhdb.put[root;2026.01.01;`fx_markout;.qt.qproc.fillhdb.old_long[]];
    stat:.qt.qproc.fillhdb.scratch[];
    r:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.fx_schema;stat];
    .qunit.assertEquals[r 1;0;"clean"];
    c:.qt.qproc.fillhdb.col_of[root;2026.01.01;`fx_markout;];
    .qunit.assertEquals[c`clock;7#`unknown;"the clock of a stored row is unknown, never a guess and never null"];
    .qunit.assertEquals[count distinct c`config_id;1;"the stored rows name one legacy configuration"];
    .qunit.assertTrue[(string first c`config_id) like "markout_legacy_*";"a legacy snapshot"];
    .qunit.assertEquals[c`quote_ccy;(5#`PLN),2#`USD;"the pair's quote leg"];
    .qunit.assertEquals[c`price;(5#3.99),2#1.1001;"the fill price, read off the total row of the (execution, horizon)"];
    .qunit.assertEquals[c`ref_t0;(5#4f),2#1.1;"the reference at the execution, read off the execution residual's price_t1"];
    .qunit.assertEquals[c`ref_t1;(5#5.5),2#1.1005;"the reference at the horizon, read off the total's price_t1"];
    .qunit.assertEquals[c`move;-1.51 -0.01 -1 -0.5 0 4e-4 -1e-4;"pips over the pip factor"];
    .qunit.assertEquals[c`pip_value;7#100f;"1e6 over a pip factor of 10000"];
    .qunit.assertEquals[c`quote_amount;-1510000 -10000 -1000000 -500000 0 400 -100f;"the size times the move"];
    .qunit.assertTrue[all null c`report_amount;"what no stored row can say stays null: no rate was ever declared"];
    .qunit.assertTrue[all null c`rate;"the rate"];
    .qunit.assertEquals[c`pips;-15100 -100 -10000 -5000 0 4 -1f;"and nothing already stored is touched"];
    after:.qt.qproc.fillhdb.snapshot[root];
    r2:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.fx_schema;stat];
    .qunit.assertTrue[any (r2 0) like "*already rectangular - nothing to do*";"nothing to do the second time"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after;"the derived columns are not rewritten"]};

.qt.qproc.fillhdb.test_the_wide_fx_markout_gains_the_same_derived_columns:{[t]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D10:00:10.000000000;
    / the wide table before the contract: a row per (execution, horizon), the markout in total_pips
    .qt.qproc.fillhdb.put[root;2026.01.01;`fx_markout_wide;([] time:2#t0; sym:`USDPLN`EURUSD; size:2#1e6; pip_factor:2#10000; total_pips:-15100 4f)];
    schema:"fx_markout_wide:([] time:`timestamp$(); sym:`symbol$(); size:`float$(); pip_factor:`long$(); total_pips:`float$(); clock:`symbol$(); quote_ccy:`symbol$(); move:`float$(); pip_value:`float$(); quote_amount:`float$(); report_amount:`float$(); config_id:`symbol$())\n";
    r:.qt.qproc.fillhdb.run_migrating[root;schema;.qt.qproc.fillhdb.scratch[]];
    .qunit.assertEquals[r 1;0;"clean"];
    c:.qt.qproc.fillhdb.col_of[root;2026.01.01;`fx_markout_wide;];
    .qunit.assertEquals[(c`clock;c`quote_ccy;count distinct c`config_id);(2#`unknown;`PLN`USD;1);"clock unknown, the pair's quote leg, one legacy configuration"];
    .qunit.assertEquals[(c`move;c`pip_value;c`quote_amount);(-1.51 4e-4;2#100f;-1510000 400f);"the markout over the pip factor, and its money in the quote currency"];
    .qunit.assertTrue[all null c`report_amount;"and nothing is converted that no rate was declared for"]};

/ --- the markout configuration migration (#1999) ---------------------------------------

/ crypto_markout after the migration, in the one shape the tests need: the columns the migration
/ does not touch, and config_id where the three configuration columns were.
.qt.qproc.fillhdb.cfg_schema:"crypto_markout:([] time:`timestamp$(); fill_id:`symbol$(); status:`symbol$(); config_id:`symbol$())\n"

/ A partition as the markout writers left it before snapshots: the three configuration columns beside the rest.
.qt.qproc.fillhdb.legacy_rows:{[cv;rt;rc]
    n:count cv;
    ([] time:n#2026.01.01D10:00:00.000000000; fill_id:`$"f",/:string til n; status:n#`ok; contract_version:cv; rate_timing:rt; report_ccy:rc)}

/ A scratch HDB with crypto_markout in three partitions: one configuration; two (one of them the first's);
/ and one older than the configuration columns, which holds none of them.
.qt.qproc.fillhdb.cfg_build:{[]
    root:.qt.qproc.fillhdb.scratch[];
    .qt.qproc.fillhdb.put[root;2026.01.01;`crypto_markout;.qt.qproc.fillhdb.legacy_rows[3#1;3#`execution;3#`USD]];
    .qt.qproc.fillhdb.put[root;2026.01.02;`crypto_markout;.qt.qproc.fillhdb.legacy_rows[1 1 1 1;`execution`horizon`horizon`execution;`USD`EUR`EUR`USD]];
    .qt.qproc.fillhdb.put[root;2026.01.03;`crypto_markout;([] time:2#2026.01.03D10:00:00.000000000; fill_id:`g0`g1; status:`ok`unavailable)];
    root}

.qt.qproc.fillhdb.test_the_migration_folds_the_configuration_columns_into_legacy_snapshots_and_drops_them:{[t]
    root:.qt.qproc.fillhdb.cfg_build[];
    stat:.qt.qproc.fillhdb.scratch[];
    before:.qt.qproc.fillhdb.snapshot[root];
    r:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.cfg_schema;stat];
    .qunit.assertEquals[r 1;0;"the migration exits clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;;`crypto_markout] each .qt.qproc.fillhdb.parts;3#enlist `time`fill_id`status`config_id;"every partition lists the declared columns and not the three that were folded"];
    gone:raze {[root;p] ` sv/: .qt.qproc.fillhdb.dir[root;p;`crypto_markout],/:`contract_version`rate_timing`report_ccy}[root] each .qt.qproc.fillhdb.parts;
    .qunit.assertEquals[{()~key x} each gone;9#1b;"and their files are gone"];
    / the snapshots: one per distinct triple, across all partitions, each with the triple it stands for
    snap:{[stat] setenv[`UQF_STATUS_DIR;stat]; .qetl.snapshot.reload[]; .qetl.snapshot.audit[]};
    a:snap stat;
    .qunit.assertEquals[count a;3;"three distinct triples in the whole database, three snapshots - (1;execution;USD) once, though two partitions hold it"];
    conts:.qetl.snapshot.decode each a`content;
    triples:{[c] `contract_version`rate_timing`report_ccy!(c`contract_version;c`rate_timing;c`report_ccy)} each conts;
    .qunit.assertEquals[asc triples;asc ((`contract_version`rate_timing`report_ccy)!(1;`execution;`USD);(`contract_version`rate_timing`report_ccy)!(1;`horizon;`EUR);(`contract_version`rate_timing`report_ccy)!(1;`;`));"the triples, and the older partition's own answer: contract 1, the rest unknown"];
    .qunit.assertTrue[all {x`legacy} each conts;"each is marked legacy"];
    .qunit.assertTrue[all a`digest_ok;"with a good digest"];
    / the rows link to the snapshot of THEIR triple
    c1:.qt.qproc.fillhdb.col_of[root;2026.01.01;`crypto_markout;]`config_id;
    c2:.qt.qproc.fillhdb.col_of[root;2026.01.02;`crypto_markout;]`config_id;
    c3:.qt.qproc.fillhdb.col_of[root;2026.01.03;`crypto_markout;]`config_id;
    of:{[a;id] .qetl.snapshot.decode first exec content from a where snapshot_id=id};
    .qunit.assertEquals[(distinct c1;count distinct c2;(c2 0)=c2 3;count distinct c3);(enlist first c1;2;1b;1);"the first partition names one, the second two (rows 0 and 3 share), the third one"];
    .qunit.assertEquals[(of[a;first c1]`rate_timing;of[a;c2 1]`rate_timing;of[a;c2 1]`report_ccy;of[a;first c3]`contract_version);(`execution;`horizon;`EUR;1);"each row's snapshot is the one its old columns described"];
    .qunit.assertEquals[c2 0;first c1;"and the same triple in two partitions is the same snapshot"];
    / nothing else moved
    after:.qt.qproc.fillhdb.snapshot[root];
    kept:(key before) where {any x like/: ("*/crypto_markout/fill_id";"*/crypto_markout/status";"*/crypto_markout/time")} each key before;
    .qunit.assertEquals[after kept;before kept;"the columns that were not folded are byte-identical"]};

.qt.qproc.fillhdb.test_the_migration_is_idempotent_and_finishes_what_an_interruption_left:{[t]
    root:.qt.qproc.fillhdb.cfg_build[];
    stat:.qt.qproc.fillhdb.scratch[];
    .qunit.assertEquals[(.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.cfg_schema;stat]) 1;0;"first run"];
    after:.qt.qproc.fillhdb.snapshot[root];
    st1:.qt.qproc.fillhdb.snapshot[stat];
    r2:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.cfg_schema;stat];
    .qunit.assertEquals[r2 1;0;"a second run is clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];after;"and changes nothing in the HDB"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[stat];st1;"nor in the snapshot store: the same triples are the same snapshots"];
    / an interruption after config_id was written and before the old columns were dropped: put them back by hand
    d:.qt.qproc.fillhdb.dir[root;2026.01.01;`crypto_markout];
    (` sv d,`contract_version) set 3#1;
    (` sv d,`.d) set (get ` sv d,`.d),`contract_version;
    r3:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.cfg_schema;stat];
    .qunit.assertEquals[r3 1;0;"the next run is clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`crypto_markout];`time`fill_id`status`config_id;"and only drops the leftover column: config_id was already there and is not rewritten"]};

.qt.qproc.fillhdb.test_without_the_migration_switch_an_existing_markout_partition_is_left_alone:{[t]
    root:.qt.qproc.fillhdb.cfg_build[];
    before:.qt.qproc.fillhdb.snapshot[root];
    r:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.cfg_schema];
    .qunit.assertEquals[r 1;0;"the bootstrap fill is clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];before;"it deletes nothing and adds no config_id to a partition that already holds the table: that is the migration's, which is asked for"]};

.qt.qproc.fillhdb.test_a_migration_with_nowhere_to_record_its_snapshots_stops_before_it_drops_anything:{[t]
    root:.qt.qproc.fillhdb.cfg_build[];
    before:.qt.qproc.fillhdb.snapshot[root];
    sf:root,".database.q";
    (hsym `$sf) 0: enlist .qt.qproc.fillhdb.cfg_schema;
    out:root,".out.txt";
    cmd:"env -u UQF_STATUS_DIR -u TORQDATA ",.qt.support.q_interpreter[]," scripts/hdb/hdb_fill.q ",root," ",sf," migrate < /dev/null > ",out," 2>&1; echo $?";
    code:"J"$first system cmd;
    .qunit.assertEquals[code;1;"refused"];
    .qunit.assertTrue[any (read0 hsym `$out) like "*set UQF_STATUS_DIR*";"saying what to set"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root];before;"and the HDB is as it was"]};

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
    c:system "env ",.qt.support.q_interpreter[]," scripts/hdb/hdb_column_types.q ",root,
        ".partitions ",root,".database.q -q < /dev/null";
    .qunit.assertTrue[any c like "type_change|",root,"/2026.01.02|book|qty|j|f";
        "hdb_column_types.q reports it: stored as j, declared f"]};

.qt.qproc.fillhdb.test_the_destructive_dbmaint_operations_are_not_reachable:{[t]
    code:" " sv {$[("/"=first x) or 0=count x; ""; x]} each read0 `:scripts/hdb/hdb_fill.q;
    banned:`addtable`addcol`add1table`deletecol`delete1col`castcol`renamecol`rename1col`rentable`ren1table`copycol`copy1col`fncol`fn1col`fixtable`fix1table`reordercols`setattrcol`clearattrcol;
    hit:banned where {[code;b] 0<count ss[code;string b]}[code] each banned;
    .qunit.assertEquals[hit;`symbol$();"the script calls no dbmaint operation that rewrites, drops or blindly adds"];
    .qunit.assertTrue[0<count ss[code;"add1col"];"and does call the per-directory column add"]};

.qt.qproc.fillhdb.test_a_tree_without_the_vendored_dbmaint_is_refused_by_name:{[t]
    tree:.qt.qproc.fillhdb.scratch[];
    system "mkdir -p ",tree,"/scripts/hdb";
    system "cp scripts/hdb/hdb_fill.q ",tree,"/scripts/hdb/";
    root:.qt.qproc.fillhdb.build[];
    r:.qt.qproc.fillhdb.run_script[root;.qt.qproc.fillhdb.schema;tree,"/scripts/hdb/hdb_fill.q"];
    .qunit.assertTrue[any (r 0) like "*no dbmaint.q at*";"it says what is missing, rather than failing on an undefined name"];
    .qunit.assertEquals[r 1;1;"and exits 1: a repair that never ran must not read as one that did"]};

.qt.qproc.fillhdb.test_the_repair_loads_dbmaint_from_a_copy_of_the_tree_laid_out_like_a_release:{[t]
    tree:.qt.qproc.fillhdb.scratch[];
    system "mkdir -p ",tree,"/scripts/hdb ",tree,"/lib/kx-dbmaint";
    system "cp scripts/hdb/hdb_fill.q ",tree,"/scripts/hdb/";
    system "cp lib/kx-dbmaint/* ",tree,"/lib/kx-dbmaint/";
    root:.qt.qproc.fillhdb.build[];
    / from a directory that is not the tree: the script finds dbmaint by its own path, not the cwd
    r:.qt.qproc.fillhdb.run_script[root;"book:([] time:`timestamp$(); sym:`symbol$(); px:`float$())\nnewtab:([] time:`timestamp$())\n";tree,"/scripts/hdb/hdb_fill.q"];
    .qunit.assertEquals[r 1;0;"a release-shaped tree repairs"];
    .qunit.assertEquals[.qt.qproc.fillhdb.cols_of[root;2026.01.01;`newtab];enlist `time;"and writes the table dbmaint's allpaths named"]};

/ --- market_data: the flat top, the order and the row numbers (#2288) ----------------------------

/ market_data as the plant declares it now: the flat top and the row number after clock.
.qt.qproc.fillhdb.md_schema:
    "market_data:([] time:`timestamp$(); sym:`g#`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:(); clock:`symbol$(); bid:`float$(); ask:`float$(); rid:`long$())\n"

/ An HDB as it was before the flat top: market_data in arrival order with no bid, ask or rid. Three sources
/ across two syms, in time order, so within a sym the sources interleave - not the declared order. One book has
/ a withdrawn bid, one a zero-size ask level (no price), and one a level that is a price.
.qt.qproc.fillhdb.old_market_data:{[]
    root:.qt.qproc.fillhdb.scratch[];
    t0:2026.01.01D10:00:00.000000000;
    n:9;
    rows:([] time:t0+1000000000*til n; sym:`EURUSD`GBPUSD`EURUSD`EURUSD`GBPUSD`EURUSD`GBPUSD`EURUSD`GBPUSD;
        source:`b`a`a`b`b`a`a`b`a; market:n#`fx; source_time:t0+1000000000*(n-til n);
        bid_prices:(1.1 1.09;1.25 1.24;1.2;`float$();1.3;1.4 1.39;1.5;1.6;1.7);
        bid_sizes:(1e6 2e6;1e6 2e6;1e6;`float$();1e6;1e6 2e6;1e6;1e6;1e6);
        ask_prices:(1.2 1.21;1.26;1.3;1.4;1.31;1.5;1.6;1.7;1.8);
        ask_sizes:(1e6 2e6;1e6;1e6;1e6;0f;1e6;1e6;1e6;1e6);
        clock:n#`venue);
    .qt.qproc.fillhdb.put[root;2026.01.01;`market_data;rows];
    root}

/ The market_data columns of one partition, enumerations resolved.
.qt.qproc.fillhdb.md_cols:{[root;part]
    dir:.qt.qproc.fillhdb.dir[root;part;`market_data];
    `sym set get ` sv (hsym `$root),`sym;
    c:get ` sv dir,`.d;
    c!{[dir;c] v:get ` sv dir,c; $[(type v) within 20 76h; value v; v]}[dir] each c}

.qt.qproc.fillhdb.test_the_flat_top_the_order_and_the_row_numbers_come_only_when_asked_for:{[t]
    root:.qt.qproc.fillhdb.old_market_data[];
    before:.qt.qproc.fillhdb.snapshot[root,"/2026.01.01"];
    r:.qt.qproc.fillhdb.run[root;.qt.qproc.fillhdb.md_schema];
    .qunit.assertEquals[r 1;0;"the bootstrap fill runs clean"];
    .qunit.assertEquals[last .qt.qproc.fillhdb.cols_of[root;2026.01.01;`market_data];`clock;"and leaves the partition as it was: no bid, ask or rid, which a null would misstate"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root,"/2026.01.01"];before;"byte for byte"];
    m:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.md_schema;root,".status"];
    .qunit.assertEquals[m 1;0;"the migration runs clean"];
    c:.qt.qproc.fillhdb.md_cols[root;2026.01.01];
    .qunit.assertEquals[key[c] except `time`sym`source`market`source_time`bid_prices`bid_sizes`ask_prices`ask_sizes`clock;`bid`ask`rid;"the flat top and the row number are added, after clock"];
    .qunit.assertEquals[last key c;`rid;"and the row number is last"];
    n:count c`sym;
    .qunit.assertEquals[n;9;"no row lost"];
    o:`sym`source`source_time xasc flip `sym`source`source_time!c`sym`source`source_time;
    .qunit.assertEquals[flip `sym`source`source_time!c`sym`source`source_time;o;"sorted by sym, source, source_time"];
    .qunit.assertEquals[c`rid;til n;"each row numbered by its position in that order"];
    dir:.qt.qproc.fillhdb.dir[root;2026.01.01;`market_data];
    .qunit.assertEquals[attr get ` sv dir,`sym;`p;"with p# on sym"];
    / the flat top is the plant's rule: the first level, when it is a price someone could deal at
    ok:{[px;sz] $[(count px)&count sz; (0<first px)&(0w>first px)&(0<first sz)&0w>first sz; 0b]};
    bids:{$[x[2]; x 0; 0n]}each flip (first each c`bid_prices;first each c`bid_sizes;ok'[c`bid_prices;c`bid_sizes]);
    asks:{$[x[2]; x 0; 0n]}each flip (first each c`ask_prices;first each c`ask_sizes;ok'[c`ask_prices;c`ask_sizes]);
    .qunit.assertEquals[c`bid;"f"$bids;"bid is the first bid level where it is a price, null where the side is withdrawn"];
    .qunit.assertEquals[c`ask;"f"$asks;"ask likewise, and a zero-size level is no price"];
    .qunit.assertTrue[any null c`bid;"the fixture holds a withdrawn bid"];
    .qunit.assertTrue[any null c`ask;"and a zero-size ask level"];
    again:.qt.qproc.fillhdb.snapshot[root,"/2026.01.01"];
    m2:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.md_schema;root,".status"];
    .qunit.assertEquals[m2 1;0;"a second migration runs clean"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[root,"/2026.01.01"];again;"and changes not one byte: the migration is idempotent"]};

.qt.qproc.fillhdb.test_a_partition_in_order_with_the_columns_is_not_rewritten:{[t]
    root:.qt.qproc.fillhdb.old_market_data[];
    .qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.md_schema;root,".status"];
    dir:.qt.qproc.fillhdb.dir[root;2026.01.01;`market_data];
    before:.qt.qproc.fillhdb.snapshot[1_string dir];
    / staging is empty between runs: a rewrite leaves its dated directories, so none appearing is a pass
    r:.qt.qproc.fillhdb.run_migrating[root;.qt.qproc.fillhdb.md_schema;root,".status"];
    .qunit.assertEquals[(r 1;any (r 0) like "*rewritten in*");(0;0b);"the output says nothing was rewritten"];
    .qunit.assertEquals[.qt.qproc.fillhdb.snapshot[1_string dir];before;"and no file changed"]};
