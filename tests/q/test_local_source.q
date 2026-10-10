/ test_local_source.q - the `local transport: a source read from an HDB
/ directory's files (.qetl.source.local, local_read, local_root).
/ .
/ Every HDB here is a temporary one written by the test, under build/, with a
/ sym file that DIFFERS from this process's own `sym` - the case a plain
/ `get` gets silently wrong, and the reason local_read decodes against the
/ HDB's own file. No real HDB is read.

\d .loctest

/ A two-day HDB: `trades on 2026.09.17 and 2026.09.18, its sym file in the
/ opposite order to the symbols' first use, so position 0 is USDJPY.
dir:{[] "build/test-local-hdb"}
build:{[]
    d:.loctest.dir[];
    system"rm -rf ",d; system"mkdir -p ",d;
    (hsym `$d,"/sym") set `USDJPY`EURUSD;
    / enumerated by POSITION against that file, as a writer of that HDB would
    / leave them: 1 is EURUSD, 0 is USDJPY
    {[d;dt;ix;px] (hsym `$d,"/",string[dt],"/trades/") set
        ([] time:dt+0D10:00 0D11:00; sym:`sym!ix; px:px)}[d]'[
        2026.09.17 2026.09.18;(1 0;0 1);(1.1 150.;151. 1.2)];
    hsym `$d}

beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

/ A source and worker over the local HDB, defined once.
beforeNamespace_define:{[]
    .qetl.source.define[`loctest_src;
        `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz`transport!
        (`loctest_src;`trades;`loctest_out;`time;`time`sym;`time`sym`px;"psf";
         {[h;range_from;range_to]
            .qetl.source.local[h;{[read;from_ts;to_ts]
                select time, sym, px from read[`trades;from_ts;to_ts]
                    where time>=from_ts, time<to_ts
              };range_from;range_to]};
         {[] ([] time:enlist 2026.09.17D10:00; sym:enlist `FIXTURE; px:enlist 0f)};
         `UTC;`local)];
    .qetl.transform.define[`loctest_passthrough;`inputs`output`fn`examples!(
        enlist[`batch]!enlist ([] time:`timestamp$(); sym:`symbol$(); px:`float$());
        ([] time:`timestamp$(); sym:`symbol$(); px:`float$());
        {[batch] batch};
        enlist `inputs`expected!(
            enlist[`batch]!enlist ([] time:enlist 2026.09.17D10:00; sym:enlist `EURUSD; px:enlist 1.1);
            ([] time:enlist 2026.09.17D10:00; sym:enlist `EURUSD; px:enlist 1.1)))];
    .qetl.job.bounded.define[`loctest_backfill;
        `source`dataset`width`transform!(`loctest_src;`loctest_out;1D;`loctest_passthrough)];
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    .qetl.job.bounded.state.release_lock `loctest_backfill;
    .qetl.job.bounded.state.clear_checkpoint `loctest_backfill;
    `loctest_out set ([] time:`timestamp$(); sym:`symbol$(); px:`float$());
    .loctest.root:.loctest.build[];
    }

tearDown_release:{[] .qpipe.job.loctest_backfill.cleanup[]; setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;""];}

spec:{[] `source_version`range_from`range_to!(`v1;2026.09.17D00:00;2026.09.19D00:00)}

/ --- reading ----------------------------------------------------------------

test_symbols_are_decoded_against_the_hdbs_own_sym_file:{[t]
    r:.qetl.source.local_read[.loctest.root;`trades;2026.09.17D00:00;2026.09.18D00:00];
    .qunit.assertEquals[exec sym from r;`EURUSD`USDJPY;
        "the HDB's own sym file, whatever this process has loaded as `sym"]};

test_a_read_spans_every_partition_the_window_touches:{[t]
    r:.qetl.source.local_read[.loctest.root;`trades;2026.09.17D12:00;2026.09.18D01:00];
    .qunit.assertEquals[(count r;distinct r`date);(4;2026.09.17 2026.09.18);
        "whole partitions, with their date - the query filters the rows"]};

test_a_window_with_no_partition_is_empty_in_the_tables_shape:{[t]
    r:.qetl.source.local_read[.loctest.root;`trades;2026.10.01D00:00;2026.10.02D00:00];
    .qunit.assertEquals[(count r;cols r);(0;`date`time`sym`px);"empty, but a table a select can run on"]};

test_a_table_no_partition_holds_is_refused_by_name:{[t]
    .qunit.assertThrows[{.qetl.source.local_read[.loctest.root;`nope;x 0;x 1]};
        (2026.09.17D00:00;2026.09.18D00:00);"local: no partition of nope under *";
        "named, not an empty result that hides a typo"]};

test_a_column_enumerated_against_another_domain_uses_that_domain:{[t]
    / `venue, not `sym: decoding by sym's positions returned `b`a`b for `Y`X`Y.
    d:"build/test-local-venue"; system"rm -rf ",d; system"mkdir -p ",d;
    (hsym `$d,"/sym") set `a`b;
    (hsym `$d,"/venue") set `X`Y;
    `venue set `X`Y;
    (hsym `$d,"/2026.09.17/t/") set ([] time:2026.09.17D10:00 2026.09.17D10:01 2026.09.17D10:02; v:`venue$`Y`X`Y);
    delete venue from `.;
    r:.qetl.source.local_read[hsym `$d;`t;2026.09.17D00:00;2026.09.18D00:00];
    .qunit.assertEquals[exec v from r;`Y`X`Y;"each column against its own domain's file"]};

test_a_missing_domain_file_is_refused_not_decoded_to_blanks:{[t]
    d:"build/test-local-nosym"; system"rm -rf ",d; system"mkdir -p ",d;
    / Enumerated against a `sym set only for this write; the process's own
    / `sym - which the HDB writers in other suites rely on - is put back.
    keep:@[get;`sym;{[e] `symbol$()}];
    `sym set `a`b;
    (hsym `$d,"/2026.09.17/t/") set ([] time:2026.09.17D10:00 2026.09.17D10:01; s:`sym$`a`b);
    `sym set keep;
    .qunit.assertThrows[{.qetl.source.local_read[hsym `$x;`t;2026.09.17D00:00;2026.09.18D00:00]};d;
        "local: column s is enumerated against `sym, but * does not exist";
        "no sym file, no blank symbols"]};

test_a_domain_file_too_short_for_the_column_is_refused:{[t]
    d:"build/test-local-short"; system"rm -rf ",d; system"mkdir -p ",d;
    keep:@[get;`sym;{[e] `symbol$()}];
    `sym set `a`b`c;
    (hsym `$d,"/2026.09.17/t/") set ([] time:2026.09.17D10:00 2026.09.17D10:01; s:`sym$`a`c);
    `sym set keep;
    (hsym `$d,"/sym") set enlist `a;
    .qunit.assertThrows[{.qetl.source.local_read[hsym `$x;`t;2026.09.17D00:00;2026.09.18D00:00]};d;
        "local: column s needs 3 entries of `sym*";"positions past the file's end are refused"]};

test_partitions_with_different_columns_are_refused_by_date:{[t]
    d:"build/test-local-drift"; system"rm -rf ",d; system"mkdir -p ",d;
    (hsym `$d,"/sym") set `a;
    (hsym `$d,"/2026.09.17/t/") set ([] time:enlist 2026.09.17D10:00; px:enlist 1f);
    (hsym `$d,"/2026.09.18/t/") set ([] time:enlist 2026.09.18D10:00; px:enlist 1f; qty:enlist 2);
    .qunit.assertThrows[{.qetl.source.local_read[hsym `$x;`t;2026.09.17D00:00;2026.09.19D00:00]};d;
        "local: t's columns differ between partitions 2026.09.18*";
        "named, not a list of dicts that fails in the query"]};

test_a_nested_symbol_list_column_is_decoded_row_by_row:{[t]
    / A list of symbols per row is stored as 77h - outside 20-76h, so it came
    / back as raw enum positions.
    d:"build/test-local-nested"; system"rm -rf ",d; system"mkdir -p ",d;
    keep:@[get;`sym;{[e] `symbol$()}];
    `sym set `x`y`z;
    (hsym `$d,"/sym") set `x`y`z;
    (hsym `$d,"/2026.09.17/t/") set ([] time:2026.09.17D10:00 2026.09.17D10:01; tags:(`sym$`x`y;`sym$enlist `z));
    `sym set keep;
    r:.qetl.source.local_read[hsym `$d;`t;2026.09.17D00:00;2026.09.18D00:00];
    .qunit.assertEquals[r`tags;(`x`y;enlist `z);"each row's symbols, decoded"]};

test_a_domain_file_that_is_not_symbols_is_refused:{[t]
    d:"build/test-local-baddomain"; system"rm -rf ",d; system"mkdir -p ",d;
    keep:@[get;`sym;{[e] `symbol$()}];
    `sym set `a`b;
    (hsym `$d,"/2026.09.17/t/") set ([] time:2026.09.17D10:00 2026.09.17D10:01; s:`sym$`a`b);
    `sym set keep;
    (hsym `$d,"/sym") set 1 2 3;
    .qunit.assertThrows[{.qetl.source.local_read[hsym `$x;`t;2026.09.17D00:00;2026.09.18D00:00]};d;
        "local: * is not a symbol list - not a domain file";"longs are not symbols"]};

/ --- the credential is a path, checked when the worker connects -------------

test_a_path_that_does_not_exist_is_refused:{[t]
    .qunit.assertThrows[.qetl.source.local_root;"/no/such/hdb";"local: /no/such/hdb does not exist";
        "a wrong path fails"]};

test_a_file_is_not_an_hdb:{[t]
    .qunit.assertThrows[.qetl.source.local_root;.loctest.dir[],"/sym";"local: *is a file, not an HDB directory";
        "the sym file itself is refused"]};

test_a_directory_with_no_sym_or_partition_is_not_an_hdb:{[t]
    system"mkdir -p build/test-local-empty";
    .qunit.assertThrows[.qetl.source.local_root;"build/test-local-empty";"local: *is it an HDB root?";
        "an empty directory is refused"]};

/ --- a worker over a local source -----------------------------------------

test_a_configured_path_reads_the_hdb:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;.loctest.dir[]];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    r:.qpipe.job.loctest_backfill.run[];
    rows:get `loctest_out;
    .qunit.assertEquals[(r`state;count rows;asc distinct rows`sym);(`completed;4;`EURUSD`USDJPY);
        "all four HDB rows, symbols decoded - not the fixture's one"]};

test_a_wrong_configured_path_fails_and_never_reads_the_fixture:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;"/no/such/hdb"];
    e:@[.qpipe.job.loctest_backfill.init;.loctest.spec[];{x}];
    .qunit.assertTrue[(10h=type e) and e like "*/no/such/hdb does not exist*";
        "init fails, naming the path"];
    .qunit.assertEquals[count get `loctest_out;0;"and nothing - fixture or otherwise - was written"]};

test_an_unset_path_runs_on_the_fixture:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;""];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    .qpipe.job.loctest_backfill.run[];
    .qunit.assertEquals[exec sym from get `loctest_out;enlist `FIXTURE;"the explicit fixture path, as before"]};

/ --- a fixture's windows are not the source's (#1082) -----------------------

/ The bug: a run with no credential recorded its fixture windows as covered
/ under v1, so the live run that followed once a credential was set found
/ the range done, fetched nothing, and left the synthetic row standing.
test_a_live_run_replaces_what_a_fixture_run_wrote:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;""];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    .qpipe.job.loctest_backfill.run[];
    .qpipe.job.loctest_backfill.cleanup[];
    fv:`$"v1~fixture";
    fixture_covered:.qetl.coverage.is_covered[`loctest_out;`;fv;.z.p;2026.09.17D00:00;2026.09.19D00:00];
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;.loctest.dir[]];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    r:.qpipe.job.loctest_backfill.run[];
    rows:get `loctest_out;
    .qunit.assertEquals[(fixture_covered;r`state;r`windows_completed);(1b;`completed;2);
        "the fixture covered its own release, and the live run still fetched both days"];
    .qunit.assertEquals[asc distinct rows`sym;`EURUSD`USDJPY;"the live rows, and the fixture's row is gone"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`loctest_out;`;`v1;.z.p;2026.09.17D00:00;2026.09.19D00:00];1b;
        "v1 is covered by the live run"];
    .qunit.assertEquals[count .qetl.coverage.intervals[`loctest_out;`;fv;.z.p];0;
        "and the fixture's claims on those windows are withdrawn"]};

test_a_fixture_run_must_be_asked_for:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;""];
    setenv[`UQF_FIXTURE_WRITES;""];
    .qunit.assertThrows[.qpipe.job.loctest_backfill.init;.loctest.spec[];
        "init: loctest_src has no credential - refusing to write its fixture. Set UQF_SOURCE_CRED_LOCTEST_SRC, or UQF_FIXTURE_WRITES=1*";
        "a run with no credential is refused, naming both ways out"];
    .qunit.assertEquals[(count get `loctest_out;count .qetl.coverage.ledger[]);0 0;"and nothing was written"]};

test_a_dry_run_may_read_the_fixture_unasked:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;""];
    setenv[`UQF_FIXTURE_WRITES;""];
    setenv[`UQF_DRY_RUN;"true"];
    s:.qpipe.job.loctest_backfill.init[.loctest.spec[]];
    .qunit.assertEquals[s`source_version;`$"v1~fixture";"a rehearsal writes nothing, so it is not refused"]};

test_cleanup_does_not_try_to_close_a_directory:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;.loctest.dir[]];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    h:.qetl.job.bounded.read_state[`loctest_backfill;`handle];
    .qpipe.job.loctest_backfill.cleanup[];
    .qunit.assertEquals[(type h;.qetl.job.bounded.read_state[`loctest_backfill;`handle]);(-11h;0Ni);
        "the handle was the directory, and cleanup forgets it without an hclose"]};

/ --- tracing -----------------------------------------------------------------

/ Every line `f` logs, context merged; TRACE on for the call.
logged:.testutil.captured_log[1b]

test_a_local_query_is_traced_like_a_remote_one:{[t]
    lines:.loctest.logged {.qetl.source.local[.loctest.root;{[read;a;b] read[`trades;a;b]};2026.09.17D00:00;2026.09.18D00:00]};
    .qunit.assertEquals[lines[;1 2];((`local;"query sent");(`local;"query returned"));"sent, then returned"];
    .qunit.assertEquals[(lines[0;3]`transport;(lines[0;3]`request)~lines[1;3]`request;lines[1;3]`rows);(`local;1b;2);
        "transport local, one request number, the rows read"]};

test_a_traced_query_carries_its_whole_lambda_at_any_console_width:{[t]
    / -3! cut the call at the console width before log.q's full-width
    / rendering saw it - 79 characters at the default 80 columns.
    c:system"c"; system"c 25 80";
    f:{[read;from_ts;to_ts] select time, sym, px from read[`trades;from_ts;to_ts] where time>=from_ts, time<to_ts};
    / Through a global: `logged {..}[f]` would run the query before logged
    / installs its recorder.
    `.loctest.f set f;
    lines:.loctest.logged {.qetl.source.local[.loctest.root;.loctest.f;2026.09.17D00:00;2026.09.18D00:00]};
    system"c ",(" " sv string c);
    .qunit.assertEquals[lines[0;3]`call;string f;"the lambda's full text, not 79 characters and .."]};

test_a_failing_local_query_is_traced_and_rethrown:{[t]
    lines:.loctest.logged {@[.qetl.source.local[.loctest.root;{[read;a;b] '"bad query"};2026.09.17D00:00];
        2026.09.18D00:00;{`.loctest.err set x}]};
    .qunit.assertEquals[(lines[;2];.loctest.err);(("query sent";"query failed");"bad query");
        "the failure is traced under its request, and the caller still gets the error"]};


/ --- layouts, columns and metadata (#621) ------------------------------------

/ A segmented HDB: the sym file at the root, par.txt naming two segments
/ relative to it, 2026.09.17 in seg1 and 2026.09.18 in seg2.
segdir:{[] "build/test-local-seg"}
build_segmented:{[]
    d:.loctest.segdir[];
    system"rm -rf ",d; system"mkdir -p ",d,"/seg1 ",d,"/seg2";
    (hsym `$d,"/sym") set `USDJPY`EURUSD;
    (hsym `$d,"/par.txt") 0: ("seg1";"seg2");
    {[d;seg;dt;ix;px] (hsym `$d,"/",seg,"/",string[dt],"/trades/") set
        ([] time:dt+0D10:00 0D11:00; sym:`sym!ix; px:px)}[d]'[
        ("seg1";"seg2");2026.09.17 2026.09.18;(1 0;0 1);(1.1 150.;151. 1.2)];
    hsym `$d}

test_a_segmented_hdb_is_read_across_its_segments:{[t]
    .loctest.build_segmented[];
    root:.qetl.source.local_root .loctest.segdir[];
    r:.qetl.source.local_read[root;`trades;2026.09.17D00:00;2026.09.19D00:00];
    .qunit.assertEquals[(.qetl.source.local_dates[root;-0Wd;0Wd];exec sym from r);
        (2026.09.17 2026.09.18;`EURUSD`USDJPY`USDJPY`EURUSD);
        "both segments' partitions, decoded against the root's own sym file"]};

test_a_missing_segment_is_refused_by_name:{[t]
    .loctest.build_segmented[];
    (hsym `$.loctest.segdir[],"/par.txt") 0: ("seg1";"seg2";"seg3");
    .qunit.assertThrows[.qetl.source.local_root;.loctest.segdir[];"*does not exist or cannot be read*";
        "a segment par.txt names but the disk does not have fails at connect, not as no data"]};

test_a_month_partitioned_hdb_is_refused_by_name:{[t]
    d:"build/test-local-month";
    system"rm -rf ",d; system"mkdir -p ",d;
    (hsym `$d,"/sym") set enlist `EURUSD;
    (hsym `$d,"/2026.09/trades/") set ([] time:enlist 2026.09.17D10:00; px:enlist 1.1);
    .qunit.assertThrows[.qetl.source.local_root;d;"*partitioned by month*";
        "month partitions were skipped in silence; now the real cause is named"]};

test_an_int_partitioned_hdb_is_refused_by_name:{[t]
    d:"build/test-local-int";
    system"rm -rf ",d; system"mkdir -p ",d;
    (hsym `$d,"/sym") set enlist `EURUSD;
    (hsym `$d,"/7/trades/") set ([] time:enlist 2026.09.17D10:00; px:enlist 1.1);
    .qunit.assertThrows[.qetl.source.local_root;d;"*year or int*";"an int partition is refused too"]};

test_a_read_of_named_columns_reads_only_their_files:{[t]
    / px's file is gone from one partition: a whole read fails on it, a read
    / that does not ask for px never opens it.
    hdel hsym `$.loctest.dir[],"/2026.09.17/trades/px";
    r:.qetl.source.local_read[.loctest.root;(`trades;`time`sym);2026.09.17D00:00;2026.09.18D00:00];
    .qunit.assertEquals[(cols r;exec sym from r);(`date`time`sym;`EURUSD`USDJPY);
        "the requested columns, decoded, and the partition's date"]};

test_a_requested_column_the_table_lacks_is_refused:{[t]
    .qunit.assertThrows[{.qetl.source.local_read[.loctest.root;(`trades;`time`qty);x;x+1D]};2026.09.17D00:00;
        "*has no column(s) qty*";"a missing column is named, not read as nulls"]};

test_metadata_decodes_one_row_not_the_partition:{[t]
    / The sym file now holds one entry: enough for the first row of the newest
    / partition (position 0), too few to decode all of it (positions 0 1).
    (hsym `$.loctest.dir[],"/sym") set enlist `USDJPY;
    .qunit.assertThrows[{.qetl.source.local_latest[x;`trades]};.loctest.root;"*needs 2 entries*";
        "a whole-partition read decodes every row"];
    .qunit.assertEquals[.qetl.source.local_meta[.loctest.root;`trades];
        ([] c:`date`time`sym`px; t:"dpsf");
        "the columns and types come from the .d file and one decoded row"]};

\d .
