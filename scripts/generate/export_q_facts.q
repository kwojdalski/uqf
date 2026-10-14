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

/ Where each bounded worker's dataset is read by time and by instrument (#1363).
/ A dataset is browsable through getdata only if scripts/torqconfig/dataaccess/
/ tableproperties.csv has a row for it, and Python (scripts/generate/
/ tableproperties.py) writes that row. The columns come from the transform's
/ output - the shape the worker publishes - and the time column is `time`
/ where the output has one, else the column the worker windows on.
datasets:{[] exec distinct dataset from .qetl.job.bounded.worker_cfg}
dataset_columns:{[d] cols .qetl.transform.output_schema first exec transform from .qetl.job.bounded.worker_cfg where dataset=d}
dataset_time:{[d] $[`time in dataset_columns d; `time; first exec window_column from .qetl.job.bounded.worker_cfg where dataset=d]}
dataset_instrument:{[d] $[`sym in dataset_columns d; `sym; `]}

/ name -> a list of symbols, in q's own order, or a string.
facts:(!) . flip (
    (`IO_STRATEGIES;        .qetl.io.strategies);
    (`RUN_MODES;            .qetl.job.bounded.runtime.modes);
    (`LOG_LEVELS;           .qetl.log.levels);
    (`ETL_RUNS_COLUMNS;     cols .qetl.run.init_runs[]);
    (`ETL_RUN_META_COLUMNS; cols .qetl.run.init_meta[]);
    / The status file every worker writes and four readers poll (#1362).
    (`STATUS_STATES;          .qetl.status.status_states);
    (`STATUS_TERMINAL_STATES; .qetl.status.terminal_states);
    / The states a reader may call success: the ones the worker's process
    / exits 0 for, so a reader and the exit code cannot disagree (#609).
    (`STATUS_SUCCESS_STATES;  .qetl.status.status_states where 0i=.qetl.job.bounded.exit_code each .qetl.status.status_states);
    (`STATUS_FILE_PREFIX;     .qetl.status.file_prefix);
    (`STATUS_FILE_SUFFIX;     .qetl.status.file_suffix);
    / The datasets bounded workers write, keyed by name (#1363).
    (`BOUNDED_DATASET_TIME_COLUMNS;       (!) . (asc datasets[]; string dataset_time each asc datasets[]));
    (`BOUNDED_DATASET_INSTRUMENT_COLUMNS; (!) . (asc datasets[]; string dataset_instrument each asc datasets[])))

\d .

-1 .j.j {$[11h=type x; string x; x]} each .qfacts.facts;
exit 0
