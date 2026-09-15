/ coverage.q - the append-only completeness ledger and its interval
/ arithmetic (.qcov).
/ .
/ Implements requirements E-07 to E-11 of docs/etl-framework-requirements.md.
/ Those IDs are the REQUIREMENTS document's; the question bank uses an
/ overlapping E-nn scheme for source-adapter questions, so question-bank
/ answers are named as such below.
/ .
/ The two things most easily got wrong here, both counter-intuitive and both
/ stated explicitly in the canonical document:
/ .
/   1. Coverage is RECORDED, not derived (E-07). It is not computed from the
/      target data; a completion event is staged for every completed bounded
/      window, INCLUDING AN EMPTY ONE. The empty-window rule is what makes
/      "we ran and there was nothing" distinguishable from "we never ran" -
/      and a derived ledger cannot express that difference at all.
/ .
/   2. source_version is not optional and not advisory (E-09, E-10).
/      Coverage recorded under one source release says nothing about
/      another, so every read filters on it and intervals from different
/      versions are NEVER merged to satisfy a dependency.

\d .qcov

/ ---------------------------------------------------------------- SCHEMA

/ ASSUMED, NOT VERIFIED - see issue #60.
/ .
/ etl_coverage exists only in the canonical Bitbucket tree, which is
/ unreachable from here, so these columns are inferred from what E-07/E-08/
/ E-09 require plus the frontend requirements' description of coverage "by
/ dataset, partition key, and time range".
/ .
/ Note that description mentions a PARTITION KEY, which is not in the shape
/ below. If canonical carries one, a query filtering only on dataset and
/ version could report a gap-ridden range as complete - the dangerous
/ direction. One `meta etl_coverage` on the work machine settles it.
/ .
/ Deliberately one constant in one place: correcting it should be an edit
/ here plus the writer's column list, not a hunt through the file.
schema:`dataset`source_version`range_from`range_to`rows_published`recorded_at

/ Create the ledger if absent. Append-only by contract (E-07) - nothing in
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
init_ledger:{[]
    if[not `etl_coverage in tables `.;
        `etl_coverage set ([] dataset:`symbol$(); source_version:`symbol$();
            range_from:`timestamp$(); range_to:`timestamp$();
            rows_published:`long$(); recorded_at:`timestamp$())];
    `etl_coverage}

/ The root ledger table. Exists so no read below names `etl_coverage` bare -
/ see init_ledger's note on namespace resolution.
ledger:{[] value `etl_coverage}

/ Refuse to trust a ledger whose shape is not the one this file assumes
/ (issue #60).
/ .
/ The point is to convert a SILENT wrong answer into a loud refusal. If the
/ real table carries a partition key, every read here aggregates across
/ partitions, so a range covered in one partition and empty in the others is
/ reported COMPLETE - and nothing errors, because every row found is valid.
/ A consumer then reads a gap-ridden range believing it is whole.
/ .
/ Call this from a worker's init when attached to a ledger this process did
/ not create. It is NOT called from init_ledger: a table this file just built
/ trivially matches, so checking there would only ever confirm itself.
/ .
/ `scripts/verify_coverage_schema.q` is the same check as a standalone
/ command, for settling #60 without starting a worker.
/ @return 1b when the live shape matches
/ @throws error naming the difference, and what it would silently do
require_schema:{[]
    live:exec c from 0!meta ledger[];
    missing:schema where not schema in live;
    extra:live where not live in schema;
    if[count missing;
        '"require_schema: etl_coverage is missing ",(", " sv string missing),
         " - the assumed shape (see #60) is wrong, and reads here would fail or return nulls"];
    if[count extra;
        / Kept short deliberately: q truncates a thrown string at 255 bytes,
        / and the consequence is the part worth keeping. The long form lives
        / in this function's own comment above and in #60.
        '"require_schema: etl_coverage has unexpected column(s) ",
         (", " sv string extra),
         " - a partition key here means reads aggregate ACROSS partitions and report a gap-ridden range as complete. See #60"];
    1b}

/ -------------------------------------------------------------- INTERVALS

/ Validate a half-open [from;to) interval (E-08).
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
/ (E-08).
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

/ Stage a completion event for one completed bounded window (E-07).
/ .
/ Called for EVERY completed window, including one that published no rows.
/ That is not an oversight to optimise away: an empty window is positive
/ evidence that the range was examined and held nothing, and without it a
/ reader cannot distinguish that from a range never attempted. Recording
/ rows_published=0 is the whole point.
/ .
/ Only ever called AFTER the underlying work is complete (E-07), which is
/ the same publish-before-acknowledge ordering E-05 requires of cursors.
/ @param dataset the dataset completed, e.g. `markouts
/ @param source_version the immutable source-release label (E-09)
/ @param range_from window start
/ @param range_to window end, exclusive
/ @param rows_published how many rows the window published; 0 is legal and
/   meaningful
/ @return the number of rows now in the ledger
/ @throws error if source_version is null, or the interval is empty/reversed
/ @eg .qcov.stage_completion[`markouts;`v1;2026.09.13D00:00;2026.09.14D00:00;1234]
stage_completion:{[dataset;source_version;range_from;range_to;rows_published]
    if[null source_version;
        '"stage_completion: source_version must be set - coverage under one source release says nothing about another (E-09)"];
    require_interval[range_from;range_to];
    init_ledger[];
    `etl_coverage insert (dataset;source_version;range_from;range_to;
                          "j"$rows_published;.z.p);
    count ledger[]}

/ ------------------------------------------------------------------ READ

/ Every coverage interval for one dataset at one source release.
/ .
/ source_version is a required parameter rather than an optional filter,
/ because E-09 requires consumers to filter on it and an optional filter is
/ one a caller forgets. Intervals from other versions are not returned, so
/ they cannot be merged into this answer (E-10).
/ @param dataset the dataset to report on
/ @param source_version the release to report for
/ @return a table of composed intervals
/ Parameters are ds/version, not dataset/source_version: those are column
/ names, and `where dataset=dataset` compares the column to itself and
/ matches every row. Same trap as gaps above.
intervals:{[ds;version]
    init_ledger[];
    matching:select range_from, range_to from ledger[]
        where dataset=ds, source_version=version;
    compose matching}

/ Is [range_from;range_to) fully covered for this dataset and release?
/ @return 1b when there are no gaps
is_covered:{[ds;version;from_ts;to_ts]
    0=count gaps[from_ts;to_ts;intervals[ds;version]]}

/ The uncovered sub-ranges of a requested range, for a caller that wants to
/ narrow its request rather than be refused outright.
missing:{[ds;version;from_ts;to_ts]
    gaps[from_ts;to_ts;intervals[ds;version]]}

/ Admission check: refuse the caller unless the range is fully covered.
/ .
/ E-11 requires that local historical reads, INCLUDING this check, go
/ through a gateway addressing both `rdb and `hdb. That matters because
/ completion data moves after EOD: a check bound to an rdb-only handle
/ starts returning false gaps the morning after, for data that is present
/ and simply no longer in memory. Callers must therefore route this read,
/ not run it against a single tier.
/ @throws error naming the missing ranges when not fully covered
require_covered:{[ds;version;from_ts;to_ts]
    m:missing[ds;version;from_ts;to_ts];
    if[count m;
        '"require_covered: ",string[ds]," at source_version ",
         string[version]," is not fully published for [",
         string[from_ts],"; ",string[to_ts],") - missing: ",
         ", " sv {"[",string[x`range_from],"; ",string[x`range_to],")"} each m];
    1b}

\d .
