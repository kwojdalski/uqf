/ torq_tap.q - a generic debug tap: subscribes to some (or, by default,
/ every) table on the tickerplant and logs each incoming batch as it
/ arrives, unmodified - "print out all data being added to kdb". Table
/ filter comes from process.csv's `extras` field (-tables t1 t2 ...), the
/ same convention other processes here already use for CLI flags (e.g.
/ sctp1's -parentproctype); blank/unset means every table. Change it live
/ with `uqs config-set tap1 extras "-tables quote wide_book"` then
/ restart tap1 - no orchestrator-side code needed for the filtering.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv uqs.stack.runtime.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+28 - see scripts/processes/process_ports.csv).
/ startwithall=0 (a debug utility, not part of the standing demo stack) -
/ start it explicitly with `uqs start tap1`, then watch it with
/ `uqs logs -f tap1`. Each line's `id` field (see
/ stack/logs.py's parse_log_line/_LOG_FIELDS) is the table name that ticked, so
/ `uqs logs -f tap1 | grep quotes`-style filtering works even
/ without narrowing the subscription itself.

\d .qproc.tap

opts:.Q.opt[.z.x];
/ blank symbol = subscribe to every table - same "no filter" convention
/ .sub.subscribe/RDB's own default subtabs already use.
tap_tables:$[`tables in key opts; `$opts[`tables]; `];

tickerplanttypes:`segmentedtickerplant;
requiredprocs:tickerplanttypes;
tpconsleep:10;
tpcheckcycles:0W;

subscribe:{
  if[0=count s:.sub.getsubscriptionhandles[.qproc.tap.tickerplanttypes;();()!()];:()];
  subproc:first s;
  / tap_tables is either the blank atom `` (subscribe to everything) or a
  / symbol vector (from -tables t1 t2 ...) - string of an atom is already
  / flat, string of a vector needs an explicit join, or "," ends up
  / splicing individual characters in among the table names instead of
  / joining them (threw a 'type error the first time this shipped).
  .lg.o[`subscribe;"tapping ",$[.qproc.tap.tap_tables~`;"all tables";", " sv string .qproc.tap.tap_tables]," on ",string subproc`procname];
  / setschema=1b (unlike cross1/vectorize1, which pre-define their own
  / namespaced mirror table): tap1 has no local table of its own for any
  / of this - .sub.subscribe's createtables auto-creates a matching empty
  / root-level table per subscribed table, which the underlying dispatch
  / needs to exist before it'll actually call upd (confirmed empirically:
  / without this, subscription "succeeds" and upd works fine when called
  / manually, but never fires for real ticks - no error, just silently
  / dropped).
  .sub.subscribe[.qproc.tap.tap_tables;`;1b;0b;subproc]
 };

init:{
  .servers.startupdepcycles[.qproc.tap.requiredprocs;.qproc.tap.tpconsleep;.qproc.tap.tpcheckcycles];
  .qproc.tap.subscribe[];
 };

\d .

/ Receive every tapped table's ticks and log the table name (as the log
/ line's `id` field) plus the raw batch, unmodified - .Q.s1 handles whatever
/ shape x happens to arrive in (a table, or a list of columns) without
/ needing to know which.
/ .
/ At ROOT, where the tickerplant calls it (scripts/processes/torq_pipeline.q,
/ invariant 5). Everything else this process owns is in .qproc.tap.
upd:{[t;x]
  .lg.o[t; .Q.s1 x];
 }

/ same reasoning as torq_cross_etl.q/torq_vectorize_etl.q: a real
/ .sub.subscribe subscriber needs .servers.startup[] to open a live,
/ access-listed handle to stp1 - tap1's proctype "metrics" in process.csv
/ (model/registry.py) borrows an already-credentialed type for that.
.servers.CONNECTIONS:.qproc.tap.requiredprocs;
.servers.startup[];
.qproc.tap.init[];

/ The plant sends `(`endofperiod;x;y;z)` and `(`endofday;x;y)` to every
/ subscriber and expects both at ROOT. tap1 is a subscriber like any other,
/ and without these it throws once a period into its own stderr log, where
/ nothing looks. Defined here rather than via
/ .qpipe.install_period_handlers because tap1 does its own subscribing and
/ deliberately does not load the adapter (loads_qpipe=False) - see
/ scripts/processes/torq_pipeline.q, invariant 9, for the reasoning behind
/ the empty bodies.
endofperiod:{[current_period;next_period;data]
    .lg.o[`qproc;"end of period ",(string current_period)," -> ",string next_period];
    }

endofday:{[dt;data] .lg.o[`qproc;"end of day ",string dt]; }
