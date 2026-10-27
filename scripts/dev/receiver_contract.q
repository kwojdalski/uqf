// receiver_contract.q - print a bounded worker's receiver contract as JSON (#1717).
//
//   q scripts/dev/receiver_contract.q -worker W
//
// Run by `uqs job contract export W` (uqs.attach.receiver_contract) on the machine that has the
// worker's declarations: it loads that worker alone and prints .qetl.io.remote.contract.of as one
// line of JSON, which uqs gives its identity. Nothing is written. Exits 2, naming the cause, for a
// worker it cannot load or derive a contract from.

if[not `worker in key opts:.Q.opt .z.x; -1 "receiver_contract: -worker is required"; exit 2];

\l src/init.q
.qetl.load.only:`$first opts`worker
\l src/etl/init.q

-1 .j.j @[.qetl.io.remote.contract.of;`$first opts`worker;{[e] -2 "receiver_contract: ",e; exit 2}];
exit 0
