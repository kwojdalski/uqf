/ test_compression.q - the HDB compression policy and the TorQ file it generates
/ (src/etl/core/compression.q, src/etl/compression_policy.q) (.qt.qetl.compression, #2152).
/ .
/ A policy is a dict per table - `algo`blocksize`level`minage`columns - validated
/ when it is declared, and resolved against a stack default. The refusals each
/ name the key and the range; the generated CSV is held to reference values.
/ Every case runs in a sandbox that swaps the three registries for empty ones
/ and puts the shipped policy back, so no test sees another's declarations.


/ Run f[] with empty registries; restore the shipped policy whatever happens.
.qt.qetl.compression.sandbox:{[f] .qt.qetl.compression.sandbox_with[{[f;x] f[]}[f];::]}

/ Run f x with empty registries; restore the shipped policy whatever happens.
.qt.qetl.compression.sandbox_with:{[f;x]
    saved:(.qetl.compression.stack_default;.qetl.compression.table_policy;.qetl.compression.column_policy);
    `.qetl.compression.stack_default set ();
    `.qetl.compression.table_policy set 0#.qetl.compression.table_policy;
    `.qetl.compression.column_policy set 0#.qetl.compression.column_policy;
    r:@[{(1b;x y)}[f];x;{(0b;x)}];
    `.qetl.compression.stack_default set saved 0;
    `.qetl.compression.table_policy set saved 1;
    `.qetl.compression.column_policy set saved 2;
    if[not r 0; 'r 1];
    r 1}

/ A full policy dict.
.qt.qetl.compression.full:{[a;b;l;m] `algo`blocksize`level`minage!(a;b;l;m)}

/ The stack default every sandboxed case starts from: gzip 6, block 16, a week.
.qt.qetl.compression.gz:{[] .qetl.compression.set_default .qt.qetl.compression.full[2;16;6;7]}

.qt.qetl.compression.test_the_stack_default_resolves_for_a_table_that_declares_nothing:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        r:.qetl.compression.resolve `orders;
        .qunit.assertEquals[r;`algo`blocksize`level`minage`columns`mode!(2j;16j;6j;7j;(`symbol$())!();`age);
            "no declaration is the default, with no column overrides, compressed by age"]}};

.qt.qetl.compression.test_a_table_declares_only_what_differs:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;`minage`blocksize!(3;18)];
        r:.qetl.compression.resolve `orders;
        .qunit.assertEquals[(r`algo;r`blocksize;r`level;r`minage);(2j;18j;6j;3j);
            "minage and blocksize replaced, algo and level inherited"]}};

.qt.qetl.compression.test_algorithm_zero_means_decompress_and_takes_no_block_or_level:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`reference_data;(enlist `algo)!enlist 0];
        r:.qetl.compression.resolve `reference_data;
        .qunit.assertEquals[(r`algo;r`blocksize;r`level);0 0 0j;"left uncompressed, block and level are not applicable"]}};

.qt.qetl.compression.test_the_algorithm_must_be_one_kdbx_has_0_to_5:{[t]
    .qt.qetl.compression.sandbox {[]
        f:{[a] .qetl.compression.set_default .qt.qetl.compression.full[a;16;0;7]};
        .qunit.assertThrows[f;6;"*algo 6 is not 0-5*";"past KDB-X's own range"];
        .qunit.assertThrows[f;-1;"*algo -1 is not 0-5*";"negative"];
        .qunit.assertThrows[f;`gzip;"*algo must be an integer*";"a name is not a number"];
        .qunit.assertEquals[(f 3)`algo;3j;"snappy is accepted"]}};

.qt.qetl.compression.test_lz4hc_and_zstd_are_refused_where_torqs_age_pass_would_read_them:{[t]
    / #2295: .cmp takes 0-3, and it is the age pass, so 4 and 5 are refused on
    / every policy whose mode lets the age pass apply it, naming the mode.
    .qt.qetl.compression.sandbox {[]
        f:{[a] .qetl.compression.set_default .qt.qetl.compression.full[a;16;1;7]};
        .qunit.assertThrows[f;4;"*algo 4 (lz4hc) needs mode `write: TorQ's .cmp, which compresses a mode `age table by age, takes 0-3*";"lz4hc, an age default"];
        .qunit.assertThrows[f;5;"*algo 5 (zstd) needs mode `write*";"zstd, an age default"]};
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};`algo`level!(5;3);
            "compression: orders: algo 5 (zstd) needs mode `write: TorQ's .cmp, which compresses a mode `age table by age, takes 0-3*";"a table compressed by age"];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};`algo`level`mode!(5;3;`both);
            "*algo 5 (zstd) needs mode `write: TorQ's .cmp, which compresses a mode `both table by age*";"both keeps its age rows, so it is refused too"];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`fx_markout;s]};`minage`columns!(14;(enlist `invert)!enlist 4 16 9);
            "*fx_markout: algo 4 (lz4hc) needs mode `write*";"and a column"]}};

.qt.qetl.compression.test_lz4hc_and_zstd_are_accepted_for_a_write_table:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;`algo`blocksize`level`mode!(5;17;3;`write)];
        .qetl.compression.declare[`fx_markout;`mode`columns!(`write;(enlist `invert)!enlist 4 16 9)];
        .qunit.assertEquals[(.qetl.compression.resolve `orders)`algo`blocksize`level;5 17 3j;"zstd, as declared"];
        .qunit.assertEquals[(.qetl.compression.resolve `fx_markout)[`columns]`invert;4 16 9j;"lz4hc on a column of a write table"]};
    .qt.qetl.compression.sandbox {[]
        / A write default may name zstd; a table that inherits it and is not write is refused.
        .qetl.compression.set_default `algo`blocksize`level`minage`mode!(5;17;3;7;`write);
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};(enlist `mode)!enlist `age;
            "*algo 5 (zstd) needs mode `write*";"the inherited algorithm, judged against the table's own mode"]}};

.qt.qetl.compression.test_lz4hc_and_zstd_take_their_own_levels:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        f:{[s] .qetl.compression.declare[`orders;s]};
        .qunit.assertThrows[f;`algo`level`mode!(5;23;`write);"*level 23 does not fit zstd - its levels are -7-22*";"zstd above"];
        .qunit.assertThrows[f;`algo`level`mode!(5;-8;`write);"*level -8 does not fit zstd*";"zstd below"];
        .qunit.assertThrows[f;`algo`level`mode!(4;17;`write);"*level 17 does not fit lz4hc - its levels are 0-16*";"lz4hc above"];
        .qunit.assertEquals[f `algo`level`mode!(5;-7;`write);`orders;"zstd's fastest level is accepted"]}};

.qt.qetl.compression.test_a_zstd_write_table_renders_a_row_torqs_cmp_loads:{[t]
    / The CSV must still pass .cmp.checkcsv: the write table's row, held out of
    / the age pass by its minage, carries 0 0 0 where zstd and lz4hc would be.
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        tbl:first .qetl.plant.names[];
        .qetl.compression.declare[tbl;`algo`blocksize`level`mode!(5;17;3;`write)];
        r:select from .qetl.compression.rows[] where table=tbl;
        .qunit.assertEquals[r;([] table:enlist tbl; minage:enlist .qetl.compression.never; column:enlist `default; calgo:enlist 0j; cblocksize:enlist 0j; clevel:enlist 0j);
            "never selected, and nothing .cmp refuses"];
        .qt.fixture.torq_cmp.load[];
        ok:@[{.cmp.checkcsv ("SISIII";enlist ",") 0: x; 1b};.qetl.compression.csv[];{x}];
        .qt.fixture.torq_cmp.unload[];
        .qunit.assertEquals[ok;1b;"TorQ's own checkcsv takes the whole file"]}};

.qt.qetl.compression.test_the_block_size_must_be_12_to_20:{[t]
    .qt.qetl.compression.sandbox {[]
        f:{[b] .qetl.compression.set_default .qt.qetl.compression.full[2;b;6;7]};
        .qunit.assertThrows[f;11;"*blocksize 11 is not 12-20*";"below"];
        .qunit.assertThrows[f;21;"*blocksize 21 is not 12-20*";"above"];
        .qunit.assertEquals[(f 20)`blocksize;20j;"the top of the range"]};
    .qt.qetl.compression.sandbox {[]
        .qunit.assertEquals[(.qetl.compression.set_default .qt.qetl.compression.full[2;12;6;7])`blocksize;12j;"the bottom of the range"]}};

.qt.qetl.compression.test_each_algorithm_takes_only_its_own_levels:{[t]
    / (algo; level; fits)
    cases:((2;0;1b);(2;9;1b);(2;10;0b);(2;-1;0b);(1;0;1b);(1;1;0b);(3;0;1b);(3;1;0b));
    {[c]
        r:@[{.qt.qetl.compression.sandbox_with[{[x] .qetl.compression.set_default .qt.qetl.compression.full[x 0;16;x 1;7]};x]; 1b};c;{0b}];
        .qunit.assertEquals[r;c 2;"algo ",string[c 0]," level ",string[c 1]]} each cases}

.qt.qetl.compression.test_a_level_that_misses_the_algorithm_names_the_range:{[t]
    .qt.qetl.compression.sandbox {[]
        f:{[s] .qetl.compression.set_default s};
        .qunit.assertThrows[f;.qt.qetl.compression.full[2;16;10;7];"*level 10 does not fit gzip - its levels are 0-9*";"gzip"];
        .qunit.assertThrows[f;.qt.qetl.compression.full[1;16;5;7];"*level 5 does not fit qipc - it takes no level*";"qipc"]}};

.qt.qetl.compression.test_a_table_that_changes_the_algorithm_is_held_to_the_levels_it_inherits:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        / The default's level 6 fits gzip, but not qipc or snappy, which take none.
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};(enlist `algo)!enlist 1;
            "*level 6 does not fit qipc*";"refused at declaration, not at generation"];
        .qetl.compression.declare[`trades;`algo`level!(3;0)];
        .qunit.assertEquals[(.qetl.compression.resolve[`trades])`algo`level;3 0j;"snappy with the level it takes"]}};

.qt.qetl.compression.test_minage_is_a_non_negative_number_of_days:{[t]
    .qt.qetl.compression.sandbox {[]
        f:{[m] .qetl.compression.set_default .qt.qetl.compression.full[2;16;6;m]};
        .qunit.assertThrows[f;-1;"*minage -1 must be a non-negative number of days*";"negative"];
        .qunit.assertThrows[f;1.5;"*minage must be an integer*";"a fraction of a day"];
        .qunit.assertEquals[(f 0)`minage;0j;"zero is compress at once"]}};

.qt.qetl.compression.test_an_unknown_key_is_refused_naming_it:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};`minage_days`algo!(3;2);
            "*declares minage_days*";"a typo is not silently ignored"]}};

.qt.qetl.compression.test_sym_dot_d_par_txt_and_the_coverage_ledger_cannot_be_declared:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        f:{[n] .qetl.compression.declare[n;(enlist `algo)!enlist 2]};
        .qunit.assertThrows[f;`sym;"*no policy can include sym*";"the sym file"];
        .qunit.assertThrows[f;`.d;"*no policy can include .d*";".d files"];
        .qunit.assertThrows[f;`par.txt;"*no policy can include par.txt*";"par.txt"];
        .qunit.assertThrows[f;`etl_coverage;"*no policy can include etl_coverage*";"the coverage ledger"]}};

.qt.qetl.compression.test_a_table_is_declared_once:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;(enlist `minage)!enlist 3];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`orders;s]};(enlist `minage)!enlist 4;
            "*orders is already declared*";"one home per table"]}};

.qt.qetl.compression.test_the_stack_default_is_declared_once_and_takes_no_columns:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.set_default s};.qt.qetl.compression.full[2;16;6;7];
            "*already declared*";"twice"]};
    .qt.qetl.compression.sandbox {[]
        .qunit.assertThrows[{[s] .qetl.compression.set_default s};`algo`blocksize`level`minage`columns!(2;16;6;7;(enlist `active)!enlist enlist 2 16 9);
            "*takes no columns*";"a column belongs to a table"]}};

.qt.qetl.compression.test_the_stack_default_must_be_complete:{[t]
    .qt.qetl.compression.sandbox {[]
        .qunit.assertThrows[{[s] .qetl.compression.set_default s};`algo`minage!(2;7);"*needs blocksize, level*";"gzip needs both"];
        r:.qetl.compression.set_default `algo`minage!(0;7);
        .qunit.assertEquals[r;`algo`blocksize`level`minage`mode!(0j;0j;0j;7j;`age);"algorithm 0 needs neither"]}};

.qt.qetl.compression.test_a_column_override_must_name_a_column_of_the_table:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`fx_markout;s]};`minage`columns!(14;`invert`no_such_col!(2 16 9;2 16 9));
            "*the table has no column no_such_col*";"checked against the plant schema"];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`fx_markout;s]};`minage`columns!(14;(enlist `sym)!enlist 2 16 99);
            "*level 99 does not fit gzip*";"the column's own triple is checked"]}};

.qt.qetl.compression.test_a_column_override_triple_is_checked_and_named:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`fx_markout;s]};`minage`columns!(14;(enlist `invert)!enlist 9 16 9);
            "*fx_markout column invert: algo 9 is not 0-5*";"names the table and the column"];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`fx_markout;s]};`minage`columns!(14;(enlist `invert)!enlist 2 16);
            "*must be (algo;blocksize;level)*";"a triple"]}};

.qt.qetl.compression.test_column_overrides_need_a_table_whose_columns_are_known:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qunit.assertThrows[{[s] .qetl.compression.declare[`event_tape;s]};`minage`columns!(14;(enlist `x)!enlist 2 16 9);
            "*only a plant table has them*";"a worker's dataset has no schema to check against"]}};

.qt.qetl.compression.test_a_columns_only_spec_survives_the_one_row_table_q_makes_of_it:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        / enlist of a dict is a table in q, and this spec's one key makes exactly that.
        .qetl.compression.declare[`fx_markout;(enlist `columns)!enlist (enlist `invert)!enlist 2 16 9];
        r:.qetl.compression.resolve `fx_markout;
        .qunit.assertEquals[r`columns;(enlist `invert)!enlist 2 16 9j;"the override is kept"]}};

.qt.qetl.compression.test_the_rows_are_default_then_the_ledger_then_each_table_and_its_columns:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;(enlist `algo)!enlist 0];
        .qetl.compression.declare[`fx_markout;`minage`columns!(14;`invert`status!(2 18 9;0 0 0))];
        r:.qetl.compression.rows[];
        .qunit.assertEquals[r`table;`default`etl_coverage`fx_markout`fx_markout`fx_markout`orders;"default, ledger, then tables in name order"]}};

.qt.qetl.compression.test_the_csv_is_held_to_a_reference:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;(enlist `algo)!enlist 0];
        .qetl.compression.declare[`fx_markout;`minage`columns!(14;(enlist `invert)!enlist 2 18 9)];
        .qunit.assertEquals[.qetl.compression.csv[];(
            "table,minage,column,calgo,cblocksize,clevel";
            "default,7,default,2,16,6";
            "etl_coverage,0,default,0,0,0";
            "fx_markout,14,default,2,16,6";
            "fx_markout,14,invert,2,18,9";
            "orders,7,default,0,0,0");
            "TorQ's six columns: a default row, the ledger pinned at 0, then each table and its overrides"]}};

.qt.qetl.compression.test_the_csv_is_the_same_every_time_whatever_the_declaration_order:{[t]
    a:.qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`orders;(enlist `minage)!enlist 3];
        .qetl.compression.declare[`trades;(enlist `minage)!enlist 4];
        .qetl.compression.csv[]};
    b:.qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`trades;(enlist `minage)!enlist 4];
        .qetl.compression.declare[`orders;(enlist `minage)!enlist 3];
        .qetl.compression.csv[]};
    .qunit.assertEquals[a;b;"deterministic"]};

.qt.qetl.compression.test_a_policy_for_a_table_that_does_not_exist_is_refused_at_generation:{[t]
    .qt.qetl.compression.sandbox {[]
        .qt.qetl.compression.gz[];
        .qetl.compression.declare[`ordres;(enlist `minage)!enlist 3];
        .qunit.assertThrows[{[x] .qetl.compression.rows[]};0;"*ordres is declared but neither a plant table nor a bounded worker's dataset*";
            "a misspelt name would otherwise compress nothing and say nothing"]}};

.qt.qetl.compression.test_there_is_nothing_to_generate_without_a_stack_default:{[t]
    .qt.qetl.compression.sandbox {[]
        .qunit.assertThrows[{[x] .qetl.compression.csv[]};0;"*no stack default is declared*";"nothing to fall back on"]}};

/ ------------------------------------------------------------- THE SHIPPED POLICY

.qt.qetl.compression.test_the_shipped_policy_declares_a_default_and_only_known_tables:{[t]
    .qunit.assertTrue[not ()~.qetl.compression.stack_default;"src/etl/compression_policy.q declares the default"];
    .qunit.assertEquals[.qetl.compression.declared[] except .qetl.compression.known[];`symbol$();"every declared table exists"]};

.qt.qetl.compression.test_the_shipped_policy_resolves_for_every_table_it_names:{[t]
    {[n] r:.qetl.compression.resolve n;
        .qunit.assertTrue[r[`minage]>=0;string[n]," has a minage"]} each .qetl.compression.declared[]};

.qt.qetl.compression.test_the_shipped_policy_leaves_small_reference_tables_alone_and_compresses_market_data:{[t]
    .qunit.assertEquals[(.qetl.compression.resolve `reference_data)`algo;0j;"a few rows a day stay as written"];
    r:.qetl.compression.resolve `market_data;
    .qunit.assertEquals[(r`algo;r`minage);(2j;3j);"market data is compressed after three days"];
    .qunit.assertEquals[(.qetl.compression.resolve `fx_markout)`minage;14j;"markouts after a fortnight"]};

.qt.qetl.compression.test_the_committed_torq_overlay_is_what_the_policy_renders:{[t]
    .qunit.assertEquals[read0 `:scripts/torqconfig/compressionconfig.csv;.qetl.compression.csv[];
        "derive.py compression-config keeps it so; this is the same check without Python"]};
