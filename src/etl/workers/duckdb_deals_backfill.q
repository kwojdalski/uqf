/ duckdb_deals_backfill.q - the duckdb_deals bounded worker
/ (.qwrk.duckdb_deals_backfill).
/ .
/ Copies mock FX deals out of a DuckDB file, over ODBC, into duckdb_deals, a
/ day at a time. A declaration over .qbw like every worker: windowing,
/ retries, coverage, checkpoints and dry-run are the shell's.
/ .
/ WHERE THE ROWS GO. No `io` is declared, so .qio.memory writes them to a
/ duckdb_deals table in THIS process - which is where a test or an operator
/ at the q prompt reads them. Run as the duckdb_deals_backfill1 process, the
/ process exits when the range is done and the rows go with it, while the
/ coverage ledger on disk records the range as covered. Landing them in the
/ stack's RDB/HDB needs an io manager that publishes or writes down, which
/ this tree does not have yet; databento_book_backfill has the same gap.

\d .qwrk.duckdb_deals_backfill

/ Refuse a batch that is shaped correctly but cannot be true: a
/ non-positive rate or notional, or a null deal_id - conditions no correct
/ deal can meet, as in demo_deals_backfill.
/ @param batch the transformed rows of one window
/ @return a table check/status/detail, one row per offending deal; empty when the batch passes
/ @eg .qwrk.duckdb_deals_backfill.quality_check[.qfeed.duckdb_deals.fixture[]]  ->  an empty table
quality_check:{[batch]
    if[0=count batch; :.qbw.no_failures[]];
    bad_rate:select from batch where not rate>0;
    bad_notional:select from batch where not notional>0;
    bad_id:select from batch where null deal_id;
    raze {[nm;t]
        if[0=count t; :.qbw.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.Q.s1 each t)
      }'[`nonpositive_rate`nonpositive_notional`null_deal_id;
         (bad_rate;bad_notional;bad_id)]}

/ What a window SAW beyond its row count: how many deals, over how many
/ pairs, and the first and last deal time. Every aggregate survives an
/ empty window, which is legal and recorded deliberately.
/ @param batch the transformed rows of one window
/ @return a dictionary of labels for the window's materialisation
/ @eg .qwrk.duckdb_deals_backfill.facts[.qfeed.duckdb_deals.fixture[]]
facts:{[batch]
    if[0=count batch; :(enlist `window)!enlist "empty window"];
    `deals`pairs`first_deal`last_deal!(count batch;count distinct batch`sym;min batch`deal_time;max batch`deal_time)}

\d .

/ Pass-through: deals are copied as fetched. The example is the source's
/ own fixture.
.qxf.passthrough[`duckdb_deals_passthrough;`batch;0#.qfeed.duckdb_deals.fixture[];.qfeed.duckdb_deals.fixture[]];

.qbw.define[`duckdb_deals_backfill;
    `source`dataset`width`transform`check`facts`procname`note!
        (`duckdb_deals;`duckdb_deals;1D;`duckdb_deals_passthrough;
         .qwrk.duckdb_deals_backfill.quality_check;.qwrk.duckdb_deals_backfill.facts;
         `duckdb_deals_backfill1;
         "bounded: copies mock FX deals from a DuckDB file over ODBC, a day at a time")];
