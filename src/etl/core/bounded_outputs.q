/ bounded_outputs.q - a bounded worker declares the one dataset it fills as
/ `outputs` (.qetl.job.bounded, #2102).
/ .
/ A streaming job's `outputs` are the tables it publishes; a worker's is the
/ dataset coverage, replacement, reactions and the run ledger are all keyed on.
/ The key is shared, the meaning is the job type's: a worker's outputs hold
/ EXACTLY ONE dataset, because its coverage is recorded per dataset and a worker
/ filling two would have one ledger row describing two tables. Zero, several or
/ an atom is refused at declaration, naming the count.
/ .
/ The one dataset is stored as the worker's `dataset` (.qetl.job.bounded.worker_cfg),
/ DERIVED here, so the lifecycle reads a symbol and not a list it must index.
/ The old key is refused with its migration, not accepted: there is no alias.
/ .
/ Its own file because bounded_worker.q is held to its line budget (q-module-size).

\d .

/ Check a worker's declared `outputs` and derive its dataset, so a malformed one fails at
/ declaration and not on a first run. An absent `outputs` is left for define's missing-key
/ check, which names every missing key at once.
/ @param worker the worker's name
/ @param given the declaration
/ @return the declaration with `dataset` set from its one output
/ @throws error when the retired `dataset` key is declared, or outputs is not a symbol list of exactly one
/ @eg .qetl.job.bounded.require_outputs[`demo;enlist[`outputs]!enlist enlist `d]`dataset  ->  `d
.qetl.job.bounded.require_outputs:{[worker;given]
    if[`dataset in key given;
        '"define: ",string[worker]," declares dataset, which is now outputs - rename the key and enlist the value (outputs: enlist `<dataset>); a bounded worker's outputs hold exactly one dataset"];
    if[not `outputs in key given; :given];
    if[not 11h=type given`outputs;
        '"define: ",string[worker],"'s outputs must be a symbol list holding exactly one dataset, e.g. enlist `demo_deals"];
    if[not 1=count given`outputs;
        '"define: ",string[worker]," declares ",string[count given`outputs]," outputs - a bounded worker fills exactly one dataset, because its coverage is recorded per dataset; split it into one worker per dataset"];
    given,enlist[`dataset]!enlist first given`outputs}
