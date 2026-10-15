/ test_io_manager.q - where a worker's output goes (.qt.qetl.io).
/ .
/ The seam replaces four hardcoded lines
/ in .qetl.job.bounded.publish, so the first thing these prove is that the DEFAULT
/ behaviour is unchanged - a seam that quietly altered where every existing
/ worker writes would be a worse problem than the one it solves.


.qt.qetl.io.setUp_clean:{[]
    if[`iotgt in tables `.; delete iotgt from `.];
    `.qetl.io.touched set 0#.qetl.io.touched;
    `.qetl.io.default set .qetl.io.memory;
    }

.qt.qetl.io.batch:{[] ([] a:1 2 3j; b:`x`y`z)}

/ --- the managers --------------------------------------------------------

.qt.qetl.io.test_memory_creates_the_table_from_the_batch:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[]];
    .qunit.assertEquals[count value `iotgt;3;
        "the default manager creates the target from the batch's own shape"]};

.qt.qetl.io.test_memory_appends_rather_than_replaces:{[t]
    .qetl.io.write[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[]];
    .qetl.io.write[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[]];
    .qunit.assertEquals[count value `iotgt;6;
        "a second write appends - coverage records windows, so publication must accumulate"]};

.qt.qetl.io.test_memory_reports_the_rows_written:{[t]
    .qunit.assertEquals[.qetl.io.write[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[]];3;
        "the manager reports what it wrote, which is what finish_window records"]};

.qt.qetl.io.test_discard_writes_nothing:{[t]
    .qetl.io.write[.qetl.io.discard;`iotgt;.qt.qetl.io.batch[]];
    .qunit.assertEquals[`iotgt in tables `.;0b;
        "discard does not create the target at all"]};

.qt.qetl.io.test_discard_still_reports_the_count:{[t]
    / It must report honestly, or a pipeline running on discard would record
    / zero rows against a window that really had three - which is a worse
    / lie than not writing them.
    .qunit.assertEquals[.qetl.io.write[.qetl.io.discard;`iotgt;.qt.qetl.io.batch[]];3;
        "discard reports what it would have written"]};

/ --- validation ----------------------------------------------------------

.qt.qetl.io.test_a_manager_must_be_a_dictionary:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};42;
        "a manager that is not a dictionary is refused"]};

.qt.qetl.io.test_a_manager_missing_write_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `read)!enlist {[t;b] b};
        "a manager without a write is refused, naming the missing key"]};

.qt.qetl.io.test_a_manager_whose_write_is_not_a_function_is_refused:{[t]
    .qunit.assertError[{.qetl.io.require_manager x};(enlist `write)!enlist 42;
        "a write that is not callable is refused at declaration, not at first use"]};

/ --- resolution ----------------------------------------------------------

.qt.qetl.io.test_a_config_without_io_gets_memory:{[t]
    / The property that makes this seam safe to introduce: a declaration
    / saying nothing about io behaves exactly as it did before the seam
    / existed.
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.memory;
        "a worker that declares no manager gets the in-memory default"]};

.qt.qetl.io.test_a_declared_manager_is_used:{[t]
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.discard);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.discard;
        "a declared manager is returned rather than the default"]};

.qt.qetl.io.test_a_malformed_declared_manager_is_refused:{[t]
    .qunit.assertError[{.qetl.io.for_cfg x};`source`dataset`width`io!(`s;`d;1D;42);
        "a malformed manager is refused when resolved, not when first written through"]};

/ --- the process default ---------------------------------------------------

.qt.qetl.io.test_a_config_without_io_gets_the_process_default:{[t]
    / What torq_backfill.q relies on: it sets the default, and every worker
    / that declares no manager of its own follows it.
    `.qetl.io.default set .qetl.io.discard;
    .qunit.assertEquals[.qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)];.qetl.io.discard;
        "a worker that declares no manager gets whatever the process chose"]};

.qt.qetl.io.test_a_declared_manager_beats_the_process_default:{[t]
    `.qetl.io.default set .qetl.io.discard;
    cfg:`source`dataset`width`io!(`s;`d;1D;.qetl.io.memory);
    .qunit.assertEquals[.qetl.io.for_cfg cfg;.qetl.io.memory;"the worker's own choice wins"]};

.qt.qetl.io.test_finish_is_a_no_op_for_a_manager_without_one:{[t]
    .qunit.assertEquals[.qetl.io.finish .qetl.io.memory;::;"memory has nothing to do at the end of a run"]};

.qt.qetl.io.test_a_finish_that_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[{.qetl.io.require_manager x};`write`finish!({[t;b] count b};42);
        "require_manager: an io manager's finish must be a niladic function";
        "a malformed finish is refused at declaration, like a malformed write"]};

/ --- the HDB writer -------------------------------------------------------

/ A fresh, empty HDB directory for one test.
.qt.qetl.io.hdb_dir:{[] `$":",first system"mktemp -d"}

/ One partition's table, read back from disk.
.qt.qetl.io.part:{[root;d;t] get hsym `$(string .Q.par[root;d;t]),"/"}

/ Three deals over two past days, deliberately out of sym and time order.
.qt.qetl.io.deals:{[] ([] deal_time:2026.01.02D10:00:00.000000000 2026.01.02D09:00:00.000000000 2026.01.03D11:00:00.000000000;
    sym:`GBPUSD`EURUSD`EURUSD; notional:1e6 2e6 3e6)}

.qt.qetl.io.test_hdb_writes_each_row_into_its_own_date:{[t]
    root:.qt.qetl.io.hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;.qt.qetl.io.deals[]];
    .qunit.assertEquals[(count .qt.qetl.io.part[root;2026.01.02;`iodeals];count .qt.qetl.io.part[root;2026.01.03;`iodeals]);2 1;
        "two deals on the 2nd, one on the 3rd - each in the partition of the day it happened"]};

/ #1083: every writer to one root holds its lock for the call, so backfills
/ partitioned over one dataset queue rather than swap over each other.
.qt.qetl.io.hold_root:{[root]
    path:.qetl.io.hdb_lock root;
    system"mkdir -p ",path;
    .qetl.job.bounded.state.write_owner path;
    path}

.qt.qetl.io.test_a_write_waits_for_another_writer_on_the_same_root:{[t]
    root:.qt.qetl.io.hdb_dir[];
    held:.qt.qetl.io.hold_root[root];
    saved:.qetl.io.hdb_lock_wait;
    `.qetl.io.hdb_lock_wait set 0D00:00:00.050;
    r:@[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;.qt.qetl.io.deals[]]; `wrote};root;{x}];
    `.qetl.io.hdb_lock_wait set saved;
    system"rm -rf ",held;
    / two wildcards at most: `like` throws 'nyi on three on KDB-X
    .qunit.assertTrue[r like "*could not take the lock at*";"refused while another live writer holds the root"];
    .qunit.assertTrue[0<count ss[r;(1_string root),".write.lock"];"naming the root's own lock"];
    .qunit.assertEquals[count key root;0;"and nothing was written meanwhile"]};

.qt.qetl.io.test_a_write_releases_the_root_lock_whether_it_succeeds_or_throws:{[t]
    root:.qt.qetl.io.hdb_dir[];
    path:.qetl.io.hdb_lock root;
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;.qt.qetl.io.deals[]];
    .qunit.assertEquals[()~key hsym `$path;1b;"released after a write"];
    @[{.qetl.io.write[.qetl.io.hdb[x;`no_such_col];`iobook;([] notional:enlist 1f)]};root;{x}];
    .qunit.assertEquals[()~key hsym `$path;1b;"and after one that threw"]};

.qt.qetl.io.test_two_roots_have_two_locks:{[t]
    .qunit.assertEquals[.qetl.io.hdb_lock each `:/data/hdb`:/data/hdb2;("/data/hdb.write.lock";"/data/hdb2.write.lock");
        "beside each root: one HDB's writers do not wait for another's"]};

/ #1168: one lock per root however it is spelled - a trailing slash put it
/ inside the root, and uqs data migrate passes the root absolute.
.qt.qetl.io.test_the_root_lock_is_beside_the_root_however_it_is_spelled:{[t]
    .qunit.assertEquals[.qetl.io.hdb_lock each `:/data/hdb/`:/data/hdb//;2#enlist "/data/hdb.write.lock";
        "a trailing slash names the same root"];
    .qunit.assertEquals[.qetl.io.hdb_lock `:data/hdb/;(system"cd"),"/data/hdb.write.lock";
        "a relative root is the one under this process's directory"]};

.qt.qetl.io.test_hdb_gives_the_rows_a_time_from_the_partition_column:{[t]
    root:.qt.qetl.io.hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;.qt.qetl.io.deals[]];
    p:.qt.qetl.io.part[root;2026.01.03;`iodeals];
    .qunit.assertEquals[(cols p;first p`time);(`time`deal_time`sym`notional;2026.01.03D11:00:00.000000000);
        "time leads, as on every plant table, and is the deal's own time"]};

.qt.qetl.io.test_hdb_partitions_by_time_when_the_batch_has_one:{[t]
    root:.qt.qetl.io.hdb_dir[];
    b:([] time:enlist 2026.01.05D12:00:00.000000000; sym:enlist `EURUSD; px:enlist 1.1);
    .qetl.io.write[.qetl.io.hdb[root;`not_a_column];`iobook;b];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.05;`iobook];1;
        "a batch that already carries time is partitioned by it"]};

.qt.qetl.io.test_hdb_appends_a_second_window:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.02;`iodeals];4;"windows accumulate in the partition"]};

.qt.qetl.io.test_hdb_refuses_today:{[t]
    root:.qt.qetl.io.hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "hdb: iodeals has rows dated *";
        "today's partition belongs to the tickerplant and end-of-day"]};

.qt.qetl.io.test_the_refusal_of_today_says_what_to_do_instead:{[t]
    / The boundary is right - today is the plant's, earlier is the
    / backfill's, end-of-day is the handover - but it is met after the fetch
    / and the quality gate have both passed, which is a confusing place to
    / learn a policy from a message that only says no.
    root:.qt.qetl.io.hdb_dir[];
    b:([] deal_time:enlist .z.p; sym:enlist `EURUSD; notional:enlist 1e6);
    .qunit.assertThrows[{.qetl.io.write[.qetl.io.hdb[x;`deal_time];`iodeals;y]}[root];b;
        "*Backfill a range ending on or before ",string[.z.d-1],", or let today's rows arrive through the live path";
        "names the remedy and the latest date that would work, not only the refusal"]};

.qt.qetl.io.test_hdb_refuses_a_batch_with_nothing_to_partition_by:{[t]
    root:.qt.qetl.io.hdb_dir[];
    / Two named parameters, so {...}[root] is a projection: a lambda reading
    / only x is monadic, and {...}[root] would CALL it here, outside the assert.
    .qunit.assertThrows[{[r;ignored] .qetl.io.write[.qetl.io.hdb[r;`deal_time];`iodeals;([] sym:enlist `EURUSD)]}[root];::;
        "hdb: iodeals's batch has neither time nor deal_time to partition by";
        "no time column, no partition"]};

.qt.qetl.io.test_hdb_finish_sorts_and_attributes:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.finish m;
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;p`notional;attr p`sym);(`EURUSD`GBPUSD;2e6 1e6;`p);
        "sorted by sym then time, with p#sym, as an HDB partition is expected to be"]};

/ --- recovering what an interrupted run left unfinished ----------------

/ What a killed process leaves: partitions written, and the in-memory list of
/ what to finish gone with it. Emptying `touched` is that, without the kill.
.qt.qetl.io.forget:{[] `.qetl.io.touched set 0#.qetl.io.touched}

.qt.qetl.io.test_a_written_partition_is_unfinished_and_a_finished_one_is_not:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iodeals];"no partition at all has nothing to finish"];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qunit.assertTrue[not .qetl.io.is_finished[root;2026.01.02;`iodeals];"written and not finished"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iodeals];"finished: p#sym"]};

.qt.qetl.io.test_a_table_without_sym_is_judged_by_its_sorted_time:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iobare;delete sym from .qt.qetl.io.deals[]];
    .qunit.assertTrue[not .qetl.io.is_finished[root;2026.01.02;`iobare];"written, unsorted"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.is_finished[root;2026.01.02;`iobare];"xasc left s# on time"]};

.qt.qetl.io.test_recover_finds_what_a_lost_process_wrote_and_finish_repairs_it:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qt.qetl.io.forget[];
    .qunit.assertEquals[.qetl.io.finish m;0;"with the list gone, finish alone has nothing to do - the bug"];
    n:.qetl.io.recover[m;`iodeals;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];
    .qunit.assertEquals[n;2;"both written days are found from the files"];
    .qetl.io.finish m;
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`EURUSD`GBPUSD;`p);"sorted and p#sym again"]};

.qt.qetl.io.test_recover_looks_only_inside_the_range:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qt.qetl.io.forget[];
    .qunit.assertEquals[.qetl.io.recover[m;`iodeals;2026.01.03D00:00:00.000000000;2026.01.04D00:00:00.000000000];1;
        "only the day inside [from;to), and the end is exclusive"]};

.qt.qetl.io.test_recover_leaves_finished_partitions_alone:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.finish m;
    .qunit.assertEquals[.qetl.io.recover[m;`iodeals;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];0;
        "nothing to repair, so nothing is re-sorted"]};

.qt.qetl.io.test_a_manager_without_recover_recovers_nothing:{[t]
    .qunit.assertEquals[.qetl.io.recover[.qetl.io.memory;`iotgt;2026.01.01D00:00:00.000000000;2026.01.05D00:00:00.000000000];0;
        "memory has nothing that can be left unfinished"]};

.qt.qetl.io.test_hdb_finish_fills_a_partition_missing_a_table:{[t]
    / The most recent partition holds both tables, so .Q.chk has a template.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.write[m;`iotrades;([] deal_time:enlist 2026.01.03D12:00:00.000000000; sym:enlist `EURUSD)];
    .qetl.io.finish m;
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.02;`iotrades];0;
        "the 2nd gets an empty iotrades, so a query across both days can run"]};

.qt.qetl.io.test_hdb_appends_to_a_finished_partition:{[t]
    / A second run into a date the first one finished: the p# the first
    / finish applied must not stop the append, and the second finish sorts
    / the whole partition again.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.finish m;
    .qetl.io.write[m;`iodeals;([] deal_time:enlist 2026.01.02D08:00:00.000000000; sym:enlist `AUDUSD; notional:enlist 5e6)];
    .qetl.io.finish m;
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`AUDUSD`EURUSD`GBPUSD;`p);
        "three deals, sorted again, attribute restored"]};

.qt.qetl.io.test_hdb_finish_forgets_what_it_finished:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.finish m;
    .qunit.assertEquals[.qetl.io.finish m;0;"a second finish has nothing left to do"]};

/ --- flush: finishing a run's days as it moves past them -----------------

.qt.qetl.io.test_flush_finishes_only_the_days_wholly_behind_it:{[t]
    / deals[] spans the 2nd and the 3rd. A window ending at midnight on the
    / 3rd puts the 2nd behind the run - nothing later writes to it - and the
    / 3rd still open.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    r:.qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[r;`finished`pending!1 1;"the 2nd is finished, the 3rd still open"];
    .qunit.assertEquals[(attr .qt.qetl.io.part[root;2026.01.02;`iodeals]`sym;attr .qt.qetl.io.part[root;2026.01.03;`iodeals]`sym);(`p;`);
        "only the finished day is sorted and given p#sym"]};

.qt.qetl.io.test_finish_after_flush_does_only_what_flush_left:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[.qetl.io.finish m;1;"the 2nd was finished already; only the 3rd is left"];
    .qunit.assertEquals[attr .qt.qetl.io.part[root;2026.01.03;`iodeals]`sym;`p;"and the 3rd is finished now"]};

.qt.qetl.io.test_on_ready_hears_every_flush_and_the_finish:{[t]
    root:.qt.qetl.io.hdb_dir[];
    `.qt.qetl.io.heard set ();
    m:.qetl.io.hdb[root;`deal_time],enlist[`on_ready]!enlist {[s] `.qt.qetl.io.heard set .qt.qetl.io.heard,enlist s};
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.flush[m;2026.01.03D00:00:00.000000000];
    .qetl.io.finish m;
    .qunit.assertEquals[.qt.qetl.io.heard;
        (`finished`pending`final!(1;1;0b);`finished`pending`final!(1;0;1b));
        "what finished, what is still open, and whether the run is over"]};

.qt.qetl.io.test_a_failing_on_ready_does_not_fail_the_flush:{[t]
    / The rows are on disk either way; a reload that failed is no reason to
    / fail the run that wrote them.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time],enlist[`on_ready]!enlist {[s] '"reload refused"};
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qunit.assertEquals[.qetl.io.flush[m;2026.01.03D00:00:00.000000000];`finished`pending!1 1;
        "flush still reports what it finished"]};

.qt.qetl.io.test_flush_is_a_no_op_for_a_manager_without_one:{[t]
    .qunit.assertEquals[.qetl.io.flush[.qetl.io.memory;2026.01.03D00:00:00.000000000];`finished`pending!0 0;
        "memory finishes nothing early and has nothing open"]};

.qt.qetl.io.test_a_flush_that_is_not_a_function_is_refused:{[t]
    .qunit.assertThrows[{.qetl.io.require_manager x};`write`flush!({[t;b] count b};42);
        "require_manager: an io manager's flush must be*";"flush must be callable"]};

/ --- due: whether a deployment should expose finished work now -----------

.qt.qetl.io.dueat:{[dirty;last_at;finished;pending;final]
    .qetl.io.due[`dirty`last!(dirty;last_at);`finished`pending`final!(finished;pending;final);
        2026.01.02D12:00:00.000000000;0D00:00:30]}

.qt.qetl.io.test_due_acts_when_something_finished_and_nothing_is_open:{[t]
    d:.qt.qetl.io.dueat[0b;0Np;1;0;0b];
    .qunit.assertEquals[(d`act;d`state);(1b;`dirty`last!(0b;2026.01.02D12:00:00.000000000));
        "act, and remember when"]};

.qt.qetl.io.test_due_waits_while_a_part_is_open:{[t]
    / The HDB would map a partition still being appended to, unsorted.
    d:.qt.qetl.io.dueat[0b;0Np;1;1;0b];
    .qunit.assertEquals[(d`act;d[`state]`dirty);(0b;1b);"no - but what finished is still owed"]};

.qt.qetl.io.test_due_does_nothing_when_nothing_is_new:{[t]
    .qunit.assertEquals[.qt.qetl.io.dueat[0b;0Np;0;0;1b]`act;0b;"even at the end, nothing new is nothing to do"]};

.qt.qetl.io.test_due_holds_back_within_the_interval_mid_run:{[t]
    d:.qt.qetl.io.dueat[0b;2026.01.02D11:59:50.000000000;1;0;0b];
    .qunit.assertEquals[(d`act;d[`state]`dirty);(0b;1b);"ten seconds after the last one: not yet, still owed"]};

.qt.qetl.io.test_due_acts_after_the_interval:{[t]
    .qunit.assertEquals[.qt.qetl.io.dueat[1b;2026.01.02D11:59:00.000000000;0;0;0b]`act;1b;
        "a minute later, what was owed is shown"]};

.qt.qetl.io.test_the_final_call_ignores_the_interval:{[t]
    .qunit.assertEquals[.qt.qetl.io.dueat[1b;2026.01.02D11:59:59.000000000;0;0;1b]`act;1b;
        "the run's end never leaves finished work unshown"]};

/ --- acknowledged: what stays owed after acting (#1084) -------------------

.qt.qetl.io.test_a_reload_every_hdb_acknowledged_owes_nothing:{[t]
    s:.qetl.io.acknowledged[`dirty`last!(0b;2026.01.02D12:00:00.000000000);`registered`reloaded!2 2];
    .qunit.assertEquals[s;`dirty`last!(0b;2026.01.02D12:00:00.000000000);"cleared, as due left it"]};

.qt.qetl.io.test_a_reload_an_hdb_refused_stays_owed_for_the_next_call:{[t]
    s:.qetl.io.acknowledged[`dirty`last!(0b;2026.01.02D12:00:00.000000000);`registered`reloaded!2 1];
    .qunit.assertEquals[s`dirty;1b;"one of two reloaded: still owed"];
    .qunit.assertEquals[.qetl.io.due[s;`finished`pending`final!(0;0;1b);2026.01.02D12:00:01.000000000;0D00:00:30]`act;1b;
        "so the run's final call asks again, though nothing new finished"]};

.qt.qetl.io.test_hdb_refuses_a_root_that_is_not_a_file_symbol:{[t]
    .qunit.assertThrows[{.qetl.io.hdb[x;`deal_time]};`plain;
        "hdb: root must be a file symbol*";"a bare symbol is not a directory"]};

/ --- on_conflict: what a write does with a row already there -------------

/ Two rows held, two arriving; id 2 is in both.
.qt.qetl.io.held:{[] ([] id:1 2; v:10 20; time:2026.01.02D01:00:00.000000000 2026.01.02D02:00:00.000000000)}
.qt.qetl.io.incoming:{[] ([] id:2 3; v:21 30; time:2026.01.02D02:30:00.000000000 2026.01.02D03:00:00.000000000)}
.qt.qetl.io.opts:{[] `row_key`target`time_column`range_from`range_to!(`id;`t;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000)}

.qt.qetl.io.test_upsert_replaces_a_key_already_there_and_adds_the_rest:{[t]
    r:.qetl.io.resolve[`upsert;.qt.qetl.io.held[];.qt.qetl.io.incoming[];.qt.qetl.io.opts[]];
    .qunit.assertEquals[(r`id;r`v);(1 2 3;10 21 30);"id 2 takes the incoming value, once"]};

.qt.qetl.io.test_ignore_keeps_the_row_already_there:{[t]
    r:.qetl.io.resolve[`ignore;.qt.qetl.io.held[];.qt.qetl.io.incoming[];.qt.qetl.io.opts[]];
    .qunit.assertEquals[(r`id;r`v);(1 2 3;10 20 30);"id 2 keeps 20; only id 3 is new"]};

.qt.qetl.io.test_append_keeps_both:{[t]
    r:.qetl.io.resolve[`append;.qt.qetl.io.held[];.qt.qetl.io.incoming[];.qt.qetl.io.opts[]];
    .qunit.assertEquals[r`id;1 2 2 3;"no check - the behaviour before on_conflict existed"]};

.qt.qetl.io.test_fail_refuses_naming_the_clash:{[t]
    .qunit.assertThrows[{[x] .qetl.io.resolve[`fail;.qt.qetl.io.held[];.qt.qetl.io.incoming[];.qt.qetl.io.opts[]]};::;
        "on_conflict fail: 1 row(s) of t already there by id";"one clash, named, nothing resolved"]};

.qt.qetl.io.test_fail_writes_when_nothing_clashes:{[t]
    r:.qetl.io.resolve[`fail;.qt.qetl.io.held[];1#.qt.qetl.io.incoming[] where 3=.qt.qetl.io.incoming[]`id;.qt.qetl.io.opts[]];
    .qunit.assertEquals[r`id;1 2 3;"fail only refuses a clash"]};

.qt.qetl.io.test_replace_drops_what_the_window_held_and_the_batch_left_out:{[t]
    / The window is the whole of the 2nd: id 1 is inside it and not in the
    / batch, so the source has withdrawn it.
    r:.qetl.io.resolve[`replace;.qt.qetl.io.held[];.qt.qetl.io.incoming[];.qt.qetl.io.opts[]];
    .qunit.assertEquals[(r`id;r`v);(2 3;21 30);"only what the source now says"]};

.qt.qetl.io.test_replace_keeps_what_lies_outside_the_window:{[t]
    o:@[.qt.qetl.io.opts[];`range_from;:;2026.01.02D02:00:00.000000000];
    r:.qetl.io.resolve[`replace;.qt.qetl.io.held[];.qt.qetl.io.incoming[];o];
    .qunit.assertEquals[r`id;1 2 3;"id 1 at 01:00 is before the window, so it stays"]};

.qt.qetl.io.test_a_batch_that_repeats_a_key_writes_it_once:{[t]
    b:([] id:3 3; v:30 31; time:2#2026.01.02D03:00:00.000000000);
    .qunit.assertEquals[exec v from .qetl.io.resolve[`upsert;.qt.qetl.io.held[];b;.qt.qetl.io.opts[]] where id=3;enlist 31;
        "upsert: the last of the batch wins"];
    .qunit.assertEquals[exec v from .qetl.io.resolve[`ignore;.qt.qetl.io.held[];b;.qt.qetl.io.opts[]] where id=3;enlist 30;
        "ignore: the first does"]};

/ #1089: the fold is deliberate but not silent - a key a source only
/ observed to be unique shows in the log the first time it repeats.
.qt.qetl.io.warned:{[f] {x where `WARNING=first each x} .qt.support.captured_log[0b] f}

.qt.qetl.io.test_a_keyed_write_that_folds_a_repeated_key_says_so:{[t]
    w:.qt.qetl.io.warned {.qetl.io.write_keyed[.qetl.io.discard;`t;([] id:3 3 4; v:30 31 40);`on_conflict`row_key!(`upsert;`id)]};
    .qunit.assertEquals[count w;1;"one warning for the batch"];
    .qunit.assertEquals[(w[0;3])`folded`target;(1;`t);"naming the target and how many rows folded"]};

.qt.qetl.io.test_a_keyed_write_of_unique_keys_and_an_append_say_nothing:{[t]
    .qunit.assertEquals[count .qt.qetl.io.warned {.qetl.io.write_keyed[.qetl.io.discard;`t;([] id:3 4; v:30 40);`on_conflict`row_key!(`upsert;`id)]};0;
        "unique keys fold nothing"];
    .qunit.assertEquals[count .qt.qetl.io.warned {.qetl.io.write_keyed[.qetl.io.discard;`t;([] id:3 3; v:30 31);`on_conflict`row_key!(`append;`id)]};0;
        "append keeps both rows, so there is nothing to report"]};

.qt.qetl.io.test_a_batch_with_other_columns_is_refused:{[t]
    .qunit.assertThrows[{[x] .qetl.io.resolve[`upsert;.qt.qetl.io.held[];([] id:enlist 9);.qt.qetl.io.opts[]]};::;
        "on_conflict: t holds *";"columns that differ are named, not joined"]};

.qt.qetl.io.test_an_unknown_strategy_is_refused_naming_the_ones_there_are:{[t]
    .qunit.assertThrows[{.qetl.io.require_strategy x};`merge;
        "on_conflict must be one of append, fail, ignore, replace, upsert - not merge";"a typo names the alternatives"]};

.qt.qetl.io.test_memory_upserts_rather_than_duplicating:{[t]
    o:`on_conflict`row_key!(`upsert;`a);
    .qetl.io.write_keyed[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[];o];
    .qetl.io.write_keyed[.qetl.io.memory;`iotgt;.qt.qetl.io.batch[];o];
    .qunit.assertEquals[count value `iotgt;3;"the same three rows twice is still three rows"]};

.qt.qetl.io.test_a_manager_that_can_only_append_refuses_any_other_strategy:{[t]
    / Rather than append quietly - the duplication this exists to stop.
    mgr:(enlist `write)!enlist {[target;b] count b};
    .qunit.assertThrows[{[m] .qetl.io.write_keyed[m;`iotgt;.qt.qetl.io.batch[];`on_conflict`row_key!(`upsert;`a)]};mgr;
        "write_keyed: this io manager can only append*";"refused, not appended"];
    .qunit.assertEquals[.qetl.io.write_keyed[mgr;`iotgt;.qt.qetl.io.batch[];`on_conflict`row_key!(`append;`a)];3;
        "append is what it can do"]};

/ The HDB writer, keyed: deals[] on the 2nd and 3rd, then written again.
.qt.qetl.io.hdb_opts:{[s] `on_conflict`row_key`time_column`range_from`range_to!(s;`sym`deal_time;`deal_time;2026.01.02D00:00:00.000000000;2026.01.04D00:00:00.000000000)}

.qt.qetl.io.test_hdb_upsert_writes_a_window_twice_without_duplicating:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qetl.io.write_keyed[m;`iodeals;update notional:9e6 from .qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(count p;asc p`notional);(2;9e6 9e6);"two deals on the 2nd, each restated, none doubled"]};

/ #1086: a batch whose keys a date does not hold is appended, not resolved
/ into a rewrite of the whole date - with the same result.
.qt.qetl.io.test_hdb_upsert_of_new_keys_adds_them_to_the_day:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    more:([] deal_time:enlist 2026.01.02D12:00:00.000000000; sym:enlist `AUDUSD; notional:enlist 5e6);
    .qetl.io.write_keyed[m;`iodeals;more;.qt.qetl.io.hdb_opts`upsert];
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(count p;asc p`notional);(3;1e6 2e6 5e6);"the day's two deals and the new one"]};

.qt.qetl.io.test_hdb_new_keys_a_batch_repeats_fold_as_resolve_folds_them:{[t]
    twice:([] deal_time:2#2026.01.02D12:00:00.000000000; sym:2#`AUDUSD; notional:5e6 6e6);
    up:.qt.qetl.io.hdb_dir[];
    .qetl.io.write_keyed[.qetl.io.hdb[up;`deal_time];`iodeals;twice;.qt.qetl.io.hdb_opts`upsert];
    ig:.qt.qetl.io.hdb_dir[];
    .qetl.io.write_keyed[.qetl.io.hdb[ig;`deal_time];`iodeals;twice;.qt.qetl.io.hdb_opts`ignore];
    .qunit.assertEquals[(.qt.qetl.io.part[up;2026.01.02;`iodeals]`notional;.qt.qetl.io.part[ig;2026.01.02;`iodeals]`notional);(enlist 6e6;enlist 5e6);
        "upsert keeps the last, ignore the first - one row either way"]};

/ #1142: a key on both sides of midnight is two rows - the fast path (fresh
/ HDB) must fold per date like the full path (the 2nd already holds a key).
.qt.qetl.io.test_hdb_fast_path_keeps_a_key_either_side_of_midnight:{[t]
    b:([] deal_time:2026.01.02D23:59:00.000000000 2026.01.03D00:01:00.000000000 2026.01.02D13:00:00.000000000; sym:`AUDUSD`AUDUSD`GBPUSD; notional:1 2 3e6);
    o:`on_conflict`row_key`time_column`range_from`range_to!(`upsert;enlist `sym;`deal_time;2026.01.02D00:00:00.000000000;2026.01.04D00:00:00.000000000);
    fast:.qt.qetl.io.hdb_dir[];
    .qetl.io.write_keyed[.qetl.io.hdb[fast;`deal_time];`iodeals;b;o];
    slow:.qt.qetl.io.hdb_dir[];
    .qetl.io.write_keyed[.qetl.io.hdb[slow;`deal_time];`iodeals;([] deal_time:enlist 2026.01.02D13:00:00.000000000; sym:enlist `GBPUSD; notional:enlist 9e6);o];
    .qetl.io.write_keyed[.qetl.io.hdb[slow;`deal_time];`iodeals;b;o];
    rows:{[r] asc raze {[r;d] p:.qt.qetl.io.part[r;d;`iodeals]; exec notional from p}[r] each 2026.01.02 2026.01.03};
    .qunit.assertEquals[rows fast;1 2 3e6;"fast path: one AUDUSD row each side of midnight"];
    .qunit.assertEquals[rows fast;rows slow;"and the full path agrees"]};

/ #1160: the fast path is derived from resolve, so for any clean batch it
/ writes what the full path writes. The full path is forced by seeding the
/ second HDB with a row of an unrelated key on the 2nd (a non-empty date that
/ clashes with nothing); that row is set aside before comparing.
.qt.qetl.io.test_hdb_fast_path_writes_what_the_full_path_writes:{[t]
    mk:{[tm;sy;nt] ([] deal_time:tm; sym:sy; notional:nt)};
    batches:(
        mk[2026.01.02D23:59:00.000000000 2026.01.03D00:01:00.000000000 2026.01.02D13:00:00.000000000;`AUDUSD`AUDUSD`GBPUSD;1 2 3e6];
        mk[2026.01.02D10:00:00.000000000 2026.01.02D11:00:00.000000000 2026.01.02D12:00:00.000000000 2026.01.03D01:00:00.000000000 2026.01.03D02:00:00.000000000;`AUDUSD`AUDUSD`NZDUSD`NZDUSD`NZDUSD;1 2 3 4 5e6];
        mk[2026.01.03D09:00:00.000000000 2026.01.02D09:00:00.000000000 2026.01.03D10:00:00.000000000;`EURUSD`EURUSD`EURUSD;7 8 9e6];
        mk[enlist 2026.01.03D05:00:00.000000000;enlist `CADUSD;enlist 4e6]);
    / Enumerated symbols read against THIS root's domain, so they read as symbols.
    rows:{[r] @[`.;`sym;:;get ` sv r,`sym]; a:raze {[r;d] $[()~key hsym `$(string .Q.par[r;d;`iodeals]),"/"; ([] deal_time:0#0Np; sym:0#`; notional:0#0f); select deal_time,sym:`$string sym,notional from .qt.qetl.io.part[r;d;`iodeals] where sym<>`ZZZ]}[r] each 2026.01.02 2026.01.03;
        `deal_time`notional xasc 0!a};
    seed:mk[enlist 2026.01.02D08:00:00.000000000;enlist `ZZZ;enlist 0e6];
    run:{[rows;seed;batches;s]
        o:.qt.qetl.io.hdb_opts[s];
        {[rows;seed;o;b]
            fast:.qt.qetl.io.hdb_dir[];
            .qetl.io.write_keyed[.qetl.io.hdb[fast;`deal_time];`iodeals;b;o];
            slow:.qt.qetl.io.hdb_dir[];
            .qetl.io.write_keyed[.qetl.io.hdb[slow;`deal_time];`iodeals;seed;o];
            .qetl.io.write_keyed[.qetl.io.hdb[slow;`deal_time];`iodeals;b;o];
            (rows fast;rows slow)}[rows;seed;o] each batches};
    {[run;rows;seed;batches;s]
        pairs:run[rows;seed;batches;s];
        .qunit.assertEquals[pairs[;0];pairs[;1];"fast path equals full path under ",string s]}[run;rows;seed;batches] each `upsert`ignore;
    pairs:run[rows;seed;batches;`fail];
    .qunit.assertEquals[pairs[;0];pairs[;1];"fast path equals full path under fail"]};

.qt.qetl.io.test_hdb_fail_with_one_clashing_date_writes_no_date:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    b:([] deal_time:2026.01.02D10:00:00.000000000 2026.01.03D12:00:00.000000000; sym:`GBPUSD`AUDUSD; notional:7e6 8e6);
    .qunit.assertThrows[{[r;b] .qetl.io.write_keyed[.qetl.io.hdb[r;`deal_time];`iodeals;b;.qt.qetl.io.hdb_opts`fail]}[root];b;
        "on_conflict fail: *";"the 2nd's clash is refused"];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.03;`iodeals];1;"and the 3rd's new deal was not appended meanwhile"]};

.qt.qetl.io.test_hdb_append_still_duplicates:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`append];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`append];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.02;`iodeals];4;"append is the old path, unchanged"]};

.qt.qetl.io.test_hdb_ignore_keeps_the_first_write:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`ignore];
    .qetl.io.write_keyed[m;`iodeals;update notional:9e6 from .qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`ignore];
    .qunit.assertEquals[asc .qt.qetl.io.part[root;2026.01.02;`iodeals]`notional;1e6 2e6;"the rows already there stay"]};

.qt.qetl.io.test_hdb_replace_clears_a_day_the_source_has_emptied:{[t]
    / The second write returns only the 3rd's deal: the 2nd, inside the
    / window and now empty upstream, must lose what it held.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qetl.io.write_keyed[m;`iodeals;select from .qt.qetl.io.deals[] where deal_time>2026.01.03D00:00:00.000000000;.qt.qetl.io.hdb_opts`replace];
    .qunit.assertEquals[(count .qt.qetl.io.part[root;2026.01.02;`iodeals];count .qt.qetl.io.part[root;2026.01.03;`iodeals]);0 1;
        "the emptied day is empty, the other day keeps its deal"]};

/ A stream job's row and its backfill twin's, one key, stamped either side of
/ midnight: the live copy by receipt (half a second after), the twin's by the
/ event (#884). Partitioned on `time`, keyed without it.
.qt.qetl.io.spill_opts:`on_conflict`row_key`time_column`range_from`range_to!(`replace;`sym`trade_time;`time;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000)
.qt.qetl.io.live_rows:{[] ([] time:2026.01.03D00:00:00.500000000 2026.01.03D10:00:00.000000000;
    sym:`EURUSD`EURUSD; trade_time:2026.01.02D23:59:59.000000000 2026.01.03D09:59:59.000000000; px:1.1 1.2)}
.qt.qetl.io.twin_row:{[] ([] time:enlist 2026.01.02D23:59:59.900000000; sym:enlist `EURUSD;
    trade_time:enlist 2026.01.02D23:59:59.000000000; px:enlist 1.1)}

.qt.qetl.io.test_hdb_replace_takes_its_keys_out_of_the_next_day:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`time];
    .qetl.io.write[m;`iospill;.qt.qetl.io.live_rows[]];
    .qetl.io.write_keyed[m;`iospill;.qt.qetl.io.twin_row[];.qt.qetl.io.spill_opts];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.02;`iospill];1;"the twin's row is on the 2nd"];
    .qunit.assertEquals[exec trade_time from .qt.qetl.io.part[root;2026.01.03;`iospill];enlist 2026.01.03D09:59:59.000000000;
        "and the 3rd lost the live copy of that key - nothing else"]};

/ #972: a transform that moves `time` (trade_time+horizon). Window one is the
/ 2nd and its row is timed on the 3rd; window two is the 3rd. Replacing window
/ two must clear on trade_time - the column the windows were cut on - or the
/ 3rd's partition loses window one's row.
.qt.qetl.io.moved_opts:{[from_ts;to_ts] `on_conflict`row_key`time_column`range_from`range_to!(`replace;`sym`trade_time;`trade_time;from_ts;to_ts)}
.qt.qetl.io.moved_row:{[tt;dt] ([] time:enlist tt+dt; sym:enlist `EURUSD; trade_time:enlist tt; px:enlist 1.1)}

.qt.qetl.io.test_hdb_replace_clears_on_the_window_column_not_on_time:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`time];
    d2:2026.01.02D00:00:00.000000000; d3:2026.01.03D00:00:00.000000000; d4:2026.01.04D00:00:00.000000000;
    .qetl.io.write_keyed[m;`iomoved;.qt.qetl.io.moved_row[2026.01.02D23:59:59.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d2;d3]];
    .qetl.io.write_keyed[m;`iomoved;.qt.qetl.io.moved_row[2026.01.03D10:00:00.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d3;d4]];
    .qunit.assertEquals[asc exec trade_time from .qt.qetl.io.part[root;2026.01.03;`iomoved];
        2026.01.02D23:59:59.000000000 2026.01.03D10:00:00.000000000;
        "window one's row, timed on the 3rd, survives window two's replace"];
    / Restating window two with nothing clears its own row and only that.
    .qetl.io.write_keyed[m;`iomoved;0#.qt.qetl.io.moved_row[2026.01.03D10:00:00.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d3;d4]];
    .qunit.assertEquals[exec trade_time from .qt.qetl.io.part[root;2026.01.03;`iomoved];enlist 2026.01.02D23:59:59.000000000;
        "an emptied window loses its own rows"]};

.qt.qetl.io.test_memory_replace_clears_on_the_window_column_not_on_time:{[t]
    d2:2026.01.02D00:00:00.000000000; d3:2026.01.03D00:00:00.000000000; d4:2026.01.04D00:00:00.000000000;
    `iomoved_mem set 0#.qt.qetl.io.moved_row[2026.01.02D23:59:59.000000000;0D00:00:10];
    .qetl.io.write_keyed[.qetl.io.memory;`iomoved_mem;.qt.qetl.io.moved_row[2026.01.02D23:59:59.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d2;d3]];
    .qetl.io.write_keyed[.qetl.io.memory;`iomoved_mem;.qt.qetl.io.moved_row[2026.01.03D10:00:00.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d3;d4]];
    .qunit.assertEquals[asc (get `iomoved_mem)`trade_time;2026.01.02D23:59:59.000000000 2026.01.03D10:00:00.000000000;
        "window two's replace leaves window one's row, whose time is past midnight"];
    .qetl.io.write_keyed[.qetl.io.memory;`iomoved_mem;0#.qt.qetl.io.moved_row[2026.01.03D10:00:00.000000000;0D00:00:10];.qt.qetl.io.moved_opts[d3;d4]];
    .qunit.assertEquals[(get `iomoved_mem)`trade_time;enlist 2026.01.02D23:59:59.000000000;
        "restating window two empty clears its own row and only that"]};

.qt.qetl.io.test_hdb_replace_leaves_the_next_day_alone_when_time_is_in_the_key:{[t]
    / With time in the key, a later partition cannot hold the same key.
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`time];
    .qetl.io.write[m;`iospill;.qt.qetl.io.live_rows[]];
    .qetl.io.write_keyed[m;`iospill;.qt.qetl.io.twin_row[];@[.qt.qetl.io.spill_opts;`row_key;:;`sym`time]];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.03;`iospill];2;"both rows of the 3rd are still there"]};

.qt.qetl.io.test_hdb_fail_writes_nothing_when_a_key_clashes:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qunit.assertThrows[{[r;ignored] .qetl.io.write_keyed[.qetl.io.hdb[r;`deal_time];`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`fail]}[root];::;
        "on_conflict fail: *";"the clash is refused"];
    .qunit.assertEquals[count .qt.qetl.io.part[root;2026.01.02;`iodeals];2;"and nothing was written - the 2nd still holds two"]};

.qt.qetl.io.test_hdb_keyed_writes_are_finished_like_any_other:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qetl.io.finish m;
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(value p`sym;attr p`sym);(`EURUSD`GBPUSD;`p);"sorted and p#sym, as an appended partition is"]};

/ --- staged keyed writes and their recovery ------------------------------

.qt.qetl.io.test_a_keyed_write_leaves_nothing_in_staging:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    s:.qetl.io.staging root;
    left:{[s;kind] $[()~key hsym `$s,"/",kind; 0; sum {[s;kind;d] count key hsym `$s,"/",kind,"/",string d}[s;kind] each key hsym `$s,"/",kind]}[s] each ("new";"old");
    .qunit.assertEquals[(left;count .qt.qetl.io.part[root;2026.01.02;`iodeals]);(0 0;2);
        "the partition is written and the staging area is empty again"]};

/ A kill between the two renames: the live table moved to `old`, its
/ replacement never moved in. recover puts the old one back.
.qt.qetl.io.test_recover_restores_a_table_swapped_out_and_never_replaced:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qetl.io.finish m;
    before:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    old:.qetl.io.staged[root;`old;2026.01.02;`iodeals];
    system"mkdir -p ",(.qetl.io.staging root),"/old/2026.01.02";
    system"mv ",.qetl.io.part_path[root;2026.01.02;`iodeals]," ",old;
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[(.qt.qetl.io.part[root;2026.01.02;`iodeals];()~key hsym `$old);(before;1b);
        "the partition reads as it did before the write, and old is gone"]};

/ A kill after staging and before any swap: the staged copy is a write that
/ never happened, and the live partition was never touched.
.qt.qetl.io.test_recover_discards_a_staged_table_that_never_reached_its_swap:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    before:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qetl.io.stage[root;2026.01.02;`iodeals;0#before];
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertEquals[(count .qt.qetl.io.part[root;2026.01.02;`iodeals];()~key hsym `$.qetl.io.staged[root;`new;2026.01.02;`iodeals]);(2;1b);
        "the live partition keeps its rows and the staged copy is removed"]};

/ Another table's staging is another process's write in flight.
.qt.qetl.io.test_recover_leaves_another_tables_staging_alone:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write_keyed[m;`iodeals;.qt.qetl.io.deals[];.qt.qetl.io.hdb_opts`upsert];
    .qetl.io.stage[root;2026.01.02;`other;0#.qt.qetl.io.part[root;2026.01.02;`iodeals]];
    .qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qunit.assertTrue[not ()~key hsym `$.qetl.io.staged[root;`new;2026.01.02;`other];"untouched"]};

/ A kill part way through an APPEND, which writes column by column: one
/ column one row longer than the rest. recover trims it back and queues the
/ partition for finishing.
.qt.qetl.io.test_recover_trims_a_torn_append:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    .qetl.io.finish m;
    f:hsym `$.qetl.io.part_path[root;2026.01.02;`iodeals],"/notional";
    f set (get f),9e9;
    n:.qetl.io.recover[m;`iodeals;2026.01.02D00:00:00.000000000;2026.01.03D00:00:00.000000000];
    .qetl.io.finish m;
    p:.qt.qetl.io.part[root;2026.01.02;`iodeals];
    .qunit.assertEquals[(n;count p;asc p`notional;attr p`sym);(1;2;1e6 2e6;`p);
        "every column ends at the same row again, and the partition is finished"]};

/ --- the wiring ----------------------------------------------------------

.qt.qetl.io.test_define_refuses_a_malformed_manager:{[t]
    / At DEFINE time, not at first write. A worker with a broken manager
    / should fail before it has fetched a window it cannot store.
    .qunit.assertError[{.qetl.job.bounded.define[`io_broken;x]};
        `source`dataset`width`transform`io!(`demo_deals;`io_broken_ds;1D;`demo_deals_passthrough;42);
        "a malformed io manager stops the worker at declaration"]};

.qt.qetl.io.test_a_custom_manager_receives_the_target_and_batch:{[t]
    / Proves the seam actually carries the worker's own target through,
    / rather than the manager being called with something else.
    `iocapture set ();
    mgr:(enlist `write)!enlist {[target;b] `iocapture set (target;count b); count b};
    .qetl.io.write[mgr;`some_target;.qt.qetl.io.batch[]];
    .qunit.assertEquals[value `iocapture;(`some_target;3);
        "the manager is handed the declared target and the batch"]};

/ --- the registry's shape, which this work exposed ------------------------

.qt.qetl.io.test_a_worker_is_not_handed_a_key_it_never_declared:{[t]
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

.qt.qetl.io.test_a_worker_declaring_a_new_optional_key_can_register:{[t]
    / Before normalisation this threw 'mismatch: a table cannot gain a column
    / by assignment, so the FIRST worker to declare a key no earlier worker
    / had could not be registered at all. Registration order silently decided
    / which declarations were legal.
    .qunit.assertEquals[.qetl.job.bounded.define[`io_newkey;
        `source`dataset`width`transform`io!(`demo_deals;`io_newkey_ds;1D;`demo_deals_passthrough;.qetl.io.discard)];
        `io_newkey;
        "a worker declaring an optional key registers regardless of order"]};

.qt.qetl.io.test_every_registered_config_has_the_same_keys:{[t]
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

.qt.qetl.io.test_read_part_returns_a_partition_with_its_symbols_decoded:{[t]
    root:.qt.qetl.io.hdb_dir[];
    .qetl.io.write[.qetl.io.hdb[root;`deal_time];`iodeals;.qt.qetl.io.deals[]];
    p:.qetl.io.read_part hsym `$(string .Q.par[root;2026.01.02;`iodeals]),"/";
    .qunit.assertEquals[(type p`sym;asc p`sym);(11h;`EURUSD`GBPUSD);
        "symbols as symbols, not the sym file's enumeration - what a whole rewrite appends to"];
    .qunit.assertEquals[cols p;cols .qt.qetl.io.part[root;2026.01.02;`iodeals];"every column the partition has"]};

.qt.qetl.io.test_in_order_is_finish_s_order_sym_then_time:{[t]
    root:.qt.qetl.io.hdb_dir[];
    m:.qetl.io.hdb[root;`deal_time];
    .qetl.io.write[m;`iodeals;.qt.qetl.io.deals[]];
    base:string .Q.par[root;2026.01.02;`iodeals];
    c:get hsym `$base,"/.d";
    .qunit.assertTrue[not .qetl.io.in_order[base;c];"written in arrival order, GBPUSD first"];
    .qetl.io.finish m;
    .qunit.assertTrue[.qetl.io.in_order[base;c];"finished: sorted by sym, then time"];
    .qunit.assertTrue[.qetl.io.in_order[base;`notional];"no sym and no time: nothing to be out of order"]};
