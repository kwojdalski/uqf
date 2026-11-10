/ twin.q - a backfill declares which live job it repairs, and q holds the pair
/ to that (.qetl.twin, #2103).
/ .
/ A backfill that refills a streaming job's table is only useful while it still
/ writes what the live job publishes. `uqs job new --kind backfill --twin-of`
/ derived the destination and columns from the live job ONCE, when it wrote the
/ files; nothing afterwards kept the two compatible. Change the live job to
/ publish marks_v2 - another valid table, same columns - and the backfill kept
/ writing marks: each producer still met its own schema, and the backfill no
/ longer repaired the live output.
/ .
/ So the relationship is declared, on the backfill, and validated by q whenever
/ the two declarations are both loaded - with no CLI in the way:
/ .
/   twin_of           the live streaming job this worker repairs
/   twin_calculation  `shared, optionally: the two promise ONE calculation
/ .
/ WHICH OUTPUT. A bounded worker fills exactly one dataset (its `outputs`), so
/ that dataset IS the live output it repairs. A live job with several outputs
/ needs no second key: the twin names the one it writes, and it must be among
/ the live job's. Naming a table the live job does not list is refused even when
/ the two tables have identical columns - identical columns are what made the
/ drift invisible.
/ .
/ THE DATASET CONTRACT is the plant's: the twin's transform output must satisfy
/ .qetl.plant.problems for the table, the same check the live job's publication
/ is held to (tests/q/test_job_output_contracts.q).
/ .
/ THE CALCULATION is the one thing a twin does not get for free. A twin reads a
/ different source through a different transform, so equal columns say nothing
/ about equal arithmetic. Three outcomes, and only the first is ever claimed:
/   shared           both apply the same registered transform (declared as
/                    `twin_calculation: `shared, which REFUSES a pair that does
/                    not; or found equal without being declared)
/   not_established  no shared transform. Allowed - a twin's transform is often
/                    an adapter over the same core - but recorded, logged and
/                    reported as "calculation equivalence not established",
/                    never implied. tests/q/test_twins.q holds the pairs it
/                    lists to the live job's own examples.
/   unchecked        the live job is not loaded in this process
/ .
/ ONLY DECLARED TWINS are validated. Two producers of one dataset are not a twin
/ (a backfill reading a csv drop and a normalizer both write `executions`):
/ nothing here looks at datasets for a pair. .qetl.uptime.twins, which `uqs job
/ gaps` uses to pick a refill, still goes by dataset - see its comment.
/ .
/ WHAT MAY DIFFER stays free: transport, timestamps, windowing, width, source,
/ and the whole execution lifecycle. The pair is held to its output, its
/ contract and its calculation, and to nothing else.
/ .
/ Validation runs from BOTH ends, because workers load before streaming jobs
/ and a test may define either first: .qetl.job.bounded.define checks a twin
/ whose live job is already loaded, .qetl.job.stream.define checks the twins
/ already waiting for it. A process that loads every declaration then calls
/ .qetl.twin.require_resolved, so a typo in twin_of is refused rather than
/ never checked.
/ .
/ Its own file because bounded_worker.q and stream_job.q are held to a line
/ budget (q-module-size).

\d .

/ The promises a twin can make about its calculation. Today one.
.qetl.twin.promises:enlist `shared

/ The words a not-established twin is reported with.
.qetl.twin.unestablished:"calculation equivalence not established"

/ Validate and resolve a worker's twin declaration: both keys read as symbols,
/ absent ones as ` (a typed column cannot hold (::) beside a symbol).
/ @param worker the worker's name
/ @param decl its declaration
/ @return the declaration with twin_of and twin_calculation set
/ @throws error when twin_of is not a symbol naming another job, or twin_calculation is declared without twin_of or is not a promise
/ @eg .qetl.twin.require_declared[`w;()!()]`twin_of  ->  `
.qetl.twin.require_declared:{[worker;decl]
    who:"define: ",string worker;
    / A null twin_of is how a stored declaration says "no twin" (worker_cfg holds a symbol
    / column), so reading one back and defining another worker from it is not a twin.
    has:$[`twin_of in key decl; not null decl`twin_of; 0b];
    if[(`twin_of in key decl) and not -11h=type decl`twin_of;
        'who,"'s twin_of must be a symbol naming the streaming job it repairs, e.g. `fx_markout"];
    if[$[has; (decl`twin_of)=worker; 0b]; 'who,"'s twin_of names itself - a twin repairs a streaming job's output"];
    if[(not has) and $[`twin_calculation in key decl; not null decl`twin_calculation; 0b];
        'who," declares twin_calculation without twin_of - the promise is about the live job it repairs; name it with twin_of"];
    if[has and `twin_calculation in key decl;
        c:decl`twin_calculation;
        ok:$[-11h=type c; (null c) or c in .qetl.twin.promises; 0b];
        if[not ok;
            'who,"'s twin_calculation must be one of ",(", " sv string .qetl.twin.promises)," - the only promise q can hold a pair to; drop the key to leave the calculation unestablished"]];
    decl[`twin_of]:$[has; decl`twin_of; `];
    decl[`twin_calculation]:$[has and `twin_calculation in key decl; decl`twin_calculation; `];
    decl}

/ A loaded streaming job's declaration, or :: when it is not loaded.
/ @param job the job's name
/ @return the declaration dict, or ::
/ @private
.qetl.twin.live_decl:{[job]
    jobs:@[value;`.qetl.job.stream.jobs;{()}];
    $[job in key jobs; first jobs job; ::]}

/ The registered transform a live job declares, or ` when it declares none.
/ @param live the live job's declaration
/ @return a symbol
/ @private
.qetl.twin.live_transform:{[live] $[99h=type live; live`transform; `]}

/ Does the twin share its live job's calculation?
/ @param twin_transform the transform the backfill applies
/ @param live the live job's declaration, or :: when it is not loaded
/ @return `shared when both apply one registered transform, `not_established when they do not or the live job declares none, `unchecked when it is not loaded
/ @eg .qetl.twin.calculation[`t;enlist[`transform]!enlist `]  ->  `not_established
.qetl.twin.calculation:{[twin_transform;live]
    if[(::)~live; :`unchecked];
    lt:.qetl.twin.live_transform[live];
    $[(not null lt) and lt=twin_transform; `shared; `not_established]}

/ Private: what stops a twin from repairing the live job, as strings.
/ @private
.qetl.twin.problems:{[cfg;job;live]
    out:(),live`outputs;
    ds:cfg`dataset;
    p:();
    if[not ds in out;
        p,:enlist "it writes ",string[ds],", which ",string[job]," does not list among its outputs (",(", " sv string out),
            ") - a twin repairs one of the live job's outputs, even when another table has the same columns; ",
            "point outputs at one of them, or update twin_of"];
    / guarded: PeachQ's `,:` of () onto a one-string list appends an empty element
    c:.qetl.twin.contract_problems[ds;cfg`transform];
    if[count c; p,:c];
    lt:.qetl.twin.live_transform[live];
    if[(cfg[`twin_calculation]=`shared) and not lt=cfg`transform;
        p,:enlist "it promises a shared calculation (twin_calculation `shared) but ",
            $[null lt; string[job]," declares no transform";
              "applies ",string[cfg`transform]," where ",string[job]," applies ",string lt],
            " - give both the same registered transform, or drop the promise"];
    p}

/ The problems with a transform's output as rows of a dataset, against the
/ plant's contract for it: the same check a live job's publication is held to.
/ `time` is the plant's to stamp, so it is not compared.
/ @param dataset the plant table the twin writes
/ @param transform the registered transform the twin applies
/ @return strings, empty when the output satisfies the contract
/ @eg .qetl.twin.contract_problems[`not_a_plant_table;`x]  ->  enlist "not_a_plant_table is not a plant table, so there is no dataset contract to hold the twin's transform to"
.qetl.twin.contract_problems:{[dataset;transform]
    if[not dataset in .qetl.plant.names[];
        :enlist string[dataset]," is not a plant table, so there is no dataset contract to hold the twin's transform to"];
    s:.qetl.transform.output_schema transform;
    r:.qetl.plant.problems[dataset;(cols[s] except `time)#s];
    r where 0<count each r}

/ Check one declared twin against its live job, or refuse it. A twin with no
/ shared transform passes and is logged as not established.
/ @param worker the backfill's name
/ @param cfg its configuration, with dataset, transform, twin_of and twin_calculation
/ @param live the live job's declaration
/ @return the calculation outcome
/ @throws error naming the twin, the live job and every problem at once
/ @eg .qetl.twin.check[`w;`dataset`transform`twin_of`twin_calculation!(`exec_bar;`hdb_exec_bars;`exec_bars;`);.qetl.job.stream.def `exec_bars]  ->  `not_established
.qetl.twin.check:{[worker;cfg;live]
    job:cfg`twin_of;
    p:.qetl.twin.problems[cfg;job;live];
    if[count p;
        '"twin: ",string[worker]," is declared a twin of ",string[job]," but ","; " sv p];
    c:.qetl.twin.calculation[cfg`transform;live];
    if[c=`not_established;
        .[{.qetl.log.ch.dbg[`config;y;(enlist[`worker]!enlist x),z]};(worker;"twin declared: ",.qetl.twin.unestablished;
            `twin_of`outputs`transform`live_transform!(job;cfg`dataset;cfg`transform;.qetl.twin.live_transform[live]));::]];
    c}

/ Check a worker being defined against its live job, when that job is already
/ loaded. A live job not loaded yet is checked when it is defined.
/ @param worker the backfill's name
/ @param cfg its declaration, as define resolved it
/ @return the worker name
/ @throws error when the pair is not compatible, or twin_of names a bounded worker
.qetl.twin.check_worker:{[worker;cfg]
    job:cfg`twin_of;
    if[null job; :worker];
    if[job in .qetl.job.bounded.defined[];
        '"twin: ",string[worker]," declares twin_of ",string[job],", which is a bounded worker - a twin repairs the output of a streaming job"];
    live:.qetl.twin.live_decl[job];
    if[99h=type live; .qetl.twin.check[worker;cfg;live]];
    worker}

/ Check the twins waiting for a streaming job against it, as the job is defined.
/ @param job the live job's name
/ @param decl its declaration
/ @return the job name
/ @throws error when a declared twin no longer repairs the job
.qetl.twin.check_live:{[job;decl]
    ws:exec name from .qetl.job.bounded.worker_cfg where twin_of=job;
    {[decl;w] .qetl.twin.check[w;.qetl.job.bounded.def w;decl]}[decl] each ws;
    job}

/ The workers declared a twin of a job that no loaded streaming job is.
/ @return worker names, a symbol list
/ @eg .qetl.twin.unresolved[]
.qetl.twin.unresolved:{[]
    live:key @[value;`.qetl.job.stream.jobs;{()}];
    exec name from .qetl.job.bounded.worker_cfg where not null twin_of, not twin_of in live}

/ Refuse a twin whose live job never loaded. For a process that loaded every
/ declaration: a partial load (.qetl.load.only) legitimately lacks the live job.
/ @return the empty list
/ @throws error naming each twin and the job it names
.qetl.twin.require_resolved:{[]
    u:.qetl.twin.unresolved[];
    if[count u;
        '"twin: ",("; " sv {string[x]," declares twin_of ",string[.qetl.job.bounded.def[x]`twin_of]," which is no loaded streaming job"} each u),
            " - check the spelling, or declare the live job"];
    u}

/ Every declared twin, with what q knows of its calculation. A pair whose
/ calculation is `not_established says so; nothing is implied.
/ @return a table of worker, live, output, twin_transform, live_transform, calculation, note
/ @eg cols .qetl.twin.report[]  ->  `worker`live`output`twin_transform`live_transform`calculation`note
.qetl.twin.report:{[]
    w:exec name, live:twin_of, output:dataset, twin_transform:transform, promise:twin_calculation from .qetl.job.bounded.worker_cfg where not null twin_of;
    / each live name resolved on its own: an `each` that RETURNED the declarations would
    / hand back a table whenever they share keys (see .qetl.job.stream.jobs)
    lt:{.qetl.twin.live_transform[.qetl.twin.live_decl[x]]} each w`live;
    c:{.qetl.twin.calculation[x;.qetl.twin.live_decl[y]]}'[w`twin_transform;w`live];
    ([] worker:w`name; live:w`live; output:w`output; twin_transform:w`twin_transform; live_transform:lt; calculation:c;
        note:{$[x=`shared; ""; x=`unchecked; "the live job is not loaded in this process"; .qetl.twin.unestablished]} each c)}

\d .
