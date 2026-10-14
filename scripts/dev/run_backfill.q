// run_backfill.q - run one bounded worker in THIS process, with no TorQ.
//
// WHY THIS EXISTS. `uqs backfill` launches the worker through torq.sh, which
// starts the SYSTEM q. On macOS that q is arm64, and KX ships odbc.so for
// x86_64 only - so an ODBC-backed worker launched that way can never reach a
// driver, whatever the credential says. Every ODBC source in this tree is a
// DuckDB file, which makes that the normal case rather than an edge one.
//
// scripts/dev/odbc_rosetta.sh builds an x86_64 overlay QHOME that CAN load
// the driver, but its `q` verb only opens a REPL. This script is the missing
// half: the same run the backfill process performs, with nothing TorQ-shaped
// around it, so it can be launched under that overlay.
//
//   scripts/dev/odbc_rosetta.sh backfill crypto_market_data_backfill \
//       -version v2 -from 2026.09.11D21:00 -to 2026.09.11D23:00
//
// or, when no driver is needed (the fixture path):
//
//   q scripts/dev/run_backfill.q -worker crypto_market_data_backfill \
//       -version v2 -from 2026.09.11D21:00 -to 2026.09.11D23:00 -fixture
//
// -fixture is what lets a run with no credential write the fixture (#1082);
// without it that run is refused.
//
// WHAT IT IS NOT. Not a replacement for `uqs backfill`, and deliberately
// smaller: it does not register with discovery, does not tell a running HDB
// to reload, and does not read KDBHDB. A DEVELOPMENT aid for the one case the
// fleet path cannot serve - reach a real ODBC driver - and for running a
// worker without a stack at all.
//
// Flags are torq_backfill.q's, so a command line moves between the two
// unchanged. -hdb is the one addition: without it the rows stay in this
// process (.qetl.io.memory) and are gone at exit, which is what you want when
// the question is "does the query work", not "fill the database".
// -mode (validate, plan, dry_run, run) and -on_conflict are applied the way
// torq_backfill.q applies them (#1319), by the same functions; -mode plan and
// -mode validate open nothing and write nothing, whatever -hdb says.
//
// Run from the repository root: init.q's \l lines are root-relative.

if[not `worker in key .Q.opt .z.x;
    -1 "run_backfill: -worker is required. e.g. -worker crypto_market_data_backfill -version v1 -from 2026.09.11D21:00 -to 2026.09.11D23:00";
    exit 2];

\l src/init.q
\l src/etl/init.q

\d .qdev.runbackfill

/ The flags are torq_backfill.q's, parsed by the same function:
/ .qetl.job.bounded.spec_from_flags. -version may be left out for a worker
/ that declares a default source_version, exactly as on the fleet path.

/ Where rows go: the HDB named by -hdb, or this process's memory.
/ .
/ Partitioned by the SOURCE's time column, exactly as torq_backfill.q's
/ use_hdb does - where rows belong is a fact about the source, not about how
/ the worker was launched.
/ @param opts the parsed command line
/ @param worker the worker about to run
/ @return the io manager now installed as the default
use_io:{[opts;worker]
    if[not `hdb in key opts;
        .qetl.log.info[`run_backfill;"writing into this process only - pass -hdb <root> to keep the rows";()!()];
        :.qetl.io.default:.qetl.io.memory];
    root:hsym `$first opts`hdb;
    col:(.qetl.source.def (.qetl.job.bounded.def worker)`source)`time_column;
    .qetl.log.info[`run_backfill;"writing into the HDB";`root`partition_col!(root;col)];
    .qetl.io.default:.qetl.io.hdb[root;col]}

\d .

/ A status directory is required by the lock and the checkpoint. Defaulted
/ here rather than refused, because TORQDATA is unset outside a TorQ process
/ and the bare fallback is "/status", which is not writable and fails with a
/ bare 'os halfway through init.
if[""~getenv `UQF_STATUS_DIR;
    setenv[`UQF_STATUS_DIR;"output/uqs/status"];
    -1 "run_backfill: UQF_STATUS_DIR was unset, using output/uqs/status"];
system "mkdir -p ",getenv `UQF_STATUS_DIR;

/ Caught rather than thrown: a bad flag reaching q's own handler prints a
/ trace and exits 0, which is the worst of both - it looks like a clean run
/ to a shell and like a crash to a reader.
s:@[.qetl.job.bounded.spec_from_flags;.Q.opt .z.x;{[e] -1 e; exit 2}];
worker:s`worker;
ns:(.qetl.job.bounded.def worker)`ns;

.qetl.log.info[worker;"run_backfill starting";s`spec];
if[`fixture in key .Q.opt .z.x; .qetl.cfg.set_override[`fixture_writes;"1"]];
/ -on_conflict then -mode, as torq_backfill.q does, and BEFORE any io is
/ opened: a bad value, or a variable that outranks the flag, is refused here
/ rather than ignored (#1319). A -mode plan the script dropped wrote rows
/ into the -hdb root.
@[{[o] .qetl.job.bounded.runtime.use_on_conflict["run_backfill";o]; .qetl.job.bounded.runtime.use_mode["run_backfill";o]};
    .Q.opt .z.x;{[e] -1 e; exit 2}];
/ A read-only mode stops here: nothing opened, nothing written.
md:.qetl.job.bounded.runtime.mode[];
if[md in `validate`plan;
    / Caught like the flag refusals above (#1373): validate and plan_only throw
    / on their first refusal - an unaligned range, a bad source row - and an
    / untrapped throw is a raw trace, or a debug prompt on a terminal.
    r:@[{[w;sp;m] $[m~`validate; .qetl.job.bounded.validate[w;sp]; .qetl.job.bounded.plan_only[w;sp]]}[worker;s`spec];
        md;{[e] -1 "run_backfill: ",e; exit 2}];
    .qetl.log.info[worker;string[md]," - nothing opened, nothing written";`plan _ r];
    exit 0];
.qdev.runbackfill.use_io[.Q.opt .z.x;worker];

/ Three named parameters so {...}[ns;spec] is a PROJECTION: .Q.trp calls
/ what it is given with one argument, and a two-parameter lambda with both
/ supplied is a finished value, which .Q.trp refuses with a bare 'type.
result:.Q.trp[{[ns;spec;ignored] (` sv ns,`init)[spec]; (` sv ns,`run)[]}[ns;s`spec];::;
    {[e;bt]
        .qetl.log.err[`run_backfill;"run failed";enlist[`error]!enlist e];
        .qetl.log.err[`run_backfill;"backtrace";enlist[`trace]!enlist .Q.sbt bt];
        `state`error!(`failed;e)}];

.qetl.log.info[worker;"run_backfill finished";result];
code:.qetl.job.bounded.exit_code result`state;
exit code;
