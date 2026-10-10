/ hdb_transfer.q - trades read from a kdb+ HDB on this machine (.qpipe.source.hdb_transfer).
/ .
/ The source half of the HDB-to-HDB example: a date-partitioned `trades`
/ table in another kdb+ database, read straight from its files by the
/ `local` transport - no q process serving it. The worker
/ (hdb_transfer_backfill) adds each trade's notional and writes the rows to
/ trades_copy. scripts/examples/hdb_transfer_example.q builds a source HDB
/ and runs the whole thing end to end; docs/scaffolding/hdb-transfer.md
/ walks through it.

\d .qpipe.source.hdb_transfer

source_name:`hdb_transfer

/ The columns this adapter READS - not everything the source has. Declaring
/ one the worker never touches means an upstream change to an unused column
/ breaks the run.
columns:`time`trade_id`sym`price`size`side
types:"pjsfjs"


/ The column the window is taken on.
time_column:`time

/ What identifies a row uniquely. Only correct if the source guarantees it:
/ a source that reuses ids after a purge silently merges unrelated rows.
/ The source's trade ids are unique across the whole table.
row_key:`trade_id

/ A claim, not a default. An unstated zone is the shape of the bug - every
/ later reader assumes UTC while the source hands over local wall-clock time.
tz:`UTC

transport:`local

/ What UQF_SOURCE_CRED_HDB_TRANSFER looks like: the source HDB's root
/ directory, the one holding the sym file and the date partitions.
credential_example:"/data/source_hdb"

/ `h` is the HDB directory, read from its files with no process in between.
/ Send the query with .qetl.source.local[h;{[read;from_ts;to_ts] ...};range_from;range_to]:
/ read[`table;from_ts;to_ts] returns the whole date partitions the window
/ touches, symbols decoded against the HDB's own sym file - filter the rows
/ to [from_ts;to_ts) yourself.
/ .
/ Half-open [range_from;range_to) - a DIFFERENT rule: >= on the lower
/ bound and < on the upper, so a boundary row is published exactly once.
query:{[h;range_from;range_to]
    .qetl.source.local[h;{[read;from_ts;to_ts]
        / Whole partitions come back; keep [from_ts;to_ts) and the declared
        / columns. Indexed rather than select-from: a bare name after `from`
        / is what .srctest guards against in a query sent to a remote.
        t:read[`trades;from_ts;to_ts];
        (`time`trade_id`sym`price`size`side)#t where (t[`time]>=from_ts) & t[`time]<to_ts};
        range_from;range_to]}

/ Must satisfy the same contract as the live source, and be DETERMINISTIC:
/ a fixture that changes between runs makes a failing assertion impossible
/ to attribute. Used when no credential is configured - a stated demo path,
/ never a fallback for a failed connection.
/ .
fixture:{[]
    ([] time:2026.01.05D09:00 2026.01.05D09:30 2026.01.05D14:00 2026.01.06D10:15;
        trade_id:1 2 3 4;
        sym:`EURUSD`USDJPY`EURUSD`GBPUSD;
        price:1.0842 151.20 1.0851 1.2655;
        size:1000000 500000 250000 750000;
        side:`buy`sell`sell`buy)}

/ Register on load, so the declaration and the implementation cannot drift.
.qetl.source.define[source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`transport`credential_example!
    (source_name;`trades;time_column;row_key;columns;types;query;fixture;tz;transport;credential_example)];

\d .
