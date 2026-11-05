/ bounded_pin.q - a bounded worker pins the configuration its rows will name (.qetl.job.bounded, #1999).
/ .
/ A worker whose rows carry a `config_id` (the markout workers) declares `pin`: a niladic
/ function that records, durably, the configuration snapshot those rows name and returns the
/ ids (src/etl/core/config_snapshot.q). The run calls it once, before it plans or fetches
/ anything, so no row is calculated under a configuration nobody recorded; a pin that throws
/ fails the run before its first window. The other half is the worker's own `check`, which
/ refuses a batch naming a snapshot that is not durable (config_markout.check_pinned).
/ .
/ Its own file because bounded_worker.q is held to its line budget (q-module-size) and this is
/ one hook, not part of the lifecycle's decisions.

\d .

/ Validate a worker's declared `pin` at declaration time, so a malformed one fails then and not
/ at the first run. (Its second parameter is `given`, not `decl`: this is not a declaration
/ verb, which uqs's job-kind table lists by that shape.)
/ @param worker the worker's name
/ @param given the declaration
/ @return the declared pin, or (::) when the worker declares none
/ @throws error when the declared pin is not a function
/ @eg .qetl.job.bounded.require_pin[`demo;()!()]  ->  (::)
.qetl.job.bounded.require_pin:{[worker;given]
    if[not `pin in key given; :(::)];
    if[(::)~given`pin; :(::)];
    if[not .qetl.declared.callable given`pin;
        '"define: ",string[worker],"'s pin must be a niladic function recording the configuration snapshot its rows name"];
    given`pin}

/ Validate every function a worker declares - `check`, `facts` and `pin` - at declaration time,
/ so a malformed one fails then and not at the first window (#2114). `check` and `facts` once
/ reached only the first window: a `check` written as a projection passed define and then failed
/ every window, and a `facts` that was not a function logged "facts function failed" on each. A
/ stored declaration carries (::) for a hook the worker does not declare, and that is "none".
/ @param worker the worker's name
/ @param given the declaration
/ @return the declared pin, or (::) when the worker declares none
/ @throws error naming the key when check, facts or pin is declared and is not a function
/ @eg .qetl.job.bounded.require_hooks[`demo;()!()]  ->  (::)
.qetl.job.bounded.require_hooks:{[worker;given]
    who:"define: ",string worker;
    if[$[`check in key given; not (::)~given`check; 0b];
        .qetl.declared.require_callable[who;`check;given`check;"a function taking the batch and returning a table of failures"]];
    if[$[`facts in key given; not (::)~given`facts; 0b];
        .qetl.declared.require_callable[who;`facts;given`facts;"a function taking the batch and returning a dictionary of facts"]];
    .qetl.job.bounded.require_pin[worker;given]}

/ Pin the configuration this worker's rows will name, before it calculates.
/ .
/ The worker's declared `pin` records the snapshot durably and returns its ids, which are
/ logged. A worker that declares none pins nothing; a dry run writes nothing, so pins nothing
/ either (`pin_config is one of the effects it suppresses).
/ @param worker the worker's name
/ @return the ids pinned, empty when none
/ @throws error when the pin throws, or returns something other than symbols
/ @eg .qetl.job.bounded.pin_config`demo_deals_backfill  ->  `symbol$()
.qetl.job.bounded.pin_config:{[worker]
    cfg:.qetl.job.bounded.def worker;
    if[(::)~cfg`pin; :`symbol$()];
    if[not .qetl.job.bounded.runtime.allows`pin_config; :`symbol$()];
    ids:(cfg`pin)[];
    if[not (11h=type ids) and not any null ids; '"pin_config: ",string[worker],"'s pin must return the snapshot ids it recorded, a list of symbols"];
    .qetl.log.info[worker;"configuration pinned";enlist[`config_ids]!enlist ids];
    ids}

\d .
