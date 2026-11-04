/ test_eq_fills_backfill.q - the equity fills source and its bounded worker
/ (.qpipe.source.eq_fills, .qpipe.job.eq_fills_backfill), #1981.
/ .
/ What these prove: a csv drop of fills becomes canonical `executions` rows on the
/ declared clock, windowed, idempotent under range-and-version skipping, and a window that
/ cannot be read is refused and left uncovered (an under-claim, never an over-claim). The last
/ block carries the rows on to the markout core with the equity domain, and checks the numbers worked by hand below.
/ .
/ HAND-WORKED, on 2026-02-25 (EST, so the regular session is [14:30:00, 21:00:00) UTC), the
/ fills scored with the equity domain against books written here. The same fills against the
/ databento fixture's books are in sidecars/databento/test_eq_fills_markout.q.
/ .
/   x0003  AAPL sell 100 at 271.50, 20:59:50. Books: 20:59:49 bid 271.40 / ask 271.60 (mid
/          271.50), 20:59:54 bid 271.30 / ask 271.50 (mid 271.40) and an after-hours 21:00:20
/          bid 270.00 / ask 270.20 (mid 270.10). At +5s (20:59:55, in the session) the move is
/          -(271.40 - 271.50) = +0.10, worth +10.0 USD, and the execution's own move at the
/          271.50 mid is 0. At +30s the horizon is 21:00:20, after the close: unavailable, with
/          no move, although a book stamped 21:00:20 exists.
/   x0004  META buy 5 at 643.00, 20:59:55. At +5s the horizon is 21:00:00, exactly the close,
/          which is not in the session [open, close): unavailable.

.qt.qpipe.job.eq_fills_backfill.d:{[n] 2026.02.25D00:00:00.000000000+n*1D}

.qt.qpipe.job.eq_fills_backfill.spec_for:{[version;from_n;to_n]
    `source_version`range_from`range_to!(version;.qt.qpipe.job.eq_fills_backfill.d[from_n];.qt.qpipe.job.eq_fills_backfill.d[to_n])}

.qt.qpipe.job.eq_fills_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.eq_fills_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_EQ_FILLS;""];
    .qetl.job.bounded.state.release_lock `eq_fills_backfill;
    .qetl.job.bounded.state.clear_checkpoint `eq_fills_backfill;
    `executions set .qetl.plant.published `executions;
    }

.qt.qpipe.job.eq_fills_backfill.tearDown_release:{[] setenv[`UQF_SOURCE_CRED_EQ_FILLS;""]; .qpipe.job.eq_fills_backfill.cleanup[];}

/ The fixture as the worker's transform makes it.
.qt.qpipe.job.eq_fills_backfill.expected:{[]
    .qetl.transform.apply[`eq_fills_to_executions;enlist[`batch]!enlist .qpipe.source.eq_fills.fixture[]]}

/ --- the mapping into executions ------------------------------------------

.qt.qpipe.job.eq_fills_backfill.test_the_rows_are_canonical_executions:{[t]
    e:.qt.qpipe.job.eq_fills_backfill.expected[];
    .qunit.assertEquals[.qetl.plant.problems[`executions;e];();"the plant's columns and types"];
    .qunit.assertEquals[distinct e`clock;enlist `venue;"every row says whose clock source_time is"];
    .qunit.assertEquals[.qetl.plant.require_clock[`executions;e]~e;1b;"and the clock rule accepts them"];
    .qunit.assertEquals[(distinct e`book;distinct e`product);(enlist `;enlist `);"a drop carries no desk book or product"]};

.qt.qpipe.job.eq_fills_backfill.test_a_receipt_clock_source_stamps_receipt:{[t]
    .qetl.source.fills.declare[`t_receipt_fills;`transport`table_name`columns`sides`clock`tz!(`csv;`fills;.qpipe.source.eq_fills.columns_map;.qpipe.source.eq_fills.sides;`receipt;`UTC)];
    p:`:tests/q/data/eq_fills.csv;
    r:.qetl.source.fills.read[`t_receipt_fills;p;.qt.qpipe.job.eq_fills_backfill.d[0];.qt.qpipe.job.eq_fills_backfill.d[2]];
    ![`.qetl.source.fills.registry;enlist (in;`name;enlist `t_receipt_fills);0b;`symbol$()];
    .qunit.assertEquals[distinct r`clock;enlist `receipt;"the clock is the declaration's, not the source's guess"]};

/ --- the data-quality gate and facts ---------------------------------------

.qt.qpipe.job.eq_fills_backfill.test_the_fixture_passes_the_quality_check:{[t]
    .qunit.assertEquals[count .qpipe.job.eq_fills_backfill.quality_check .qt.qpipe.job.eq_fills_backfill.expected[];0;"five correct fills, no failures"]};

.qt.qpipe.job.eq_fills_backfill.test_the_quality_check_names_each_impossible_batch:{[t]
    e:.qt.qpipe.job.eq_fills_backfill.expected[];
    chk:{[e] (.qpipe.job.eq_fills_backfill.quality_check e)`check};
    .qunit.assertEquals[chk update size:0f from e where fill_id=`x0002;enlist `nonpositive_size;"a fill of nothing"];
    .qunit.assertEquals[chk update price:-1f from e where fill_id=`x0002;enlist `nonpositive_price;"a negative price"];
    .qunit.assertEquals[chk update fill_id:`x0001 from e where fill_id=`x0002;`duplicate_fill`duplicate_fill;"one (venue; fill_id) twice, both rows named"];
    .qunit.assertEquals[chk update venue:?[i=1;`OTHER;venue], fill_id:`x0001 from e where fill_id in `x0001`x0002;`symbol$();"the same id on another venue is another fill"];
    .qunit.assertEquals[chk update clock:` from e where fill_id=`x0002;enlist `unstamped_clock;"a row with no clock"]};

.qt.qpipe.job.eq_fills_backfill.test_facts_describe_the_window:{[t]
    f:.qpipe.job.eq_fills_backfill.facts .qt.qpipe.job.eq_fills_backfill.expected[];
    .qunit.assertEquals[(f`fills;f`symbols;f`venues;f`clocks);(5;2;1;"venue");"five fills over AAPL and META on XNAS, on the venue clock"];
    .qunit.assertEquals[(f`first_fill;f`last_fill);2026.02.25D14:30:00.020000000 2026.02.26D14:31:00.000000000;"first and last execution time"];
    .qunit.assertEquals[(.qpipe.job.eq_fills_backfill.facts 0#.qt.qpipe.job.eq_fills_backfill.expected[])`window;"empty window";"a zero-row window is legal and says so"]};

/ --- a full pass on the fixture ---------------------------------------------

.qt.qpipe.job.eq_fills_backfill.test_no_credential_means_the_fixture_path:{[t]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qunit.assertTrue[null .qpipe.job.eq_fills_backfill.handle;"no file configured, so the demo path"]};

.qt.qpipe.job.eq_fills_backfill.test_a_full_run_lands_every_fill_once:{[t]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    r:.qpipe.job.eq_fills_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed;r`windows_failed);(`completed;2;0);"two days, two windows, none failed"];
    .qunit.assertEquals[value `executions;.qt.qpipe.job.eq_fills_backfill.expected[];"the fixture's fills, once, in order"]};

.qt.qpipe.job.eq_fills_backfill.test_a_full_run_leaves_the_range_covered:{[t]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qpipe.job.eq_fills_backfill.run[];
    .qunit.assertTrue[.qetl.coverage.is_covered[`executions;`;`$"v1~fixture";.z.p;.qt.qpipe.job.eq_fills_backfill.d[0];.qt.qpipe.job.eq_fills_backfill.d[2]];
        "the two windows compose into the requested range"]};

.qt.qpipe.job.eq_fills_backfill.test_a_second_run_at_the_same_version_is_idle:{[t]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qpipe.job.eq_fills_backfill.run[];
    r:.qpipe.job.eq_fills_backfill.run[];
    .qunit.assertEquals[(r`state;count value `executions);(`idle;5);"already covered at this version: nothing fetched, nothing duplicated"]};

.qt.qpipe.job.eq_fills_backfill.test_a_new_version_refetches_and_upserts_by_fill_id:{[t]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qpipe.job.eq_fills_backfill.run[];
    .qpipe.job.eq_fills_backfill.cleanup[];
    .qetl.job.bounded.state.release_lock `eq_fills_backfill;
    .qetl.job.bounded.state.clear_checkpoint `eq_fills_backfill;
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v2;0;2]];
    r:.qpipe.job.eq_fills_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"a corrected drop is fetched again"];
    .qunit.assertEquals[value `executions;.qt.qpipe.job.eq_fills_backfill.expected[];"and replaces the rows with the same (venue; fill_id), not appends"]};

/ --- a live run: the csv file through the csv transport -------------------

.qt.qpipe.job.eq_fills_backfill.test_a_configured_file_is_read_through_the_csv_transport:{[t]
    setenv[`UQF_SOURCE_CRED_EQ_FILLS;"tests/q/data/eq_fills.csv"];
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qunit.assertFalse[null .qpipe.job.eq_fills_backfill.handle;"a handle, so the live path"];
    r:.qpipe.job.eq_fills_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"both windows read"];
    .qunit.assertEquals[value `executions;.qt.qpipe.job.eq_fills_backfill.expected[];"the file's rows are the fixture's"];
    .qunit.assertTrue[.qetl.coverage.is_covered[`executions;`;`v1;.z.p;.qt.qpipe.job.eq_fills_backfill.d[0];.qt.qpipe.job.eq_fills_backfill.d[2]];
        "covered at v1, not v1~fixture: a live read and a fixture run are not the same claim"]};

.qt.qpipe.job.eq_fills_backfill.test_a_missing_file_fails_at_connect_and_takes_nothing:{[t]
    setenv[`UQF_SOURCE_CRED_EQ_FILLS;"build/test-fills/no-such-drop.csv"];
    r:@[.qpipe.job.eq_fills_backfill.init;.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2];{(`threw;x)}];
    .qunit.assertEquals[first r;`threw;"init refused"];
    .qunit.assertTrue[(last r) like "*csv: build/test-fills/no-such-drop.csv is not a file that exists*";"naming the file"]};

.qt.qpipe.job.eq_fills_backfill.test_an_unreadable_window_fails_and_stays_uncovered:{[t]
    system "mkdir -p build/test-fills";
    p:`$":build/test-fills/bad_day2.csv";
    p 0: ("exec_time,exchange,exec_id,ticker,buy_sell,qty,px,commission,commission_ccy";
        "2026-02-25T14:30:00.020000Z,XNAS,x0001,AAPL,B,100,271.60,0.35,USD";
        "2026-02-26T14:31:00.000000Z,XNAS,x0005,AAPL,Q,50,272.00,0.18,USD");
    setenv[`UQF_SOURCE_CRED_EQ_FILLS;"build/test-fills/bad_day2.csv"];
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    r:.qpipe.job.eq_fills_backfill.run[];
    .qunit.assertEquals[(r`windows_completed;r`windows_failed);1 1;"the good day completes, the bad day fails"];
    .qunit.assertEquals[(value `executions)`fill_id;enlist `x0001;"nothing of the bad window is published"];
    .qunit.assertTrue[.qetl.coverage.is_covered[`executions;`;`v1;.z.p;.qt.qpipe.job.eq_fills_backfill.d[0];.qt.qpipe.job.eq_fills_backfill.d[1]];"the good day is covered"];
    .qunit.assertFalse[.qetl.coverage.is_covered[`executions;`;`v1;.z.p;.qt.qpipe.job.eq_fills_backfill.d[1];.qt.qpipe.job.eq_fills_backfill.d[2]];
        "the bad day is not: a re-run redoes it, and no fill is claimed that was not published"]};

/ --- the rows scored: equity domain, databento books ----------------------

.qt.qpipe.job.eq_fills_backfill.session:{[]
    ([] sym:enlist `; open_time:enlist 2026.02.25D14:30:00.000000000; close_time:enlist 2026.02.25D21:00:00.000000000)}

.qt.qpipe.job.eq_fills_backfill.close_book:{[tm;bid;ask]
    ([] source_time:enlist tm; sym:enlist `AAPL; source:enlist `XNAS; bid_prices:enlist enlist bid; bid_sizes:enlist enlist 100f;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 100f; clock:enlist `venue)}

/ The three AAPL books around the close.
.qt.qpipe.job.eq_fills_backfill.books:{[]
    c:.qt.qpipe.job.eq_fills_backfill.close_book;
    raze (c[2026.02.25D20:59:49.000000000;271.4;271.6];
          c[2026.02.25D20:59:54.000000000;271.3;271.5];
          c[2026.02.25D21:00:20.000000000;270f;270.2])}

/ The worker's output, scored at 5s and 30s.
.qt.qpipe.job.eq_fills_backfill.scored:{[]
    .qpipe.job.eq_fills_backfill.init[.qt.qpipe.job.eq_fills_backfill.spec_for[`v1;0;2]];
    .qpipe.job.eq_fills_backfill.run[];
    spec:`horizons`report_ccy`rate_timing`max_age`sessions!(0D00:00:05 0D00:00:30;`USD;`execution;0D00:00:02;.qt.qpipe.job.eq_fills_backfill.session[]);
    exe:(cols .qpipe.transform.markout_core.executions_in)#value `executions;
    .qpipe.transform.markout_core.calc[.qpipe.transform.eq_domain.domain;spec;exe;.qt.qpipe.job.eq_fills_backfill.books[]]}

.qt.qpipe.job.eq_fills_backfill.at:{[r;id;knd;hz;col]
    first (r col) where ((r`fill_id)=id) & ((r`kind)=knd) & (r`horizon)=hz}

.qt.qpipe.job.eq_fills_backfill.test_a_horizon_that_crosses_the_close_is_unavailable:{[t]
    r:.qt.qpipe.job.eq_fills_backfill.scored[];
    at:.qt.qpipe.job.eq_fills_backfill.at[r];
    .qunit.assertEquals[at[`x0003;`total;0D00:00:05;`status];`ok;"+5s is 20:59:55, inside the session"];
    .qunit.assertTrue[1e-9>abs at[`x0003;`total;0D00:00:05;`move]-0.1;"-(271.40 - 271.50)"];
    .qunit.assertTrue[1e-9>abs at[`x0003;`total;0D00:00:05;`report_amount]-10;"times 100 shares"];
    .qunit.assertEquals[at[`x0003;`execution;0D00:00:05;`move];0f;"the execution's own move at the 271.50 mid"];
    .qunit.assertEquals[at[`x0003;`total;0D00:00:30;`status];`unavailable;"+30s is 21:00:20, after the close"];
    .qunit.assertTrue[null at[`x0003;`total;0D00:00:30;`move];"no move, although a book stamped 21:00:20 exists"]};

.qt.qpipe.job.eq_fills_backfill.test_a_horizon_landing_on_the_close_is_unavailable:{[t]
    r:.qt.qpipe.job.eq_fills_backfill.scored[];
    .qunit.assertEquals[.qt.qpipe.job.eq_fills_backfill.at[r;`x0004;`total;0D00:00:05;`status];`unavailable;"20:59:55 + 5s = 21:00:00, and the session is half-open"]};

.qt.qpipe.job.eq_fills_backfill.test_every_scored_row_is_on_the_venue_clock:{[t]
    r:.qt.qpipe.job.eq_fills_backfill.scored[];
    .qunit.assertEquals[distinct r`clock;enlist `venue;"the fills and the books share a clock, so none is clock_mismatch"];
    .qunit.assertFalse[`clock_mismatch in r`status;"nothing refused for its clock"]};
