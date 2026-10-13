/ fx_positions.q - the whole of the FX positions service
/ (.qpipe.job.fx_positions).
/ .
/ Subscribes to `executions`, nets every fill into a running book keyed on
/ (sym, book, product), and on a timer publishes two things: a
/ `fx_position` snapshot of the whole book, and a `fx_limit_breach` row
/ for each limit newly crossed.
/ .
/ Modelled on Data Intellect's TorQ FX positions engine, built on this
/ repository's own machinery instead - and it runs with no TorQ at all,
/ through .qetl.tick and scripts/processes/run_stream.q.
/ .
/ WHY IT SNAPSHOTS ON A TIMER RATHER THAN PER BATCH. A position is a
/ STATE, and republishing the whole book on every order would write a
/ snapshot per tick - the same row over and over for every pair that did
/ not trade. The timer decouples how often the desk wants to see the book
/ from how fast the market happens to be going, which is what a snapshot
/ is for. Breaches go out on the same timer for the same reason, and are
/ throttled on top of it (see .qlimit.throttle).
/ .
/ WHY IT IS NOT posbook. .qpipe.job.posbook answers "what did we make", per
/ sym, at weighted-average cost, marked to mid. This answers "what are we
/ holding", along the dimensions a desk reports on, with no marks and no
/ P&L - over the SAME fills (#885): both read `executions`, the one tape
/ every fill reaches, so desk exposure and desk P&L net one population and
/ reconcile per sym. A fill with no book or product - an FX `trades` fill,
/ a crypto fill - is held under null dimensions rather than dropped, which
/ is what keeps the totals equal. See src/portfolio/desk_positions.q for
/ why one module cannot answer both questions.
/ .
/ WHAT IS IN THIS FILE: the schemas, the netting transform with its
/ examples, the batch handler, the timer, the state, and the declaration
/ the runner reads.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - the plant stamps it (invariant 1).

\d .qpipe.job.fx_positions

/ ------------------------------------------------------------- THE SHAPES

/ The fills, narrowed to what netting reads. Not named `executions`: that
/ name is held to the plant's whole table (tests/q/test_stack_tables.q).
desk_fills:.qetl.plant.columns[`executions;`time`sym`side`size`price`book`product]
desk_book:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    base_qty:`float$(); quote_qty:`float$(); fill_count:`long$())
fx_position:.qetl.plant.published `fx_position
fx_limit_breach:.qetl.plant.published `fx_limit_breach

/ The dimensions the book is keyed on - the blog's (currency_pair,
/ product, book), in this repository's own column names.
dimensions:`sym`book`product

/ ---------------------------------------------------------- THE TRANSFORM

/ Net a batch of fills into a desk book.
/ .
/ The book is an INPUT and the new book is the output, never a global this
/ mutates - which is what lets a batch against a given book have an
/ expected answer, and is the convention every job here follows.
/ .
/ Every row is a fill: the executions normalizer keeps only the filled
/ orders, so a cancel never reaches here.
/ @param book the current desk book, unkeyed
/ @param batch executions rows
/ @return the new desk book, unkeyed
net_fills:{[book;batch]
    updated:.qdesk.apply_fills[
        .qpipe.job.fx_positions.dimensions xkey book;
        select sym, book, product, side, size, price from batch];
    `sym`book`product xasc 0!updated}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qetl.job.stream.wire points it at a tickerplant
/ (a runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `fx_positions;

/ The running book, keyed on its dimensions. Only ever changed through
/ net_fills, which has expected tables.
positions:`sym`book`product xkey desk_book;

/ The desk's limits. Empty until something loads them - a service with no
/ limits reports positions and polices nothing, which is a legitimate way
/ to run and better than inventing caps nobody agreed to.
/ .
/ Scoped on the same three dimensions as the book, so a limit is written
/ per pair per book per product. A cap on a total is the same table at a
/ coarser scope, evaluated against .qdesk.rollup - see limits.q - but the
/ scope always keeps sym: base_qty and quote_qty are in each pair's own
/ currencies, so a total across pairs would add currencies (#1260).
limits:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    metric:`symbol$(); cap:`float$(); severity:`symbol$());

/ The metrics the limits are allowed to police. Named rather than "every
/ numeric column", so adding a column to the book cannot silently start
/ policing it - see .qlimit.measure.
policed:`base_qty`quote_qty

/ How long a breach stays quiet after alerting. A breach is a state that
/ persists until someone trades out of it, so without this the desk gets
/ one alert per timer tick and stops reading them.
alert_period:0D00:05;

/ Throttle state: breach identity -> when it was last alerted. Rebuilt on a
/ restart from this job's own fx_limit_breach rows in the replayed log
/ (restore_throttle, #1197), so a standing breach published inside the
/ period before the restart is not published again until the period is out.
alerts:.qlimit.no_alerts[];

/ Fold replayed fx_limit_breach rows into the throttle state: each breach
/ identity is remembered at the newest plant `time` it was published at.
/ .
/ The identity is built exactly as fresh_breaches builds it (the dimensions
/ and the metric, rendered by .qlimit.identity) and the time is the plant's
/ stamp on the row, the moment the breach was last published, so after the
/ restart `.qlimit.throttle` sees what it saw before it: quiet until the
/ period has run from that stamp. Merged with `|` so a state already held is
/ never moved back in time. Only a replay folds: live, a row of this table is
/ the job's own echo and the throttle already knows it.
/ @param x fx_limit_breach rows as the plant logged them, with `time`
/ @return the number of identities now held
/ @eg .qpipe.job.fx_positions.restore_throttle 0#.qpipe.job.fx_positions.fx_limit_breach  ->  count .qpipe.job.fx_positions.alerts
restore_throttle:{[x]
    if[(0=count x) or not `time in cols x; :count .qpipe.job.fx_positions.alerts];
    keyed:.qpipe.job.fx_positions.dimensions,`metric;
    ids:.qlimit.identity ?[x;();0b;keyed!keyed];
    seen:exec max time by id from ([] id:ids; time:x`time);
    `.qpipe.job.fx_positions.alerts set .qpipe.job.fx_positions.alerts|seen;
    count .qpipe.job.fx_positions.alerts}

/ Load a limits table, refusing a malformed one at load rather than at the
/ first breach - a limits table that polices nothing looks exactly like a
/ quiet day.
/ .
/ The SCOPE is the table's dimension columns: all of sym, book and product
/ to police each position, fewer to police a total - a table of sym alone
/ caps each pair across every book. fresh_breaches rolls the book up to
/ that scope before comparing. It used to compare per position whatever
/ the scope, so a sym cap of 10mm passed 6mm in each of two books (#733).
/ The scope must keep sym: the quantities are in each pair's own
/ currencies, so a total across pairs adds currencies together (#1260).
/ A NULL scope value is refused for the same reason: a null book matches
/ no position, and to mean "every book" the column is left out instead.
/ @param limits the limits table, scoped on sym and optionally book and product
/ @return the number of limits loaded
/ @throws error naming a malformed limit, a scope column that is not one of this book's dimensions, or a scope without sym
/ @eg .qpipe.job.fx_positions.load_limits[([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot; metric:enlist `base_qty; cap:enlist 1e6; severity:enlist `hard)] -> 1
load_limits:{[limits]
    .qlimit.require_limits limits;
    / .qlimit cannot check this: it is not told which columns are meant to
    / be scope, so a stray column simply becomes one and the limit matches
    / nothing. THIS file knows the dimensions, so it can say so at load -
    / and a limit that matches nothing is exactly the failure that looks
    / like a quiet day.
    scope:.qlimit.scope_cols limits;
    stray:scope where not scope in .qpipe.job.fx_positions.dimensions;
    if[count stray;
        '"load_limits: ",(", " sv string stray)," is not a dimension of this book, so a limit carrying it would be scoped on something no position has - the dimensions are ",
         ", " sv string .qpipe.job.fx_positions.dimensions];
    if[0=count scope;
        '"load_limits: a limit needs at least one of ",(", " sv string .qpipe.job.fx_positions.dimensions)," to say what it caps"];
    / base_qty is in each pair's base currency and quote_qty in its quote
    / currency, so only a scope that keeps sym sums one currency. Without
    / it a book cap added EUR to USD and USD to JPY (#1260). This job holds
    / no rates to revalue with - no marks, by design (desk_positions.q) -
    / so the limit is refused rather than compared with a meaningless sum.
    if[not `sym in scope;
        '"load_limits: a limit without sym would sum base_qty and quote_qty across pairs, adding different currencies - write the cap per sym"];
    blank:scope where {[limits;c] any null limits c}[limits] each scope;
    if[count blank;
        '"load_limits: ",(", " sv string blank)," is null on some limit, which matches no position - leave the column out to cap the total across it"];
    `.qpipe.job.fx_positions.limits set limits;
    count limits}

/ The limits file a running process loads (#1112): a CSV with a header, one
/ row per limit - the scope columns it caps on (sym, and any of book and
/ product), then metric, cap and severity. Read as symbols but cap, and
/ handed to load_limits, so a malformed file is refused as a malformed table
/ would be. An empty scope cell is null, which load_limits refuses.
/ @param path the file, as a string
/ @return the limits table
/ @throws error naming a file that does not exist
/ @eg .qpipe.job.fx_positions.read_limits "no/such/fx_limits.csv"  ->  throws
read_limits:{[path]
    f:hsym `$path;
    if[()~key f; '"read_limits: ",path," does not exist - UQF_FX_POSITION_LIMITS names the limits file"];
    hdr:`$"," vs first read0 f;
    (("SF" `cap=hdr);enlist ",") 0: f}

/ Load the limits before the job runs (#1112). Nothing else does: a limits
/ table is configuration, and a restart would otherwise start policing
/ nothing, which looks exactly like a quiet day - so the file named by
/ UQF_FX_POSITION_LIMITS is loaded here, and no file is said out loud.
/ @return the number of limits loaded
/ @throws error when the file is missing or malformed: the job does not start
on_start:{[]
    path:getenv `UQF_FX_POSITION_LIMITS;
    if[count path; load_limits[read_limits[path]]];
    if[0=count .qpipe.job.fx_positions.limits;
        .qetl.log.warn[`fx_positions;"no limits loaded - fx_limit_breach stays empty and nothing is policed. Name a limits CSV in UQF_FX_POSITION_LIMITS, or load one over IPC with load_limits";
            enlist[`variable]!enlist "UQF_FX_POSITION_LIMITS"]];
    count .qpipe.job.fx_positions.limits}

/ Net one batch of fills into the book.
/ .
/ The book is replaced BEFORE anything is published, the way posbook does
/ it: fills will not be redelivered, so a failed publish must not also
/ lose them from the position.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    / The job's own output, read back only by a replay (restore_from): the
    / throttle must know what was published before the restart (#1197).
    if[t=`fx_limit_breach;
        if[.qetl.job.stream.replaying; .qpipe.job.fx_positions.restore_throttle[x]];
        :()];
    if[not t=`executions; :()];
    if[0=count x; :()];
    updated:.qetl.transform.apply[`fx_positions;`book`executions!(
        0!.qpipe.job.fx_positions.positions;
        select time, sym, side, size, price, book, product from x)];
    `.qpipe.job.fx_positions.positions set `sym`book`product xkey updated;
    }

/ The breaches this book is in, as of now, after throttling.
/ .
/ Separated from on_timer so that the decision - which breaches are worth
/ sending - can be tested without a timer and without a publisher. The
/ throttle state is read and written here because it is state that only
/ makes sense across calls; everything it depends on is an argument.
/ @param now the time to throttle against
/ @return the breaches to publish, possibly none
fresh_breaches:{[now]
    if[0=count .qpipe.job.fx_positions.limits; :0#.qpipe.job.fx_positions.fx_limit_breach];
    / Measured at the limits' own scope: a cap on a total is compared with
    / the total, not with each position under it.
    scope:.qlimit.scope_cols .qpipe.job.fx_positions.limits;
    rolled:.qdesk.rollup[.qpipe.job.fx_positions.positions;scope];
    measured:.qlimit.measure[rolled;.qpipe.job.fx_positions.policed];
    breaches:.qlimit.evaluate[measured;.qpipe.job.fx_positions.limits];
    / A breach always names every dimension, null where it covers them all,
    / so fx_limit_breach keeps one shape and the throttle one identity. The
    / dimensions lead, in the book's order, so the identity renders the same
    / whatever order the limits file named its scope in - restore_throttle
    / builds it from the published row, which is always in that order.
    breaches:{[t;c] $[c in cols t; t; ![t;();0b;(enlist c)!enlist enlist `]]}/[breaches;.qpipe.job.fx_positions.dimensions];
    breaches:(.qpipe.job.fx_positions.dimensions,cols[breaches] except .qpipe.job.fx_positions.dimensions)#breaches;
    r:.qlimit.throttle[.qpipe.job.fx_positions.alerts;breaches;now;.qpipe.job.fx_positions.alert_period];
    `.qpipe.job.fx_positions.alerts set r`state;
    r`alerts}

/ Publish a snapshot of the whole book, and any newly breached limit.
/ .
/ THE WHOLE BOOK, every tick, not just what moved. A snapshot that carried
/ only the changed rows would need its reader to keep the rest, which is
/ the reader building a second copy of this service's state and getting it
/ wrong on the first dropped message. The book is one row per (pair, book,
/ product) and a desk has hundreds, not millions.
/ @return nothing
on_timer:{[]
    snapshot:0!.qdesk.break_even .qpipe.job.fx_positions.positions;
    if[count snapshot;
        .qpipe.job.fx_positions.publish[`fx_position;
            select sym, book, product, base_qty, quote_qty, fill_count, break_even from snapshot]];
    breaches:.qpipe.job.fx_positions.fresh_breaches[.z.p];
    if[count breaches;
        .qpipe.job.fx_positions.publish[`fx_limit_breach;
            select sym, book, product, metric, observed, cap, severity, utilisation from breaches]];
    }

\d .

.qetl.transform.define[`fx_positions;`inputs`output`fn`examples!(
    `book`executions!(.qpipe.job.fx_positions.desk_book;.qpipe.job.fx_positions.desk_fills);
    .qpipe.job.fx_positions.desk_book;
    .qpipe.job.fx_positions.net_fills;
    (
    / From an empty book: a buy and a sell of the same pair on one book net
    / against each other, a second book is a row of its own rather than
    / being folded into the first, and a fill with no book or product - an
    / FX `trades` fill - is held under null dimensions, not dropped.
    `inputs`expected!(
        `book`executions!(
            .qpipe.job.fx_positions.desk_book;
            ([] time:2026.09.17D10:00:00+0D00:00:01*til 4;
                sym:`EURUSD`EURUSD`EURUSD`EURUSD;
                side:1 -1 1 1;
                size:1000000 400000 500000 250000f;
                price:1.0850 1.0860 1.0855 1.0851;
                book:`london`london``newyork;
                product:`spot`spot``spot));
        ([] sym:`EURUSD`EURUSD`EURUSD; book:``london`newyork; product:``spot`spot;
            base_qty:500000 600000 250000f; quote_qty:-542750 -650600 -271275f; fill_count:1 2 1));
    / Against an existing book: a fill in a product the book has never seen
    / opens a row rather than being dropped, and the pair already there is
    / added to rather than replaced.
    `inputs`expected!(
        `book`executions!(
            ([] sym:enlist `USDJPY; book:enlist `london; product:enlist `spot;
                base_qty:enlist 1000000f; quote_qty:enlist -149500000f; fill_count:enlist 1);
            ([] time:2026.09.17D10:00:05 2026.09.17D10:00:06;
                sym:`USDJPY`USDJPY;
                side:-1 1;
                size:400000 250000f;
                price:149.60 149.55;
                book:`london`london;
                product:`spot`fwd));
        ([] sym:`USDJPY`USDJPY; book:`london`london; product:`fwd`spot;
            base_qty:250000 600000f; quote_qty:-37387500 -89660000f; fill_count:1 2))
    ))];

/ replay 1b: on a restart the book is rebuilt from the day's log - its
/ opening book, then its executions - not started flat, under TorQ as well
/ as run_stream.q.
/ Positions carry across days (#943): the shell publishes the book onto
/ fx_position_open at end of day and restores it on replay (carry, #963).
/ The breach throttle is rebuilt from the job's own fx_limit_breach rows in
/ the log (restore_from, #1197), so a restart does not re-publish a standing
/ breach inside its period.
/ The limits are loaded at start (on_start) and watched, so a change over
/ IPC, or a restart that drops one, lands in config_change (#1112).
.qetl.cfg.audit.watch[`fx_positions;enlist `.qpipe.job.fx_positions.limits];
.qetl.job.stream.define[`fx_positions;`procname`subscribe_to`publishes`on_batch`period`on_timer`on_start`start_with_all`replay`restore_from`carry`note`state!(
    `fxpositions1;
    enlist `executions;
    `fx_position`fx_limit_breach`fx_position_open`config_change;
    .qpipe.job.fx_positions.on_batch;
    0D00:00:05.000;
    .qpipe.job.fx_positions.on_timer;
    .qpipe.job.fx_positions.on_start;
    1b;
    1b;
    enlist `fx_limit_breach;
    `state`table!(`positions;`fx_position_open);
    "net exposure by (sym, book, product) with limit breaches. Runs here AND standalone under processes/run_stream.q on stock kdb+ - a job is TorQ-free code and the runner decides the transport, so being runnable without TorQ is no reason not to be startable with it";
    `positions`limits`alerts)];
