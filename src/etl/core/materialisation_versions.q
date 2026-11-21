/ materialisation_versions.q - a window one source_version overwrote is no
/ longer another version's (.qetl.coverage, #2846). Part of the ledger in
/ materialisation.q, loaded after it; it is split out only for that file's
/ line budget.
/ .
/ The ledger keeps claims per source_version, but a destination written under
/ upsert or replace holds ONE copy of a window: the rows of the last version
/ written. When v2 overwrote a window v1 had claimed, v1's claim stayed
/ current and said the destination held v1's rows, which it no longer did: a
/ re-run at v1 - rolling back a bad release - was idle and left v2's rows,
/ and require_upstream at v1 admitted a consumer that then read v2's. The
/ header's rule 2, coverage under one release says nothing about another, is
/ what that broke: v1's claim was answering for v2's data.
/ .
/ So such a publish withdraws every OTHER version's current claim that
/ overlaps its window, before it records its own. Overlap, as supersede: a
/ claim reaching past the window is withdrawn whole, an under-claim the next
/ run at that version refills. Withdrawn first and recorded second, so an
/ interruption between the two also leaves an under-claim, never an
/ over-claim. append, ignore and fail leave what other versions wrote in
/ place, so their claims stand.

\d .

/ Withdraw the current claims other source_versions hold over [from_ts;to_ts)
/ for one dataset and partition: what a publish that overwrote the window
/ does before it records its own claim.
/ @param ds the dataset
/ @param part the partition, or ` for an unpartitioned dataset
/ @param version the version that overwrote the window, whose claims stay
/ @param from_ts inclusive lower bound
/ @param to_ts exclusive upper bound
/ @return the number of claims withdrawn
/ @throws error when version is null, or the interval is not a proper half-open range
/ @eg .qetl.coverage.withdraw_other_versions[`no_such_dataset;`;`v2;2026.09.12D00:00;2026.09.13D00:00]  ->  0
.qetl.coverage.withdraw_other_versions:{[ds;part;version;from_ts;to_ts]
    if[null version;
        '"stage_completion: source_version must be set - coverage under one source release says nothing about another"];
    .qetl.coverage.require_interval[from_ts;to_ts];
    .qetl.coverage.init_ledger[];
    n:.qetl.coverage.with_lock[{[ds;part;version;from_ts;to_ts]
        .qetl.coverage.reload[];
        / a local copy of still_current: see supersede_locked
        cur:.qetl.coverage.still_current;
        others:exec distinct source_version from .qetl.coverage.ledger[]
            where dataset=ds, partition=part, not source_version=version,
                  superseded_at=cur, range_from<to_ts, from_ts<range_to;
        n:sum "j"$.qetl.coverage.supersede_locked[ds;part;;from_ts;to_ts] each others;
        if[n>0; .qetl.coverage.persist[]];
        n};
        (ds;part;version;from_ts;to_ts)];
    if[n>0;
        .[{.qetl.log.ch.info[x;y;z]};(`coverage;"withdrew other source versions' claims over a window this one overwrote";
            `dataset`partition`source_version`range_from`range_to`withdrawn!(ds;part;version;from_ts;to_ts;n));::]];
    n}

/ stage_written for a window whose publish OVERWROTE it (upsert, replace):
/ the other versions' claims over it are withdrawn first.
/ @param dataset the dataset completed
/ @param partition the slice of the dataset, or `
/ @param source_version the immutable source-release label
/ @param range_from window start
/ @param range_to window end, exclusive
/ @param rows_published how many rows the window published
/ @param touched the dates the publish wrote; empty when none were reported
/ @return the number of rows now in the ledger
/ @throws as stage_written
/ @eg .qetl.coverage.stage_overwriting[`markouts;`;`v2;2026.09.13D00:00;2026.09.14D00:00;9;enlist 2026.09.13]
.qetl.coverage.stage_overwriting:{[dataset;partition;source_version;range_from;range_to;rows_published;touched]
    .qetl.coverage.withdraw_other_versions[dataset;partition;source_version;range_from;range_to];
    .qetl.coverage.stage_written[dataset;partition;source_version;range_from;range_to;rows_published;touched]}
