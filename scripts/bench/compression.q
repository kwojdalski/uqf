// compression.q - compression settings measured on this HDB's own data (#2297).
//
//   q scripts/bench/compression.q -hdb output/uqs/hdb -table quote -partition 2026.10.07
//       [-candidates current,policy,none,gzip/17/6,...] [-reps 3] [-points 100] [-probes 1000]
//       [-range <rows>] [-seed 42] [-tmp DIR] [-keep] [-qbin q]
//
// Run from the repository root. It prints one table: per setting and query shape, the
// partition's bytes on disk, its ratio (uncompressed over on-disk), the fastest elapsed time
// of -reps runs, and the read syscalls of one run where a tracer can count them; then each
// column's ratio per setting.
//
// IT NEVER WRITES TO THE HDB. Each setting is a copy of the one partition in a temporary
// directory (-tmp, else mktemp -d; refused when inside the HDB), written by
// compression_lib.q's write_copy with -19! from the HDB's file as the source and checked
// to read back equal; the HDB's sym file is copied beside it. The directory is removed at the
// end unless -keep.
//
// CANDIDATES. current: the files as the HDB holds them. policy: what this stack's declared
// policy gives the table (.qetl.compression.resolve, src/etl/compression_policy.q, column
// overrides included). none: uncompressed. algo/blocksize/level, the algorithm by number or
// name: gzip/17/6, 2/17/6, zstd/17/3; any of these but current and policy may be followed by
// column overrides, +column=setting each, to measure a per-column policy before declaring it:
// gzip/16/6+time=none+sym=gzip/20/6. A setting -19! refuses here - a block size below the
// page size (2^14 on macOS arm64), snappy/lz4/zstd without its library on the loader's path -
// is listed as unavailable and the rest still run. On macOS with Homebrew,
// DYLD_LIBRARY_PATH=/opt/homebrew/lib gives q all three; it is passed on to every child.
//
// THE SHAPES (compression_lib.q): sweep (every column of the whole day), aj (-probes
// rows sampled from the day, aj'd against the mapped day), range (a contiguous block of
// -range rows, 1% of the day by default, every column), point (-points single-row selects,
// every column). Each run is a fresh q process that loads the copy with \l and times only the
// query, so one setting's decompressed blocks are not another's cache.
//
// READS. strace -c on Linux; dtruss -c on macOS only when run as root (it and fs_usage need
// root). Otherwise the column is empty and the header says so. A count is one traced run
// minus a traced run of the same process that queries nothing. It counts read syscalls,
// which is how q reads a compressed file; an uncompressed file is mapped, and its pages
// fault in without a syscall, so `none` and `current`'s uncompressed columns read low.
//
// WHAT IT DOES NOT. Empty the OS page cache: the copies were just written, so every run is
// warm, and times are decompression and mapping cost rather than disk latency. Numbers from
// one machine compare rows of one run; they are not figures to quote.

opts:.Q.opt .z.x;
here:{[] f:string .z.f; d:$["/" in f; (last where f="/")#f; "."]; $["/"=first d; d; (first system "pwd"),"/",d]}[];
system "l ",here,"/compression_lib.q";

/ ------------------------------------------------------------ CHILD
/ One timed query: -child -root R -table T -params P -shape S. Prints result|ms|rows.

if[`child in key opts;
    r:@[{[o]
        system "l ",first o`root;
        sel:.qdev.cbench.hdb_selector[`$first o`table;.Q.pf;first .Q.pv];
        p:get hsym `$first o`params;
        s:`$first o`shape;
        t0:.z.p;
        n:.qdev.cbench.run_shape[s;sel;p];
        ms:1e-6*"j"$.z.p-t0;
        "result|",string[ms],"|",string n};opts;{[e] "error|",e}];
    -1 r;
    exit 0];

/ ------------------------------------------------------------ COMMAND

fail:{[why] -2 "bench_compression: ",why; exit 1};
arg:{[k;d] $[k in key opts; first opts k; d]};
if[count miss:`hdb`table`partition except key opts;
    fail "missing ",(", " sv "-",/:string miss),"; usage: q scripts/bench/compression.q -hdb DIR -table T -partition P"];

abs_dir:{[d] first system "(cd ",(.qdev.cbench.sh d)," && pwd -P)"};
hdb_text:@[abs_dir;arg[`hdb;""];{[e] fail "no HDB directory at ",arg[`hdb;""]}];
hdb:hsym `$hdb_text;
tbl:`$arg[`table;""];
part:arg[`partition;""];
src:@[.qdev.cbench.find_partition[hdb;part];tbl;fail];
cands:@[.qdev.cbench.parse_candidates;arg[`candidates;.qdev.cbench.default_candidates];fail];
num:{[k;d] v:arg[k;""]; $[count v; "J"$v; d]};
reps:1|num[`reps;3];
o:`points`probes`range`seed!(num[`points;100];num[`probes;1000];num[`range;0Nj];num[`seed;42]);
qbin:.qdev.cbench.resolve[getenv `PATH;arg[`qbin;first .z.X]];

/ The scratch directory is checked before it is made: as text against the HDB as named and as
/ found, then again once it exists, through any symbolic link.
cwd:first system "pwd";
segs:.qdev.cbench.path each .qdev.cbench.segments hdb;
guarded:distinct (hdb_text;.qdev.cbench.absolute[cwd;arg[`hdb;""]]),segs;
refuse_inside:{[d] if[any .qdev.cbench.inside[d] each guarded; fail "-tmp ",d," is inside the HDB; the benchmark writes only outside it"]};
tmp:$[`tmp in key opts;
    [refuse_inside .qdev.cbench.absolute[cwd;first opts`tmp]; system "mkdir -p ",.qdev.cbench.sh first opts`tmp; abs_dir first opts`tmp];
    first system "mktemp -d"];
refuse_inside tmp;

/ The policy candidate: this stack's declaration, resolved for the table.
cols_src:get hsym `$(.qdev.cbench.path src),"/.d";
{[cs;c] if[count bad:(key c`columns) except cs; fail "bench: candidate ",string[c`name],": the table has no column ",", " sv string bad]}[cols_src] each cands;
unavailable:();
resolve_policy:{[t]
    system "l src/init.q";
    system "l src/etl/init.q";
    .qetl.compression.resolve t};
if[`policy in {x`name} each cands;
    pol:@[{(1b;resolve_policy x)};tbl;{(0b;x)}];
    i:first where `policy={x`name} each cands;
    $[pol 0;
        cands[i]:.qdev.cbench.from_policy[pol 1;cols_src];
        [unavailable,:enlist (`policy;"cannot load the policy (src/init.q, src/etl/init.q): ",pol 1); cands:cands _ i]]];

/ The copies: one root per setting, holding the HDB's symbol domains and the partition.
doms:.qdev.cbench.domains hdb;
{[f] (last ` vs f) set get f} each doms;
roots:{[i] tmp,"/s",string i} each til count cands;
copy_one:{[root;cand]
    system "mkdir -p ",.qdev.cbench.sh root;
    {[root;f] system "cp ",(.qdev.cbench.sh .qdev.cbench.path f)," ",.qdev.cbench.sh root,"/"}[root] each doms;
    .qdev.cbench.write_copy[src;hsym `$root,"/",part,"/",string tbl;cand]};
made:{[root;cand] @[{[a] copy_one . a; (1b;"")};(root;cand);{(0b;x)}]}'[roots;cands];
unavailable,:{[c;m] (c`name;m 1)}'[cands;made] where not {x 0} each made;
ok:where {x 0} each made;
cands:cands ok;
roots:roots ok;
if[0=count cands; fail "no candidate could be written: ","; " sv {string[x 0],": ",x 1} each unavailable];

/ What every setting is asked: drawn once, from the source partition.
params_file:tmp,"/params";
(hsym `$params_file) set .qdev.cbench.params[get ` sv src,`;o];
p:get hsym `$params_file;

/ Bytes per setting and column.
/ The ratio's numerator is the source's uncompressed bytes for every setting: a nested
/ column's data file is padded differently by each writer, so a copy's own count would differ.
cb:{[root] .qdev.cbench.column_bytes hsym `$root,"/",part,"/",string tbl} each roots;
logical_src:.qdev.cbench.column_bytes src;

/ The counter, and the child's command line.
have:.qdev.cbench.on_path[getenv `PATH] each `strace`dtruss!`strace`dtruss;
counter:.qdev.cbench.counter[.z.o;"J"$first system "id -u";have];
env:.qdev.cbench.env_prefix `DYLD_LIBRARY_PATH`LD_LIBRARY_PATH!getenv each `DYLD_LIBRARY_PATH`LD_LIBRARY_PATH;
script:here,"/compression.q";
core:{[root;s] qbin," ",(.qdev.cbench.sh script)," -child -root ",(.qdev.cbench.sh root)," -table ",string[tbl]," -params ",(.qdev.cbench.sh params_file)," -shape ",string[s]," -q"};

/ One run: (ms; rows; reads), each null where it failed or was not counted.
run_child:{[root;s;traced]
    out:tmp,"/trace.txt";
    cmd:.qdev.cbench.command[env;$[traced; counter; `none];out;core[root;s]];
    lines:@[system;cmd;{[e] enlist "error|",e}];
    r:lines where {x like "result|*"} each lines;
    if[0=count r; :(0n;0Nj;0Nj;"|" sv lines)];
    f:"|" vs first r;
    reads:$[traced; .qdev.cbench.parse_reads[counter;@[read0;hsym `$out;{()}]]; 0Nj];
    ("F"$f 1;"J"$f 2;reads;"")};

shapes:.qdev.cbench.shape_names;
rows:raze {[root;cand]
    / One untimed sweep first, so the setting reported first is not the one that warmed the cache.
    run_child[root;`sweep;0b];
    base:$[`none=counter; 0Nj; (run_child[root;`none;1b]) 2];
    {[root;cand;base;s]
        rs:{[a] run_child . a} each reps#enlist (root;s;0b);
        ms:min {x 0} each rs;
        err:first {x 3} each rs where 0<count each {x 3} each rs;
        if[count err; `unavailable set unavailable,enlist (`$string[cand`name],":",string s;err)];
        reads:$[`none=counter; 0Nj; 0|(run_child[root;s;1b] 2)-base];
        `setting`shape`ms`reads!(cand`name;s;ms;reads)}[root;cand;base] each shapes}'[roots;cands];
sizes:([] setting:{x`name} each cands; bytes:{sum x`bytes} each cb);
sizes:update ratio:.qdev.cbench.ratio[sum logical_src`logical] each bytes from sizes;
res:select setting, shape, bytes, ratio, ms:0.001*floor 0.5+1000*ms, reads from (([] setting:{x`setting} each rows; shape:{x`shape} each rows; ms:{x`ms} each rows; reads:{x`reads} each rows) lj `setting xkey select setting, bytes, ratio from sizes);

/ ------------------------------------------------------------ REPORT

/ What `current` and `policy` are, in the settings' own names.
settings_of:{[dir;cs] {[dir;c] h:-21!` sv dir,c; $[count h; .qdev.cbench.label "j"$h`algorithm`logicalBlockSize`zipLevel; `none]}[dir] each cs};
current_text:{[cs] g:count each group settings_of[src;cs]; n:count cs; ", " sv {[n;k;m] string[k]," (",string[m],"/",string[n]," columns)"}[n]'[key g;value g]}[cols_src];
policy_text:$[`policy in {x`name} each cands;
    {[c] string[.qdev.cbench.label c`triple],$[count c`columns; ", with ",(", " sv {[k;t] string[k]," ",string .qdev.cbench.label t}'[key c`columns;value c`columns]); ""]} first cands where `policy={x`name} each cands;
    ""];

\c 200 250
-1 "table ",string[tbl],", partition ",part,": ",string[p`n]," rows, ",string[count cols_src]," columns, ",string[sum logical_src`logical]," bytes uncompressed";
-1 "source ",(.qdev.cbench.path src)," (read only); copies in ",tmp,$[`keep in key opts; " (kept)"; " (removed at the end)"];
-1 "shapes: sweep = every column of all ",string[p`n]," rows; aj = aj[",(raze "`",/:string p`aj_cols),"] of ",string[count p`probes]," probes against the mapped day",
    $[0=count p`aj_cols; " (n/a: no sym and time columns)"; ""],"; range = ",string[p`k]," contiguous rows; point = ",string[count p`points]," single-row selects";
-1 "current = as the HDB holds it: ",current_text,$[`policy in sizes`setting; "; policy = ",policy_text; ""];
-1 "ms: fastest of ",string[reps]," fresh processes, warm page cache; reads: ",
    $[`none=counter; "not counted (strace on Linux, or dtruss as root on macOS, is needed)"; "read syscalls by ",string[counter],", one run less an idle run"];
if[any 3<{x[`triple] 0} each cands where `triple={x`kind} each cands;
    -1 "lz4hc and zstd are KDB-X's: TorQ's .cmp, which applies the policy, takes algorithms 0-3"];
-1 "";
show res;
-1 "";
-1 "ratio per column (uncompressed bytes / bytes on disk):";
pc:flip (`column`logical,{x`name} each cands)!(logical_src`column;logical_src`logical),{[t] .qdev.cbench.ratio'[logical_src`logical;t`bytes]} each cb;
show pc;
if[count unavailable;
    -1 "";
    -1 "unavailable:";
    {[u] -1 "  ",string[u 0],": ",u 1} each unavailable];
if[not `keep in key opts; system "rm -rf ",.qdev.cbench.sh tmp];
exit 0
