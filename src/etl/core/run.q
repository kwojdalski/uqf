/ run.q - run identity and materialisation metadata (.qrun). Closes gap 2.3
/ of docs/architecture/pipeline-framework-gaps.md.
/ .
/ WHAT WAS MISSING, AND WHY IT MATTERED
/ .
/ A coverage row recorded dataset, source_version, range, rows_published,
/ recorded_at and superseded_at. Every one of those describes the WINDOW.
/ Nothing described the EXECUTION that produced it, so three questions had no
/ answer:
/ .
/   - which materialisations came from one execution?
/   - what else did that execution produce, or fail to produce?
/   - are these two datasets consistent because they were built together?
/ .
/ The third is the one that bites. Two datasets each covered for the same
/ window look mutually consistent, and they are not if one was built from a
/ source snapshot taken an hour after the other. source_version catches that
/ only when the versions differ; two runs against the same version, hours
/ apart, are indistinguishable without a run identity.
/ .
/ And rows_published was the only fact recorded about a materialisation. A
/ row count says a window produced output; it says nothing about whether the
/ output is the output expected. Dagster attaches arbitrary metadata to a
/ materialisation - min/max of the partition column, null fractions, a
/ checksum, the query that produced it - and that metadata is what makes a
/ materialisation AUDITABLE rather than merely RECORDED.
/ .
/ WHY run_id IS AMBIENT AND NOT A PARAMETER
/ .
/ ETL-09 argues that source_version must be a required parameter, because an
/ optional filter is one a caller forgets. run_id looks similar and is not.
/ source_version is a CHOICE the caller makes - which release am I recording
/ against - and a wrong choice is silent corruption. run_id is a FACT about
/ the executing process, like recorded_at's .z.p, and there is exactly one
/ right answer at any instant. Threading it through five signatures would
/ give every caller the chance to pass the wrong one, which is a failure mode
/ that does not otherwise exist.
/ .
/ So `current[]` is the single source, and .qcov.stage_completion reads it.
/ Outside a run it returns the null guid, and that is recorded honestly: a
/ materialisation not attributable to any run is a real state (a direct call
/ from a test, or a repair by hand) and saying so is better than inventing an
/ identity for it.
/ .
/ THE TABLES ARE APPEND-ONLY, EXCEPT ONE FIELD
/ .
/ `finish` updates the row `begin` wrote - ended_at and status - because a
/ run's outcome is not known when it starts, and appending a second row would
/ make "how many runs were there" ambiguous. That is the only mutation here.
/ Nothing ever deletes.

\d .qrun

/ ---------------------------------------------------------------- SCHEMA

/ The run ledger's columns, in order. One constant in one place: changing the
/ shape is an edit here plus the writer's column list plus require_run_schema,
/ not a hunt through the file. Same discipline as .qcov.schema.
run_schema:`run_id`worker`process`host`pid`started_at`ended_at`status

/ The metadata table's columns, in order.
/ .
/ Long form - one row per fact - rather than one column per kind of fact. The
/ whole point of gap 2.3's second half is that the metadata a pipeline wants
/ to attach is NOT known in advance: a row count, a min/max, a null fraction,
/ a checksum, the query text. A wide table would need a schema migration per
/ new kind of fact, which is precisely what stops anyone recording one.
/ .
/ The columns are `label` and `text`, not `key` and `value`, because `key`
/ and `value` are q builtins - a column named `value` reads fine in qSQL and
/ then bites the first person who writes a lambda over the table.
/ .
/ `text` is a string rather than a general value, because these facts are
/ heterogeneous by nature and this table's job is to preserve them for a
/ READER, not to compute on them. A general column would let two rows with
/ the same label disagree about that label's type, which is worse for a
/ reader than everything being text.
meta_schema:`run_id`dataset`range_from`range_to`label`text`recorded_at

/ A run in flight has not ended. Same sentinel discipline as .qcov's
/ still_current: an ended_at far in the future satisfies every "ended before
/ x" comparison by arithmetic, so no read needs a null branch.
not_ended:0Wp

/ The status of a run that has begun and not reported an outcome.
/ .
/ A run that dies without calling `finish` keeps this status forever, and
/ that is the intended reading: `running` on a run whose process is gone
/ means "this execution was interrupted", which is exactly what a reader
/ needs to find. Inventing a `crashed` status would require someone to detect
/ the crash, and nothing here can.
in_flight:`running

/ ---------------------------------------------------------------- TABLES

/ Create the run ledger if absent.
/ @return the ledger table name
/ @eg .qrun.init_runs[]
init_runs:{[]
    if[not `etl_runs in tables `.;
        `etl_runs set ([] run_id:`guid$(); worker:`symbol$(); process:`symbol$();
            host:`symbol$(); pid:`int$(); started_at:`timestamp$();
            ended_at:`timestamp$(); status:`symbol$())];
    `etl_runs}

/ Create the materialisation metadata table if absent.
/ @return the metadata table name
/ @eg .qrun.init_meta[]
init_meta:{[]
    if[not `etl_run_meta in tables `.;
        `etl_run_meta set ([] run_id:`guid$(); dataset:`symbol$();
            range_from:`timestamp$(); range_to:`timestamp$();
            label:`symbol$(); text:(); recorded_at:`timestamp$())];
    `etl_run_meta}

/ The root run table. Exists so no read below names `etl_runs` bare - inside
/ \d .qrun a bare name resolves to .qrun.etl_runs, which does not exist, and
/ the read would fail at the point of use rather than here. Same reason
/ .qcov.ledger exists.
runs:{[] value `etl_runs}

/ The root metadata table, for the same reason.
/ .
/ NOT named `meta`: that is a q builtin, and shadowing it inside this
/ namespace would break every use of `meta` on a table in this file.
meta_table:{[] value `etl_run_meta}

/ ------------------------------------------------------------- VALIDATION

/ Check a run ledger this process did not create against the shape above.
/ .
/ Same asymmetry .qcov.attach draws: a table we just built matches by
/ construction and checking it would only ever confirm itself; a table
/ someone else built is EVIDENCE, and must be validated before a read is
/ trusted.
/ @return 1b when the live shape matches
/ @throws error naming the difference
/ @eg .qrun.require_run_schema[]
require_run_schema:{[]
    live:exec c from 0!meta value `etl_runs;
    absent:run_schema where not run_schema in live;
    extra:live where not live in run_schema;
    if[count absent;
        '"require_run_schema: etl_runs is missing ",(", " sv string absent),
         " - built to a different shape, and reads here would return nulls"];
    if[count extra;
        '"require_run_schema: etl_runs has unexpected ",(", " sv string extra)];
    1b}

/ Attach to both tables, validating a shape this process did not create.
/ .
/ The same create-if-absent-and-verify contract .qcov.attach offers, and the
/ function a worker's init should call. The asymmetry is the point: a table
/ we just built matches by construction, so checking it would only ever
/ confirm itself; a table another process built is evidence, and is validated
/ before a read is trusted.
/ .
/ Niladic and idempotent deliberately - scripts/export_contract_surface.q
/ discovers this tree's tables by calling every niladic `attach` in an owned
/ namespace, so a table reachable only through init_runs/init_meta would be
/ missing from the contract surface and a reconciliation would not compare it.
/ .
/ NOT YET PERSISTED, unlike .qcov's ledger. These two tables still die with
/ the process, so `history` and `unfinished` describe this process only. That
/ is a smaller gap than coverage's was - nothing decides whether to re-fetch
/ based on a run row - but it is a gap, and the fix is the same shape:
/ ledger_path/persist/reload under .qcov.with_lock.
/ @return the two table names
/ @eg .qrun.attach[]
attach:{[]
    existed:`etl_runs in tables `.;
    init_runs[];
    if[existed; require_run_schema[]];
    init_meta[];
    `etl_runs`etl_run_meta}

/ ---------------------------------------------------------------- IDENTITY

/ The run currently in flight in THIS process, or the null guid.
/ .
/ Process-local by construction. A run identifies one execution, an execution
/ happens in one process, so a second process reading this would be asking
/ the wrong question - it should read `etl_runs`, which is shared.
current_run:0Ng

/ The in-flight run's id, or the null guid outside a run.
/ .
/ Callers recording a fact should use this and record what it returns,
/ INCLUDING the null. See this file's header on why a null run_id is an
/ honest answer rather than a missing one.
/ @return the current run id, or 0Ng
/ @eg .qrun.current[]
current:{[] current_run}

/ Is a run in flight?
/ @return 1b when current[] would return a real id
/ @eg .qrun.is_running[]
is_running:{[] not null current_run}

/ The current run id, refusing to answer outside a run.
/ .
/ For a caller whose work is meaningless without a run to attribute it to -
/ `record` below. Deliberately distinct from `current`, which answers with a
/ null because coverage genuinely can be staged outside a run.
/ @return the current run id
/ @throws error when no run is in flight
/ @eg .qrun.require_current[]
require_current:{[]
    if[not is_running[];
        '"require_current: no run in flight - begin[] one before recording against it"];
    current_run}

/ ---------------------------------------------------------------- LIFECYCLE

/ Mint a run id.
/ .
/ NOT `first 1?0Ng`, which is the obvious way to write this and is wrong
/ here. q seeds its random state identically at every process start, so
/ `1?0Ng` yields the SAME guid in every fresh process - measured, not
/ assumed: three separate interpreters each returned
/ 8c6b8b64-6815-6084-0a3e-178401251b68 as their first.
/ .
/ For a process-local id that would be harmless. `etl_coverage` is persisted
/ to disk and reloaded by every worker's attach, so it IS shared across
/ processes - two backfill processes would stamp different executions with
/ one identity and `materialisations_of` would merge them. (`etl_runs` is
/ still process-local; see attach's note.)
/ That is worse than the gap it closes: absent attribution is visibly absent,
/ while wrong attribution reads as correct.
/ .
/ Derived from host, pid, wall clock and nanosecond counter instead, so
/ uniqueness comes from things that actually differ between processes rather
/ than from a generator that does not.
/ @return a fresh run id
/ @eg .qrun.mint[]
mint:{[]
    hex:raze string md5 raze string (.z.h;.z.i;.z.p;.z.n);
    "G"$ "-" sv (0 8 12 16 20) _ hex}

/ Private: this process's TorQ name, or the null symbol outside TorQ.
/ .
/ Read through a protected eval because .proc is TorQ's and this file is unit
/ tested outside a TorQ process, where .proc does not exist at all. An
/ unwrapped read would make every test here depend on a running stack - the
/ same wrapping .qwrt does for .servers.SERVERS.
proc_name:{[] @[value;`.proc.procname;`]}

/ Begin a run, and make it current.
/ .
/ The row is written at once rather than at `finish`, so a run that dies
/ mid-flight still leaves one. A ledger that recorded only runs that finished
/ would be blind to exactly the executions a reader most wants to find.
/ @param worker the worker this run executes, e.g. `demo_deals_backfill
/ @return the new run id
/ @throws error when a run is already in flight in this process
/ @eg .qrun.begin[`demo_deals_backfill]
begin:{[worker]
    if[is_running[];
        '"begin: a run is already in flight in this process - finish or release it first"];
    init_runs[];
    id:mint[];
    `etl_runs insert (id;worker;proc_name[];.z.h;.z.i;.z.p;not_ended;in_flight);
    current_run::id;
    id}

/ Close the current run with an outcome.
/ .
/ Updates the row `begin` wrote rather than appending a second one, so "how
/ many runs were there" has one answer. This is the only mutation in the file.
/ @param status the outcome, e.g. `completed or `failed
/ @return the run id that was closed
/ @throws error when no run is in flight
/ @eg .qrun.finish[`completed]
finish:{[status]
    id:require_current[];
    init_runs[];
    / Both locals are renamed before the qSQL below: a bare `id` in the where
    / clause would resolve to the run_id column and a bare `status` to the
    / status column, each comparing a column to itself and matching every
    / row. Same trap .qcov.valid_at documents.
    target:id;
    outcome:status;
    `etl_runs set update ended_at:.z.p, status:outcome from runs[] where run_id=target;
    current_run::0Ng;
    id}

/ Abandon the current run without recording an outcome.
/ .
/ For a caller that must clear process-local state - a test's teardown -
/ without claiming the run ended. The row keeps `running`, which is the
/ truth: nothing observed it finish.
/ @return the run id that was released, or 0Ng when none was in flight
/ @eg .qrun.release[]
release:{[]
    id:current_run;
    current_run::0Ng;
    id}

/ ---------------------------------------------------------------- METADATA

/ Private: one metadata value as text.
/ .
/ -3! rather than `string`, because string on a list or dict produces a
/ nested result that does not fit one cell, while -3! always yields a single
/ readable string. Two types bypass it: a string passes through unchanged so
/ it does not acquire a layer of quotes on every round trip, and a symbol
/ atom is stringified plainly, because -3! would render `v1 as "`v1" and the
/ backtick is noise to every reader of this column.
as_text:{[v] $[10h=type v; v; -11h=type v; string v; -3!v]}

/ Attach metadata to one materialisation.
/ .
/ Keyed by the run AND the window, because the same window materialised by
/ two runs has two sets of facts about it, and a reader comparing them is
/ doing exactly what run identity exists to allow.
/ .
/ Values are stringified here rather than at each call site, so a caller
/ passes the natural q value (a long, a timestamp, a float) and the table
/ stays uniform.
/ @param dataset the dataset materialised
/ @param range_from window start
/ @param range_to window end, exclusive
/ @param facts a dict of symbol labels to values
/ @return the number of facts recorded
/ @throws error when no run is in flight, facts is not a symbol-keyed dict,
/   or the interval is empty/reversed
/ @eg .qrun.record[`demo_deals;2026.09.13D00:00;2026.09.14D00:00;(enlist `rows)!enlist 42]
record:{[dataset;range_from;range_to;facts]
    id:require_current[];
    if[not 99h=type facts;
        '"record: facts must be a dictionary of symbol labels to values"];
    ks:key facts;
    if[not 11h=abs type ks;
        '"record: facts labels must be symbols"];
    .qcov.require_interval[range_from;range_to];
    init_meta[];
    n:count ks;
    `etl_run_meta insert (n#id;n#dataset;n#range_from;n#range_to;
                          ks;as_text each value facts;n#.z.p);
    n}

/ ------------------------------------------------------------------- READ

/ Every run recorded, newest first.
/ @return the run ledger, newest first
/ @eg .qrun.history[]
history:{[]
    init_runs[];
    `started_at xdesc runs[]}

/ One run's row.
/ @param id the run id
/ @return that run's row, or an empty table when the id is unknown
/ @eg .qrun.of_run[.qrun.current[]]
of_run:{[id]
    init_runs[];
    target:id;
    select from runs[] where run_id=target}

/ The runs still in flight - begun, never finished.
/ .
/ On a shared ledger this includes runs whose process has since died, which
/ is the intended reading: an interrupted execution is one a reader needs to
/ find, and nothing else records it.
/ @return the unfinished runs
/ @eg .qrun.unfinished[]
unfinished:{[]
    init_runs[];
    running:in_flight;
    select from runs[] where status=running}

/ Every fact recorded against one run.
/ @param id the run id
/ @return the metadata rows for that run
/ @eg .qrun.facts_of[.qrun.current[]]
facts_of:{[id]
    init_meta[];
    target:id;
    select from meta_table[] where run_id=target}

/ Every fact recorded about one materialisation, across all runs.
/ .
/ The cross-run view: one window materialised twice, with both sets of facts
/ side by side. That comparison is the question run identity was added to
/ make askable.
/ @param dataset the dataset
/ @param range_from window start
/ @param range_to window end, exclusive
/ @return the metadata rows for that window, from any run
/ @eg .qrun.facts_about[`demo_deals;2026.09.13D00:00;2026.09.14D00:00]
facts_about:{[dataset;range_from;range_to]
    init_meta[];
    ds:dataset; rf:range_from; rt:range_to;
    select from meta_table[] where dataset=ds, range_from=rf, range_to=rt}

\d .
