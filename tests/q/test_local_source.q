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

test_cleanup_does_not_try_to_close_a_directory:{[t]
    setenv[`UQF_SOURCE_CRED_LOCTEST_SRC;.loctest.dir[]];
    .qpipe.job.loctest_backfill.init[.loctest.spec[]];
    h:.qetl.job.bounded.read_state[`loctest_backfill;`handle];
    .qpipe.job.loctest_backfill.cleanup[];
    .qunit.assertEquals[(type h;.qetl.job.bounded.read_state[`loctest_backfill;`handle]);(-11h;0Ni);
        "the handle was the directory, and cleanup forgets it without an hclose"]};

/ --- tracing -----------------------------------------------------------------

/ Every line `f` logs, context merged; TRC on for the call.
logged:{[f]
    keep:.qetl.log.line; was:.qetl.log.trace_enabled; .qetl.log.trace 1b; `.loctest.lines set ();
    .qetl.log.line:{[level;id;text;fields] .loctest.lines,:enlist (level;id;text;.qetl.log.with_scope fields)};
    @[f;::;::]; .qetl.log.line:keep; .qetl.log.trace was;
    .loctest.lines}

test_a_local_query_is_traced_like_a_remote_one:{[t]
    lines:.loctest.logged {.qetl.source.local[.loctest.root;{[read;a;b] read[`trades;a;b]};2026.09.17D00:00;2026.09.18D00:00]};
    .qunit.assertEquals[lines[;1 2];((`local;"query sent");(`local;"query returned"));"sent, then returned"];
    .qunit.assertEquals[(lines[0;3]`transport;(lines[0;3]`request)~lines[1;3]`request;lines[1;3]`rows);(`local;1b;2);
        "transport local, one request number, the rows read"]};

test_a_failing_local_query_is_traced_and_rethrown:{[t]
    lines:.loctest.logged {@[.qetl.source.local[.loctest.root;{[read;a;b] '"bad query"};2026.09.17D00:00];
        2026.09.18D00:00;{`.loctest.err set x}]};
    .qunit.assertEquals[(lines[;2];.loctest.err);(("query sent";"query failed");"bad query");
        "the failure is traced under its request, and the caller still gets the error"]};

\d .
