/ torq_tap.q - a generic debug tap: subscribes to some (or, by default,
/ every) table on the tickerplant and logs each incoming batch as it
/ arrives, unmodified - "print out all data being added to kdb". Table
/ filter comes from process.csv's `extras` field (-tables t1 t2 ...), the
/ same convention other processes here already use for CLI flags (e.g.
/ sctp1's -parentproctype); blank/unset means every table. Change it live
/ with `torq-demo config-set tap1 extras "-tables quote wide_book"` then
/ restart tap1 - no orchestrator-side code needed for the filtering.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+28 - see TAP_PORT_OFFSET in core.py).
/ startwithall=0 (a debug utility, not part of the standing demo stack) -
/ start it explicitly with `torq-demo start tap1`, then watch it with
/ `torq-demo logs -f tap1`. Each line's `id` field (see
/ core.py's parse_log_line/_LOG_FIELDS) is the table name that ticked, so
/ `torq-demo logs -f tap1 | grep quotes`-style filtering works even
/ without narrowing the subscription itself.

opts:.Q.opt[.z.x];
/ blank symbol = subscribe to every table - same "no filter" convention
/ .sub.subscribe/RDB's own default subtabs already use.
tap_tables:$[`tables in key opts; `$opts[`tables]; `];

/ receive every tapped table's ticks and log the table name (as the log
/ line's `id` field) plus the raw batch, unmodified - .Q.s1 handles
/ whatever shape x happens to arrive in (a table, or a list of columns;
/ see torq_vectorize_etl.q's own comment on this ambiguity) without
/ needing to know which.
upd:{[t;x]
  .lg.o[t; .Q.s1 x];
 }

tickerplanttypes:`segmentedtickerplant;
requiredprocs:tickerplanttypes;
tpconsleep:10;
tpcheckcycles:0W;

subscribe:{
  if[0=count s:.sub.getsubscriptionhandles[tickerplanttypes;();()!()];:()];
  subproc:first s;
  / tap_tables is either the blank atom `` (subscribe to everything) or a
  / symbol vector (from -tables t1 t2 ...) - string of an atom is already
  / flat, string of a vector needs an explicit join, or "," ends up
  / splicing individual characters in among the table names instead of
  / joining them (threw a 'type error the first time this shipped).
  .lg.o[`subscribe;"tapping ",$[tap_tables~`;"all tables";", " sv string tap_tables]," on ",string subproc`procname];
  / setschema=1b (unlike cross1/vectorize1, which pre-define their own
  / namespaced mirror table): tap1 has no local table of its own for any
  / of this - .sub.subscribe's createtables auto-creates a matching empty
  / root-level table per subscribed table, which the underlying dispatch
  / needs to exist before it'll actually call upd (confirmed empirically:
  / without this, subscription "succeeds" and upd works fine when called
  / manually, but never fires for real ticks - no error, just silently
  / dropped).
  .sub.subscribe[tap_tables;`;1b;0b;subproc]
 };

init:{
  .servers.startupdepcycles[requiredprocs;tpconsleep;tpcheckcycles];
  subscribe[];
 };

/ same reasoning as torq_cross_etl.q/torq_vectorize_etl.q: a real
/ .sub.subscribe subscriber needs .servers.startup[] to open a live,
/ access-listed handle to stp1 - tap1's proctype "metrics" in process.csv
/ (core.py) borrows an already-credentialed type for that.
.servers.CONNECTIONS:requiredprocs;
.servers.startup[];
init[];
