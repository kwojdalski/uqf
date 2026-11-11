// test_bench_compression.q - the compression benchmark (.qt.qdev.cbench, #2297):
// scripts/bench/compression_lib.q, and scripts/bench/compression.q run end to end.
//
// The pure parts run against in-memory tables and fixed text. The copies and the command run
// against a small synthetic HDB in a scratch directory, never the stack's; they need -19!,
// which the pinned PeachQ lacks, so they are in tests/q/peachq_known_gaps.txt. Block size 17
// and algorithms 1 and 2 only: 2^14 is the smallest block size macOS arm64 takes, and 3-5
// need a shared library on the loader's path.

system "l scripts/bench/compression_lib.q";

/ A quote-shaped table: p#sym, time sorted within sym, floats, a nested char column.
.qt.qdev.cbench.table:{[n]
    s:asc n?`aa`bb`cc;
    tm:2026.01.02D00:00:00.000000000+asc n?0D10:00:00.000000000;
    ([] sym:`p#s; time:tm; px:n?1f; qty:n?100; tags:n?("ab";"cde";"f"))}

/ Run f with the global `sym` set to a domain, and put `sym` back as it was whatever happens:
/ .Q.en and a `get` of an enumerated column both use the global.
.qt.qdev.cbench.with_sym:{[dom;f]
    had:`sym in key `.;
    keep:$[had; get `sym; ()];
    `sym set dom;
    r:@[{(1b;x[::])};f;{(0b;x)}];
    $[had; `sym set keep; ![`.;();0b;enlist `sym]];
    $[r 0; r 1; 'r 1]}

/ A scratch HDB with one partition of `quote` and a sym file, as a file symbol.
.qt.qdev.cbench.hdb:{[]
    root:hsym `$first system "mktemp -d";
    .qt.qdev.cbench.with_sym[`symbol$();{[root;u] (` sv root,`2026.01.02`quote`) set .Q.en[root;.qt.qdev.cbench.table 20000]}[root]];
    root}

/ Every regular file under a directory, with its bytes.
.qt.qdev.cbench.bytes:{[dir]
    fs:system "find ",(1_string dir)," -type f | sort";
    fs!read1 each hsym each `$fs}

.qt.qdev.cbench.opts:`points`probes`range`seed!(10;20;0Nj;42)

/ --- candidates ----------------------------------------------------------

.qt.qdev.cbench.test_a_triple_is_parsed_by_name_or_number:{[t]
    a:.qdev.cbench.parse_candidate "gzip/17/6";
    b:.qdev.cbench.parse_candidate "2/17/6";
    .qunit.assertEquals[a`triple;2 17 6j;"gzip is algorithm 2"];
    .qunit.assertEquals[a`name;`$"gzip/17/6";"named by algorithm, block size and level"];
    .qunit.assertEquals[b;a;"2/17/6 is the same candidate"];
    .qunit.assertEquals[(.qdev.cbench.parse_candidate "zstd/17/-3")`triple;5 17 -3j;"zstd takes negative levels"]}

.qt.qdev.cbench.test_none_current_and_policy_are_named_candidates:{[t]
    .qunit.assertEquals[(.qdev.cbench.parse_candidate "none")`triple;0 0 0j;"none is uncompressed"];
    .qunit.assertEquals[(.qdev.cbench.parse_candidate "0/17/6")`triple;0 0 0j;"algorithm 0 has no block size or level"];
    .qunit.assertEquals[(.qdev.cbench.parse_candidate "current")`kind;`current;"current copies the files"];
    .qunit.assertEquals[(.qdev.cbench.parse_candidate " policy ")`kind;`policy;"policy is resolved by the command"]}

.qt.qdev.cbench.test_a_candidate_out_of_range_is_refused_by_name:{[t]
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/21/6";"bench: candidate gzip/21/6: block size 21 is not 12-20*";"block size"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/17/10";"bench: candidate gzip/17/10: level 10 does not fit gzip (0-9)";"level"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"qipc/17/3";"*level 3 does not fit qipc (0-0)";"qipc takes no level"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"brotli/17/6";"*algorithm brotli is not one of 0-5*";"algorithm"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/17";"*is not current, policy, none or algo/blocksize/level";"shape"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/x/6";"*block size and level must be integers";"integers"]}

.qt.qdev.cbench.test_a_setting_takes_column_overrides:{[t]
    c:.qdev.cbench.parse_candidate "gzip/16/6+time=none+sym=2/20/6";
    .qunit.assertEquals[c`name;`$"gzip/16/6+time=none+sym=gzip/20/6";"named by its settings, algorithms by name"];
    .qunit.assertEquals[c`triple;2 16 6j;"the table's triple"];
    .qunit.assertEquals[c`columns;`time`sym!(0 0 0j;2 20 6j);"each column's, in the order given"];
    .qunit.assertEquals[.qdev.cbench.column_triple[c;`time];0 0 0j;"time uncompressed"];
    .qunit.assertEquals[.qdev.cbench.column_triple[c;`px];2 16 6j;"the rest take the table's"];
    .qunit.assertEquals[(.qdev.cbench.parse_candidate "none+sym=qipc/17/0")`columns;(enlist `sym)!enlist 1 17 0j;"over an uncompressed table"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/16/6+time";"bench: candidate gzip/16/6+time: a column override is +column=setting";"no setting"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/16/6+=none";"*: a column override names no column";"no column"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/16/6+a=none+a=gzip/17/6";"*: a column is overridden twice";"twice"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"gzip/16/6+a=gzip/21/6";"*: block size 21 is not 12-20*";"the override is checked"];
    .qunit.assertThrows[.qdev.cbench.parse_candidate;"policy+a=none";"*is not current, policy, none or algo/blocksize/level";"policy takes no overrides"]}

.qt.qdev.cbench.test_a_list_keeps_its_order_and_refuses_a_repeat:{[t]
    c:.qdev.cbench.parse_candidates "current, none,gzip/17/6,";
    .qunit.assertEquals[{x`name} each c;`current`none,`$"gzip/17/6";"in the order given, empty items dropped"];
    .qunit.assertThrows[.qdev.cbench.parse_candidates;"none,0/0/0";"bench: candidate none is named twice";"the same setting twice"];
    .qunit.assertThrows[.qdev.cbench.parse_candidates;" , ";"bench: no candidates";"nothing named"]}

.qt.qdev.cbench.test_the_default_candidates_parse:{[t]
    c:.qdev.cbench.parse_candidates .qdev.cbench.default_candidates;
    .qunit.assertEquals[first {x`name} each c;`current;"current first, the baseline"];
    .qunit.assertTrue[`policy in {x`name} each c;"the declared policy is compared by default"]}

.qt.qdev.cbench.test_the_policy_keeps_the_overrides_the_partition_has:{[t]
    r:`algo`blocksize`level`minage`columns`mode!(2;16;6;7;`invert`gone!(2 16 9;1 17 0);`age);
    c:.qdev.cbench.from_policy[r;`sym`invert];
    .qunit.assertEquals[c`triple;2 16 6j;"the table's triple"];
    .qunit.assertEquals[c`columns;(enlist `invert)!enlist 2 16 9j;"a column the partition lacks is dropped"];
    .qunit.assertEquals[.qdev.cbench.column_triple[c;`invert];2 16 9j;"the override"];
    .qunit.assertEquals[.qdev.cbench.column_triple[c;`sym];2 16 6j;"the table's, for the rest"];
    u:.qdev.cbench.from_policy[`algo`blocksize`level`columns!(0;0;0;(`symbol$())!());`sym];
    .qunit.assertEquals[u`triple;0 0 0j;"a table the policy leaves uncompressed"]}

.qt.qdev.cbench.test_labels:{[t]
    .qunit.assertEquals[.qdev.cbench.label 0 0 0;`none;"uncompressed"];
    .qunit.assertEquals[.qdev.cbench.label 3 17 0;`$"snappy/17/0";"algorithm by name"]}

/ --- paths ---------------------------------------------------------------

.qt.qdev.cbench.test_inside_compares_whole_path_segments:{[t]
    .qunit.assertTrue[.qdev.cbench.inside["/data/hdb";"/data/hdb/"];"the root itself"];
    .qunit.assertTrue[.qdev.cbench.inside["/data/hdb/tmp/x";"/data/hdb"];"under it"];
    .qunit.assertFalse[.qdev.cbench.inside["/data/hdb2/x";"/data/hdb"];"a sibling sharing a prefix is not inside"];
    .qunit.assertFalse[.qdev.cbench.inside["/tmp/x";"/data/hdb"];"elsewhere"]}

.qt.qdev.cbench.test_absolute_resolves_dots_as_text:{[t]
    .qunit.assertEquals[.qdev.cbench.absolute["/data/run";"../hdb/./x/"];"/data/hdb/x";"relative"];
    .qunit.assertEquals[.qdev.cbench.absolute["/data/run";"/tmp/a/../b"];"/tmp/b";"absolute"];
    .qunit.assertEquals[.qdev.cbench.absolute["/data";"../.."];enlist "/";"no higher than the root"]}

.qt.qdev.cbench.test_paths_and_shell_quoting:{[t]
    .qunit.assertEquals[.qdev.cbench.path `:/a/b/;"/a/b";"no colon, no trailing slash"];
    .qunit.assertEquals[.qdev.cbench.path "/";enlist "/";"the root stays the root"];
    .qunit.assertEquals[.qdev.cbench.sh "it's";"'it'\\''s'";"a single quote survives /bin/sh"];
    .qunit.assertEquals[first system "printf %s ",.qdev.cbench.sh "a b'c";"a b'c";"what the shell hands on"]}

.qt.qdev.cbench.test_a_missing_partition_is_named:{[t]
    root:.qt.qdev.cbench.hdb[];
    .qunit.assertEquals[.qdev.cbench.find_partition[root;"2026.01.02";`quote];` sv root,`2026.01.02`quote;"the splayed directory"];
    .qunit.assertThrows[.qdev.cbench.find_partition[root;"2026.01.03"];`quote;"bench: no table quote in partition 2026.01.03 under *";"no such partition"];
    .qunit.assertThrows[.qdev.cbench.find_partition[`:/nonexistent/hdb;"2026.01.02"];`quote;"bench: no HDB at /nonexistent/hdb";"no HDB"];
    .qunit.assertEquals[.qdev.cbench.domains root;enlist ` sv root,`sym;"the sym file is the domain"];
    system "rm -rf ",1_string root}

.qt.qdev.cbench.test_a_segmented_hdb_is_searched_segment_by_segment:{[t]
    root:hsym `$first system "mktemp -d";
    seg:hsym `$first system "mktemp -d";
    (` sv root,`par.txt) 0: enlist 1_string seg;
    (` sv seg,`2026.01.02`trade`) set ([] px:1 2f);
    .qunit.assertEquals[.qdev.cbench.find_partition[root;"2026.01.02";`trade];` sv seg,`2026.01.02`trade;"found in the segment"];
    system "rm -rf ",(1_string root)," ",1_string seg}

.qt.qdev.cbench.test_on_path_reads_the_directories:{[t]
    d:first system "mktemp -d";
    (hsym `$d,"/strace") 0: enlist "";
    .qunit.assertEquals[.qdev.cbench.resolve["/nonexistent:",d;"strace"];d,"/strace";"a bare name, found"];
    .qunit.assertEquals[.qdev.cbench.resolve["/nonexistent";"q"];enlist "q";"not found: as given"];
    .qunit.assertTrue[.qdev.cbench.on_path["/nonexistent:",d;`strace];"in the second directory"];
    .qunit.assertFalse[.qdev.cbench.on_path["/nonexistent:",d;`dtruss];"not there"];
    .qunit.assertFalse[.qdev.cbench.on_path["";`strace];"an empty PATH"];
    system "rm -rf ",d}

/ --- parameters and shapes -------------------------------------------------

.qt.qdev.cbench.test_aj_joins_on_sym_and_time_or_the_first_of_each:{[t]
    .qunit.assertEquals[.qdev.cbench.aj_cols meta ([] a:`x`y; sym:`x`y; ts:2#.z.p; time:2#.z.p);`sym`time;"sym and time by name"];
    .qunit.assertEquals[.qdev.cbench.aj_cols meta ([] ccy:`x`y; ts:2#.z.p);`ccy`ts;"the first of each type"];
    .qunit.assertEquals[.qdev.cbench.aj_cols meta ([] ccy:`x`y; d:2#.z.d);`symbol$();"a date is not a time to join on"]}

.qt.qdev.cbench.test_params_are_drawn_once_and_restore_the_seed:{[t]
    tb:.qt.qdev.cbench.table 1000;
    s0:system "S";
    a:.qdev.cbench.params[tb;.qt.qdev.cbench.opts];
    .qunit.assertEquals[system "S";s0;"q's seed is put back"];
    b:.qdev.cbench.params[tb;.qt.qdev.cbench.opts];
    .qunit.assertEquals[a;b;"the same draws for the same seed"];
    .qunit.assertEquals[a`n`lo`k;1000 495 10j;"1% of the day, from the middle"];
    .qunit.assertEquals[count distinct a`points;10;"distinct point rows"];
    .qunit.assertTrue[all (a`points) within 0 999;"inside the day"];
    .qunit.assertEquals[cols a`probes;`sym`time;"the aj columns"];
    .qunit.assertEquals[type (a`probes)`sym;11h;"symbols, not the partition's enumeration"];
    .qunit.assertTrue[{x~asc x} (a`probes)`time;"probes in time order"];
    c:.qdev.cbench.params[tb;@[.qt.qdev.cbench.opts;`range`points;:;5000 2000]];
    .qunit.assertEquals[c`k`lo;1000 0j;"a range longer than the day is the day"];
    .qunit.assertEquals[count c`points;1000;"no more points than rows"]}

.qt.qdev.cbench.test_each_shape_reads_what_it_names:{[t]
    tb:.qt.qdev.cbench.table 1000;
    p:.qdev.cbench.params[tb;.qt.qdev.cbench.opts];
    sel:.qdev.cbench.table_selector tb;
    .qunit.assertEquals[.qdev.cbench.run_shape[`sweep;sel;p];1000j;"every row"];
    .qunit.assertEquals[.qdev.cbench.run_shape[`range;sel;p];10j;"the block"];
    .qunit.assertEquals[.qdev.cbench.run_shape[`point;sel;p];10j;"one row per point"];
    .qunit.assertEquals[.qdev.cbench.run_shape[`aj;sel;p];20j;"one row per probe"];
    .qunit.assertEquals[.qdev.cbench.run_shape[`none;sel;p];0j;"the baseline reads nothing"];
    .qunit.assertEquals[.qdev.cbench.read_rows[tb;2 3];(tb[`sym] 2 3;tb[`time] 2 3;tb[`px] 2 3;tb[`qty] 2 3;tb[`tags] 2 3);"every column at those rows"]}

.qt.qdev.cbench.test_the_hdb_selector_keeps_to_its_partition:{[t]
    tb:([] date:2026.01.01 2026.01.02 2026.01.02; a:1 2 3);
    sel:.qdev.cbench.hdb_selector[tb;`date;2026.01.02];
    .qunit.assertEquals[exec a from sel ();2 3;"the partition's rows"];
    .qunit.assertEquals[exec a from sel enlist (=;`a;3);enlist 3;"and the extra clauses"]}

.qt.qdev.cbench.test_aj_is_null_without_columns_to_join_on:{[t]
    tb:([] px:til 100);
    p:.qdev.cbench.params[tb;.qt.qdev.cbench.opts];
    .qunit.assertEquals[.qdev.cbench.run_shape[`aj;.qdev.cbench.table_selector tb;p];0Nj;"n/a"]}

/ --- counting reads --------------------------------------------------------

.qt.qdev.cbench.test_the_counter_degrades_to_none_without_a_tracer_it_may_run:{[t]
    both:`strace`dtruss!11b;
    .qunit.assertEquals[.qdev.cbench.counter[`l64;1000;both];`strace;"strace needs no root for its own child"];
    .qunit.assertEquals[.qdev.cbench.counter[`l64;1000;`strace`dtruss!00b];`none;"Linux without strace"];
    .qunit.assertEquals[.qdev.cbench.counter[`m64;501;both];`none;"dtruss needs root"];
    .qunit.assertEquals[.qdev.cbench.counter[`m64;0;both];`dtruss;"root on macOS"];
    .qunit.assertEquals[.qdev.cbench.counter[`w64;0;both];`none;"nothing elsewhere"]}

.qt.qdev.cbench.test_strace_summary_counts_the_read_family:{[t]
    s:("% time     seconds  usecs/call     calls    errors syscall";
       "------ ----------- ----------- --------- --------- ----------------";
       " 61.11    0.000110           1        70           read";
       " 27.78    0.000050           2        25         3 pread64";
       " 11.11    0.000020           4         5           openat";
       "------ ----------- ----------- --------- --------- ----------------";
       "100.00    0.000180           1       100         3 total");
    .qunit.assertEquals[.qdev.cbench.parse_reads[`strace;s];95j;"read and pread64, not openat or the total"];
    .qunit.assertEquals[.qdev.cbench.parse_strace enlist "strace: attach: ptrace(PTRACE_SEIZE, 1): Operation not permitted";0Nj;"no summary is not zero"]}

.qt.qdev.cbench.test_dtruss_summary_counts_the_read_family:{[t]
    s:("SYSCALL(args) \t\t = return";"read(0x3, \"\", 0x10)\t\t = 0 0";"";
       "CALL                                        COUNT";
       "open                                            4";
       "pread                                          40";
       "read                                           12";
       "read_nocancel                                   1");
    .qunit.assertEquals[.qdev.cbench.parse_reads[`dtruss;s];53j;"the summary, not the trace above it"];
    .qunit.assertEquals[.qdev.cbench.parse_dtruss enlist "dtrace: failed to initialize dtrace: DTrace requires additional privileges";0Nj;"no summary"]}

.qt.qdev.cbench.test_the_command_puts_the_environment_before_the_tracer:{[t]
    .qunit.assertEquals[.qdev.cbench.command["A='1' ";`none;"/o";"q x.q"];"A='1' q x.q";"untraced"];
    .qunit.assertEquals[.qdev.cbench.command["A='1' ";`strace;"/o";"q x.q"];"A='1' strace -f -c -e trace=read,pread64,readv,preadv,preadv2 -o '/o' q x.q";"strace writes its summary to the file"];
    .qunit.assertEquals[.qdev.cbench.command["";`dtruss;"/o";"q x.q"];"dtruss -c -f q x.q 2> '/o'";"dtruss to stderr"];
    .qunit.assertEquals[.qdev.cbench.env_prefix `DYLD_LIBRARY_PATH`LD_LIBRARY_PATH!("/opt/homebrew/lib";"");"DYLD_LIBRARY_PATH='/opt/homebrew/lib' ";"set ones only"]}

.qt.qdev.cbench.test_ratio_is_two_places_and_null_for_nothing:{[t]
    .qunit.assertEquals[.qdev.cbench.ratio[1000;300];3.33;"rounded"];
    .qunit.assertEquals[.qdev.cbench.ratio[0;0];0n;"nothing on disk"]}

/ --- copies (KDB-X: -19!) --------------------------------------------------

.qt.qdev.cbench.test_a_compressed_copy_reads_back_equal_and_leaves_the_source:{[t]
    root:.qt.qdev.cbench.hdb[];
    src:` sv root,`2026.01.02`quote;
    before:.qt.qdev.cbench.bytes root;
    out:hsym `$first system "mktemp -d";
    dst:` sv out,`quote;
    c:.qdev.cbench.parse_candidate "gzip/17/6";
    c[`columns]:(enlist `qty)!enlist 1 17 0j;
    same:.qt.qdev.cbench.with_sym[get ` sv root,`sym;{[src;dst;c;u]
        .qdev.cbench.write_copy[src;dst;c];
        / Two mapped tables never match (~ compares the mapping); their rows do.
        ((select from get ` sv dst,`)~select from get ` sv src,`;attr get ` sv dst,`sym)}[src;dst;c]];
    .qunit.assertEquals[.qt.qdev.cbench.bytes root;before;"the source is only read"];
    .qunit.assertTrue[same 0;"the same table"];
    .qunit.assertEquals[same 1;`p;"the attribute kept"];
    .qunit.assertEquals[`algorithm`logicalBlockSize`zipLevel#-21!` sv dst,`px;`algorithm`logicalBlockSize`zipLevel!2 17 6i;"the candidate's triple"];
    .qunit.assertEquals[(-21!` sv dst,`qty)`algorithm;1i;"a column override"];
    cb:.qdev.cbench.column_bytes dst;
    .qunit.assertEquals[cb`column;`sym`time`px`qty`tags;"every column of .d"];
    .qunit.assertTrue[all (cb`bytes)<cb`logical;"smaller on disk"];
    / A nested column's data file is padded differently by each writer, so only the flat ones match.
    .qunit.assertEquals[-1_cb`logical;-1_(.qdev.cbench.column_bytes src)`logical;"the same data uncompressed"];
    tg:` sv dst,`$"tags#";
    .qunit.assertEquals[(.qdev.cbench.column_bytes dst)[`bytes] 4;(.qdev.cbench.disk_bytes ` sv dst,`tags)+.qdev.cbench.disk_bytes tg;"a nested column counts its data file"];
    system "rm -rf ",(1_string root)," ",1_string out}

.qt.qdev.cbench.test_current_copies_the_files_as_they_are:{[t]
    root:.qt.qdev.cbench.hdb[];
    src:` sv root,`2026.01.02`quote;
    out:hsym `$first system "mktemp -d";
    dst:` sv out,`quote;
    .qdev.cbench.write_copy[src;dst;.qdev.cbench.parse_candidate "current"];
    a:.qt.qdev.cbench.bytes src;
    b:.qt.qdev.cbench.bytes dst;
    .qunit.assertEquals[value b;value a;"byte for byte"];
    system "rm -rf ",(1_string root)," ",1_string out}

/ --- the command (KDB-X: -19!, and a child q per run) ----------------------

/ Run the command with arguments; its output lines, then exit=<status>.
.qt.qdev.cbench.run:{[args]
    system "(",(first .z.X)," scripts/bench/compression.q ",args," -q 2>&1; echo exit=$?)"}

.qt.qdev.cbench.test_the_command_prints_setting_by_shape_and_never_writes_the_hdb:{[t]
    root:.qt.qdev.cbench.hdb[];
    before:.qt.qdev.cbench.bytes root;
    out:.qt.qdev.cbench.run "-hdb ",(1_string root)," -table quote -partition 2026.01.02 -candidates current,policy,qipc/17/0,gzip/17/6 -reps 1 -points 5 -probes 10";
    .qunit.assertEquals[last out;"exit=0";"it succeeds: ","\n" sv out];
    .qunit.assertEquals[.qt.qdev.cbench.bytes root;before;"the HDB is byte for byte as it was"];
    hdr:first where {x like "setting *"} each out;
    .qunit.assertFalse[null hdr;"the table's header"];
    body:{[l] w where 0<count each w:" " vs l} each (hdr+2)_out;
    body:body where {$[1<count x; (`$x 1) in .qdev.cbench.shape_names; 0b]} each body;
    .qunit.assertEquals[count body;16;"four settings by four shapes"];
    .qunit.assertEquals[distinct `$body[;0];`current`policy,`$("qipc/17/0";"gzip/17/6");"each setting"];
    .qunit.assertEquals[distinct `$body[;1];.qdev.cbench.shape_names;"each shape"];
    .qunit.assertTrue[all 0<"F"$body[;4];"a time for every row"];
    gz:"F"$first body[;3] where body[;0]~\:"gzip/17/6";
    .qunit.assertTrue[gz>1;"gzip shrinks the partition"];
    .qunit.assertTrue[any out like "current = as the HDB holds it: none (5/5 columns)*";"what current is"];
    .qunit.assertTrue[any out like "*policy = *";"what the policy is"];
    system "rm -rf ",1_string root}

.qt.qdev.cbench.test_a_candidate_out_of_range_stops_the_run_before_anything_is_copied:{[t]
    root:.qt.qdev.cbench.hdb[];
    out:.qt.qdev.cbench.run "-hdb ",(1_string root)," -table quote -partition 2026.01.02 -candidates none,lz4hc/11/5";
    .qunit.assertEquals[last out;"exit=1";"refused"];
    .qunit.assertTrue[any out like "bench_compression: bench: candidate lz4hc/11/5: block size 11 is not 12-20*";"naming the candidate"];
    system "rm -rf ",1_string root}

.qt.qdev.cbench.test_a_mixed_setting_writes_each_column_its_own:{[t]
    root:.qt.qdev.cbench.hdb[];
    src:` sv root,`2026.01.02`quote;
    out:hsym `$first system "mktemp -d";
    dst:` sv out,`quote;
    c:.qdev.cbench.parse_candidate "gzip/17/6+time=none+sym=gzip/20/6";
    .qt.qdev.cbench.with_sym[get ` sv root,`sym;{[src;dst;c;u] .qdev.cbench.write_copy[src;dst;c]}[src;dst;c]];
    .qunit.assertEquals[count -21!` sv dst,`time;0;"time uncompressed"];
    .qunit.assertEquals[(-21!` sv dst,`sym)`logicalBlockSize;20i;"sym in its own block size"];
    .qunit.assertEquals[(-21!` sv dst,`px)`logicalBlockSize;17i;"the rest in the table's"];
    system "rm -rf ",(1_string root)," ",1_string out}

.qt.qdev.cbench.test_an_override_of_a_column_the_table_lacks_is_refused:{[t]
    root:.qt.qdev.cbench.hdb[];
    out:.qt.qdev.cbench.run "-hdb ",(1_string root)," -table quote -partition 2026.01.02 -candidates none,gzip/17/6+nope=none";
    .qunit.assertEquals[last out;"exit=1";"refused"];
    .qunit.assertTrue[any out like "bench_compression: bench: candidate gzip/17/6+nope=none: the table has no column nope";"naming the column"];
    system "rm -rf ",1_string root}

.qt.qdev.cbench.test_a_scratch_directory_inside_the_hdb_is_refused:{[t]
    root:.qt.qdev.cbench.hdb[];
    before:.qt.qdev.cbench.bytes root;
    out:.qt.qdev.cbench.run "-hdb ",(1_string root)," -table quote -partition 2026.01.02 -candidates none -tmp ",(1_string root),"/scratch";
    .qunit.assertEquals[last out;"exit=1";"refused"];
    .qunit.assertTrue[any {0<count x ss "is inside the HDB; the benchmark writes only outside it"} each out;"saying why"];
    .qunit.assertEquals[(.qt.qdev.cbench.bytes root);before;"nothing written under it"];
    system "rm -rf ",1_string root}

.qt.qdev.cbench.test_missing_arguments_are_named:{[t]
    out:.qt.qdev.cbench.run "-table quote";
    .qunit.assertEquals[last out;"exit=1";"refused"];
    .qunit.assertTrue[any out like "bench_compression: missing -hdb, -partition*";"each missing flag"]}
