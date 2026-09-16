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
/ premise is gone: A-03 made this repository the primary lineage and A-02
/ froze canonical, so there is no other schema to verify against. The shape
/ below IS the schema, and `scripts/verify_coverage_schema.q -local 1`
/ confirms the code and the table agree.
/ .
/ ON THE PARTITION KEY, WHICH IS THE PART WORTH READING
/ .
/ The frontend requirements describe coverage "by dataset, partition key, and
/ time range", and there is no partition key here. That was the open worry on
/ #60 - if a real ledger carried one, a query filtering on dataset and
/ version alone would aggregate ACROSS partitions and report a range covered
/ in one partition as covered everywhere.
/ .
/ It is deliberately absent, because coverage in this tree has no partition
/ dimension to record. There is exactly one caller of stage_completion -
/ .qwrt.finish_window - and it takes (dataset; source_version; range_from;
/ range_to; rows). A bounded worker covers a WHOLE dataset for a window;
/ nothing backfills one partition at a time. A partition-key column would
/ hold one value per dataset and widen every signature below for nothing.
/ .
/ WHAT WOULD CHANGE THAT ANSWER
/ .
/ A worker that backfills per partition - per sym, per venue, per region.
/ Then coverage genuinely needs the fourth dimension, and it must be a
/ REQUIRED parameter for the same reason source_version is (ETL-09): an
/ optional filter is one a caller forgets, and forgetting this one reports a
/ gap-ridden range as complete.
/ .
/ That is not left to memory. .qbw.define refuses two workers declaring the
/ same dataset, which is the concrete shape the problem takes - two things
/ filling one dataset with no way to tell their coverage apart. A future
/ per-partition worker trips that guard and has to make the decision
/ deliberately.
/ .
/ Deliberately one constant in one place: changing it should be an edit here
/ plus the writer's column list, not a hunt through the file.
schema:`dataset`source_version`range_from`range_to`rows_published`recorded_at`superseded_at

/ Create the ledger if absent. Append-only by contract (ETL-07) - nothing in
/ this file updates or deletes a row, and a reader should treat any such
/ mutation elsewhere as a bug.
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

init_ledger:{[]
    if[not `etl_coverage in tables `.;
        `etl_coverage set ([] dataset:`symbol$(); source_version:`symbol$();
            range_from:`timestamp$(); range_to:`timestamp$();
            rows_published:`long$(); recorded_at:`timestamp$();
            superseded_at:`timestamp$())];
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
/ have a shape this file does not expect - a partition key, say - and then
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
    `etl_coverage}

/ Refuse to trust a ledger whose shape is not the one this file declares.
/ .
/ Converts a SILENT wrong answer into a loud refusal. A ledger carrying an
/ extra column that distinguishes rows - a partition key is the obvious one -
/ makes every read here aggregate across it, so a range covered for one value
/ reports as COMPLETE for all of them, and nothing errors because every row
/ found is valid. A consumer then reads a gap-ridden range believing it whole.
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
/ `scripts/verify_coverage_schema.q` is the same check as a standalone
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
/ @eg .qcov.compose[([] range_from:...; range_to:...)]
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
/ @param source_version the immutable source-release label (ETL-09)
/ @param range_from window start
/ @param range_to window end, exclusive
/ @param rows_published how many rows the window published; 0 is legal and
/   meaningful
/ @return the number of rows now in the ledger
/ @throws error if source_version is null, or the interval is empty/reversed
/ @eg .qcov.stage_completion[`markouts;`v1;2026.09.13D00:00;2026.09.14D00:00;1234]
stage_completion:{[dataset;source_version;range_from;range_to;rows_published]
    if[null source_version;
        '"stage_completion: source_version must be set - coverage under one source release says nothing about another (ETL-09)"];
    require_interval[range_from;range_to];
    init_ledger[];
    `etl_coverage insert (dataset;source_version;range_from;range_to;
                          "j"$rows_published;.z.p;still_current);
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
valid_at:{[ds;version;as_of]
    init_ledger[];
    select range_from, range_to from ledger[]
        where dataset=ds, source_version=version,
              recorded_at<=as_of, as_of<superseded_at}

/ The composed intervals covered for a dataset and source_version, as
/ understood at `as_of` (D-11).
/ @param as_of the instant to answer as of; .z.p for "now"
/ @eg .qcov.intervals[`demo_deals;`v1;.z.p]
intervals:{[ds;version;as_of]
    compose valid_at[ds;version;as_of]}

/ Is [range_from;range_to) fully covered for this dataset and release?
/ @return 1b when there are no gaps
is_covered:{[ds;version;as_of;from_ts;to_ts]
    0=count gaps[from_ts;to_ts;intervals[ds;version;as_of]]}

/ The uncovered sub-ranges of a requested range, for a caller that wants to
/ narrow its request rather than be refused outright.
missing:{[ds;version;as_of;from_ts;to_ts]
    gaps[from_ts;to_ts;intervals[ds;version;as_of]]}

/ Admission check: refuse the caller unless the range is fully covered.
/ .
/ ETL-11 requires that local historical reads, INCLUDING this check, go
/ through a gateway addressing both `rdb and `hdb. That matters because
/ completion data moves after EOD: a check bound to an rdb-only handle
/ starts returning false gaps the morning after, for data that is present
/ and simply no longer in memory. Callers must therefore route this read,
/ not run it against a single tier.
/ @throws error naming the missing ranges when not fully covered
require_covered:{[ds;version;as_of;from_ts;to_ts]
    m:missing[ds;version;as_of;from_ts;to_ts];
    if[count m;
        '"require_covered: ",string[ds]," at source_version ",
         string[version]," is not fully published for [",
         string[from_ts],"; ",string[to_ts],") - missing: ",
         ", " sv {"[",string[x`range_from],"; ",string[x`range_to],")"} each m];
    1b}


/ ------------------------------------------------------- SUPERSESSION

/ Withdraw the coverage claims overlapping [from_ts;to_ts), as of now.
/ .
/ D-11, option A: a restatement does not DELETE the old claim, it closes it.
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
/ @param ds the dataset
/ @param version the source_version whose claims are being withdrawn
/ @param from_ts inclusive lower bound of the restated range
/ @param to_ts exclusive upper bound
/ @return the number of claims withdrawn
/ @throws error when the interval is not a proper half-open range
/ @eg .qcov.supersede[`demo_deals;`v1;2026.09.12D00:00;2026.09.13D00:00]
supersede:{[ds;version;from_ts;to_ts]
    require_interval[from_ts;to_ts];
    init_ledger[];
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
        where dataset=ds, source_version=version,
              superseded_at=cur,
              range_from<to_ts, from_ts<range_to;
    if[0=count idx; :0];
    `etl_coverage set update superseded_at:now from ledger[] where i in idx;
    count idx}

/ Every claim ever made for a dataset and source_version, withdrawn or not.
/ .
/ The audit view. `intervals` answers "what is true", this answers "what was
/ ever said", which is the question a restatement makes worth asking.
/ @return the ledger rows for this dataset/version, in record order
/ @eg .qcov.history[`demo_deals;`v1]
history:{[ds;version]
    init_ledger[];
    select from ledger[] where dataset=ds, source_version=version}

\d .
