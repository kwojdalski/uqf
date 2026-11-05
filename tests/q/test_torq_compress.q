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
    if[not `o in key `.lg; `.lg.o set {[a;b]}; `.lg.e set {[a;b]}; `.qt.qtorq.compress.added set .qt.qtorq.compress.added,`lg];
    if[not `getconfigfile in key `.proc; `.proc.getconfigfile set {[n] enlist `:build/none.csv}; `.qt.qtorq.compress.added set .qt.qtorq.compress.added,`proc];
    system"l lib/torq/code/common/compress.q"; }

.qt.fixture.torq_cmp.clear:{[ns]
    {[ns;n] ![ns;();0b;enlist n]}[ns] each (key ns) except `;}

.qt.fixture.torq_cmp.unload:{[]
    .qt.fixture.torq_cmp.clear[`.cmp];
    if[`lg in .qt.qtorq.compress.added; .qt.fixture.torq_cmp.clear[`.lg]];
    if[`proc in .qt.qtorq.compress.added; .qt.fixture.torq_cmp.clear[`.proc]];
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

/ Stand-in for .servers: the hdbs it finds are function handles that record the call.
.qt.qtorq.compress.servers:{[handles]
    had:`getservers in key `.servers;
    `.qt.qtorq.compress.had_servers set had;
    `.qt.qtorq.compress.old_servers set $[had; .servers.getservers; ::];
    `.servers.getservers set {[hs;a;b;c;d;e] ([] w:hs)}[handles]; }

.qt.qtorq.compress.unservers:{[]
    if[.qt.qtorq.compress.had_servers; `.servers.getservers set .qt.qtorq.compress.old_servers];
    if[not .qt.qtorq.compress.had_servers; .qt.fixture.torq_cmp.clear[`.servers]]; }

.qt.qtorq.compress.calls:()

.qt.qtorq.compress.test_run_reloads_the_hdbs_only_when_a_file_changed:{[t]
    root:.qt.qtorq.compress.hdb[];
    conf:.qt.qtorq.compress.conf[root;.qt.qtorq.compress.default_rows[]];
    `.qt.qtorq.compress.calls set ();
    h:{`.qt.qtorq.compress.calls set .qt.qtorq.compress.calls,enlist x; (1b;`)};
    .qt.qtorq.compress.servers[enlist h];
    .qtorq.compress.run[root;conf;365];
    .qunit.assertEquals[count .qt.qtorq.compress.calls;1];
    .qunit.assertEquals[last first .qt.qtorq.compress.calls;1_string root];
    .qtorq.compress.run[root;conf;365];
    .qunit.assertEquals[count .qt.qtorq.compress.calls;1];
    .qt.qtorq.compress.unservers[];
    ::}

.qt.qtorq.compress.test_a_failing_reload_is_named_not_swallowed:{[t]
    `.qt.qtorq.compress.calls set ();
    good:{(1b;`)};
    bad:{'"hdb is busy"};
    .qt.qtorq.compress.servers[(good;bad)];
    msg:@[{.qtorq.compress.reload `:/hdb; ""};::;{x}];
    .qt.qtorq.compress.unservers[];
    .qunit.assertEquals[msg;"qtorq.compress: 1 hdb(s) failed to reload: hdb is busy"]}
