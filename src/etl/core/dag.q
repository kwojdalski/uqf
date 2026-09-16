/ dag.q - the job graph: every data engineering job's inputs and outputs, in
/ q, so a DAG can be generated and drawn without leaving the interpreter
/ (.qdag).
/ .
/ Jobs declare what they READ and what they WRITE; the edges are derived by
/ matching one job's outputs against another's inputs. Nothing declares an
/ edge directly, because an edge stated by hand is a third place for the
/ graph to be wrong - and this repository already has H-01 as its standing
/ lesson about process facts living in more than one file.
/ .
/ DERIVE, NEVER RE-DECLARE. Three registries already know their own inputs
/ and outputs, so none of them is asked to restate anything:
/ .
/   .qbw.config     bounded workers. Input is the source's remote `table`,
/                   output is its `target` - both already on the .qsrc
/                   declaration, reachable from the worker's `source`.
/   .qcont.feeds    continuous feeders. Output is the dataset they feed;
/                   their input is external by definition (a live feed).
/   PIPELINES       the nine TorQ streaming processes. H-05 already decided
/                   the Python Pipeline registry is the source of truth, so
/                   this side is GENERATED into q rather than re-declared -
/                   the same shape as generate_operational_docs.py, and for
/                   the same reason.
/ .
/ The point of having it in q: `topological[]` gives a runnable order and
/ `mermaid[]`/`to_json[]` give a drawing, with no Python in the path. A q
/ process can therefore schedule and render its own graph.

\d .qdag

/ ------------------------------------------------------------- REGISTRY

/ job -> spec dict. A dictionary rather than a table so a spec can hold
/ variable-length symbol vectors without nesting rules getting in the way.
jobs:(`symbol$())!();

/ The keys every spec must carry. `kind` is descriptive rather than
/ behavioural - nothing branches on it - but it is what lets a viz tool
/ colour a streaming process differently from a backfill.
required_spec:`kind`inputs`outputs

/ The kinds a job may declare. Closed on purpose: a typo like `streaming`
/ for `stream` would otherwise silently create a new category that every
/ consumer has to learn about.
kinds:`bounded`continuous`stream

/ Register a job's inputs and outputs.
/ .
/ Re-registering the same job REPLACES its spec rather than erroring, so
/ reloading a file during development is not a failure - the same posture
/ .qbw.define takes for redeclaring a worker.
/ @param job symbol naming the job, e.g. `posbook1 or `demo_deals_backfill
/ @param spec dict of kind, inputs, outputs
/ @throws error when a required key is missing, or the kind is not known
/ @eg .qdag.register[`cross1;`kind`inputs`outputs!(`stream;`quotes;`symbol$())]
register:{[job;spec]
    missing:required_spec where not required_spec in key spec;
    if[count missing;
        '"register: ",string[job]," is missing ",", " sv string missing];
    if[not (spec`kind) in kinds;
        '"register: ",string[job],"'s kind ",string[spec`kind]," is not one of ",
         ", " sv string kinds];
    jobs[job]:`kind`inputs`outputs!(spec`kind; `$(); `$());
    jobs[job;`inputs]:(),spec`inputs;
    jobs[job;`outputs]:(),spec`outputs;
    job}

/ A job's spec, or a refusal naming it.
/ @throws error when the job was never registered
declaration:{[job]
    if[not job in key jobs;
        '"declaration: ",string[job]," is not a registered job"];
    jobs job}

/ Forget every registration. For tests, and for rebuilding the graph after
/ the underlying registries change.
reset:{[] jobs::(`symbol$())!(); ()}

/ The registry as a table, for reading rather than for lookup.
registry:{[]
    js:asc key jobs;
    ([] job:js;
        kind:{(declaration x)`kind} each js;
        inputs:{(declaration x)`inputs} each js;
        outputs:{(declaration x)`outputs} each js)}

/ ---------------------------------------------------------------- GRAPH

/ Which jobs write this table? Empty means nothing here produces it, which
/ makes it an external input rather than an error.
producers:{[tbl] js:key jobs; js where {[t;j] t in (declaration j)`outputs}[tbl] each js}

/ Which jobs read this table?
consumers:{[tbl] js:key jobs; js where {[t;j] t in (declaration j)`inputs}[tbl] each js}

/ Private: the empty edge table, so every return path has one shape.
no_edges:{[] ([] upstream:`symbol$(); tbl:`symbol$(); downstream:`symbol$())}

/ Every edge in the graph, one row per (producer; table; consumer).
/ .
/ An input nothing produces still gets a row, with a null upstream - so a
/ drawing shows where data ENTERS the system rather than silently omitting
/ it. Dropping those rows would make an unconnected job look like a root.
edges:{[]
    js:key jobs;
    if[0=count js; :no_edges[]];
    e:raze {[j]
        ins:(declaration j)`inputs;
        if[0=count ins; :no_edges[]];
        raze {[j;t]
            ps:producers t;
            $[0=count ps;
              ([] upstream:enlist `; tbl:enlist t; downstream:enlist j);
              ([] upstream:ps; tbl:count[ps]#t; downstream:count[ps]#j)]
          }[j] each ins
      } each js;
    $[0=count e; no_edges[]; e]}

/ Tables read by some job and written by none - where data enters.
external_inputs:{[]
    ins:distinct raze {(declaration x)`inputs} each key jobs;
    ins where 0=count each producers each ins}

/ Tables written by some job and read by none - where data comes to rest.
sinks:{[]
    outs:distinct raze {(declaration x)`outputs} each key jobs;
    outs where 0=count each consumers each outs}

/ Private: job-level dependency pairs, with external entry points dropped -
/ a null upstream is not a job and cannot be waited on.
job_edges:{[] distinct select upstream, downstream from edges[] where not null upstream}

/ ----------------------------------------------------------- ORDERING

/ A runnable order: every job appears after everything it reads from.
/ .
/ Kahn's algorithm, one layer at a time. The layers matter beyond ordering -
/ jobs in the same layer have no dependency between them, so a scheduler can
/ run a layer in parallel, which is the question a DAG is usually asked.
/ .
/ Refuses on a cycle and NAMES the jobs still unplaced, because "there is a
/ cycle" without saying where leaves the reader tracing edges by hand.
/ @throws error naming every job in the cycle
topological:{[]
    e:job_edges[];
    remaining:key jobs;
    order:`$();
    while[count remaining;
        blocked:distinct exec downstream from e where upstream in remaining,
                                                     downstream in remaining;
        ready:asc remaining where not remaining in blocked;
        if[0=count ready;
            '"topological: cycle among ",", " sv string asc remaining];
        order,:ready;
        remaining:remaining except ready];
    order}

/ The same order, grouped into parallel-safe layers.
layers:{[]
    e:job_edges[];
    remaining:key jobs;
    out:();
    while[count remaining;
        blocked:distinct exec downstream from e where upstream in remaining,
                                                     downstream in remaining;
        ready:asc remaining where not remaining in blocked;
        if[0=count ready;
            '"layers: cycle among ",", " sv string asc remaining];
        out,:enlist ready;
        remaining:remaining except ready];
    out}

/ ----------------------------------------------------------- RENDERING

/ Private: the characters a mermaid node id may contain.
id_chars:.Q.a,.Q.A,.Q.n,"_"

/ Private: a mermaid-safe node id - every other character becomes "_".
/ .
/ An allow-list rather than a list of replacements, because the first version
/ replaced only dots and spaces and then met `demo_deals@demo_deals`, whose
/ `@` is invalid in a mermaid id and produced a diagram that silently failed
/ to render. Enumerating what is permitted cannot be outgrown that way;
/ enumerating what is forbidden always can.
safe_id:{[s] c:string s; @[c;where not c in id_chars;:;"_"]}

/ A mermaid flowchart of the whole graph, as a newline-joined string.
/ .
/ Mermaid rather than an image format: GitHub renders it, a plain clone reads
/ it as text, and a reviewer sees a diagram change as a readable diff. That is
/ J-03's answer applied here.
mermaid:{[]
    e:edges[];
    lines:enlist "flowchart LR";
    lines,:{"    ",x} each distinct
        {[r] $[null r`upstream;
                "ext_",safe_id[r`tbl],"[(",string[r`tbl],")] --> ",safe_id r`downstream;
                safe_id[r`upstream]," -->|",string[r`tbl],"| ",safe_id r`downstream]
          } each e;
    / A job with no edges at all would otherwise not appear.
    lonely:key[jobs] where {[j] 0=count ?[edges[];enlist (or;(=;`upstream;enlist j);
                                                            (=;`downstream;enlist j));0b;()]} each key jobs;
    lines,:{"    ",safe_id[x],"[",string[x],"]"} each lonely;
    "\n" sv lines}

/ The graph as JSON, for a viz tool that would rather not parse mermaid.
to_json:{[]
    .j.j `jobs`edges`external_inputs`sinks`order!
        (registry[]; edges[]; external_inputs[]; sinks[]; topological[])}

/ ----------------------------------------------------- ADOPTION (derive)

/ Register every bounded worker from .qbw.config, deriving its inputs and
/ outputs from the source declaration it already names.
/ .
/ A worker reads the source's remote `table` and writes its `target`, so
/ neither has to be restated on the worker - which is the whole point: a
/ worker that declared its own inputs could disagree with the source it
/ actually reads.
/ A node naming a table that lives on an EXTERNAL source, as "table@source".
/ .
/ A table's identity in this graph is (location, name), not name - and the
/ shipped sources make that unavoidable rather than theoretical. Both of them
/ declare `table` and `target` as the SAME symbol: demo_deals reads a remote
/ `demo_deals` and writes a local `demo_deals`. Keyed on the bare name, a
/ worker therefore consumed exactly what it produced, and the first run of
/ adopt_all[] reported a cycle among both workers - correctly, given what it
/ had been told.
/ .
/ Qualifying the remote side fixes the model rather than the symptom. The
/ same trap in a different costume as `quote` versus `quotes` (N-03): two
/ different things one character - here, zero characters - apart.
/ .
/ Parseable on purpose, so a viz tool can split it back into source and table
/ rather than having to treat the node as opaque.
/ @eg .qdag.external_ref[`demo_deals;`demo_deals]  ->  `demo_deals@demo_deals
external_ref:{[source;tbl] `$(string tbl),"@",string source}

adopt_workers:{[]
    if[not `qbw in key `; :`$()];
    ws:key .qbw.config;
    {[w]
        cfg:.qbw.config w;
        d:.qsrc.declaration cfg`source;
        register[w;`kind`inputs`outputs!
            (`bounded; external_ref[cfg`source;d`table]; d`target)]
      } each ws;
    ws}

/ Register every continuous feeder from .qcont.feeds.
/ .
/ A feeder's input is external by definition - it tails a live feed, which
/ is why there is no coverage ledger on that path (ETL-22) - so it declares
/ no inputs and appears as a root.
adopt_feeders:{[]
    if[not `qcont in key `; :`$()];
    fs:key .qcont.feeds;
    {[f] register[f;`kind`inputs`outputs!(`continuous; `$(); .qcont.feeds f)]} each fs;
    fs}

/ Register the nine TorQ streaming processes, if the generated bridge has
/ been loaded.
/ .
/ src/etl/generated/pipeline_dag.q defines register_pipelines; it is
/ GENERATED from the Python Pipeline registry, because H-05 made that the
/ source of truth and a hand-written q copy would be a second place for the
/ same edges to be wrong. Absent - a bare ETL process that never loads it -
/ this returns empty rather than throwing, so the graph is simply smaller.
adopt_pipelines:{[]
    $[`register_pipelines in key `.qdag; register_pipelines[]; `$()]}

/ Rebuild the whole graph from every registry that declares one.
/ .
/ Resets first, so calling it twice gives the same graph rather than an
/ accumulation - and every registration below is reproducible from a
/ registry, so nothing is lost by clearing.
adopt_all:{[]
    reset[];
    `workers`feeders`pipelines!(adopt_workers[]; adopt_feeders[]; adopt_pipelines[])}

\d .
