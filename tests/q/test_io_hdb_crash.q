/ test_io_hdb_crash.q - the HDB writer killed part way (.qt.qetl.hdb_crash, #2184 #2185 #2187).
/ .
/ A kill is simulated by .qetl.io.step, the marker the stage-and-swap protocol calls after each
/ step: the tests replace it with a function that throws at one named step. Nothing cleans up after
/ a throw, so the disk is left exactly as a kill at that step leaves it. Each case then runs what
/ the next run does - recover, then finish - and holds the partition to one of two states: the one
/ before the run, or the whole one after it. Rows are checked for ALIGNMENT, not for count: sym,
/ k and px are functions of id, so a row carrying another row's sym is counted.

.qt.qetl.hdb_crash.n:20

/ Unsorted rows whose sym, k and px are functions of id.
.qt.qetl.hdb_crash.rows:{[]
    id:(7*til .qt.qetl.hdb_crash.n) mod .qt.qetl.hdb_crash.n;
    ([] time:2026.01.02D00:00:00.000000000+1000000*id; sym:`$"s",/:string id mod 5; id:id; k:id*3; px:id%7)}

.qt.qetl.hdb_crash.root:{[] `$":",first system"mktemp -d"}

/ The partition as a table with symbols decoded, or () when it is absent.
.qt.qetl.hdb_crash.read:{[root;t]
    p:.Q.par[root;2026.01.02;t];
    $[()~key hsym `$(string p),"/.d"; (); .qetl.io.read_part[hsym `$(string p),"/"]]}

/ Is every row of the table its own - sym, k and px the functions of its id?
.qt.qetl.hdb_crash.aligned:{[x]
    (all (x`sym)=`$"s",/:string (x`id) mod 5) and (all (x`k)=3*x`id) and all (x`px)=(x`id)%7}

/ The whole state after finish, for comparison.
.qt.qetl.hdb_crash.sorted:{[] `sym`time xasc .qt.qetl.hdb_crash.rows[]}

/ Run f (a function of one ignored argument, so it is not a call until here) with the step marker
/ throwing at `name`; 1b when the kill fired.
.qt.qetl.hdb_crash.kill_at:{[name;f]
    `.qt.qetl.hdb_crash.fired set 0b;
    `.qetl.io.step set {[name;s] if[s=name; `.qt.qetl.hdb_crash.fired set 1b; '"killed at ",string name]}[name];
    @[f;::;{[e] (::)}];
    `.qetl.io.step set {[name] (::)};
    .qt.qetl.hdb_crash.fired}

.qt.qetl.hdb_crash.setUp_reset:{[]
    `.qetl.io.touched set 0#.qetl.io.touched;
    `.qetl.io.step set {[name] (::)}}

/ An unsorted, unfinished partition, as a worker's append leaves it.
.qt.qetl.hdb_crash.written:{[]
    root:.qt.qetl.hdb_crash.root[];
    m:.qetl.io.hdb[root;`time];
    m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]];
    (root;m)}

.qt.qetl.hdb_crash.window:{[m] m[`recover][`hcrash;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000]}

/ --- #2184: finish never sorts the live partition ----------------------------

/ At the moment the sort is done and before the swap, the live partition is untouched.
.qt.qetl.hdb_crash.test_finish_leaves_the_live_partition_alone_while_it_sorts:{[t]
    rm:.qt.qetl.hdb_crash.written[]; root:rm 0; m:rm 1;
    before:.qt.qetl.hdb_crash.read[root;`hcrash];
    `.qt.qetl.hdb_crash.seen set ();
    `.qetl.io.step set {[root;s] if[s=`sorted; `.qt.qetl.hdb_crash.seen set .qt.qetl.hdb_crash.read[root;`hcrash]]}[root];
    m[`finish][::];
    .qunit.assertEquals[.qt.qetl.hdb_crash.seen;before;"mid-finish, the live partition is byte-for-byte what it was"];
    .qunit.assertEquals[.qt.qetl.hdb_crash.read[root;`hcrash];.qt.qetl.hdb_crash.sorted[];"and after the swap it is the sorted copy"]};

/ A kill at each step of finish, then the next run's recover and finish.
.qt.qetl.hdb_crash.finish_killed_at:{[name]
    rm:.qt.qetl.hdb_crash.written[]; root:rm 0; m:rm 1;
    pre:.qt.qetl.hdb_crash.read[root;`hcrash];
    post:.qt.qetl.hdb_crash.sorted[];
    fired:.qt.qetl.hdb_crash.kill_at[name;{[m;u] m[`finish][::]}[m]];
    mid:.qt.qetl.hdb_crash.read[root;`hcrash];
    / what a reader sees right after the kill: the old partition, the new one, or (between the
    / two renames, for an instant) none - never a mix
    ok_mid:(mid~()) or (mid~pre) or mid~post;
    `.qetl.io.touched set 0#.qetl.io.touched;
    .qt.qetl.hdb_crash.window m;
    rec:.qt.qetl.hdb_crash.read[root;`hcrash];
    ok_rec:(rec~pre) or rec~post;
    `.qetl.io.touched set 0#.qetl.io.touched;
    .qt.qetl.hdb_crash.window m;
    m[`finish][::];
    fin:.qt.qetl.hdb_crash.read[root;`hcrash];
    / PeachQ keeps no attribute (.qetl.io.on_peachq), so the attribute is judged on KDB-X only
    (fired;ok_mid;ok_rec;fin~post;.qt.qetl.hdb_crash.aligned fin;$[.qetl.io.on_peachq;`p;attr fin`sym])}

.qt.qetl.hdb_crash.test_finish_killed_after_the_copy_is_staged:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.finish_killed_at`staged;(1b;1b;1b;1b;1b;`p);"old or new, aligned, finished"]};

.qt.qetl.hdb_crash.test_finish_killed_after_the_sort_before_the_swap:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.finish_killed_at`sorted;(1b;1b;1b;1b;1b;`p);"old or new, aligned, finished"]};

.qt.qetl.hdb_crash.test_finish_killed_between_the_two_renames:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.finish_killed_at`live_retired;(1b;1b;1b;1b;1b;`p);"old or new, aligned, finished"]};

.qt.qetl.hdb_crash.test_finish_killed_after_the_swap_before_the_cleanup:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.finish_killed_at`swapped_in;(1b;1b;1b;1b;1b;`p);"old or new, aligned, finished"]};

/ A kill and its recovery never leave coverage ahead of the data: the ledger is staged by
/ finish_window only after publication returns, so a publication killed mid-protocol claims nothing,
/ and one that completes claims exactly its window.
.qt.qetl.hdb_crash.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .qt.support.reset_coverage_ledger[]}

.qt.qetl.hdb_crash.publish:{[m;ignored]
    m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]]; m[`finish][::]; .qt.qetl.hdb_crash.n}

.qt.qetl.hdb_crash.test_coverage_agrees_with_the_data_after_a_kill:{[t]
    root:.qt.qetl.hdb_crash.root[]; m:.qetl.io.hdb[root;`time];
    spec:`source_version`range_from`range_to!(`v1;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000);
    killed:.qt.qetl.hdb_crash.kill_at[`sorted;{[m;spec;u]
        .qetl.job.bounded.runtime.finish_window[`hcrash_w;`hcrash;`;spec;spec`range_from;spec`range_to;.qt.qetl.hdb_crash.publish[m;]]}[m;spec]];
    claimed_after_kill:count value `etl_coverage;
    .qt.qetl.hdb_crash.window m;
    m[`finish][::];
    .qunit.assertEquals[(killed;claimed_after_kill);(1b;0);"a killed publication claimed nothing"];
    / the rerun, as the worker does it: the first write is gone or whole; write what is missing
    if[()~.qt.qetl.hdb_crash.read[root;`hcrash]; m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]]; m[`finish][::]];
    .qetl.job.bounded.runtime.finish_window[`hcrash_w;`hcrash;`;spec;spec`range_from;spec`range_to;{20}];
    p:.qt.qetl.hdb_crash.read[root;`hcrash];
    .qunit.assertEquals[(count value `etl_coverage;p~.qt.qetl.hdb_crash.sorted[]);(1;1b);"the claim is made only with the data whole"]};

/ --- #2185: the first write of a table into a date is staged -----------------

.qt.qetl.hdb_crash.first_write_killed_at:{[name]
    root:.qt.qetl.hdb_crash.root[]; m:.qetl.io.hdb[root;`time];
    whole:.qt.qetl.hdb_crash.rows[];
    fired:.qt.qetl.hdb_crash.kill_at[name;{[m;u] m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]]}[m]];
    mid:.qt.qetl.hdb_crash.read[root;`hcrash];
    / absent, or whole - never a partition whose .d lists a column that has no file
    ok_mid:(mid~()) or mid~whole;
    `.qetl.io.touched set 0#.qetl.io.touched;
    threw:@[{.qt.qetl.hdb_crash.window x; 0b};m;{[e] 1b}];
    rec:.qt.qetl.hdb_crash.read[root;`hcrash];
    / the HDB answers a query over every column, on the date
    qry:$[()~rec; 1b; @[{select max px, max k from x; 1b};rec;{[e] 0b}]];
    (fired;ok_mid;threw;(rec~()) or rec~whole;qry)}

.qt.qetl.hdb_crash.test_first_write_killed_after_the_directory_is_made:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.first_write_killed_at`staged_dir;(1b;1b;0b;1b;1b);"absent or whole, recover does not throw"]};

.qt.qetl.hdb_crash.test_first_write_killed_after_the_rows_are_staged:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.first_write_killed_at`staged;(1b;1b;0b;1b;1b);"absent or whole, recover does not throw"]};

.qt.qetl.hdb_crash.test_first_write_killed_before_the_rename:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.first_write_killed_at`live_retired;(1b;1b;0b;1b;1b);"absent or whole, recover does not throw"]};

.qt.qetl.hdb_crash.test_first_write_killed_after_the_rename:{[t]
    .qunit.assertEquals[.qt.qetl.hdb_crash.first_write_killed_at`swapped_in;(1b;1b;0b;1b;1b);"absent or whole, recover does not throw"]};

/ A partition left as the OLD unstaged write left it - .d naming columns with no file - is set aside
/ by recover, which no longer throws, and the next write lands whole.
.qt.qetl.hdb_crash.test_recover_sets_aside_a_table_with_a_column_file_missing:{[t]
    root:.qt.qetl.hdb_crash.root[]; m:.qetl.io.hdb[root;`time];
    base:.qetl.io.part_path[root;2026.01.02;`hcrash];
    system"mkdir -p ",base;
    (hsym `$base,"/.d") set `time`sym`id`k`px;
    (hsym `$base,"/time") set 2026.01.02D00:00:00.000000000+1000000*til 20;
    n:.qt.qetl.hdb_crash.window m;
    q:(.qetl.io.staging root),"/quarantine/2026.01.02/hcrash";
    .qunit.assertEquals[(n;()~key hsym `$base;not ()~key hsym `$q,"/time");(0;1b;1b);
        "nothing left to finish, the date reads as absent, and the remains are kept"];
    m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]];
    m[`finish][::];
    .qunit.assertEquals[.qt.qetl.hdb_crash.read[root;`hcrash];.qt.qetl.hdb_crash.sorted[];"the next write is whole and sorted"]};

/ --- #2496: a later write restores a dead writer's retired copy first -----------

.qt.qetl.hdb_crash.keyed_rows:{[ids] ([] time:2026.01.02D10:00:00.000000000+60000000000*ids; sym:(count ids)#`EURUSD; id:ids; px:(count ids)#1.1)}

/ A writer killed between the two renames leaves the date with no table and every row in
/ staging/old. A sibling writer under the root lock, which runs no recover, must put it back
/ before it reads the date - not read it as empty and then delete the retired copy in its swap.
.qt.qetl.hdb_crash.test_a_write_after_a_kill_between_renames_keeps_the_retired_rows:{[t]
    root:.qt.qetl.hdb_crash.root[]; m:.qetl.io.hdb[root;`time];
    o:`on_conflict`row_key`time_column`range_from`range_to!(`upsert;`sym`id;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000);
    m[`write_keyed][`hkeyed;.qt.qetl.hdb_crash.keyed_rows[1 2];o];
    fired:.qt.qetl.hdb_crash.kill_at[`live_retired;{[m;o;u] m[`write_keyed][`hkeyed;.qt.qetl.hdb_crash.keyed_rows[1 2 3];o]}[m;o]];
    m[`write_keyed][`hkeyed;.qt.qetl.hdb_crash.keyed_rows[enlist 4];o];
    got:.qt.qetl.hdb_crash.read[root;`hkeyed];
    .qunit.assertEquals[(fired;asc got`id);(1b;1 2 4);"the rows the killed writer retired are back, with the new key"];
    .qunit.assertEquals[key hsym `$(.qetl.io.staging root),"/old/2026.01.02";`symbol$();"and nothing is left in staging"]};

/ --- #2187: every path is one shell word -------------------------------------

.qt.qetl.hdb_crash.test_a_root_with_a_space_touches_only_its_own_paths:{[t]
    base:first system"mktemp -d";
    system"mkdir -p '",base,"/uqf/hdb' '",base,"/uqf prod'";
    (hsym `$base,"/uqf/hdb/precious.txt") 0: enlist "precious";
    root:hsym `$base,"/uqf prod/hdb";
    system"mkdir -p '",base,"/uqf prod/hdb'";
    m:.qetl.io.hdb[root;`time];
    m[`write][`hcrash;.qt.qetl.hdb_crash.rows[]];
    opts:`on_conflict`row_key`time_column`range_from`range_to!(`replace;`sym`time;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000);
    m[`write_keyed][`hcrash;.qt.qetl.hdb_crash.rows[];opts];
    m[`finish][::];
    sweep:.qetl.io.sweep_staging[root;`hcrash];
    retired:.qetl.io.retire[root;2026.01.02;`hcrash];
    .qunit.assertEquals[(key hsym `$base,"/uqf/hdb";key hsym `$base,"/uqf";sweep;retired);
        (enlist `precious.txt;enlist `hdb;0;1b);"the sibling directory survives every step"];
    .qunit.assertEquals[(key hsym `$base,"/prod";asc key hsym `$base,"/uqf prod");(();asc (`hdb;`$"hdb.staging"));"nothing stray beside it"]};

/ --- #2274: the repair a recovery reports is on the storage channel ------------

.qt.qetl.hdb_crash.test_a_recovery_reports_its_repair_on_the_storage_channel:{[t]
    rm:.qt.qetl.hdb_crash.written[]; m:rm 1;
    .qt.qetl.hdb_crash.kill_at[`live_retired;{[m;u] m[`finish][::]}[m]];
    `.qetl.io.touched set 0#.qetl.io.touched;
    lines:.qt.support.captured_log[0b;{[m;u] .qt.qetl.hdb_crash.window m}[m]];
    repaired:lines where lines[;2]~\:"repaired partitions a killed write left behind";
    .qunit.assertEquals[(count repaired;distinct repaired[;1]);(1;enlist `storage);
        "one repair line, on the storage channel, not under a component name"]};

/ --- #2285: one sym, at the HDB root -----------------------------------------

/ Every file called sym anywhere under the root, as paths relative to it. A table with a symbol
/ column named sym has a column file of that name, so those are excluded by looking only at the
/ levels a sym FILE can wrongly sit at: the root itself and the date folders.
.qt.qetl.hdb_crash.stray_syms:{[root]
    r:1_string root;
    all_found:system "find ",r," -name sym -type f";
    rel:(1+count r)_/:all_found;
    rel where 2>=count each "/" vs/: rel}

/ After a first write and an append into the same date, the only sym FILE is the root's, and the
/ symbols still decode to what was written. KDB-X refuses an HDB with a sym inside a partition.
.qt.qetl.hdb_crash.test_a_write_leaves_one_sym_at_the_root_and_none_in_a_partition:{[t]
    rm:.qt.qetl.hdb_crash.written[]; root:rm 0; m:rm 1;
    base:.qt.qetl.hdb_crash.rows[];
    more:base;
    more[`id]:100+base`id;
    more[`sym]:`$"t",/:string (base`id) mod 3;
    m[`write][`hcrash;more];
    m[`finish][::];
    .qunit.assertEquals[.qt.qetl.hdb_crash.stray_syms root;enlist "sym";"the HDB root holds the only sym file"];
    got:.qt.qetl.hdb_crash.read[root;`hcrash];
    .qunit.assertEquals[count got;2*.qt.qetl.hdb_crash.n;"both writes are in the partition"];
    .qunit.assertEquals[asc distinct got`sym;asc distinct (.qt.qetl.hdb_crash.rows[]`sym),more`sym;"the symbols decode to what was written"]};
