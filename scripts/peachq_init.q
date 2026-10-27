/ scripts/peachq_init.q - what TorQ's processes call and PeachQ does not define.
/ .
/ A PeachQ runtime's processes start with QINIT naming this file
/ (uqs.stack.env.interpreter_env), so it loads before torq.q in every one of
/ them. It fills interpreter gaps only: each name is defined when PeachQ lacks
/ it and left alone where the interpreter has it, so on KDB-X it does nothing.
/ It never changes TorQ, which is vendored and not edited.
/ .
/ .Q.chk. TorQ's wdb.q calls it while it starts (.wdb.startup ->
/ initmissingtables -> filldb, lib/torq/code/processes/wdb.q). PeachQ has
/ none, so a PeachQ stack's wdb died loading (`failed to load .../wdb.q :
/ .Q.chk`), and with no wdb an attachment pinned no HDB directory and an
/ attached backfill had no writer to coordinate with (#1650).
/ This is KDB-X's .Q.chk, in q: every partition directory under `root` (named
/ like "[0-9]*", or under each directory par.txt lists) is given an empty copy
/ of each table some partition holds and it lacks, taken from the most recent
/ partition that holds it, its symbols enumerated against root's sym file.
/ Returns, per partition, the partition once for each table it wrote there.

if[10h=type @[value;`.Q.chk;{x}];
    .Q.chk:{[root]
        if[root~(::); '"missing dir argument"];
        parts:{[r] ` sv' r,'p where (string p:key r) like "[0-9]*"};
        dirs:$[`par.txt in key root; raze parts each hsym `$read0 ` sv root,`par.txt; parts root];
        if[0=count dirs; :()];
        dirs@:idesc {value string last ` vs x} each dirs;
        / A table is a directory with a .d; PeachQ's set leaves a sym FILE
        / beside a splayed table, which is not one.
        held:{[dir] t where {[dir;t] not ()~key ` sv dir,t,`.d}[dir] each t:key dir} each dirs;
        tabs:distinct raze held;
        / Each-both over empty lists is 'nyi on PeachQ.
        if[0=count tabs; :(count dirs)#enlist ()];
        / Column by column: PeachQ cannot `get` a splayed directory. Symbols
        / are decoded here and enumerated against root's sym file on write.
        empty:{[dir;t]
            base:string ` sv dir,t;
            c:get hsym `$base,"/.d";
            flip c!{[base;col] v:0#get hsym `$base,"/",string col; $[type[v] within 20 76h; value v; v]}[base] each c
            }'[dirs (flip tabs in/: held)?\:1b; tabs];
        {[root;tabs;empty;dir;have]
            miss:where not tabs in have;
            if[0=count miss; :()];
            {[root;dir;t;e] (` sv dir,t,`) set .Q.en[root] e; dir}[root;dir]'[tabs miss; empty miss]
            }[root;tabs;empty]'[dirs;held]}]
