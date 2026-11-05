/ test_torq_compress.q - the thin wrapper over TorQ's own HDB compression (.qt.qtorq.compress, #2146).
/ .
/ The REAL .cmp runs: lib/torq/code/common/compress.q is loaded for each test, with
/ stand-ins for the two things it asks of a TorQ process (.lg and .proc.getconfigfile),
/ and removed again afterwards. What is under test is uqf's part - the plan's
/ shape, the check after the run (TorQ only logs a failed read-back), the reload - and
/ that TorQ's behaviour is what the wrapper builds on: age-based config, exclusions,
/ idempotence, decompression, hash files.
/ .
/ KDB-X only: the pinned PeachQ has no -19! (#2153), so every case that compresses is in
/ tests/q/peachq_known_gaps.txt. Block size 17 throughout (2^14 is the smallest macOS
/ arm64 accepts). Algorithms 1 and 2 only: 3 needs snappy on the loader's path.

.qt.qtorq.compress.n:100000

/ What the test added to the process, so that tearDown removes exactly that.
.qt.qtorq.compress.added:`symbol$()

/ Load TorQ's compress.q against stand-ins for .lg and .proc.getconfigfile.
.qt.fixture.torq_cmp.load:{[]
    if[not `o in key `.lg; `.lg.o set {[a;b]}; `.qt.qtorq.compress.added set .qt.qtorq.compress.added,`lg_o];
    if[not `e in key `.lg; `.lg.e set {[a;b]}; `.qt.qtorq.compress.added set .qt.qtorq.compress.added,`lg_e];
    if[not `getconfigfile in key `.proc; `.proc.getconfigfile set {[n] enlist `:build/none.csv}; `.qt.qtorq.compress.added set .qt.qtorq.compress.added,`proc_getconfigfile];
    system"l lib/torq/code/common/compress.q"; }

.qt.fixture.torq_cmp.clear:{[ns]
    {[ns;n] ![ns;();0b;enlist n]}[ns] each (key ns) except `;}

/ Remove one member of a namespace, leaving the rest.
.qt.fixture.torq_cmp.drop:{[ns;n] ![ns;();0b;enlist n]; }

.qt.fixture.torq_cmp.unload:{[]
    .qt.fixture.torq_cmp.clear[`.cmp];
    if[`lg_o in .qt.qtorq.compress.added; .qt.fixture.torq_cmp.drop[`.lg;`o]];
    if[`lg_e in .qt.qtorq.compress.added; .qt.fixture.torq_cmp.drop[`.lg;`e]];
    if[`proc_getconfigfile in .qt.qtorq.compress.added; .qt.fixture.torq_cmp.drop[`.proc;`getconfigfile]];
    `.qt.qtorq.compress.added set `symbol$(); }

.qt.qtorq.compress.setUp_fresh:{[]
    .qt.fixture.torq_cmp.load[]; }

.qt.qtorq.compress.tearDown_fresh:{[]
    .qt.fixture.torq_cmp.unload[]; }

/ boolean, symbol, long, sparse, float and a nested symbol column.
.qt.qtorq.compress.table:{[]
    n:.qt.qtorq.compress.n;
    ([] flag:n#0b; sym:n?`aa`bb`cc; qty:til n; sparse:@[n#0Nj;0 1000;:;1 2j]; px:n#1.5; tags:n#enlist `x`y)}

/ A fresh HDB: trade on 30, 20 and 2 days ago; a sym file in the root.
.qt.qtorq.compress.days:{[] .z.D-30 20 2}

.qt.qtorq.compress.hdb:{[]
    root:`$":",first system"mktemp -d";
    {[root;d] (` sv root,`$string[d],"/trade/") set .Q.en[root;.qt.qtorq.compress.table[]]}[root] each .qt.qtorq.compress.days[];
    root}

/ A config file: every table gets `default` unless a row names it. minage 10.
.qt.qtorq.compress.conf:{[root;rows]
    f:` sv root,`cfg.csv;
    f 0: enlist["table,minage,column,calgo,cblocksize,clevel"],rows;
    f}

.qt.qtorq.compress.default_rows:{[] ("default,10,default,2,17,6";"trade,10,qty,1,17,0")}

.qt.qtorq.compress.part:{[root;d] ` sv root,`$string[d],"/trade"}

/ Every regular file under a directory with its bytes.
.qt.qtorq.compress.bytes:{[dir]
    fs:system"find ",(1_string dir)," -type f | sort";
    fs!read1 each hsym each `$fs}

.qt.qtorq.compress.values:{[part] names:get ` sv part,`.d; names!{[part;c] get ` sv part,c}[part] each names}

/ --- plan ----------------------------------------------------------------

.qt.qtorq.compress.test_plan_matches_the_config_and_writes_nothing:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    b0:.qt.qtorq.compress.bytes root;
    p:.qtorq.compress.plan[root;conf;365];
    / the two old partitions, six columns each; the 2-day-old one is younger than minage 10
    .qunit.assertEquals[count p;12];
    .qunit.assertEquals[asc distinct p`partition;asc .z.D-30 20];
    .qunit.assertEquals[distinct p`action;enlist `compress];
    .qunit.assertEquals[distinct p`table;enlist `trade];
    / the per-column row wins for qty, the default for the rest
    .qunit.assertEquals[exec distinct calgo from p where column=`qty;enlist 1j];
    .qunit.assertEquals[exec distinct calgo from p where not column=`qty;enlist 2j];
    / nothing that is not a column: no .d, no hash files, no sym
    .qunit.assertEquals[asc distinct p`column;asc `flag`sym`qty`sparse`px`tags];
    .qunit.assertTrue[not any (string p`fullpath) like "*#";"no hash files"];
    .qunit.assertEquals[.qt.qtorq.compress.bytes root;b0];
    ::}

.qt.qtorq.compress.test_plan_refuses_where_torqs_compression_is_not_loaded:{[t]
    / the runner's setUp has loaded .cmp for this test; take it away again
    .qt.fixture.torq_cmp.clear[`.cmp];
    m:@[{.qtorq.compress.plan[`:/nowhere;`:/nowhere.csv;365]; ""};::;{x}];
    .qunit.assertTrue[m like "qtorq.compress: TorQ's .cmp is not loaded*";"named: ",m]}

.qt.qtorq.compress.test_torqs_own_config_limits_stand:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;enlist "default,10,default,4,17,6"];
    m:.[{.qtorq.compress.plan[x;y;365]; ""};(root;conf);{x}];
    .qunit.assertTrue[0<count ss[m;"incorrect compression algo"];"algo 4 is outside .cmp's 0 to 3: ",m];
    ::}

/ --- apply ----------------------------------------------------------------

.qt.qtorq.compress.test_apply_compresses_verifies_and_keeps_every_value:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    v0:.qt.qtorq.compress.values each .qt.qtorq.compress.part[root;] each .qt.qtorq.compress.days[];
    r:.qtorq.compress.apply[root;conf;365];
    .qunit.assertEquals[count r;12];
    .qunit.assertTrue[all (r`bytes_after)<r`bytes_before;"every file got smaller"];
    / the algorithm is the one the config named, per column
    st:{[f] -21!f} each exec fullpath from r where column=`qty;
    .qunit.assertEquals[st@\:`algorithm;2#1i];
    st:{[f] -21!f} each exec fullpath from r where column=`flag;
    .qunit.assertEquals[st@\:`algorithm;2#2i];
    .qunit.assertEquals[st@\:`logicalBlockSize;2#17i];
    / sensible ratios
    ratio:{[f] z:-21!f; z[`uncompressedLength]%z`compressedLength} each exec fullpath from r where column in `flag`sparse`px;
    .qunit.assertTrue[all ratio>50f;"constant and sparse columns shrink by orders of magnitude"];
    / the 2-day-old partition is untouched and every value everywhere is as it was
    young:` sv/:.qt.qtorq.compress.part[root;.z.D-2],/:`flag`qty;
    .qunit.assertEquals[.qtorq.compress.is_compressed each young;00b];
    .qunit.assertEquals[.qt.qtorq.compress.values each .qt.qtorq.compress.part[root;] each .qt.qtorq.compress.days[];v0];
    ::}

.qt.qtorq.compress.test_apply_keeps_a_nested_columns_hash_files:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    p:.qt.qtorq.compress.part[root;.z.D-30];
    h0:read1 each `$string[` sv p,`tags],/:("#";"##");
    .qtorq.compress.apply[root;conf;365];
    .qunit.assertTrue[.qtorq.compress.is_compressed ` sv p,`tags;"the nested column itself is compressed"];
    .qunit.assertEquals[read1 each `$string[` sv p,`tags],/:("#";"##");h0];
    ::}

.qt.qtorq.compress.test_applying_twice_changes_nothing:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    .qtorq.compress.apply[root;conf;365];
    b1:.qt.qtorq.compress.bytes root;
    r:.qtorq.compress.apply[root;conf;365];
    .qunit.assertEquals[count r;0];
    .qunit.assertEquals[.qt.qtorq.compress.bytes root;b1];
    ::}

.qt.qtorq.compress.test_algo_zero_decompresses_to_the_original_bytes:{[t]
    root:.qt.qtorq.compress.hdb[];
    b0:.qt.qtorq.compress.bytes root;
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    .qtorq.compress.apply[root;conf;365];
    off:.qt.qtorq.compress.conf[root;enlist "default,10,default,0,17,0"];
    p:.qtorq.compress.plan[root;off;365];
    .qunit.assertEquals[distinct p`action;enlist `decompress];
    r:.qtorq.compress.apply[root;off;365];
    .qunit.assertEquals[count r;12];
    .qunit.assertEquals[.qt.qtorq.compress.bytes root;b0];
    .qunit.assertEquals[count .qtorq.compress.apply[root;off;365];0];
    ::}

/ A file already compressed with another algorithm is skipped by TorQ, not recompressed.
.qt.qtorq.compress.test_a_file_compressed_with_another_algorithm_is_skipped:{[t]
    root:.qt.qtorq.compress.hdb[];
    .qtorq.compress.apply[root;.qt.qtorq.compress.conf[root;enlist "default,10,default,1,17,0"];365];
    p:.qtorq.compress.plan[root;.qt.qtorq.compress.conf[root;enlist "default,10,default,2,17,6"];365];
    .qunit.assertEquals[distinct p`action;enlist `skip];
    ::}

/ --- an injected mismatch becomes a named error, and the original is kept ---

/ .cmp.compress, but the temp file of one column is replaced by a well-formed compressed
/ file of other values, so TorQ's own get ~ get check fails and it only LOGS it.
.qt.qtorq.compress.sabotage:{[col;file;algo;blocksize;level;size]
    .qt.qtorq.compress.real_compress[file;algo;blocksize;level;size];
    if[col=last ` vs file;
        tmp:hsym `$string[file],"_kdbtempzip";
        `:build/other set 1+til .qt.qtorq.compress.n;
        -19!(`:build/other;tmp;blocksize;algo;level)]; }

.qt.qtorq.compress.test_a_failed_read_back_is_a_named_error_and_the_original_is_kept:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    old:.qt.qtorq.compress.part[root;.z.D-30];
    qty:` sv old,`qty;
    orig:read1 qty;
    `.qt.qtorq.compress.real_compress set .cmp.compress;
    `.cmp.compress set .qt.qtorq.compress.sabotage[`qty];
    msg:.[{.qtorq.compress.apply[x;y;365]; ""};(root;conf);{x}];
    `.cmp.compress set .qt.qtorq.compress.real_compress;
    .qunit.assertTrue[msg like "qtorq.compress: 2 file(s) were not brought to their target state*";"names the failure: ",msg];
    .qunit.assertTrue[0<count ss[msg;string qty];"lists the file: ",msg];
    .qunit.assertEquals[read1 qty;orig];
    .qunit.assertEquals[count -21! qty;0];
    .qunit.assertEquals[key[old] where key[old] like "*_kdbtempzip";`symbol$()];
    / the other columns were converted before the refusal and are still right
    .qunit.assertTrue[.qtorq.compress.is_compressed ` sv old,`flag;"TorQ swaps file by file"];
    ::}

/ --- reload ----------------------------------------------------------------

/ run's reload is .qtorq.reload_hdb; swap it for a recorder and put it back.
.qt.qtorq.compress.reloads:0

.qt.qtorq.compress.test_run_reloads_the_hdbs_only_when_a_file_changed:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    `.qt.qtorq.compress.reloads set 0;
    old:.qtorq.reload_hdb;
    `.qtorq.reload_hdb set {`.qt.qtorq.compress.reloads set 1+.qt.qtorq.compress.reloads; `registered`opened`reloaded!0 0 0};
    r1:.qtorq.compress.run[root;conf;365];
    n1:.qt.qtorq.compress.reloads;
    r2:.qtorq.compress.run[root;conf;365];
    n2:.qt.qtorq.compress.reloads;
    `.qtorq.reload_hdb set old;
    .qunit.assertEquals[count r1;12];
    .qunit.assertEquals[n1;1];
    .qunit.assertEquals[count r2;0];
    .qunit.assertEquals[n2;1]}

/ --- report (#2149) ---------------------------------------------------------

/ sort.csv's default rows as TorQ ships them (lib/torq/config/sort.csv), in the shape
/ .sort.getsortcsv loads them into .sort.params.
.qt.qtorq.compress.sort_rows:{[] ([] tabname:`default`default; att:`p`; column:`sym`time; sort:11b)}

/ One day of trade: sorted by sym then time with p#sym, as TorQ's wdb leaves it, or not.
/ qty counts rows in the sorted order, so a shuffled day is out of order on qty as well.
.qt.qtorq.compress.report_day:{[in_order;with_attr]
    n:.qt.qtorq.compress.n;
    t:`sym`time xasc ([] sym:n?`aa`bb`cc; time:n?0D23:59; px:n#1.5);
    t:update qty:til n from t;
    if[not in_order; t:t neg[n]?n];
    $[with_attr; update `p#sym from t; t]}

/ A fresh HDB of four days: two old ones as the wdb writes them, with px compressed at
/ gzip level 6 and qty at gzip level 1 (set with a compression triple, the one way PeachQ
/ writes a compressed file, #2153); a day 10 days old whose rows are out of order and
/ carry no attribute; and one 5 days old in order but without p#.
.qt.qtorq.compress.report_hdb:{[]
    root:`$":",first system"mktemp -d";
    days:.z.D-30 20 10 5;
    {[root;d;s;a] (` sv root,`$string[d],"/trade/") set .Q.en[root;.qt.qtorq.compress.report_day[s;a]]}[root]'[days;1100b;1100b];
    {[root;d;c;lvl] f:` sv root,(`$string d),`trade,c; v:get f; hdel f; (f;17;2;lvl) set v}[root]'[
        days 0 0 1 1;`px`qty`px`qty;6 1 6 1];
    root}

.qt.qtorq.compress.test_report_reads_each_columns_algorithm_block_size_and_ratio:{[t]
    root:.qt.qtorq.compress.report_hdb[];
    r:.qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];10];
    c:`column xkey r`columns;
    .qunit.assertEquals[asc exec column from r`columns;`px`qty`sym`time];
    .qunit.assertEquals[c[`px;`files];4];
    .qunit.assertEquals[c[`px;`compressed];2];
    .qunit.assertEquals[asc c[`px;`algos];0 2i];
    .qunit.assertEquals[asc c[`qty;`algos];0 2i];
    .qunit.assertEquals[c[`px;`blocksizes];enlist 17i];
    .qunit.assertTrue[c[`px;`compressed_ratio]>50f;"a constant column shrinks by orders of magnitude"];
    / ratio is over every file, the uncompressed ones at 1x, so it sits below the compressed one
    .qunit.assertTrue[c[`px;`ratio]<c[`px;`compressed_ratio];"the whole column's ratio counts its plain files"];
    .qunit.assertEquals[c[`sym;`compressed];0];
    .qunit.assertTrue[null c[`sym;`compressed_ratio];"no compressed file, no compressed ratio"];
    .qunit.assertEquals[c[`sym;`blocksizes];`int$()];
    / the walk is .cmp's: no .d, no root sym file among the columns
    .qunit.assertEquals[count .qtorq.compress.files root;16];
    ::}

.qt.qtorq.compress.test_report_lists_the_largest_uncompressed_partitions_first:{[t]
    root:.qt.qtorq.compress.report_hdb[];
    big:(.qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];2])`largest_uncompressed;
    .qunit.assertEquals[count big;2];
    / the two young days hold four plain files each; the old ones two
    .qunit.assertEquals[asc big`partition;.z.D-10 5];
    .qunit.assertEquals[big`files;4 4];
    .qunit.assertTrue[(>=) . big`bytes;"largest first"];
    all3:(.qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];10])`largest_uncompressed;
    .qunit.assertEquals[count all3;4];
    .qunit.assertEquals[all3`bytes;desc all3`bytes];
    ::}

.qt.qtorq.compress.test_report_names_the_unsorted_partition_and_the_one_missing_its_attribute:{[t]
    root:.qt.qtorq.compress.report_hdb[];
    u:`partition xasc (.qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];10])`unsorted;
    .qunit.assertEquals[u`partition;.z.D-10 5];
    .qunit.assertEquals[u`sorted;01b];
    .qunit.assertEquals[u`missing_attr;(enlist `sym;enlist `sym)];
    .qunit.assertEquals[first u`sort_by;`sym`time];
    .qunit.assertEquals[first u`declared_attr;(enlist `sym)!enlist `p];
    .qunit.assertEquals[u`rows;2#.qt.qtorq.compress.n];
    ::}

.qt.qtorq.compress.test_report_judges_a_table_by_its_own_sort_rows_before_the_default:{[t]
    root:.qt.qtorq.compress.report_hdb[];
    own:.qt.qtorq.compress.sort_rows[],([] tabname:enlist `trade; att:enlist `; column:enlist `qty; sort:enlist 1b);
    u:(.qtorq.compress.report[root;own;10])`unsorted;
    / trade's own row sorts by qty and declares no attribute: only the shuffled day fails
    .qunit.assertEquals[u`partition;enlist .z.D-10];
    .qunit.assertEquals[u`sort_by;enlist enlist `qty];
    .qunit.assertEquals[u`missing_attr;enlist `symbol$()];
    ::}

.qt.qtorq.compress.test_report_writes_nothing_and_never_sorts:{[t]
    root:.qt.qtorq.compress.report_hdb[];
    b0:.qt.qtorq.compress.bytes root;
    .qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];10];
    .qunit.assertEquals[.qt.qtorq.compress.bytes root;b0];
    ::}

.qt.qtorq.compress.test_report_on_an_hdb_with_no_tables_is_empty:{[t]
    root:`$":",first system"mktemp -d";
    r:.qtorq.compress.report[root;.qt.qtorq.compress.sort_rows[];10];
    .qunit.assertEquals[count each r;`columns`largest_uncompressed`unsorted!0 0 0];
    ::}
