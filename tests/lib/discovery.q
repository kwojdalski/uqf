// discovery.q - which namespaces are test suites, and the layout rules that
// keep a renamed suite from silently ceasing to run (.qt.support).
//
// Loaded by tests/lib/support.q, which every runner loads, so a runner needs
// no second \l line for it.
//
// THE CONVENTION. A suite is a namespace under .qt that mirrors the production
// owner it tests, with a descriptive suite name where an owner needs several:
//   .qt.qetl.coverage              tests .qetl.coverage
//   .qt.qpipe.job.fx_markout       tests .qpipe.job.fx_markout
//   .qt.qtorq.pipeline             one of several suites for .qtorq
// Two subtrees are reserved and never run: .qt.support (shared helpers, this
// file among them) and .qt.fixture.* (doubles and fixture workers). Everything
// else under .qt is either a SUITE or a CONTAINER.
//
// HOW A SUITE IS RECOGNISED - by what it holds, not by how it is spelled. A
// namespace is a suite when it directly holds at least one function named
// test*. It used to be a name ending in `test`, listed by hand in the runner,
// and either rule fails the same way: a suite that does not match is loaded,
// runs nothing and leaves the run green. So discovery is built to be loud:
//   - a namespace under .qt that holds functions but no test* is refused,
//     because it is a helper outside the two reserved subtrees;
//   - a test* member that is not a one-argument function is refused, because
//     qUnit would report it as an error row with nothing in it;
//   - a path the files define both as a namespace container and as a value is
//     refused (below), because q does not: defining the value silently replaces
//     the container and every test in it.
// A container holds only namespaces and is skipped. Order is depth first,
// parents before children, siblings alphabetical - a property of the names,
// not of the order the files happened to load in.
//
// DEFINITIONS. A suite file defines everything from the root context with a
// fully qualified name:
//   .qt.qetl.io.batch:{[] ...}
//   .qt.qetl.io.test_memory_creates_the_table:{[t] ...}
// It never uses `\d` or system "d ..." to enter the suite's namespace. q 4.0
// rejects a nested working context (\d .a.b), and a leading dot is ABSOLUTE:
// inside `\d .qt` a definition `.qf.x:1` would be .qf.x, not .qt.qf.x. Writing
// the whole path is the one spelling that means the same on every version.
// The price is that a suite refers to its own helpers by full path too: a
// function defined from the root resolves bare names in the root.
//
// OUTSIDE .qt. A root-level namespace that holds test* functions is a suite
// declared the old way (<thing>test, usually inside a nested `\d`) and is
// refused by layout_problems rather than run: it would otherwise run, or not,
// depending on how it was spelled (#1412).

// Is this value a namespace, as `\d` creates them - a dictionary whose keys
// include the empty symbol? The same rule .qf.util.is_namespace states, written
// out here so the harness does not stand on the library whose suites it runs.
// The key type is checked first: a dictionary keyed by chars would throw 'type
// from the lookup of ` in it.
// @param v any value
// @return 1b for a namespace
.qt.support.is_namespace:{[v] $[not 99h=type v; 0b; not 11h=type key v; 0b; ` in key v]}

// The library's namespaces among some, without the test harness: .q, the
// vendored .qunit, and .qt with everything beneath it. `.qf.util.owned` lists every
// root namespace that starts with q, and .qt does, so a tool that walks the
// library - coverage, the documentation ratchet - must drop it by name.
// @param nss namespace symbols
// @return the same, without the harness
.qt.support.library_only:{[nss]
    nss where not (nss in `.q`.qunit`.qt) or (string each nss) like ".qt.*"}

// The namespaces directly under one, fully qualified and alphabetical.
// @param ns a namespace symbol, e.g. `.qt
// @return the child namespaces, `.qt.qetl for `.qt, or an empty list when ns is none
.qt.support.children:{[ns]
    d:@[value;ns;{[e] (::)}];
    if[not .qt.support.is_namespace d; :`symbol$()];
    ks:asc (key d) except `;
    ks:ks where {[ns;k] .qt.support.is_namespace @[value;` sv ns,k;{[e] (::)}]}[ns] each ks;
    ` sv/: ns,/:ks}

// The suite namespace that mirrors a production job, `.qt.qpipe.job.<job>`:
// the one place that spelling is written. A job's own suite is where its
// contract_driver lives, so test_job_output_contracts finds it here, and
// `uqs job new` (python/uqs scaffold/jobs.py test_namespace) generates the same
// name - tests/q/test_scaffold... pins that the two agree.
// @param job the job's name, as in .qpipe.job.<job>
// @return the namespace symbol
.qt.support.job_suite:{[job] ` sv `.qt.qpipe.job,job}

// The two subtrees that hold helpers and doubles, never suites.
.qt.support.reserved:`.qt.support`.qt.fixture

// A namespace and everything beneath it, parents first, siblings alphabetical,
// the reserved subtrees left out.
// @param ns a namespace symbol
// @return the namespaces, ns first
.qt.support.walk:{[ns]
    kids:.qt.support.children[ns] except .qt.support.reserved;
    ns,raze .qt.support.walk each kids}

// The functions a namespace directly holds whose name starts test.
// @param ns a namespace symbol
// @return their bare names, sorted
.qt.support.tests_in:{[ns]
    fl:system "f ",string ns;
    fl where fl like "test*"}

// Refuse a test* member qUnit cannot run: it must be a lambda taking one
// argument, which is what runTest checks before it will start one.
// @param ns a namespace symbol
// @throws a message naming the first member that is not one-argument
.qt.support.require_runnable:{[ns]
    bad:.qt.support.tests_in[ns] where not {[ns;t]
        v:@[value;` sv ns,t;{[e] (::)}];
        $[100h=type v; 1=count (value v) 1; 0b]}[ns] each .qt.support.tests_in[ns];
    if[count bad;
        '(string ns),".",(string first bad)," is not a one-argument function - qUnit cannot run it"];
    }

// The suites beneath a root namespace, in the order they run.
// @param root the namespace to search, `.qt for the real tree
// @return namespace symbols
// @throws when root is not a namespace, when a namespace holds functions but no
//         test, or when a test is not a one-argument function
.qt.support.suites_under:{[root]
    if[not .qt.support.is_namespace @[value;root;{[e] (::)}];
        '"suites: there is no ",(string root)," namespace - no suite file defined one"];
    nss:.qt.support.walk root;
    helperless:nss where {[ns] (0<count system "f ",string ns) and 0=count .qt.support.tests_in ns} each nss;
    if[count helperless;
        '(string first helperless)," holds functions but no test* - a helper belongs under .qt.support or .qt.fixture"];
    suites:nss where 0<count each .qt.support.tests_in each nss;
    .qt.support.require_runnable each suites;
    suites}

// The suites under .qt, in the order they run.
// @return namespace symbols
// @throws as suites_under does
.qt.support.suites:{[] .qt.support.suites_under `.qt}

// Suites declared outside .qt: a root-level namespace holding test* functions.
// There are none to run - a suite lives under .qt - so this exists to refuse one.
// @return root namespace symbols, alphabetical
.qt.support.outside_suites:{[]
    top:asc (key `) except `qt`q`Q`h`j`o`z`qunit;
    ns:` sv/: `,'top;
    ns where {[ns]
        d:@[value;ns;{[e] (::)}];
        .qt.support.is_namespace[d] and 0<count .qt.support.tests_in ns} each ns}

// Reorder suites for the runner: UQF_TEST_ORDER=reverse or shuffle runs the
// same suites in another order, so a test that quietly depends on running
// after another one fails there (scripts/test.py q-order). Shuffle draws from
// the seed the runner set, so a shuffled run can be repeated.
// @param suites namespace symbols
// @param order "", "reverse" or "shuffle"
// @return the suites, reordered
// @throws on any other order
.qt.support.ordered:{[suites;order]
    if[not order in ("";"reverse";"shuffle");
        '"ordered: UQF_TEST_ORDER must be reverse, shuffle, or unset - not ",order];
    $[order~"reverse"; reverse suites; order~"shuffle"; suites iasc (count suites)?1000000; suites]}

// ---------------------------------------------------------------------------
// What the FILES define, held against what loaded
// ---------------------------------------------------------------------------
// q lets a later definition silently replace an earlier one, and lets a value
// replace a namespace container with everything in it. Neither is visible in
// the live tree afterwards, so both are checked in the text, where the
// definitions are still separate.

// The path a source line defines, when it is a fully qualified .qt definition
// at the start of the line.
// @param ln one line of source
// @return the path as a symbol, or ` for any other line
.qt.support.defined_path:{[ln]
    if[not ln like ".qt.*"; :`];
    i:ln?":";
    if[i=count ln; :`];
    p:i#ln;
    $[all p in .Q.an,"._"; `$p; `]}

// Every .qt path defined in some lines of source, in order, repeats kept.
// @param lines a list of source lines
// @return symbols
.qt.support.defined_paths:{[lines]
    p:.qt.support.defined_path each lines;
    p where not null p}

// The collisions among defined paths: a path defined twice (the later one wins,
// silently), and a path defined as a value that another path lies beneath (so
// it is required to be a container too). A label that repeats at different
// levels - .qt.qetl.job.stream and .qt.qetl.job.stream.poll - is neither.
// @param paths symbols, as defined_paths returns them
// @return a list of strings, one per collision, empty when there are none
.qt.support.collisions:{[paths]
    twice:where 1<count each group paths;
    s:asc distinct paths;
    strs:string each s;
    nx:1_strs,enlist "";
    both:s where {[a;b] ((count a)<count b) and ((1+count a)#b)~a,"."}'[strs;nx];
    (("defined twice: ",/:string each twice),("both a value and a namespace: ",/:string each both))}

// The test source files and the helper files beside them, whose .qt
// definitions are held to the layout rules. Not qunit.q, which is vendored.
// @return file paths as strings
.qt.support.layout_files:{[]
    lib:key `:tests/lib;
    lib:lib where (lib like "*.q") and not lib like "qunit.q";
    (("tests/lib/",/:string lib),("tests/q/",/:string .qt.support.suite_files[]),enlist "tests/q/reference_worker.q")}

// The tests a file declares, as full names: a .qt definition at the start of a
// line whose last label starts test. The harness and the fixtures are never
// suites, whatever their functions are called.
// @param lines a file's source lines
// @return symbols
.qt.support.declared_tests:{[lines]
    named:.qt.support.defined_paths lines;
    named:named where not {[p] any (string p) like/: (".qt.support.*";".qt.fixture.*")} each named;
    named where (string last each ` vs/: named) like "test*"}

// The suites that define a qunitConfig. qUnit reads it through the first label
// of a test's name - `.[`.<first label>; `qunitConfig, test]` - which is the
// suite's namespace only for a root-level suite, so below the root it is
// dropped without a word. None does today; this keeps it so.
// @param suites namespace symbols
// @return those that define one
.qt.support.config_ignored:{[suites] suites where {[ns] `qunitConfig in key value ns} each suites}

// Everything wrong with the suites' layout, as messages. Empty when the suite
// files and the live tree agree.
//   - a path defined twice, or as both value and container;
//   - a declared test that did not load as a test of a discovered suite (a
//     replaced definition, or a suite named so that it is not found);
//   - a discovered test no file declares (defined at run time, outside the
//     convention);
//   - a suite with a qunitConfig, which qUnit reads only for root-level suites
//     and would ignore here, silently.
// @return a list of strings
.qt.support.layout_problems:{[]
    files:.qt.support.layout_files[];
    lines:{read0 hsym `$x} each files;
    problems:.qt.support.collisions raze .qt.support.defined_paths each lines;
    declared:distinct raze .qt.support.declared_tests each lines;
    suites:.qt.support.suites[];
    live:raze {[ns] ` sv/: ns,/:.qt.support.tests_in ns} each suites;
    problems,:("a suite outside .qt: ",/:string each .qt.support.outside_suites[]);
    problems,:("declared but not discovered as a test: ",/:string each asc declared except live);
    problems,:("discovered but not declared in a suite file: ",/:string each asc live except declared);
    problems,:("qunitConfig is ignored for a suite not at the root: ",/:string each .qt.support.config_ignored suites);
    problems}

// Refuse a run whose layout has any problem.
// @throws a message listing the problems
.qt.support.require_layout:{[]
    p:.qt.support.layout_problems[];
    if[count p; '"suite layout: ","; " sv p]; }

// ---------------------------------------------------------------------------
// Running a suite that is not at the root
// ---------------------------------------------------------------------------
// The vendored .qunit.runNsTests refuses any namespace whose name is not a
// root-level key: it asks whether `1_string ns` is in `key ```, and for
// .qt.qetl.coverage the answer is no, whatever the namespace holds. The body
// below is the vendored one with exactly that test changed - it asks whether
// the namespace exists, wherever it is - and every reference qualified, since
// it is defined from the root and not from inside .qunit. The file
// tests/lib/qunit.q is not touched; this replaces its function at load, as
// support.q already does for runTest.
// @private
.qt.support.run_ns_tests:{[ns]
    if[not (ns~`.) or .qt.support.is_namespace @[value;ns;{[e] (::)}]; 'nsNoExist];
    .qunit.currentNamespaceBeingTested::{$["."=first a:string x; `$1 _ a; x]} ns;
    ff:.qunit.findFuncs[ns;;1b];
    .qunit.run each .qunit.findFuncs[ns;"beforeParameters*";0b];
    pFunc:first .qunit.findFuncs[ns;"parameters*";0b];
    pVals:$[0<count pFunc; @[.qunit.run;pFunc;()]; ()];
    if[count pVals; .qunit.lg "parameters = ",-3!pVals];
    idx:0;
    c:();
    do[max 1,count pVals;
        if[count pVals; .qunit.lg "parameter = ",-3!pVals idx];
        @[ns;`parameter;:;pVals idx];
        .qunit.run each ff "beforeNamespace*";
        testList: ff "test*";
        runRes: .qunit.runTest each testList;
        runRes:update parameter:(count testList)#enlist pVals idx from runRes;
        c,:runRes;
        .qunit.run each ff "afterNamespace*";
        idx+:1];
    .qunit.run each .qunit.findFuncs[ns;"afterParameters*";0b];
    $[count c; `status`name`result`actual`expected`msg`time`mem xcols update namespace:ns,name:testList from c; ()] };

// The vendored runNsTests, kept once, so loading this file twice does not
// capture the replacement as the original.
if[not `run_ns_tests_vendored in key `.qt.support; .qt.support.run_ns_tests_vendored:.qunit.runNsTests];
.qunit.runNsTests:.qt.support.run_ns_tests;
