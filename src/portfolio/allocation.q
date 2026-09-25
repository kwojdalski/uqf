/ allocation.q - P&L attribution: which opening trade paid for which close,
/ and what each trade earned (.qalloc). Requires risk.q (pnl) to be loaded
/ first.
/ .
/ WHY THIS IS NOT positions.q. .qpos tracks a book weighted-average-cost:
/ apply_fill realises P&L at the *running average* price and keeps no lots.
/ That is the right model for a position book - O(1) per fill, and it
/ answers "where am I now" - but it has already thrown away what
/ attribution needs. Once two buys have merged into one average there is no
/ longer a fact of the matter about which of them a later sell closed, so
/ FIFO and LIFO cannot be recovered from a book after the fact. They have
/ to be computed from the trades, which is what this module does.
/ .
/ WHAT COMES OUT. A match ledger: one row per (opening trade, closing
/ trade, matched quantity), carrying both prices and the realised P&L of
/ that slice. Everything else here is a rollup of that one table - by
/ trade, by sym, by anything - because the ledger is the finest grain at
/ which realised P&L is a fact rather than an allocation.
/ .
/ THREE AXES OF FLEXIBILITY, all declared rather than hardcoded.
/ .
/ 1. METHOD. A registry of matching strategies in the dict-of-functions
/    idiom .qsrc and .qio already use here. A method is two functions: how
/    an opening trade joins the lot queue (`open`), and which lot the next
/    close consumes (`pick`). That factoring is what makes fifo, lifo, hifo
/    and weighted four lines each rather than four engines - and it is why
/    a desk can register a fifth without touching this file.
/ .
/ 2. BUCKET (`by`). Which columns define an independent matching queue.
/    `sym by default, but two strategies trading the same pair must not
/    match against each other, so a desk that books a strategy column can
/    pass `sym`strategy and get separate queues for free.
/ .
/ 3. FILTERS. Two of them, and conflating them is the mistake this module
/    exists to avoid:
/      universe - applied BEFORE matching. It changes which trades exist,
/                 so it changes which lot each close hits and therefore
/                 changes the answer. "What would this book have earned if
/                 only these trades had happened."
/      where    - applied AFTER matching. The full book is matched, then
/                 the ledger is sliced. "Of what actually happened, show me
/                 this part." The numbers still add up to the book total.
/    A one-argument function in both cases, so any q predicate works;
/    by_cols builds the common column-in-values one declaratively.
/ .
/ THE BOOK IS NOT FLAT AT THE START, AND NOT ONLY AT THE END. A day's
/ trades are a slice of a position that predates them and outlives them,
/ so attribution has to be able to enter and leave mid-position:
/   opening - lots carried in, seeded into each bucket's queue ahead of
/             every trade. Without it a sell that closes yesterday's buy
/             finds an empty queue and opens a short, which is not a
/             smaller answer but a wrong one.
/   asof    - the trades are truncated at an instant, so residual is the
/             position AS OF that moment and the ledger is what had been
/             realised by then. position and position_at roll that up into
/             a book, which is the same object .qpos carries - and
/             test_allocation.q holds the two to agreeing.
/ .
/ CONSERVATION. Once a bucket is flat, every method realises the same total
/ P&L - they disagree only about which trade gets credited, and about what
/ is still open while the book is not flat. test_allocation.q asserts this,
/ because a matching engine that fails it is not attributing, it is
/ inventing money.

\d .qalloc

/ --------------------------------------------------------------- METHODS

/ The keys a matching method must carry. `why is optional and defaults to
/ "", but every STORED method has all three: q collapses a dictionary whose
/ values are same-keyed dicts into a table, after which assigning a
/ differently-keyed dict throws `type - the same trap .qsrc.define
/ normalises row_key around. Fixing the shape at registration keeps every
/ method mutually assignable.
required:`open`pick

/ Refuse a method that is not one, naming what is wrong.
/ .
/ Checked at REGISTRATION rather than at first use, so a malformed method
/ fails on the line that declares it rather than halfway through a ledger.
/ @param decl the candidate method
/ @return 1b when acceptable
/ @throws error naming the missing key, the unknown key, or the key whose value is not a function
/ @eg .qalloc.require_method[`open`pick!(.qalloc.append_lot;.qalloc.pick_first)] -> 1b
require_method:{[decl]
    if[not 99h=type decl;
        '"require_method: a matching method must be a dictionary carrying ",
         ", " sv string required];
    missing:required where not required in key decl;
    if[count missing;
        '"require_method: matching method is missing ",", " sv string missing];
    supplied:key decl;
    unknown:supplied where not supplied in required,`why;
    if[count unknown;
        '"require_method: unknown matching method key ",", " sv string unknown];
    if[not all (type each decl required) within 100 104h;
        '"require_method: a matching method's open and pick must both be functions"];
    1b}

/ method name -> its declaration.
methods:(`symbol$())!()

/ Register a matching method under a name.
/ .
/ The registry IS the list of methods - there is no second enumeration of
/ which ones exist, so adding one is a registration and nothing else.
/ @param name the name callers will pass as the `method option
/ @param decl a dict with `open (lots;lot -> lots) and `pick (lots -> index), optionally `why
/ @return the name registered
/ @throws error when the method is malformed - see require_method
/ @eg .qalloc.register[`fifo_again;`open`pick!(.qalloc.append_lot;.qalloc.pick_first)] -> `fifo_again
register:{[name;decl]
    require_method decl;
    methods[name]:`open`pick`why!(decl`open; decl`pick; $[`why in key decl; decl`why; ""]);
    name}

/ Look a method up by name.
/ @param name a registered method name
/ @return its declaration dict
/ @throws error when nothing is registered under that name, listing what is
/ @eg .qalloc.method[`fifo]`why -> "oldest open lot first"
method:{[name]
    if[not name in key methods;
        '"method: ",string[name]," is not a registered matching method - have ",
         ", " sv string key methods];
    methods name}

/ Every registered method name, in registration order.
/ @return a symbol vector - the five built in here, plus anything a desk has registered since
/ @eg `fifo in .qalloc.registered[] -> 1b
registered:{[] key methods}

/ ------------------------------------------------------- METHOD PIECES

/ Private: the ordinary lot-opening rule - a new lot on the end of the
/ queue, keeping every fill's own price. Everything except weighted uses
/ this; they differ only in which end `pick reaches into.
append_lot:{[lots;lot] lots,enlist lot}

/ Private: weighted-average lot-opening - fold the fill into the single
/ open lot at the size-weighted average price, so the queue never holds
/ more than one entry and there is nothing left to choose between. This is
/ .qpos.apply_fill's convention, expressed as an opening rule.
/ .
/ The merged lot keeps the FIRST trade's id and time, because there is no
/ better answer: weighted average has destroyed the distinction the id
/ would name. Under this method open_id labels the lot, not a unique trade
/ - which is the whole reason fifo and lifo exist.
merge_lot:{[lots;lot]
    if[0=count lots; :enlist lot];
    open_lot:last lots;
    total:open_lot[`qty]+lot`qty;
    weighted:((open_lot[`qty]*open_lot`price)+lot[`qty]*lot`price)%total;
    (-1_lots),enlist @[open_lot;`qty`price;:;(total;weighted)]}

/ Private: pick the oldest open lot (fifo).
pick_first:{[lots] 0}

/ Private: pick the newest open lot (lifo).
pick_last:{[lots] count[lots]-1}

/ Private: pick the highest-priced open lot (hifo). For a long book that is
/ highest-cost-first, the convention that realises the smallest gain.
pick_highest:{[lots] p:lots`price; first where p=max p}

/ Private: pick the lowest-priced open lot (lofo), hifo's mirror.
pick_lowest:{[lots] p:lots`price; first where p=min p}

register[`fifo;     `open`pick`why!(append_lot; pick_first;   "oldest open lot first")];
register[`lifo;     `open`pick`why!(append_lot; pick_last;    "newest open lot first")];
register[`hifo;     `open`pick`why!(append_lot; pick_highest; "highest-priced open lot first")];
register[`lofo;     `open`pick`why!(append_lot; pick_lowest;  "lowest-priced open lot first")];
register[`weighted; `open`pick`why!(merge_lot;  pick_first;   "one running weighted-average lot - matches .qpos")];

/ --------------------------------------------------------------- FILTERS

/ The filter that keeps everything - the default for both universe and
/ where, so the unfiltered path runs the same code as the filtered one.
/ @param t any table
/ @return t
/ @eg count .qalloc.all_rows ([] a:1 2 3) -> 3
all_rows:{[t] t}

/ Build a filter from a dictionary of column -> accepted value(s).
/ .
/ Covers the common case declaratively, so a caller does not write a lambda
/ to say "EBS only". Atoms and vectors both work as values. Anything this
/ cannot express is still just a function, which is why the option's type
/ is a function rather than a dict.
/ @param d column name -> an accepted value, or a list of them
/ @return a function taking a table and returning the rows that match every entry
/ @eg count .qalloc.by_cols[enlist[`a]!enlist 1 2] ([] a:1 2 3) -> 2
by_cols:{[d]
    {[d;t]
        if[0=count d; :t];
        masks:{[t;kv] (t kv 0) in (),kv 1}[t;] each flip (key d; value d);
        t where all masks}[d]}

/ --------------------------------------------------------------- OPTIONS

/ Every option and its default. The key set is closed - an unrecognised
/ option is a typo, and a typo that silently does nothing is worse than an
/ error, so normalised_opts refuses one.
/ `opening is a lot table rather than a position book on purpose: a book
/ has already averaged its lots away, so seeding from one would force
/ weighted's convention on fifo and lifo. residual emits exactly the shape
/ opening accepts, which is what makes a chain of days compose.
/ .
/ `asof defaults to (::), "no truncation", rather than to a far-future
/ timestamp - the trades table's time column need not be a timestamp, and a
/ default that assumed one would be a type error on the untruncated path.
default_opts:`method`by`universe`where`opening`asof!(`fifo; enlist `sym; all_rows; all_rows; (::); (::))

/ Private: fill in the defaults, reject typos, and normalise `by to a vector.
/ .
/ A bare symbol is accepted in place of the whole dict, because
/ allocate[trades;`lifo] is what a caller reaches for first.
/ .
/ `by is normalised to a vector for .qsrc.define's reason: storing one
/ shape means no consumer downstream has to decide whether a single-column
/ bucket needs enlisting.
normalised_opts:{[opts]
    o:$[-11h=type opts; (enlist `method)!enlist opts;
        99h=type opts; opts;
        '"allocate: opts must be a dictionary of options, or a method name"];
    supplied:key o;
    unknown:supplied where not supplied in key default_opts;
    if[count unknown;
        '"allocate: unknown option ",(", " sv string unknown),
         " - have ",", " sv string key default_opts];
    o:default_opts,o;
    / 100 104h rather than 100h alone: by_cols returns a PROJECTION, so the
    / filter a caller is most likely to pass is not a plain lambda.
    if[not all (type each o`universe`where) within 100 104h;
        '"allocate: universe and where must each be a function taking a table"];
    @[o;`by;(),]}

/ ---------------------------------------------------------------- ENGINE

/ The columns a trades table must carry. side is +1 buy / -1 sell and size
/ is unsigned, the same convention .qpos.apply_fills consumes - so the one
/ table feeds both the book and its attribution.
required_cols:`time`sym`side`size`trade_price

/ Private: check the input and give every trade an identity.
/ .
/ trade_id is the row index of the table AS SUPPLIED when the caller has
/ not provided one, assigned before any filtering or sorting so that an id
/ in the ledger still points at a row of the table the caller passed in.
prepared:{[trades;bys]
    if[not 98h=type trades; '"allocate: trades must be a table"];
    .qschema.require_cols[`allocate;`trades;trades;distinct required_cols,bys];
    $[`trade_id in cols trades; trades; update trade_id:i from trades]}

/ The columns a carried-in lot table must carry, on top of the bucket
/ columns. Deliberately the subset of residual's own columns that says what
/ a position IS - trade_id and time are optional, because a position
/ carried in from a prior system may have no trade behind it.
required_lot_cols:`qty`price`side

/ Private: check a carried-in lot table, and give back an empty one when
/ the caller passed nothing.
prepared_opening:{[opening;bys]
    if[(::)~opening; :()];
    if[not 98h=type opening; '"allocate: opening must be a table of lots"];
    .qschema.require_cols[`allocate;`opening;opening;distinct required_lot_cols,bys];
    if[not all (exec side from opening) in -1 1;
        '"allocate: every opening lot's side must be 1 (long) or -1 (short)"];
    opening}

/ Private: an empty lot queue whose id/time/side types are the trades
/ table's own, so no assumption is made about how a desk spells a
/ timestamp. qty and price are pinned to float - a fill of integer size
/ still averages to a fraction.
new_lots:{[trades]
    ([] trade_id:0#trades`trade_id; time:0#trades`time; qty:`float$();
        price:`float$(); side:0#trades`side)}

/ Private: an empty match ledger, typed from the trades table the same way.
new_matches:{[trades]
    ([] open_id:0#trades`trade_id; open_time:0#trades`time; open_side:0#trades`side;
        close_id:0#trades`trade_id; close_time:0#trades`time; qty:`float$();
        open_price:`float$(); close_price:`float$())}

/ Private: apply one trade to one bucket's state.
/ .
/ A trade first CLOSES against lots on the other side, one pick at a time,
/ until either it is used up or the book is flat; whatever is left OPENS a
/ new lot. That ordering is what makes a flip fall out for free rather than
/ needing its own branch: a sell of 3 against a long of 1 closes the 1 and
/ opens a short of 2 at the sell price, which is .qpos's convention too.
/ .
/ Every lot in a bucket shares a side - you cannot be long and short the
/ same thing at once here - so checking the first lot's side decides
/ whether this trade closes or opens.
step:{[m;state;row]
    lots:state`lots;
    out:state`matches;
    qty:"f"$row`size;
    side:row`side;
    while[(0<qty) and (0<count lots) and not side=first lots`side;
        ix:(m`pick) lots;
        lot:lots ix;
        take:min(qty; lot`qty);
        out,:enlist `open_id`open_time`open_side`close_id`close_time`qty`open_price`close_price!
            (lot`trade_id; lot`time; lot`side; row`trade_id; row`time; take;
             lot`price; "f"$row`trade_price);
        qty-:take;
        lots:$[take=lot`qty; lots _ ix; @[lots;`qty;@[;ix;-;take]]];
        ];
    if[0<qty;
        lots:(m`open)[lots;
            `trade_id`time`qty`price`side!(row`trade_id; row`time; qty; "f"$row`trade_price; side)]];
    `lots`matches!(lots;out)}

/ Private: widen a result table with the bucket's own key columns.
/ .
/ Takes the values rather than reading them off the trades, because a
/ bucket can exist in `opening and have no trades at all - a position
/ carried in and not touched today still has to appear in the residual.
with_by:{[out;kv;bys]
    n:count out;
    bys xcols out ,' flip bys!n#/:kv bys}

/ Private: just the bucket columns of a table, in `by order.
/ .
/ A TABLE of keys rather than a grouped dictionary, because `group` on a
/ multi-column table returns a dictionary KEYED BY A TABLE, and a table is
/ not a list of dicts you can look a row up in - so the single- and
/ multi-column cases stopped being one code path. As a table both cases
/ are: find each row's key in the distinct keys, group on the answer.
key_table:{[t;bys] ?[t;();0b;bys!bys]}

/ Private: the seed lot queue for one bucket - the carried-in position,
/ typed like the trades table and stripped of its bucket columns, which
/ the queue does not carry (they are constant within a bucket by
/ definition). Opening lots sit at the FRONT of the queue: they are the
/ oldest thing in it, so fifo reaches them first and lifo reaches them
/ last, exactly as if their trades had been in the table.
seed_lots:{[trades;opening;ix]
    empty:new_lots trades;
    if[0=count ix; :empty];
    rows:opening ix;
    n:count rows;
    / A carried-in lot with no trade behind it gets a null id and a null
    / time, of the trades table's own types - so the queue stays one
    / typed table whatever mixture it holds.
    empty upsert ([]
        trade_id:$[`trade_id in cols rows; rows`trade_id; n#first 0#trades`trade_id];
        time:$[`time in cols rows; rows`time; n#first 0#trades`time];
        qty:"f"$rows`qty; price:"f"$rows`price; side:rows`side)}

/ Private: match one bucket, returning its ledger and its leftover lots.
bucket:{[m;bys;kv;seed;sub]
    st:(step[m])/[`lots`matches!(seed; new_matches sub); sub];
    `matches`residual!(with_by[st`matches;kv;bys]; with_by[st`lots;kv;bys])}

/ Attribute realised P&L to trades, and report what is still open.
/ .
/ The one entry point that does the work; allocate and residual are views
/ of it, and exist because a caller almost always wants one or the other
/ rather than both.
/ @param trades a table with time, sym, side (+1/-1), size (unsigned), trade_price, and optionally trade_id
/ @param opts an options dict (see default_opts), or a bare method name
/ @return a dict with `matches (the ledger, plus a pnl column) and `residual (unmatched open lots)
/ @throws error on a missing column, an unknown option, or an unregistered method
/ @eg count .qalloc.run[([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:`EURUSD`EURUSD; side:1 -1; size:2#1000000f; trade_price:1.0 2.0);`fifo]`matches -> 1
run:{[trades;opts]
    o:normalised_opts opts;
    m:method o`method;
    bys:o`by;
    trades:prepared[trades;bys];
    opening:prepared_opening[o`opening;bys];
    trades:(o`universe) trades;
    / asof AFTER universe, so the two compose the way they read: pick the
    / trades that count, then stop the clock.
    if[not (::)~o`asof; trades:?[trades;enlist (<=;`time;o`asof);0b;()]];
    trades:`time xasc trades;
    tk:key_table[trades;bys];
    ok:$[count opening; key_table[opening;bys]; 0#tk];
    / The buckets are every key either side knows about: a position
    / carried in for a sym that did not trade today is still a bucket.
    ks:distinct tk,ok;
    tid:ks?tk;
    oid:ks?ok;
    res:{[m;bys;trades;opening;ks;tid;oid;j]
        bucket[m;bys;ks j;
            seed_lots[trades;opening;where oid=j];
            trades where tid=j]}[m;bys;trades;opening;ks;tid;oid] each til count ks;
    empty_kv:bys!first each 0#/:trades bys;
    matches:$[count res; raze res[;`matches]; with_by[new_matches trades;empty_kv;bys]];
    residual:$[count res; raze res[;`residual]; with_by[new_lots trades;empty_kv;bys]];
    matches:update pnl:.qrisk.pnl[qty;open_price;close_price;open_side] from matches;
    `matches`residual!((o`where) `close_time xasc matches; residual)}

/ The match ledger: one row per (opening trade, closing trade, matched qty).
/ .
/ pnl is that slice's realised P&L in quote currency, via .qrisk.pnl at the
/ two prices - so this module owns the matching and risk.q still owns the
/ arithmetic.
/ @param trades see run
/ @param opts see run
/ @return a table of bucket columns, open_id/open_time/open_side, close_id/close_time, qty, open_price, close_price, pnl
/ @eg exec first pnl from .qalloc.allocate[([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:`EURUSD`EURUSD; side:1 -1; size:2#1000000f; trade_price:1.0 2.0);`fifo] -> 1000000f
allocate:{[trades;opts] run[trades;opts]`matches}

/ The lots left open once every trade has been applied - the position the
/ ledger does not explain, and the only part of the book whose P&L is still
/ a mark rather than a fact. Pair it with unrealised.
/ @param trades see run
/ @param opts see run
/ @return a table of bucket columns, trade_id, time, qty, price, side
/ @eg exec first qty from .qalloc.residual[([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:`EURUSD`EURUSD; side:1 -1; size:3000000 1000000f; trade_price:1.0 2.0);`fifo] -> 2000000f
residual:{[trades;opts] run[trades;opts]`residual}

/ ------------------------------------------------------------- POSITIONS

/ The book the open lots add up to: signed quantity and weighted-average
/ price per bucket.
/ .
/ The same object .qpos carries, derived rather than tracked - which is why
/ it is worth having both. .qpos folds fills into a book in one pass and is
/ what a live process runs; this recomputes the book from the lots at
/ whatever instant and under whatever filters were asked for, which is what
/ a question about history needs. They agree, and test_allocation.q says so.
/ .
/ Every lot in a bucket shares a side, so a weighted average of their
/ prices is a real entry price rather than a mixture of longs and shorts.
/ @param lots a residual table from residual
/ @param group_cols the columns to group by - normally the same `by the lots were matched under
/ @return a table of group_cols plus qty (signed), avg_price and the number of open lots
/ @eg exec first qty from .qalloc.position[.qalloc.residual[([] time:enlist 2026.01.01D09:00:00; sym:enlist `EURUSD; side:enlist 1; size:enlist 1000000f; trade_price:enlist 1.0);`fifo];`sym] -> 1000000f
position:{[lots;group_cols]
    ?[lots; (); {x!x} (),group_cols;
        `qty`avg_price`lots!((sum;(*;`side;`qty));
            (%;(sum;(*;`qty;`price));(sum;`qty)); (count;`i))]}

/ The position held at an instant.
/ .
/ run's `asof option with the rollup applied, because "what were we holding
/ at 10am" is the question, and spelling it as a truncate-then-group is the
/ answer rather than the question.
/ @param trades see run
/ @param opts see run - its `asof is overwritten by ts
/ @param ts the instant to stop at, inclusive
/ @return a book, as position returns one
/ @eg exec first qty from .qalloc.position_at[([] time:2026.01.01D09:00:00 2026.01.01D11:00:00; sym:2#`EURUSD; side:1 -1; size:2#1000000f; trade_price:1.0 2.0);`fifo;2026.01.01D10:00:00] -> 1000000f
position_at:{[trades;opts;ts]
    o:normalised_opts opts;
    position[residual[trades;@[o;`asof;:;ts]]; o`by]}

/ -------------------------------------------------------------- ROLLUPS

/ P&L per trade, both ways round.
/ .
/ Every slice of realised P&L is attributable TWICE - once to the trade
/ that opened the position and once to the trade that closed it - and which
/ one a desk wants depends on the question. closing_pnl is "what did this
/ sell make", opening_pnl is "what did this buy eventually make".
/ .
/ Each column sums to the book's total realised P&L on its own. They are
/ two views of the same money, so adding them together would count it
/ twice, which is why there is no total column here.
/ @param matches a ledger from allocate
/ @return a table keyed on nothing, one row per trade that opened or closed something
/ @eg count .qalloc.by_trade .qalloc.allocate[([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:`EURUSD`EURUSD; side:1 -1; size:2#1000000f; trade_price:1.0 2.0);`fifo] -> 2
by_trade:{[matches]
    closing:select closing_pnl:sum pnl, closed_qty:sum qty by trade_id:close_id from matches;
    opening:select opening_pnl:sum pnl, opened_qty:sum qty by trade_id:open_id from matches;
    r:0!closing uj opening;
    update 0^closing_pnl, 0^opening_pnl, 0f^closed_qty, 0f^opened_qty from `trade_id xasc r}

/ Realised P&L grouped by whatever a desk reports on.
/ .
/ The ledger carries the bucket columns and both trades' times, so a
/ rollup is a group-by rather than a rerun of the matching.
/ @param matches a ledger from allocate
/ @param group_cols the columns to group by
/ @return a table of group_cols plus pnl, qty and the number of matches
/ @eg exec first pnl from .qalloc.summary[.qalloc.allocate[([] time:2026.01.01D09:00:00 2026.01.01D10:00:00; sym:`EURUSD`EURUSD; side:1 -1; size:2#1000000f; trade_price:1.0 2.0);`fifo];enlist `sym] -> 1000000f
summary:{[matches;group_cols]
    ?[matches; (); {x!x} (),group_cols;
        `pnl`qty`matches!((sum;`pnl);(sum;`qty);(count;`i))]}

/ Mark the open lots that no close has explained yet.
/ .
/ Kept separate from the ledger because it is a different kind of number:
/ every pnl in the ledger is realised and will not move again, while this
/ one is only as good as the mark it was given.
/ @param lots a residual table from residual
/ @param marks a dictionary of sym -> current price
/ @return lots plus mark_price and unrealised_pnl
/ @eg exec first unrealised_pnl from .qalloc.unrealised[.qalloc.residual[([] time:enlist 2026.01.01D09:00:00; sym:enlist `EURUSD; side:enlist 1; size:enlist 1000000f; trade_price:enlist 1.0);`fifo]; enlist[`EURUSD]!enlist 2.0] -> 1000000f
unrealised:{[lots;marks]
    r:update mark_price:marks sym from lots;
    update unrealised_pnl:.qrisk.pnl[qty;price;mark_price;side] from r}

\d .
