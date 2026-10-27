// test_peachq_init.q - scripts/peachq_init.q, what a PeachQ runtime's processes
// load before torq.q (QINIT, uqs.stack.env.interpreter_env) (.qt.qproc.peachq_init).
//
// It defines .Q.chk where the interpreter has none, which TorQ's wdb calls as
// it starts (#1650). This runner is KDB-X, which has its own: so the file must
// leave that one alone, and the definition it would make on PeachQ - obtained
// here by hiding the native one while the file loads - must fill a database
// exactly as the native one does.


/ A scratch directory, as a string.
.qt.qproc.peachq_init.scratch:{[] first system "mktemp -d"}

/ The definition the file makes where .Q.chk is missing. The native one is
/ hidden while the file loads and put back whatever happens.
/ @return the function the file defined
.qt.qproc.peachq_init.polyfill:{[]
    native:.Q.chk;
    .Q.chk:"absent";
    ok:@[{system "l ",x; 1b};"scripts/peachq_init.q";{[e] 0b}];
    made:.Q.chk;
    .Q.chk:native;
    if[not ok; '"peachq_init.q did not load"];
    made}

/ A partitioned database three days deep: the newest day lacks `ref` (an
/ enumerated table), the middle day lacks `tick`, the oldest holds both.
/ @param root the directory, as a string
/ @return the root as a file symbol
.qt.qproc.peachq_init.db:{[root]
    r:hsym `$root;
    .Q.dd[r;2026.01.01,`tick`] set .Q.en[r] ([] sym:`a`b; px:1 2f; note:("x";"yz"));
    .Q.dd[r;2026.01.01,`ref`] set .Q.en[r] ([] sym:`c`d; n:1 2i);
    .Q.dd[r;2026.01.02,`ref`] set .Q.en[r] ([] sym:1#`e; n:1#3i);
    .Q.dd[r;2026.01.03,`tick`] set .Q.en[r] ([] sym:1#`f; px:1#3f; note:enlist "w");
    r}

/ What a database holds after .Q.chk: per partition, its tables, and each
/ table's meta and row count.
.qt.qproc.peachq_init.shape:{[r]
    parts:.Q.dd[r] each 2026.01.01 2026.01.02 2026.01.03;
    {[p] {[p;t] (t;0!meta get ` sv p,t,`;count get ` sv p,t,`)}[p] each key p} each parts}

.qt.qproc.peachq_init.test_it_leaves_an_interpreter_s_own_chk_alone:{[t]
    native:.Q.chk;
    system "l scripts/peachq_init.q";
    .qunit.assertEquals[.Q.chk;native;"KDB-X's .Q.chk is kept"]}

.qt.qproc.peachq_init.test_it_fills_a_database_as_the_native_chk_does:{[t]
    filled:.qt.qproc.peachq_init.polyfill[];
    native:.qt.qproc.peachq_init.db .qt.qproc.peachq_init.scratch[];
    ours:.qt.qproc.peachq_init.db .qt.qproc.peachq_init.scratch[];
    / KDB-X names the partition once per table it gave it.
    names:{[w] {[ps] {[p] last ` vs p} each ps} each w};
    theirs:names .Q.chk native;
    .qunit.assertEquals[theirs;(enlist `2026.01.03;enlist `2026.01.02;());
        "newest day first: the newest gains ref, the middle tick, the oldest nothing"];
    .qunit.assertEquals[names filled ours;theirs;"the same partitions written"];
    .qunit.assertEquals[.qt.qproc.peachq_init.shape ours;.qt.qproc.peachq_init.shape native;
        "the same tables, empty, with the same columns and types"]}

.qt.qproc.peachq_init.test_it_writes_nothing_where_nothing_is_missing:{[t]
    filled:.qt.qproc.peachq_init.polyfill[];
    r:hsym `$.qt.qproc.peachq_init.scratch[];
    .Q.dd[r;2026.01.01,`tick`] set ([] px:1 2f);
    .Q.dd[r;2026.01.02,`tick`] set ([] px:1#3f);
    .qunit.assertEquals[filled r;(();());"no partition lacks a table"];
    .qunit.assertEquals[filled hsym `$.qt.qproc.peachq_init.scratch[];();"no partitions at all"]}
