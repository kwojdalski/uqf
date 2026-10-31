// support.q - the shared helpers of the test suites (.qt.support), on top of
// the vendored qUnit framework (tests/lib/qunit.q).
// Load qunit.q before this file. Every definition is a fully qualified .qt.support
// name from the root context, never inside a `\d` block - see discovery.q for why,
// and for how a suite is told from a helper, a container and a fixture.
// This file loads discovery.q last, so a runner loads one file for both.


// True if actual and expected are within tol of each other. Reduced with
// `all` so it also works when actual/expected are equal-length vectors
// (assertThat needs a single boolean, not a vector of them).
.qt.support.approx:{[tol;a;b] all tol>=abs a-b};

// assertThat wrapper for approximate numeric equality.
.qt.support.assertApprox:{[actual;expected;tol;msg] .qunit.assertThat[actual;.qt.support.approx[tol];expected;msg]};

// A genuinely empty etl_coverage ledger, whatever shape it currently has.
//
// The DELETE is the point. `.qetl.coverage.init_ledger` creates the table only when
// absent, which is the right contract - but it means a test that replaced the
// ledger with a differently-shaped one cannot restore it by calling
// init_ledger again: the wrong-shaped table exists, so init_ledger leaves it,
// and the bad shape leaks into every suite that runs afterwards. That is
// exactly what happened when the #60 schema-guard tests were added, and it
// broke 21 tests in two other namespaces.
//
// Also note `value`: init_ledger returns the SYMBOL `etl_coverage, so
// `0#init_ledger[]` is an empty symbol VECTOR rather than an empty table.
// Every suite healed itself on first use, because an empty vector is not in
// `tables` and init_ledger then re-created the table - silent, and invisible
// until something called `meta` directly.
// An empty coverage ledger built column by column, independently of
// .qetl.coverage.init_ledger - so a test asserting "a foreign ledger of the right
// shape is accepted" asserts something, rather than comparing init_ledger's
// output against itself.
//
// It exists as ONE fixture because it was three hardcoded column lists, and
// every addition to .qetl.coverage.schema broke all three at once in a way that read
// like a bug in require_schema. superseded_at did it; run_id did it
// again. test_coverage's test_the_foreign_fixture_tracks_the_declared_schema
// now fails FIRST, and by name, so the next one is a one-line fix here.
.qt.support.foreign_coverage_ledger:{[]
    ([] dataset:`symbol$(); partition:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$();
        superseded_at:`timestamp$(); run_id:0#0Ng)};

.qt.support.reset_coverage_ledger:{[]
    ![`.;();0b;enlist `etl_coverage];
    // The ledger is persisted now, so deleting the in-memory table is no
    // longer a reset: the next attach or stage_completion reloads whatever
    // the last suite left on disk. Remove the file too, or tests leak rows
    // into each other in run order - which is the same silent cross-test
    // dependency this helper was written to prevent.
    @[{system"rm -f ",x};.qetl.coverage.ledger_path[];{[e] (::)}];
    // and its readiness (#1094): the watermarks, and this process's state
    @[{system"rm -f ",x};.qetl.coverage.ready_path[];{[e] (::)}];
    `.qetl.coverage.unready set 0#.qetl.coverage.unready;
    `.qetl.coverage.defer_ready set 0b;
    .qetl.coverage.init_ledger[];
    value `etl_coverage};

// ---------------------------------------------------------------------------
// What the suite IS: its files. Its namespaces are discovered (discovery.q).
// ---------------------------------------------------------------------------
// Three scripts need the file list and all three used to get it by reading
// tests/run_tests.q as TEXT. The definition lives here, as a function, and the
// runner is one caller among three rather than the source everyone parses.
// The namespaces are not listed anywhere: a suite is found by what it holds.

// Every test suite file under tests/q/, alphabetically.
//
// test_*.q ONLY: tests/q/ also holds scripts RUN as child processes -
// run_examples.q, run_coverage.q, upstream_instance.q,
// smoke_databento_odbc.q - and loading those here would execute them.
//
// Two clauses rather than like "test_*.q": an interior wildcard is unreliable
// in q's like (QB002), which the repository's trap gate refuses.
.qt.support.suite_files:{[]
    f:key `:tests/q;
    f:f where f like "*.q";
    f:asc f where f like "test_*";
    if[0=count f; '"support.suite_files: no tests/q/test_*.q found - wrong directory?"];
    f}

// --- running a subset: -files (#1690, #1694) ------------------------------
// `q tests/run_tests.q -files a.q,b.q` RUNS only the suites those files
// define - comma-separated names, bare or as tests/q/ paths. Every file is
// still LOADED: a suite may lean on what another file defines (the contract
// drivers test_job_output_contracts runs, the builders the documentation
// examples reuse, the scripts a TorQ suite \l's), and loading is cheap
// next to running. What scripts/dev/test_impact.py selects for a change, and
// what a shard of the suite runs.
//
// An empty list and a name that is no suite file are refused, not skipped:
// a subset that silently lost a file would pass without running it. A
// command-line option rather than an environment variable, so the child
// processes a suite starts - which load the suites themselves - do not
// inherit it.

// The `-files` option among command-line arguments.
// @param args strings, as .z.x
// @return the option's value as one string, or :: when it was not given
.qt.support.files_option:{[args]
    o:.Q.opt args;
    if[not `files in key o; :(::)];
    v:o`files;
    $[10h=type v; v; "," sv v]}

// The suite files a selection names, in suite order.
// @param files symbols, every suite file (`test_a.q`test_b.q...)
// @param sel :: for no selection, else a string of comma-separated file names
// @return symbols, the selected files - every one when sel is ::
// @throws error when sel names no file, or names one that is not a suite file
.qt.support.select_files:{[files;sel]
    if[sel~(::); :files];
    names:{x where 0<count each x} trim each "," vs sel;
    if[0=count names; '"support.select_files: -files names no file - give tests/q/test_*.q names, comma-separated"];
    want:`${$["tests/q/"~8#x; 8_x; x]} each names;
    if[count bad:want except files;
        '"support.select_files: -files names no suite file: ",", " sv string bad];
    files where files in want}

// The suites among `suites` that the files at `paths` declare a test in -
// read from the files, by the rule the layout check holds every file to
// (.qt.support.declared_tests), so it is the same answer the load gave.
// @param suites namespace symbols, as .qt.support.suites returns them
// @param paths file paths, as strings
// @return the suites those files define, in the order given
.qt.support.suites_of:{[suites;paths]
    tests:raze {.qt.support.declared_tests read0 hsym `$x} each paths;
    suites where suites in distinct {` sv -1_` vs x} each tests}

// The suites this run runs: those of the files -files names, or all of them.
// @param suites namespace symbols, every suite discovered
// @param args strings, as .z.x
// @return symbols
// @throws as select_files does
.qt.support.selected_suites:{[suites;args]
    sel:.qt.support.files_option args;
    if[sel~(::); :suites];
    files:.qt.support.select_files[.qt.support.suite_files[];sel];
    .qt.support.suites_of[suites;"tests/q/",/:string files]}

// --- coverage of a subset: -modules (#1719) --------------------------------
// `q tests/q/run_coverage.q -files a.q,b.q -modules src/x.q,src/y.q` runs the
// suites those files define and instruments and judges only the functions
// the named production files define - what scripts/dev/test_impact.py's
// coverage_for picks for a change. A subset that finds a function newly
// uncovered exits 3 and scripts/test.py q-coverage hands the verdict to a run
// of the whole suite; one that finds a baseline entry now entered fails, as
// the whole suite would (it runs these suites and more).

// The `-modules` option among command-line arguments.
// @param args strings, as .z.x
// @return the paths, as strings, or :: when it was not given
// @throws error when it is given and names nothing
.qt.support.modules_option:{[args]
    o:.Q.opt args;
    if[not `modules in key o; :(::)];
    v:o`modules;
    m:{x where 0<count each x} trim each "," vs $[10h=type v; v; "," sv v];
    if[0=count m; '"support.modules_option: -modules names no file - give .q paths, comma-separated"];
    m}

// The file a function was defined in, relative to the working directory
// (the repository root, where every runner starts) - "" when it has none,
// as for one built from a string at run time.
// @param nm a function's name, fully qualified
// @return a string
.qt.support.source_file:{[nm]
    f:@[{first -3#value value x};nm;{[e] ""}];
    if[not 10h=type f; :""];
    cwd:raze[system "cd"],"/";
    $[cwd~(count cwd)#f; (count cwd)_f; f]}

// The functions among `names` that the files `modules` define.
// @param names function names
// @param modules repository-relative paths as strings, or :: for every file
// @return the names defined there, in the order given
.qt.support.defined_in:{[names;modules]
    if[modules~(::); :names];
    / As symbols: `in` over strings compares characters, not paths.
    names where (`$.qt.support.source_file each names) in `$modules}

// What a coverage run is judged on: the functions nothing entered against
// the baseline, both ways, among the functions it judges.
// A baseline entry outside `judged` is not this run's to judge - unless the
// tree defines no such function at all (`known`), when it is stale wherever
// the run looked.
// @param cold symbols, the judged functions nothing entered
// @param baseline symbols, tests/q/coverage_baseline.txt's names
// @param judged symbols, the functions this run judged
// @param known symbols, every function the run could have instrumented
// @return a dictionary new_gaps (cold, not in the baseline) and closed
//   (in the baseline, not cold)
.qt.support.coverage_gate:{[cold;baseline;judged;known]
    base:baseline where (baseline in judged) or not baseline in known;
    cold:cold where cold in judged;
    `new_gaps`closed!(asc cold except base;asc base except cold)}

// --- which test files enter each function: the coverage map (#1719) --------
// tests/q/coverage_map.txt says, for every function a whole run enters, the
// suite files whose suites enter it. scripts/dev/test_impact.py's
// coverage_for adds to a subset a file that enters each function it judges,
// so a function covered only by a suite outside q-tests' selection is not
// reported as never entered. `q tests/q/run_coverage.q -map PATH` writes it
// from a whole run (scripts/dev/master_coverage.py --write-map).

// The `-map` option among command-line arguments.
// @param args strings, as .z.x
// @return the path as a string, or :: when it was not given
// @throws error when it is given without a path
.qt.support.map_option:{[args]
    o:.Q.opt args;
    if[not `map in key o; :(::)];
    v:o`map;
    v:trim $[10h=type v; v; " " sv v];
    if[0=count v; '"support.map_option: -map names no file - give the path to write"];
    v}

// While a coverage run runs, each suite's probes it raised: .qunit.runNsTests
// is wrapped, and its before/after hooks count as the suite's. A dictionary
// suite -> indices into .cov.lineHits.
.qt.support.suite_hits:(`symbol$())!();

// Wrap .qunit.runNsTests to record into .qt.support.suite_hits; returns the
// original, for .qt.support.untrack_suites. .qunit.runTests calls it by name,
// so the wrapper is what runs. Only the probes that existed before the suite
// ran are compared: a nested .cov.run in a suite grows the vector.
// @return the original .qunit.runNsTests
.qt.support.track_suites:{[]
    orig:.qunit.runNsTests;
    .qt.support.suite_hits:(`symbol$())!();
    .qunit.runNsTests:{[orig;ns]
        before:.cov.lineHits;
        r:orig ns;
        now:(count before)#.cov.lineHits;
        .qt.support.suite_hits[ns]:where now>before;
        r}[orig];
    orig}

// Put back what .qt.support.track_suites replaced.
// @param orig its return value
.qt.support.untrack_suites:{[orig] .qunit.runNsTests:orig;}

// The coverage map from a whole run.
// @param results .cov.run's table, in probe order
// @param first0 .cov.nextLine before the run: the first probe it allocated
// @param hits suite -> probe indices raised, as .qt.support.suite_hits
// @param files suite file -> the suites it declares, as symbols
// @return a table module/function/files (files a sorted symbol list), one row
//   per function some suite entered, sorted by module then function
.qt.support.coverage_map:{[results;first0;hits;files]
    n:count each results`lines;
    start:first0+0^prev sums n;
    / A function is entered when its first statement's probe ran - the same
    / test as run_coverage.q's `iterations=0`, which reads that probe.
    entered:{[n;start;names;h] names where (n>0) and start in h}[n;start;results`name] each hits;
    pairs:raze {[s;fs] ([] function:fs; suite:(count fs)#s)}'[key entered;value entered];
    if[0=count pairs; :([] module:`symbol$(); function:`symbol$(); files:())];
    / function -> the suites entering it -> the files declaring those suites
    fsuites:exec suite by function from pairs;
    filesOf:{[files;s] `#asc where {[s;x] any x in s}[s] each files}[files];
    t:([] function:key fsuites; files:filesOf each value fsuites);
    t:update module:`$.qt.support.source_file each function from t;
    `module`function xasc `module`function`files xcols select from t where not null module}

// The map as tests/q/coverage_map.txt's lines.
// @param m a table as .qt.support.coverage_map returns
// @return strings
.qt.support.map_lines:{[m]
    head:("# Which suite files enter each function, written by a whole coverage run";
        "# (q tests/q/run_coverage.q -map tests/q/coverage_map.txt, #1719). A subset";
        "# coverage run adds a file that enters each function it judges, so a";
        "# function only a suite outside q-tests' selection enters is not reported";
        "# as never entered. Stale is slow, never wrong: a subset that finds a";
        "# function never entered hands its verdict to a whole run.";
        enlist "#";
        "# module<TAB>function<TAB>suite files, comma-separated");
    head,{"\t" sv (string x`module;string x`function;"," sv string x`files)} each m}

// Load every suite file. Globbed, so a new suite runs the day it is written.
.qt.support.load_suites:{[] {system "l tests/q/",string x} each .qt.support.suite_files[];}

// A run's outcomes as data, for whoever holds the run to something - one
// writer, so tests/run_tests.q and tests/q/run_coverage.q cannot drift into
// two formats. `res` is what .qunit.runTests returns.
//   UQF_TEST_RESULTS   every test that ran: suite and name, tab-separated,
//                      in run order. What a rename or a move must not change
//                      is the SET of these, comparable by diff, not by total.
//   UQF_TEST_FAILURES  every test that did not pass: name, status and why,
//                      tab-separated; one empty line when all passed. Read by
//                      scripts/portable/full_suite.py (PeachQ) and by
//                      scripts/dev/attest_kdbx.py, which takes q-tests'
//                      verdict from the coverage run (#1704).
// Each is written only when its variable names a file.
.qt.support.write_outcomes:{[res]
    if[count getenv `UQF_TEST_RESULTS;
        (hsym `$getenv `UQF_TEST_RESULTS) 0: {(string x`namespace),"\t",string x`name} each 0!select namespace,name from res];
    if[count getenv `UQF_TEST_FAILURES;
        bad:select name,status,detail:{$[x=`error; y; z]}'[status;result;msg] from res where status<>`pass;
        flat:{ssr[ssr[x;"\n";" "];"\t";" "]};
        lines:{[flat;r] "\t" sv (string r`name;string r`status;flat $[10h=type d:r`detail; d; -3!d])}[flat] each bad;
        (hsym `$getenv `UQF_TEST_FAILURES) 0: $[count lines; lines; enlist ""]];
    }

// ---------------------------------------------------------------------------
// What the TREE declares, as opposed to what a test registered
// ---------------------------------------------------------------------------
// Several suites check a live registry against the namespaces it should have
// produced - every registered source has a `.qpipe.source.<name>`, every registered
// worker a `.qpipe.job.<name>`. Those registries also hold entries the TESTS put
// there: `.qetl.job.bounded` fixture workers named `reference`, `partial` and `fixture_*`,
// and sources registered by `etl_test_doubles.q`. None has a declaration
// file, so none has a namespace, and a test that reads the registry alone
// fails or passes on whether the suite that registered them ran first.
//
// So: the files are the tree's declarations, and a suite intersects the
// registry with these to ask about the tree rather than about the process.

// The declaration stems in one of the ETL directories: `foo.q -> `foo.
.qt.support.etl_declaration_names:{[dir]
    n:key hsym `$dir;
    n:n where n like "*.q";
    asc `$-2_/:string n}

// The q command a runner starts its child processes with: $QCMD if set,
// otherwise `q`, resolved by the shell on PATH. TorQ's rule (torq.sh), and
// uqs.interpreter.q_command's (#414); scripts/test.py passes QCMD on to every lane
// it runs, so the children run the binary the runner itself was started with.
.qt.support.q_interpreter:{[] $[""~getenv `QCMD; "q"; getenv `QCMD]}

// Every .q file under a directory, recursively.
//
// `key` on a directory returns a symbol LIST (11h) and on a file an atom
// (-11h), which is how a subdirectory is told from a file without shelling
// out.
.qt.support.q_files:{[dir]
    paths:(dir,"/"),/:string key hsym `$dir;
    isdir:{11h=type key hsym `$x} each paths;
    (paths where (not isdir) and paths like "*.q"),
        raze .qt.support.q_files each paths where isdir}

// The namespaces this tree's own source DECLARES, from the files.
//
// The alternative - a live scan filtered by a hand-kept deny-list of test
// scaffolding - cannot hold, because suites create whole namespaces at run
// time: `.qcompletetest` and `.qmethodsonly` are fixture workers built
// inside assertions, and `.qpipe.job.nt_k`/`.qpipe.job.nt_l` are streaming jobs
// registered by a test. Each new one would have to be remembered, and until
// it was, whichever suite ran first decided the answer.
//
// Worker instances are appended because no file declares them: `.qetl.job.bounded.define`
// stamps `.qpipe.job.<name>` from the registered name (#227).
.qt.support.tree_namespaces:{[]
    decls:raze {[f]
        src:read0 hsym `$f;
        3_/:src where src like "\\d .*"} each .qt.support.q_files["src"];
    ns:`$decls where 1<count each decls;
    ns:ns,`$".qpipe.job.",/:string .qt.support.etl_declaration_names["src/etl/workers"];
    asc distinct ns}


/ Every log line `f` writes, as (level;id;text;fields) with the scoped context
/ merged in as the real .qetl.log.line merges it - with TRACE switched on for
/ the call when `trace` is 1b. The real line function and the trace switch
/ are put back however `f` ends, thrown or not: a recorder left installed
/ swallows every log line of every suite after it.
/ @param trace 1b to switch TRACE on for the call
/ @param f a niladic function
/ @return the lines, in order
.qt.support.captured_log:{[trace;f]
    keep:.qetl.log.line; was:.qetl.log.trace_enabled;
    if[trace; .qetl.log.trace 1b];
    `.qt.support.lines set ();
    .qetl.log.line:{[level;id;text;fields] .qt.support.lines,:enlist (level;id;text;.qetl.log.with_scope fields)};
    @[f;::;::];
    .qetl.log.line:keep; .qetl.log.trace was;
    .qt.support.lines}

/ ------------------------------------------------- KEYED-TABLE REGISTRIES
/ .
/ .qetl.transform.registry, .qetl.source.sources, .qetl.job.bounded.worker_cfg
/ and .qetl.dag.jobs are keyed tables keyed on `name` (#512), as
/ .qf.exec.methods is. A test that swaps a row in or out cannot use the
/ dictionary idioms it once did - `k _ reg`, `reg[k]:row`, `reg[k;col]:v` all
/ throw on a keyed table - so it goes through these, which keep the registry
/ declared rather than rebuilding it.

/ Drop rows from a registry by name; a name it does not hold is ignored.
/ @param reg the registry's global name, e.g. `.qetl.source.sources
/ @param names the names to drop
.qt.support.drop_rows:{[reg;names] ![reg;enlist (in;`name;enlist (),names);0b;`symbol$()];}

/ Store `row` under `name`, replacing any row already there.
/ @param reg the registry's global name
/ @param name the key
/ @param row the row, as the registry's def returns it
.qt.support.put_row:{[reg;name;row] reg upsert (enlist[`name]!enlist name),row;}

/ Set one field of one row, leaving the rest of the row as it was.
/ @param reg the registry's global name
/ @param name the key of a row the registry holds
/ @param col the column
/ @param v the value
.qt.support.set_field:{[reg;name;col;v] .qt.support.put_row[reg;name;@[(get reg) name;col;:;v]]}

/ ------------------------------------------------------ TorQ PROCESS NAME
/ .
/ Run f as if hosted by a TorQ process named p - or, with p null, as plain q
/ with no TorQ name at all - then put .proc back as it was, absent included.
/ .qetl.run.proc_name is src/'s one read of it (#619), so this is how a test
/ shows a behaviour in both hosts.
/ @param p the process name, or ` for no TorQ
/ @param f a niladic function
/ @return f's result, or (`threw;error)
.qt.support.with_procname:{[p;f]
    had:@[{`procname in key x};`.proc;0b];
    old:$[had; .proc.procname; `];
    $[null p; if[had; ![`.proc;();0b;enlist `procname]]; `.proc.procname set p];
    r:@[f;::;{(`threw;x)}];
    $[had; `.proc.procname set old; @[{![`.proc;();0b;enlist `procname]};::;::]];
    r}

// ------------------------------------------------- STREAM JOB REGISTRY
//
// A suite that defines streaming jobs (.qetl.job.stream.define and the
// horizon, rolling, bars and normalizer kinds built on it) writes the
// process-global registries, and .qt.qpipe.job_output_contracts and .qt.qetl.dag scan those live, so
// whichever suite ran first decided what they saw (#1375). The per-test
// isolation below deliberately leaves them alone - several suites define a job
// once and reuse it across cases - so a suite brackets its WHOLE namespace:
//   beforeNamespace_save_jobs:{[] .qt.support.save_stream_jobs[`.mytest]}
//   afterNamespace_restore_jobs:{[] .qt.support.restore_stream_jobs[`.mytest]}

// Every registry a stream job's definition writes is .qetl.job.stream.registries
// (src/etl/core/stream_registries.q): each job kind names its own where it
// declares it, so a new kind is bracketed without editing this file (#1386).

// Remember the stream registries, for the suite in namespace ns.
// @param ns the suite's namespace, e.g. `.qt.qetl.job.stream.horizons
// @return the registries saved
.qt.support.save_stream_jobs:{[ns]
    r:.qetl.job.stream.registries where {@[{get x; 1b};x;{[e] 0b}]} each .qetl.job.stream.registries;
    / each value enlisted: dicts that share keys would otherwise collapse into a table
    (` sv ns,`saved_stream_registries) set r!{enlist get x} each r;
    r}

// Put back the stream registries save_stream_jobs took: a job the suite
// defined is gone, and one it removed is back.
// @param ns the suite's namespace, as given to save_stream_jobs
// @return the registries restored
.qt.support.restore_stream_jobs:{[ns]
    s:get ` sv ns,`saved_stream_registries;
    {x set first y}'[key s;value s];
    key s}

// ------------------------------------------------------------ ISOLATION
//
// Every suite runs in ONE q process, so whatever a test changes outlives it
// unless something puts it back (#834). #801's test set
// UQS_REQUIRE_LIVE_SOURCES=1 and never cleared it, and every suite after it
// ran with live sources required. Each test restoring its own state by hand
// is a convention, and a convention is broken by the next test written.
//
// So the harness does it: .qunit.runTest - the vendored runner's per-test
// entry point, which runNsTests calls by name - is wrapped below to take a
// snapshot before each test and put it back after, whatever the test did and
// whether or not it threw. setUp/tearDown run inside the wrapper, so state a
// suite's setUp establishes is gone after each test as well.
//
// WHAT IS RESTORED:
//   environment   every variable the tree reads - the contract surface's env
//                 scan (docs/reference/surfaces/current/variables.csv) - and
//                 every one a test or the source sets with setenv. q cannot
//                 unset a variable, so one that was unset comes back as "",
//                 which getenv cannot tell apart.
//   the timer     \t, so a test that starts a timer cannot leave it firing
//                 into later suites.
//   root tables   one a test creates is dropped after it, so the next test
//                 meets the tree's own tables, not a fixture left behind.
//                 One that existed before the test is left alone: a test
//                 that changes a shared table restores it itself.

// Private: the names in `files`' `setenv[`NAME;...]` calls.
// @private
.qt.support.setenv_names:{[files]
    lines:raze {read0 hsym `$x} each files;
    / `[[]`: a bare `[` opens a character class in ss's pattern, as in like's
    at:ss[;"setenv[[]`"] each lines;
    names:raze {[l;i] {`$(x?";")#x} each 8_/:i _\: l}'[lines;at];
    names where {all x in .Q.an} each string names}

// The environment variables a test may change, read once at load.
.qt.support.env_names:{[]
    / untrapped: a harness that could not read it would isolate less, silently
    surface:exec name from (enlist "S";enlist ",") 0: `:docs/reference/surfaces/current/variables.csv;
    asc distinct surface,.qt.support.setenv_names .qt.support.q_files["tests/q"],.qt.support.q_files["src"]}[]

// What the harness restores after each test: the environment, the timer, and
// which root tables exist.
.qt.support.snapshot:{[] `env`timer`tables!(.qt.support.env_names!getenv each .qt.support.env_names;system"t";tables `.)}

// Put back the variables `env` held: only what changed is written.
// @param env a dictionary of variable names to values, as snapshot takes it
.qt.support.restore_env:{[env]
    now:getenv each key env;
    changed:where not env~'now;
    if[count changed; setenv'[changed;env changed]];
    }

// Put back what `snapshot` took: only what changed is written.
// @param s a snapshot
.qt.support.restore:{[s]
    .qt.support.restore_env s`env;
    if[not (s`timer)=system"t"; system"t ",string s`timer];
    made:(tables `.) except s`tables;
    if[count made; ![`.;();0b;made]];
    }

// The runner's own runTest, kept once: loading this file twice must not wrap
// the wrapper, which would then call itself.
if[not `run_test_unisolated in key `.qt.support; .qt.support.run_test_unisolated:.qunit.runTest];

// Private: one test, isolated - see ISOLATION above.
// @private
.qt.support.isolated_run_test:{[fn]
    s:.qt.support.snapshot[];
    r:@[.qt.support.run_test_unisolated;fn;{[s;e] .qt.support.restore s; 'e}[s]];
    .qt.support.restore s;
    r}

.qunit.runTest:.qt.support.isolated_run_test;

// ------------------------------------------------------ SUITE ISOLATION
//
// The per-test wrapper above does not reach a suite's beforeNamespace_* and
// afterNamespace_* hooks: the runner calls them from runNsTests, outside any
// runTest. A variable a suite set there - UQF_STATUS_DIR, most often -
// stayed set for every suite after it, and a suite could pass or fail on
// which ran before it (#1814, #1818). So runNsTests is wrapped as runTest is
// (discovery.q installs it, after defining the runNsTests it wraps): the
// environment (the same variables) and the working directory are taken
// before the suite's first hook and put back after its last, whether it
// passed, failed or threw. Tables and the timer are left to the per-test
// restore: what a suite's beforeNamespace builds for its tests is its own to
// tear down.

// What the harness restores after each suite: the environment and the cwd.
.qt.support.suite_snapshot:{[] `env`cwd!(.qt.support.env_names!getenv each .qt.support.env_names;raze system"cd")}

// Put back what `suite_snapshot` took.
// @param s a suite snapshot
.qt.support.suite_restore:{[s]
    .qt.support.restore_env s`env;
    if[not (s`cwd)~raze system"cd"; system"cd ",s`cwd];
    }

// Private: one suite, isolated - see SUITE ISOLATION above.
// @private
.qt.support.isolated_run_ns_tests:{[ns]
    s:.qt.support.suite_snapshot[];
    r:@[.qt.support.run_ns_tests;ns;{[s;e] .qt.support.suite_restore s; 'e}[s]];
    .qt.support.suite_restore s;
    r}

// The run spec a worker with no credential stores once `init` has it: the
// same, under the release tagged as the fixture's (#1082).
// @param s a run spec - source_version, range_from, range_to
// @return s, its source_version tagged
.qt.support.fixture_spec:{[s] @[s;`source_version;.qetl.source.fixture_version]}

// The suites run bounded workers on their sources' fixtures, which a run must
// now ask for (#1082). Set once, before any test, so it is the baseline every
// test is restored to; a test of the refusal clears it for itself.
setenv[`UQF_FIXTURE_WRITES;enlist "1"];

// Which namespaces are suites, and the layout rules that keep a renamed suite
// from silently ceasing to run.
system "l tests/lib/discovery.q";
