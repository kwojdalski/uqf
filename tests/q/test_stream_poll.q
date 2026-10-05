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

feeds:`spt_feed`spt_quote`spt_broken`spt_compound
procs:`spt_feed1`spt_quote1`spt_broken1`spt_compound1

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
    .qunit.assertEquals[(.streampolltest.cursor_file_exists `spt_feed;null .qetl.job.continuous.load_cursor `spt_feed);01b;
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

/ The bug a polling sidecar hit: a populated ladder was compared with the
/ empty schema's " ", and every valid book page was reported invalid.
ladders:{[n;bids] ([] sym:n#`EURUSD; bid_prices:bids; ask_prices:n#enlist 1.11 1.12)}

test_populated_ladders_preview_as_valid:{[t]
    .streampolltest.declare[`spt_quote;`mkt_orderbook;{[page] .streampolltest.ladders[count page;(count page)#enlist 1.1 1.09]};.streampolltest.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[(r`state;r`failures);(`previewed;());"float vectors are what mkt_orderbook declares its ladders hold"]};

test_atoms_where_ladders_belong_are_invalid_in_preview:{[t]
    .streampolltest.declare[`spt_quote;`mkt_orderbook;{[page] .streampolltest.ladders[count page;(count page)#1.1]};.streampolltest.after];
    r:.qetl.job.stream.preview[`spt_quote;5];
    .qunit.assertEquals[r`state;`invalid;"one price per row is not a ladder"];
    .qunit.assertTrue[(first r`failures) like "mkt_orderbook.bid_prices row 0 holds*";"named by table, column and row"]};

/ The timer holds a page to the same contract, and refuses it whole: nothing
/ published, and the cursor where it was, so the page is fetched again.
test_the_timer_refuses_a_page_the_plant_would_not_take:{[t]
    .streampolltest.declare[`spt_quote;`mkt_orderbook;{[page] .streampolltest.ladders[count page;(count page)#1.1]};.streampolltest.after];
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_quote};::;"tick: spt_quote would publish what the plant does not take - mkt_orderbook.bid_prices*";
        "the same failure the preview reports"];
    .qunit.assertEquals[(count .streampolltest.sent;null .qetl.job.continuous.load_cursor `spt_quote);(0;1b);
        "nothing published and no cursor saved"]};

test_the_timer_publishes_a_page_that_fits:{[t]
    .streampolltest.declare[`spt_quote;`mkt_orderbook;{[page] .streampolltest.ladders[count page;(count page)#enlist 1.1 1.09]};.streampolltest.after];
    r:.qetl.job.stream.tick `spt_quote;
    .qunit.assertEquals[(r`state;count .streampolltest.sent);(`published;1);"a valid page goes out"]};

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

/ --- a compound cursor (#666) ----------------------------------------------

/ Three rows share a timestamp: a page of two cannot be acknowledged by a
/ time alone, so the cursor is the row's position.
book:([] time:d 1 1 1 2; securityId:`a`b`c`a; priceBookType:`x`x`x`x; v:1 2 3 4f)
fields:`time`securityId`priceBookType

/ The page of two after `cursor`, in the source's sort order.
page_of_two:{[cursor]
    after:$[(::)~cursor; .streampolltest.book;
        .streampolltest.book where .qetl.job.continuous.lexically_after[.streampolltest.fields;cursor] each .streampolltest.book];
    2 sublist after}

compound:{[advances]
    .qetl.job.stream.define[`spt_compound;`procname`subscribe_to`publishes`period`poll!(
        `spt_compound1;`symbol$();enlist `spt_out;0D00:00:01;
        `fetch`normalize`next_cursor`load`save`advances!(
            .streampolltest.page_of_two;
            {[page] select time, securityId, v from page};
            {[page] .streampolltest.fields#last page};
            .qetl.job.continuous.load_cursor_value;
            .qetl.job.continuous.save_cursor_value;
            advances))];
    (` sv .qetl.job.stream.namespace[`spt_compound],`publish) set .streampolltest.record;
    `spt_compound}

test_a_compound_cursor_pages_through_rows_sharing_a_timestamp:{[t]
    .streampolltest.compound .qetl.job.continuous.lexically_after .streampolltest.fields;
    .qetl.job.stream.tick `spt_compound;
    mid:.qetl.job.continuous.load_cursor_value `spt_compound;
    .qetl.job.stream.tick `spt_compound;
    .qunit.assertEquals[(mid;exec sum n from .streampolltest.sent);
        (.streampolltest.fields!(d 1;`b;`x);4);
        "the first page stops at b inside one timestamp, the second takes c and the next day - every row once"]};

test_a_compound_cursor_keeps_its_tie_breakers:{[t]
    .streampolltest.compound .qetl.job.continuous.lexically_after .streampolltest.fields;
    .qetl.job.stream.tick `spt_compound;
    .qunit.assertEquals[key .qetl.job.continuous.load_cursor_value `spt_compound;.streampolltest.fields;
        "securityId and priceBookType survive the file, not just the time"]};

test_a_compound_preview_leaves_the_cursor_file_alone:{[t]
    .streampolltest.compound .qetl.job.continuous.lexically_after .streampolltest.fields;
    .qetl.job.stream.tick `spt_compound;
    before:.qetl.job.continuous.load_cursor_value `spt_compound;
    r:.qetl.job.stream.preview[`spt_compound;5];
    .qunit.assertEquals[(r`state;r`fetched;r`next_cursor;.qetl.job.continuous.load_cursor_value `spt_compound);
        (`previewed;2;.streampolltest.fields!(d 2;`a;`x);before);
        "it proposes the next position and the saved one is untouched"]};

test_a_failed_publish_saves_no_compound_cursor:{[t]
    .streampolltest.compound .qetl.job.continuous.lexically_after .streampolltest.fields;
    (` sv .qetl.job.stream.namespace[`spt_compound],`publish) set {[t;rows] '"plant refused"};
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_compound};::;"plant refused";"the publish error is the tick's"];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor_value `spt_compound;(::);
        "nothing published, so nothing acknowledged - the page is fetched again"]};

test_a_compound_cursor_that_does_not_advance_is_refused_in_both_modes:{[t]
    .streampolltest.compound {[current;proposed] 0b};
    r:.qetl.job.stream.preview[`spt_compound;5];
    .qunit.assertThrows[{.qetl.job.stream.tick `spt_compound};::;"*does not move past*";"the timer refuses"];
    .qunit.assertEquals[(r`state;count .streampolltest.sent);(`invalid;0);
        "the preview reports it, and the timer refused before publishing anything"]};

test_define_refuses_half_a_custom_cursor:{[t]
    d:.streampolltest.poll_decl ()!();
    d[`poll]:d[`poll],enlist[`load]!enlist .qetl.job.continuous.load_cursor_value;
    .qunit.assertThrows[{.qetl.job.stream.define[`spt_broken;x]};d;"*load, save and advances together*";
        "a cursor loaded one way and saved another drifts"]};

test_lexically_after_compares_the_first_differing_field:{[t]
    f:.qetl.job.continuous.lexically_after[`t`id];
    .qunit.assertEquals[(f[`t`id!(1;`a);`t`id!(1;`b)];f[`t`id!(1;`b);`t`id!(2;`a)];f[`t`id!(1;`b);`t`id!(1;`b)];f[(::);`t`id!(0;`a)]);
        1101b;"a tie on time goes to the next field; equal is not after; a first run is after nothing"]};

\d .
