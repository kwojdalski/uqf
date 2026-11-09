/ test_compression_write.q - write-time compression, a declared choice (.qt.qetl.compression.write, #2151).
/ .
/ A table's mode says WHEN it is compressed: `age (TorQ's .cmp, later), `write (the HDB writer,
/ at save) or `both. Every case writes through the repo's own HDB writer (.qetl.io.hdb), finishes
/ it, and reads the result back with -21! - the file's own account of how it is stored.
/ .
/ KDB-X only: the pinned PeachQ has no -19! and no compressing .z.zd (#2153), so the cases that
/ compress are in tests/q/peachq_known_gaps.txt. Block size 17 throughout (2^14 is the smallest
/ macOS arm64 accepts).

/ A fresh HDB root, as a file symbol.
.qt.qetl.compression.write.root:{[] `$":",first system"mktemp -d"}

/ A table that compresses well: n rows of a day, sorted by sym then time as finish leaves them.
.qt.qetl.compression.write.batch:{[n]
    ([] time:2026.01.02D00:00:00.000000000+1000000000*til n; sym:n?`aa`bb`cc; px:n#1.5; flag:n#0b)}

/ Run f[] with the policy registries empty and a gzip block-17 level-6 stack default; put the
/ shipped policy back whatever happens.
.qt.qetl.compression.write.sandboxed:{[f]
    saved:(.qetl.compression.stack_default;.qetl.compression.table_policy;.qetl.compression.column_policy);
    `.qetl.compression.stack_default set ();
    `.qetl.compression.table_policy set 0#.qetl.compression.table_policy;
    `.qetl.compression.column_policy set 0#.qetl.compression.column_policy;
    .qetl.compression.set_default `algo`blocksize`level`minage!(2;17;6;0);
    r:@[{(1b;x[])};f;{(0b;x)}];
    `.qetl.compression.stack_default set saved 0;
    `.qetl.compression.table_policy set saved 1;
    `.qetl.compression.column_policy set saved 2;
    if[not r 0; 'r 1];
    r 1}

/ Declare `tbl` with a mode, write one finished partition of it, and return the root.
.qt.qetl.compression.write.save:{[tbl;mode]
    .qetl.compression.declare[tbl;(enlist `mode)!enlist mode];
    root:.qt.qetl.compression.write.root[];
    mgr:.qetl.io.hdb[root;`time];
    .qetl.io.write[mgr;tbl;.qt.qetl.compression.write.batch[20000]];
    .qetl.io.finish mgr;
    root}

/ A column file of the 2026.01.02 partition of tbl.
.qt.qetl.compression.write.file:{[root;tbl;col] hsym `$(1_string .Q.par[root;2026.01.02;tbl]),"/",string col}

/ How the four columns are stored: column -> -21! (empty for an uncompressed file).
.qt.qetl.compression.write.state:{[root;tbl]
    cs:`time`sym`px`flag;
    cs!{-21!x} each .qt.qetl.compression.write.file[root;tbl;] each cs}

/ The algorithm, block size and level of a column, or 0 0 0 when it is not compressed.
.qt.qetl.compression.write.triple:{[z] $[count z; (z`algorithm;z`logicalBlockSize;z`zipLevel); 0 0 0i]}

/ --- the three modes ------------------------------------------------------------------------

.qt.qetl.compression.write.test_age_mode_leaves_the_partition_uncompressed_for_the_age_pass:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        root:.qt.qetl.compression.write.save[`wt_age;`age];
        st:.qt.qetl.compression.write.state[root;`wt_age];
        .qunit.assertEquals[count each value st;0 0 0 0;"no column is compressed: -21! describes none"]}};

.qt.qetl.compression.write.test_write_mode_compresses_every_column_at_save:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        root:.qt.qetl.compression.write.save[`wt_write;`write];
        st:.qt.qetl.compression.write.state[root;`wt_write];
        .qunit.assertEquals[.qt.qetl.compression.write.triple each value st;4#enlist 2 17 6i;
            "gzip, block 17, level 6 on time, sym, px and flag"];
        .qunit.assertTrue[all {x[`compressedLength]<x`uncompressedLength} each value st;"smaller on disk than as written"]}};

.qt.qetl.compression.write.test_both_mode_compresses_at_save_too:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        root:.qt.qetl.compression.write.save[`wt_both;`both];
        .qunit.assertEquals[.qt.qetl.compression.write.triple each value .qt.qetl.compression.write.state[root;`wt_both];
            4#enlist 2 17 6i;"the writer's half of both"]}};

.qt.qetl.compression.write.test_write_mode_keeps_every_value_and_the_attribute:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        root:.qt.qetl.compression.write.save[`wt_write;`write];
        b:.qt.qetl.compression.write.batch[20000];
        got:get .qt.qetl.compression.write.file[root;`wt_write;`px];
        .qunit.assertEquals[got;b`px;"the column reads back as it was"];
        .qunit.assertEquals[attr get .qt.qetl.compression.write.file[root;`wt_write;`sym];`p;"p# on sym, put on a compressed file"]}};

.qt.qetl.compression.write.test_algorithm_zero_writes_uncompressed_whatever_the_mode:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .qetl.compression.declare[`wt_zero;`algo`mode!(0;`write)];
        root:.qt.qetl.compression.write.root[];
        mgr:.qetl.io.hdb[root;`time];
        .qetl.io.write[mgr;`wt_zero;.qt.qetl.compression.write.batch[20000]];
        .qetl.io.finish mgr;
        .qunit.assertEquals[count each value .qt.qetl.compression.write.state[root;`wt_zero];0 0 0 0;"algo 0 is leave as written"]}};

.qt.qetl.compression.write.test_a_column_override_is_applied_at_save:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .qetl.compression.declare[`wt_cols;(enlist `mode)!enlist `write];
        `.qetl.compression.column_policy upsert (`wt_cols;`flag;2j;17j;9j);
        root:.qt.qetl.compression.write.root[];
        mgr:.qetl.io.hdb[root;`time];
        .qetl.io.write[mgr;`wt_cols;.qt.qetl.compression.write.batch[20000]];
        .qetl.io.finish mgr;
        t3:.qt.qetl.compression.write.triple each value .qt.qetl.compression.write.state[root;`wt_cols];
        .qunit.assertEquals[t3;(2 17 6i;2 17 6i;2 17 6i;2 17 9i);"flag at level 9, the rest at the table's level 6"];
        .qunit.assertEquals[get .qt.qetl.compression.write.file[root;`wt_cols;`flag];20000#0b;"and flag still reads back"]}};

/ --- .z.zd is the process's, so nothing may leak ------------------------------------------------

.qt.qetl.compression.write.test_zd_is_unset_again_after_a_write_that_found_it_unset:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        if[`zd in key `.z; system "x .z.zd"];
        .qt.qetl.compression.write.save[`wt_write;`write];
        .qunit.assertTrue[not `zd in key `.z;".z.zd is not left set by the write"]}};

.qt.qetl.compression.write.test_zd_is_put_back_as_it_was_after_a_write_that_found_it_set:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .z.zd:16 1 0;
        .qt.qetl.compression.write.save[`wt_write;`write];
        r:.z.zd;
        system "x .z.zd";
        .qunit.assertEquals[r;16 1 0;"a value an operator set before is the value after"]}};

.qt.qetl.compression.write.test_zd_is_restored_and_the_error_rethrown_when_the_write_fails:{[t]
    if[`zd in key `.z; system "x .z.zd"];
    m:@[{.qetl.compression.with_zd[17 2 6;{[x] 'boom};enlist 1]; ""};::;{x}];
    r:`zd in key `.z;
    .qunit.assertEquals[m;"boom";"the failure comes out unchanged"];
    .qunit.assertTrue[not r;".z.zd is not left at 17 2 6 by a write that failed"]};

.qt.qetl.compression.write.test_with_zd_runs_the_function_under_the_triple:{[t]
    if[`zd in key `.z; system "x .z.zd"];
    .qunit.assertEquals[.qetl.compression.with_zd[17 2 6;{[x] .z.zd};enlist 0];17 2 6;"the function sees the triple"];
    .qunit.assertEquals[.qetl.compression.with_zd[();{[x] `zd in key `.z};enlist 0];0b;"an empty triple leaves .z.zd alone"]};

/ --- never twice ----------------------------------------------------------------------------

.qt.qetl.compression.write.test_a_compressed_partition_is_not_compressed_again_by_a_later_finish:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        root:.qt.qetl.compression.write.save[`wt_write;`write];
        / the policy changes under it: a later finish must not take the partition to the new one
        `.qetl.compression.table_policy set 0#.qetl.compression.table_policy;
        .qetl.compression.declare[`wt_write;`mode`level`blocksize!(`write;9;18)];
        mgr:.qetl.io.hdb[root;`time];
        more:.qt.qetl.compression.write.batch[20000];
        .qetl.io.write[mgr;`wt_write;update time:time+1D from more];
        .qetl.io.finish mgr;
        after:.qt.qetl.compression.write.state[root;`wt_write];
        .qunit.assertEquals[.qt.qetl.compression.write.triple each value after;4#enlist 2 17 6i;
            "still block 17 level 6: the partition was compressed once"];
        .qunit.assertEquals[.qetl.compression.for_write[`wt_write;.Q.dd[root;`$"2026.01.02/wt_write/"]];();
            "the helper itself returns no triple for a compressed partition"]}};

.qt.qetl.compression.write.test_the_age_pass_finds_nothing_to_do_in_a_both_partition:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        tbl:first .qetl.plant.names[];
        root:.qt.qetl.compression.write.save[tbl;`both];
        r:.qt.qetl.compression.write.age_plan[root;tbl];
        .qunit.assertTrue[0<count r;"the age pass sees the partition's files (both keeps its rows)"];
        .qunit.assertEquals[distinct r`action;enlist `skip;"and plans nothing: already compressed, so never twice"]}};

.qt.qetl.compression.write.test_the_age_pass_is_held_off_a_write_table:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        tbl:first .qetl.plant.names[];
        root:.qt.qetl.compression.write.save[tbl;`write];
        r:.qt.qetl.compression.write.age_plan[root;tbl];
        .qunit.assertEquals[count r;0;"a write table's age rows select nothing, so .cmp neither compresses nor decompresses it"]}};

.qt.qetl.compression.write.test_the_age_pass_still_compresses_an_age_table:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        tbl:first .qetl.plant.names[];
        root:.qt.qetl.compression.write.save[tbl;`age];
        r:.qt.qetl.compression.write.age_plan[root;tbl];
        .qunit.assertEquals[distinct r`action;enlist `compress;"the control: an age table is the age pass's"]}};

/ What TorQ's age pass would do to tbl's partition under the policy's own CSV, with the real .cmp
/ loaded for the duration (the fixture of test_torq_compress.q).
.qt.qetl.compression.write.age_plan:{[root;tbl]
    .qt.fixture.torq_cmp.load[];
    f:` sv root,`cfg.csv;
    f 0: .qetl.compression.csv[];
    r:.[{(1b;.qtorq.compress.plan[x;y;3650])};(root;f);{(0b;x)}];
    .qt.fixture.torq_cmp.unload[];
    if[not r 0; 'r 1];
    select from r 1 where table=tbl}

/ --- the declaration -------------------------------------------------------------------------

.qt.qetl.compression.write.test_mode_is_age_unless_declared:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .qetl.compression.declare[`wt_a;(enlist `level)!enlist 6];
        .qunit.assertEquals[.qetl.compression.resolve[`wt_a]`mode;`age;"the default is the age pass, as before"];
        .qetl.compression.declare[`wt_b;(enlist `mode)!enlist `both];
        .qunit.assertEquals[.qetl.compression.resolve[`wt_b]`mode;`both;"declared"]}};

.qt.qetl.compression.write.test_a_mode_the_policy_does_not_know_is_refused_by_name:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .qunit.assertThrows[{[m] .qetl.compression.declare[`wt_c;(enlist `mode)!enlist m]};`eod;
            "compression: wt_c: mode `eod is not one of age, write, both";"a mode is one of three"];
        .qunit.assertThrows[{[m] .qetl.compression.declare[`wt_c;(enlist `mode)!enlist m]};"write";
            "*mode must be one of age, write, both*";"a string is not a symbol"]}};

.qt.qetl.compression.write.test_a_write_table_is_pinned_away_from_the_age_pass_in_torqs_file:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        a:first .qetl.plant.names[];
        b:.qetl.plant.names[] 1;
        .qetl.compression.declare[a;`mode`minage!(`write;5)];
        .qetl.compression.declare[b;`mode`minage!(`both;5)];
        r:.qetl.compression.rows[];
        .qunit.assertEquals[exec minage from r where table=a;enlist .qetl.compression.never;"write: minage is the one nothing reaches"];
        .qunit.assertEquals[exec minage from r where table=b;enlist 5j;"both: its own minage"];
        .qunit.assertEquals[exec minage from r where table=`default;enlist 0j;"the stack default is age"]}};

/ --- #2274: the column rewrite is reported on the storage channel -------------------------------

.qt.qetl.compression.write.test_a_column_override_is_logged_on_the_storage_channel:{[t]
    .qt.qetl.compression.write.sandboxed {[]
        .qetl.compression.declare[`wt_cols;(enlist `mode)!enlist `write];
        `.qetl.compression.column_policy upsert (`wt_cols;`flag;2j;17j;9j);
        mgr:.qetl.io.hdb[.qt.qetl.compression.write.root[];`time];
        .qetl.io.write[mgr;`wt_cols;.qt.qetl.compression.write.batch[2000]];
        .qetl.log.debug[1b];
        lines:.qt.support.captured_log[0b;{[mgr;u] .qetl.io.finish mgr}[mgr]];
        .qetl.log.debug[0b];
        mine:lines where lines[;2]~\:"column compression applied";
        .qunit.assertEquals[(count mine;distinct mine[;1]);(1;enlist `storage);"the rewrite of a column is a storage line"]}};
