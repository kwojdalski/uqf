/ run_examples.q - every documented @eg must run.
/ .
/ tests/q/test_docstring_examples.q checks the examples that state a value
/ (`-> 3f`). This checks ALL of them, including the majority that state
/ none - which until this file nothing ran. An example claiming no value can
/ still be wrong in the way that matters most to a reader: it can fail to
/ run. Thirty-one did, when this was first measured: they named data nothing
/ defined, were pseudo-code with a literal `...`, or had been cut off
/ mid-bracket by a scanner that read only their first line.
/ .
/ WHY A SEPARATE PROCESS, and not a test in the q-unit suite. Examples
/ without a value are there to show a call, and many of those calls CHANGE
/ things: they stage coverage, write status files, take locks, open runs.
/ Run in the suite's own process that state would leak into every test that
/ followed, and a test that passed or failed because of an unrelated
/ docstring is a worse problem than the one this solves. So it runs here,
/ once, with its own status directory - the same reason run_backfill_process
/ is its own lane.
/ .
/ Run from the repository root:
/   UQFSTATUSDIR=$(mktemp -d) q tests/q/run_examples.q
/ or through `scripts/test.py q-examples`.

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l src/integrations/data.q
\l docs/man.q
\l scripts/torq_pipeline.q
\l scripts/torq_metatables.q
\l src/etl/init.q
\l tests/lib/etl_test_doubles.q
\l tests/q/reference_worker.q
\l scripts/coverage.q

/ The test files, for the builders the fixtures reuse - read from
/ run_tests.q rather than listed again, so a new suite cannot be forgotten
/ here. Loading a test file defines its tests; nothing runs them.
{[l] if[l like "\\l tests/q/test_*"; system 1_l]} each read0 `:tests/run_tests.q;

/ A status directory of our own. Examples take locks and write status
/ files, and a directory another run left behind would make a lock example
/ fail for a reason that has nothing to do with the docs.
if[0=count getenv `UQFSTATUSDIR; setenv[`UQFSTATUSDIR;first system"mktemp -d"]];

/ The Databento directory, pinned to an EMPTY one, always - not only when
/ unset. Found the first time this ran in a fresh worktree: four data.q
/ examples had passed in the main checkout only because a gitignored .env
/ there names a real data directory, so the result depended on whose machine
/ ran it. An OS variable takes precedence over .env (.datatest pins that), so
/ setting one here makes every checkout see the same thing.
setenv[`DATABENTO_DATA_DIR;first system"mktemp -d"];

.egtest.bind_fixtures[];

rows:.egtest.examples[];
live:.egtest.needs_live;

/ One example's outcome: `ok, or the reason it is a failure.
/ `-> throws` documents a refusal, which passes only by refusing.
outcome:{[r]
    ran:@[{value x; (1b;"")};r 2;{(0b;x)}];
    $[(r 3) like "throws*";
        $[ran 0; "documented to throw, but ran without error"; `ok];
        $[ran 0; `ok; "throws: ",ran 1]]}

results:outcome each rows;
listed:(rows[;2]) in live`expr;

/ A listed example that fails is expected. A listed example that RUNS is a
/ failure too: its excuse has expired, and leaving the entry would let the
/ next real breakage in that call go unseen.
problems:();
{[r;o;isl]
    if[isl and o~`ok;
        problems,::enlist (r 0),":",string[r 1],"  listed as needing a live process, but it runs - remove it from .egtest.needs_live";
        :(::)];
    if[(not isl) and not o~`ok;
        problems,::enlist (r 0),":",string[r 1],"  ",(200 sublist o),"\n      ",r 2];
 }'[rows;results;listed];

/ An entry naming an example that no longer exists is stale in the other
/ direction - kept, it would silently excuse whatever takes that text later.
stale:(live`expr) where not (live`expr) in rows[;2];
{[e] problems,::enlist "needs_live entry matches no example (stale): ",e;} each stale;

-1 "";
-1 "================= documented examples =================";
-1 string[count rows]," @eg example(s): ",string[sum results~\:`ok]," run, ",
   string[sum listed]," need a live process (listed with a reason), ",
   string[count problems]," problem(s)";
if[count problems;
    -1 "";
    {[p] -1 "  ",p} each problems;
    exit 1];
exit 0
