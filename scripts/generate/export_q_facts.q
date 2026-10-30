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

/ q's column types and attributes (#1785): the vocabulary uqs's attachment
/ check, schema view, plant metadata, scaffold and the browser's catalog share.
/ q has no list of its type NAMES, so they are written here once - and each is
/ held to q: its cast must make the type whose .Q.t character it is paired
/ with, and every character .Q.t has (the space, a general list, aside) must
/ be named exactly once. The attributes are what `attr` reports for each.
type_names:`boolean`guid`byte`short`int`long`real`float`char`symbol`timestamp`month`date`datetime`timespan`minute`second`time;
type_chars:{[n] .Q.t type n$()} each type_names;
if[not (asc type_chars)~asc distinct .Q.t except " ";
    -2 "export_q_facts: the type names cast to \"",type_chars,"\", not every type .Q.t has (\"",(distinct .Q.t except " "),"\")";
    exit 1];
attributes:attr each (`s#0 1;`u#0 1;`p#0 0 1;`g#0 1);

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
    (`BOUNDED_DATASET_INSTRUMENT_COLUMNS; (!) . (asc datasets[]; string dataset_instrument each asc datasets[]));
    / Names q computes and the browser and `uqs` repeat (#1368).
    (`CFG_SOURCES;            .qetl.cfg.sources);
    (`CFG_ENV_LAYER;          .qetl.cfg.env_layer);
    (`CFG_OVERRIDE_LAYER;     .qetl.cfg.override_layer);
    (`CFG_ENV_PREFIX;         .qetl.cfg.env_prefix);
    (`SOURCE_CREDENTIAL_PREFIX; .qetl.source.credential_prefix);
    (`FIXTURE_TAG;            .qetl.source.fixture_tag);
    (`REVISION_TAG;           .qetl.job.bounded.runtime.revision_tag);
    (`Q_TYPE_NAMES;           (`$enlist each type_chars)!type_names);
    (`Q_TYPE_CHARS;           type_names!`$enlist each type_chars);
    (`Q_ATTRIBUTES;           attributes))

\d .

-1 .j.j {$[11h=type x; string x; x]} each .qfacts.facts;
exit 0
