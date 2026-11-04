/ test_bounded_batch_read.q - a batched window whose batches read the source themselves
/ (.qetl.job.bounded.batching source_read `batch, .qetl.source batch_read, #1921).
/ .
/ Four things are proven. The CONTRACT: a source's batch_read is refused by name when it is
/ malformed, and a worker asks for it only of a source that declares one. The WINDOW READ: the
/ presence summary lets `ready` answer exactly as it answers on the whole window. The READS: each
/ batch asks the source for the references ITS rows need and no others, and a reference one batch
/ read serves the next, within a bound and never across a source_version or a window. And the
/ OUTCOME: batched through the source, a window publishes the rows an unbatched one does,
/ is claimed only after every batch, and a batch whose read fails fails the window with nothing
/ written or covered.
/ .
/ No HDB is needed: the live path is driven through a stand-in handle that evaluates each query
/ against tables held here, recording what it was asked and what it returned.

.qt.qetl.job.bounded.batch_read.worker:`hdb_fx_markouts_backfill

/ The window the stand-in HDB's day is read over: the hour that holds the fixture's executions.
.qt.qetl.job.bounded.batch_read.hour:{[] `range_from`range_to!(2026.09.17D10:00:00.000000000;2026.09.17D11:00:00.000000000)}

/ What the stand-in handle was asked: one row per query, (kind; rows returned; syms of its books).
.qt.qetl.job.bounded.batch_read.sent:()

/ The query number that throws (0 for none), and what it throws.
.qt.qetl.job.bounded.batch_read.fail_at:0
.qt.qetl.job.bounded.batch_read.fail_with:"boom"

/ A handle that runs each query here, noting what it returned.
.qt.qetl.job.bounded.batch_read.hdb:{[msg]
    n:1+count .qt.qetl.job.bounded.batch_read.sent;
    r:value msg;
    k:$[not 98h=type r; `other; `bid_prices in cols r; `books; `executions];
    .qt.qetl.job.bounded.batch_read.sent,:enlist (k;count r;$[k=`books; asc distinct r`sym; `symbol$()]);
    if[n=.qt.qetl.job.bounded.batch_read.fail_at; '.qt.qetl.job.bounded.batch_read.fail_with];
    r}

/ The tables the stand-in serves: the fixture's day, optionally without the books of some pairs.
.qt.qetl.job.bounded.batch_read.serve:{[without]
    `executions set update date:`date$source_time from .qpipe.source.hdb_fx_markouts.raw_executions[];
    `market_data set update date:`date$source_time from select from .qpipe.source.hdb_fx_markouts.raw_market_data[] where not sym in without;
    `.qt.qetl.job.bounded.batch_read.sent set ();
    `.qt.qetl.job.bounded.batch_read.fail_at set 0}

.qt.qetl.job.bounded.batch_read.unserve:{[]
    ![`.;();0b;`executions`market_data];
    }

/ The markout worker's batching declaration as define would store it.
.qt.qetl.job.bounded.batch_read.declare:{[mg;read;reuse]
    b:`group_by`max_rows`max_groups`source_read`reuse_rows!(enlist `sym;1000;mg;read;reuse);
    .qetl.job.bounded.batching.require[.qt.qetl.job.bounded.batch_read.worker;`source`batching!(`hdb_fx_markouts;b)]}

.qt.qetl.job.bounded.batch_read.set_batching:{[v]
    d:.qetl.job.bounded.def .qt.qetl.job.bounded.batch_read.worker;
    d[`batching]:v;
    `.qetl.job.bounded.worker_cfg upsert (.qt.qetl.job.bounded.batch_read.worker,d cols value .qetl.job.bounded.worker_cfg);
    v}

/ Has the ledger claimed the hour that holds the fixture's executions?
.qt.qetl.job.bounded.batch_read.claimed:{[]
    0<count select from .qetl.coverage.ledger[] where dataset=`fx_markout,
        range_from=2026.09.17D10:00:00.000000000, range_to=2026.09.17D11:00:00.000000000}

/ The worker initialised for the hour and pointed at the stand-in HDB, as a live run is.
.qt.qetl.job.bounded.batch_read.start:{[]
    h:.qt.qetl.job.bounded.batch_read.hour[];
    .qpipe.job.hdb_fx_markouts_backfill.init[`source_version`range_from`range_to!(`v1;h`range_from;h`range_to)];
    .qetl.job.bounded.write_state[.qt.qetl.job.bounded.batch_read.worker;`handle;.qt.qetl.job.bounded.batch_read.hdb];
    .qetl.job.bounded.write_state[.qt.qetl.job.bounded.batch_read.worker;`live;1b];
    .qetl.job.bounded.write_state[.qt.qetl.job.bounded.batch_read.worker;`progress;.qetl.job.bounded.no_progress]}

/ One window through every stage, as a run takes it: 1b when it was published.
.qt.qetl.job.bounded.batch_read.window_run:{[]
    .qetl.job.bounded.batching.reset_reads[];
    .qt.qetl.job.bounded.batch_read.start[];
    .qetl.job.bounded.do_window[.qt.qetl.job.bounded.batch_read.worker;.qt.qetl.job.bounded.batch_read.hour[]]}

.qt.qetl.job.bounded.batch_read.published:{[]
    `venue`fill_id`horizon`kind`step xasc get `fx_markout}

.qt.qetl.job.bounded.batch_read.fresh_window:{[]
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qt.support.reset_coverage_ledger[];
    .qetl.job.bounded.state.release_lock .qt.qetl.job.bounded.batch_read.worker;
    .qetl.job.bounded.state.clear_checkpoint .qt.qetl.job.bounded.batch_read.worker;
    .qetl.job.bounded.batching.reset_reads[]}

.qt.qetl.job.bounded.batch_read.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.job.bounded.batch_read.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock .qt.qetl.job.bounded.batch_read.worker;
    .qetl.job.bounded.state.clear_checkpoint .qt.qetl.job.bounded.batch_read.worker;
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qetl.job.bounded.batching.reset_reads[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    }

.qt.qetl.job.bounded.batch_read.tearDown_restore:{[]
    / the stand-in is not a connection: clear it before cleanup would try to close it
    @[{.qetl.job.bounded.write_state[x;`handle;0Ni]; .qetl.job.bounded.write_state[x;`live;0b]};.qt.qetl.job.bounded.batch_read.worker;{[e] (::)}];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    .qt.qetl.job.bounded.batch_read.set_batching[(::)];
    .qt.qetl.job.bounded.batch_read.unserve[];
    .qetl.job.bounded.batching.reset_reads[];
    }

/ --- the source's contract -----------------------------------------------------------------------

/ The markout source's own declaration, and a stand-in for the whole definition the check sees.
.qt.qetl.job.bounded.batch_read.br:{[] (.qetl.source.def `hdb_fx_markouts)`batch_read}
.qt.qetl.job.bounded.batch_read.decl:{[] `supporting`tz!(.qpipe.source.hdb_fx_markouts.supporting;`UTC)}

.qt.qetl.job.bounded.batch_read.check:{[br;decl]
    @[{.qetl.source.check_batch_read[`probe;x 0;x 1]; "accepted"};(br;decl);{[e] e}]}

.qt.qetl.job.bounded.batch_read.test_the_markout_source_declares_a_batch_read_the_check_accepts:{[t]
    .qunit.assertEquals[key .qt.qetl.job.bounded.batch_read.br[];.qetl.source.batch_read_keys;"all five keys"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.check[.qt.qetl.job.bounded.batch_read.br[];.qt.qetl.job.bounded.batch_read.decl[]];"accepted";"as defined"];
    .qunit.assertThrows[.qetl.source.batch_read;`demo_deals;"batch_read: demo_deals declares no batch_read*";"a source without one says so"]};

.qt.qetl.job.bounded.batch_read.test_a_malformed_batch_read_is_refused_by_name:{[t]
    br:.qt.qetl.job.bounded.batch_read.br[];
    d:.qt.qetl.job.bounded.batch_read.decl[];
    c:.qt.qetl.job.bounded.batch_read.check;
    .qunit.assertTrue[c[br;`supporting`tz!(()!();`UTC)] like "*needs supporting inputs*";"nothing to read a batch at a time"];
    .qunit.assertTrue[c[br;@[d;`tz;:;`$"Europe/London"]] like "*is for a UTC source*";"a zoned source"];
    .qunit.assertTrue[c[1b;d] like "*must be a dict of window, needs, read, keys, sort";"not a dictionary"];
    .qunit.assertTrue[c[4#br;d] like "*is missing sort";"a missing key"];
    .qunit.assertTrue[c[br,enlist[`speed]!enlist 1;d] like "*declares speed, which it does not take*";"an unknown key"];
    .qunit.assertTrue[c[@[br;`needs;:;{[a;b] a}];d] like "*malformed needs (a lambda taking the primary rows)*";"wrong arity for needs"];
    .qunit.assertTrue[c[@[br;`read;:;{[h;a;b] a}];d] like "*malformed read (a lambda taking handle*";"wrong arity for read"];
    .qunit.assertTrue[c[@[br;`keys;:;enlist[`other]!enlist `sym];d] like "*malformed keys*";"keys must name the supporting inputs"];
    .qunit.assertTrue[c[@[br;`sort;:;enlist[`market_data]!enlist `nope];d] like "*names a column the supporting input does not hold: sort market_data.nope*";"a sort column the input lacks"];
    .qunit.assertTrue[c[@[br;`keys;:;enlist[`market_data]!enlist `nope];d] like "*keys market_data.nope*";"a key column the input lacks"]};

.qt.qetl.job.bounded.batch_read.test_a_worker_reads_by_batch_only_from_a_source_that_declares_it:{[t]
    req:{[b] @[{.qetl.job.bounded.batching.require[`probe;x]};b;{[e] e}]};
    base:`group_by`max_rows!(`sym;5);
    ok:req[`source`batching!(`hdb_fx_markouts;base,enlist[`source_read]!enlist `batch)];
    .qunit.assertEquals[(ok`source_read;ok`reuse_rows);(`batch;0);"the markout source reads by batch, reusing nothing unless told"];
    .qunit.assertTrue[req[`source`batching!(`hdb_fx_markouts;base,enlist[`source_read]!enlist `ledger)] like "*source_read must be `window or `batch";"a word that is neither"];
    .qunit.assertTrue[req[`source`batching!(`demo_deals;base,enlist[`source_read]!enlist `batch)] like "*source_read `batch needs a source that declares batch_read, and demo_deals declares none";"a source with nothing to read"];
    ctx:`group_by`max_rows`source_read`context!(`sym;5;`batch;.qt.qetl.job.bounded.batching.legs_context);
    .qunit.assertTrue[req[`source`batching!(`hdb_fx_markouts;ctx)] like "*source_read `batch replaces context*";"the source narrows, so a context would narrow twice"];
    .qunit.assertTrue[req[`source`batching!(`hdb_fx_markouts;base,enlist[`reuse_rows]!enlist 100)] like "*reuse_rows needs source_read `batch*";"nothing to reuse in a window read whole"];
    .qunit.assertTrue[req[`source`batching!(`hdb_fx_markouts;base,`source_read`reuse_rows!(`batch;-1))] like "*reuse_rows must be a non-negative, finite long*";"a negative bound"];
    .qunit.assertTrue[req[`source`batching!(`hdb_fx_markouts;base,`source_read`reuse_rows!(`batch;0W))] like "*reuse_rows must be a non-negative, finite long*";"no bound is not a bound"]};

/ --- the window read -----------------------------------------------------------------------------

.qt.qetl.job.bounded.batch_read.test_the_window_read_holds_the_executions_and_one_book_per_pair_and_source:{[t]
    h:.qt.qetl.job.bounded.batch_read.hour[];
    whole:.qpipe.source.hdb_fx_markouts.query[.qt.qetl.job.bounded.batch_read.hdb;h`range_from;h`range_to];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    summary:.qpipe.source.hdb_fx_markouts.window[.qt.qetl.job.bounded.batch_read.hdb;h`range_from;h`range_to];
    .qunit.assertEquals[summary`executions;whole`executions;"the primary rows are the query's"];
    .qunit.assertEquals[asc (summary[`market_data]`sym),'(summary[`market_data]`source);asc (`EURPLN`a;`EURUSD`a;`EURUSD`b);"a book for each (pair, source) the window's executions need"];
        .qunit.assertEquals[asc cols summary`market_data;asc cols whole`market_data;"and it keeps the input's columns, so it satisfies the same contract"];
    .qunit.assertTrue[.qetl.source.validate[`hdb_fx_markouts;summary];"the summary satisfies the contract"]};

.qt.qetl.job.bounded.batch_read.test_ready_answers_the_same_on_the_summary_as_on_the_whole_window:{[t]
    h:.qt.qetl.job.bounded.batch_read.hour[];
    f:{[h;without]
        .qt.qetl.job.bounded.batch_read.serve[without];
        whole:.qpipe.source.hdb_fx_markouts.query[.qt.qetl.job.bounded.batch_read.hdb;h`range_from;h`range_to];
        summary:.qpipe.source.hdb_fx_markouts.window[.qt.qetl.job.bounded.batch_read.hdb;h`range_from;h`range_to];
        (.qpipe.source.hdb_fx_markouts.ready[whole;h`range_from;h`range_to];.qpipe.source.hdb_fx_markouts.ready[summary;h`range_from;h`range_to])}[h];
    r:f `symbol$();
    .qunit.assertEquals[r 0;r 1;"every book present: ready on both"];
    r:f `EURPLN;
    .qunit.assertEquals[r 0;r 1;"one leg's book missing: a legitimate unavailable row, ready on both"];
    r:f `EURUSD`EURPLN;
    .qunit.assertTrue[0<count r 0;"no books at all: not ready on the whole window"];
    .qunit.assertEquals[r 0;r 1;"and the same words on the summary"]};

/ --- the reads -------------------------------------------------------------------------------------

.qt.qetl.job.bounded.batch_read.test_a_batched_window_publishes_what_an_unbatched_one_does_reading_each_batch_its_own_books:{[t]
    .qt.qetl.job.bounded.batch_read.set_batching[(::)];
    .qunit.assertTrue[.qt.qetl.job.bounded.batch_read.window_run[];"the unbatched window is published"];
    plain:.qt.qetl.job.bounded.batch_read.published[];
    whole_read:.qt.qetl.job.bounded.batch_read.sent;
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;0]];
    .qunit.assertTrue[.qt.qetl.job.bounded.batch_read.window_run[];"the batched window is published"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.published[];plain;"the same rows by identity"];
    .qunit.assertEquals[count plain;36;"including USDPLN's legs: EURUSD inverted and EURPLN"];
    .qunit.assertEquals[whole_read[;0];`executions`books;"unbatched: the executions, then every book at once"];
    sent:.qt.qetl.job.bounded.batch_read.sent;
    .qunit.assertEquals[sent[;0];`executions`books`books`books;"batched: the executions, the presence summary, then a read for each batch that needs books"];
    .qunit.assertEquals[sent[2 3;2];(enlist `EURUSD;`EURPLN`EURUSD);"EURUSD's batch reads EURUSD, USDPLN's reads both of its legs; the crypto fill reads nothing"];
    .qunit.assertTrue[(sent[2;1])<(whole_read[1;1]);"EURUSD's batch reads fewer rows than the whole window holds"];
    .qunit.assertEquals[.qetl.job.bounded.batching.counts`reads;3;"three keys were asked for across the batches"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.claimed[];1b;"and the window is claimed once all its batches passed"]};

.qt.qetl.job.bounded.batch_read.test_a_book_one_batch_read_serves_the_next_within_the_bound:{[t]
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    sent:.qt.qetl.job.bounded.batch_read.sent;
    .qunit.assertEquals[sent[2 3;2];(enlist `EURUSD;enlist `EURPLN);"USDPLN's batch reads only EURPLN: EURUSD was read for the batch before"];
    c:.qetl.job.bounded.batching.counts;
    .qunit.assertEquals[(c`hits;c`reads;c`evicted);1 2 0;"one key served from the cache, two read, none evicted"];
    held:.qetl.job.bounded.batching.cache;
    .qunit.assertTrue[(sum held`n)<=1000;"within the bound"];
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;0]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    plain_reads:sum .qt.qetl.job.bounded.batch_read.sent[;1];
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    .qunit.assertTrue[(sum .qt.qetl.job.bounded.batch_read.sent[;1])<plain_reads;"reuse reads fewer rows from the source"]};

.qt.qetl.job.bounded.batch_read.test_reuse_is_identical_to_no_reuse:{[t]
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;0]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    plain:.qt.qetl.job.bounded.batch_read.published[];
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.published[];plain;"reusing a book changes no row"];
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.serve[`symbol$()];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[0W;`batch;1000]];
    .qt.qetl.job.bounded.batch_read.window_run[];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.published[];plain;"nor does one batch holding every symbol"]};

.qt.qetl.job.bounded.batch_read.test_the_bound_evicts_the_least_recently_used_and_never_keeps_what_alone_exceeds_it:{[t]
    sc:`worker`version`range_from`range_to!(`w;`v1;2026.09.17D10:00:00;2026.09.17D11:00:00);
    rows:{[n] enlist[`market_data]!enlist ([] sym:n#`a)};
    .qetl.job.bounded.batching.reset_reads[];
    .qunit.assertEquals[.qetl.job.bounded.batching.put[sc;5;`a`b;(rows 3;rows 3)];1;"two entries of 3 do not fit 5: the older goes"];
    .qunit.assertEquals[asc .qetl.job.bounded.batching.cache`item;enlist `b;"the newer is the one kept"];
    .qetl.job.bounded.batching.take[sc;enlist `b];
    .qunit.assertEquals[.qetl.job.bounded.batching.put[sc;5;enlist `c;enlist rows 2];0;"3 and 2 fit 5: nothing goes"];
    .qunit.assertTrue[(sum .qetl.job.bounded.batching.cache`n)<=5;"never above the bound"];
    .qunit.assertEquals[.qetl.job.bounded.batching.put[sc;5;enlist `d;enlist rows 6];0;"an entry bigger than the bound is not kept, and costs the others nothing"];
    .qunit.assertEquals[asc .qetl.job.bounded.batching.cache`item;`b`c;"the cache is as it was"]};

.qt.qetl.job.bounded.batch_read.test_a_row_read_for_another_version_or_window_is_never_served:{[t]
    sc:`worker`version`range_from`range_to!(`w;`v1;2026.09.17D10:00:00;2026.09.17D11:00:00);
    rows:enlist[`market_data]!enlist ([] sym:3#`a);
    .qetl.job.bounded.batching.reset_reads[];
    .qetl.job.bounded.batching.put[sc;100;enlist `a;enlist rows];
    .qunit.assertEquals[key .qetl.job.bounded.batching.take[sc;enlist `a];enlist `a;"the same worker, version and window: a hit"];
    .qunit.assertEquals[count .qetl.job.bounded.batching.take[@[sc;`version;:;`v2];enlist `a];0;"another source_version: a miss"];
    .qunit.assertEquals[count .qetl.job.bounded.batching.take[@[sc;`range_to;:;2026.09.17D12:00:00];enlist `a];0;"another window: a miss"];
    .qunit.assertEquals[count .qetl.job.bounded.batching.take[@[sc;`worker;:;`other];enlist `a];0;"another worker: a miss"];
    .qunit.assertEquals[count .qetl.job.bounded.batching.cache;1;"and it is still held, for the scope that read it"]};

.qt.qetl.job.bounded.batch_read.test_starting_an_attempt_drops_all_this_workers_previous_reads:{[t]
    .qt.qetl.job.bounded.batch_read.start[];
    w:.qt.qetl.job.bounded.batch_read.worker;
    sc:.qetl.job.bounded.batching.scope[w;.qt.qetl.job.bounded.batch_read.hour[]];
    rows:enlist[`market_data]!enlist ([] sym:3#`a);
    .qetl.job.bounded.batching.reset_reads[];
    .qetl.job.bounded.batching.put[@[sc;`version;:;`v0];100;enlist `a;enlist rows];
    .qetl.job.bounded.batching.put[@[sc;`range_to;:;2026.09.17D12:00:00];100;enlist `b;enlist rows];
    .qetl.job.bounded.batching.put[sc;100;enlist `c;enlist rows];
    .qetl.job.bounded.batching.put[@[sc;`worker;:;`other];100;enlist `d;enlist rows];
    .qunit.assertEquals[.qetl.job.bounded.batching.begin_reads[w];3;"all prior reads, including the same window and version"];
    .qunit.assertEquals[.qetl.job.bounded.batching.cache`item;enlist `d;"another worker's reads stay"]};

/ --- readiness and failure ---------------------------------------------------------------------------

.qt.qetl.job.bounded.batch_read.test_a_window_with_no_books_is_not_ready_so_no_batch_reads_and_nothing_is_claimed:{[t]
    .qt.qetl.job.bounded.batch_read.serve[`EURUSD`EURPLN];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.window_run[];0b;"the window failed"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.sent[;0];`executions`books;"the executions and the presence summary: no batch asked the source for anything"];
    .qunit.assertEquals[(.qetl.job.bounded.batching.counts`reads;count get `fx_markout);0 0;"no key read, nothing published"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.claimed[];0b;"and the window is not claimed, so the next run plans it again"]};

.qt.qetl.job.bounded.batch_read.test_a_failing_read_fails_the_window_naming_the_batch_with_nothing_written_or_covered:{[t]
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    / queries: 1 executions, 2 presence, 3 EURUSD's batch, 4 USDPLN's
    `.qt.qetl.job.bounded.batch_read.fail_at set 4;
    .qt.qetl.job.bounded.batch_read.start[];
    r:.qetl.job.bounded.do_window[.qt.qetl.job.bounded.batch_read.worker;.qt.qetl.job.bounded.batch_read.hour[]];
    .qunit.assertEquals[r;0b;"the window failed"];
    .qunit.assertEquals[count get `fx_markout;0;"the batch that succeeded wrote nothing beside the one that did not"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.claimed[];0b;"and the window is not covered"];
    `.qt.qetl.job.bounded.batch_read.fail_at set 0;
    .qunit.assertTrue[.qetl.job.bounded.do_window[.qt.qetl.job.bounded.batch_read.worker;.qt.qetl.job.bounded.batch_read.hour[]];"the retry publishes the window"];
    .qunit.assertEquals[(count get `fx_markout;.qt.qetl.job.bounded.batch_read.claimed[]);(36;1b);"each row once, and now claimed"]};

.qt.qetl.job.bounded.batch_read.test_a_read_that_fails_in_transit_is_retried_as_the_window_fetch_is:{[t]
    .qetl.cfg.set_override[`retry_base_delay_ms;"0"];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;0]];
    `.qt.qetl.job.bounded.batch_read.fail_at set 3;
    `.qt.qetl.job.bounded.batch_read.fail_with set "connection refused";
    r:.qt.qetl.job.bounded.batch_read.window_run[];
    `.qt.qetl.job.bounded.batch_read.fail_with set "boom";
    .qunit.assertEquals[r;1b;"the window is published after a retry"];
    .qunit.assertEquals[count get `fx_markout;36;"every row"]};

.qt.qetl.job.bounded.batch_read.test_a_failed_window_retry_reads_books_that_arrived_after_the_failure:{[t]
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    `.qt.qetl.job.bounded.batch_read.fail_at set 4;
    .qt.qetl.job.bounded.batch_read.start[];
    worker:.qt.qetl.job.bounded.batch_read.worker;
    h:.qt.qetl.job.bounded.batch_read.hour[];
    .qunit.assertEquals[.qetl.job.bounded.do_window[worker;h];0b;"a later batch fails after EURUSD was cached"];
    .qunit.assertEquals[.qetl.job.bounded.batching.cache`item;enlist `EURUSD;"the failed attempt left a book"];
    .qunit.assertEquals[(count get `fx_markout;.qt.qetl.job.bounded.batch_read.claimed[]);(0;0b);"nothing published or covered"];
    late:0!select by sym,source from `source_time xasc select from market_data where sym=`EURUSD;
    late:update source_time:h[`range_from]+0D00:00:00.5,
        bid_prices:bid_prices+0.01,ask_prices:ask_prices+0.01 from late;
    `market_data set market_data,late;
    `.qt.qetl.job.bounded.batch_read.fail_at set 0;
    .qunit.assertTrue[.qetl.job.bounded.do_window[worker;h];"the same window and version now succeeds"];
    whole:.qpipe.source.hdb_fx_markouts.query[.qt.qetl.job.bounded.batch_read.hdb;h`range_from;h`range_to];
    expected:`venue`fill_id`horizon`kind`step xasc .qetl.job.bounded.transform_batch[worker;whole];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.published[];expected;"every value matches a fresh source read, including the late books"];
    .qunit.assertEquals[(count get `fx_markout;.qt.qetl.job.bounded.batch_read.claimed[]);(36;1b);"all rows published once before coverage"]};

.qt.qetl.job.bounded.batch_read.test_a_source_returning_a_book_nobody_asked_for_is_refused:{[t]
    br:.qetl.source.batch_read `hdb_fx_markouts;
    src:.qetl.source.def `hdb_fx_markouts;
    got:enlist[`market_data]!enlist ([] source_time:2#2026.09.17D10:00:00; sym:`EURUSD`GBPUSD; source:2#`a);
    .qunit.assertThrows[.qetl.job.bounded.batching.split_read[src;br;got;];enlist `EURUSD;
        "batch_read: read returned market_data rows for GBPUSD which were not asked for";"over-reading is a fault, not a bonus"];
    .qunit.assertThrows[.qetl.job.bounded.batching.split_read[src;br;;enlist `EURUSD];enlist[`other]!enlist 1;
        "batch_read: read must return a dictionary of the supporting inputs market_data";"the supporting inputs are named"]};

/ --- off the live path --------------------------------------------------------------------------------

.qt.qetl.job.bounded.batch_read.test_on_the_fixture_a_batched_window_is_read_whole_and_publishes_the_same_rows:{[t]
    d:.qt.qetl.job.bounded.batch_read.hour[];
    run:{[d;unused]
        .qpipe.job.hdb_fx_markouts_backfill.init[`source_version`range_from`range_to!(`v1;d`range_from;d`range_to)];
        .qpipe.job.hdb_fx_markouts_backfill.run[]}[d];
    .qt.qetl.job.bounded.batch_read.set_batching[(::)];
    run[::];
    plain:.qt.qetl.job.bounded.batch_read.published[];
    .qt.qetl.job.bounded.batch_read.fresh_window[];
    .qt.qetl.job.bounded.batch_read.set_batching[.qt.qetl.job.bounded.batch_read.declare[1;`batch;1000]];
    r:run[::];
    .qunit.assertEquals[r`state;`completed;"the run completes"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batch_read.published[];plain;"the fixture holds no source to read: the batches use the window it returned"];
    .qunit.assertEquals[.qetl.job.bounded.batching.counts`queries;0;"and nothing was asked of a source"]};
