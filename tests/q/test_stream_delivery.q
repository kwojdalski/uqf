// test_stream_delivery.q - what a polling feed's tick does about a page that
// may or may not have reached the plant, and the acknowledgement it can ask
// for (src/etl/core/stream_delivery.q, #1651) (.qt.qetl.job.stream.delivery).
//
// The publish is a recorder, the reader and the logged count are functions:
// what is asserted is what the tick sends, what it saves and what it leaves
// behind on disk - the in-flight marker above all.


.qt.qetl.job.stream.delivery.d:{[n] 2026.09.10D00:00:00.000000000+n*1D}

/ The upstream the test feed polls: three rows, one a day, keyed by id.
.qt.qetl.job.stream.delivery.upstream:([] ts:.qt.qetl.job.stream.delivery.d[1 2 3]; id:`a`b`c; v:1.5 2.5 3.5)

/ What the feed published, row by row.
.qt.qetl.job.stream.delivery.sent:([] tbl:`symbol$(); id:`symbol$())

.qt.qetl.job.stream.delivery.record:{[t;rows] `.qt.qetl.job.stream.delivery.sent insert (count[rows]#t;rows`id); count rows}

.qt.qetl.job.stream.delivery.after:{[cursor] select from .qt.qetl.job.stream.delivery.upstream where ts>cursor}

/ A polling feed over `upstream`, with `extra` added to its poll.
.qt.qetl.job.stream.delivery.declare:{[extra]
    .qetl.job.stream.define[`sdt_feed;`procname`subscribe_to`publishes`period`poll!(
        `sdt_feed1;`symbol$();enlist `sdt_out;0D00:00:01;
        (`fetch`normalize`next_cursor!(.qt.qetl.job.stream.delivery.after;{[page] select ts, id, v from page};{[page] last page`ts})),extra)];
    (` sv .qetl.job.stream.namespace[`sdt_feed],`publish) set .qt.qetl.job.stream.delivery.record;
    `sdt_feed}

/ A publish that throws, as one would whose tickerplant went away mid-page.
.qt.qetl.job.stream.delivery.break_publish:{[] (` sv .qetl.job.stream.namespace[`sdt_feed],`publish) set {[t;rows] '"plant gone"};}
.qt.qetl.job.stream.delivery.mend_publish:{[] (` sv .qetl.job.stream.namespace[`sdt_feed],`publish) set .qt.qetl.job.stream.delivery.record;}

.qt.qetl.job.stream.delivery.forget:{[]
    `.qetl.job.stream.jobs set (enlist `sdt_feed) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.procnames set (enlist `sdt_feed1) _ .qetl.job.stream.procnames;
    .qetl.job.stream.delivery.unwire `sdt_feed;
    .qetl.job.stream.delivery.unmark `sdt_feed;
    delete from `.qetl.stream_health.doubts where job=`sdt_feed;
    .qetl.job.continuous.clear_cursor `sdt_feed;}

.qt.qetl.job.stream.delivery.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.job.stream.delivery.setUp_fresh:{[]
    .qt.qetl.job.stream.delivery.forget[];
    `.qt.qetl.job.stream.delivery.sent set 0#.qt.qetl.job.stream.delivery.sent;
    }

.qt.qetl.job.stream.delivery.tearDown_forget:{[] .qt.qetl.job.stream.delivery.forget[];}

/ A page left in doubt: the first tick's publish throws after the marker is
/ written, so the cursor never moves.
.qt.qetl.job.stream.delivery.leave_in_doubt:{[]
    .qt.qetl.job.stream.delivery.break_publish[];
    @[.qetl.job.stream.tick;`sdt_feed;::];
    .qt.qetl.job.stream.delivery.mend_publish[];}

.qt.qetl.job.stream.delivery.warned:{[lines] lines[;2] where `WARNING=lines[;0]}

/ --- the marker ------------------------------------------------------------

.qt.qetl.job.stream.delivery.test_a_published_page_leaves_no_marker:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qetl.job.stream.tick `sdt_feed;
    .qunit.assertEquals[(.qetl.job.stream.delivery.marker`sdt_feed;.qetl.job.continuous.load_cursor`sdt_feed);
        ((::);.qt.qetl.job.stream.delivery.d[3]);"the cursor is saved and the marker removed"]};

.qt.qetl.job.stream.delivery.test_a_failed_publish_leaves_the_page_marked_in_doubt:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qt.qetl.job.stream.delivery.break_publish[];
    .qunit.assertThrows[{.qetl.job.stream.tick `sdt_feed};::;"plant gone";"the publish's own error"];
    m:.qetl.job.stream.delivery.marker`sdt_feed;
    .qunit.assertEquals[(null m`from;m`to;m`rows);(1b;.qt.qetl.job.stream.delivery.d[3];3);
        "from the cursor it had (none), to the one it proposed, with the page's rows"];
    .qunit.assertTrue[null .qetl.job.continuous.load_cursor`sdt_feed;"and the cursor did not move"]};

/ The save happened and the removal did not: nothing is in doubt.
.qt.qetl.job.stream.delivery.test_a_marker_whose_page_was_saved_is_cleared_not_doubted:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qetl.job.continuous.save_cursor[`sdt_feed;.qt.qetl.job.stream.delivery.d[1]];
    .qetl.job.stream.delivery.mark[`sdt_feed;0Np;.qt.qetl.job.stream.delivery.d[1];1];
    lines:.qt.support.captured_log[0b] {.qetl.job.stream.tick `sdt_feed};
    .qunit.assertEquals[(exec id from .qt.qetl.job.stream.delivery.sent;(.qetl.stream_health.doubts_of`sdt_feed)`duplicated);(`b`c;0);
        "the page after the saved cursor is published once, and no doubt counted"];
    .qunit.assertFalse[any .qt.qetl.job.stream.delivery.warned[lines] like "in-doubt*";"nor logged"];
    .qunit.assertEquals[.qetl.job.stream.delivery.marker`sdt_feed;(::);"the stale marker is gone"]};

/ --- an in-doubt page without a row key ----------------------------------

.qt.qetl.job.stream.delivery.test_without_a_row_key_the_page_is_sent_again_and_flagged:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    lines:.qt.support.captured_log[0b] {.qetl.job.stream.tick `sdt_feed};
    .qunit.assertEquals[exec id from .qt.qetl.job.stream.delivery.sent;`a`b`c;"the whole page, as before"];
    w:first lines where lines[;2]~\:"in-doubt page published again - possibly duplicated";
    .qunit.assertEquals[(w[3]`to;w[3]`rows;w[3]`reason);(.qt.qetl.job.stream.delivery.d[3];3;"the job declares no poll row_key");
        "logged with the cursor range it covers, and why it could not be resolved"];
    .qunit.assertEquals[(.qetl.stream_health.doubts_of`sdt_feed)`duplicated`duplicated_rows`resolved;1 3 0;
        "counted in the job's health"];
    .qunit.assertEquals[(.qetl.job.continuous.load_cursor`sdt_feed;.qetl.job.stream.delivery.marker`sdt_feed);
        (.qt.qetl.job.stream.delivery.d[3];::);"then saved, and no longer in doubt"]};

.qt.qetl.job.stream.delivery.test_a_row_key_with_no_reader_wired_is_flagged_not_resolved:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`row_key]!enlist `id;
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    lines:.qt.support.captured_log[0b] {.qetl.job.stream.tick `sdt_feed};
    w:first lines where lines[;2]~\:"in-doubt page published again - possibly duplicated";
    .qunit.assertEquals[(count .qt.qetl.job.stream.delivery.sent;w[3]`reason);(3;"no reader of the published tables is wired");
        "a key alone cannot say what landed - the reader can"]};

.qt.qetl.job.stream.delivery.test_the_health_file_reports_possibly_duplicated_pages:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    .qetl.job.stream.tick `sdt_feed;
    got:.j.k first read0 hsym `$.qetl.stream_health.write[`sdt_feed;0b];
    .qunit.assertEquals[got`possibly_duplicated_pages`possibly_duplicated_rows;1 3f;"what uqs reads, without q"]};

/ --- an in-doubt page resolved by its row key -----------------------------

/ A reader holding `held`: which of the asked keys it has.
.qt.qetl.job.stream.delivery.reader:{[held] {[held;t;k] (k`id) in held}[held]}

.qt.qetl.job.stream.delivery.test_a_row_key_and_a_reader_publish_only_the_absent_rows:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`row_key]!enlist `id;
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`present]!enlist .qt.qetl.job.stream.delivery.reader`a`b];
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    lines:.qt.support.captured_log[0b] {.qetl.job.stream.tick `sdt_feed};
    .qunit.assertEquals[exec id from .qt.qetl.job.stream.delivery.sent;enlist `c;"a and b reached the plant before; only c is sent"];
    w:first lines where lines[;2]~\:"in-doubt page resolved by row key";
    .qunit.assertEquals[w[3]`fetched`already_published`publishing;3 2 1;"logged with what it left out"];
    .qunit.assertEquals[(.qetl.stream_health.doubts_of`sdt_feed)`duplicated`resolved`resolved_rows;0 1 2;"counted as resolved, not duplicated"];
    .qunit.assertEquals[.qetl.job.continuous.load_cursor`sdt_feed;.qt.qetl.job.stream.delivery.d[3];"and the cursor moves past the page"]};

.qt.qetl.job.stream.delivery.test_the_reader_is_asked_only_about_a_page_in_doubt:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`row_key]!enlist `id;
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`present]!enlist {[t;k] '"the reader must not be asked"}];
    .qetl.job.stream.tick `sdt_feed;
    .qunit.assertEquals[count .qt.qetl.job.stream.delivery.sent;3;"a page with no marker before it is published without a lookup"]};

.qt.qetl.job.stream.delivery.test_a_reader_that_answers_the_wrong_shape_publishes_nothing:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`row_key]!enlist `id;
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`present]!enlist {[t;k] 1b}];
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    .qunit.assertThrows[{.qetl.job.stream.tick `sdt_feed};::;"tick: the reader of sdt_out must answer one boolean per key row*";
        "a reader that cannot say row by row cannot resolve anything"];
    .qunit.assertEquals[(count .qt.qetl.job.stream.delivery.sent;null .qetl.job.continuous.load_cursor`sdt_feed;
        (.qetl.job.stream.delivery.marker`sdt_feed)`to);(0;1b;.qt.qetl.job.stream.delivery.d[3]);
        "nothing sent, the cursor where it was, the page still in doubt"]};

.qt.qetl.job.stream.delivery.test_a_reader_that_throws_publishes_nothing:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`row_key]!enlist `id;
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`present]!enlist {[t;k] '"no rdb"}];
    .qt.qetl.job.stream.delivery.leave_in_doubt[];
    .qunit.assertThrows[{.qetl.job.stream.tick `sdt_feed};::;"no rdb";"the reader's own error"];
    .qunit.assertEquals[count .qt.qetl.job.stream.delivery.sent;0;"and no row is sent on a guess"]};

/ --- the acknowledgement ---------------------------------------------------

/ A plant whose logged count moves by `step` rows per publish.
.qt.qetl.job.stream.delivery.count:0
.qt.qetl.job.stream.delivery.counting:{[step]
    `.qt.qetl.job.stream.delivery.count set 0;
    (` sv .qetl.job.stream.namespace[`sdt_feed],`publish) set {[step;t;rows]
        .qt.qetl.job.stream.delivery.record[t;rows];
        `.qt.qetl.job.stream.delivery.count set .qt.qetl.job.stream.delivery.count+step*count rows;
        count rows}[step];
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`logged]!enlist {[t] .qt.qetl.job.stream.delivery.count}];}

.qt.qetl.job.stream.delivery.test_an_acknowledged_publish_moves_the_cursor:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`ack]!enlist 1b;
    .qt.qetl.job.stream.delivery.counting[1];
    r:.qetl.job.stream.tick `sdt_feed;
    .qunit.assertEquals[(r`state;.qetl.job.continuous.load_cursor`sdt_feed);(`published;.qt.qetl.job.stream.delivery.d[3]);
        "the plant counted every row, so the page is acknowledged"]};

.qt.qetl.job.stream.delivery.test_a_publish_the_plant_did_not_count_does_not_move_the_cursor:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`ack]!enlist 1b;
    .qt.qetl.job.stream.delivery.counting[0];
    .qunit.assertThrows[{.qetl.job.stream.tick `sdt_feed};::;
        "tick: sdt_feed's publish of 3 row(s) onto sdt_out was not acknowledged - the tickerplant's logged count went from 0 to 0*";
        "the error says what was sent and what the plant counted"];
    .qunit.assertEquals[(null .qetl.job.continuous.load_cursor`sdt_feed;(.qetl.job.stream.delivery.marker`sdt_feed)`rows);(1b;3);
        "treated as failed: the cursor stays and the page is in doubt"]};

.qt.qetl.job.stream.delivery.test_ack_without_a_logged_count_publishes_nothing:{[t]
    .qt.qetl.job.stream.delivery.declare enlist[`ack]!enlist 1b;
    .qunit.assertThrows[{.qetl.job.stream.tick `sdt_feed};::;"*declares poll ack, but its runner wires no logged count*";
        "an acknowledgement asked for and impossible to give is refused"];
    .qunit.assertEquals[count .qt.qetl.job.stream.delivery.sent;0;"before anything is sent"]};

.qt.qetl.job.stream.delivery.test_without_ack_the_logged_count_is_never_asked:{[t]
    .qt.qetl.job.stream.delivery.declare ()!();
    .qetl.job.stream.delivery.wire[`sdt_feed;enlist[`logged]!enlist {[t] '"the plant must not be asked"}];
    r:.qetl.job.stream.tick `sdt_feed;
    .qunit.assertEquals[r`state;`published;"behaviour unchanged when ack is not declared"]};

/ --- what define and wire refuse -------------------------------------------

.qt.qetl.job.stream.delivery.test_define_refuses_a_row_key_with_time:{[t]
    .qunit.assertThrows[.qt.qetl.job.stream.delivery.declare;enlist[`row_key]!enlist `ts`time;
        "define: sdt_feed's poll row_key includes time*";"the plant stamps its own time"]};

.qt.qetl.job.stream.delivery.test_define_refuses_a_row_key_that_is_not_symbols:{[t]
    .qunit.assertThrows[.qt.qetl.job.stream.delivery.declare;enlist[`row_key]!enlist "id";
        "define: sdt_feed's poll row_key must be a symbol or symbols*";"a string is not a column name"]};

.qt.qetl.job.stream.delivery.test_define_refuses_a_row_key_the_plant_table_lacks:{[t]
    .qunit.assertThrows[{.qetl.job.stream.define[`sdt_feed;`procname`subscribe_to`publishes`period`poll!(
            `sdt_feed1;`symbol$();enlist `mkt_orderbook;0D00:00:01;
            `fetch`normalize`next_cursor`row_key!(.qt.qetl.job.stream.delivery.after;{[p] p};{[p] last p`ts};`nosuch))]};::;
        "define: sdt_feed's poll row_key nosuch is not in the columns of mkt_orderbook";
        "a key the published table cannot hold can never match"]};

.qt.qetl.job.stream.delivery.test_define_refuses_an_ack_that_is_not_a_boolean:{[t]
    .qunit.assertThrows[.qt.qetl.job.stream.delivery.declare;enlist[`ack]!enlist 1;
        "define: sdt_feed's poll ack must be a boolean*";"1b, not 1"]};

.qt.qetl.job.stream.delivery.test_wire_refuses_a_hook_it_does_not_know:{[t]
    .qunit.assertThrows[{.qetl.job.stream.delivery.wire[`sdt_feed;enlist[`confirm]!enlist {x}]};::;
        "delivery.wire: sdt_feed was given confirm, which is not a delivery hook";"named"]};
