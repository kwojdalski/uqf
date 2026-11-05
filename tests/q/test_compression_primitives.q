// test_compression_primitives.q - the interpreter's file-compression
// primitives that .qetl.compress and the write-time policy stand on
// (.qt.qetl.compress.primitives, #2153).
//
// Nothing here tests uqf code: each case pins one thing q itself must do for
// the HDB compression work (#2146-#2152) to be sound - `-19!` compressing a
// file, `-21!` describing one, `set` given a (file;blocksize;algo;level)
// triple, `.z.zd` as the default for every `set`, and a compressed column read
// back through `get`, a splayed table and `select` on a partitioned database,
// with its attribute. KDB-X passes all of them. The pinned PeachQ supports only
// part of this; what it lacks is listed in tests/q/peachq_known_gaps.txt, and
// docs/reference/environment.md#peachq-and-file-compression has the matrix.
//
// Block size 17 (128 kB) throughout: the smallest block size KDB-X accepts is
// the page size, 2^12 on Linux x86-64 but 2^14 on macOS arm64, so 12 is not
// portable. Algorithms 1 (q IPC) and 2 (gzip) only: 3-5 (snappy, lz4, zstd)
// need a shared library on the loader's path, which is a fact about the host.


/ A scratch directory, as a string.
.qt.qetl.compress.primitives.scratch:{[] first system "mktemp -d"}

/ A file in a fresh scratch directory.
/ @param nm the file's name, as a string
/ @return a file symbol
.qt.qetl.compress.primitives.file:{[nm] hsym `$.qt.qetl.compress.primitives.scratch[],"/",nm}

/ A vector that compresses well and is not trivially constant.
.qt.qetl.compress.primitives.vec:{[] 10000#til 100}

/ Run a function with .z.zd set, and unset it again whatever happens.
/ @param zd the value for .z.zd
/ @param fn a function of one ignored argument
/ @return what fn returns; rethrows what it throws
.qt.qetl.compress.primitives.with_zd:{[zd;fn]
    .z.zd:zd;
    r:@[{(1b;x[::])};fn;{(0b;x)}];
    system "x .z.zd";
    $[r 0; r 1; 'r 1]}

/ What -21! reports of a compressed file, without the two lengths.
.qt.qetl.compress.primitives.settings:{[f] `algorithm`logicalBlockSize`zipLevel#-21!f}

/ A file KDB-X wrote with ((f;17;2;6) set 20000#42): its bytes, exactly. Two
/ blocks, because a one-block file is laid out the same by every writer seen so
/ far and the block table after the first is where they part. This is the
/ on-disk format the compression work reads, so an interpreter that reads only
/ its own writing fails here.
.qt.qetl.compress.primitives.kdbx_gzip:0x6b787a6970706564789cedc5210d00000800301c35d0f4cfc7b074e0379fcab8dab66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbf6db17a5678168789cedc5310100000c02a0e5b17fc01d1630003ce42ab66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbb66ddbe30f1d2b51b3030000000206000010710200000000002b010000000000000000020000000000dd0000000200000046000000020000000200000000000000

.qt.qetl.compress.primitives.test_a_set_given_a_triple_writes_a_file_21_describes:{[t]
    v:.qt.qetl.compress.primitives.vec[];
    plain:.qt.qetl.compress.primitives.file["plain"];
    plain set v;
    f:.qt.qetl.compress.primitives.file["gz"];
    (f;17;2;6) set v;
    .qunit.assertEquals[.qt.qetl.compress.primitives.settings[f];
        `algorithm`logicalBlockSize`zipLevel!2 17 6i;"gzip, 128 kB blocks, level 6"];
    d:-21!f;
    .qunit.assertEquals[d`uncompressedLength;hcount plain;"the length the plain file has"];
    .qunit.assertTrue[d[`compressedLength]<d`uncompressedLength;"smaller than it was"];
    .qunit.assertEquals[get f;v;"reads back the same"]}

.qt.qetl.compress.primitives.test_21_of_an_uncompressed_file_is_an_empty_dictionary:{[t]
    f:.qt.qetl.compress.primitives.file["plain"];
    f set .qt.qetl.compress.primitives.vec[];
    .qunit.assertEquals[-21!f;(`symbol$())!();"how a plain file is told from a compressed one"]}

.qt.qetl.compress.primitives.test_algorithm_1_round_trips:{[t]
    v:.qt.qetl.compress.primitives.vec[];
    f:.qt.qetl.compress.primitives.file["ipc"];
    (f;17;1;0) set v;
    .qunit.assertEquals[(-21!f)`algorithm;1i;"the q IPC codec"];
    .qunit.assertEquals[get f;v;"reads back the same"]}

.qt.qetl.compress.primitives.test_19_compresses_an_existing_file:{[t]
    v:.qt.qetl.compress.primitives.vec[];
    src:.qt.qetl.compress.primitives.file["src"];
    src set v;
    dst:.qt.qetl.compress.primitives.file["dst"];
    .qunit.assertEquals[-19!(src;dst;17;2;6);dst;"-19! returns the target"];
    .qunit.assertEquals[.qt.qetl.compress.primitives.settings[dst];
        `algorithm`logicalBlockSize`zipLevel!2 17 6i;"the settings it was given"];
    .qunit.assertEquals[get dst;v;"the copy holds the same values"];
    .qunit.assertEquals[-21!src;(`symbol$())!();"the source is left as it was"]}

.qt.qetl.compress.primitives.test_19_with_algorithm_0_writes_an_uncompressed_copy:{[t]
    v:.qt.qetl.compress.primitives.vec[];
    src:.qt.qetl.compress.primitives.file["gz"];
    (src;17;2;6) set v;
    dst:.qt.qetl.compress.primitives.file["plain"];
    -19!(src;dst;17;0;0);
    .qunit.assertEquals[-21!dst;(`symbol$())!();"decompressed"];
    .qunit.assertEquals[get dst;v;"the same values"]}

.qt.qetl.compress.primitives.test_zd_compresses_every_column_of_a_splayed_set:{[t]
    root:.qt.qetl.compress.primitives.scratch[];
    tbl:.Q.en[hsym `$root] ([] sym:10000#`a`b`c; n:.qt.qetl.compress.primitives.vec[]; px:10000#1.5 2.5);
    d:hsym `$root,"/t/";
    .qt.qetl.compress.primitives.with_zd[17 2 6;{[d;tbl;x] d set tbl}[d;tbl]];
    files:` sv/: (hsym `$root,"/t"),/:`sym`n`px;
    .qunit.assertEquals[{(-21!x)`algorithm} each files;3#2i;"every column is gzip"];
    .qunit.assertEquals[-21!hsym `$root,"/t/.d";(`symbol$())!();"the column list is not compressed"];
    .qunit.assertEquals[-21!hsym `$root,"/sym";(`symbol$())!();"nor is the enumeration domain .Q.en wrote"];
    .qunit.assertEquals[select from get d;tbl;"the table reads back the same"]}

.qt.qetl.compress.primitives.test_zd_zero_writes_uncompressed:{[t]
    f:.qt.qetl.compress.primitives.file["plain"];
    .qt.qetl.compress.primitives.with_zd[0 0 0;{[f;x] f set .qt.qetl.compress.primitives.vec[]}[f]];
    .qunit.assertEquals[-21!f;(`symbol$())!();"0 0 0 turns the default off"]}

.qt.qetl.compress.primitives.test_a_zd_dictionary_compresses_per_column:{[t]
    root:.qt.qetl.compress.primitives.scratch[];
    zd:(``n`px)!(0 0 0;17 2 6;17 1 0);
    v:.qt.qetl.compress.primitives.vec[];
    .qt.qetl.compress.primitives.with_zd[zd;{[r;v;x] (hsym `$r,"/t/") set ([] n:v; px:`float$v; k:v)}[root;v]];
    info:{[r;c] -21!hsym `$r,"/t/",string c}[root] each `n`px`k;
    .qunit.assertEquals[info[0 1]@\:`algorithm;2 1i;"each named column as its entry says"];
    .qunit.assertEquals[info 2;(`symbol$())!();"the rest by the default entry, here none"]}

.qt.qetl.compress.primitives.test_a_compressed_column_keeps_its_sorted_attribute:{[t]
    f:.qt.qetl.compress.primitives.file["s"];
    (f;17;2;6) set `s#til 10000;
    .qunit.assertEquals[attr get f;`s;"s# survives compression"]}

.qt.qetl.compress.primitives.test_a_compressed_column_keeps_its_parted_attribute:{[t]
    root:.qt.qetl.compress.primitives.scratch[];
    tbl:([] k:`p#asc 10000#til 10; v:10000?1f);
    .qt.qetl.compress.primitives.with_zd[17 2 6;{[r;tbl;x] (hsym `$r,"/t/") set tbl}[root;tbl]];
    .qunit.assertEquals[(-21!hsym `$root,"/t/k")`algorithm;2i;"the column is compressed"];
    .qunit.assertEquals[attr (get hsym `$root,"/t/")`k;`p;"p# survives compression"]}

.qt.qetl.compress.primitives.test_select_reads_a_compressed_partitioned_database:{[t]
    root:.qt.qetl.compress.primitives.scratch[];
    hdb:hsym `$root,"/hdb";
    tbl:update `p#sym from .Q.en[hdb] ([] sym:asc 9999#`a`b`c; px:9999#1 2 3f);
    write:{[hdb;tbl;x] {[hdb;tbl;d] .Q.dd[hdb;d,`trade`] set tbl}[hdb;tbl] each 2026.01.01 2026.01.02};
    .qt.qetl.compress.primitives.with_zd[17 2 6;write[hdb;tbl]];
    .qunit.assertEquals[(-21!hsym `$root,"/hdb/2026.01.02/trade/px")`algorithm;2i;"the partition is compressed"];
    / A child q loads it, so this process's tables and working directory stay its own.
    script:root,"/read.q";
    out:root,"/out.txt";
    (hsym `$script) 0: (
        "system \"l ",root,"/hdb\";";
        "-1 string count select from trade where date=2026.01.02, sym=`a;";
        "-1 string attr (select from trade where date=2026.01.02)`sym;";
        "-1 string exec sum px from select px from trade;";
        "exit 0");
    / `env` first: q's `system` reads a command starting with a q command letter as its own
    system "env ",.qt.support.q_interpreter[]," ",script," -q < /dev/null > ",out," 2>&1; true";
    .qunit.assertEquals[read0 hsym `$out;("3333";enlist "p";"39996");
        "rows by date and sym, the sym column parted, every row of both dates"]}

.qt.qetl.compress.primitives.test_a_file_kdbx_compressed_reads_back:{[t]
    f:.qt.qetl.compress.primitives.file["kdbx"];
    f 1: .qt.qetl.compress.primitives.kdbx_gzip;
    .qunit.assertEquals[.qt.qetl.compress.primitives.settings[f];
        `algorithm`logicalBlockSize`zipLevel!2 17 6i;"the header KDB-X wrote"];
    .qunit.assertEquals[get f;20000#42;"the values KDB-X wrote"]}

.qt.qetl.compress.primitives.test_an_unsupported_algorithm_is_refused_naming_it:{[t]
    f:.qt.qetl.compress.primitives.file["bad"];
    .qunit.assertThrows[{[f] (f;17;6;0) set til 10};f;"unsupported compression algo 6*";
        "algorithm 6 does not exist"]}

.qt.qetl.compress.primitives.test_an_out_of_range_level_is_refused_naming_it:{[t]
    f:.qt.qetl.compress.primitives.file["bad"];
    .qunit.assertThrows[{[f] (f;17;2;10) set til 10};f;"unsupported compression Level 10*";
        "gzip's levels stop at 9"]}

.qt.qetl.compress.primitives.test_an_out_of_range_block_size_is_refused:{[t]
    f:.qt.qetl.compress.primitives.file["bad"];
    .qunit.assertThrows[{[f] (f;21;2;6) set til 10};f;"bad blockSize*";"blocks stop at 2^20"];
    .qunit.assertThrows[{[f] (f;11;2;6) set til 10};f;"bad blockSize*";"and start at a page"]}

.qt.qetl.compress.primitives.test_a_malformed_zd_is_refused_not_ignored:{[t]
    f:.qt.qetl.compress.primitives.file["bad"];
    .qunit.assertThrows[.qt.qetl.compress.primitives.with_zd[17 2];{[f;x] f set til 10}[f];
        "file compression - expecting*";"a .z.zd of two items"]}
