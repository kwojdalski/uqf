/ torq_vectorize_etl.q - proof-of-concept ETL: subscribes to
/ torq_wide_book_feed.q's `wide_book` table (bids0..bids10/asks0..asks10,
/ one scalar column per depth level - the "incorrectly-ingested wide order
/ book" shape src/book.q's own header describes) and, on every batch,
/ folds it into forwards.q's vector-column book shape via uqf's own
/ .qbook.book_from_wide_levels/derive_level_groups - then publishes the
/ result back onto the tickerplant as a second table, `mkt_orderbook`
/ (bid_prices/ask_prices as level-0-first vectors). A literal "insert
/ records from a wide table into a vectorized version of it", transform
/ done by uqf rather than a straight copy - same proof-of-concept shape as
/ torq_cross_etl.q/cross1, exercising a different uqf module (book.q
/ instead of forwards.q's cross_book_at).
/ .
/ mkt_orderbook is a normal database table, not private state: like
/ quotes/wide_book, its schema is generated into database.q
/ (MKT_ORDERBOOK_TABLE_SCHEMA in core.py) and it's published through stp1,
/ so it flows through rdb1/wdb1/hdb exactly like any vendored table -
/ query it via rdb1 (or any other subscriber), not by connecting to
/ vectorize1 directly.
/ .
/ Not loaded by src/init.q or anything else uqf itself runs - registered
/ only in the process.csv torq_orchestrator.core.bootstrap() generates on
/ the fly (port {KDBBASEPORT}+27 - see VECTORIZE_ETL_PORT_OFFSET in
/ core.py). e.g. `torq-demo query "select from mkt_orderbook" --port
/ <base+2>` (rdb1).

/ pull in uqf's own src/init.q (loads .qbook/.qfwd/... - see UQFROOT in
/ core.py's build_env) FIRST - level_groups below calls .qbook.
/ derive_level_groups at load time, so .qbook has to already exist.
/ init.q's own \l lines are repo-root-relative (`\l src/stats.q`, ...),
/ and torq.sh doesn't launch us from the repo root, so cd there for the
/ load and back again immediately after - system"cd ..." is q's own
/ builtin chdir, not a subshell, so it sticks across the two calls. Same
/ trick as torq_cross_etl.q.
{[uqfroot]
  cwd:first system"pwd";
  system"cd ",uqfroot;
  system"l src/init.q";
  system"cd ",cwd;
 }[getenv[`UQFROOT]];

\d .vec

/ mirror of torq_wide_book_feed.q's `wide_book` schema (see
/ WIDE_BOOK_TABLE_SCHEMA in python/torq_orchestrator/src/torq_orchestrator/
/ core.py) - used only to derive level_groups below (upd receives the
/ real table straight off the subscription, not built from this). Built
/ from til/flip rather than 24 hand-written column defs, same level-0-
/ first bids0..bids10/asks0..asks10 naming .qbook.derive_level_groups
/ expects.
level_names:(`$("bids",/:string til 11)),(`$("asks",/:string til 11));
wide_book:flip (`time`sym,level_names)!(`timestamp$();`g#`symbol$()),(count[level_names]#enlist `float$());

/ target_col!ordered_source_cols for .qbook.fold_level_columns, derived
/ once from wide_book's own column names rather than hand-listed.
level_groups:.qbook.derive_level_groups[cols wide_book;(("bids";`bid_prices);("asks";`ask_prices))];

\d .

/ receive wide_book ticks from the tickerplant subscription - x arrives
/ here as an actual table already matching .vec.wide_book's schema (not a
/ bare list-of-columns the way cross1's upd assumed; confirmed live via
/ `type x` = 98h) - so it's fed into book_from_wide_levels directly, no
/ reconstruction needed. Fold each batch, then republish it onto the
/ tickerplant as mkt_orderbook (h, set up at the bottom) rather than
/ keeping it in a private local table - .u.upd stamps its own time
/ (`.z.p`) on receipt, same as every other feed here, so the folded
/ table's own `time` column is dropped before publishing.
upd:{[t;x]
  if[t=`wide_book;
    folded:.qbook.book_from_wide_levels[x;.vec.level_groups;`sym];
    h (`.u.upd;`mkt_orderbook;folded`sym`bid_prices`ask_prices);
  ];
 }

\d .vec

tickerplanttypes:`segmentedtickerplant
requiredprocs:tickerplanttypes
tpconsleep:10
tpcheckcycles:0W

subscribe:{
  if[0=count s:.sub.getsubscriptionhandles[tickerplanttypes;();()!()];:()];
  subproc:first s;
  .lg.o[`subscribe;"subscribing to ",string subproc`procname];
  .sub.subscribe[`wide_book;`;0b;0b;subproc]
 }

init:{
  .servers.startupdepcycles[requiredprocs;tpconsleep;tpcheckcycles];
  subscribe[];
 }

\d .

/ same reasoning as torq_cross_etl.q: a real .sub.subscribe subscriber
/ needs .servers.startup[] to open a live, access-listed handle to stp1 -
/ vectorize1's proctype "metrics" in process.csv (core.py) borrows an
/ already-credentialed type for that, same as cross1.
.servers.CONNECTIONS:.vec.requiredprocs;
.servers.startup[];
.vec.init[];

/ separate, unauthenticated publish handle to stp1 - same
/ .servers.gethandlebytype pattern torq_fx_feed.q/torq_quotes_feed.q/
/ torq_wide_book_feed.q use, independent of the .servers.startup[]
/ subscription handle above. Safe to acquire now: .vec.init[] (just
/ above) already blocked until stp1 was confirmed up.
h:.servers.gethandlebytype[`segmentedtickerplant;`any];
