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

recorder:{[job;t;x]
    `.sjtest.published upsert (job;t;enlist x);
    count x}

reset:{[]
    `.sjtest.published set 0#.sjtest.published;
    `.qsub.markout.pending set 0#.qsub.markout.pending;
    `.qsub.markout.quote_hist set 0#.qsub.markout.quote_hist;
    `.qsub.cross.quotes set 0#.qsub.cross.quotes;
    `.qsub.cross.crosses set 0#.qsub.cross.crosses;
    `.qsub.superbook.books set `sym`source xkey .qsub.market_data.market_data;
    `.qsub.posbook.book set 1!0#.qsub.posbook.position_book;
    `.qsub.posbook.last_mid set (`symbol$())!`float$();
    `.qsub.crypto_mock.last_id set .qsub.crypto_mock.venues!(count .qsub.crypto_mock.venues)#0;
    `.qsub.fx_positions.book set `sym`book`product xkey 0#.qsub.fx_positions.desk_book;
    `.qsub.fx_positions.limits set 0#.qsub.fx_positions.limits;
    `.qsub.fx_positions.alerts set .qlimit.no_alerts[];
    {.qstream.wire[x;.sjtest.recorder x]} each .qstream.defined[];
    }

/ The rows of the last publication. The `rows` column holds each batch as
/ ONE cell - a table inside a general column - so reading it back takes an
/ unwrap: without it `exec rows from` hands back a one-element list that
/ looks like a one-row table and quietly answers every count with 1.
last_rows:{[] first last exec rows from .sjtest.published}

d:{[n] 2026.09.17D10:00:00.000000000+n*0D00:00:01}

/ --- the contract ---------------------------------------------------------

test_every_job_is_registered:{[t]
    / Feeds publish on a timer and subscribe to nothing; subscribers are the
    / other half. One contract covers both.
    / .
    / Derived from the files rather than listed (#352): a job is named after
    / its file, so every file in the directory must have registered. The hand
    / list this replaced made every new job an edit here. `except` rather than
    / equality because other suites register test jobs (.qsub.nt_k and
    / friends), and whether they ran first is not what this asks. The other
    / direction is test_every_registered_job_has_a_file, below.
    .qunit.assertEquals[.testutil.etl_declaration_names["src/etl/streaming"] except .qstream.defined[];
        `symbol$();
        "each job file registers itself as it loads"]};

/ The jobs src/ registered, taken as this file LOADS rather than when a test
/ runs. Every suite loads before any of them runs (.testutil.load_suites),
/ and test jobs - .qsub.nt_k, regtest_feed and friends - register only inside
/ test bodies, so this is the tree's own set whatever UQF_TEST_ORDER says.
/ A suite that registered a job at load time would land here and fail the
/ test below, which is the right outcome: register fixtures inside a test.
src_jobs:.qstream.defined[]

test_every_registered_job_has_a_file:{[t]
    / The direction the hand list used to carry and the file-derived check
    / above cannot (#352): a job in the registry with no file. One declared
    / from anywhere but src/etl/streaming/<job>.q is invisible to every check
    / that reads that directory, while still running in the stack.
    .qunit.assertEquals[src_jobs except .testutil.etl_declaration_names["src/etl/streaming"];
        `symbol$();
        "every registered job lives in src/etl/streaming/<job>.q, named after its file"]};

test_a_feed_declares_no_subscription:{[t]
    .qunit.assertEquals[count .qstream.def[`fx_feed]`subscribe_to;0;
        "a feed produces rows on a timer rather than reacting to a table"]};

test_a_subscriber_with_no_handler_is_refused:{[t]
    / It would receive every batch and drop it, and look healthy doing so.
    .qunit.assertError[{.qstream.define[`handlerless;x]};
        `procname`subscribe_to`publishes`period`on_timer!(
            `handlerless1;enlist `trades;`symbol$();0D00:00:01;{[] ()});
        "a job that subscribes must say what to do with a batch"]};

test_a_job_that_does_nothing_is_refused:{[t]
    .qunit.assertError[{.qstream.define[`idle;x]};
        `procname`subscribe_to`publishes!(`idle1;`symbol$();`symbol$());
        "a job with neither a handler nor a timer runs nothing at all"]};

/ start_with_all and note are read by uqs's process registry, which is derived
/ from these declarations - so a malformed one is refused here, by name,
/ rather than read later as a registry that quietly disagrees.
test_an_autostart_that_is_not_a_boolean_is_refused:{[t]
    .qunit.assertThrows[{.qstream.define[`badstart;x]};
        `procname`subscribe_to`publishes`period`on_timer`start_with_all!(
            `badstart1;`symbol$();`symbol$();0D00:00:01;{[] ()};`yes);
        "*start_with_all must be a boolean*";
        "start_with_all is a flag, not a word that reads like one"]};

test_a_note_that_is_not_a_string_is_refused:{[t]
    .qunit.assertThrows[{.qstream.define[`badnote;x]};
        `procname`subscribe_to`publishes`period`on_timer`note!(
            `badnote1;`symbol$();`symbol$();0D00:00:01;{[] ()};`why);
        "*note must be a string*";
        "a note is prose for the process table, so a symbol is refused"]};

test_a_jobs_namespace_is_derived_from_its_name:{[t]
    .qunit.assertEquals[.qstream.namespace `markout;`.qsub.markout;
        "a job's namespace is .qsub.<job>, not a second name to keep in step"]};

test_the_declaration_names_the_namespace_that_holds_the_job:{[t]
    / Not a tautology with the test above: this one checks the derived name
    / is where the implementation actually is.
    .qunit.assertEquals[`on_batch in key .qstream.def[`markout]`ns;1b;
        "the derived namespace holds the job's own handler"]};

test_a_process_finds_its_job_by_name:{[t]
    / How the generic runner knows which job it is.
    .qunit.assertEquals[.qstream.for_procname `markout1;`markout;
        "the job that claims a process is found by that process's name"]};

test_an_unclaimed_process_is_refused_by_name:{[t]
    .qunit.assertError[{.qstream.for_procname x};`nosuchproc1;
        "a process no job claims is an error naming it, not a job subscribed to nothing"]};

test_a_declaration_missing_a_field_is_refused:{[t]
    .qunit.assertError[{.qstream.define[`incomplete;x]};
        `procname`subscribe_to`publishes!(`incomplete1;enlist `t;`symbol$());
        "a job with no on_batch is refused at declaration"]};

test_half_a_timer_is_refused:{[t]
    / A period with no body is a job whose timer never does anything, and
    / every other test still passes.
    .qunit.assertError[{.qstream.define[`halftimer;x]};
        `procname`subscribe_to`publishes`on_batch`period!(
            `halftimer1;enlist `t;`symbol$();{[t;x] ()};0D00:00:01);
        "a period without an on_timer is refused"]};

test_two_jobs_may_not_claim_one_process:{[t]
    .qunit.assertError[{.qstream.define[`impostor;x]};
        `procname`subscribe_to`publishes`on_batch!(
            `markout1;enlist `t;`symbol$();{[t;x] ()});
        "one process runs one job, so a second claim on markout1 is refused"]};

test_an_unwired_publish_throws_rather_than_dropping_rows:{[t]
    / The seam's whole point: rows going nowhere must not look like a job
    / with nothing to say.
    .qunit.assertError[{.qstream.unwired[`somejob][`t;x]};([] a:enlist 1);
        "publishing before anything wired the job is an error naming it"]};

/ --- the feeds ------------------------------------------------------------

test_the_fx_feed_publishes_one_quote_per_pair:{[t]
    reset[];
    .qsub.fx_feed.on_timer[];
    rows:last_rows[];
    .qunit.assertEquals[(first exec tbl from .sjtest.published;count first rows);
        (`quote;count .qsynth.pairs);
        "a tick quotes every pair the demo trades"]};

test_the_fx_feed_quotes_a_pip_either_side:{[t]
    / The spread is the whole content of a top-of-book tick, and it is the
    / one thing a wrong pip vector would silently break.
    / Compared within a tolerance, not with ~: (mid+pip)-(mid-pip) is not
    / bit-identical to 2*pip for any of these levels, so an exact match here
    / fails on arithmetic rather than on the spread being wrong.
    rows:.qsub.fx_feed.tick_rows .qsynth.spot;
    .qunit.assertEquals[all 1e-12>abs (rows[2]-rows[1])-2*.qsynth.pip;1b;
        "ask minus bid is two pips, pair by pair"]};

test_the_fx_feed_walks_its_level:{[t]
    reset[];
    before:.qsub.fx_feed.spot;
    .qsub.fx_feed.on_timer[];
    .qunit.assertTrue[not before~.qsub.fx_feed.spot;
        "each tick moves the level rather than republishing the same one"]};

test_the_depth_feed_quotes_three_levels_a_side:{[t]
    rows:.qsub.quotes_feed.tick_rows .qsynth.spot;
    .qunit.assertEquals[distinct count each raze rows 1 3;enlist .qsub.quotes_feed.n_levels;
        "every pair's bid and ask ladder is n_levels deep"]};

test_the_depth_feeds_ladders_are_level_zero_first:{[t]
    / .qbook and .qfwd.cross_book_at both read level 0 as the touch, so a
    / ladder built outwards-in prices every cross off the wrong level.
    rows:.qsub.quotes_feed.tick_rows .qsynth.spot;
    bids:first rows 1;
    .qunit.assertEquals[bids~desc bids;1b;"the bid ladder descends from the touch"]};

test_the_wide_feed_publishes_one_column_per_level:{[t]
    / 1 sym + 11 bids + 11 asks. The vectorize job derives its groups from
    / its own schema, so a mismatch here is a column-count error on insert.
    rows:.qsub.wide_book_feed.tick_rows .qsynth.spot;
    .qunit.assertEquals[count rows;1+2*.qsub.wide_book_feed.n_levels;
        "the wide book is published as one column per level per side"]};

test_the_wide_feed_column_is_as_long_as_the_pair_list:{[t]
    / The transpose that makes this a wide table rather than a nested one.
    rows:.qsub.wide_book_feed.tick_rows .qsynth.spot;
    .qunit.assertEquals[distinct count each rows;enlist count .qsynth.pairs;
        "every column carries one value per pair"]};

test_a_fill_prices_around_its_pairs_level:{[t]
    / Three pips either way, which is what makes some fills cross the spread
    / and some improve.
    rows:.qsub.fx_trades_feed.fill_rows[0;1;1e6;3];
    .qunit.assertEquals[first rows 2;
        (first .qsynth.spot)+3%first .qsub.fx_trades_feed.pip_factor;
        "the fill prints its slippage in pips from the pair's own level"]};

test_a_fill_is_published_as_one_element_vectors:{[t]
    / .u.upd counts rows from column lengths, so an atom column here is a
    / length error on insert - the vendored feed.q's own convention.
    rows:.qsub.fx_trades_feed.fill_rows[0;1;1e6;0];
    .qunit.assertEquals[distinct count each rows;enlist 1;
        "every column of a fill is a one-element vector, never an atom"]};

test_the_trades_feed_publishes_one_fill_a_tick:{[t]
    reset[];
    .qsub.fx_trades_feed.on_timer[];
    .qunit.assertEquals[(count .sjtest.published;first exec tbl from .sjtest.published);(1;`trades);
        "a tick publishes exactly one fill, onto trades"]};

/ --- the publisher invariants, over every feed --------------------------

/ Three rules govern every batch that reaches a tickerplant, and they are
/ restated in five file headers and enforced by no gate:
/ .
/   1. the PLANT stamps `time`; a publisher must not send one
/   2. keyed tables are refused - a plant appends
/   3. the row count comes from column length, so every column is a LIST
/ .
/ Both plants guard all three at RUNTIME. But a feed's timer body only
/ runs under a live plant, which is the least-covered path in the tree -
/ the .qpipe column-list regression sat undetected for exactly that
/ reason, and would have failed all four FX feeds on every tick.
/ .
/ So fire each feed's own on_timer against the recorder and check what it
/ WOULD have published. Two tests already did this, one feed at a time
/ (fx_trades_feed and crypto_mock); six feeds exist. Enumerating is the
/ same correction #267 made to the schema gate: a rule that applies to a
/ class should not be checked one instance at a time.

/ Every registered job that publishes on a timer.
feeds:{[] .qstream.defined[] where
    {[j] d:.qstream.def j; (`period in key d) and count d`publishes} each .qstream.defined[]}

/ Invariant 3, plus the length agreement it exists to protect: a batch's
/ row count comes from its first column, so a column of a different
/ length silently truncates or overruns.
columns_are_lists:{[batch]
    cs:$[98h=type batch; value flip batch; batch];
    $[not all 0<=type each cs; "a column is an ATOM - a one-row batch then reads as a one-COLUMN batch";
      1<count distinct count each cs; "columns are not all the same length, so the row count depends on which one is read first";
      ""]}

test_every_feed_publishes_columns_that_are_lists:{[t]
    / Invariant 3, the one that actually bites: `enlist` omitted from a row
    / builder is invisible until a plant takes the row count from it.
    bad:();
    {[job]
        reset[];
        (.qstream.def[job]`on_timer)[];
        {[job;cell]
            problem:.sjtest.columns_are_lists cell;
            if[count problem; `.sjtest.bad set .sjtest.bad,enlist string[job],": ",problem]
          }[job] each first each exec rows from .sjtest.published
      } each feeds[];
    .qunit.assertEquals[.sjtest.bad;();"every feed publishes column vectors of equal length"]};

test_no_feed_sends_its_own_time:{[t]
    / Invariant 1. Both plants now STRIP a publisher's `time` rather than
    / refusing it (#266), so a feed that sends one is silently corrected
    / and would never be caught at runtime. That makes this the invariant
    / with the least live protection, not the most.
    bad:();
    {[job]
        reset[];
        (.qstream.def[job]`on_timer)[];
        {[job;cell]
            / Nested, not `and`: q's `and` does not short-circuit, so the
            / one-line spelling evaluates `cols` on a list-of-columns
            / batch and throws `type`. The same trap this suite's own
            / subject hit in .qtick.publish.
            if[$[98h<>type cell; 0b; `time in cols cell];
                `.sjtest.bad set .sjtest.bad,enlist string[job]," sends its own `time`"]
          }[job] each first each exec rows from .sjtest.published
      } each feeds[];
    .qunit.assertEquals[.sjtest.bad;();
        "no feed sends `time` - the plant stamps it, and a source's own event time belongs in a column named for what it is"]};

test_no_feed_publishes_a_keyed_table:{[t]
    / Invariant 2. A plant appends; upserting by key drops the ticks that
    / make a log a log.
    bad:();
    {[job]
        reset[];
        (.qstream.def[job]`on_timer)[];
        {[job;cell]
            if[99h=type cell; `.sjtest.bad set .sjtest.bad,enlist string[job]," published a keyed table"]
          }[job] each first each exec rows from .sjtest.published
      } each feeds[];
    .qunit.assertEquals[.sjtest.bad;();"no feed publishes a keyed table"]};

test_the_invariant_check_catches_an_atom_column:{[t]
    / The check must fail on the shape it exists for, or it is decoration.
    .qunit.assertEquals[.sjtest.columns_are_lists (enlist `EURUSD;enlist 1.085);"";
        "one-element vectors are fine"];
    .qunit.assertTrue[0<count .sjtest.columns_are_lists (`EURUSD;1.085);
        "bare atoms are caught"];
    .qunit.assertTrue[0<count .sjtest.columns_are_lists (enlist `EURUSD;1.085 1.086);
        "and so are columns of unequal length"]};

test_every_feed_is_covered_by_these_checks:{[t]
    / feeds[] is derived, so a new feed is covered the day it registers -
    / but only if it publishes on its timer. This pins the count so that a
    / feed which quietly stops publishing is noticed.
    .qunit.assertTrue[4<count feeds[];
        "the FX feeds, the trades feed and the crypto mock all publish on a timer"]};

bad:()

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
    .qstream.wire[`markout;{[t;x] '"tickerplant is down"}];
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

/ posbook reads the two normalizers' outputs, as the plant delivers them:
/ an executions row and a marks row, `time` stamped in front.
an_execution:{[ts;s;side;price;size]
    ([] time:enlist ts; source_time:enlist ts; sym:enlist s; venue:enlist `fx; side:enlist side;
        size:enlist size; price:enlist price; fee:enlist 0f; fee_ccy:enlist `; fill_id:enlist `)}

a_mark:{[s;mid] ([] time:enlist d 0; source_time:enlist d 0; sym:enlist s; venue:enlist `fx; mid:enlist mid)}

test_posbook_marks_a_fill_against_the_last_mark:{[t]
    reset[];
    .qsub.posbook.on_batch[`marks;a_mark[`EURUSD;1.104]];
    .qsub.posbook.on_batch[`executions;an_execution[d 0;`EURUSD;1;1.1;1e6]];
    out:last_rows[];
    .qunit.assertEquals[out[`mark_price];enlist 1.104;
        "the fill is marked to the last mid seen for its sym"]};

test_posbook_carries_the_book_between_batches:{[t]
    / The book is process state rebuilt from each batch's output - the one
    / thing a transform test cannot check, because the transform is pure.
    reset[];
    .qsub.posbook.on_batch[`executions;an_execution[d 0;`EURUSD;1;1.1;1e6]];
    .qsub.posbook.on_batch[`executions;an_execution[d 1;`EURUSD;-1;1.105;4e5]];
    .qunit.assertEquals[exec qty from 0!.qsub.posbook.book;enlist 6e5;
        "the second batch applies to the book the first one left"]};

test_posbook_publishes_one_row_per_fill:{[t]
    reset[];
    .qsub.posbook.on_batch[`executions;an_execution[d 0;`EURUSD;1;1.1;1e6],an_execution[d 1;`USDJPY;-1;150f;1e6]];
    .qunit.assertEquals[count last_rows[];2;
        "every fill in the batch produces a position row"]};

test_posbook_publishes_nothing_for_a_mark:{[t]
    reset[];
    .qsub.posbook.on_batch[`marks;a_mark[`EURUSD;1.1001]];
    .qunit.assertEquals[count .sjtest.published;0;
        "a mark only refreshes the cache, it is not a publication"]};

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

/ --- databento_book: the live fold ----------------------------------------

/ The job under test republishes rows folded by the SAME .qxf transform the
/ ODBC backfill applies. That sharing is the point of the job existing, so
/ the tests below check the wiring and the two things the job itself
/ decides - which table it listens to, and that the venue clock survives -
/ rather than re-testing the fold, which test_transform.q already runs
/ against its own declared examples.

/ Private: a live batch, shaped as it arrives off the tickerplant - the
/ source's own fixture with a `time` prepended, which is what .u.upd does
/ to every message on receipt.
mbp10_batch:{[] update time:.z.p from .qfeed.databento_mbp10.fixture[]}

test_databento_folds_a_live_batch_and_republishes_it:{[t]
    reset[];
    .qsub.databento_book.on_batch[`databento_mbp10;mbp10_batch[]];
    out:last_rows[];
    .qunit.assertEquals[
        (first exec tbl from .sjtest.published;count out;count out[0;`bid_prices]);
        (`databento_book;4;10);
        "four MBP-10 records fold into four book rows of ten levels a side"]};

test_databento_keeps_the_venue_clock_as_its_own_column:{[t]
    / THE POINT OF ts_event. .u.upd stamps `time` on receipt, so a book
    / carrying only `time` would say when it ARRIVED and nothing about when
    / it happened - and a feed stuck an hour behind would look current. A
    / Binance book in the cryptorust recorder was stamped 1973 for weeks
    / because the only clock came from the venue and nothing could
    / cross-check it.
    reset[];
    .qsub.databento_book.on_batch[`databento_mbp10;mbp10_batch[]];
    out:last_rows[];
    .qunit.assertEquals[`ts_event in cols out;1b;"the venue clock is carried through"];
    .qunit.assertEquals[out[0;`ts_event];(.qfeed.databento_mbp10.fixture[])[0;`ts_event];
        "and it is Databento's value, not the time the row was folded"]};

test_databento_does_not_republish_the_tickerplants_time:{[t]
    / One column too wide is a live `length error at the tickerplant, not a
    / warning - .qpipe.publish drops `time`, and this job must not hand it
    / one to drop in the first place.
    reset[];
    .qsub.databento_book.on_batch[`databento_mbp10;mbp10_batch[]];
    .qunit.assertEquals[`time in cols last_rows[];0b;
        "the receipt stamp is the tickerplant's to add, not this job's to send"]};

test_databento_ignores_a_table_it_did_not_subscribe_to:{[t]
    reset[];
    .qsub.databento_book.on_batch[`quotes;mbp10_batch[]];
    .qunit.assertEquals[count .sjtest.published;0;"a batch from another table is ignored"]};

test_databento_publishes_nothing_for_an_empty_batch:{[t]
    / An empty batch is ordinary on a quiet symbol. Publishing a zero-row
    / message would put an empty write on the tickerplant every tick.
    reset[];
    .qsub.databento_book.on_batch[`databento_mbp10;0#mbp10_batch[]];
    .qunit.assertEquals[count .sjtest.published;0;"an empty batch publishes nothing"]};

test_databento_keeps_no_state:{[t]
    reset[];
    .qsub.databento_book.on_batch[`databento_mbp10;mbp10_batch[]];
    .qsub.databento_book.on_batch[`databento_mbp10;mbp10_batch[]];
    .qunit.assertEquals[count .sjtest.published;2;"each batch stands alone"]};

/ --- the adapter's publish, over what the feeds actually send ------------

/ A handle that records the .u.upd message instead of sending it - the
/ integer-handle seam .qpipe.publish applies as h[(`.u.upd;tbl;data)].
sent:();
fake_handle:{[m] `.sjtest.sent set .sjtest.sent,enlist m; 1};

test_the_adapter_recognises_the_list_of_columns_form:{[t]
    / Every feed's row builder returns this shape, and so does cryptorust's
    / recorder. Refusing it was invisible while the running stack predated
    / the runner, because the old per-feed scripts sent to .u.upd directly.
    .qunit.assertTrue[.qpipe.is_columns .qsub.fx_trades_feed.fill_rows[0;1;1e6;0];
        "a fill's rows are the list-of-columns form"];
    .qunit.assertTrue[.qpipe.is_columns .qsub.quotes_feed.tick_rows .qsynth.spot;
        "and so is a depth tick, whose ladder columns are lists of vectors"];
    .qunit.assertTrue[.qpipe.is_columns .qsub.crypto_mock.fill_rows[`binance_spot;`$"BTC-USDT";1;62000f;0.25;`x];
        "and a mock crypto fill"]};

test_the_adapter_still_refuses_a_list_of_atoms:{[t]
    / Invariant 3's own trap: one row of atoms reads as one column each.
    .qunit.assertFalse[.qpipe.is_columns (`EURUSD;1;1.085);"a general list of atoms is not the columns form"];
    .qunit.assertFalse[.qpipe.is_columns ([] a:1 2);"and a table is a table, handled by as_table"];
    .qunit.assertFalse[.qpipe.is_columns ();"an empty list is nothing to publish"]};

test_the_adapter_sends_columns_straight_through:{[t]
    `.sjtest.sent set ();
    rows:.qsub.fx_trades_feed.fill_rows[0;1;1e6;0];
    n:.qpipe.publish[.sjtest.fake_handle;`trades;rows];
    .qunit.assertEquals[n;1;"one row, counted from the first column"];
    .qunit.assertEquals[.sjtest.sent 0;(`.u.upd;`trades;rows);
        "the message is .u.upd's own shape, untouched"]};

test_the_adapter_reshapes_a_table_and_a_dict_to_u_upds_shape:{[t]
    / The invariants as_table exists for: a keyed table is unkeyed (2), a
    / dict of atoms is one row of one-element vectors (3), and a `time`
    / column is stripped because the plant stamps its own (1).
    `.sjtest.sent set ();
    .qpipe.publish[.sjtest.fake_handle;`trades;([sym:enlist `EURUSD] time:enlist d 0; side:enlist 1)];
    .qunit.assertEquals[.sjtest.sent[0;2];(enlist `EURUSD;enlist 1);
        "a keyed table goes out unkeyed and without its time, as column vectors"];
    `.sjtest.sent set ();
    .qpipe.publish[.sjtest.fake_handle;`trades;`sym`side!(`EURUSD;1)];
    .qunit.assertEquals[.sjtest.sent[0;2];(enlist `EURUSD;enlist 1);
        "a dict of atoms is one row, every column a one-element vector"];
    .qunit.assertThrows[.qpipe.publish[.sjtest.fake_handle;`trades;];(`EURUSD;1);
        "*expected a table*";"a bare list of atoms is refused by name"]};

test_the_adapter_publishes_nothing_for_empty_columns:{[t]
    `.sjtest.sent set ();
    .qunit.assertEquals[.qpipe.publish[.sjtest.fake_handle;`trades;(`symbol$();`long$())];0;
        "empty columns are zero rows"];
    .qunit.assertEmpty[.sjtest.sent;"and nothing is sent"]};

/ --- crypto mock ----------------------------------------------------------

/ The exact column order and types cryptorust's kdb_fills_recorder.rs sends
/ in build_real_upd_message: symbol, symbol, long, float, float, float,
/ symbol, symbol. The mock's whole reason to exist is to be this, row for
/ row, so this is the assertion that matters most in the section.
recorder_types:11 11 7 9 9 9 11 11h

test_the_mock_publishes_a_book_and_then_fills:{[t]
    reset[];
    .qsub.crypto_mock.on_timer[];
    .qunit.assertEquals[first exec tbl from .sjtest.published;`crypto_book;
        "the book goes out first, so no fill prints at a price the tape has not shown"]};

test_a_mock_fill_is_the_recorders_wire_shape:{[t]
    / One message per fill, every column a one-element typed vector, no
    / time. Not a table: the recorder does not send one, and a mock that
    / sent the tidier shape would pass here and fail against the plant.
    reset[];
    r:.qsub.crypto_mock.fill_rows[`binance_spot;`$"BTC-USDT";1;62000f;0.25;`$"binance_spot-1"];
    .qunit.assertEquals[type each r;recorder_types;
        "sym, venue, side, trade_price, size, fee, fee_currency, exchange_fill_id - the recorder's types in the recorder's order"];
    .qunit.assertEquals[distinct count each r;enlist 1;"every column is a one-element vector, never an atom"];
    .qunit.assertEquals[count r;count 1_cols .qsub.executions.crypto_trades;
        "and there are exactly as many as crypto_trades has columns after time"]};

test_a_mock_fills_fee_is_the_venues_maker_bps_in_the_quote_currency:{[t]
    r:.qsub.crypto_mock.fill_rows[`bybit_spot;`$"ETH-USDT";-1;2000f;2f;`$"bybit_spot-7"];
    .testutil.assertApprox[first r 5;2000*2*8%10000;1e-9;"2 ETH at 2000 on bybit's 8bps is 3.2"];
    .qunit.assertEquals[first r 6;`USDT;"charged in the quote currency, which is what comes after the hyphen"]};

test_the_fill_model_skews_with_toxicity_the_way_cryptorust_does:{[t]
    / Positive toxicity is buy-heavy flow: it lifts the ASK more. Negative
    / hits the bid. The mock keeps the sign convention of QuoteFillSimulator.
    up:.qsub.crypto_mock.skewed[0.4;1f];
    down:.qsub.crypto_mock.skewed[0.4;-1f];
    .qunit.assertTrue[(up 1)>up 0;"buy-heavy flow makes the ask likelier to fill than the bid"];
    .qunit.assertTrue[(down 0)>down 1;"sell-heavy flow, the bid"];
    .qunit.assertEquals[.qsub.crypto_mock.skewed[0.4;0f];0.4 0.4;"and no toxicity leaves them equal"]};

test_a_fill_takes_a_capped_fraction_of_the_quote:{[t]
    row:first .qsub.crypto_mock.market;
    q:.qsub.crypto_mock.quote_size .qsub.crypto_mock.syms?row`sym;
    / draws: at the touch, both uniforms at 0 (certain fill), fractions 1
    decided:.qsub.crypto_mock.decide[row;1;(1b;0f;0f;1f;1f)];
    .qunit.assertEquals[count decided;2;"both sides filled"];
    .testutil.assertApprox[decided[0;2];q*.qsub.crypto_mock.max_fill_fraction;1e-12;
        "a fraction of 1 is capped at max_fill_fraction, as the simulator caps it"];
    .qunit.assertEquals[decided[;0];1 -1;"the bid fill is a buy and the ask fill a sell"]};

test_a_maker_not_at_the_touch_fills_nothing:{[t]
    row:first .qsub.crypto_mock.market;
    .qunit.assertEmpty[.qsub.crypto_mock.decide[row;1;(0b;0f;0f;1f;1f)];
        "outquoted, the maker fills nothing however the dice fall"]};

test_the_beta_draw_is_in_range:{[t]
    draws:{[i] .qsub.crypto_mock.beta_draw[.qsub.crypto_mock.fill_alpha;.qsub.crypto_mock.fill_beta]} each til 200;
    .qunit.assertTrue[all draws within 0 1f;"a Beta draw is a fraction"];
    / Beta(2,2) has mean a half; 200 draws sit within a few percent of it.
    .qunit.assertTrue[0.15>abs 0.5-avg draws;"and Beta(2,2) draws average about a half"]};

test_fill_ids_are_unique_per_venue:{[t]
    reset[];
    ids:.qsub.crypto_mock.next_id each 3#`binance_spot;
    .qunit.assertEquals[ids;`$("binance_spot-1";"binance_spot-2";"binance_spot-3");
        "monotonic within a venue, the way an exchange's own ids are"]};

test_the_mock_publishes_no_sim_fills:{[t]
    / The paper strategy's table. A mock that published it would tempt a
    / position engine into consuming it.
    .qunit.assertFalse[`crypto_sim_fills in .qstream.def[`crypto_mock]`publishes;
        "crypto_sim_fills is not something this mock claims to publish"]};

/ --- the normalizers and posbook over them -------------------------------

crypto_fill:{[s;side;price;size]
    ([] time:enlist d 0; sym:enlist s; venue:enlist `binance_spot; side:enlist side;
        trade_price:enlist price; size:enlist size; fee:enlist 0f;
        fee_currency:enlist `USDT; exchange_fill_id:enlist `$"binance_spot-1")}

fx_fill:{[s;side;price;size]
    ([] time:enlist d 0; sym:enlist s; side:enlist side; trade_price:enlist price;
        size:enlist size; pip_factor:enlist 10000)}

crypto_book_row:{[s;bid;ask]
    ([] time:enlist d 0; venue:enlist `binance_spot; sym:enlist s;
        bid_prices:enlist bid; bid_sizes:enlist 3#0.5; ask_prices:enlist ask; ask_sizes:enlist 3#0.5)}

/ Deliver a normalizer's published rows to posbook the way the plant would:
/ as a table with `time` stamped in front.
to_posbook:{[t;x] .qsub.posbook.on_batch[t;`time xcols update time:.sjtest.d 0 from x]; count x}

test_the_executions_normalizer_spells_both_fill_tables_one_way:{[t]
    reset[];
    .qsub.executions.on_batch[`trades;fx_fill[`EURUSD;1;1.085;1e6]];
    .qsub.executions.on_batch[`crypto_trades;crypto_fill[`$"BTC-USDT";-1;62000f;0.25]];
    out:raze first each exec rows from .sjtest.published;
    .qunit.assertEquals[distinct exec tbl from .sjtest.published;enlist `executions;
        "both go out on the one canonical table"];
    .qunit.assertEquals[exec venue from out;`fx`binance_spot;"each attributed to its venue"];
    .qunit.assertEquals[cols out;cols .qsub.executions.executions;"in the canonical columns"]};

test_a_normalizer_drops_a_table_that_is_not_its_source:{[t]
    reset[];
    .qsub.executions.on_batch[`quote;([] time:enlist d 0; sym:enlist `EURUSD; bid:enlist 1f; ask:enlist 1.1)];
    .qunit.assertEmpty[.sjtest.published;"a batch on a table that is not a source is not normalized"]};

test_posbook_folds_an_fx_and_a_crypto_fill_into_one_book:{[t]
    / The point of the normalizers: one job, one book, two markets, and
    / posbook itself knows nothing about either.
    reset[];
    to_posbook[`executions;.qnorm.normalize[`executions;`trades;fx_fill[`EURUSD;1;1.085;1e6]]];
    to_posbook[`executions;.qnorm.normalize[`executions;`crypto_trades;crypto_fill[`$"BTC-USDT";1;62000f;0.5]]];
    .qunit.assertEquals[count .qsub.posbook.book;2;"one position per instrument, whichever market"];
    .testutil.assertApprox[(.qsub.posbook.book`$"BTC-USDT")`qty;0.5;1e-12;"long half a bitcoin"];
    .testutil.assertApprox[(.qsub.posbook.book`EURUSD)`qty;1e6;1e-9;"and a million euros"]};

test_posbook_marks_to_whichever_book_the_marks_normalizer_saw:{[t]
    reset[];
    to_posbook[`marks;.qnorm.normalize[`marks;`crypto_book;crypto_book_row[`$"BTC-USDT";61999 61998 61997f;62001 62002 62003f]]];
    to_posbook[`marks;.qnorm.normalize[`marks;`quote;([] time:enlist d 0; sym:enlist `EURUSD; bid:enlist 1.0849; ask:enlist 1.0851)]];
    to_posbook[`executions;.qnorm.normalize[`executions;`crypto_trades;crypto_fill[`$"BTC-USDT";1;61000f;1f]]];
    row:last_rows[];
    .testutil.assertApprox[first row`mark_price;62000f;1e-9;"the crypto mid, off the ladder's first level"];
    .testutil.assertApprox[first row`unrealized_pnl;1000f;1e-9;"bought at 61000, marked at 62000"];
    .testutil.assertApprox[.qsub.posbook.last_mid`EURUSD;1.085;1e-9;"and the FX mid is cached alongside it"]};

test_posbook_no_longer_reads_the_raw_tables:{[t]
    .qunit.assertEquals[.qstream.def[`posbook]`subscribe_to;`executions`marks;
        "posbook subscribes to the two normalizers and nothing else"];
    reset[];
    .qsub.posbook.on_batch[`trades;fx_fill[`EURUSD;1;1.085;1e6]];
    .qunit.assertEmpty[.qsub.posbook.book;"a raw trades batch, were one to arrive, moves nothing"]};

test_the_mock_reaches_posbook_through_both_normalizers:{[t]
    / The mock's rows, delivered the way the plant would deliver them, run
    / through executions and marks and land in the one book.
    reset[];
    as_table:{[cols_after_time;r] update time:.sjtest.d 0 from flip cols_after_time!r};
    .qstream.wire[`crypto_mock;{[as_table;tbl;r]
        norm:$[tbl=`crypto_book;`marks;`executions];
        .sjtest.to_posbook[norm;.qnorm.normalize[norm;tbl;`time xcols as_table[
            $[tbl=`crypto_book;`venue`sym`bid_prices`bid_sizes`ask_prices`ask_sizes;
              `sym`venue`side`trade_price`size`fee`fee_currency`exchange_fill_id];r]]];
        1}[as_table]];
    do[20;.qsub.crypto_mock.on_timer[]];
    .qunit.assertTrue[0<count .qsub.posbook.book;
        "twenty ticks at a 30% touch share is enough for at least one fill to reach the book"];
    .qunit.assertTrue[all (exec sym from .qsub.posbook.book) in .qsub.crypto_mock.syms;
        "and every position is in a symbol the mock trades"];
    .qunit.assertTrue[all (exec sym from .qsub.posbook.book) in key .qsub.posbook.last_mid;
        "each marked to a mid the marks normalizer produced from the mock's own book"]};
/ --- fx orders feed -------------------------------------------------------

test_the_orders_feed_publishes_one_order_a_tick:{[t]
    reset[];
    .qsub.fx_orders_feed.on_timer[];
    .qunit.assertEquals[(count .sjtest.published;first exec tbl from .sjtest.published);(1;`orders);
        "a tick publishes exactly one order, onto orders"]};

test_an_orders_row_is_one_element_vectors:{[t]
    / The plant takes its row count from column length, so an atom column
    / makes a one-row batch read as a one-COLUMN batch.
    reset[];
    .qsub.fx_orders_feed.on_timer[];
    .qunit.assertEquals[distinct count each last_rows[];enlist 1;
        "every column is a one-element vector, never a bare atom"]};

test_the_orders_feed_emits_statuses_that_are_not_fills:{[t]
    / The positions service exists partly to filter these out, and a feed
    / that only ever emitted fills would let it be written without a
    / filter and still pass.
    .qunit.assertTrue[any not .qsub.fx_orders_feed.statuses=.qsub.fx_positions.filled_status;
        "order flow carries cancels and rejects, not only fills"]};

test_an_order_id_is_unique_within_a_run:{[t]
    reset[];
    .qsub.fx_orders_feed.on_timer[];
    .qsub.fx_orders_feed.on_timer[];
    / The feed publishes a list of column vectors, so each recorded batch
    / is that list and its order ids are the first of them.
    ids:raze first each first each exec rows from .sjtest.published;
    .qunit.assertEquals[count distinct ids;2;"two orders, two ids"]};

/ --- fx positions ---------------------------------------------------------

/ Four orders: a buy and a sell that net on one book, a cancel that must
/ not count, and a fill on a second book that must not merge with the
/ first. Round prices so every expected figure is exact.
orders_batch:{[]
    ([] time:d each til 4; order_id:1 2 3 4;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD;
        book:`london`london`london`newyork;
        product:`spot`spot`spot`spot;
        side:1 -1 1 1;
        size:1000000 400000 5000000 250000f;
        price:1.0850 1.0860 1.0855 1.0851;
        order_status:`filled`filled`cancelled`filled)}

test_positions_net_only_filled_orders:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qunit.assertEquals[count .qsub.fx_positions.book;2;
        "two positions - the cancelled 5mm buy is not one of them"];
    .testutil.assertApprox[.qsub.fx_positions.book[`EURUSD`london`spot]`base_qty;600000f;1e-9;
        "and it did not move london's, which is 1mm bought less 400k sold"]};

test_positions_keep_the_books_apart:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .testutil.assertApprox[.qsub.fx_positions.book[`EURUSD`newyork`spot]`base_qty;250000f;1e-9;
        "newyork's fill is its own position, not netted into london's"]};

test_positions_ignore_a_table_they_did_not_subscribe_to:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`trades;([] time:enlist d 0; sym:enlist `EURUSD;
        side:enlist 1; trade_price:enlist 1.085; size:enlist 1e6; pip_factor:enlist 10000)];
    .qunit.assertEmpty[.qsub.fx_positions.book;"a batch on another table moves nothing"]};

test_positions_accumulate_across_batches:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_batch[`orders;([] time:enlist d 9; order_id:enlist 9;
        sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        side:enlist 1; size:enlist 400000f; price:enlist 1.0870;
        order_status:enlist `filled)];
    .testutil.assertApprox[.qsub.fx_positions.book[`EURUSD`london`spot]`base_qty;1000000f;1e-9;
        "the second batch is added to the book, not substituted for it"]};

test_the_book_changes_before_anything_is_published:{[t]
    / Orders will not be redelivered, so a failed publish must not also
    / lose them from the position - the ordering posbook established.
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qunit.assertEmpty[.sjtest.published;"the batch handler publishes nothing at all"];
    .qunit.assertEquals[count .qsub.fx_positions.book;2;"while the book has already moved"]};

/ --- the snapshot ---------------------------------------------------------

test_the_timer_publishes_the_whole_book:{[t]
    / Not just what moved: a snapshot carrying only changes would need its
    / reader to keep the rest, which is the reader building a second copy
    / of this service's state and getting it wrong on the first drop.
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[(count .sjtest.published;first exec tbl from .sjtest.published);(1;`fx_position);
        "one publication, onto fx_position"];
    .qunit.assertEquals[count last_rows[];2;"carrying every position, not only the one that last moved"]};

test_the_snapshot_carries_a_break_even_rate:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    .testutil.assertApprox[
        first exec break_even from last_rows[] where book=`london;650600%600000;1e-12;
        "650,600 USD paid out over 600k EUR held"]};

test_an_empty_book_publishes_no_snapshot:{[t]
    reset[];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEmpty[.sjtest.published;
        "a service that has seen nothing has nothing to say, rather than an empty table every five seconds"]};

test_the_snapshot_matches_the_declared_stack_table:{[t]
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[cols last_rows[];cols .qsub.fx_positions.fx_position;
        "the published columns are the job's declared output shape"]};

/ --- limits ---------------------------------------------------------------

mk_limits:{[]
    ([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        metric:enlist `base_qty; cap:enlist 100000f; severity:enlist `hard)}

test_no_limits_means_no_breaches:{[t]
    / A service with no limits reports positions and polices nothing,
    / which is a legitimate way to run and better than inventing caps.
    reset[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[distinct exec tbl from .sjtest.published;enlist `fx_position;
        "the snapshot goes out and nothing else"]};

test_a_breached_limit_is_published:{[t]
    reset[];
    .qsub.fx_positions.load_limits mk_limits[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[asc exec tbl from .sjtest.published;`s#`fx_limit_breach`fx_position;
        "the snapshot and the breach both go out"];
    breach:first first exec rows from .sjtest.published where tbl=`fx_limit_breach;
    .testutil.assertApprox[first breach`utilisation;6f;1e-9;"600k against a 100k cap is six times the limit"]};

test_a_standing_breach_does_not_republish_every_tick:{[t]
    / A breach is a state, not an event: without throttling the desk gets
    / one alert per timer tick until someone trades out of the position.
    reset[];
    .qsub.fx_positions.load_limits mk_limits[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    `.sjtest.published set 0#.sjtest.published;
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[exec tbl from .sjtest.published;enlist `fx_position;
        "the second tick republishes the book but not the breach"]};

test_a_position_that_moves_further_over_is_still_one_breach:{[t]
    reset[];
    .qsub.fx_positions.load_limits mk_limits[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qsub.fx_positions.on_timer[];
    `.sjtest.published set 0#.sjtest.published;
    .qsub.fx_positions.on_batch[`orders;([] time:enlist d 9; order_id:enlist 9;
        sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        side:enlist 1; size:enlist 2000000f; price:enlist 1.0870;
        order_status:enlist `filled)];
    .qsub.fx_positions.on_timer[];
    .qunit.assertEquals[exec tbl from .sjtest.published;enlist `fx_position;
        "the identity is the scope and the metric, never the observed value, so a worsening breach is the same breach"]};

test_a_malformed_limits_table_is_refused_at_load:{[t]
    reset[];
    .qunit.assertThrows[.qsub.fx_positions.load_limits;
        ([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist -1f);
        "*must be positive*";
        "a limits table that polices nothing is indistinguishable from a quiet day, so it is refused where it is loaded"]};

test_a_limit_scoped_on_a_non_dimension_is_refused_at_load:{[t]
    / .qlimit cannot catch this - it is never told which columns are meant
    / to be scope, so a stray one silently becomes scope and the limit
    / matches nothing. The job knows its own dimensions, so it can.
    reset[];
    .qunit.assertThrows[.qsub.fx_positions.load_limits;
        ([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
            base_qty:enlist 1e6; metric:enlist `base_qty; cap:enlist 1f);
        "*is not a dimension of this book*";
        "a limits table carrying an extra column would police nothing, and would look exactly like a quiet day"]};

test_fresh_breaches_is_decidable_without_a_timer:{[t]
    reset[];
    .qsub.fx_positions.load_limits mk_limits[];
    .qsub.fx_positions.on_batch[`orders;orders_batch[]];
    .qunit.assertEquals[count .qsub.fx_positions.fresh_breaches d 0;1;"the first evaluation alerts"];
    .qunit.assertEmpty[.qsub.fx_positions.fresh_breaches d 1;"a second, a second later, does not"];
    .qunit.assertEquals[count .qsub.fx_positions.fresh_breaches d 600;1;
        "and once the throttle period has passed it does again"]};

\d .
