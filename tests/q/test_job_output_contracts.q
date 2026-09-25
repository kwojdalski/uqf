/ test_job_output_contracts.q - what every streaming job ACTUALLY publishes,
/ held to the tickerplant table it publishes into (.jobouttest).
/ .
/ The declarations were already checked end to end except at one link:
/ .tabletest holds each job's declared table to the plant's, and
/ python/uqs/tests/test_publish_declarations.py holds each publish call to
/ the job's `publishes`. Neither runs a job, so neither sees the rows it
/ builds - and those rows are what the plant receives.
/ .
/ That link matters more than it looks. .qpipe.publish sends a table to
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
/ actually acts on, declared as .<job>test.contract_driver in its own test
/ file (where `uqs new-job` scaffolds one) or in the dictionary below. A new
/ job with no driver fails
/ test_every_publishing_job_can_be_driven by name - that is the point, since
/ a job this suite cannot drive is a job whose output nothing checks.
/ .
/ The drivers reuse the batch builders the job suites already prove
/ (.sjtest, .xarbtest), so an input shape is maintained in one place. All
/ suites load into one process (tests/run_tests.q), which is what makes
/ them reachable from here.

\d .jobouttest

/ --- the plant's tables ----------------------------------------------------

/ table name -> the plant's empty table, from scripts/processes/uqs_tables.q.
/ .
/ Each definition's right-hand side is evaluated rather than the file
/ loaded, so this suite defines no root tables - the reason .tabletest
/ gives for loading that file only inside itself. The line filter is
/ .tabletest.declared's, and like it contains no brackets: `like` reads
/ "[...]" as a character class.
plant:{[]
    ls:read0 `$":scripts/processes/uqs_tables.q";
    ls:ls where (not ls like "/*") and ls like "*:(*";
    (`$ {x til x?":"} each ls)!{value (1+x?":") _ x} each ls}

shapes:()

beforeNamespace_read_the_plant:{[] `.jobouttest.shapes set .jobouttest.plant[];}

/ The plant's columns and type characters for `tbl`, without `time`.
want:{[tbl]
    m:select from 0!meta .jobouttest.shapes tbl where c<>`time;
    (m`c;m`t)}

/ Published tables this tree does not define, so it has no shape to hold
/ them to. Each needs a reason; test_the_excuses_still_describe_something_real
/ keeps the list from outliving what it excuses.
unowned:enlist[`quote]!enlist
    "the vendored starter pack's quote table, which fx_feed publishes into and lib/torq defines"

/ (job; table; reason) for a table a job declares but its handlers never
/ build. Two entries, so a list of triples as written; a single entry would
/ need the enlist .tabletest.not_exchanged explains.
not_built:(
    (`superbook;`config_change;
        "published by .qcfgaudit through the job's own publish when its config changes, not by its handlers");
    (`cross_arbitrage;`config_change;
        "the same .qcfgaudit publication"))

/ --- the comparison -------------------------------------------------------

/ "" when `cell` has the plant's shape for `tbl`, else what differs.
/ .
/ A cell is what a job handed publish: a table, or a list of column vectors
/ as the feeds send. A table is held to names, order and types; a column
/ list has no names, so it is held to count and types, and order is only
/ as good as the types can tell apart. A plant type of " " is a general
/ list column (the vector-per-row book columns), which any column matches.
/ An empty batch is not checked: .qpipe.publish sends nothing for one.
problem:{[tbl;cell]
    w:want tbl; wc:w 0; wt:w 1;
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

/ Two LPs quoting one pair, crossed, as .sbtest drives market_data.
lp_quotes:{[]
    ([] time:2#.z.p; sym:`EURUSD`EURUSD; bid:1.101 1.099; ask:1.103 1.100;
        bsize:100 500; asize:200 60; src:`LP_A`LP_B)}

/ The last batch `j` published, as the plant would hand it downstream.
last_of:{[j] first last exec rows from .sjtest.published where job=j}

/ job -> a niladic function pushing input the job acts on. Each driver
/ runs after .sjtest.reset[], which empties job state and wires every job
/ to .sjtest.recorder.
drivers:`markout`posbook`vectorize`databento_book`fx_positions`executions`marks`market_data`superbook`arbitrage`cross_arbitrage!(
    {[] .qsub.markout.on_batch[`trades;([] time:enlist .sjtest.d 0; sym:enlist `EURUSD; side:enlist 1;
            trade_price:enlist 1.1; size:enlist 1e6; pip_factor:enlist 10000)];
        .qsub.markout.on_batch[`quote;([] time:enlist .sjtest.d 1; sym:enlist `EURUSD;
            bid:enlist 1.1004; ask:enlist 1.1006)];
        .qsub.markout.score_ready .sjtest.d 20};
    {[] .qsub.posbook.on_batch[`marks;.sjtest.a_mark[`EURUSD;1.104]];
        .qsub.posbook.on_batch[`executions;.sjtest.an_execution[.sjtest.d 0;`EURUSD;1;1.1;1e6]]};
    {[] .qsub.vectorize.on_batch[`wide_book;.sjtest.wide_row[]]};
    {[] .qsub.databento_book.on_batch[`databento_mbp10;.sjtest.mbp10_batch[]]};
    {[] .qsub.fx_positions.load_limits .sjtest.mk_limits[];
        .qsub.fx_positions.on_batch[`orders;.sjtest.orders_batch[]];
        .qsub.fx_positions.on_timer[]};
    {[] .qsub.executions.on_batch[`trades;.sjtest.fx_fill[`EURUSD;1;1.085;1e6]];
        .qsub.executions.on_batch[`crypto_trades;.sjtest.crypto_fill[`$"BTC-USDT";-1;62000f;0.25]]};
    {[] .qsub.marks.on_batch[`quote;([] time:enlist .sjtest.d 0; sym:enlist `EURUSD;
            bid:enlist 1.0849; ask:enlist 1.0851)]};
    {[] .qsub.market_data.on_batch[`quote;.jobouttest.lp_quotes[]]};
    {[] .jobouttest.drivers[`market_data][];
        .qsub.superbook.on_batch[`market_data;.jobouttest.last_of `market_data]};
    {[] .jobouttest.drivers[`superbook][];
        .qsub.arbitrage.on_batch[`superbook;.jobouttest.last_of `superbook]};
    {[] .qsub.cross_arbitrage.on_batch[`superbook;0!.xarbtest.with_direct[164.80;164.90]]})

/ Every registered job that declares at least one published table.
publishing:{[] j where {[j] 0<count (),.qstream.declaration[j]`publishes} each j:.qstream.defined[]}

/ A feed drives itself: it subscribes to nothing and publishes on a timer.
/ Fifty ticks because crypto_mock's fills are a draw - .sjtest relies on
/ twenty producing one, and this suite needs one every run.
is_feed:{[j] 0=count (),.qstream.declaration[j]`subscribes}

/ The driver `j`'s own test file declares, as .<job>test.contract_driver -
/ where `uqs new-job` scaffolds one - or :: when it declares none.
/ .
/ A second home because the dictionary above cannot take a new job's entry:
/ it is one literal in this file, and a test file loaded before this one that
/ amended it would be overwritten when this one loads. A name the job's test
/ owns has no load order. The dictionary wins where both exist; it holds the
/ jobs whose suites predate this.
own_driver:{[j] @[get;`$".",string[j],"test.contract_driver";{[e] ::}]}

/ Can this suite drive `j`: a driver in either place, or its own timer?
can_drive:{[j] (j in key .jobouttest.drivers) or (100h=type .jobouttest.own_driver j) or .jobouttest.is_feed j}

/ Everything `j` published when driven, as a (tbl; rows) table. Each rows
/ cell holds its batch ENLISTED, as .sjtest.recorder stores it - so a
/ reader takes `first each` before looking at a batch, as .sjtest does.
/ Jobs whose own driver THREW, as (job; error) pairs. `uqs new-job` scaffolds
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
unimplemented:()

drive:{[j]
    .sjtest.reset[];
    `.qsub.cross_arbitrage.books set 0#.qsub.cross_arbitrage.books;
    $[j in key .jobouttest.drivers; .jobouttest.drivers[j][];
      100h=type f:own_driver j;
        @[f;::;{[j;e] `.jobouttest.unimplemented set
            .jobouttest.unimplemented,enlist (j;e); }[j]];
      is_feed j; do[50; (.qstream.declaration[j]`on_timer)[]];
      '"drive: ",string[j]," subscribes and has no driver"];
    select tbl, rows from .sjtest.published where job=j}

/ The jobs this suite can drive, each with what it published - minus any whose
/ driver threw, which cannot be held to a contract it never reached.
runs:{[]
    `.jobouttest.unimplemented set ();
    js:publishing[] where can_drive each publishing[];
    (first each .jobouttest.unimplemented) _ js!drive each js}

/ Leave the jobs as .sjtest leaves them, not holding this suite's batches.
afterNamespace_reset_the_jobs:{[] .sjtest.reset[];}

/ --- the contract ---------------------------------------------------------

bad:()

test_every_publishing_job_can_be_driven:{[t]
    missing:publishing[] where not .jobouttest.can_drive each publishing[];
    .qunit.assertEquals[missing;`symbol$();
        "every job that subscribes and publishes has a driver - .<job>test.contract_driver in its own test file, or an entry in .jobouttest.drivers - without one, nothing checks what it sends the plant"]};

test_no_contract_driver_is_left_scaffolded:{[t]
    / The scaffolded driver throws, so this is the test a fresh `uqs new-job`
    / is meant to leave red. It replaces two errors that said `.
    runs[];
    / The job AND its error IN THE MESSAGE. assertEquals reports only its msg,
    / never the values it compared - which is why .xftest builds its detail
    / into the string too. With thirty publishing jobs, "a driver threw" sends
    / the reader looking; the scaffolded driver's own text names the file to
    / open and what to write in it.
    detail:", " sv {string[x 0],": ",x 1} each .jobouttest.unimplemented;
    .qunit.assertEquals[detail;"";
        "every declared contract_driver runs - a scaffolded one throws until it is written: ",detail]};

test_every_driver_names_a_publishing_job:{[t]
    / The other direction, so a renamed or retired job cannot leave a
    / driver behind that drives nothing.
    .qunit.assertEquals[(key drivers) except publishing[];`symbol$();
        "every driver drives a registered job that publishes"]};

test_every_declared_table_is_actually_published:{[t]
    / A driver that publishes nothing would pass the shape check below
    / vacuously, so each declared table must turn up at least once.
    r:runs[];
    excused:{[p] `$string[p 0],"/",string p 1} each not_built;
    `.jobouttest.bad set ();
    {[r;excused;j]
        seen:distinct exec tbl from r j;
        owed:((),.qstream.declaration[j]`publishes) except seen;
        owed:owed where not ({`$string[x],"/",string y}[j] each owed) in excused;
        if[count owed; `.jobouttest.bad set .jobouttest.bad,enlist string[j]," never published ",", " sv string owed]
      }[r;excused] each key r;
    .qunit.assertEquals[.jobouttest.bad;();
        "driven, every job publishes each table it declares"]};

test_every_publication_has_the_plant_shape:{[t]
    r:runs[];
    `.jobouttest.bad set ();
    {[r;j]
        {[j;tbl;cell]
            msg:$[tbl in key .jobouttest.unowned; "";
                  not tbl in key .jobouttest.shapes; "has no table in scripts/processes/uqs_tables.q";
                  .jobouttest.problem[tbl;cell]];
            if[count msg; `.jobouttest.bad set .jobouttest.bad,enlist string[j],"/",string[tbl],": ",msg]
          }[j]'[exec tbl from r j;first each exec rows from r j]
      }[r] each key r;
    .qunit.assertEquals[.jobouttest.bad;();
        "every batch a job publishes has its plant table's columns, in the plant's order, with the plant's types"]};

/ --- the check must fail on what it exists for ---------------------------

/ One fx_position row as fx_positions builds it.
a_position:{[]
    ([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot;
        base_qty:enlist 6e5; quote_qty:enlist -651000f; fill_count:enlist 2; break_even:enlist 1.085)}

test_the_right_shape_passes:{[t]
    .qunit.assertEquals[problem[`fx_position;a_position[]];"";
        "the published columns fx_positions builds are accepted"]};

test_two_swapped_columns_of_one_type_are_caught:{[t]
    / The positional failure this suite exists for: both floats, so the
    / plant would take them without complaint.
    swapped:`sym`book`product`quote_qty`base_qty`fill_count`break_even xcols a_position[];
    .qunit.assertTrue[0<count problem[`fx_position;swapped];
        "quote_qty ahead of base_qty is refused by name"]};

test_a_wrong_type_is_caught:{[t]
    wrong:update fill_count:`float$fill_count from a_position[];
    .qunit.assertTrue[(problem[`fx_position;wrong]) like "types differ on fill_count*";
        "a float where the plant keeps a long is named"]};

test_a_column_list_is_held_to_count_and_types:{[t]
    / How the feeds publish: no names, so only what the vectors carry.
    ok:value flip a_position[];
    .qunit.assertEquals[problem[`fx_position;ok];"";"the same row as columns passes"];
    .qunit.assertTrue[0<count problem[`fx_position;-1_ok];"a missing column is caught"];
    .qunit.assertTrue[0<count problem[`fx_position;@[ok;6;`long$]];"a long where the plant keeps a float is caught"]};

test_the_excuses_still_describe_something_real:{[t]
    / So neither list can rot into cover for the next real gap.
    stale:not_built where not {[p] p[1] in (),.qstream.declaration[p 0]`publishes} each not_built;
    .qunit.assertEquals[count stale;0;"every not_built pair is still declared by its job"];
    owners:raze {[j] (),.qstream.declaration[j]`publishes} each publishing[];
    .qunit.assertEquals[(key unowned) except owners;`symbol$();
        "every unowned table is still published by some job"];
    .qunit.assertEquals[(key unowned) inter key shapes;`symbol$();
        "and none has since gained a table in uqs_tables.q, which would make the excuse wrong"]};

\d .
