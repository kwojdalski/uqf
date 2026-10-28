// test_stream_poll.q - polling feeds (.qetl.job.stream poll, tick and
// preview, src/etl/core/stream_poll.q) (.qt.qetl.job.stream.poll).
//
// What preview promises is mostly an absence - nothing published, no cursor
// written - so most of these assert one: a recorder that counts publishes,
// and the cursor file read back after the preview.
//
// The test feeds register as ordinary streaming jobs, so each test removes
// them again: a feed left registered would reach the suites that walk every
// job (.qt.qpipe.job_output_contracts needs a driver for each one that publishes).


.qt.qetl.job.stream.poll.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ The upstream a test feed polls: three rows, one a day.
.qt.qetl.job.stream.poll.upstream:([] ts:.qt.qetl.job.stream.poll.d[1 2 3]; v:1.5 2.5 3.5)

/ What the feeds publish, and whether their close ran.
.qt.qetl.job.stream.poll.sent:([] tbl:`symbol$(); n:`long$())
.qt.qetl.job.stream.poll.closed:0b

.qt.qetl.job.stream.poll.record:{[t;rows] `.qt.qetl.job.stream.poll.sent upsert (t;count rows); count rows}

.qt.qetl.job.stream.poll.feeds:`spt_feed`spt_quote`spt_broken`spt_compound
.qt.qetl.job.stream.poll.procs:`spt_feed1`spt_quote1`spt_broken1`spt_compound1

/ A polling feed over `upstream`, publishing `out` through `normalize`.
.qt.qetl.job.stream.poll.declare:{[job;out;normalize;fetch]
    .qetl.job.stream.define[job;`procname`subscribe_to`publishes`period`poll!(
        `$string[job],"1";`symbol$();enlist out;0D00:00:01;
        `fetch`normalize`next_cursor`close!(fetch;normalize;{[page] last page`ts};
            {[] `.qt.qetl.job.stream.poll.closed set 1b}))];
    (` sv .qetl.job.stream.namespace[job],`publish) set .qt.qetl.job.stream.poll.record;
    job}

.qt.qetl.job.stream.poll.after:{[cursor] select from .qt.qetl.job.stream.poll.upstream where ts>cursor}

.qt.qetl.job.stream.poll.forget:{[]
    `.qetl.job.stream.jobs set .qt.qetl.job.stream.poll.feeds _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set .qt.qetl.job.stream.poll.procs _ .qetl.job.stream.procnames;
    .qetl.job.continuous.clear_cursor each .qt.qetl.job.stream.poll.feeds;}

.qt.qetl.job.stream.poll.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.job.stream.poll.setUp_fresh:{[]
    .qt.qetl.job.stream.poll.forget[];
    `.qt.qetl.job.stream.poll.sent set 0#.qt.qetl.job.stream.poll.sent;
    `.qt.qetl.job.stream.poll.closed set 0b;
    .qt.qetl.job.stream.poll.declare[`spt_feed;`spt_out;{[page] select ts, v from page};.qt.qetl.job.stream.poll.after];
    }

.qt.qetl.job.stream.poll.tearDown_forget:{[] .qt.qetl.job.stream.poll.forget[];}

.qt.qetl.job.stream.poll.cursor_file_exists:{[job] 0<count key hsym `$.qetl.job.continuous.cursor_path job}

/ --- preview -------------------------------------------------------------

.qt.qetl.job.stream.poll.test_preview_reports_the_page_it_would_publish:{[t]
    r:.qetl.job.stream.preview[`spt_feed;2];
    .qunit.assertEquals[r`state`fetched`next_cursor`advances`live;(`previewed;3;.qt.qetl.job.stream.poll.d[3];1b;`unknown);
        "one page of three rows, a cursor it would move to, and no source named to judge liveness by"];
    .qunit.assertEquals[(r`rows;count each r`sample);(enlist[`spt_out]!enlist 3;enlist[`spt_out]!enlist 2);
        "every row counted, a sample of two"]};

.qt.qetl.job.stream.poll.test_preview_publishes_nothing:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[count .qt.qetl.job.stream.poll.sent;0;"the feed's publish was never called"]};

.qt.qetl.job.stream.poll.test_preview_writes_no_cursor:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(.qt.qetl.job.stream.poll.cursor_file_exists[`spt_feed];null .qetl.job.continuous.load_cursor `spt_feed);01b;
        "no cursor file appears, so a run still starts where it would have"]};

.qt.qetl.job.stream.poll.test_preview_leaves_a_saved_cursor_alone:{[t]
    .qetl.job.continuous.save_cursor[`spt_feed;.qt.qetl.job.stream.poll.d[1]];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(r`cursor;r`fetched;.qetl.job.continuous.load_cursor `spt_feed);(.qt.qetl.job.stream.poll.d[1];2;.qt.qetl.job.stream.poll.d[1]);
        "it reads from the saved cursor and leaves it where it was"]};

.qt.qetl.job.stream.poll.test_an_empty_page_is_idle:{[t]
    .qetl.job.continuous.save_cursor[`spt_feed;.qt.qetl.job.stream.poll.d[3]];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[r`state`fetched`advances;(`idle;0;0b);"nothing new is idle, not a failure"]};

.qt.qetl.job.stream.poll.test_close_runs_after_a_preview:{[t]
    .qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[.qt.qetl.job.stream.poll.closed;1b;"what fetch opened is released"]};

.qt.qetl.job.stream.poll.test_a_failing_fetch_raises_and_still_closes:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_broken;`spt_out;{[page] page};{[cursor] '"source unreachable"}];
    .qunit.assertThrows[{.qetl.job.stream.preview[`spt_broken;5]};::;"source unreachable";
        "the source's own error, not a quiet empty page"];
    .qunit.assertEquals[.qt.qetl.job.stream.poll.closed;1b;"close runs on failure too"]};

.qt.qetl.job.stream.poll.test_output_that_does_not_fit_the_plant_is_invalid:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`arbitrage;{[page] select sym:`a, wrong:v from page};.qt.qetl.job.stream.poll.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[r`state;`invalid;"a table the plant would mis-store is reported"];
    .qunit.assertTrue[(first r`failures) like "arbitrage has columns*";"naming the table and its columns"]};

.qt.qetl.job.stream.poll.test_output_that_fits_the_plant_is_previewed:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`arbitrage;{[page] .qetl.plant.published `arbitrage};.qt.qetl.job.stream.poll.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[(r`state;r`failures);(`previewed;());"the plant's own published shape passes"]};

/ The bug a polling sidecar hit: a populated ladder was compared with the
/ empty schema's " ", and every valid book page was reported invalid.
.qt.qetl.job.stream.poll.ladders:{[n;bids] ([] sym:n#`EURUSD; bid_prices:bids; ask_prices:n#enlist 1.11 1.12)}

.qt.qetl.job.stream.poll.test_populated_ladders_preview_as_valid:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`mkt_orderbook;{[page] .qt.qetl.job.stream.poll.ladders[count page;(count page)#enlist 1.1 1.09]};.qt.qetl.job.stream.poll.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[(r`state;r`failures);(`previewed;());"float vectors are what mkt_orderbook declares its ladders hold"]};

.qt.qetl.job.stream.poll.test_atoms_where_ladders_belong_are_invalid_in_preview:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`mkt_orderbook;{[page] .qt.qetl.job.stream.poll.ladders[count page;(count page)#1.1]};.qt.qetl.job.stream.poll.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[r`state;`invalid;"one price per row is not a ladder"];
    .qunit.assertTrue[(first r`failures) like "mkt_orderbook.bid_prices row 0 holds*";"named by table, column and row"]};

/ The timer holds a page to the same contract, and refuses it whole: nothing
/ published, and the cursor where it was, so the page is fetched again.
.qt.qetl.job.stream.poll.test_the_timer_refuses_a_page_the_plant_would_not_take:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`mkt_orderbook;{[page] .qt.qetl.job.stream.poll.ladders[count page;(count page)#1.1]};.qt.qetl.job.stream.poll.after];
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_quote};::;"tick: spt_quote would publish what the plant does not take - mkt_orderbook.bid_prices*";
        "the same failure the preview reports"];
    .qunit.assertEquals[(count .qt.qetl.job.stream.poll.sent;null .qetl.job.continuous.load_cursor `spt_quote);(0;1b);
        "nothing published and no cursor saved"]};

.qt.qetl.job.stream.poll.test_the_timer_publishes_a_page_that_fits:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`mkt_orderbook;{[page] .qt.qetl.job.stream.poll.ladders[count page;(count page)#enlist 1.1 1.09]};.qt.qetl.job.stream.poll.after];
    r:.qetl.job.stream.tick `spt_quote;
    .qunit.assertEquals[(r`state;count .qt.qetl.job.stream.poll.sent);(`published;1);"a valid page goes out"]};

/ A fetch that sends through .qetl.source.ipc_call is traced, and every line
/ it logs names the job and the cursor it fetched after - what `uqs job
/ preview --trace` shows.
.qt.qetl.job.stream.poll.test_a_traced_fetch_names_its_job_and_cursor:{[t]
    .qt.qetl.job.stream.poll.declare[`spt_quote;`spt_out;{[page] select ts, v from page};
        {[cursor] .qetl.source.ipc_call[{value x};.qt.qetl.job.stream.poll.after;enlist cursor]}];
    .qetl.job.continuous.save_cursor[`spt_quote;.qt.qetl.job.stream.poll.d[1]];
    lines:.qt.support.captured_log[1b] {.qetl.job.stream.preview[`spt_quote;5]};
    sent:first lines[;3] where lines[;2]~\:"query sent";
    .qunit.assertEquals[(sent`job;sent`cursor;sent`args);(`spt_quote;.qt.qetl.job.stream.poll.d[1];enlist .qt.qetl.job.stream.poll.d 1);
        "the query, the job that sent it and the cursor it fetched after"]};

.qt.qetl.job.stream.poll.test_a_cursor_that_would_not_advance_is_invalid:{[t]
    .qt.qetl.job.stream.poll.forget[];
    .qetl.job.stream.define[`spt_feed;`procname`subscribe_to`publishes`period`poll!(
        `spt_feed1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor!(.qt.qetl.job.stream.poll.after;{[page] page};{[page] 0Np}))];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(r`state;r`advances);(`invalid;0b);"a run would refuse this cursor, so the preview says so"]};

.qt.qetl.job.stream.poll.test_a_subscriber_cannot_be_previewed:{[t]
    .qunit.assertThrows[{.qetl.job.stream.preview[`demo_markout;5]};::;"*subscribes to*";
        "its input comes from the plant - there is no page to fetch"]};

.qt.qetl.job.stream.poll.test_a_feed_without_poll_cannot_be_previewed:{[t]
    .qunit.assertThrows[{.qetl.job.stream.preview[`fx_feed;5]};::;"*declares no poll*";
        "running its on_timer would publish"]};

/ --- the timer built from poll --------------------------------------------

.qt.qetl.job.stream.poll.test_a_tick_publishes_then_advances:{[t]
    r:.qetl.job.stream.tick `spt_feed;
    .qunit.assertEquals[(r`state;r`rows;exec sum n from .qt.qetl.job.stream.poll.sent;.qetl.job.continuous.load_cursor `spt_feed);
        (`published;3;3;.qt.qetl.job.stream.poll.d[3]);"three rows published and the cursor moved past them"]};

.qt.qetl.job.stream.poll.test_the_generated_timer_ticks:{[t]
    (.qetl.job.stream.def[`spt_feed]`on_timer)[];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor `spt_feed;.qt.qetl.job.stream.poll.d[3];"on_timer is the tick"]};

.qt.qetl.job.stream.poll.test_after_a_tick_the_preview_is_idle:{[t]
    .qetl.job.stream.tick `spt_feed;
    .qunit.assertEquals[.qetl.job.stream.preview[`spt_feed;5]`state;`idle;"what was published is not fetched again"]};

/ --- what define refuses ---------------------------------------------------

.qt.qetl.job.stream.poll.poll_decl:{[extra]
    (`procname`subscribe_to`publishes`period`poll!(`spt_broken1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor!({[c] ([] ts:`timestamp$())};{[p] p};{[p] 0Np}))),extra}

.qt.qetl.job.stream.poll.test_define_refuses_poll_with_on_timer:{[t]
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};
        .qt.qetl.job.stream.poll.poll_decl[enlist[`on_timer]!enlist {[] ::}];"*both poll and on_timer*";
        "the timer is built from poll, so a second one would be ignored or doubled"]};

.qt.qetl.job.stream.poll.test_define_refuses_poll_without_period:{[t]
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};
        `period _ .qt.qetl.job.stream.poll.poll_decl ()!();"*without period*";"how often is part of the declaration"]};

.qt.qetl.job.stream.poll.test_define_refuses_a_poll_missing_a_step:{[t]
    d:.qt.qetl.job.stream.poll.poll_decl[()!()];
    d[`poll]:`next_cursor _ d`poll;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*missing next_cursor";"each step is named"]};

.qt.qetl.job.stream.poll.test_define_refuses_poll_on_a_subscriber:{[t]
    d:.qt.qetl.job.stream.poll.poll_decl[()!()];
    d[`subscribe_to]:enlist `quote;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*declares poll and subscribes*";
        "a polling job fetches its own input"]};

/ --- a compound cursor (#666) ----------------------------------------------

/ Three rows share a timestamp: a page of two cannot be acknowledged by a
/ time alone, so the cursor is the row's position.
.qt.qetl.job.stream.poll.book:([] time:.qt.qetl.job.stream.poll.d[1 1 1 2]; securityId:`a`b`c`a; priceBookType:`x`x`x`x; v:1 2 3 4f)
.qt.qetl.job.stream.poll.fields:`time`securityId`priceBookType

/ The page of two after `cursor`, in the source's sort order.
.qt.qetl.job.stream.poll.page_of_two:{[cursor]
    after:$[(::)~cursor; .qt.qetl.job.stream.poll.book;
        .qt.qetl.job.stream.poll.book where .qetl.job.continuous.lexically_after[.qt.qetl.job.stream.poll.fields;cursor] each .qt.qetl.job.stream.poll.book];
    2 sublist after}

.qt.qetl.job.stream.poll.compound:{[advances]
    .qetl.job.stream.define[`spt_compound;`procname`subscribe_to`publishes`period`poll!(
        `spt_compound1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor`load`save`advances!(
            .qt.qetl.job.stream.poll.page_of_two;
            {[page] select time, securityId, v from page};
            {[page] .qt.qetl.job.stream.poll.fields#last page};
            .qetl.job.continuous.load_cursor_value;
            .qetl.job.continuous.save_cursor_value;
            advances))];
    (` sv .qetl.job.stream.namespace[`spt_compound],`publish) set .qt.qetl.job.stream.poll.record;
    `spt_compound}

.qt.qetl.job.stream.poll.test_a_compound_cursor_pages_through_rows_sharing_a_timestamp:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    .qetl.job.stream.tick `spt_compound;
    mid:.qetl.job.continuous.load_cursor_value `spt_compound;
    .qetl.job.stream.tick `spt_compound;
    .qunit.assertEquals[(mid;exec sum n from .qt.qetl.job.stream.poll.sent);
        (.qt.qetl.job.stream.poll.fields!(.qt.qetl.job.stream.poll.d[1];`b;`x);4);
        "the first page stops at b inside one timestamp, the second takes c and the next day - every row once"]};

.qt.qetl.job.stream.poll.test_a_compound_cursor_keeps_its_tie_breakers:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    .qetl.job.stream.tick `spt_compound;
    .qunit.assertEquals[key .qetl.job.continuous.load_cursor_value `spt_compound;.qt.qetl.job.stream.poll.fields;
        "securityId and priceBookType survive the file, not just the time"]};

.qt.qetl.job.stream.poll.test_a_compound_preview_leaves_the_cursor_file_alone:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    .qetl.job.stream.tick `spt_compound;
    before:.qetl.job.continuous.load_cursor_value `spt_compound;
    r:.qetl.job.stream.preview[`spt_compound;5];
    .qunit.assertEquals[(r`state;r`fetched;r`next_cursor;.qetl.job.continuous.load_cursor_value `spt_compound);
        (`previewed;2;.qt.qetl.job.stream.poll.fields!(.qt.qetl.job.stream.poll.d[2];`a;`x);before);
        "it proposes the next position and the saved one is untouched"]};

.qt.qetl.job.stream.poll.test_a_failed_publish_saves_no_compound_cursor:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    (` sv .qetl.job.stream.namespace[`spt_compound],`publish) set {[t;rows] '"plant refused"};
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_compound};::;"plant refused";"the publish error is the tick's"];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor_value `spt_compound;(::);
        "nothing published, so nothing acknowledged - the page is fetched again"]};

.qt.qetl.job.stream.poll.test_a_compound_cursor_that_does_not_advance_is_refused_in_both_modes:{[t]
    .qt.qetl.job.stream.poll.compound {[current;proposed] 0b};
    r:.qetl.job.stream.preview[`spt_compound;5];
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_compound};::;"*does not move past*";"the timer refuses"];
    .qunit.assertEquals[(r`state;count .qt.qetl.job.stream.poll.sent);(`invalid;0);
        "the preview reports it, and the timer refused before publishing anything"]};

.qt.qetl.job.stream.poll.test_define_refuses_half_a_custom_cursor:{[t]
    d:.qt.qetl.job.stream.poll.poll_decl[()!()];
    d[`poll]:d[`poll],enlist[`load]!enlist .qetl.job.continuous.load_cursor_value;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*load, save and advances together*";
        "a cursor loaded one way and saved another drifts"]};

.qt.qetl.job.stream.poll.test_lexically_after_compares_the_first_differing_field:{[t]
    f:.qetl.job.continuous.lexically_after[`t`id];
    .qunit.assertEquals[(f[`t`id!(1;`a);`t`id!(1;`b)];f[`t`id!(1;`b);`t`id!(2;`a)];f[`t`id!(1;`b);`t`id!(1;`b)];f[(::);`t`id!(0;`a)]);
        1101b;"a tie on time goes to the next field; equal is not after; a first run is after nothing"]};

/ --- a recent-data window (#681) ------------------------------------------

/ Rows around NOW: 20s, 10s, 5s and 1s ago, and one a minute in the future -
/ which a window ending now must not include.
.qt.qetl.job.stream.poll.fresh:([] ts:`timestamp$(); v:`float$())
.qt.qetl.job.stream.poll.fresh_rows:{[] `.qt.qetl.job.stream.poll.fresh set ([] ts:.z.p+1000000000*-20 -10 -5 -1 60; v:1 2 3 4 5f);}
.qt.qetl.job.stream.poll.after_fresh:{[cursor] select from .qt.qetl.job.stream.poll.fresh where ts>cursor}

.qt.qetl.job.stream.poll.recent_feed:{[extra]
    .qt.qetl.job.stream.poll.fresh_rows[];
    .qetl.job.stream.define[`spt_quote;`procname`subscribe_to`publishes`period`poll!(
        `spt_quote1;`symbol$();enlist `spt_out;0D00:00:01;
        (`fetch`normalize`next_cursor!(.qt.qetl.job.stream.poll.after_fresh;{[page] select ts, v from page};{[page] last page`ts})),extra)];
    (` sv .qetl.job.stream.namespace[`spt_quote],`publish) set .qt.qetl.job.stream.poll.record;
    `spt_quote}

.qt.qetl.job.stream.poll.test_a_recent_preview_keeps_the_rows_in_the_window:{[t]
    .qt.qetl.job.stream.poll.recent_feed enlist[`page_limit]!enlist 1000;
    r:.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07];
    .qunit.assertEquals[r`mode`state`fetched`kept;(`recent;`previewed;3;2);
        "5s and 1s ago are in [now-7s, now); the future row is fetched and dropped"];
    .qunit.assertEquals[r[`sample;`spt_out;`v];3 4f;"the two recent rows"]};

/ A window given outright (#1735): the same rows, judged the same way.
.qt.qetl.job.stream.poll.test_a_window_preview_keeps_the_rows_in_the_window:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    now:.z.p;
    r:.qetl.job.stream.preview_window[`spt_quote;5;now-0D00:00:12;now-0D00:00:03];
    .qunit.assertEquals[(r`mode;r`state;r`kept;r[`sample;`spt_out;`v];r[`window;`from`to]);
        (`window;`previewed;2;2 3f;(now-0D00:00:12;now-0D00:00:03));
        "10s and 5s ago are in [now-12s, now-3s); the window is the one given"]};

.qt.qetl.job.stream.poll.test_a_window_preview_refuses_an_empty_window:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    .qunit.assertThrows[{.qetl.job.stream.preview_window[`spt_quote;5;.z.p;.z.p-1]};::;
        "preview_window: the window is empty*";"the end before the start"]};

.qt.qetl.job.stream.poll.test_a_window_preview_takes_only_timestamps:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    .qunit.assertThrows[{.qetl.job.stream.preview_window[`spt_quote;5;2026.09.13;2026.09.14]};::;
        "preview_window: the bounds must be timestamps*";"dates are not timestamps"]};

.qt.qetl.job.stream.poll.test_a_recent_window_is_from_one_instant:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    w:.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07]`window;
    .qunit.assertEquals[(w[`to]-w`from;w`start_cursor;w`end_cursor);(0D00:00:07;w[`from]-1;w[`to]-1);
        "from is exactly the span before to, and a timestamp cursor starts 1ns before each bound"]};

.qt.qetl.job.stream.poll.test_a_recent_preview_leaves_the_saved_cursor_alone:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    ahead:.z.p+1D;
    .qetl.job.continuous.save_cursor[`spt_quote;ahead];
    r:.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07];
    .qunit.assertEquals[(r`kept;.qetl.job.continuous.load_cursor `spt_quote;count .qt.qetl.job.stream.poll.sent);(2;ahead;0);
        "the window is fetched despite a saved cursor a day ahead, which stays where it was, and nothing is published"]};

.qt.qetl.job.stream.poll.test_a_recent_preview_writes_no_cursor:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    .qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07];
    .qunit.assertEquals[.qt.qetl.job.stream.poll.cursor_file_exists[`spt_quote];0b;"no cursor file appears"]};

.qt.qetl.job.stream.poll.test_live_polling_is_unchanged_after_a_recent_preview:{[t]
    .qetl.job.stream.preview_recent[`spt_feed;5;0D00:00:07];
    r:.qetl.job.stream.tick `spt_feed;
    .qunit.assertEquals[(r`rows;.qetl.job.continuous.load_cursor `spt_feed);(3;.qt.qetl.job.stream.poll.d[3]);
        "the next tick still starts from no cursor and takes every row"]};

.qt.qetl.job.stream.poll.test_without_last_a_preview_is_the_next_page:{[t]
    .qetl.job.continuous.save_cursor[`spt_feed;.qt.qetl.job.stream.poll.d[1]];
    r:.qetl.job.stream.preview[`spt_feed;5];
    .qunit.assertEquals[(r`mode;r`cursor;r`fetched;`window in key r);(`next_page;.qt.qetl.job.stream.poll.d[1];2;0b);
        "the saved cursor, as before, and no window"]};

.qt.qetl.job.stream.poll.test_a_full_page_says_the_window_may_hold_more:{[t]
    .qt.qetl.job.stream.poll.recent_feed enlist[`page_limit]!enlist 3;
    r:.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07];
    .qunit.assertEquals[r`page_limit`limited;(3;1b);"three fetched against a limit of three"]};

.qt.qetl.job.stream.poll.test_an_undeclared_page_limit_is_null:{[t]
    .qt.qetl.job.stream.poll.recent_feed ()!();
    r:.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07];
    / limited means "the declared page was full"; with no limit declared it is never claimed
    .qunit.assertEquals[(null r`page_limit;r`limited);10b;"not declared, so not claimed"]};

.qt.qetl.job.stream.poll.test_a_recent_preview_traces_its_bounds_and_limit:{[t]
    .qt.qetl.job.stream.poll.fresh_rows[];
    .qetl.job.stream.define[`spt_quote;`procname`subscribe_to`publishes`period`poll!(
        `spt_quote1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor`page_limit!(
            {[cursor] .qetl.source.ipc_call[{value x};.qt.qetl.job.stream.poll.after_fresh;enlist cursor]};
            {[page] select ts, v from page};{[page] last page`ts};1000))];
    lines:.qt.support.captured_log[1b] {.qetl.job.stream.preview_recent[`spt_quote;5;0D00:00:07]};
    sent:first lines[;3] where lines[;2]~\:"query sent";
    .qunit.assertEquals[(count sent`window;sent`page_limit);(2;1000);"the query names [from, to) and the page limit"]};

.qt.qetl.job.stream.poll.test_a_span_that_is_not_positive_is_refused:{[t]
    .qunit.assertThrows[.qetl.job.stream.preview_recent[`spt_feed;5;];0D;"preview_recent: span must be positive*";"zero"];
    .qunit.assertThrows[.qetl.job.stream.preview_recent[`spt_feed;5;];neg 0D00:00:01;"preview_recent: span must be positive*";"negative"];
    .qunit.assertThrows[.qetl.job.stream.preview_recent[`spt_feed;5;];30;"preview_recent: span must be a timespan*";"a bare number"]};

.qt.qetl.job.stream.poll.test_a_compound_feed_without_start_cursor_is_refused:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    .qunit.assertThrows[.qetl.job.stream.preview_recent[`spt_compound;5;];0D00:00:30;
        "preview: spt_compound keeps its own cursor (load, save, advances) and declares no start_cursor*";
        "only the feed knows its tie-breakers"]};

/ The window's edges for a compound cursor are the feed's own: rows sharing
/ the start's time are in, rows sharing the end's time are out.
.qt.qetl.job.stream.poll.test_a_compound_window_keeps_its_tie_breakers:{[t]
    .qt.qetl.job.stream.poll.compound .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
    start:{[instant] .qt.qetl.job.stream.poll.fields!(instant;`;`)};
    w:`from`to`start_cursor`end_cursor!(.qt.qetl.job.stream.poll.d[1];.qt.qetl.job.stream.poll.d[2];start .qt.qetl.job.stream.poll.d 1;start .qt.qetl.job.stream.poll.d 2);
    kept:.qt.qetl.job.stream.poll.book where .qetl.job.stream.in_window[`spt_compound;w;.qt.qetl.job.stream.poll.book];
    .qunit.assertEquals[kept`securityId;`a`b`c;"all three rows at the start's time, none at the end's"]};

.qt.qetl.job.stream.poll.test_a_compound_feed_with_start_cursor_previews_recent_data:{[t]
    .qetl.job.stream.define[`spt_compound;`procname`subscribe_to`publishes`period`poll!(
        `spt_compound1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor`load`save`advances`start_cursor!(
            .qt.qetl.job.stream.poll.page_of_two;
            {[page] select time, securityId, v from page};
            {[page] .qt.qetl.job.stream.poll.fields#last page};
            .qetl.job.continuous.load_cursor_value;
            .qetl.job.continuous.save_cursor_value;
            .qetl.job.continuous.lexically_after .qt.qetl.job.stream.poll.fields;
            {[instant] .qt.qetl.job.stream.poll.fields!(instant;`;`)}))];
    r:.qetl.job.stream.preview_recent[`spt_compound;5;0D00:00:30];
    .qunit.assertEquals[(r`mode;r`state;r[`window;`start_cursor;`securityId]);(`recent;`idle;`);
        "the feed's own start cursor; the fixture book is long past, so the window is empty"]};

.qt.qetl.job.stream.poll.test_define_refuses_a_bad_page_limit:{[t]
    decl:.qt.qetl.job.stream.poll.poll_decl[()!()];
    decl[`poll;`page_limit]:0;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};decl;"*page_limit must be a positive long*";"zero is no limit"]};

.qt.qetl.job.stream.poll.test_define_refuses_a_start_cursor_that_is_not_a_function:{[t]
    decl:.qt.qetl.job.stream.poll.poll_decl[()!()];
    decl[`poll;`start_cursor]:`now;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};decl;"*start_cursor must be functions";"a step, like the others"]};
