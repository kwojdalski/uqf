/ etl/init.q - loads the ETL tree in dependency order. One list, used by both
/ a running process and the test suite.
/ .
/ Why this file exists: until it did, nothing loaded the ETL tree as a whole.
/ Each worker was loaded piecemeal and tests/run_tests.q held the only
/ complete, correctly-ordered list anywhere in the repository. So .qetl.dag's job
/ graph - the whole point of which is that a q PROCESS can order and draw its
/ own DAG - existed only inside the test suite. A capability that works only
/ under the test runner is not a capability.
/ .
/ It also removes a duplicate. run_tests.q keeps two hardcoded lists (the
/ files it loads and the nsList of namespaces it runs), and forgetting the
/ second means tests silently never run. This takes the ETL half of the first
/ list out of that file, so there is one place to add an ETL module.
/ .
/ THE ORDER IS LOAD-BEARING, and not obvious from the filenames:
/ .
/   coercion    before  source_contract  - .qetl.source's type table names
/                                          .qetl.coerce.to_timestamp/to_symbol AT
/                                          LOAD TIME, so a later coercion.q
/                                          aborts source_contract.q with a
/                                          bare `.qetl.coerce.to_symbol
/   materialisation before worker_runtime - .qetl.job.bounded.runtime.remaining calls .qetl.coverage
/   dag         before  pipeline_dag     - the generated bridge defines
/                                          .qetl.dag.register_pipelines, which
/                                          calls .qetl.dag.register
/   core        before  sources/workers  - a declaration registers itself on
/                                          load, so the registry it registers
/                                          into has to exist
/ .
/ run.q sits after materialisation.q by READABILITY, not by necessity:
/ .qetl.run.record
/ calls .qetl.coverage.require_interval and .qetl.coverage.stage_completion reads
/ .qetl.run.current[], so the two reference each other and q's call-time binding
/ resolves both whichever order they load in. Both calls are additionally
/ protected, because several minimal loaders pull in materialisation.q without the
/ rest of the tree and a missing .qetl.run must degrade to an unattributed
/ materialisation rather than an error.
/ .
/ NOT every loader should use this file. tests/q/try_acquire.q,
/ read_checkpoint.q, run_backfill_process.q and smoke_external_metadata.q
/ deliberately load a minimal subset - they are small scripts run as CHILD
/ processes to prove one thing (a lock is exclusive; a real source's metadata
/ matches its declaration), and pulling in the whole tree would be slower and,
/ for the smoke script, wrong: it runs outside a worker on purpose, which is
/ why it reads getenv rather than .qetl.cfg. Those are minimal by intent, not
/ oversights to tidy into full loads.
/ .
/ Assumes src/init.q has already been loaded: the ETL tree uses the library's
/ own namespaces. Run from the repository root, like every other loader here.

\l src/etl/core/status.q
\l src/etl/core/backfill_state.q
\l src/etl/core/log.q
\l src/etl/core/coercion.q
\l src/etl/core/materialisation.q
\l src/etl/core/run.q
\l src/etl/core/io_manager.q
\l src/etl/core/singlestore_odbc.q
\l src/etl/core/heartbeat.q
\l src/etl/core/dag.q
\l src/etl/core/react.q
\l src/etl/generated/pipeline_dag.q
\l src/etl/core/worker_config.q
\l src/etl/core/worker_runtime.q
\l src/etl/core/continuous_state.q
\l src/etl/core/transform.q
\l src/etl/core/source_contract.q
\l src/etl/core/bounded_worker.q
\l src/etl/core/stream_job.q
\l src/etl/core/config_audit.q
\l src/etl/core/normalizer.q
\l src/etl/core/tick.q

/ The invented market the demo's feeds publish and its jobs consume. Before
/ the core declarations, because a job filters on .qsynth.pairs.
\l src/etl/synthetic_market.q

/ Declarations last. Each registers itself on load, so that a declaration and
/ its implementation cannot drift - there is no way to have one without the
/ other.
/ .
/ GLOBBED, not listed. Every .q file in these four directories is loaded, so
/ adding a source, a worker or a streaming job means adding the file and
/ nothing else. What this replaced was twenty-six \l lines - a hand-kept copy
/ of `ls`, which had to be edited in the right place and whose failure mode
/ was a file nobody loaded.
/ .
/ Shared transforms load after sources and before either job family.
/ The DIRECTORY order is load-bearing: .qetl.job.bounded.define looks its source up at
/ define time, so a worker whose source has not loaded aborts with a bare
/ `.qpipe.source.<name> from inside a declaration that looks fine.
/ .
/ Within a directory the order is alphabetical, except the names passed as
/ lead, which load first. Those are the files that read ANOTHER job's table
/ at load time, to build an empty keyed table from its schema:
/ .
/   superbook.q:11        .qpipe.job.market_data.market_data
/   cross_arbitrage.q:47  .qpipe.job.superbook.superbook
/ .
/ Alphabetically superbook sorts AFTER cross_arbitrage, so a plain glob
/ aborts there. A name in lead with no matching file throws rather than being
/ ignored - a stale entry that silently does nothing is how an ordering rots.

etl_load_declarations:{[dir;lead]
    lead:(),lead;
    found:key hsym `$dir;
    found:asc found where found like "*.q";
    if[0=count found; '"etl_load_declarations: no .q files under ",dir];
    leadq:`$string[lead],\:".q";
    missing:leadq except found;
    if[count missing;
        '"etl_load_declarations: ",dir," names ",(", " sv string missing),
            " first, but no such file"];
    {system "l ",x} each (dir,"/"),/:string leadq,found except leadq;
    }

etl_load_declarations["src/etl/sources";`symbol$()];
etl_load_declarations["src/etl/transforms";`symbol$()];
etl_load_declarations["src/etl/workers";`symbol$()];
etl_load_declarations["src/etl/streaming";`market_data`superbook];

/ Local to this file rather than tree API: the load order is init.q's own
/ business, and a helper left in the root namespace is one the enumeration
/ tools in src/namespaces.q would have to account for.
delete etl_load_declarations from `.;
