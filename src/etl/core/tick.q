/ tick.q - a pub/sub tickerplant in stock kdb+, with no TorQ (.qetl.tick).
/ .
/ WHY THIS EXISTS. Every streaming job in this tree is already TorQ-free:
/ a job calls `publish` in its own namespace and .qetl.job.stream.wire points that
/ at something. But the only thing that ever wired it was
/ scripts/processes/torq_stream.q, and the only pub/sub in the repository
/ is TorQ's own u.q under lib/. So a job's CODE did not need TorQ while
/ RUNNING one did, which made "TorQ-free" a claim about the source tree
/ rather than about the service. This file closes that gap: it is the
/ ~200 lines of tickerplant the seam was always waiting for, and with it
/ any registered job runs under bare q.
/ .
/ It is deliberately NOT a replacement for TorQ. No discovery, no process
/ manager, no HDB writedown, no chained plants, no access control - TorQ
/ does all of that and does it better. What is here is the part a single
/ service needs to run on its own: subscribe, publish, log, replay.
/ .
/ THE INVARIANTS ARE TorQ'S, ON PURPOSE. A job must behave identically
/ whichever plant carries it, so the three rules that bite in this
/ repository are the same three rules here:
/ .
/   1. the PLANT stamps `time`, never the publisher - and a publisher that
/      sends one anyway has it STRIPPED and replaced, silently. That is
/      .qtorq.publish's behaviour, and matching it is the point: a job
/      developed against the TorQ stack must not fail on first contact
/      with this plant, which is the whole reason these invariants are
/      TorQ's rather than invented here.
/      .
/      Forgiveness with a cost, worth naming: a source's OWN event time,
/      if it is called `time`, is discarded here without a word. That is
/      why the normalizers name theirs `source_time`, and why a schema
/      that means "when it happened" should never spell it `time`.
/   2. keyed tables are refused. A tickerplant appends; upserting by key
/      silently drops the history that makes a tick log a tick log.
/   3. the row count comes from column length, so every column must be a
/      LIST. A scalar column makes a one-row batch look like a
/      one-COLUMN batch, which is the single most common way a feed
/      written against this shape goes wrong.
/ .
/ THE SINK IS A CALLABLE, which is what makes this testable. A real
/ subscriber is registered as `neg h` and q's own IPC send does the work;
/ a test registers a function and reads what would have gone out. Both
/ are applied the same way - sink[(`upd;tbl;rows)] - because applying an
/ integer handle to a message IS the async send in q, so one code path
/ serves both and the tested path is the shipped one.

\d .qetl.tick

/ ------------------------------------------------------------- THE STATE

/ Who wants what. One row per subscription, not per subscriber: a process
/ that wants two tables is two rows, which makes the fan-out for a table a
/ select rather than a scan through nested lists.
subscribers:([] sink:(); tbl:`symbol$(); registered:`timestamp$())

/ Table name -> its empty schema, learned from the first batch published
/ to it or declared up front by schema.
/ .
/ A plant that knows its schemas can refuse a batch of the wrong shape at
/ the door, where the publisher's name is still known, rather than letting
/ it land and be found by whoever queries the table next.
schemas:(`symbol$())!()

/ The open log file's handle and how many messages have gone into it.
/ Both null until open_log.
log_handle:0N
log_path:`
msg_count:0

/ ---------------------------------------------------------- SUBSCRIPTION

/ Declare a table's schema before anything publishes to it.
/ .
/ Optional - a first publish learns the schema anyway - but a plant that
/ is told up front can refuse the first bad batch as well as the second,
/ and a subscriber that connects before any data flows gets the schema it
/ needs to build its own empty table.
/ @param table_name the table name
/ @param empty an empty table of the right shape, `time` first
/ @return the table name
/ @throws error when empty is not a table, or is keyed
/ @eg .qetl.tick.schema[`eg_quote;([] time:`timestamp$(); sym:`symbol$(); bid:`float$())] -> `eg_quote
schema:{[table_name;empty]
    if[not 98h=type empty;
        '"schema: ",string[table_name],"'s schema must be an unkeyed table"];
    if[count empty;
        '"schema: ",string[table_name],"'s schema must be EMPTY - it declares a shape, not data"];
    if[not `time=first cols empty;
        '"schema: ",string[table_name],"'s first column must be `time` - the plant stamps it there, and a schema that omits it describes rows nobody will ever receive"];
    schemas[table_name]:empty;
    table_name}

/ Subscribe a sink to one or more tables.
/ .
/ Returns the schemas of what was subscribed to, the way a tickerplant
/ does: a subscriber needs to build its own tables, and a second round
/ trip to ask for them is a race against the first batch.
/ @param want the table names, or ` for every table the plant knows
/ @param sink where batches go - `neg h` for a real subscriber, a function in a test
/ @return a dict of table name -> empty schema, for the tables subscribed to
/ @throws error when the sink is not callable, or a named table is unknown
/ @eg .qetl.tick.reset[]; .qetl.tick.schema[`eg_t;([] time:`timestamp$(); a:`long$())]; key .qetl.tick.subscribe[`eg_t;{[m] m}] -> enlist `eg_t
subscribe:{[want;sink]
    if[not can_send sink;
        '"subscribe: a sink must be callable as sink[(`upd;table;rows)] - a function, or `neg h` for a real subscriber"];
    t:$[(11h=type want) or -11h=type want; (),want; '"subscribe: tables must be a symbol or symbol list, or ` for all"];
    t:$[t~enlist `; key schemas; t];
    unknown:t where not t in key schemas;
    if[count unknown;
        '"subscribe: no schema for ",(", " sv string unknown)," - declare it with .qetl.tick.schema before subscribing, so a subscriber cannot wait forever on a typo"];
    `.qetl.tick.subscribers upsert ([] sink:(count t)#enlist sink; tbl:t; registered:(count t)#.z.p);
    t!schemas t}

/ Drop every subscription held by a sink - what a process calls from .z.pc
/ when a subscriber's connection drops.
/ .
/ Matching on ~ rather than =: a sink may be a function, and = on two
/ functions is not a comparison q will do.
/ @param sink the sink to remove
/ @return how many subscriptions went
/ @eg .qetl.tick.reset[]; .qetl.tick.unsubscribe[{[m] m}] -> 0
unsubscribe:{[sink]
    gone:count where {[s;row] row[`sink]~s}[sink] each subscribers;
    `.qetl.tick.subscribers set subscribers where not {[s;row] row[`sink]~s}[sink] each subscribers;
    gone}

/ Private: can this value be applied to a message?
/ .
/ A function (100-112h) or an INTEGER HANDLE - applying a negative handle
/ to a message is q's own async send, so a real subscriber and a test
/ recorder are called by identical code.
can_send:{[sink] ((type sink) within 100 112h) or (type sink) in -6 -7h}

/ ------------------------------------------------------------- PUBLISHING

/ Private: refuse a batch that a tickerplant cannot carry, naming the rule
/ it breaks. See the header for why each of these is a rule.
require_batch:{[t;x]
    if[99h=type x;
        '"publish: ",string[t]," was given a KEYED table - a tickerplant appends, and upserting by key would silently drop ticks"];
    if[98h=type x; :1b];
    if[0h<>type x;
        '"publish: ",string[t],"'s rows must be a table, or a list of one column vector each"];
    if[not all 0<=type each x;
        '"publish: ",string[t]," has a column that is an ATOM - the row count comes from column length, so a one-row batch of atoms reads as a one-column batch"];
    1b}

/ Publish a batch: stamp it, log it, and fan it out to whoever wants it.
/ .
/ The equivalent of .u.upd, and named `publish` rather than `upd` because
/ nothing here is called by a remote over a wire the way .u.upd is - the
/ runner installs the root `upd` that forwards to this.
/ .
/ THE ORDER IS LOG, THEN SEND. A message that reached a subscriber but not
/ the log cannot be replayed after a restart, so the subscriber's state
/ and the plant's history disagree and nothing says so. The other way
/ round, a crash between the two costs a resend, which recovery handles.
/ @param t the table name
/ @param x a table, or a list of one column vector per column
/ @return the number of rows published
/ @throws error naming the invariant a malformed batch breaks
/ @eg .qetl.tick.reset[]; .qetl.tick.schema[`eg_t;([] time:`timestamp$(); a:`long$())]; .qetl.tick.publish[`eg_t;enlist enlist 1] -> 1
publish:{[t;x]
    require_batch[t;x];
    / `time` FIRST, matching every declared schema in
    / scripts/processes/uqs_tables.q and .u.upd's own convention. A
    / plant that appended it instead would build tables whose columns are
    / one position out from everything else in this repository.
    now:.z.p;
    / Invariant 1: strip a publisher-supplied `time` rather than refusing
    / it, so this plant and .qtorq treat the same mistake the same way.
    / Only the table form can carry one - the list-of-columns form has no
    / names to check.
    / .
    / A NESTED cond, not `(98h=type rows) and `time in cols rows`: q's `and`
    / does not short-circuit, so the single-condition spelling evaluates
    / `cols` on every batch, and `cols` of a list of column vectors throws
    / `type`. The $[c;v;c;v;else] form does short-circuit between pairs.
    x:$[98h<>type x; x;
        `time in cols x; ![x;();0b;enlist `time];
        x];
    stamped:$[98h=type x;
        ([] time:(count x)#now) ,' x;
        (enlist (count first x)#now),x];
    if[(not t in key schemas) and 98h=type stamped; schemas[t]:0#stamped];
    / ONE canonical form - a table - logged and sent. The list-of-columns
    / spelling is a convenience for feeds and it stops here.
    / .
    / Logging the raw form instead is a bug with a long fuse: a feed that
    / publishes columns would have its columns in the log and its TABLE on
    / the wire, so a subscriber's live path and its recovery path receive
    / different shapes. Everything works until the day something restarts.
    batch:$[98h=type stamped; stamped; learn[t;stamped]];
    record (`upd;t;batch);
    fan_out[t;batch];
    count batch}

/ Private: build a table from a declared schema's column names and a list
/ of column vectors, so a plant that was told its schema can hand
/ subscribers a table rather than a bare list.
learn:{[table_name;stamped]
    if[not table_name in key schemas;
        '"publish: ",string[table_name]," was published as a list of columns and has no declared schema, so the plant cannot name them - call .qetl.tick.schema first"];
    flip (cols schemas table_name)!stamped}

/ Private: send one batch to every sink subscribed to that table.
fan_out:{[name;batch]
    / `where tbl=name`, never `where tbl=tbl`: inside a qSQL clause the
    / column shadows a same-named parameter, so the second spelling
    / compares the column with itself, is true for every row, and sends
    / every table to every subscriber. It reads correct and is not.
    dst:exec sink from subscribers where tbl=name;
    {[name;batch;sink] sink (`upd;name;batch)}[name;batch] each dst;
    count dst}

/ ---------------------------------------------------------------- THE LOG

/ Open (or reopen) the log for a given day, and say how many messages are
/ already in it.
/ .
/ Appending to an existing log rather than truncating it is what makes a
/ restart mid-day recoverable: the messages already written are the ones
/ replay will hand back.
/ @param dir the directory to log into - created if absent
/ @param name the log's base name, e.g. `fxpositions
/ @param dt the date the log is for
/ @return the number of messages already in the log
/ @throws error when the directory cannot be created
/ @eg .qetl.tick.reset[] -> `
open_log:{[dir;name;dt]
    d:$[10h=abs type dir; dir; string dir];
    system "mkdir -p ",d;
    path:hsym `$d,"/",string[name],ssr[string dt;".";""];
    / -11!(-2;path) counts the messages and checks the log is not
    / truncated. Bare -11!path REPLAYS them, calling whatever `upd` is
    / installed, and -11!(-1;path) still resolves `upd` and so throws in a
    / process that has none. Only the -2 form reads a log without needing
    / a handler, which is what opening one should do.
    existing:$[() ~ key path; 0; -11!(-2;path)];
    if[() ~ key path; path set ()];
    `.qetl.tick.log_path set path;
    `.qetl.tick.log_handle set hopen path;
    `.qetl.tick.msg_count set existing;
    existing}

/ Private: append one message to the log, when there is one to append to.
/ .
/ A plant with no log still publishes - that is the shape a test wants,
/ and a service that genuinely does not need recovery should be able to
/ say so by not opening one, rather than by writing to /dev/null.
record:{[msg]
    if[null log_handle; :0];
    log_handle enlist msg;
    `.qetl.tick.msg_count set msg_count+1;
    msg_count}

/ Replay a log through a handler, returning how many messages it carried.
/ .
/ WHY THE HANDLER IS INSTALLED AT ROOT. q's own -11! streaming replay
/ calls `upd` at root by name - it is not given a function. So this saves
/ whatever `upd` is there, installs the handler, replays, and puts the old
/ one back, which is what lets a service replay its own log inside a
/ process that is already running one.
/ .
/ A REPLAYED MESSAGE ALREADY HAS ITS `time`, because the plant stamped it
/ on the way in. That is the point of replaying rather than re-publishing:
/ recovery has to reproduce the original stamps, not invent new ones.
/ @param path the log file, as an hsym
/ @param handler a function of (table name; rows), called per message
/ @return the number of messages replayed, or 0 when the log does not exist
/ @throws error when the handler is not callable
/ @eg .qetl.tick.replay[hsym `$"/nonexistent/log";{[t;r] r}] -> 0
replay:{[path;handler]
    if[not can_send handler; '"replay: the handler must be callable as handler[table;rows]"];
    if[() ~ key path; :0];
    n:-11!(-2;path);
    if[0=n; :0];
    saved:$[`upd in key `.; enlist get `upd; ()];
    `upd set handler;
    / Restore the previous root upd whether the replay finished or threw:
    / leaving a replay handler installed would send live traffic into
    / recovery code, which is worse than the failure that caused it.
    r:@[{[path] -11!path; 1b}; path; {[e] e}];
    $[count saved; `upd set first saved; ![`.;();0b;enlist `upd]];
    if[not 1b~r; '"replay: ",$[10h=type r; r; .Q.s1 r]];
    n}

/ ----------------------------------------------------------------- RESET

/ Forget every subscription, schema and log handle - what a test calls
/ between cases, and what a process calls at start of day.
/ @return the log path that was closed, or ` when none was open
/ @eg .qetl.tick.reset[] -> `
reset:{[]
    was:log_path;
    if[not null log_handle; hclose log_handle];
    `.qetl.tick.subscribers set 0#subscribers;
    `.qetl.tick.schemas set (`symbol$())!();
    `.qetl.tick.log_handle set 0N;
    `.qetl.tick.log_path set `;
    `.qetl.tick.msg_count set 0;
    was}

\d .
