/ intervals.q - half-open interval arithmetic for the coverage ledger
/ (.qetl.coverage): require_interval, compose, gaps.
/ .
/ A FILE OF ITS OWN SO THE GATEWAY CAN LOAD IT. The frontend's /coverage
/ reads etl_coverage from the RDB and the HDB, and only the gateway sees
/ both halves, so the merge and the gaps have to be worked out there. They
/ used to be worked out a second time in Python (uqf_frontend/coverage.py),
/ which made two implementations of the rule a backfill trusts when it skips
/ a covered window - free to disagree, with no test holding them together.
/ gateway1 now loads this file (VENDORED_LOAD_OVERLAY in uqs/stack/procs.py)
/ and the frontend calls these functions there.
/ .
/ So nothing here may depend on the rest of the ETL tree: the gateway loads
/ this file and nothing else from src/etl. Every other loader loads it just
/ before materialisation.q, which defines the ledger in the same namespace.

\d .qetl.coverage

/ Validate a half-open [from;to) interval.
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
/.
/ .
/ "Only at their common boundary" is the load-bearing phrase. With half-open
/ intervals [Mon;Tue) and [Tue;Wed) are contiguous with nothing between
/ them, so they compose; [Mon;Tue) and [Wed;Thu) leave Tuesday uncovered and
/ MUST NOT be merged. A naive "merge anything close" implementation silently
/ reports a missing day as covered, which is the failure this rule exists to
/ prevent.
/ @param intervals a table with range_from and range_to columns
/ @return a table of composed intervals, ordered by range_from
/ @eg .qetl.coverage.compose[([] range_from:2026.09.11D00:00 2026.09.12D00:00; range_to:2026.09.12D00:00 2026.09.13D00:00)]
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
/ name and aborted the whole file's load, leaving .qetl.coverage empty while
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

\d .
