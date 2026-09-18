/ coverage.q - the append-only completeness ledger and its interval
/ arithmetic (.qcov).
/ .
/ Implements requirements ETL-07 to ETL-11 of docs/reference/etl-framework-requirements.md.
/ Those IDs are the REQUIREMENTS document's; the question bank uses an
/ overlapping E-nn scheme for source-adapter questions, so question-bank
/ answers are named as such below.
/ .
/ The two things most easily got wrong here, both counter-intuitive and both
/ stated explicitly in the canonical document:
/ .
/   1. Coverage is RECORDED, not derived (ETL-07). It is not computed from the
/      target data; a completion event is staged for every completed bounded
/      window, INCLUDING AN EMPTY ONE. The empty-window rule is what makes
/      "we ran and there was nothing" distinguishable from "we never ran" -
/      and a derived ledger cannot express that difference at all.
/ .
/   2. source_version is not optional and not advisory (ETL-09, ETL-10).
/      Coverage recorded under one source release says nothing about
/      another, so every read filters on it and intervals from different
/      versions are NEVER merged to satisfy a dependency.

\d .qcov

/ ---------------------------------------------------------------- SCHEMA

/ DECIDED, and this tree is the authority (issue #60, closed 2026-09-16).
/ .
/ This block used to read "ASSUMED, NOT VERIFIED", because etl_coverage
/ existed only in a canonical tree that could not be reached from here. That
/ premise is gone: this repository is the primary lineage now and canonical
/ froze canonical, so there is no other schema to verify against. The shape
/ below IS the schema, and `scripts/dev/verify_coverage_schema.q -local 1`
/ confirms the code and the table agree.
/ .
/ ON THE PARTITION KEY, WHICH IS THE PART WORTH READING
/ .
/ This column used to be absent, and the block here argued for its absence:
/ nothing backfilled one partition at a time, so a partition column would
/ hold one value per dataset and widen every signature for nothing. The
/ argument named the condition that would overturn it - "a worker that
/ backfills per partition, per sym, per venue, per region" - and that is the
/ condition issue #185 raised. A backfill could not be PARALLELISED, because
/ .qbw.define had to refuse two workers on one dataset, because their
/ coverage would wrongly compose. For a framework whose principal job is
/ backfill, that was the ceiling worth lifting.
/ .
/ So `partition` is now the fourth dimension of the coverage key, and it is a
/ REQUIRED parameter on write AND on read, for the reason source_version is
/ (ETL-09): an optional filter is one a caller forgets, and forgetting THIS
/ one reports a gap-ridden range as complete. It is required by ARITY - every
/ signature below takes it positionally - so omitting it is an arity error at
/ the call site, never a silent default.
/ .
/ NO READ UNIONS ACROSS PARTITIONS. Every read filters `partition=part` with
/ equality, so coverage of `EURUSD says nothing about `USDJPY and cannot be
/ composed into it. That is not a convention to remember; it is what equality
/ does, and it is the whole reason the column exists rather than being
/ recovered from the dataset name by string surgery.
/ .
/ THE UNPARTITIONED SENTINEL is the null symbol, `. A dataset with no
/ partition dimension records ` and reads `, and because the filter is
/ equality, a read for ` does NOT match partitioned rows and a read for a
/ partition does not match ` rows. The two populations cannot bleed into one
/ another in either direction.
/ .
/ The sentinel is deliberately NOT refused the way a null source_version is.
/ For source_version a null means "the caller forgot"; here it is a legitimate
/ value meaning "this dataset has no partition dimension", and the protection
/ against forgetting is arity, not a null check.
/ .
/ ON run_id, WHICH IS THE COLUMN THAT DID GET ADDED
/ .
/ Gap 2.3 of the pipeline-framework assessment: every column above describes
/ the WINDOW, and none described the EXECUTION, so "which materialisations
/ came from one run" and "were these two datasets built together" had no
/ answer. run_id is that column. It differs from `partition` above in exactly
/ the way that matters - it does NOT partition the data, so no read here
/ filters on it and no existing query can aggregate across it wrongly.
/ Adding it cannot make a gap-ridden range report as complete; it can only
/ add attribution that was absent.
/ .
/ It is written from .qrun.current[] rather than passed in - see that file's
/ header for why an ambient fact is not the same as ETL-09's required choice.
/ .
/ A LEDGER WRITTEN BEFORE THIS COLUMN EXISTED will now fail require_schema
/ with "missing run_id". That is deliberate and is the whole point of the
/ guard: this file's reads would return nulls for a column the table does not
/ have. `scripts/dev/verify_coverage_schema.q` prints the remedy.
/ .
/ Deliberately one constant in one place: changing it should be an edit here
/ plus the writer's column list, not a hunt through the file.
schema:`dataset`partition`source_version`range_from`range_to`rows_published`recorded_at`superseded_at`run_id

/ Create the ledger if absent.
/ .
/ Append-only in the sense ETL-07 means: no row is ever deleted, and no fact
/ about a window is edited once written. `supersede` is the one writer that
/ updates, and it only ever stamps `superseded_at` on a row whose claim has
/ been withdrawn - the claim itself stays readable at any earlier as-of
/ This comment used to say "nothing in this file updates or deletes a
/ row", which supersede made false the moment it landed here.
/ .
/ The table lives at the ROOT, not in .qcov, because it is a published
/ database table like quotes/trades/position - it flows through the
/ tickerplant to rdb/hdb and is read by processes that know nothing about
/ this namespace.
/ .
/ That has a consequence worth stating, because it is a silent-wrong-answer
/ trap: inside `\d .qcov` a bare `etl_coverage` resolves to
/ `.qcov.etl_coverage`, NOT the root table. Backtick forms
/ (`etl_coverage set / insert) are absolute and hit the root; bare reads are
/ not. Every read below therefore goes through ledger[] rather than naming
/ the table directly.
/ Sentinel for "this claim has not been superseded". 0Wp, not 0Np.
/ .
/ A null would make the validity test need a special case - `as_of<0Np` is
/ false in q, so every current row would drop out of an as-of read and the
/ ledger would report a fully published range as empty. Infinity makes
/ `as_of<0Wp` true by arithmetic, so the current rows need no branch at all.
/ That is the classic bitemporal encoding and it is chosen here specifically
/ because this repository keeps being bitten by null comparisons that return
/ a plausible answer instead of erroring.
still_current:0Wp

/ Create the root ledger table if it is absent, and return its name.
/ .
/ Inside \d .qcov a bare `etl_coverage` resolves to `.qcov.etl_coverage`, NOT
/ the root table. Backtick forms (`etl_coverage set / insert) are absolute and
/ hit the root; bare reads are not. Every read below therefore goes through
/ ledger[] rather than naming the table directly.
/ @return the ledger table name
/ @eg .qcov.init_ledger[]
init_ledger:{[]
    if[not `etl_coverage in tables `.;
        `etl_coverage set ([] dataset:`symbol$(); partition:`symbol$();
            source_version:`symbol$();
            range_from:`timestamp$(); range_to:`timestamp$();
            rows_published:`long$(); recorded_at:`timestamp$();
            superseded_at:`timestamp$(); run_id:`guid$())];
    `etl_coverage}

/ The root ledger table. Exists so no read below names `etl_coverage` bare -
/ see init_ledger's note on namespace resolution.
ledger:{[] value `etl_coverage}

/ Attach to the ledger, validating its shape only if it already existed.
/ .
/ This is the function a worker's init should call, and it exists because
/ `require_schema` alone was the wrong shape for a caller: a worker cannot
/ tell whether it is the process that created the table, so it either
/ validated a table it had just built (trivially true, and therefore
/ pointless) or skipped the check entirely. Making that distinction here
/ rather than at every call site means no worker has to get it right.
/ .
/ The asymmetry is the whole point:
/ .
/   ledger absent   -> we create it, so it matches by construction. Nothing
/                      to check, and checking would only ever confirm
/                      itself.
/   ledger present  -> someone else created it, so its shape is EVIDENCE
/                      rather than assumption, and it must be validated
/                      before a single read is trusted.
/ .
/ That second case is why this exists. A ledger another process created may
/ have a shape this file does not expect - a SECOND partitioning dimension,
/ say - and then
/ every read here aggregates across whatever that column distinguishes, so a
/ range covered for one value of it reports as COMPLETE for all of them,
/ with no error, because every row found is valid.
/ @return the ledger table name
/ @throws error, via require_schema, when an existing ledger has a
/   different shape
/ @eg .qcov.attach[]
attach:{[]
    existed:`etl_coverage in tables `.;
    init_ledger[];
    if[existed; require_schema[]];
    / Pick up what earlier processes recorded. THIS is what makes ETL-07's
    / "durable cross-process completeness" true rather than aspirational: the
    / ledger used to be an in-memory table that died with the worker, so a
    / bounded worker - which runs a range and exits - took its own coverage
    / with it and ETL-13's skip-what-is-covered could never fire across runs.
    / reload validates the shape it finds, so an older file is refused by
    / name rather than read.
    reload[];
    `etl_coverage}

/ Refuse to trust a ledger whose shape is not the one this file declares.
/ .
/ Converts a SILENT wrong answer into a loud refusal. A ledger carrying an
/ extra column that distinguishes rows - a venue, a region, a tier this tree
/ does not know about - makes every read here aggregate across it, so a range
/ covered for one value reports as COMPLETE for all of them, and nothing
/ errors because every row found is valid. A consumer then reads a gap-ridden
/ range believing it whole. `partition` is the one such column this tree DOES
/ know about, which is exactly why it is declared and filtered on rather than
/ tolerated.
/ .
/ With #60 closed this is no longer a guard against an unknown canonical
/ shape; it is a drift guard. The shape is decided (see the header), and this
/ catches a ledger some other process built to a different one.
/ .
/ Call it from a worker's init when attaching to a ledger this process did
/ not create - .qbw.init does, via attach. NOT called from init_ledger: a
/ table this file just built trivially matches, so checking there would only
/ ever confirm itself.
/ .
/ `scripts/dev/verify_coverage_schema.q` is the same check as a standalone
/ command, against a local table or a remote handle.
/ @return 1b when the live shape matches
/ @throws error naming the difference, and what it would silently do
require_schema:{[]
    live:exec c from 0!meta ledger[];
    missing:schema where not schema in live;
    extra:live where not live in schema;
    if[count missing;
        '"require_schema: etl_coverage is missing ",(", " sv string missing),
         " - this ledger was built to a different shape, and reads here would fail or return nulls"];
    if[count extra;
        / Kept short deliberately: q truncates a thrown string at 255 bytes,
        / and the consequence is the part worth keeping. The long form lives
        / in this function's own comment above.
        '"require_schema: etl_coverage has unexpected column(s) ",
         (", " sv string extra),
         " - an extra column that distinguishes rows makes reads aggregate across it and report a gap-ridden range as complete"];
    1b}

/ -------------------------------------------------------------- INTERVALS

/ Validate a half-open [from;to) interval (ETL-08).
/ .
/ Rejecting empty and reversed intervals at construction is what stops a
/ zero-width backfill reporting success: a window where from=to covers
/ nothing, so recording it as coverage would claim completeness for no data.
/ @throws error if the interval is empty or reversed
require_interval:{[range_from;range_to]
    if[not range_to>range_from;
        '"require_interval: must be non-empty and forward-going, got [",
         string[range_from],"; ",string[range_to],")"];
    (range_from;range_to)}

/ Merge overlapping and boundary-adjacent intervals, leaving real gaps
/ (ETL-08).
/ .
/ "Only at their common boundary" is the load-bearing phrase. With half-open
/ intervals [Mon;Tue) and [Tue;Wed) are contiguous with nothing between
/ them, so they compose; [Mon;Tue) and [Wed;Thu) leave Tuesday uncovered and
/ MUST NOT be merged. A naive "merge anything close" implementation silently
/ reports a missing day as covered, which is the failure this rule exists to
/ prevent.
/ @param intervals a table with range_from and range_to columns
/ @return a table of composed intervals, ordered by range_from
/ @eg .qcov.compose[([] range_from:2026.09.11D00:00 2026.09.12D00:00; range_to:2026.09.12D00:00 2026.09.13D00:00)]
compose:{[intervals]
    if[0=count intervals; :intervals];
    sorted:`range_from xasc 0!intervals;
    / fold: extend the open interval when the next one touches it, else
    / start a new one. `touches` is <=, not <, precisely so a shared
    / boundary composes.
    step:{[acc;row]
        $[0=count acc;
            enlist row;
          row[`range_from]<=last[acc]`range_to;
            (-1_acc),enlist @[last acc;`range_to;:;max (last[acc]`range_to;row`range_to)];
            acc,enlist row]};
    result:step/[();sorted];
    $[0=count result; 0#sorted; (0#sorted) upsert result]}

/ The sub-ranges of [range_from;range_to) that `covered` does not cover.
/ An empty result means fully covered.
/ @param range_from start of the requested range
/ @param range_to end of the requested range, exclusive
/ @param covered a table of intervals, need not be composed
/ @return a table of uncovered intervals
/ Parameters are from_ts/to_ts, NOT range_from/range_to, deliberately: those
/ are column names on the interval tables below, and a qSQL where-clause
/ comparing a column to a parameter of the same name compares the column to
/ itself and is always true. forwards.q's leg_book_as_of and
/ microstructure.q's quotes_for_sym both name around this same trap.
/ .
/ Vectorised rather than looped. An earlier version used a while loop with
/ `continue`, which is not q - it is a C-ism that parses as an undefined
/ name and aborted the whole file's load, leaving .qcov empty while
/ `system"l"` still reported success.
/ @param from_ts start of the requested range
/ @param to_ts end of the requested range, exclusive
/ @param covered a table of intervals, need not be composed
/ @return a table of uncovered sub-ranges; empty means fully covered
gaps:{[from_ts;to_ts;covered]
    require_interval[from_ts;to_ts];
    merged:compose covered;
    if[0=count merged;
        :([] range_from:enlist from_ts; range_to:enlist to_ts)];
    / overlapping intervals only, each clipped to the requested range
    rel:select range_from:from_ts|range_from, range_to:to_ts&range_to
        from merged where range_to>from_ts, range_from<to_ts;
    if[0=count rel;
        :([] range_from:enlist from_ts; range_to:enlist to_ts)];
    / a gap sits between one interval's end and the next one's start, plus
    / before the first and after the last. Zip those boundaries and keep the
    / positive-width ones.
    starts:from_ts,exec range_to from rel;
    ends:(exec range_from from rel),to_ts;
    candidates:([] range_from:starts; range_to:ends);
    select from candidates where range_to>range_from}

/ ---------------------------------------------------------------- RECORD

/ --------------------------------------------------------- PERSISTENCE

/ Where the ledger lives between processes.
/ .
/ Beside the checkpoints, in .qbfstate.lock_dir[], and that is the whole
/ argument for this location: save_checkpoint ALREADY requires that directory
/ to exist and be writable, so persisting here adds no environmental
/ dependency a worker did not already have. A process that can checkpoint can
/ persist; a process that cannot fails the same way it already failed.
/ @return the ledger file path
/ @eg .qcov.ledger_path[]
ledger_path:{[] (.qbfstate.lock_dir[]),"/etl_coverage"}

/ Private: this ledger's mutex path. Distinct from .qbfstate's per-worker
/ INSTANCE lock, which guards something else entirely (one instance of one
/ worker) and deliberately REFUSES rather than waits. A ledger write is a
/ short critical section several workers legitimately contend for, so that
/ one waits - see .qbfstate.with_file_lock.
lock_path:{[] .qbfstate.file_lock_path `etl_coverage}

/ Run `f . args` holding this ledger's mutex.
/ .
/ A thin delegation: the mutex itself lives in .qbfstate, which already owns
/ the lock directory and the mkdir primitive, so .qrun can guard its own
/ tables with the same implementation rather than a second copy of it.
/ @param f the function to run under the lock
/ @param args its arguments, as a list
/ @return whatever f returns
/ @eg .qcov.with_lock[{[n] n};enlist 1]
with_lock:{[f;args] .qbfstate.with_file_lock[`etl_coverage;f;args]}

/ Write the in-memory ledger to disk.
/ .
/ q binary via `set`, not CSV: the ledger carries a guid, a long, four
/ timestamps and the 0Wp sentinel, and a text format would have to re-parse
/ every one of them on the way back. Round-tripping through `set`/`get`
/ preserves types exactly, which matters most for still_current - a
/ sentinel that came back as a null would silently make every superseded
/ read wrong.
/ .
/ Call only under with_lock.
/ @return the path written
persist:{[] (hsym `$ledger_path[]) set ledger[]; ledger_path[]}

/ Replace the in-memory ledger with the one on disk, if there is one.
/ .
/ Validated on the way in. A file written by an older version of this tree
/ has an older shape, and require_schema is exactly the guard for that - so a
/ ledger missing run_id is refused by name here rather than read and silently
/ aggregated across a column it does not have.
/ .
/ No file is not an error: a first run has nothing to reload.
/ @return the ledger table name
/ @eg .qcov.reload[]
reload:{[]
    p:hsym `$ledger_path[];
    if[()~key p; :init_ledger[]];
    `etl_coverage set get p;
    require_schema[];
    `etl_coverage}

/ Private: the run this process is executing, or the null guid.
/ .
/ Protected rather than a bare .qrun.current[] call, because run.q is not a
/ load-time dependency of this file and several minimal loaders
/ (tests/q/read_checkpoint.q and friends) pull in coverage.q alone. Without
/ the wrapper, staging a completion in one of those would fail on a missing
/ namespace rather than record a run-less materialisation, which is the
/ honest outcome there.
/ .
/ @[f;::;e] rather than .[f;();e]: applying a niladic through the dot form
/ passes no argument list q can match, and the error handler swallows the
/ resulting rank error instead of the failure it was written for.
current_run:{[] @[{.qrun.current[]};::;0Ng]}

/ Stage a completion event for one completed bounded window (ETL-07).
/ .
/ Called for EVERY completed window, including one that published no rows.
/ That is not an oversight to optimise away: an empty window is positive
/ evidence that the range was examined and held nothing, and without it a
/ reader cannot distinguish that from a range never attempted. Recording
/ rows_published=0 is the whole point.
/ .
/ Only ever called AFTER the underlying work is complete (ETL-07), which is
/ the same publish-before-acknowledge ordering ETL-05 requires of cursors.
/ @param dataset the dataset completed, e.g. `markouts
/ @param partition the slice of that dataset this window covers - `EURUSD, a
/   venue, a region - or ` when the dataset has no partition dimension.
/   Required, and required positionally: see the header on why arity rather
/   than a null check is what stops a caller forgetting it
/ @param source_version the immutable source-release label (ETL-09)
/ @param range_from window start
/ @param range_to window end, exclusive
/ @param rows_published how many rows the window published; 0 is legal and
/   meaningful
/ @return the number of rows now in the ledger
/ @throws error if source_version is null, or the interval is empty/reversed
/ @eg .qcov.stage_completion[`markouts;`EURUSD;`v1;2026.09.13D00:00;2026.09.14D00:00;1234]
stage_completion:{[dataset;partition;source_version;range_from;range_to;rows_published]
    if[null source_version;
        '"stage_completion: source_version must be set - coverage under one source release says nothing about another (ETL-09)"];
    require_interval[range_from;range_to];
    init_ledger[];
    / Reload, insert, persist - all three under the lock, so a second process
    / staging concurrently cannot lose this row by writing a copy of the
    / ledger it read before this insert existed.
    with_lock[{[row]
        reload[];
        `etl_coverage insert row;
        persist[]};
        enlist (dataset;partition;source_version;range_from;range_to;
                "j"$rows_published;.z.p;still_current;current_run[])];
    count ledger[]}

/ ------------------------------------------------------------------ READ

/ Every coverage interval for one dataset at one source release.
/ .
/ source_version is a required parameter rather than an optional filter,
/ because ETL-09 requires consumers to filter on it and an optional filter is
/ one a caller forgets. Intervals from other versions are not returned, so
/ they cannot be merged into this answer (ETL-10).
/ @param dataset the dataset to report on
/ @param source_version the release to report for
/ @return a table of composed intervals
/ Parameters are ds/version, not dataset/source_version: those are column
/ names, and `where dataset=dataset` compares the column to itself and
/ matches every row. Same trap as gaps above.
/ Private: the coverage claims that were true AT `as_of`.
/ .
/ A row is valid from when it was recorded until it was superseded, so the
/ test is `recorded_at<=as_of<superseded_at`. Current rows carry
/ `still_current` (0Wp) rather than a null, so they satisfy the upper bound
/ by arithmetic and need no branch - see that constant's note.
/ .
/ Composing AFTER the as-of filter is what makes intervals from different
/ revisions safe to merge: every row reaching `compose` was true at the same
/ instant, so adjacency means what it meant before supersession existed.
/ Filtering after composing would merge a live interval with one that had
/ already been withdrawn.
/ .
/ `partition=part` is equality, never `in` and never omitted, so a read for
/ one partition can see no other partition's rows and a read for the
/ unpartitioned sentinel ` sees only unpartitioned rows. That is what makes
/ "no read unions across partitions" a property of the code rather than a
/ rule someone has to keep.
valid_at:{[ds;part;version;as_of]
    init_ledger[];
    select range_from, range_to from ledger[]
        where dataset=ds, partition=part, source_version=version,
              recorded_at<=as_of, as_of<superseded_at}

/ The composed intervals covered for a dataset and source_version, as
/ understood at `as_of`.
/ @param as_of the instant to answer as of; .z.p for "now"
/ @param partition the slice to report on, or ` for an unpartitioned dataset
/ @eg .qcov.intervals[`demo_deals;`;`v1;.z.p]
intervals:{[ds;part;version;as_of]
    compose valid_at[ds;part;version;as_of]}

/ Is [range_from;range_to) fully covered for this dataset, partition and
/ release?
/ .
/ ONE PARTITION'S ANSWER, never several composed. A range covered for
/ `EURUSD is not covered for `USDJPY, and asking this question without
/ naming a partition would rebuild exactly the bug the column exists to
/ prevent - which is why there is no such overload.
/ @return 1b when there are no gaps
is_covered:{[ds;part;version;as_of;from_ts;to_ts]
    0=count gaps[from_ts;to_ts;intervals[ds;part;version;as_of]]}

/ The uncovered sub-ranges of a requested range, for a caller that wants to
/ narrow its request rather than be refused outright.
missing:{[ds;part;version;as_of;from_ts;to_ts]
    gaps[from_ts;to_ts;intervals[ds;part;version;as_of]]}

/ Admission check: refuse the caller unless the range is fully covered.
/ .
/ ETL-11 requires that local historical reads, INCLUDING this check, go
/ through a gateway addressing both `rdb and `hdb. That matters because
/ completion data moves after EOD: a check bound to an rdb-only handle
/ starts returning false gaps the morning after, for data that is present
/ and simply no longer in memory. Callers must therefore route this read,
/ not run it against a single tier.
/ @throws error naming the missing ranges when not fully covered
require_covered:{[ds;part;version;as_of;from_ts;to_ts]
    m:missing[ds;part;version;as_of;from_ts;to_ts];
    if[count m;
        '"require_covered: ",string[ds],"[",string[part],"] at source_version ",
         string[version]," is not fully published for [",
         string[from_ts],"; ",string[to_ts],") - missing: ",
         ", " sv {"[",string[x`range_from],"; ",string[x`range_to],")"} each m];
    1b}


/ ------------------------------------------------------- SUPERSESSION

/ Every materialisation one execution produced (gap 2.3).
/ .
/ The question run_id was added to answer. A run that published three
/ datasets has three rows here, and a reader comparing two datasets can tell
/ whether they are consistent BECAUSE they were built together, rather than
/ inferring it from two timestamps that happen to be close.
/ .
/ Includes superseded rows deliberately: what a run produced does not change
/ when a later run supersedes it, and hiding the withdrawn rows would make a
/ run that was entirely restated look like a run that did nothing.
/ @param id the run id
/ @return the coverage rows that run staged
/ @eg .qcov.materialisations_of[.qrun.current[]]
materialisations_of:{[id]
    init_ledger[];
    target:id;
    select from ledger[] where run_id=target}

/ Which runs contributed to a dataset's coverage at one release.
/ .
/ The reverse lookup: not "what did this run build" but "what built this".
/ Distinct run ids on one dataset and version mean the coverage was assembled
/ across several executions, which is normal for a backfill run in slices and
/ is worth being able to see.
/ .
/ Partitioned, like every other read here: "which runs built EURUSD" and
/ "which runs built this dataset" are different questions once a dataset is
/ filled by several workers in parallel, and the second one is the sum of
/ the first over every partition rather than a query of its own.
/ @param ds the dataset
/ @param part the partition, or ` for an unpartitioned dataset
/ @param version the source release
/ @return the distinct run ids, in first-recorded order
/ @eg .qcov.contributing_runs[`demo_deals;`;`v1]
contributing_runs:{[ds;part;version]
    init_ledger[];
    distinct exec run_id from `recorded_at xasc ledger[]
        where dataset=ds, partition=part, source_version=version}

/ Withdraw the coverage claims overlapping [from_ts;to_ts), as of now.
/ .
/ Option A: a restatement does not DELETE the old claim, it closes it.
/ The row stays in the ledger with `superseded_at` set, so a read at an
/ earlier as_of still sees it - which is the whole point of recording a
/ restatement rather than overwriting one. "What did we believe on the 12th"
/ remains answerable after the 13th says otherwise.
/ .
/ Append-only is preserved in the sense that matters: no row is removed and
/ no historical answer changes. What changes is the answer to questions asked
/ from now on, which is exactly what a restatement means.
/ .
/ OVERLAP, not containment: a claim covering [09-01;09-10) is withdrawn by a
/ restatement of [09-05;09-06), because after that restatement the original
/ claim is no longer wholly true and leaving it standing would report the
/ restated days as still covered by the old belief. The caller republishes
/ whatever is still correct; this only withdraws.
/ .
/ WITHDRAWS ONE PARTITION'S CLAIMS, not the dataset's. Restating EURUSD must
/ not withdraw USDJPY's coverage, which is what an unpartitioned supersede
/ would do the moment a dataset is filled by more than one worker - and it
/ would do it silently, leaving the other partitions reading as uncovered
/ until someone republished them.
/ @param ds the dataset
/ @param part the partition being restated, or ` for an unpartitioned dataset
/ @param version the source_version whose claims are being withdrawn
/ @param from_ts inclusive lower bound of the restated range
/ @param to_ts exclusive upper bound
/ @return the number of claims withdrawn
/ @throws error when the interval is not a proper half-open range
/ @eg .qcov.supersede[`demo_deals;`;`v1;2026.09.12D00:00;2026.09.13D00:00]
supersede:{[ds;part;version;from_ts;to_ts]
    require_interval[from_ts;to_ts];
    init_ledger[];
    / Read-modify-write under the lock, like stage_completion. A supersession
    / applied to a stale copy of the ledger would be lost by the next writer,
    / and a withdrawn claim silently coming back is the worst failure this
    / file has.
    with_lock[{[ds;part;version;from_ts;to_ts]
        reload[];
        n:supersede_locked[ds;part;version;from_ts;to_ts];
        persist[];
        n};
        (ds;part;version;from_ts;to_ts)]}

/ Private: the supersession itself, with the lock already held and the ledger
/ already reloaded. Split out so the locked wrapper above reads as what it is
/ rather than burying the interval algebra inside a lambda.
supersede_locked:{[ds;part;version;from_ts;to_ts]
    now:.z.p;
    / `cur` is a LOCAL copy of still_current, not the namespace global.
    / Inside \d .qcov a bare name in a qSQL where-clause does not resolve to
    / the namespace's own global - the same trap this file documents at
    / length for `etl_coverage`, and it throws 'still_current rather than
    / silently matching nothing, which is the better of the two failures.
    cur:still_current;
    / Half-open overlap: two intervals overlap when each starts before the
    / other ends. Both bounds are strict for that reason - [1;2) and [2;3)
    / share an endpoint and do NOT overlap.
    idx:exec i from ledger[]
        where dataset=ds, partition=part, source_version=version,
              superseded_at=cur,
              range_from<to_ts, from_ts<range_to;
    if[0=count idx; :0];
    `etl_coverage set update superseded_at:now from ledger[] where i in idx;
    count idx}

/ Every claim ever made for a dataset and source_version, withdrawn or not.
/ .
/ The audit view. `intervals` answers "what is true", this answers "what was
/ ever said", which is the question a restatement makes worth asking.
/ @param ds the dataset
/ @param part the partition, or ` for an unpartitioned dataset
/ @param version the source release
/ @return the ledger rows for this dataset/partition/version, in record order
/ @eg .qcov.history[`demo_deals;`;`v1]
history:{[ds;part;version]
    init_ledger[];
    select from ledger[]
        where dataset=ds, partition=part, source_version=version}

\d .
