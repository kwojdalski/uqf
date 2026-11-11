/ run_remote_writer.q - a backfill extracted in THIS process and written by
/ ANOTHER, which owns the HDB (#1650).
/ .
/ The destination (scripts/jobs/attached_receiver.q, as `uqs job receive` starts it) holds the HDB and its
/ scoped writer; this process runs demo_deals_backfill on its fixture with
/ .qetl.io.remote.manager as its io manager, so every window's batch crosses a
/ real handle and is committed by the destination's receiver. This process
/ never writes the HDB's files.
/ .
/ WHAT THIS PROVES that the unit suite cannot: the receiver's gate admits the
/ worker's real messages over IPC, coverage is recorded only for windows the
/ destination committed, a window whose acknowledgement was lost is resent on
/ the next run and acknowledged as a duplicate rather than appended twice, and
/ finish over the wire finishes the destination's partitions.
/ .
/ Its own process (tests/qrun/test_process_runners.py), as run_two_instances is:
/ it needs a second process and a port.

\l src/init.q
\l src/etl/init.q
\l tests/lib/qunit.q
\l tests/lib/support.q

statusdir:getenv `UQF_STATUS_DIR;
if[0=count statusdir; '"run_remote_writer: set UQF_STATUS_DIR to a fresh directory"];
system"mkdir -p ",statusdir;

failures:0;
check:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- start the destination ------------------------------------------------

root:statusdir,"/hdb";
system"mkdir -p ",root;
(hsym `$root,"/sym") set `symbol$();
/ The port is one the OS hands out (.qt.support.free_port), not a random draw
/ from 5510+, where TorQ stacks run (#2259); one that already answers - a
/ leftover receiver - is skipped, never tested as this run's receiver.
port:.qt.support.free_port[];
log_path:statusdir,"/attached_receiver.log";
/ the receiver contract the destination's operator approved, derived here from the worker's
/ declarations as `uqs job attachment contract export` derives it (#1717): the receiver loads no worker
contract_path:statusdir,"/contract.json";
(hsym `$contract_path) 0: enlist .j.j `contract`binding!(.qetl.io.remote.contract.of `demo_deals_backfill;
    (enlist `conflict_policies)!enlist `append`upsert);
/ the real receiver (scripts/jobs/attached_receiver.q), as `uqs job receive` starts it - no readers
/ to reload here, the writer allowed as this account, the fence fifteen minutes away: a receiver
/ this run fails to stop still stops itself then, rather than idling on its port (#1760)
flags:" -contract ",contract_path," -attachment uat -destid rwtest -hdb ",root,
    " -max_date ",string[.z.d-1]," -deadline ",string[.z.p+0D00:15]," -readers _none_ -user nobody",
    " -journal ",statusdir,"/journal -writers ",string .z.u;
pid:.qt.support.spawn_q["scripts/jobs/attached_receiver.q";port;flags;log_path];
/ whatever way this process ends - a failed check, an error, the end - the receiver ends with it
.z.exit:{[code] .qt.support.stop_pid pid}
ready:.qt.support.wait_listening[port;100];
if[not check["the receiver is listening";ready]; [-1 each read0 hsym `$log_path; exit 1]];
/ the writer's handle to the receiver, for the whole run
h:hopen `$":localhost:",string port;

/ demo_deals rows on the receiver's disk, read from the files as any reader would
rows_on_disk:{[]
    days:d where not null d:"D"$string key hsym `$root;
    sum {[r;x] p:` sv .Q.par[hsym `$r;x;`demo_deals],`sym; $[()~key p; 0; count get p]}[root] each days}

/ --- extract here, commit there ---------------------------------------------

setenv[`UQF_FIXTURE_WRITES;enlist "1"];
setenv[`UQF_ON_CONFLICT;"append"];
.qetl.cfg.reset[];
spec:{[a;b] `source_version`range_from`range_to!(`v1;a;b)};
d:{[n] 2026.09.10D00:00:00.000000000+n*1D};
fixture_rows:{[a;b] count select from .qpipe.source.demo_deals.fixture[] where deal_time>=a, deal_time<b};

/ The first window's answer is lost after the destination committed it.
lost:0b;
lossy:{[h;m] r:h m; if[(`.qetl.io.remote.submit~first m) and not lost; [`lost set 1b; '"connection lost"]]; r}[h];
.qetl.io.default:.qetl.io.remote.manager[lossy;`uat;`demo_deals];
.qpipe.job.demo_deals_backfill.init[spec[d 1;d 4]];
r1:.qpipe.job.demo_deals_backfill.run[];
check["the window whose acknowledgement was lost failed here";1=r1`windows_failed];
check["the others were committed";2=r1`windows_completed];
check["and the destination holds all three days, the lost one included";
    (fixture_rows[d 1;d 4])=rows_on_disk[]];

.qetl.io.default:.qetl.io.remote.manager[{[h;m] h m}[h];`uat;`demo_deals];
.qpipe.job.demo_deals_backfill.init[spec[d 1;d 4]];
r2:.qpipe.job.demo_deals_backfill.run[];
check["the re-run resends only the failed window";1=r2`windows_completed];
check["which the destination acknowledges without appending it again";
    (fixture_rows[d 1;d 4])=rows_on_disk[]];
check["coverage now holds the whole range";
    .qetl.coverage.is_covered[`demo_deals;`;`$"v1~fixture";.z.p;d 1;d 4]];
check["nothing was written to an HDB from this process";0=count .qetl.io.touched];

/ a batch the approved contract does not admit is refused over the wire, and nothing is written
o:`on_conflict`row_key`time_column`range_from`range_to`partition_column`partition`source_version!(
    `replace;`deal_id;`deal_time;d 5;d 6;`;`;`v1);
b:select from .qpipe.source.demo_deals.fixture[] where deal_time>=d 5, deal_time<d 6;
st:@[h;(`.qetl.io.remote.submit;.qetl.io.remote.request_keys!(`uat;`demo_deals;`demo_deals;`;o;b));{x}];
check["a conflict policy the approval does not permit is refused";st like "remote: on_conflict replace is not one the contract permits*"];
st:@[h;(`.qetl.io.remote.submit;.qetl.io.remote.request_keys!(`uat;`demo_deals;`demo_deals;`;@[o;`on_conflict`row_key;:;(`upsert;`sym)];b));{x}];
check["so is a row key that is not the contract's";st like "remote: the batch's row key sym is not the contract's deal_id"];
check["and nothing more reached the destination";(fixture_rows[d 1;d 4])=rows_on_disk[]];

st:@[h;(`.qetl.io.remote.status;::);{x}];
check["the gate refuses the receiver's status - it is not an entry point";st~"attached_receiver: not an entry point"];
st:@[h;(`.qetl.io.remote.forget;`x);{x}];
check["and the operator's forget";st~"attached_receiver: not an entry point"];

-1 "";
-1 $[failures=0; "remote writer: all checks passed"; "remote writer: ",string[failures]," check(s) FAILED"];
exit failures>0
