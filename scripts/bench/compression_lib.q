/ compression_lib.q - what scripts/bench/compression.q measures, as functions (#2297).
/ .
/ The command compresses COPIES of one partition of one table, once per candidate setting,
/ into a temporary directory, and times four query shapes against each copy in a fresh q
/ process. This file holds everything that is not the command line: parsing the candidates,
/ writing a copy, counting its bytes, the query shapes, the parameters they share, and the
/ read-syscall counters. Loaded by the command and by its child processes, which load nothing
/ else, and by tests/q/test_bench_compression.q.
/ .
/ NOTHING HERE WRITES UNDER THE HDB. A copy is read from it (cp, or -19! with the HDB file as
/ the source), and every write goes to the destination the caller names; the command refuses a
/ destination inside the HDB (.qdev.cbench.inside).
/ .
/ THE SHAPES, over a partition loaded as an HDB (\l), each through a selector `sel` that
/ returns the partition's rows matching some extra where clauses - so a test can hand the
/ same shapes an in-memory table:
/   sweep   select the whole day, then read every column of every row
/   aj      aj[(sym;time); probes; select the whole day] - probes sampled from the day itself
/   range   select a contiguous block of rows (where i within ...), every column
/   point   one select per sampled row (where i=...), every column
/ Every shape reads every column it returns, so a column the query maps but never touches
/ cannot make a setting look free.

\d .

/ The algorithms -19! takes, by number. 3-5 need a shared library on the loader's path (on
/ macOS with Homebrew: DYLD_LIBRARY_PATH=/opt/homebrew/lib); a setting whose library is
/ missing is reported as unavailable, not as a failure.
/ @eg .qdev.cbench.algos 2  ->  `gzip
.qdev.cbench.algos:0 1 2 3 4 5!`none`qipc`gzip`snappy`lz4hc`zstd

/ The (lowest; highest) level each algorithm takes; 0 0 for one that takes none.
.qdev.cbench.levels:0 1 2 3 4 5!(0 0;0 0;0 9;0 0;0 16;-7 22)

/ The logical block sizes -19! takes, as powers of two. The platform's page size is the real
/ floor (2^14 on macOS arm64): -19! refuses below it, and the setting is reported unavailable.
.qdev.cbench.block_range:12 20

/ The query shapes, in the order they are reported.
.qdev.cbench.shape_names:`sweep`aj`range`point

/ What the command compares when -candidates is not given.
.qdev.cbench.default_candidates:"current,policy,none,qipc/17/0,gzip/14/6,gzip/17/6,gzip/20/6,snappy/17/0,zstd/17/3"

/ The syscalls counted as reads.
.qdev.cbench.read_calls:`read`pread`pread64`readv`preadv`preadv2`read_nocancel`pread_nocancel`readv_nocancel`preadv_nocancel

/ ------------------------------------------------------------ CANDIDATES

/ A setting's name: none, or algorithm/blocksize/level.
/ @param t an (algo;blocksize;level) triple
/ @return a symbol
/ @eg .qdev.cbench.label 2 17 6  ->  `gzip/17/6
.qdev.cbench.label:{[t] $[0=t 0; `none; `$"/" sv (string .qdev.cbench.algos t 0;string t 1;string t 2)]}

/ Private: an algorithm, by number or name.
/ @private
.qdev.cbench.algo_of:{[who;s]
    n:$[all s in .Q.n; "J"$s; .qdev.cbench.algos?`$s];
    if[not n in key .qdev.cbench.algos;
        'who,": algorithm ",s," is not one of 0-5 or ",", " sv string value .qdev.cbench.algos];
    n}

/ An (algo;blocksize;level) triple, checked against what -19! takes. Algorithm 0 is 0 0 0.
/ @param who the name used in an error
/ @param t three integers
/ @return three longs
/ @throws error naming the value out of range
/ @eg .qdev.cbench.check_triple["x";2 17 6]  ->  2 17 6
.qdev.cbench.check_triple:{[who;t]
    t:"j"$t;
    if[0=t 0; :0 0 0j];
    if[not (t 1) within .qdev.cbench.block_range;
        'who,": block size ",string[t 1]," is not 12-20 (a power of two: 17 is 128 KB)"];
    r:.qdev.cbench.levels t 0;
    if[not (t 2) within r;
        'who,": level ",string[t 2]," does not fit ",string[.qdev.cbench.algos t 0]," (",string[r 0],"-",string[r 1],")"];
    t}

/ Private: one candidate dict.
/ @private
.qdev.cbench.candidate:{[nm;kind;t;cs] `name`kind`triple`columns!(nm;kind;t;cs)}

/ Parse one candidate: current (the files as the HDB holds them), policy (this stack's
/ declared policy for the table, resolved by the command), none (uncompressed), or
/ algo/blocksize/level with the algorithm by number or name (gzip/17/6, 2/17/6). A setting
/ may be followed by column overrides, +column=setting each, to measure a per-column policy
/ before it is declared: gzip/16/6+time=none+sym=gzip/20/6.
/ @param s a string
/ @return dict of name, kind (`current`policy`triple), triple and columns (column -> triple)
/ @throws error naming what is wrong with it
/ @eg .qdev.cbench.parse_candidate["gzip/17/6"]`triple  ->  2 17 6
/ @eg key .qdev.cbench.parse_candidate["gzip/16/6+time=none"]`columns  ->  enlist `time
.qdev.cbench.parse_candidate:{[s]
    s:trim s;
    who:"bench: candidate ",s;
    none:(`symbol$())!();
    if[s~"current"; :.qdev.cbench.candidate[`current;`current;();none]];
    if[s~"policy"; :.qdev.cbench.candidate[`policy;`policy;();none]];
    p:"+" vs s;
    t:.qdev.cbench.parse_triple[who;first p];
    if[1=count p; :.qdev.cbench.candidate[.qdev.cbench.label[t];`triple;t;none]];
    kv:"=" vs/: 1_p;
    if[not all 2=count each kv; 'who,": a column override is +column=setting"];
    cn:`$kv[;0];
    if[any null cn; 'who,": a column override names no column"];
    if[not count[cn]=count distinct cn; 'who,": a column is overridden twice"];
    ct:{[who;x] .qdev.cbench.parse_triple[who;x]}[who] each kv[;1];
    nm:`$"+" sv (enlist string .qdev.cbench.label t),{[c;x] string[c],"=",string .qdev.cbench.label x}'[cn;ct];
    .qdev.cbench.candidate[nm;`triple;t;cn!ct]}

/ Private: one setting, none or algo/blocksize/level, as a triple.
/ @private
.qdev.cbench.parse_triple:{[who;s]
    if[s~"none"; :0 0 0j];
    p:"/" vs s;
    if[not 3=count p; 'who," is not current, policy, none or algo/blocksize/level"];
    if[not all {(count x) and all x in .Q.n,"-"} each 1_p; 'who,": block size and level must be integers"];
    .qdev.cbench.check_triple[who;(.qdev.cbench.algo_of[who;p 0];"J"$p 1;"J"$p 2)]}

/ Parse a comma-separated list of candidates.
/ @param s a string, such as "current,none,gzip/17/6"
/ @return a list of candidate dicts (parse_candidate's), in the order given
/ @throws error for an empty list, a candidate parse_candidate refuses, or one named twice
/ @eg (.qdev.cbench.parse_candidates "none,gzip/17/6")`name  ->  `none`gzip/17/6
.qdev.cbench.parse_candidates:{[s]
    ps:p where 0<count each p:trim each "," vs s;
    if[0=count ps; '"bench: no candidates"];
    c:.qdev.cbench.parse_candidate each ps;
    nm:{x`name} each c;
    if[count d:where 1<count each group nm; '"bench: candidate ",string[first d]," is named twice"];
    c}

/ The policy candidate, from a table's resolved policy (.qetl.compression.resolve's dict):
/ its triple, with the column overrides the partition has.
/ @param r a resolved policy: algo, blocksize, level, columns (column -> (algo;blocksize;level))
/ @param cs the partition's columns
/ @return a candidate dict, kind `triple, named policy
/ @eg (.qdev.cbench.from_policy[`algo`blocksize`level`columns!(2;16;6;(`symbol$())!());`a`b])`triple  ->  2 16 6
.qdev.cbench.from_policy:{[r;cs]
    t:.qdev.cbench.check_triple["bench: policy";(r`algo;r`blocksize;r`level)];
    oc:r`columns;
    k:(key oc) inter cs;
    o:$[count k; k!{.qdev.cbench.check_triple["bench: policy column";x]} each oc k; (`symbol$())!()];
    .qdev.cbench.candidate[`policy;`triple;t;o]}

/ The triple a candidate gives one column: its override, else its own.
/ @param cand a candidate dict
/ @param c a column name
/ @return three longs
/ @eg .qdev.cbench.column_triple[.qdev.cbench.parse_candidate "gzip/17/6";`px]  ->  2 17 6
.qdev.cbench.column_triple:{[cand;c] $[c in key cand`columns; cand[`columns] c; cand`triple]}

/ ------------------------------------------------------------ PATHS

/ A path as a plain string: no leading colon, no trailing slash.
/ @param f a file symbol, symbol or string
/ @return a string
/ @eg .qdev.cbench.path `:/data/hdb/  ->  "/data/hdb"
.qdev.cbench.path:{[f]
    s:$[10h=type f; f; string f];
    s:$[":"=first s; 1_s; s];
    $[(1<count s) and "/"=last s; -1_s; s]}

/ A string quoted for /bin/sh.
/ @param s a string
/ @return the string in single quotes, any single quote in it escaped
/ @eg .qdev.cbench.sh "a b"  ->  "'a b'"
.qdev.cbench.sh:{[s] "'",(ssr[(),s;"'";"'\\''"]),"'"}

/ Is a path the root, or under it? Both are compared as given: the caller makes them absolute.
/ @param p a path
/ @param root a path
/ @return a boolean
/ @eg .qdev.cbench.inside["/data/hdb/tmp";"/data/hdb"]  ->  1b
.qdev.cbench.inside:{[p;root]
    p:.qdev.cbench.path[p];
    r:.qdev.cbench.path[root];
    (p~r) or (r,"/")~(1+count r)#p}

/ A path made absolute against a directory, its . and .. segments resolved as text (symbolic
/ links are not followed: the path need not exist yet).
/ @param cwd an absolute directory
/ @param d a path
/ @return an absolute path, a string
/ @eg .qdev.cbench.absolute["/data/run";"../hdb/./x"]  ->  "/data/hdb/x"
.qdev.cbench.absolute:{[cwd;d]
    d:.qdev.cbench.path[d];
    full:$["/"=first d; d; cwd,"/",d];
    parts:("/" vs full) except ("";enlist ".");
    out:{[acc;s] $[s~".."; -1_acc; acc,enlist s]}/[();parts];
    "/","/" sv out}

/ The directories partitions live in: the root, or the segments par.txt lists.
/ @param root the HDB root, a file symbol
/ @return a list of file symbols
.qdev.cbench.segments:{[root]
    pt:` sv root,`par.txt;
    $[()~key pt; enlist root; hsym each `$read0 pt]}

/ The splayed directory of one table in one partition.
/ @param root the HDB root, a file symbol
/ @param part the partition, as a string (2026.10.05)
/ @param tbl the table, a symbol
/ @return a file symbol
/ @throws error when there is no HDB at root, or no such table in that partition
.qdev.cbench.find_partition:{[root;part;tbl]
    if[()~key root; '"bench: no HDB at ",.qdev.cbench.path[root]];
    ds:{[p;t;s] ` sv s,(`$p),t}[part;tbl] each .qdev.cbench.segments root;
    ok:ds where {[d] not ()~key ` sv d,`.d} each ds;
    if[0=count ok; '"bench: no table ",string[tbl]," in partition ",part," under ",.qdev.cbench.path[root]];
    first ok}

/ The files at the root holding a symbol list: the enumeration domains (sym), as \l loads them.
/ @param root the HDB root, a file symbol
/ @return a list of file symbols
.qdev.cbench.domains:{[root]
    fs:` sv/:root,/:k where not (k:key root) like "*.*";
    fs where 11h={@[{type get x};x;0h]} each fs}

/ ------------------------------------------------------------ BYTES

/ Private: a column file's hash files (col#, col##) that exist.
/ @private
.qdev.cbench.companions:{[f]
    c:`$string[f],/:("#";"##");
    c where not ()~/:key each c}

/ A file's bytes on disk: the compressed length when -21! describes it, else hcount.
/ (hcount of a compressed file is its uncompressed length.)
/ @param f a file symbol
/ @return a long
.qdev.cbench.disk_bytes:{[f] h:-21!f; $[count h; h`compressedLength; hcount f]}

/ A file's bytes uncompressed.
/ @param f a file symbol
/ @return a long
.qdev.cbench.logical_bytes:{[f] h:-21!f; $[count h; h`uncompressedLength; hcount f]}

/ Per column of a splayed directory, its files' bytes on disk and uncompressed, hash files
/ (the data of a nested column) included. `.d` is not counted.
/ @param dir the table's directory, a file symbol
/ @return table column, bytes, logical
.qdev.cbench.column_bytes:{[dir]
    d:.qdev.cbench.path[dir];
    cs:get hsym `$d,"/.d";
    fs:{[d;c] f:hsym `$d,"/",string c; f,.qdev.cbench.companions[f]}[d] each cs;
    ([] column:cs; bytes:{sum .qdev.cbench.disk_bytes each x} each fs; logical:{sum .qdev.cbench.logical_bytes each x} each fs)}

/ Uncompressed over on-disk bytes, two places; null for nothing on disk.
/ @param logical a long
/ @param bytes a long
/ @return a float
/ @eg .qdev.cbench.ratio[300;100]  ->  3f
.qdev.cbench.ratio:{[logical;bytes] $[0=bytes; 0n; 0.01*floor 0.5+100*logical%bytes]}

/ ------------------------------------------------------------ COPIES

/ Write one candidate's copy of a splayed directory. `current copies the files as they are;
/ any other compresses each column of .d from the source with -19! (which reads a compressed
/ source transparently, and writes a nested column's hash files with it), then checks it reads
/ back equal, attribute included. The source is only read.
/ @param src the table's directory in the HDB, a file symbol
/ @param dst the copy's directory, a file symbol; created
/ @param cand a candidate dict of kind `current or `triple
/ @return dst
/ @throws what -19! throws (a block size below the page size, a missing library), or a column
/   that does not read back equal
.qdev.cbench.write_copy:{[src;dst;cand]
    s:.qdev.cbench.path[src];
    d:.qdev.cbench.path[dst];
    system "mkdir -p ",.qdev.cbench.sh[d];
    if[`current=cand`kind; system "cp -R ",.qdev.cbench.sh[s,"/."]," ",.qdev.cbench.sh[d]; :dst];
    cs:get hsym `$s,"/.d";
    {[s;d;cand;c]
        a:hsym `$s,"/",string c;
        b:hsym `$d,"/",string c;
        t:.qdev.cbench.column_triple[cand;c];
        -19!(a;b;$[0=t 0; 17; t 1];t 0;t 2);
        va:get a; vb:get b;
        if[not (va~vb) and (attr va)~attr vb; '"bench: ",string[c]," did not read back equal after -19!"]}[s;d;cand] each cs;
    system "cp ",.qdev.cbench.sh[s,"/.d"]," ",.qdev.cbench.sh d,"/.d";
    dst}

/ ------------------------------------------------------------ SHAPES

/ The sym and time columns aj joins on: `sym (else the first symbol column) and `time (else
/ the first timestamp, timespan, datetime or time column); empty when either is missing.
/ @param m a table's meta
/ @return two symbols, or an empty symbol list
/ @eg .qdev.cbench.aj_cols meta ([] time:2#.z.p; sym:`a`b; px:1 2f)  ->  `sym`time
.qdev.cbench.aj_cols:{[m]
    m:0!m;
    syms:exec c from m where t="s";
    tims:exec c from m where t in "pnzt";
    if[(0=count syms) or 0=count tims; :`symbol$()];
    (first $[`sym in syms; `sym; syms];first $[`time in tims; `time; tims])}

/ What the shapes share, drawn once from the source partition so every setting answers the
/ same queries: the range's first row and length, the point rows, aj's columns and probes.
/ The draws are seeded, and q's seed is put back as it was.
/ @param t the partition's table (mapped, or in memory)
/ @param o dict: points, probes (how many of each), range (rows; null for 1% of the day), seed
/ @return dict n, lo, k, points, aj_cols, probes
/ @eg (.qdev.cbench.params[([] a:til 1000);`points`probes`range`seed!(5;5;0N;7)])`k  ->  10
.qdev.cbench.params:{[t;o]
    n:count t;
    k:$[null o`range; max 1,n div 100; max 1,n&o`range];
    k:k&n;
    lo:(n-k) div 2;
    old:system "S";
    system "S ",string o`seed;
    pts:$[n=0; `long$(); neg[n&o`points]?n];
    pix:$[n=0; `long$(); asc neg[n&o`probes]?n];
    system "S ",string old;
    ac:.qdev.cbench.aj_cols[meta t];
    pr:$[0=count ac; ();
        [cv:{[t;i;c] v:t[c] i; $[20h<=type v; value v; v]}[t;pix] each ac;
         ac[1] xasc flip ac!cv]];
    `n`lo`k`points`aj_cols`probes!(n;lo;k;asc pts;ac;pr)}

/ Read every column of a table at some rows: what makes a mapped column's bytes be read.
/ @param r a table
/ @param ix row indices
/ @return the columns at those rows, a list
/ @eg count first .qdev.cbench.read_rows[([] a:til 10; b:10#`x);2 3]  ->  2
.qdev.cbench.read_rows:{[r;ix] {[r;ix;c] r[c] ix}[r;ix] each cols r}

/ Private: rows touched, read through every column.
/ @private
.qdev.cbench.touch:{[r] count first .qdev.cbench.read_rows[r;til count r]}

/ The whole day: every column of every row.
/ @param sel the partition's selector: where clauses -> rows
/ @param p the shared parameters
/ @return rows read
/ @throws error when the copy holds another number of rows than the source
.qdev.cbench.shape.sweep:{[sel;p]
    r:sel ();
    if[not (p`n)=count r; '"bench: the copy has ",string[count r]," rows, the source ",string p`n];
    .qdev.cbench.touch[r]}

/ aj of the probes against the whole day, as a query against mapped partitions makes it.
/ @param sel the partition's selector
/ @param p the shared parameters
/ @return rows joined; null when the table has no sym and time columns to join on
.qdev.cbench.shape.aj:{[sel;p]
    if[0=count p`aj_cols; :0Nj];
    r:aj[p`aj_cols;p`probes;sel ()];
    .qdev.cbench.touch[r]}

/ A contiguous block of rows, every column.
/ @param sel the partition's selector
/ @param p the shared parameters
/ @return rows read
.qdev.cbench.shape.range:{[sel;p]
    hi:(p`lo)+(p`k)-1;
    .qdev.cbench.touch[sel enlist (within;`i;(p`lo),hi)]}

/ One query per point row, every column.
/ @param sel the partition's selector
/ @param p the shared parameters
/ @return rows read
.qdev.cbench.shape.point:{[sel;p] sum {[sel;i] .qdev.cbench.touch sel enlist (=;`i;i)}[sel] each p`points}

/ Run one shape by name; `none runs nothing (the baseline a counter subtracts).
/ @param s a shape name, or `none
/ @param sel the partition's selector
/ @param p the shared parameters
/ @return rows read
.qdev.cbench.run_shape:{[s;sel;p] $[`none=s; 0j; .qdev.cbench.shape[s][sel;p]]}

/ A selector over an in-memory table (what the tests give the shapes).
/ @param t a table
/ @return a function of where clauses
/ @eg count .qdev.cbench.table_selector[([] a:til 5)] enlist (=;`i;2)  ->  1
.qdev.cbench.table_selector:{[t] {[t;w] ?[t;w;0b;()]}[t]}

/ A selector over one partition: in the child, the table of the HDB it loaded (\l), with
/ .Q.pf and the one partition in .Q.pv.
/ @param tbl the table: a symbol, or a table holding the partition column
/ @param pf the partition column (.Q.pf: date, month, int)
/ @param pv the partition's value
/ @return a function of where clauses
/ @eg count .qdev.cbench.hdb_selector[([] date:2026.01.01 2026.01.02; a:1 2);`date;2026.01.02] ()  ->  1
.qdev.cbench.hdb_selector:{[tbl;pf;pv] {[tbl;pf;pv;w] ?[tbl;(enlist (=;pf;pv)),w;0b;()]}[tbl;pf;pv]}

/ ------------------------------------------------------------ COUNTING READS

/ Which tracer counts a child's read syscalls: strace on Linux; dtruss on macOS when running
/ as root (it, and fs_usage, need root and SIP's DTrace restrictions lifted); else none, and
/ only elapsed time is reported.
/ @param os .z.o
/ @param uid the effective user id
/ @param have dict strace, dtruss -> whether each is on the PATH
/ @return `strace, `dtruss or `none
/ @eg .qdev.cbench.counter[`m64;501;`strace`dtruss!01b]  ->  `none
.qdev.cbench.counter:{[os;uid;have]
    $[(string[os] like "l*") and have`strace; `strace;
      (string[os] like "m*") and (uid=0) and have`dtruss; `dtruss;
      `none]}

/ Is a program on a PATH? Read from the directories, not asked of the shell.
/ @param path the PATH, a string
/ @param prog the program, a symbol
/ @return a boolean
/ @eg .qdev.cbench.on_path["/nonexistent";`strace]  ->  0b
.qdev.cbench.on_path:{[path;prog]
    ds:d where 0<count each d:":" vs path;
    any {[p;d] not ()~key hsym `$d,"/",string p}[prog] each ds}

/ A program's path: as given when it names a directory, else the first match on the PATH.
/ It matters for q itself: system "q ..." is q's own \q, which would exit this process.
/ @param path the PATH, a string
/ @param prog the program, a string
/ @return a string; prog unchanged when it is on no directory of the PATH
/ @eg .qdev.cbench.resolve["/nonexistent";"/opt/q/q"]  ->  "/opt/q/q"
.qdev.cbench.resolve:{[path;prog]
    prog:(),prog;
    if["/" in prog; :prog];
    ds:d where 0<count each d:":" vs path;
    hit:ds where {[p;d] not ()~key hsym `$d,"/",p}[prog] each ds;
    $[count hit; (first hit),"/",prog; prog]}

/ A shell command that runs `core` with the environment prefix, under the tracer.
/ @param env the environment assignments, a string ("" for none)
/ @param counter `strace, `dtruss or `none
/ @param out where the tracer writes its summary, a string
/ @param core the child's command line, a string
/ @return a string
/ @eg .qdev.cbench.command["";`none;"";"q x.q"]  ->  "q x.q"
.qdev.cbench.command:{[env;counter;out;core]
    env,$[`strace=counter; "strace -f -c -e trace=read,pread64,readv,preadv,preadv2 -o ",.qdev.cbench.sh[out]," ",core;
          `dtruss=counter; "dtruss -c -f ",core," 2> ",.qdev.cbench.sh[out];
          core]}

/ The read syscalls in strace -c's summary; null when the file holds no summary (strace
/ could not attach: ptrace denied in a container, say).
/ @param lines the summary's lines
/ @return a long
.qdev.cbench.parse_strace:{[lines]
    if[not any {0<count x ss "% time"} each lines; :0Nj];
    w:{x where 0<count each x} each " " vs/: lines;
    w:w where 5<=count each w;
    k:where (`$last each w) in .qdev.cbench.read_calls;
    sum "J"$w[k;3]}

/ The read syscalls in dtruss -c's summary (CALL COUNT); null when there is none.
/ @param lines dtruss's stderr
/ @return a long
.qdev.cbench.parse_dtruss:{[lines]
    h:where {(x like "CALL*") and 0<count x ss "COUNT"} each lines;
    if[0=count h; :0Nj];
    w:{x where 0<count each x} each " " vs/: (1+last h)_lines;
    w:w where 2=count each w;
    k:where (`$first each w) in .qdev.cbench.read_calls;
    sum "J"$last each w k}

/ The read syscalls a tracer's output holds.
/ @param counter `strace or `dtruss
/ @param lines its output
/ @return a long, or null
.qdev.cbench.parse_reads:{[counter;lines] $[`strace=counter; .qdev.cbench.parse_strace[lines]; .qdev.cbench.parse_dtruss[lines]]}

/ The environment a child needs passed explicitly: the loader paths snappy, lz4 and zstd are
/ found on. macOS strips DYLD_* from anything /bin/sh starts, so they are re-set on the line.
/ @param vars dict name -> value, empty values skipped
/ @return a string of NAME='value' assignments
/ @eg .qdev.cbench.env_prefix `A`B!("/lib";"")  ->  "A='/lib' "
.qdev.cbench.env_prefix:{[vars]
    k:where 0<count each vars;
    raze {[n;v] string[n],"=",.qdev.cbench.sh[v]," "}'[k;vars k]}
