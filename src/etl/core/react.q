/ react.q - recompute a dataset when the one it reads is published (.qreact).
/ .
/ "When this table updates, that one should update too." The streaming half of
/ this tree has always worked that way - a tickerplant pushes and a subscriber
/ recomputes - while the bounded half had no equivalent: a worker published its
/ window and nothing downstream heard about it.
/ .
/ WHY NOT A TIMER, WHICH IS THE OBVIOUS ANSWER
/ .
/ A timer polling the coverage ledger would work, and it is the wrong shape.
/ It asks "has anything changed?" on a clock and mostly hears no; it learns
/ that something changed but not WHAT, so it has to diff the ledger to find
/ the range; and it is late by up to one interval, always.
/ .
/ The writer already knows all of it. There is exactly one place rows enter a
/ dataset on this path - .qbw.do_window, after finish_window has recorded the
/ materialisation - so that is where a publication announces itself, with the
/ range it covered in hand. Nothing polls and nothing can be missed.
/ .
/ A timer is still right for the OTHER question, which is not this one:
/ markout1 recomputes when enough TIME has passed rather than when data
/ arrived, and no publication event can tell it that.
/ .
/ WHAT A REACTION IS, AND WHAT IT DELIBERATELY IS NOT
/ .
/ A reaction is (dataset; name; handler): when `dataset` gains rows over a
/ range, call handler[dataset; range_from; range_to].
/ .
/ It is NOT "the framework runs the downstream worker for you". .qdag knows
/ which jobs read a dataset, and that is a genuine dependency - but running a
/ bounded worker needs a source_version, which is a DECISION about which
/ release of the upstream data this run claims, and no framework can
/ invent one. So the graph says who is interested and the handler says what to
/ do, and the thing that cannot be derived is written down by the person who
/ knows it. `.qreact.dag_consumers` is here to make that wiring obvious.
/ .
/ THREE PROPERTIES THIS FILE EXISTS TO HOLD
/ .
/   QUEUED, NEVER RECURSIVE. A handler that publishes will notify again, from
/   inside the first notify. Running that immediately would nest a whole
/   downstream run inside one upstream window and, on a cycle, recurse until
/   the stack gave out. Notifications are appended to a queue and drained by
/   whichever call is already draining, so a cascade is a LOOP at one level.
/ .
/   A FAILING REACTION NEVER FAILS THE PUBLICATION. The rows are already
/   written and the coverage already staged when a reaction runs. Throwing
/   here would turn a successful materialisation into a failed one over a
/   downstream bug - exactly backwards, and the same reasoning record_facts
/   already follows. Failures are recorded in `history` and logged.
/ .
/   A CASCADE TERMINATES. The same (dataset; range) is not dispatched twice
/   within one drain, and `max_depth` bounds how far a chain may travel. A
/   cycle in the graph therefore stops rather than spinning - .qdag.topological
/   refuses a cycle at registration, but a handler can always publish anywhere.

\d .qreact

/ ------------------------------------------------------------ REGISTRY

/ dataset -> table of (name; handler).
reactions:(`symbol$())!();

/ Private: the empty reaction table, so every path has one shape.
/ .
/ `outputs` is what this reaction writes and `derived` says where that claim
/ came from - see `on` and `on_worker`.
no_reactions:{[] ([] name:`symbol$(); handler:(); outputs:(); derived:`boolean$())}

/ How far a chain of reactions may travel before it is refused.
/ .
/ A publication that triggers a job that publishes is depth 1, its own
/ downstream depth 2. Eight is far past any graph this tree has (the longest
/ real chain is two) and short enough that a runaway stops promptly.
max_depth:8

/ Register a reaction: when `dataset` gains rows, call `handler`.
/ .
/ Re-registering the same (dataset; name) REPLACES its handler rather than
/ adding a second, so reloading a file during development is not a failure -
/ the posture .qdag.register and .qbw.define already take.
/ .
/ WHAT THIS REACTION WRITES IS ASSERTED, NOT DERIVED, and the registry records
/ that. dag.q's rule is "derive, never re-declare", and it holds because a
/ worker's inputs and outputs come from the source declaration and therefore
/ cannot disagree with what it does. A handler is an arbitrary lambda that
/ could write anywhere, so `outputs` here is a CLAIM about it - which is worth
/ having (it puts the reaction in the graph, where a cycle is refused at
/ registration rather than surviving until max_depth stops it at runtime) but
/ is not the same kind of fact. `derived` is 0b for these, so a reader of the
/ graph can tell which edges were checked and which were promised. Use
/ `on_worker` where the answer IS derivable.
/ @param dataset the dataset whose publication fires this, as a symbol
/ @param name a name for this reaction, unique per dataset
/ @param handler a function taking (dataset; range_from; range_to)
/ @return the reaction's name
/ @throws error when the handler is not a 3-argument function
/ @eg .qreact.on[`demo_deals;`rebuild_positions;{[ds;f;t] .qpos.rebuild[f;t]}]
/ `nm`, not `name`: inside the where-clause below BOTH sides of a comparison
/ written `name~/:name` resolve to the COLUMN, so the filter matched nothing
/ and re-registering appended a second handler instead of replacing the
/ first - measured, not theorised. A parameter that shares a column's name
/ is the trap leg_book_as_of and quotes_for_sym already name around.
on:{[dataset;nm;handler]
    require_handler[dataset;nm;handler];
    register[dataset;nm;handler;`$();0b]}

/ Private: what every reaction's arguments must satisfy, wherever registered.
/ .
/ Arity is checked HERE, not at the first publication: a reaction registered
/ with the wrong shape would otherwise fail only when the upstream job next
/ ran, which may be hours later and is attributed to that job rather than to
/ this wiring.
require_handler:{[dataset;nm;handler]
    if[not -11h=type dataset; '"on: dataset must be a symbol"];
    if[not -11h=type nm; '"on: name must be a symbol"];
    if[not (type handler) within 100 112h;
        '"on: ",string[nm]," must be a function taking (dataset;range_from;range_to)"];
    if[(100h=type handler) and not 3=count (value handler) 1;
        '"on: ",string[nm]," must take exactly 3 arguments (dataset;range_from;range_to)"];
    1b}

/ Register a reaction that writes a dataset it declares.
/ .
/ The same as `on`, plus what the handler writes, so the reaction becomes a
/ node in the job graph rather than a terminal one. Asserted rather than
/ derived - see `on`.
/ @param outputs the dataset(s) this handler writes, as a symbol or vector
/ @throws error when outputs is not a symbol or symbol vector
/ @eg .qreact.on_writing[`demo_deals;`rebuild_positions;`positions;{[ds;f;t] count select from demo_deals where deal_time within (f;t-1)}]
on_writing:{[dataset;nm;outputs;handler]
    require_handler[dataset;nm;handler];
    if[not 11h=abs type outputs;
        '"on_writing: ",string[nm],"'s outputs must be a symbol or symbol vector naming what it writes"];
    register[dataset;nm;handler;(),outputs;0b]}

/ Register a reaction that runs a REGISTERED WORKER over the published range.
/ .
/ The case where the graph edge is derivable, and therefore the one to prefer.
/ A bounded worker already declares its target through its source, so what
/ this reaction writes is read from .qbw rather than asserted: the entry in
/ the graph cannot disagree with what the worker does, and `derived` is 1b.
/ .
/ `spec_fn` supplies the one thing that genuinely cannot be derived - the
/ run specification, whose `source_version` is a DECISION about which release
/ of the upstream data this run claims. It is called with the
/ published range and must return the dict .qbw.init takes.
/ @param dataset the upstream dataset whose publication fires this
/ @param worker a worker registered with .qbw.define
/ @param spec_fn a function (range_from;range_to) -> the run specification
/ @return the reaction's name, which is the worker's name
/ @throws error when the worker is not registered, or spec_fn is not binary
/ @eg .qreact.on_worker[`upstream_feed;`demo_deals_backfill;{[f;t] `source_version`range_from`range_to!(`v1;f;t)}]
on_worker:{[dataset;worker;spec_fn]
    cfg:.qbw.declaration worker;
    if[not (type spec_fn) within 100 112h;
        '"on_worker: ",string[worker],"'s spec_fn must be a function taking (range_from;range_to)"];
    if[(100h=type spec_fn) and not 2=count (value spec_fn) 1;
        '"on_worker: ",string[worker],"'s spec_fn must take exactly 2 arguments (range_from;range_to)"];
    ns:.qbw.namespace worker;
    h:{[worker;ns;spec_fn;ds;range_from;range_to]
        (` sv ns,`init)[spec_fn[range_from;range_to]];
        (` sv ns,`run)[];
        (` sv ns,`cleanup)[]
      }[worker;ns;spec_fn];
    register[dataset;worker;h;(),cfg`dataset;1b]}

/ Private: store one reaction, replacing any of the same name.
register:{[dataset;nm;handler;outputs;derived]
    existing:$[dataset in key reactions; reactions dataset; no_reactions[]];
    existing:select from existing where not name=nm;
    reactions[dataset]:existing upsert
        ([] name:enlist nm; handler:enlist handler; outputs:enlist outputs; derived:enlist derived);
    nm}

/ Stop reacting. Unknown names are ignored: removing a reaction that is not
/ there is the state the caller wanted either way.
/ @return the reactions still registered for that dataset
off:{[dataset;nm]
    if[not dataset in key reactions; :no_reactions[]];
    reactions[dataset]:select from reactions dataset where not name=nm;
    reactions dataset}

/ Every reaction registered for a dataset.
for_dataset:{[dataset] $[dataset in key reactions; reactions dataset; no_reactions[]]}

/ Forget every reaction, the queue and the history. For tests, and for
/ rebuilding the wiring after the registries change.
reset:{[] reactions::(`symbol$())!(); `.qreact.queue set empty_queue[]; `.qreact.history set empty_history[]; ()}

/ ------------------------------------------------------- THE DAG WIRING

/ Which jobs does .qdag say read this dataset?
/ .
/ The answer to "who should react", derived rather than restated - but NOT
/ dispatched automatically, see the header. Wiring a reaction is then:
/ .
/   .qreact.dag_consumers[`demo_deals]  ->  `positions_backfill
/   .qreact.on[`demo_deals;`positions_backfill;{[ds;f;t] ...}]
/ .
/ @param dataset the published dataset
/ @return the job names that declared it as an input, empty when .qdag is
/   not loaded or nothing reads it
/ @eg .qreact.dag_consumers `demo_deals
dag_consumers:{[dataset]
    if[not `qdag in key `; :`$()];
    @[{.qdag.consumers x};dataset;{[e] `$()}]}

/ Which registered reactions have no job in the graph behind them, and which
/ graph edges have no reaction wired?
/ .
/ Neither is an error - a reaction may legitimately do something no job
/ declares, and an edge may be filled by an orchestrator rather than here -
/ but an edge nobody reacts to is the shape of "we thought that was
/ automatic", so it is worth being able to ask.
/ `asserted` is the third answer, and the one to read before trusting a
/ drawing: reactions whose output was CLAIMED by the caller rather than read
/ from a worker's declaration. Those edges are in the graph and cannot be
/ checked against anything - see `on`.
/ @return dict of `unwired (dataset -> consumer jobs with no reaction),
/   `undeclared (datasets with a reaction that no job reads) and `asserted
/   (dataset~reaction names whose output is a claim)
audit:{[]
    reacting:key reactions;
    datasets:distinct reacting,$[`qdag in key `; raze {(.qdag.declaration x)`outputs} each key .qdag.jobs; `$()];
    unwired:(!). flip {[d] (d;dag_consumers d)} each datasets where 0=count each for_dataset each datasets;
    undeclared:reacting where 0=count each dag_consumers each reacting;
    / `count each value unwired` on an EMPTY dict throws 'type - value of an
    / empty dict is a general empty list, not a list of lists - so the filter
    / is written against the keys instead, where the empty case is a plain
    / empty symbol vector.
    wired_keys:(key unwired) where 0<count each dag_consumers each key unwired;
    asserted:raze {[ds]
        rs:.qreact.for_dataset ds;
        bad:select from rs where not derived, 0<count each outputs;
        {[ds;nm] `$(string ds),"~",string nm}[ds] each bad`name
      } each reacting;
    `unwired`undeclared`asserted!(wired_keys#unwired;undeclared;asserted)}

/ ---------------------------------------------------------- THE QUEUE

empty_queue:{[] ([] dataset:`symbol$(); range_from:`timestamp$(); range_to:`timestamp$(); depth:`long$())}

queue:empty_queue[]

/ Whether a drain is already running. The reentrancy guard: a handler that
/ publishes notifies from inside the first notify, and that notification must
/ join the queue rather than start a second, nested drain.
draining:0b

empty_history:{[]
    ([] at:`timestamp$(); dataset:`symbol$(); name:`symbol$(); depth:`long$();
        range_from:`timestamp$(); range_to:`timestamp$(); outcome:`symbol$(); detail:())}

history:empty_history[]

/ How many history rows to keep. Bounded because a long-running process would
/ otherwise grow it without limit, and the recent end is the useful one.
history_limit:1000

/ Private: record one reaction's outcome.
record:{[dataset;name;depth;range_from;range_to;outcome;detail]
    `.qreact.history set history_limit sublist history,
        ([] at:enlist .z.p; dataset:enlist dataset; name:enlist name; depth:enlist depth;
            range_from:enlist range_from; range_to:enlist range_to;
            outcome:enlist outcome; detail:enlist detail);
    }

/ ------------------------------------------------------------- NOTIFY

/ Announce that `dataset` gained rows over [range_from;range_to).
/ .
/ Called from the write seam - .qbw.do_window, after the window is recorded -
/ and safe to call from anywhere else that publishes.
/ .
/ Returns the number of reactions that RAN, which is zero when nothing is
/ registered for this dataset and zero again when a drain is already in
/ progress (the work is queued and the outer drain will do it). A caller that
/ treats the return as "did my downstream finish" would be wrong in the
/ cascade case, which is why the header calls the queue the point rather than
/ an implementation detail.
/ @param dataset the dataset just published
/ @param range_from inclusive lower bound of what was published
/ @param range_to exclusive upper bound
/ @return the number of reactions run by this call
/ @eg .qreact.notify[`demo_deals;2026.09.11D00:00;2026.09.12D00:00]
notify:{[dataset;range_from;range_to] enqueue[dataset;range_from;range_to;0]}

/ Private: queue one notification and, unless a drain is already running,
/ drain the queue.
enqueue:{[dataset;range_from;range_to;depth]
    queue,:([] dataset:enlist dataset; range_from:enlist range_from;
              range_to:enlist range_to; depth:enlist depth);
    $[draining; 0; drain[]]}

/ Private: run queued notifications until none are left.
/ .
/ The `draining` flag is set for the whole loop, so a handler that publishes
/ adds to `queue` and returns rather than starting a nested drain - which is
/ what turns a cascade into a loop at one level.
/ .
/ `done` is the cycle guard: the same (dataset; range) is dispatched at most
/ once per drain, so A -> B -> A settles instead of spinning. It is per-drain
/ rather than global on purpose - the same range published again later is a
/ new event and must fire again.
drain:{[]
    `.qreact.draining set 1b;
    done:();
    ran:0;
    while[count queue;
        item:first queue;
        `.qreact.queue set 1_queue;
        k:(item`dataset;item`range_from;item`range_to);
        / `not any done~\:k`, not `not k in done`: `in` tests ATOM
        / membership, and k is a triple - it throws 'type rather than
        / answering false, so the guard would have failed on the first
        / cascade rather than on a repeat.
        if[not any done~\:k;
            done,:enlist k;
            ran+:dispatch item]];
    `.qreact.draining set 0b;
    ran}

/ Private: run every reaction registered for one queued item.
dispatch:{[item]
    rs:for_dataset item`dataset;
    if[0=count rs; :0];
    if[item[`depth]>=max_depth;
        {[item;nm] record[item`dataset;nm;item`depth;item`range_from;item`range_to;`refused;
            "cascade deeper than .qreact.max_depth (",string[max_depth],") - refusing rather than continuing"]
          }[item] each rs`name;
        :0];
    count {[item;nm;h] run_one[item;nm;h]}[item] .' flip (rs`name;rs`handler)}

/ Private: run one handler, trapped.
/ .
/ The trap is the point. The rows are already published and the coverage
/ already staged when this runs, so a throw here would turn a successful
/ materialisation into a failed one over a downstream bug.
/ .
/ The handler runs at the item's depth; anything IT publishes notifies at
/ depth+1, which is how max_depth bounds a chain. `depth_now` is a global
/ rather than an argument because a handler calls .qbw / .qreact.notify
/ through the ordinary path and cannot be asked to thread a depth through.
depth_now:0

run_one:{[item;nm;h]
    `.qreact.depth_now set item`depth;
    r:@[{[h;item] h[item`dataset;item`range_from;item`range_to]; `ok}[h];item;{[e] (`failed;e)}];
    `.qreact.depth_now set 0;
    $[`ok~r;
        record[item`dataset;nm;item`depth;item`range_from;item`range_to;`ok;""];
        [record[item`dataset;nm;item`depth;item`range_from;item`range_to;`failed;last r];
         log_failure[item;nm;last r]]];
    1b}

/ Private: log a failed reaction, tolerating an absent .qlog.
/ .
/ Protected for the same reason .qbw.begin_run is: several minimal loaders
/ pull in part of the tree, and a reaction must degrade to an unlogged
/ failure rather than an error inside an error handler.
log_failure:{[item;nm;e]
    @[{.qlog.err[`qreact;"reaction failed";
        `dataset`reaction`range_from`range_to`error!
        (x`dataset;y;x`range_from;x`range_to;z)]}[item;nm];e;{[e] (::)}]}

/ ------------------------------------------------------ RE-ENTRY POINT

/ Announce a publication made BY a reaction, one level deeper.
/ .
/ .qbw.do_window calls this rather than `notify` directly, so a chain
/ triggered by a reaction is counted: at depth 0 the two are identical, and
/ inside a handler this is what makes max_depth bite.
notify_from_here:{[dataset;range_from;range_to]
    enqueue[dataset;range_from;range_to;$[draining; depth_now+1; 0]]}

\d .
