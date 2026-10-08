/ test_io_manager.q - where a worker's output goes (.iotest).
/ .
/ The seam replaces four hardcoded lines
/ in .qetl.job.bounded.publish, so the first thing these prove is that the DEFAULT
/ behaviour is unchanged - a seam that quietly altered where every existing
/ worker writes would be a worse problem than the one it solves.

\d .iotest

setUp_clean:{[]
    if[`iotgt in tables `.; delete iotgt from `.];
    `.qetl.io.touched set 0#.qetl.io.touched;
    `.qetl.io.default set .qetl.io.memory;
    }

batch:{[] ([] a:1 2 3j; b:`x`y`z)}

/ --- the managers --------------------------------------------------------

test_memory_creates_the_table_from_the_batch:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;3;
        "the default manager creates the target from the batch's own shape"]};

test_memory_appends_rather_than_replaces:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qetl.io.write[.qetl.io.memory;`iotgt;batch[]];
    .qunit.assertEquals[count value `iotgt;6;
        "a second write appends - coverage records windows, so publication must accumulate"]};

test_memory_reports_the_rows_written:{[t]
    .qunit.assertEquals[.qetl.io.write[.qetl.io.memory;`iotgt;batch[]];3;
        "the manager reports what it wrote, which is what finish_window records"]};

test_discard_writes_nothing:{[t]
    .qetl.io.write[.qetl.io.discard;`iotgt;batch[]];
    .qunit.assertEquals[`iotgt in tables `.;0b;
        "discard does not create the target at all"]};

test_discard_still_reports_the_count:{[t]
    / It must report honestly, or a pipeline running on discard would record
    / zero rows against a window that really had three - which is a worse
    / lie than not writing them.
    .qunit.assertEquals[.qetl.io.write[.qetl.io.discard;`iotgt;batch[]];3;
        "discard reports what it would have written"]};

/ --- validation ----------------------------------------------------------

test_a_manager_must_be_a_dictionary:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};42;
        "a manager that is not a dictionary is refused"]};

test_a_manager_missing_write_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `read)!enlist {[t;b] b};
        "a manager without a write is refused, naming the missing key"]};

test_a_manager_whose_write_is_not_a_function_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `write)!enlist 42;
        "a write that is not callable is refused at declaration, not at first use"]};

/ --- resolution ----------------------------------------------------------

test_a_config_without_io_gets_memory:{[t]
    / The property that makes this seam safe to introduce: a declaration
    / saying nothing about io behaves exactly as it did before the seam
    / existed.
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.memory;
        "a worker that declares no manager gets the in-memory default"]};

test_a_declared_manager_is_used:{[t]
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.discard);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.discard;
        "a declared manager is returned rather than the default"]};

test_a_malformed_declared_manager_is_refused:{[t]
    .qunit.assertError[{.qetl.io.for_cfg x};`source`dataset`width`io!(`s;`d;1D;42);
        "a malformed manager is refused when resolved, not when first written through"]};

/ --- the process default ---------------------------------------------------

test_a_config_without_io_gets_the_process_default:{[t]
    / What torq_backfill.q relies on: it sets the default, and every worker
    / that declares no manager of its own follows it.
    `.qetl.io.default set .qetl.io.discard;
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.discard;
        "a worker that declares no manager gets whatever the process chose"]};

test_a_declared_manager_beats_the_process_default:{[t]
    `.qetl.io.default set .qetl.io.discard;
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.memory);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.memory;"the worker's own choice wins"]};

test_finish_is_a_no_op_for_a_manager_without_one:{[t]
    .qunit.assertEquals[.qetl.io.finish .qetl.io.memory;::;"memory has nothing to do at the end of a run"]};

test_a_finish_that_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[{.qetl.io.require_manager x};`write`finish!({[t;b] count b};42);
        "require_manager: an io manager's finish must be a niladic function";
        "a malformed finish is refused at declaration, like a malformed write"]};

/ --- the HDB writer -------------------------------------------------------

/ A fresh, empty HDB directory for one test.
hdb_dir:{[] `$":",first system"mktemp -d"}

/ One partition's table, read back from disk.
part:{[root;d;t] get hsym `$(string .Q.par[root;d;t]),"/"}

/ Three deals over two past days, deliberately out of sym and time order.
deals:{[] ([] deal_time:2026.01.02D10:00:00.000000000 2026.01.02D09:00:00.000000000 2026.01.03D11:00:00.000000000;
    sym:`GBPUSD`EURUSD`EURUSD; notional:1e6 2e6 3e6)}

test_hdb_writes_each_row_into_its_own_date:{[t]
    root:hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;deals[]];
    .qunit.assertEquals[(count part[root;2026.01.02;`iodeals];count part[root;2026.01.03;`iodeals]);2 1;
        "two deals on the 2nd, one on the 3rd - each in the partition of the day it happened"]};

test_hdb_gives_the_rows_a_time_from_the_partition_column:{[t]
    root:hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;deals[]];
    p:part[root;2026.01.03;`iodeals];
    .qunit.assertEquals[(cols p;first p`time);(`time`deal_time`sym`notional;2026.01.03D11:00:00.000000000);
        "time leads, as on every plant table, and is the deal's own time"]};

test_hdb_partitions_by_time_when_the_batch_has_one:{[t]
    root:hdb_dir[];
    b:([] time:enlist 2026.01.05D12:00:00.000000000; sym:enlist `EURUSD; px:enlist 1.1);
    .qetl.io.write[.qetl.io.hdb[root;`not_a_column];`iobook;b];
    .qunit.assertEquals[count part[root;2026.01.05;`iobook];1;
        "a batch that already carries time is partitioned by it"]};

test_hdb_appends_a_second_window:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.write[m;`iodeals;deals[]];
    .qunit.assertEquals[count part[root;2026.01.02;`iodeals];4;"windows accumulate in the partition"]};

test_hdb_refuses_today:{[t]
    root:hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "hdb: iodeals has rows dated *";
        "today's partition belongs to the tickerplant and end-of-day"]};

test_the_refusal_of_today_says_what_to_do_instead:{[t]
    / The boundary is right - today is the plant's, earlier is the
    / backfill's, end-of-day is the handover - but it is met after the fetch
    / and the quality gate have both passed, which is a confusing place to
    / learn a policy from a message that only says no.
    root:hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "*Backfill a range ending on or before ",string[.z.d-1],", or let today's rows arrive through the live path";
        "names the remedy and the latest date that would work, not only the refusal"]};

test_hdb_refuses_a_batch_with_nothing_to_partition_by:{[t]
    root:hdb_dir[];
    / Two named parameters, so {...}[root] is a projection: a lambda reading
    / only x is monadic, and {...}[root] would CALL it here, outside the assert.
    .qunit.assertThrows[{[r;ignored] .qetl.io.write[.qetl.io.hdb[r;`deal_time];`iodeals;([] sym:enlist `EURUSD)]}[root];::;
        "hdb: iodeals's batch has neither time nor deal_time to partition by";
        "no time column, no partition"]};

test_hdb_finish_sorts_and_attributes:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;p`notional;attr p`sym);(`EURUSD`GBPUSD;2e6 1e6;`p);
        "sorted by sym then time, with p#sym, as an HDB partition is expected to be"]};

/ --- recovering what an interrupted run left unfinished ----------------

/ What a killed process leaves: partitions written, and the in-memory list of
/ what to finish gone with it. Emptying `touched` is that, without the kill.
forget:{[] `.qetl.io.touched set 0#.qetl.io.touched}

test_a_written_partition_is_unfinished_and_a_finished_one_is_not:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iodeals];"no partition at all has nothing to finish"];
    .qetl.io.write[m;`iodeals;deals[]];
    .qunit.assertTrue[not .qetl.io.is_finished[root;2026.01.02;`iodeals];"written and not finished"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iodeals];"finished: p#sym"]};

test_a_table_without_sym_is_judged_by_its_sorted_time:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iobare;delete sym from deals[]];
    .qunit.assertTrue[not .qetl.io.is_finished[root;2026.01.02;`iobare];"written, unsorted"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iobare];"xasc left s# on time"]};

test_recover_finds_what_a_lost_process_wrote_and_finish_repairs_it:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    forget[];
    .qunit.assertEquals[.qetl.io.finish m;0;"with the list gone, finish alone has nothing to do - the bug"];
    n:.qetl.io.recover[m;`iodeals;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];
    .qunit.assertEquals[n;2;"both written days are found from the files"];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`EURUSD`GBPUSD;`p);"sorted and p#sym again"]};

test_recover_looks_only_inside_the_range:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    forget[];
    .qunit.assertEquals[.qetl.io.recover[m;`iodeals;2026.01.03D00:00:00.000000000;2026.01.04D00:00:00.000000000];1;
        "only the day inside [from;to), and the end is exclusive"]};

test_recover_leaves_finished_partitions_alone:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    .qunit.assertEquals[.qetl.io.recover[m;`iodeals;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];0;
        "nothing to repair, so nothing is re-sorted"]};

test_a_manager_without_recover_recovers_nothing:{[t]
    .qunit.assertEquals[.qetl.io.recover[.qetl.io.memory;`iotgt;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];0;
        "memory has nothing that can be left unfinished"]};

test_hdb_finish_fills_a_partition_missing_a_table:{[t]
    / The most recent partition holds both tables, so .Q.chk has a template.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.write[m;`iotrades;([] deal_time:enlist 2026.01.03D12:00:00.000000000; sym:enlist `EURUSD)];
    .qetl.io.finish m;
    .qunit.assertEquals[count part[root;2026.01.02;`iotrades];0;
        "the 2nd gets an empty iotrades, so a query across both days can run"]};

test_hdb_appends_to_a_finished_partition:{[t]
    / A second run into a date the first one finished: the p# the first
    / finish applied must not stop the append, and the second finish sorts
    / the whole partition again.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    .qetl.io.write[m;`iodeals;([] deal_time:enlist 2026.01.02D08:00:00.000000000; sym:enlist `AUDUSD; notional:enlist 5e6)];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`AUDUSD`EURUSD`GBPUSD;`p);
        "three deals, sorted again, attribute restored"]};

test_hdb_finish_forgets_what_it_finished:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    .qunit.assertEquals[.qetl.io.finish m;0;"a second finish has nothing left to do"]};

/ --- flush: finishing a run's days as it moves past them -----------------

test_flush_finishes_only_the_days_wholly_behind_it:{[t]
    / deals[] spans the 2nd and the 3rd. A window ending at midnight on the
    / 3rd puts the 2nd behind the run - nothing later writes to it - and the
    / 3rd still open.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    r:.qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[r;`finished`pending!1 1;"the 2nd is finished, the 3rd still open"];
    .qunit.assertEquals[(attr part[root;2026.01.02;`iodeals]`sym;attr part[root;2026.01.03;`iodeals]`sym);(`p;`);
        "only the finished day is sorted and given p#sym"]};

test_finish_after_flush_does_only_what_flush_left:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[.qetl.io.finish m;1;"the 2nd was finished already; only the 3rd is left"];
    .qunit.assertEquals[attr part[root;2026.01.03;`iodeals]`sym;`p;"and the 3rd is finished now"]};

test_on_ready_hears_every_flush_and_the_finish:{[t]
    root:hdb_dir[];
    `.iotest.heard set ();
    m:.qetl.io.hdb[root;`deal_time],enlist[`on_ready]!enlist {[s] `.iotest.heard set .iotest.heard,enlist s};
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qetl.io.finish m;
    .qunit.assertEquals[.iotest.heard;
        (`finished`pending`final!(1;1;0b);`finished`pending`final!(1;0;1b));
        "what finished, what is still open, and whether the run is over"]};

test_a_failing_on_ready_does_not_fail_the_flush:{[t]
    / The rows are on disk either way; a reload that failed is no reason to
    / fail the run that wrote them.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time],enlist[`on_ready]!enlist {[s] '"reload refused"};
    .qetl.io.write[m;`iodeals;deals[]];
    .qunit.assertEquals[.qetl.io.flush[m;2026.01.03D00:00:00.000000000];`finished`pending!1 1;
        "flush still reports what it finished"]};

test_flush_is_a_no_op_for_a_manager_without_one:{[t]
    .qunit.assertEquals[.qetl.io.flush[.qetl.io.memory;2026.01.03D00:00:00.000000000];`finished`pending!0 0;
        "memory finishes nothing early and has nothing open"]};

test_a_flush_that_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[{.qetl.io.require_manager x};`write`flush!({[t;b] count b};42);
        "require_manager: an io manager's flush must be*";"flush must be callable"]};

/ --- due: whether a deployment should expose finished work now -----------

dueat:{[dirty;last_at;finished;pending;final]
    .qetl.io.due[`dirty`last!(dirty;last_at);`finished`pending`final!(finished;pending;final);
        2026.01.02D12:00:00.000000000;0D00:00:30]}

test_due_acts_when_something_finished_and_nothing_is_open:{[t]
    d:.iotest.dueat[0b;0Np;1;0;0b];
    .qunit.assertEquals[(d`act;d`state);(1b;`dirty`last!(0b;2026.01.02D12:00:00.000000000));
        "act, and remember when"]};

test_due_waits_while_a_part_is_open:{[t]
    / The HDB would map a partition still being appended to, unsorted.
    d:.iotest.dueat[0b;0Np;1;1;0b];
    .qunit.assertEquals[(d`act;d[`state]`dirty);(0b;1b);"no - but what finished is still owed"]};

test_due_does_nothing_when_nothing_is_new:{[t]
    .qunit.assertEquals[.iotest.dueat[0b;0Np;0;0;1b]`act;0b;"even at the end, nothing new is nothing to do"]};

test_due_holds_back_within_the_interval_mid_run:{[t]
    d:.iotest.dueat[0b;2026.01.02D11:59:50.000000000;1;0;0b];
    .qunit.assertEquals[(d`act;d[`state]`dirty);(0b;1b);"ten seconds after the last one: not yet, still owed"]};

test_due_acts_after_the_interval:{[t]
    .qunit.assertEquals[.iotest.dueat[1b;2026.01.02D11:59:00.000000000;0;0;0b]`act;1b;
        "a minute later, what was owed is shown"]};

test_the_final_call_ignores_the_interval:{[t]
    .qunit.assertEquals[.iotest.dueat[1b;2026.01.02D11:59:59.000000000;0;0;1b]`act;1b;
        "the run's end never leaves finished work unshown"]};

test_hdb_refuses_a_root_that_is_not_a_file_symbol:{[t]
    .qunit.assertThrows[{.qetl.io.hdb[x;`deal_time]};`plain;
        "hdb: root must be a file symbol*";"a bare symbol is not a directory"]};

/ --- on_conflict: what a write does with a row already there -------------

/ Two rows held, two arriving; id 2 is in both.
held:{[] ([] id:1 2; v:10 20; time:2026.01.02D01:00:00.000000000 2026.01.02D02:00:00.000000000)}
incoming:{[] ([] id:2 3; v:21 30; time:2026.01.02D02:30:00.000000000 2026.01.02D03:00:00.000000000)}
opts:{[] `row_key`target`time_column`range_from`range_to!(`id;`t;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000)}

test_upsert_replaces_a_key_already_there_and_adds_the_rest:{[t]
    r:.qetl.io.resolve[`upsert;.iotest.held[];.iotest.incoming[];.iotest.opts[]];
    .qunit.assertEquals[(r`id;r`v);(1 2 3;10 21 30);"id 2 takes the incoming value, once"]};

test_ignore_keeps_the_row_already_there:{[t]
    r:.qetl.io.resolve[`ignore;.iotest.held[];.iotest.incoming[];.iotest.opts[]];
    .qunit.assertEquals[(r`id;r`v);(1 2 3;10 20 30);"id 2 keeps 20; only id 3 is new"]};

test_append_keeps_both:{[t]
    r:.qetl.io.resolve[`append;.iotest.held[];.iotest.incoming[];.iotest.opts[]];
    .qunit.assertEquals[r`id;1 2 2 3;"no check - the behaviour before on_conflict existed"]};

test_fail_refuses_naming_the_clash:{[t]
    .qunit.assertThrows[{[x] .qetl.io.resolve[`fail;.iotest.held[];.iotest.incoming[];.iotest.opts[]]};::;
        "on_conflict fail: 1 row(s) of t already there by id";"one clash, named, nothing resolved"]};

test_fail_writes_when_nothing_clashes:{[t]
    r:.qetl.io.resolve[`fail;.iotest.held[];1#.iotest.incoming[] where 3=.iotest.incoming[]`id;.iotest.opts[]];
    .qunit.assertEquals[r`id;1 2 3;"fail only refuses a clash"]};

test_replace_drops_what_the_window_held_and_the_batch_left_out:{[t]
    / The window is the whole of the 2nd: id 1 is inside it and not in the
    / batch, so the source has withdrawn it.
    r:.qetl.io.resolve[`replace;.iotest.held[];.iotest.incoming[];.iotest.opts[]];
    .qunit.assertEquals[(r`id;r`v);(2 3;21 30);"only what the source now says"]};

test_replace_keeps_what_lies_outside_the_window:{[t]
    o:@[.iotest.opts[];`range_from;:;2026.01.02D02:00:00.000000000];
    r:.qetl.io.resolve[`replace;.iotest.held[];.iotest.incoming[];o];
    .qunit.assertEquals[r`id;1 2 3;"id 1 at 01:00 is before the window, so it stays"]};

test_a_batch_that_repeats_a_key_writes_it_once:{[t]
    b:([] id:3 3; v:30 31; time:2#2026.01.02D03:00:00.000000000);
    .qunit.assertEquals[exec v from .qetl.io.resolve[`upsert;.iotest.held[];b;.iotest.opts[]] where id=3;enlist 31;
        "upsert: the last of the batch wins"];
    .qunit.assertEquals[exec v from .qetl.io.resolve[`ignore;.iotest.held[];b;.iotest.opts[]] where id=3;enlist 30;
        "ignore: the first does"]};

test_a_batch_with_other_columns_is_refused:{[t]
    .qunit.assertThrows[{[x] .qetl.io.resolve[`upsert;.iotest.held[];([] id:enlist 9);.iotest.opts[]]};::;
        "on_conflict: t holds *";"columns that differ are named, not joined"]};

test_an_unknown_strategy_is_refused_naming_the_ones_there_are:{[t]
    .qunit.assertThrows[{.qetl.io.require_strategy x};`merge;
        "on_conflict must be one of append, fail, ignore, replace, upsert - not merge";"a typo names the alternatives"]};

test_memory_upserts_rather_than_duplicating:{[t]
    o:`on_conflict`row_key!(`upsert;`a);
    .qetl.io.write_keyed[.qetl.io.memory;`iotgt;batch[];o];
    .qetl.io.write_keyed[.qetl.io.memory;`iotgt;batch[];o];
    .qunit.assertEquals[count value `iotgt;3;"the same three rows twice is still three rows"]};

test_a_manager_that_can_only_append_refuses_any_other_strategy:{[t]
    / Rather than append quietly - the duplication this exists to stop.
    mgr:(enlist `write)!enlist {[target;b] count b};
    .qunit.assertThrows[{[m] .qetl.io.write_keyed[m;`iotgt;batch[];`on_conflict`row_key!(`upsert;`a)]};mgr;
        "write_keyed: this io manager can only append*";"refused, not appended"];
    .qunit.assertEquals[.qetl.io.write_keyed[mgr;`iotgt;batch[];`on_conflict`row_key!(`append;`a)];3;
        "append is what it can do"]};

/ The HDB writer, keyed: deals[] on the 2nd and 3rd, then written again.
hdb_opts:{[s] `on_conflict`row_key`time_column`range_from`range_to!(s;`sym`deal_time;`deal_time;2026.01.02D00:00:00.000000000;2026.01.04D00:00:00.000000000)}

test_hdb_upsert_writes_a_window_twice_without_duplicating:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qetl.io.write_keyed[m;`iodeals;update notional:9e6 from deals[];hdb_opts`upsert];
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(count p;asc p`notional);(2;9e6 9e6);"two deals on the 2nd, each restated, none doubled"]};

test_hdb_append_still_duplicates:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`append];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`append];
    .qunit.assertEquals[count part[root;2026.01.02;`iodeals];4;"append is the old path, unchanged"]};

test_hdb_ignore_keeps_the_first_write:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`ignore];
    .qetl.io.write_keyed[m;`iodeals;update notional:9e6 from deals[];hdb_opts`ignore];
    .qunit.assertEquals[asc part[root;2026.01.02;`iodeals]`notional;1e6 2e6;"the rows already there stay"]};

test_hdb_replace_clears_a_day_the_source_has_emptied:{[t]
    / The second write returns only the 3rd's deal: the 2nd, inside the
    / window and now empty upstream, must lose what it held.
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qetl.io.write_keyed[m;`iodeals;select from deals[] where deal_time>2026.01.03D00:00:00.000000000;hdb_opts`replace];
    .qunit.assertEquals[(count part[root;2026.01.02;`iodeals];count part[root;2026.01.03;`iodeals]);0 1;
        "the emptied day is empty, the other day keeps its deal"]};

test_hdb_fail_writes_nothing_when_a_key_clashes:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qunit.assertThrows[{[r;ignored] .qetl.io.write_keyed[.qetl.io.hdb[r;`deal_time];`iodeals;deals[];.iotest.hdb_opts`fail]}[root];::;
        "on_conflict fail: *";"the clash is refused"];
    .qunit.assertEquals[count part[root;2026.01.02;`iodeals];2;"and nothing was written - the 2nd still holds two"]};

test_hdb_keyed_writes_are_finished_like_any_other:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`EURUSD`GBPUSD;`p);"sorted and p#sym, as an appended partition is"]};

/ --- staged keyed writes and their recovery ------------------------------

test_a_keyed_write_leaves_nothing_in_staging:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    s:.qetl.io.staging root;
    left:{[s;kind] $[()~key hsym `$s,"/",kind; 0; sum {[s;kind;d] count key hsym `$s,"/",kind,"/",string d}[s;kind] each key hsym `$s,"/",kind]}[s] each ("new";"old");
    .qunit.assertEquals[(left;count part[root;2026.01.02;`iodeals]);(0 0;2);
        "the partition is written and the staging area is empty again"]};

/ A kill between the two renames: the live table moved to `old`, its
/ replacement never moved in. recover puts the old one back.
test_recover_restores_a_table_swapped_out_and_never_replaced:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qetl.io.finish m;
    before:part[root;2026.01.02;`iodeals];
    old:.qetl.io.staged[root;`old;2026.01.02;`iodeals];
    system"mkdir -p ",(.qetl.io.staging root),"/old/2026.01.02";
    system"mv ",.qetl.io.part_path[root;2026.01.02;`iodeals]," ",old;
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[(part[root;2026.01.02;`iodeals];()~key hsym `$old);(before;1b);
        "the partition reads as it did before the write, and old is gone"]};

/ A kill after staging and before any swap: the staged copy is a write that
/ never happened, and the live partition was never touched.
test_recover_discards_a_staged_table_that_never_reached_its_swap:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    before:part[root;2026.01.02;`iodeals];
    .qetl.io.stage[root;2026.01.02;`iodeals;0#before];
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[(count part[root;2026.01.02;`iodeals];()~key hsym `$.qetl.io.staged[root;`new;2026.01.02;`iodeals]);(2;1b);
        "the live partition keeps its rows and the staged copy is removed"]};

/ Another table's staging is another process's write in flight.
test_recover_leaves_another_tables_staging_alone:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;deals[];hdb_opts`upsert];
    .qetl.io.stage[root;2026.01.02;`other;0#part[root;2026.01.02;`iodeals]];
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertTrue[not ()~key hsym `$.qetl.io.staged[root;`new;2026.01.02;`other];"untouched"]};

/ A kill part way through an APPEND, which writes column by column: one
/ column one row longer than the rest. recover trims it back and queues the
/ partition for finishing.
test_recover_trims_a_torn_append:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    .qetl.io.finish m;
    f:hsym `$.qetl.io.part_path[root;2026.01.02;`iodeals],"/notional";
    f set (get f),9e9;
    n:.qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qetl.io.finish m;
    p:part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(n;count p;asc p`notional;attr p`sym);(1;2;1e6 2e6;`p);
        "every column ends at the same row again, and the partition is finished"]};

/ --- the wiring ----------------------------------------------------------

test_define_refuses_a_malformed_manager:{[t]
    / At DEFINE time, not at first write. A worker with a broken manager
    / should fail before it has fetched a window it cannot store.
    .qunit.assertError[{.qetl.job.bounded.define[`io_broken;x]};
        `source`dataset`width`transform`io!(`demo_deals;`io_broken_ds;1D;`demo_deals_passthrough;42);
        "a malformed io manager stops the worker at declaration"]};

test_a_custom_manager_receives_the_target_and_batch:{[t]
    / Proves the seam actually carries the worker's own target through,
    / rather than the manager being called with something else.
    `iocapture set ();
    mgr:(enlist `write)!enlist {[target;b] `iocapture set (target;count b); count b};
    .qetl.io.write[mgr;`some_target;batch[]];
    .qunit.assertEquals[value `iocapture;(`some_target;3);
        "the manager is handed the declared target and the batch"]};

/ --- the registry's shape, which this work exposed ------------------------

test_a_worker_is_not_handed_a_key_it_never_declared:{[t]
    / `config` is a dict of dicts, and q coerces same-keyed dicts into a
    / TABLE. That was happening by accident: demo_events_backfill declares no
    / `check`, but once demo_deals_backfill declared one, the coerced table
    / gave demo_events a `check` column too - silently, with a value it never
    / chose. Normalising at registration makes the shape intended, and this
    / pins the property that matters: an undeclared optional is (::), not
    / another worker's value.
    d:.qetl.job.bounded.def `demo_events_backfill;
    .qunit.assertEquals[d`check;(::);
        "a worker that declares no check has a null one, not a neighbour's"]};

test_a_worker_declaring_a_new_optional_key_can_register:{[t]
    / Before normalisation this threw 'mismatch: a table cannot gain a column
    / by assignment, so the FIRST worker to declare a key no earlier worker
    / had could not be registered at all. Registration order silently decided
    / which declarations were legal.
    .qunit.assertEquals[.qetl.job.bounded.define[`io_newkey;
        `source`dataset`width`transform`io!(`demo_deals;`io_newkey_ds;1D;`demo_deals_passthrough;.qetl.io.discard)];
        `io_newkey;
        "a worker declaring an optional key registers regardless of order"]};

test_every_registered_config_has_the_same_keys:{[t]
    / The registry is a declared keyed table (#512), so its columns are one
    / key set by construction; what this holds is that def hands every worker
    / that one set, optional keys and all.
    ks:{key .qetl.job.bounded.def x} each .qetl.job.bounded.defined[];
    .qunit.assertEquals[count distinct ks;1;
        "every config carries the same key set, so the registry's shape is stable"]};


/ --- the PeachQ path's two readers, run here on KDB-X ---------------------
/ .
/ On PeachQ an append rewrites a partition whole from read_part, and a
/ partition counts as finished when in_order says so. Neither runs on KDB-X
/ in the course of a write, so they are asked directly: both only read.

test_read_part_returns_a_partition_with_its_symbols_decoded:{[t]
    root:hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;deals[]];
    p:.qetl.io.read_part hsym `$(string .Q.par[root;2026.01.02;`iodeals]),"/";
    .qunit.assertEquals[(type p`sym;asc p`sym);(11h;`EURUSD`GBPUSD);
        "symbols as symbols, not the sym file's enumeration - what a whole rewrite appends to"];
    .qunit.assertEquals[cols p;cols part[root;2026.01.02;`iodeals];"every column the partition has"]};

test_in_order_is_finish_s_order_sym_then_time:{[t]
    root:hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;deals[]];
    base:string .Q.par[root;2026.01.02;`iodeals];
    c:get hsym `$base,"/.d";
    .qunit.assertTrue[not .qetl.io.in_order[base;c];"written in arrival order, GBPUSD first"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.in_order[base;c];"finished: sorted by sym, then time"];
    .qunit.assertTrue[.qetl.io.in_order[base;`notional];"no sym and no time: nothing to be out of order"]};

\d .
