// test_stream_poll.q - polling feeds (.qetl.job.stream poll, tick and
// preview, src/etl/core/stream_poll.q) (.streampolltest).
//
// What preview promises is mostly an absence - nothing published, no cursor
// written - so most of these assert one: a recorder that counts publishes,
// and the cursor file read back after the preview.
//
// The test feeds register as ordinary streaming jobs, so each test removes
// them again: a feed left registered would reach the suites that walk every
// job (.jobouttest needs a driver for each one that publishes).

\d .streampolltest

d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ The upstream a test feed polls: three rows, one a day.
upstream:([] ts:d 1 2 3; v:1.5 2.5 3.5)

/ What the feeds publish, and whether their close ran.
sent:([] tbl:`symbol$(); n:`long$())
closed:0b

record:{[t;rows] `.streampolltest.sent upsert (t;count rows); count rows}

feeds:`spt_feed`spt_quote`spt_broken
procs:`spt_feed1`spt_quote1`spt_broken1

/ A polling feed over `upstream`, publishing `out` through `normalize`.
declare:{[job;out;normalize;fetch]
    .qetl.job.stream.define[job;`procname`subscribe_to`publishes`period`poll!(
        `$string[job],"1";`symbol$();enlist out;0D00:00:01;
        `fetch`normalize`next_cursor`close!(fetch;normalize;{[page] last page`ts};
            {[] `.streampolltest.closed set 1b}))];
    (` sv .qetl.job.stream.namespace[job],`publish) set .streampolltest.record;
    job}

after:{[cursor] select from .streampolltest.upstream where ts>cursor}

forget:{[]
    `.qetl.job.stream.jobs set .streampolltest.feeds _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set .streampolltest.procs _ .qetl.job.stream.procnames;
    .qetl.job.continuous.clear_cursor each .streampolltest.feeds;}

beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .streampolltest.forget[];
    `.streampolltest.sent set 0#.streampolltest.sent;
    `.streampolltest.closed set 0b;
    .streampolltest.declare[`spt_feed;`spt_out;{[page] select ts, v from page};.streampolltest.after];
    }

tearDown_forget:{[] .streampolltest.forget[];}

cursor_file_exists:{[job] 0<count key hsym `$.qetl.job.continuous.cursor_path job}

/ --- preview -------------------------------------------------------------

test_preview_reports_the_page_it_would_publish:{[t]
    r:.qetl.job.stream.preview[`spt_feed;2];
    .qunit.assertEquals[r`state`fetched`next_cursor`advances`live;(`previewed;3;d 3;1b;`unknown);
        "one page of three rows, a cursor it would move to, and no source named to judge liveness by"];
    .qunit.assertEquals[(r`rows;count each r`sample);(enlist[`spt_out]!enlist 3;enlist[`spt_out]!enlist 2);
        "every row counted, a sample of two"]};

test_preview_publishes_nothing:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[count .streampolltest.sent;0;"the feed's publish was never called"]};

test_preview_writes_no_cursor:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(.streampolltest.cursor_file_exists `spt_feed;null .qetl.job.continuous.load_cursor `spt_feed);11b;
        "no cursor file appears, so a run still starts where it would have"]};

test_preview_leaves_a_saved_cursor_alone:{[t]
    .qetl.job.continuous.save_cursor[`spt_feed;d 1];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(r`cursor;r`fetched;.qetl.job.continuous.load_cursor `spt_feed);(d 1;2;d 1);
        "it reads from the saved cursor and leaves it where it was"]};

test_an_empty_page_is_idle:{[t]
    .qetl.job.continuous.save_cursor[`spt_feed;d 3];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[r`state`fetched`advances;(`idle;0;0b);"nothing new is idle, not a failure"]};

test_close_runs_after_a_preview:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[.streampolltest.closed;1b;"what fetch opened is released"]};

test_a_failing_fetch_raises_and_still_closes:{[t]
    .streampolltest.declare[`spt_broken;`spt_out;{[page] page};{[cursor] '"source unreachable"}];
    .qunit.assertThrows[{.qetl.job.stream.preview[`spt_broken;5]};::;"source unreachable";
        "the source's own error, not a quiet empty page"];
    .qunit.assertEquals[.streampolltest.closed;1b;"close runs on failure too"]};

test_output_that_does_not_fit_the_plant_is_invalid:{[t]
    .streampolltest.declare[`spt_quote;`arbitrage;{[page] select sym:`a, wrong:v from page};.streampolltest.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[r`state;`invalid;"a table the plant would mis-store is reported"];
    .qunit.assertTrue[(first r`failures) like "arbitrage has columns*";"naming the table and its columns"]};

test_output_that_fits_the_plant_is_previewed:{[t]
    .streampolltest.declare[`spt_quote;`arbitrage;{[page] .qetl.plant.published `arbitrage};.streampolltest.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[(r`state;r`failures);(`previewed;());"the plant's own published shape passes"]};

test_a_cursor_that_would_not_advance_is_invalid:{[t]
    .streampolltest.forget[];
    .qetl.job.stream.define[`spt_feed;`procname`subscribe_to`publishes`period`poll!(
        `spt_feed1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor!(.streampolltest.after;{[page] page};{[page] 0Np}))];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(r`state;r`advances);(`invalid;0b);"a run would refuse this cursor, so the preview says so"]};

test_a_subscriber_cannot_be_previewed:{[t]
    .qunit.assertThrows[{.qetl.job.stream.preview[`markout;5]};::;"*subscribes to*";
        "its input comes from the plant - there is no page to fetch"]};

test_a_feed_without_poll_cannot_be_previewed:{[t]
    .qunit.assertThrows[{.qetl.job.stream.preview[`fx_feed;5]};::;"*declares no poll*";
        "running its on_timer would publish"]};

/ --- the timer built from poll --------------------------------------------

test_a_tick_publishes_then_advances:{[t]
    r:.qetl.job.stream.tick `spt_feed;
    .qunit.assertEquals[(r`state;r`rows;exec sum n from .streampolltest.sent;.qetl.job.continuous.load_cursor `spt_feed);
        (`published;3;3;d 3);"three rows published and the cursor moved past them"]};

test_the_generated_timer_ticks:{[t]
    (.qetl.job.stream.def[`spt_feed]`on_timer)[];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor `spt_feed;d 3;"on_timer is the tick"]};

test_after_a_tick_the_preview_is_idle:{[t]
    .qetl.job.stream.tick `spt_feed;
    .qunit.assertEquals[.qetl.job.stream.preview[`spt_feed;5]`state;`idle;"what was published is not fetched again"]};

/ --- what define refuses ---------------------------------------------------

poll_decl:{[extra]
    (`procname`subscribe_to`publishes`period`poll!(`spt_broken1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor!({[c] ([] ts:`timestamp$())};{[p] p};{[p] 0Np}))),extra}

test_define_refuses_poll_with_on_timer:{[t]
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};
        .streampolltest.poll_decl enlist[`on_timer]!enlist {[] ::};"*both poll and on_timer*";
        "the timer is built from poll, so a second one would be ignored or doubled"]};

test_define_refuses_poll_without_period:{[t]
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};
        `period _ .streampolltest.poll_decl ()!();"*without period*";"how often is part of the declaration"]};

test_define_refuses_a_poll_missing_a_step:{[t]
    d:.streampolltest.poll_decl ()!();
    d[`poll]:`next_cursor _ d`poll;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*missing next_cursor";"each step is named"]};

test_define_refuses_poll_on_a_subscriber:{[t]
    d:.streampolltest.poll_decl ()!();
    d[`subscribe_to]:enlist `quote;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*declares poll and subscribes*";
        "a polling job fetches its own input"]};

\d .
