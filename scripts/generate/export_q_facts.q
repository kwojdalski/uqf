/ export_q_facts.q - print, as JSON, the q facts uqs's Python reads (#818).
/ .
/ uqs runs without starting q, so it cannot ask q these at runtime. It used
/ to carry hand copies, each pinned to q's source text by its own regex; now
/ scripts/generate/q_facts.py runs this, writes
/ python/uqs/src/uqs/generated/q_facts.py from it, and checks it in a hook.
/ Values, read from the loaded tree - never parsed out of source text.
/ .
/ Run from the repository root: q scripts/generate/export_q_facts.q -q

\l src/init.q
\l src/etl/init.q

\d .qfacts

/ name -> a list of symbols, in q's own order.
facts:(!) . flip (
    (`IO_STRATEGIES;        .qetl.io.strategies);
    (`RUN_MODES;            .qetl.job.bounded.runtime.modes);
    (`LOG_LEVELS;           .qetl.log.levels);
    (`ETL_RUNS_COLUMNS;     cols .qetl.run.init_runs[]);
    (`ETL_RUN_META_COLUMNS; cols .qetl.run.init_meta[]))

\d .

-1 .j.j string each .qfacts.facts;
exit 0
