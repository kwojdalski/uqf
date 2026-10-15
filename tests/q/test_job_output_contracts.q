/ test_job_output_contracts.q - what every streaming job ACTUALLY publishes,
/ held to the tickerplant table it publishes into (.qt.qpipe.job_output_contracts).
/ .
/ The declarations were already checked end to end except at one link:
/ .qt.qetl.plant.tables holds each job's declared table to the plant's, and
/ python/uqs/tests/test_publish_declarations.py holds each publish call to
/ the job's `publishes`. Neither runs a job, so neither sees the rows it
/ builds - and those rows are what the plant receives.
/ .
/ That link matters more than it looks. .qtorq.publish sends a table to
/ TorQ as `value flip`, which is POSITIONAL: a job whose select lists
/ quote_qty before base_qty publishes two floats the plant accepts and
/ stores in each other's columns, with no error anywhere. Only fx_positions
/ had a test comparing its published columns to its declared ones; the
/ others were one reordered select away from that.
/ .
/ So every publishing job is driven here against a recorder and every batch
/ it publishes is compared, by name, order and type, with the plant's table
/ minus `time` (which the plant stamps). Feeds drive themselves on their own
/ timer. A job that subscribes needs a driver: a few lines pushing a batch it
/ actually acts on, declared as .qt.qpipe.job.<job>.contract_driver in its own test
/ file (where `uqs job new` scaffolds one) or in the dictionary below. A new
/ job with no driver fails
/ test_every_publishing_job_can_be_driven by name - that is the point, since
/ a job this suite cannot drive is a job whose output nothing checks.
/ .
/ The drivers reuse the batch builders the job suites already prove
/ (.qt.qetl.job.stream, .qt.qpipe.job.cross_arbitrage), so an input shape is maintained in one place. All
/ suites load into one process (tests/run_tests.q), which is what makes
/ them reachable from here.


/ --- the plant's tables ----------------------------------------------------

/ table name -> the plant's empty table, from src/etl/plant_tables.q.
/ .
/ Each definition's right-hand side is evaluated rather than the file
/ loaded, so this suite defines no root tables - the reason .qt.qetl.plant.tables
/ gives for loading that file only inside itself. The line filter is
/ .qt.qetl.plant.tables.declared's, and like it contains no brackets: `like` reads
/ "[...]" as a character class.
.qt.qpipe.job_output_contracts.plant:{[]
    ls:read0 `$":src/etl/plant_tables.q";
    / The file defines each table qualified from the root (.qetl.plant.quote:...).
    ls:{$[x like ".qetl.plant.*"; 12_x; x]} each ls;
    / `name:([]` exactly, as uqs and .qetl.plant.adopt_vendored read it: a
    / looser "*:(*" also caught the keyed `elements:([table:...]...)` lookup.
    ls:ls where (not ls like "/*") and {[l] (0<count l) and "([]"~3#(1+l?":")_l} each ls;
    (`$ {x til x?":"} each ls)!{value (1+x?":") _ x} each ls}

.qt.qpipe.job_output_contracts.shapes:()

.qt.qpipe.job_output_contracts.beforeNamespace_read_the_plant:{[] `.qt.qpipe.job_output_contracts.shapes set .qt.qpipe.job_output_contracts.plant[];}

/ The plant's columns and type characters for `tbl`, without `time`.
.qt.qpipe.job_output_contracts.want:{[tbl]
    m:select from 0!meta .qt.qpipe.job_output_contracts.shapes tbl where c<>`time;
    (m`c;m`t)}

/ Published tables this tree does not define, so it has no shape to hold
/ them to. Each needs a reason; test_the_excuses_still_describe_something_real
/ keeps the list from outliving what it excuses.
.qt.qpipe.job_output_contracts.unowned:enlist[`quote]!enlist
    "the vendored starter pack's quote table, which fx_feed publishes into and lib/torq defines"

/ (job; table; reason) for a table a job declares but its handlers never
/ build. Several entries, so a list of triples as written; a single entry would
/ need the enlist .qt.qetl.plant.tables.not_exchanged explains.
.qt.qpipe.job_output_contracts.not_built:(
    (`superbook;`config_change;
        "published by .qetl.cfg.audit through the job's own publish when its config changes, not by its handlers");
    (`cross_arbitrage;`config_change;
        "the same .qetl.cfg.audit publication");
    (`fx_positions;`config_change;
        "the same .qetl.cfg.audit publication, of its limits (#1112)"))

/ --- the comparison -------------------------------------------------------

/ "" when `cell` has the plant's shape for `tbl`, else what differs.
/ .
/ A cell is what a job handed publish: a table, or a list of column vectors
/ as the feeds send. A table is held to names, order and types; a column
/ list has no names, so it is held to count and types, and order is only
/ as good as the types can tell apart. A plant type of " " is a general
/ list column (the vector-per-row book columns), which any column matches.
/ An empty batch is not checked: .qtorq.publish sends nothing for one.
.qt.qpipe.job_output_contracts.problem:{[tbl;cell]
    w:.qt.qpipe.job_output_contracts.want tbl; wc:w 0; wt:w 1;
    if[99h=type cell; :"a keyed table, where the plant appends rows"];
    if[98h=type cell;
        if[0=count cell; :""];
        m:select from 0!meta cell where c<>`time;
        if[not wc~m`c;
            :"columns ",(" " sv string m`c),", plant has ",(" " sv string wc)];
        bad:where not (wt=" ") or wt=m`t;
        :$[count bad;
            "types differ on ",(" " sv string wc bad),": plant ",(wt bad),", published ",(m[`t] bad);
            ""]];
    if[0h=type cell;
        if[0=count first cell; :""];
        if[not (count cell)=count wc;
            :string[count cell]," columns, plant has ",string[count wc]," (",(" " sv string wc),")"];
        ty:type each cell;
        bad:where not (wt=" ") or ty=`short$.Q.t?wt;
        :$[count bad;
            "types differ on ",(" " sv string wc bad),": plant ",(wt bad),", published ",.Q.t abs ty bad;
            ""]];
    "neither a table nor a list of columns"}

/ --- driving the jobs -----------------------------------------------------

/ Two LPs quoting one pair, crossed, as .qt.qpipe.job.superbook drives market_data.
.qt.qpipe.job_output_contracts.lp_quotes:{[]
    ([] time:2#.z.p; sym:`EURUSD`EURUSD; bid:1.101 1.099; ask:1.103 1.100;
        bsize:100 500; asize:200 60; src:`LP_A`LP_B)}

/ The last batch `j` published, as the plant would hand it downstream.
.qt.qpipe.job_output_contracts.last_of:{[j] first last exec rows from .qt.qetl.job.stream.published where job=j}

/ job -> a niladic function pushing input the job acts on. Each driver
/ runs after .qt.qetl.job.stream.reset[], which empties job state and wires every job
/ to .qt.qetl.job.stream.recorder.
.qt.qpipe.job_output_contracts.drivers:`demo_markout`posbook`vectorize`eq_orderbook`fx_positions`executions`market_data`superbook`arbitrage`cross_arbitrage`exec_bars!(
    {[] .qpipe.job.demo_markout.on_batch[`trades;([] time:enlist .qt.qetl.job.stream.d 0; sym:enlist `EURUSD; side:enlist 1;
            trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000; fill_id:enlist `f1)];
        .qpipe.job.demo_markout.on_batch[`quote;([] time:enlist .qt.qetl.job.stream.d 1; sym:enlist `EURUSD;
            bid:enlist 1.1004; ask:enlist 1.1006)];
        .qpipe.job.demo_markout.score_ready .qt.qetl.job.stream.d 20};
    {[] .qpipe.job.posbook.on_batch[`market_data;.qt.qetl.job.stream.a_book[`EURUSD;1.104]];
        .qpipe.job.posbook.on_batch[`executions;.qt.qetl.job.stream.an_execution[.qt.qetl.job.stream.d 0;`EURUSD;1;1.1;1e6]];
        / and the day ends, so its opening book is published too (#943)
        .qetl.job.stream.snapshot `posbook};
    {[] .qpipe.job.vectorize.on_batch[`wide_orderbook;.qt.qetl.job.stream.wide_row[]]};
    {[] .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qetl.job.stream.mbp10_batch[]]};
    {[] .qpipe.job.fx_positions.load_limits .qt.qetl.job.stream.mk_limits[];
        .qt.qetl.job.stream.to_fx_positions .qt.qetl.job.stream.orders_batch[];
        .qpipe.job.fx_positions.on_timer[];
        .qetl.job.stream.snapshot `fx_positions};
    {[] .qpipe.job.executions.on_batch[`trades;.qt.qetl.job.stream.fx_fill[`EURUSD;1;1.085;1e6]];
        .qpipe.job.executions.on_batch[`crypto_trades;.qt.qetl.job.stream.crypto_fill[`$"BTC-USDT";-1;62000f;0.25]]};
    {[] .qpipe.job.market_data.on_batch[`quote;.qt.qpipe.job_output_contracts.lp_quotes[]]};
    {[] .qt.qpipe.job_output_contracts.drivers[`market_data][];
        .qpipe.job.superbook.on_batch[`market_data;.qt.qpipe.job_output_contracts.last_of `market_data]};
    {[] .qt.qpipe.job_output_contracts.drivers[`superbook][];
        .qpipe.job.arbitrage.on_batch[`superbook;.qt.qpipe.job_output_contracts.last_of `superbook]};
    {[] .qpipe.job.cross_arbitrage.on_batch[`superbook;0!.qt.qpipe.job.cross_arbitrage.with_direct[164.80;164.90]]};
    {[] .qt.qetl.job.stream.bars.contract_driver[]})

/ Every registered job that declares at least one published table.
.qt.qpipe.job_output_contracts.publishing:{[] j where {[j] 0<count (),.qetl.job.stream.def[j]`publishes} each j:.qetl.job.stream.defined[]}

/ A feed drives itself: it subscribes to nothing and publishes on a timer.
/ Fifty ticks because crypto_mock's fills are a draw - .qt.qetl.job.stream relies on
/ twenty producing one, and this suite needs one every run.
.qt.qpipe.job_output_contracts.is_feed:{[j] 0=count (),.qetl.job.stream.def[j]`subscribe_to}

/ The driver `j`'s own test file declares, as .qt.qpipe.job.<job>.contract_driver -
/ where `uqs job new` scaffolds one - or :: when it declares none.
/ .
/ A second home because the dictionary above cannot take a new job's entry:
/ it is one literal in this file, and a test file loaded before this one that
/ amended it would be overwritten when this one loads. A name the job's test
/ owns has no load order. The dictionary wins where both exist; it holds the
/ jobs whose suites predate this.
.qt.qpipe.job_output_contracts.own_driver:{[j] @[get;` sv .qt.support.job_suite[j],`contract_driver;{[e] ::}]}

/ Can this suite drive `j`: a driver in either place, or its own timer?
.qt.qpipe.job_output_contracts.can_drive:{[j] (j in key .qt.qpipe.job_output_contracts.drivers) or (100h=type .qt.qpipe.job_output_contracts.own_driver j) or .qt.qpipe.job_output_contracts.is_feed j}

/ Everything `j` published when driven, as a (tbl; rows) table. Each rows
/ cell holds its batch ENLISTED, as .qt.qetl.job.stream.recorder stores it - so a
/ reader takes `first each` before looking at a batch, as .qt.qetl.job.stream does.
/ Jobs whose own driver THREW, as (job; error) pairs. `uqs job new` scaffolds
/ a contract_driver that throws until someone writes it, which is the right
/ signal and was reaching the reader in the worst possible way: can_drive sees
/ a lambda and says yes, so the throw escaped `runs` and turned the two
/ contract tests below into ERRORS - and an errored test never reaches an
/ assertion, so both reported nothing at all.
/ .
/ Trapped here instead, and asserted by its own test. A job whose driver is
/ not written yet is then indistinguishable, to the two contract tests, from
/ one with no driver at all: not checked, and named by a failure that says
/ what to write. Which is what the design already did for a missing driver.
.qt.qpipe.job_output_contracts.unimplemented:()

.qt.qpipe.job_output_contracts.drive:{[j]
    .qt.qetl.job.stream.reset[];
    .qetl.job.stream.reset `cross_arbitrage;
    $[j in key .qt.qpipe.job_output_contracts.drivers; .qt.qpipe.job_output_contracts.drivers[j][];
      100h=type f:.qt.qpipe.job_output_contracts.own_driver j;
        @[f;::;{[j;e] `.qt.qpipe.job_output_contracts.unimplemented set
            .qt.qpipe.job_output_contracts.unimplemented,enlist (j;e); }[j]];
      .qt.qpipe.job_output_contracts.is_feed j; do[50; (.qetl.job.stream.def[j]`on_timer)[]];
      '"drive: ",string[j]," subscribes and has no driver"];
    select tbl, rows from .qt.qetl.job.stream.published where job=j}

/ The jobs this suite can drive, each with what it published - minus any whose
/ driver threw, which cannot be held to a contract it never reached.
.qt.qpipe.job_output_contracts.runs:{[]
    `.qt.qpipe.job_output_contracts.unimplemented set ();
    js:.qt.qpipe.job_output_contracts.publishing[] where .qt.qpipe.job_output_contracts.can_drive each .qt.qpipe.job_output_contracts.publishing[];
    (first each .qt.qpipe.job_output_contracts.unimplemented) _ js!.qt.qpipe.job_output_contracts.drive each js}

/ Leave the jobs as .qt.qetl.job.stream leaves them, not holding this suite's batches.
.qt.qpipe.job_output_contracts.afterNamespace_reset_the_jobs:{[] .qt.qetl.job.stream.reset[];}

/ --- the contract ---------------------------------------------------------

.qt.qpipe.job_output_contracts.bad:()

.qt.qpipe.job_output_contracts.test_every_publishing_job_can_be_driven:{[t]
    missing:.qt.qpipe.job_output_contracts.publishing[] where not .qt.qpipe.job_output_contracts.can_drive each .qt.qpipe.job_output_contracts.publishing[];
    .qunit.assertEquals[missing;`symbol$();
        "every job that subscribes and publishes has a driver - .qt.qpipe.job.<job>.contract_driver in its own test file, or an entry in .qt.qpipe.job_output_contracts.drivers - without one, nothing checks what it sends the plant"]};

.qt.qpipe.job_output_contracts.test_no_contract_driver_is_left_scaffolded:{[t]
    / The scaffolded driver throws, so this is the test a fresh `uqs job new`
    / is meant to leave red. It replaces two errors that said `.
    .qt.qpipe.job_output_contracts.runs[];
    / The job AND its error IN THE MESSAGE. assertEquals reports only its msg,
    / never the values it compared - which is why .qt.qetl.transform builds its detail
    / into the string too. With thirty publishing jobs, "a driver threw" sends
    / the reader looking; the scaffolded driver's own text names the file to
    / open and what to write in it.
    detail:", " sv {string[x 0],": ",x 1} each .qt.qpipe.job_output_contracts.unimplemented;
    .qunit.assertEquals[detail;"";
        "every declared contract_driver runs - a scaffolded one throws until it is written: ",detail]};

.qt.qpipe.job_output_contracts.test_every_driver_names_a_publishing_job:{[t]
    / The other direction, so a renamed or retired job cannot leave a
    / driver behind that drives nothing.
    .qunit.assertEquals[(key .qt.qpipe.job_output_contracts.drivers) except .qt.qpipe.job_output_contracts.publishing[];`symbol$();
        "every driver drives a registered job that publishes"]};

.qt.qpipe.job_output_contracts.test_every_declared_table_is_actually_published:{[t]
    / A driver that publishes nothing would pass the shape check below
    / vacuously, so each declared table must turn up at least once.
    r:.qt.qpipe.job_output_contracts.runs[];
    excused:{[p] `$string[p 0],"/",string p 1} each .qt.qpipe.job_output_contracts.not_built;
    `.qt.qpipe.job_output_contracts.bad set ();
    {[r;excused;j]
        seen:distinct exec tbl from r j;
        owed:((),.qetl.job.stream.def[j]`publishes) except seen;
        owed:owed where not ({`$string[x],"/",string y}[j] each owed) in excused;
        if[count owed; `.qt.qpipe.job_output_contracts.bad set .qt.qpipe.job_output_contracts.bad,enlist string[j]," never published ",", " sv string owed]
      }[r;excused] each key r;
    .qunit.assertEquals[.qt.qpipe.job_output_contracts.bad;();
        "driven, every job publishes each table it declares"]};

.qt.qpipe.job_output_contracts.test_every_publication_has_the_plant_shape:{[t]
    r:.qt.qpipe.job_output_contracts.runs[];
    `.qt.qpipe.job_output_contracts.bad set ();
    {[r;j]
        {[j;tbl;cell]
            msg:$[tbl in key .qt.qpipe.job_output_contracts.unowned; "";
                  not tbl in key .qt.qpipe.job_output_contracts.shapes; "has no table in src/etl/plant_tables.q";
                  .qt.qpipe.job_output_contracts.problem[tbl;cell]];
            if[count msg; `.qt.qpipe.job_output_contracts.bad set .qt.qpipe.job_output_contracts.bad,enlist string[j],"/",string[tbl],": ",msg]
          }[j]'[exec tbl from r j;first each exec rows from r j]
      }[r] each key r;
    .qunit.assertEquals[.qt.qpipe.job_output_contracts.bad;();
        "every batch a job publishes has its plant table's columns, in the plant's order, with the plant's types"]};

/ --- the check must fail on what it exists for ---------------------------

/ One fx_position row as fx_positions builds it.
.qt.qpipe.job_output_contracts.a_position:{[]
    ([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        base_qty:enlist 6e5; quote_qty:enlist -651000f; fill_count:enlist 2; break_even:enlist 1.085)}

.qt.qpipe.job_output_contracts.test_the_right_shape_passes:{[t]
    .qunit.assertEquals[.qt.qpipe.job_output_contracts.problem[`fx_position;.qt.qpipe.job_output_contracts.a_position[]];"";
        "the published columns fx_positions builds are accepted"]};

.qt.qpipe.job_output_contracts.test_two_swapped_columns_of_one_type_are_caught:{[t]
    / The positional failure this suite exists for: both floats, so the
    / plant would take them without complaint.
    swapped:`sym`book`product`quote_qty`base_qty`fill_count`break_even xcols .qt.qpipe.job_output_contracts.a_position[];
    .qunit.assertTrue[0<count .qt.qpipe.job_output_contracts.problem[`fx_position;swapped];
        "quote_qty ahead of base_qty is refused by name"]};

.qt.qpipe.job_output_contracts.test_a_wrong_type_is_caught:{[t]
    wrong:update fill_count:`float$fill_count from .qt.qpipe.job_output_contracts.a_position[];
    .qunit.assertTrue[(.qt.qpipe.job_output_contracts.problem[`fx_position;wrong]) like "types differ on fill_count*";
        "a float where the plant keeps a long is named"]};

.qt.qpipe.job_output_contracts.test_a_column_list_is_held_to_count_and_types:{[t]
    / How the feeds publish: no names, so only what the vectors carry.
    ok:value flip .qt.qpipe.job_output_contracts.a_position[];
    .qunit.assertEquals[.qt.qpipe.job_output_contracts.problem[`fx_position;ok];"";"the same row as columns passes"];
    .qunit.assertTrue[0<count .qt.qpipe.job_output_contracts.problem[`fx_position;-1_ok];"a missing column is caught"];
    .qunit.assertTrue[0<count .qt.qpipe.job_output_contracts.problem[`fx_position;@[ok;6;`long$]];"a long where the plant keeps a float is caught"]};

.qt.qpipe.job_output_contracts.test_the_excuses_still_describe_something_real:{[t]
    / So neither list can rot into cover for the next real gap.
    stale:.qt.qpipe.job_output_contracts.not_built where not {[p] p[1] in (),.qetl.job.stream.def[p 0]`publishes} each .qt.qpipe.job_output_contracts.not_built;
    .qunit.assertEquals[count stale;0;"every not_built pair is still declared by its job"];
    owners:raze {[j] (),.qetl.job.stream.def[j]`publishes} each .qt.qpipe.job_output_contracts.publishing[];
    .qunit.assertEquals[(key .qt.qpipe.job_output_contracts.unowned) except owners;`symbol$();
        "every unowned table is still published by some job"];
    .qunit.assertEquals[(key .qt.qpipe.job_output_contracts.unowned) inter key .qt.qpipe.job_output_contracts.shapes;`symbol$();
        "and none has since gained a table in plant_tables.q, which would make the excuse wrong"]};

/ --- a suite's jobs do not outlive it (#1375) ----------------------------

.qt.qpipe.job_output_contracts.test_restoring_the_stream_registries_removes_what_a_suite_defined:{[t]
    before:key .qetl.job.stream.jobs;
    .qt.support.save_stream_jobs[`.qt.qpipe.job_output_contracts];
    .qetl.job.stream.define[`jo_scratch;`procname`subscribe_to`publishes`on_batch!(
        `joscratch1;enlist `jo_in;enlist `jo_out;{[t;x] }[::])];
    .qunit.assertTrue[`jo_scratch in key .qetl.job.stream.jobs;"defined"];
    .qt.support.restore_stream_jobs[`.qt.qpipe.job_output_contracts];
    .qunit.assertEquals[key .qetl.job.stream.jobs;before;"the registry is as it was"];
    .qunit.assertFalse[`joscratch1 in key .qetl.job.stream.procnames;"and its procname is free again"];
    .qunit.assertFalse[`jo_scratch in key .qetl.job.stream.initial;"and so is its initial state"]};
