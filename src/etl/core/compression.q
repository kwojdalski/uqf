/ compression.q - the HDB compression policy, declared once (.qetl.compression, #2152).
/ .
/ Nothing used to say how an HDB table is compressed. TorQ's .cmp
/ (lib/torq/code/common/compress.q) reads compressionconfig.csv, and the one
/ the vendored tree ships is a single `default` row; a hand-written CSV in this
/ repository would be a second copy of facts that belong to the datasets. So
/ the policy is declared here, beside the tables (src/etl/compression_policy.q),
/ and scripts/torqconfig/compressionconfig.csv - the overlay file TorQ finds
/ before its own - is GENERATED from it:
/     uv run python scripts/generate/derive.py compression-config [--check]
/ .
/ THE SHAPE, one q dict per table - the library that applies a policy (#2146)
/ reads exactly this, so the keys are fixed:
/ .
/   `algo       0 decompress, 1 qipc, 2 gzip, 3 snappy
/   `blocksize  the logical block size as a power of two, 12 to 20
/   `level      gzip 0-9; qipc and snappy take none (0)
/   `minage     whole days a partition must be old before it is compressed
/   `columns    column -> (algo;blocksize;level), for the columns that differ
/ .
/ A table's declaration is PARTIAL: it states what differs from the stack
/ default, and `resolve` returns the whole dict. Algorithm 0 means "leave this
/ uncompressed" (or decompress what is): it has no block size or level, and
/ resolves to 0 for both.
/ .
/ TORQ'S .cmp IS THE ENGINE (#2146 wraps .cmp.showcomp and .cmp.compressmaxage),
/ and the CSV below is the file it reads. Its checkcsv
/ (lib/torq/code/common/compress.q) accepts algorithms 0-3 only, so lz4hc (4)
/ and zstd (5), which KDB-X has and .cmp does not, are refused here naming that.
/ The ranges below are the ones a declaration can be checked against without
/ q's TorQ: the authority on what .cmp loads is .cmp.checkcsv itself, which
/ derive.py compression-config runs over every CSV it renders, and
/ scripts/gates/tests/test_derive.py over the committed one - nothing in src/
/ copies its rules.
/ .
/ WHICH FILE TORQ READS. Not scripts/torqconfig: TorQ's .proc.getconfig prefers
/ the starter pack's appconfig/compressionconfig.csv. uqs copies the generated
/ file into TORQDATA when it bootstraps, and
/ scripts/processes/uqs_compression_config.q points .cmp.inputcsv at that copy,
/ loaded BEFORE compress.q, which reads it once as it loads (the reporter's
/ and DQE's pattern).
/ .
/ WHAT NO POLICY CAN INCLUDE. The sym file, `.d` files and par.txt are not
/ column data - compressing them breaks the database - and the coverage ledger
/ is read and rewritten whole by the pipeline. `declare` refuses all four by
/ name, and the generated CSV pins the ledger at algorithm 0 so TorQ's default
/ row cannot reach it (TorQ itself never touches the first three: .cmp skips
/ `.d`, and a file outside a table directory is not a table).
/ .
/ The guidance this encodes is in docs/reference/compression-policy.md.

\d .

/ The algorithms a policy can name, by number: the ones .cmp compresses with.
/ @eg .qetl.compression.algos 2  ->  `gzip
.qetl.compression.algos:0 1 2 3!`decompress`qipc`gzip`snappy

/ The KDB-X algorithms .cmp does not take: refused, saying so, rather than
/ called out of range.
.qetl.compression.beyond_torq:4 5!`lz4hc`zstd

/ The (lowest; highest) level each algorithm takes. qipc and snappy take none,
/ so their only level is 0.
.qetl.compression.levels:0 1 2 3!(0 0;0 0;0 9;0 0)

/ The logical block sizes (as a power of two): lowest and highest.
.qetl.compression.block_range:12 20

/ The keys a table's policy takes.
.qetl.compression.keys:`algo`blocksize`level`minage`columns`mode

/ WHEN a table is compressed (#2151), the one choice that is not about HOW:
/   `age    TorQ's .cmp compresses a partition once it is minage days old; the
/           writers leave it as written. Hot partitions stay fast to read. The default.
/   `write  the writers compress at save (.qetl.compression.for_write), and the
/           age pass is pinned away from the table (its CSV rows carry `never`
/           as minage), so a partition is compressed once, by the writer.
/   `both   the writers compress at save AND the age pass keeps its rows: it
/           finds nothing to do in a partition the writer compressed (an
/           already-compressed file is skipped, never compressed twice) and
/           catches one a writer could not, such as one TorQ's end-of-day wrote.
.qetl.compression.modes:`age`write`both

/ The minage that keeps TorQ's age pass off a `write table: 100 years, which no
/ partition reaches. .cmp has no "skip this table" row - algorithm 0 would
/ DECOMPRESS what the writer compressed - so the selection is made to find nothing.
.qetl.compression.never:36500j

/ The names no policy can name: the sym file, `.d`, par.txt and the coverage ledger.
.qetl.compression.excluded:`sym`.d`par.txt`etl_coverage

/ The stack default: the resolved dict, or () until one is declared.
.qetl.compression.stack_default:()

/ A table's own declaration; a null is "the stack default's".
.qetl.compression.table_policy:([table:`symbol$()] algo:`long$(); blocksize:`long$(); level:`long$(); minage:`long$(); mode:`symbol$())

/ Per-column overrides: (table; column) -> the triple.
.qetl.compression.column_policy:([table:`symbol$(); column:`symbol$()] algo:`long$(); blocksize:`long$(); level:`long$())

/ ------------------------------------------------------------ VALIDATION

/ Private: what a normalised policy supplied under a key, or a null long.
/ @private
.qetl.compression.supplied:{[s;k] $[k in key s; s k; 0Nj]}

/ Private: the mode, one of `age`write`both.
/ @private
.qetl.compression.check_mode:{[who;m]
    if[not -11h=type m; 'who,": mode must be one of ",(", " sv string .qetl.compression.modes),", not a value of type ",string type m];
    if[not m in .qetl.compression.modes; 'who,": mode `",string[m]," is not one of ",", " sv string .qetl.compression.modes];
    m}

/ Private: what a normalised policy supplied as its mode, or a null symbol.
/ @private
.qetl.compression.supplied_mode:{[s] $[`mode in key s; s`mode; `]}

/ Private: an integer atom of any width, as a long.
/ @private
.qetl.compression.int_atom:{[who;name;v]
    if[not (type v) in -5 -6 -7h; 'who,": ",name," must be an integer, not ",$[-11h=type v; "`",string v; "a value of type ",string type v]];
    "j"$v}

/ Private: the algorithm, 0-3.
/ @private
.qetl.compression.check_algo:{[who;a]
    n:.qetl.compression.int_atom[who;"algo";a];
    if[n in key .qetl.compression.beyond_torq;
        'who,": algo ",string[n]," (",string[.qetl.compression.beyond_torq n],") is not supported by TorQ's .cmp, which takes 0-3 (0 decompress, 1 qipc, 2 gzip, 3 snappy)"];
    if[not n in key .qetl.compression.algos;
        'who,": algo ",string[n]," is not 0-3 (0 decompress, 1 qipc, 2 gzip, 3 snappy)"];
    n}

/ Private: the logical block size, 12-20.
/ @private
.qetl.compression.check_blocksize:{[who;b]
    n:.qetl.compression.int_atom[who;"blocksize";b];
    if[not n within .qetl.compression.block_range;
        'who,": blocksize ",string[n]," is not 12-20 (a power of two: 16 is 64 KB)"];
    n}

/ Private: the minimum partition age, in whole days.
/ @private
.qetl.compression.check_minage:{[who;m]
    n:.qetl.compression.int_atom[who;"minage";m];
    if[n<0; 'who,": minage ",string[n]," must be a non-negative number of days"];
    n}

/ Private: a level against its algorithm. Algorithm 0 has none.
/ @private
.qetl.compression.check_level_for:{[who;a;l]
    if[a=0; :0j];
    r:.qetl.compression.levels a;
    if[not l within r;
        'who,": level ",string[l]," does not fit ",string[.qetl.compression.algos a]," - ",
            $[r~0 0j; "it takes no level (use 0)"; "its levels are ",string[r 0],"-",string r 1]];
    l}

/ Private: an (algo;blocksize;level) triple, as three longs. Algorithm 0 resolves
/ to 0 0 0, whatever else was given.
/ @private
.qetl.compression.check_triple:{[who;t]
    if[not 3=count t; 'who," must be (algo;blocksize;level)"];
    a:.qetl.compression.check_algo[who;t 0];
    if[a=0; :0 0 0j];
    b:.qetl.compression.check_blocksize[who;t 1];
    lv:.qetl.compression.int_atom[who;"level";t 2];
    (a;b;.qetl.compression.check_level_for[who;a;lv])}

/ Private: a table's column overrides, refused by name when a column is not the
/ table's or cannot be checked. (A column NAMED sym is a column like another: the
/ exclusion is the sym FILE, which is not in a table.)
/ @private
.qetl.compression.check_columns:{[who;cc;known;cs]
    / q turns `enlist <dict>` into a one-row table, which is what a spec with
    / `columns as its only key holds; its row is the dict that was meant.
    if[98h=type cc; if[1=count cc; cc:first cc]];
    if[not 99h=type cc; 'who,": columns must be a dict of column -> (algo;blocksize;level)"];
    if[0=count cc; :(`symbol$())!()];
    if[not 11h=type key cc; 'who,": columns must be keyed by column name symbols"];
    if[not count[key cc]=count distinct key cc; 'who,": columns names a column twice"];
    if[not known;
        'who,": column overrides need a table whose columns are known, and only a plant table has them"];
    if[count bad:(key cc) except cs;
        'who,": the table has no column ",(", " sv string bad)];
    t:{[who;c;t] .qetl.compression.check_triple[who," column ",string c;t]}[who]'[key cc;value cc];
    (key cc)!t}

/ Private: a policy dict checked key by key, returning (the scalars it supplied;
/ its column overrides, checked). A pair, because a dict cannot hold both a long
/ and a dict among its values once it has only longs.
/ Whatever depends on the stack default (a level against an algorithm it did
/ not state) waits for `resolve`.
/ @private
.qetl.compression.normalise:{[who;spec;known;cs]
    if[not 99h=type spec; 'who,": a compression policy is a dict of ",", " sv string .qetl.compression.keys];
    .qetl.declared.refuse_unknown[who;"a compression policy";spec;.qetl.compression.keys];
    checks:`algo`blocksize`level`minage`mode!(
        .qetl.compression.check_algo[who;];
        .qetl.compression.check_blocksize[who;];
        .qetl.compression.int_atom[who;"level";];
        .qetl.compression.check_minage[who;];
        .qetl.compression.check_mode[who;]);
    ks:key[checks] inter key spec;
    out:ks!{[f;v] f v}'[checks ks;spec ks];
    if[all `algo`level in key out; .qetl.compression.check_level_for[who;out`algo;out`level]];
    (out;$[`columns in key spec; .qetl.compression.check_columns[who;spec`columns;known;cs]; (`symbol$())!()])}

/ ----------------------------------------------------------- DECLARATION

/ Declare the stack default: what every table takes that does not say otherwise.
/ @param spec a dict with `algo and `minage always, and `blocksize and `level
/   unless algo is 0; `mode is optional and defaults to `age. No `columns: a column override belongs to a table
/ @return the resolved default
/ @throws error naming the key and the range it broke, or a second declaration
.qetl.compression.set_default:{[spec]
    who:"compression: the stack default";
    if[not ()~.qetl.compression.stack_default; 'who," is already declared"];
    if[99h=type spec; if[`columns in key spec; 'who," takes no columns - a column override belongs to a table"]];
    n:.qetl.compression.normalise[who;spec;0b;`symbol$()];
    s:n 0;
    need:`algo`minage,$[0=s[`algo]; `symbol$(); `blocksize`level];
    if[0=count key s; 'who," needs ",", " sv string need];
    if[count miss:need where not need in key s; 'who," needs ",", " sv string miss];
    d:`algo`blocksize`level`minage`mode!(s`algo;0j^.qetl.compression.supplied[s;`blocksize];0j^.qetl.compression.supplied[s;`level];s`minage;`age^.qetl.compression.supplied_mode[s]);
    if[0=d`algo; d[`blocksize`level]:0 0j];
    .qetl.compression.check_level_for[who;d`algo;d`level];
    `.qetl.compression.stack_default set d;
    d}

/ Declare a table's policy: what differs from the stack default.
/ @param tbl the table or dataset, as a symbol
/ @param spec a dict of any of `algo`blocksize`level`minage`columns
/ @return the table's name
/ @throws error naming the key and the range it broke, an unknown key, a column
/   the table lacks, a name no policy can include, or a second declaration
.qetl.compression.declare:{[tbl;spec]
    if[not -11h=type tbl; '"compression: a table name must be a symbol"];
    who:"compression: ",string tbl;
    if[tbl in .qetl.compression.excluded; 'who,": no policy can include ",string[tbl]," (sym, .d, par.txt and the coverage ledger)"];
    if[tbl in exec table from 0!.qetl.compression.table_policy; 'who," is already declared"];
    known:tbl in .qetl.plant.names[];
    cs:$[known; cols .qetl.plant.schema tbl; `symbol$()];
    n:.qetl.compression.normalise[who;spec;known;cs];
    s:n 0;
    if[not ()~d:.qetl.compression.stack_default;
        .qetl.compression.check_level_for[who;$[`algo in key s; s`algo; d`algo];$[`level in key s; s`level; d`level]]];
    `.qetl.compression.table_policy upsert (tbl;.qetl.compression.supplied[s;`algo];.qetl.compression.supplied[s;`blocksize];
        .qetl.compression.supplied[s;`level];.qetl.compression.supplied[s;`minage];.qetl.compression.supplied_mode[s]);
    c:n 1;
    if[count c;
        `.qetl.compression.column_policy upsert flip `table`column`algo`blocksize`level!(
            (count c)#tbl; key c; {x 0} each value c; {x 1} each value c; {x 2} each value c)];
    tbl}

/ ------------------------------------------------------------- RESOLUTION

/ Private: the stack default, or a refusal saying there is none.
/ @private
.qetl.compression.require_default:{[]
    if[()~.qetl.compression.stack_default;
        '"compression: no stack default is declared - src/etl/compression_policy.q declares it"];
    .qetl.compression.stack_default}

/ A table's whole policy: the stack default overlaid with what the table declared.
/ @param tbl the table or dataset, as a symbol
/ @return a dict of `algo`blocksize`level`minage`columns`mode
/ @throws error when there is no stack default, or the table's level does not
/   fit the algorithm it ends up with
.qetl.compression.resolve:{[tbl]
    d:.qetl.compression.require_default[];
    t:.qetl.compression.table_policy tbl;
    k:where not null t;
    r:d;
    r[k]:t k;
    who:"compression: ",string tbl;
    if[0=r`algo; r[`blocksize`level]:0 0j];
    .qetl.compression.check_level_for[who;r`algo;r`level];
    c:0!select from .qetl.compression.column_policy where table=tbl;
    c:c iasc c`column;
    cd:$[0=count c; (`symbol$())!(); (c`column)!flip (c`algo;c`blocksize;c`level)];
    `algo`blocksize`level`minage`columns`mode!(r`algo;r`blocksize;r`level;r`minage;cd;r`mode)}

/ The names a policy can be declared for: the plant's tables and the bounded
/ workers' datasets.
/ @return a symbol list
/ @eg `orders in .qetl.compression.known[]  ->  1b
.qetl.compression.known:{[] distinct .qetl.plant.names[],exec dataset from .qetl.job.bounded.worker_cfg}

/ The tables with a declaration of their own, sorted.
/ @return a symbol list
.qetl.compression.declared:{[] asc exec table from 0!.qetl.compression.table_policy}

/ The minage TorQ's age pass is given for a policy: its own, except for a table
/ whose mode is `write, which is held out of the age pass (see `never`).
/ @param r a resolved policy, from `resolve`
/ @return a long
/ @eg .qetl.compression.age_minage `minage`mode!(7j;`write)  ->  36500j
.qetl.compression.age_minage:{[r] $[`write=r`mode; .qetl.compression.never; r`minage]}

/ Private: one row of TorQ's file.
/ @private
.qetl.compression.row:{[t;m;c;a;b;l]
    enlist `table`minage`column`calgo`cblocksize`clevel!(t;m;c;a;b;l)}

/ The whole policy as TorQ's rows: `default first, then the coverage ledger
/ pinned at 0, then each declared table's default row followed by its column
/ rows, tables and columns in name order.
/ @return a table of table, minage, column, calgo, cblocksize, clevel
/ @throws error when there is no stack default, or a table is declared that is
/   neither a plant table nor a worker's dataset (a misspelt name would
/   otherwise compress nothing)
/ @eg cols .qetl.compression.rows[]  ->  `table`minage`column`calgo`cblocksize`clevel
.qetl.compression.rows:{[]
    d:.qetl.compression.require_default[];
    if[count bad:.qetl.compression.declared[] except .qetl.compression.known[];
        '"compression: ",(", " sv string bad)," ",$[1=count bad; "is"; "are"]," declared but neither a plant table nor a bounded worker's dataset"];
    head:.qetl.compression.row[`default;.qetl.compression.age_minage[d];`default;d`algo;d`blocksize;d`level];
    ledger:.qetl.compression.row[`etl_coverage;0j;`default;0j;0j;0j];
    ts:.qetl.compression.declared[];
    body:$[0=count ts; 0#head; raze {[t]
        r:.qetl.compression.resolve[t];
        m:.qetl.compression.age_minage[r];
        one:.qetl.compression.row[t;m;`default;r`algo;r`blocksize;r`level];
        cd:r`columns;
        $[0=count cd; one; one,raze {[t;m;c;v] .qetl.compression.row[t;m;c;v 0;v 1;v 2]}[t;m]'[key cd;value cd]]
        } each ts];
    head,ledger,body}

/ TorQ's compressionconfig.csv, line by line: its header, then `rows`.
/ @return a list of strings
/ @throws error when there is no stack default, or a table is not known
/ @eg first .qetl.compression.csv[]  ->  "table,minage,column,calgo,cblocksize,clevel"
.qetl.compression.csv:{[]
    r:.qetl.compression.rows[];
    (enlist "table,minage,column,calgo,cblocksize,clevel"),
        {"," sv (string x`table;string x`minage;string x`column;string x`calgo;string x`cblocksize;string x`clevel)} each r}

/ ------------------------------------------------------------- WRITE TIME (#2151)

/ Is a file compressed? What -21! says: it describes a compressed file and
/ returns an empty dictionary for any other.
/ @param f a file symbol
/ @return 1b when the file is compressed
/ @throws error when the file does not exist
/ @eg .qetl.compression.is_compressed `:src/init.q  ->  0b
.qetl.compression.is_compressed:{[f] 0<count -21!f}

/ Is any column of a table directory compressed? The age pass skips a file
/ already compressed, so the write path must recognise one too: a partition
/ that is already compressed is left in the state it is in.
/ @param dir a splayed table's directory, as a file symbol
/ @return 1b when at least one of its columns is compressed, 0b when none is or
/   the directory is not a splayed table
/ @eg .qetl.compression.partition_compressed `:/data/hdb/2026.01.01/trade/  ->  0b
.qetl.compression.partition_compressed:{[dir]
    base:-1_string dir;
    if[()~key hsym `$base,"/.d"; :0b];
    any .qetl.compression.is_compressed each hsym each `$(base,"/"),/:string get hsym `$base,"/.d"}

/ What a writer applies to a table at save: .z.zd's (blocksize;algo;level), or
/ the empty list when it applies nothing - the table's mode is `age, its
/ algorithm is 0, no stack default is declared (this process loads no policy),
/ or the partition at `dir` is already compressed. A partition is compressed once.
/ @param tbl the table, as a symbol
/ @param dir the table's directory in the partition, as a file symbol
/ @return a list of three longs, or the empty list
/ @eg .qetl.compression.for_write[`orders;`:/data/hdb/2026.01.01/orders/]  ->  ()
.qetl.compression.for_write:{[tbl;dir]
    if[()~.qetl.compression.stack_default; :()];
    r:.qetl.compression.resolve[tbl];
    if[(r`mode) in `age; :()];
    if[0=r`algo; :()];
    if[.qetl.compression.partition_compressed[dir]; :()];
    (r`blocksize;r`algo;r`level)}

/ The columns whose own compression differs from their table's, for a table a
/ writer compresses: column -> (blocksize;algo;level).
/ @param tbl the table, as a symbol
/ @return a dictionary of column to a triple of longs; empty when none differs
/ @eg count .qetl.compression.column_triples `orders  ->  0
.qetl.compression.column_triples:{[tbl]
    r:.qetl.compression.resolve[tbl];
    cd:r`columns;
    if[0=count cd; :(`symbol$())!()];
    trips:{(x 1;x 0;x 2)} each value cd;
    base:(r`blocksize;r`algo;r`level);
    k:where not trips~\:base;
    (key cd)[k]!trips k}

/ Run f . args with .z.zd set to `triple`, and put .z.zd back as it was
/ whatever happens: unset if it was unset, its old value otherwise. .z.zd is
/ process-wide, so a writer that left it set would compress everything the
/ process wrote afterwards, a table the policy never named. An empty triple
/ leaves .z.zd alone.
/ @param triple (blocksize;algo;level), or the empty list
/ @param f a function
/ @param args the argument list for f
/ @return what f returns
/ @throws what f throws, after .z.zd is restored
/ @eg .qetl.compression.with_zd[17 2 6;{[x] .z.zd};enlist 0]  ->  17 2 6
.qetl.compression.with_zd:{[triple;f;args]
    if[0=count triple; :.[f;args]];
    had:`zd in key `.z;
    old:$[had; .z.zd; ()];
    .z.zd:triple;
    r:.[{(1b;.[x;y])};(f;args);{(0b;x)}];
    $[had; [.z.zd:old]; system "x .z.zd"];
    if[not first r; 'last r];
    last r}

/ Rewrite one column file with its own compression, keeping the original unless the
/ result reads back equal. The same steps as .cmp's compress and cleancompressed
/ (lib/torq/code/common/compress.q), whose hash-file handling it follows.
/ @param f the column file, a file symbol
/ @param triple (blocksize;algo;level); algorithm 0 decompresses
/ @return the file
/ @throws error when the rewritten column does not read back equal, with the original kept
/ @eg .qetl.compression.recompress_column[`:/data/hdb/2026.01.01/trade/price;17 2 9]
.qetl.compression.recompress_column:{[f;triple]
    tmp:hsym `$(string f),"_kdbtempzip";
    -19!(f;tmp;triple 0;triple 1;triple 2);
    ok:((get tmp)~get f) and (attr get tmp)~attr get f;
    if[not ok; hdel tmp; '"compression: ",string[f]," did not read back equal after rewriting it; the original is kept"];
    system "mv ",(1_string tmp)," ",1_string f;
    {[tmp;f;suffix]
        h:hsym `$(string tmp),suffix;
        if[not ()~key h; system "mv ",(1_string h)," ",1_string hsym `$(string f),suffix]}[tmp;f] each ("#";"##");
    f}

/ Apply a table's column overrides to a partition just written under the table's
/ own compression (the columns .z.zd could not give their own).
/ @param tbl the table, as a symbol
/ @param dir the table's directory in the partition, as a file symbol
/ @return the columns rewritten
/ @eg .qetl.compression.apply_columns[`orders;`:/data/hdb/2026.01.01/orders/]  ->  `symbol$()
.qetl.compression.apply_columns:{[tbl;dir]
    ct:.qetl.compression.column_triples[tbl];
    if[0=count ct; :`symbol$()];
    base:-1_string dir;
    {[base;c;t] .qetl.compression.recompress_column[hsym `$(base,"/"),string c;t]}[base]'[key ct;value ct];
    key ct}
