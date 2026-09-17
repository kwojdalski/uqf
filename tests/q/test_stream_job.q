/ test_stream_job.q - the streaming-job contract, and the four jobs' own
/ handlers (.sjtest).
/ .
/ MOST OF THIS COULD NOT BE WRITTEN BEFORE. Each job's `upd` lived at the
/ root of a scripts/torq_*_etl.q that subscribed to a tickerplant as it
/ loaded, so no test process could load it: the batch handler - the half
/ that decides what goes into the buffers and when rows are published - was
/ the one part of a job nothing ran. Only its transform was reachable.
/ .
/ The publish seam is what changed that. A job calls `publish` in its own
/ namespace; here it is wired to a recorder, so what a job WOULD have sent
/ to the tickerplant is available as data.
/ .
/ Every test that pushes a batch resets the job's state first: these are
/ process globals, and a test that inherits another's buffer passes or fails
/ for reasons that are not its own.

\d .sjtest

/ --- a recorder, standing in for the tickerplant ---------------------------

published:([] job:`symbol$(); tbl:`symbol$(); rows:())

recorder:{[job;tbl;rows]
    `.sjtest.published upsert (job;tbl;enlist rows);
    count rows}

reset:{[]
    `.sjtest.published set 0#.sjtest.published;
    `.qsub.markout.pending set 0#.qsub.markout.pending;
    `.qsub.markout.quote_hist set 0#.qsub.markout.quote_hist;
    `.qsub.cross.quotes set 0#.qsub.cross.quotes;
    `.qsub.cross.crosses set 0#.qsub.cross.crosses;
    `.qsub.posbook.book set 1!0#.qsub.posbook.position_book;
    `.qsub.posbook.last_mid set (`symbol$())!`float$();
    {.qstream.wire[x;.sjtest.recorder x]} each .qstream.registered[];
    }

/ The rows of the last publication. The `rows` column holds each batch as
/ ONE cell - a table inside a general column - so reading it back takes an
/ unwrap: without it `exec rows from` hands back a one-element list that
/ looks like a one-row table and quietly answers every count with 1.
last_rows:{[] first last exec rows from .sjtest.published}

d:{[n] 2026.09.17D10:00:00.000000000+n*0D00:00:01}

/ --- the contract ---------------------------------------------------------

test_every_job_is_registered:{[t]
    .qunit.assertEquals[asc .qstream.registered[];`cross`markout`posbook`vectorize;
        "each job file registers itself as it loads"]};

test_a_jobs_namespace_is_derived_from_its_name:{[t]
    .qunit.assertEquals[.qstream.namespace `markout;`.qsub.markout;
        "a job's namespace is .qsub.<job>, not a second name to keep in step"]};

test_the_declaration_names_the_namespace_that_holds_the_job:{[t]
    / Not a tautology with the test above: this one checks the derived name
    / is where the implementation actually is.
    .qunit.assertEquals[`on_batch in key .qstream.declaration[`markout]`ns;1b;
        "the derived namespace holds the job's own handler"]};

test_a_process_finds_its_job_by_name:{[t]
    / How the generic runner knows which job it is.
    .qunit.assertEquals[.qstream.for_procname `markout1;`markout;
        "the job that claims a process is found by that process's name"]};

test_an_unclaimed_process_is_refused_by_name:{[t]
    .qunit.assertError[{.qstream.for_procname x};`nosuchproc1;
        "a process no job claims is an error naming it, not a job subscribed to nothing"]};

test_a_declaration_missing_a_field_is_refused:{[t]
    .qunit.assertError[{.qstream.register[`incomplete;x]};
        `procname`subscribes`publishes!(`incomplete1;enlist `t;`symbol$());
        "a job with no on_batch is refused at declaration"]};

test_half_a_timer_is_refused:{[t]
    / A period with no body is a job whose timer never does anything, and
    / every other test still passes.
    .qunit.assertError[{.qstream.register[`halftimer;x]};
        `procname`subscribes`publishes`on_batch`timer_period!(
            `halftimer1;enlist `t;`symbol$();{[tbl;batch] ()};0D00:00:01);
        "a timer_period without an on_timer is refused"]};

test_two_jobs_may_not_claim_one_process:{[t]
    .qunit.assertError[{.qstream.register[`impostor;x]};
        `procname`subscribes`publishes`on_batch!(
            `markout1;enlist `t;`symbol$();{[tbl;batch] ()});
        "one process runs one job, so a second claim on markout1 is refused"]};

test_an_unwired_publish_throws_rather_than_dropping_rows:{[t]
    / The seam's whole point: rows going nowhere must not look like a job
    / with nothing to say.
    .qunit.assertError[{.qstream.unwired[`somejob][`t;x]};([] a:enlist 1);
        "publishing before anything wired the job is an error naming it"]};

/ --- markout --------------------------------------------------------------

test_markout_buffers_trades_and_quotes_separately:{[t]
    reset[];
    .qsub.markout.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.markout.on_batch[`quote;([] time:enlist d 0; sym:enlist `EURUSD;
        bid:enlist 1.0999; ask:enlist 1.1001)];
    .qunit.assertEquals[(count .qsub.markout.pending;count .qsub.markout.quote_hist);(1;1);
        "each batch lands in the buffer its table names"]};

test_markout_ignores_a_pair_this_demo_does_not_trade:{[t]
    / `quote` also carries the vendored starter pack's equity quotes.
    reset[];
    .qsub.markout.on_batch[`quote;([] time:enlist d 0; sym:enlist `AAPL;
        bid:enlist 150f; ask:enlist 150.1)];
    .qunit.assertEquals[count .qsub.markout.quote_hist;0;
        "a quote outside .qsynth.pairs is not buffered"]};

test_markout_publishes_nothing_until_a_fill_is_old_enough:{[t]
    / The horizon has not passed, so there is nothing to score yet - and
    / crucially the fill stays buffered rather than being scored against
    / quotes that have not arrived.
    reset[];
    .qsub.markout.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.markout.score_ready[d 1];
    .qunit.assertEquals[(count .sjtest.published;count .qsub.markout.pending);(0;1);
        "a fill younger than the longest horizon is neither scored nor dropped"]};

test_markout_scores_and_evicts_a_ready_fill:{[t]
    reset[];
    .qsub.markout.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.markout.on_batch[`quote;([] time:d 1 10; sym:`EURUSD`EURUSD;
        bid:1.1004 1.1009; ask:1.1006 1.1011)];
    .qsub.markout.score_ready[d 20];
    out:last_rows[];
    .qunit.assertEquals[(count .sjtest.published;count out;count .qsub.markout.pending);(1;2;0);
        "a fill past its longest horizon is scored at both horizons, published once, and evicted"]};

test_markout_publishes_the_execution_quality_table:{[t]
    reset[];
    .qsub.markout.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.markout.on_batch[`quote;([] time:enlist d 1; sym:enlist `EURUSD;
        bid:enlist 1.1004; ask:enlist 1.1006)];
    .qsub.markout.score_ready[d 20];
    .qunit.assertEquals[first exec tbl from .sjtest.published;`execution_quality;
        "the rows are published onto the table the job declares"]};

test_markout_keeps_the_batch_when_publishing_throws:{[t]
    / At-least-once. The runner's timer swallows a publish failure, so a
    / drain-before-publish ordering would lose the batch silently - which is
    / why score_ready evicts only after the publish returns.
    reset[];
    .qstream.wire[`markout;{[tbl;rows] '"tickerplant is down"}];
    .qsub.markout.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.markout.on_batch[`quote;([] time:enlist d 1; sym:enlist `EURUSD;
        bid:enlist 1.1004; ask:enlist 1.1006)];
    @[{.qsub.markout.score_ready x};d 20;{x}];
    .qunit.assertEquals[count .qsub.markout.pending;1;
        "a failed publish leaves the fill buffered for the next tick"]};

/ --- cross ----------------------------------------------------------------

quote_row:{[ts;sym;bid;ask]
    ([] time:enlist ts; sym:enlist sym;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 5e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 5e6)}

test_cross_mirrors_its_quotes:{[t]
    reset[];
    .qsub.cross.on_batch[`quotes;quote_row[d 0;`EURUSD;1.1;1.1002]];
    .qunit.assertEquals[count .qsub.cross.quotes;1;"the batch lands in the mirror"]};

test_cross_reprices_a_pair_it_can_chain:{[t]
    / EURUSD and USDJPY quoted -> EURJPY is buildable; the other three
    / declared crosses are not, and are left out rather than published null.
    reset[];
    .qsub.cross.on_batch[`quotes;quote_row[d 0;`EURUSD;1.1;1.1002]];
    .qsub.cross.on_batch[`quotes;quote_row[d 0;`USDJPY;150f;150.02]];
    out:.qsub.cross.reprice[d 1];
    .qunit.assertEquals[out`sym;enlist `EURJPY;
        "only the cross whose legs are quoted comes out"]};

test_cross_accumulates_rather_than_publishing:{[t]
    / cross1 declares no published table: its output is process-local, and
    / a test that saw rows on the recorder would mean the job had started
    / publishing without declaring it.
    reset[];
    .qsub.cross.on_batch[`quotes;quote_row[d 0;`EURUSD;1.1;1.1002]];
    .qsub.cross.on_batch[`quotes;quote_row[d 0;`USDJPY;150f;150.02]];
    .qunit.assertEquals[(0<count .qsub.cross.crosses;count .sjtest.published);(1b;0);
        "the crosses stay in the process and nothing is published"]};

test_cross_ignores_a_table_it_did_not_subscribe_to:{[t]
    reset[];
    .qsub.cross.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD)];
    .qunit.assertEquals[count .qsub.cross.quotes;0;"a batch from another table is ignored"]};

/ --- posbook --------------------------------------------------------------

test_posbook_marks_a_fill_against_the_last_quote:{[t]
    reset[];
    .qsub.posbook.on_batch[`quote;`sym`bid`ask!(`EURUSD;1.1035;1.1045)];
    .qsub.posbook.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    out:last_rows[];
    .qunit.assertEquals[out[`mark_price];enlist 1.104;
        "the fill is marked to the mid of the last quote seen for its sym"]};

test_posbook_carries_the_book_between_batches:{[t]
    / The book is process state rebuilt from each batch's output - the one
    / thing a transform test cannot check, because the transform is pure.
    reset[];
    .qsub.posbook.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD; side:enlist 1;
        trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
    .qsub.posbook.on_batch[`trades;([] time:enlist d 1; sym:enlist `EURUSD; side:enlist -1;
        trade_price:enlist 1.105; size:enlist 4e5; pip_factor:enlist 10000)];
    .qunit.assertEquals[exec qty from 0!.qsub.posbook.book;enlist 6e5;
        "the second batch applies to the book the first one left"]};

test_posbook_publishes_one_row_per_fill:{[t]
    reset[];
    .qsub.posbook.on_batch[`trades;([] time:d 0 1; sym:`EURUSD`USDJPY; side:1 -1;
        trade_price:1.1 150f; size:1e6 1e6; pip_factor:10000 100)];
    .qunit.assertEquals[count last_rows[];2;
        "every fill in the batch produces a position row"]};

test_posbook_publishes_nothing_for_a_quote:{[t]
    reset[];
    .qsub.posbook.on_batch[`quote;`sym`bid`ask!(`EURUSD;1.1;1.1002)];
    .qunit.assertEquals[count .sjtest.published;0;
        "a quote only refreshes the mark, it is not a publication"]};

/ --- vectorize ------------------------------------------------------------

wide_row:{[]
    flip (`time`sym,.qsub.vectorize.wide_level_names)!
        (enlist d 0;enlist `EURUSD),
        enlist each (1.1-0.0001*til 11),(1.1002+0.0001*til 11)}

test_vectorize_folds_and_republishes_each_batch:{[t]
    reset[];
    .qsub.vectorize.on_batch[`wide_book;wide_row[]];
    out:last_rows[];
    .qunit.assertEquals[(first exec tbl from .sjtest.published;count out[0;`bid_prices]);
        (`mkt_orderbook;11);
        "the wide row is folded into eleven-level vectors and published"]};

test_vectorize_keeps_no_state:{[t]
    / Two identical batches produce two identical publications: nothing
    / accumulates, which is why this job has no buffer to evict.
    reset[];
    .qsub.vectorize.on_batch[`wide_book;wide_row[]];
    .qsub.vectorize.on_batch[`wide_book;wide_row[]];
    .qunit.assertEquals[count .sjtest.published;2;"each batch stands alone"]};

test_vectorize_ignores_another_table:{[t]
    reset[];
    .qsub.vectorize.on_batch[`quotes;wide_row[]];
    .qunit.assertEquals[count .sjtest.published;0;"a batch from another table is ignored"]};

\d .
