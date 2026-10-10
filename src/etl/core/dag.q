/ dag.q - the job graph: every data engineering job's inputs and outputs, in
/ q, so a DAG can be generated and drawn without leaving the interpreter
/ (.qetl.dag).
/ .
/ Jobs declare what they READ and what they WRITE; the edges are derived by
/ matching one job's outputs against another's inputs. Nothing declares an
/ edge directly, because an edge stated by hand is a third place for the
/ graph to be wrong - and this repository already has the never-edit-the-
/ vendored-tree rule as its standing
/ lesson about process facts living in more than one file.
/ .
/ DERIVE, NEVER RE-DECLARE. The registries already know their own inputs
/ and outputs, so none of them is asked to restate anything:
/ .
/   .qetl.job.bounded.worker_cfg     bounded workers. Input is the source's remote `table_name`,
/                   reachable from the worker's `source`; output is the
/                   worker's own `dataset`, the table it writes (#769).
/   .qetl.job.continuous.feeds    continuous feeders. Output is the dataset they feed;
/                   their input is external by definition (a live feed).
/   .qetl.job.stream.jobs   streaming jobs, normalizers among them. Input is
/                   `subscribe_to`, output is `publishes`; a job that is
/                   also in .qetl.job.stream.normalizer.registry is drawn as
/                   a `normalizer`.
/   .qetl.reaction  reactions, input the dataset each watches.
/ .
/ One process q cannot describe, because it runs no declared job (tap1, a
/ diagnostic subscriber), comes from src/etl/generated/pipeline_dag.q,
/ GENERATED from Python's NON_JOB_PIPELINES. That bridge used to carry every
/ streaming process, on the ground that Python's registry was the source of
/ truth. It no longer is - Python now derives its registry from these same q
/ declarations - so carrying streams through it was a round trip back to q,
/ and it registered every backfill a second time, as an edgeless `stream`
/ node named by process.
/ .
/ The point of having it in q: `topological[]` gives a runnable order and
/ `d2[]`/`to_json[]` give a drawing, with no Python in the path. A q
/ process can therefore schedule and render its own graph.

\d .qetl.dag

/ ------------------------------------------------------------- REGISTRY

/ job -> spec, as a KEYED TABLE declared with its columns (#512). It was a
/ dictionary of dictionaries, which q collapses into a table on the first
/ entry anyway - and on PeachQ, refuses a second row added by key. inputs and
/ outputs are general columns, because each holds a symbol VECTOR of its own
/ length. A row is .qetl.dag.def; the names are .qetl.dag.defined.
jobs:([name:`symbol$()] kind:`symbol$(); inputs:(); outputs:())

/ Every registered job's name.
/ @return a symbol vector, empty when nothing has registered
/ @eg .qetl.dag.defined[]
defined:{[] (key jobs)`name}

/ The keys every spec must carry. `kind` is descriptive rather than
/ behavioural - nothing branches on it - but it is what lets a viz tool
/ colour a streaming process differently from a backfill.
required_spec:`kind`inputs`outputs

/ The kinds a job may declare. Closed on purpose: a typo like `streaming`
/ for `stream` would otherwise silently create a new category that every
/ consumer has to learn about.
/ `reaction` is a .qetl.reaction wiring: it reads the dataset it watches and writes
/ whatever it declared. Unlike the other three it may be ASSERTED rather than
/ derived - see .qetl.reaction.on's header and `reaction_edges` below - which is why
/ it is a kind of its own rather than being folded into `bounded`.
/ `normalizer` is a stream job of a particular shape - many sources, one
/ canonical output, one declared transform per source (.qetl.job.stream.normalizer). It runs
/ exactly as a `stream` does; it is a kind of its own so that a graph can
/ show where shapes converge, which is the one thing about a normalizer
/ worth seeing.
kinds:`bounded`continuous`stream`reaction`normalizer

/ Register a job's inputs and outputs.
/ .
/ Re-registering the same job REPLACES its spec rather than erroring, so
/ reloading a file during development is not a failure - the same posture
/ .qetl.job.bounded.define takes for redeclaring a worker.
/ @param job symbol naming the job, e.g. `posbook1 or `demo_deals_backfill
/ @param decl dict of kind, inputs, outputs
/ @throws error when a required key is missing, or the kind is not known
/ @eg .qetl.dag.register[`cross1;`kind`inputs`outputs!(`stream;`quotes;`symbol$())]
register:{[job;decl]
    missing:required_spec where not required_spec in key decl;
    if[count missing;
        '"register: ",string[job]," is missing ",", " sv string missing];
    if[not (decl`kind) in kinds;
        '"register: ",string[job],"'s kind ",string[decl`kind]," is not one of ",
         ", " sv string kinds];
    / Edges stored as symbol VECTORS whatever shape they arrived in, so count
    / means the number of tables - see .regtest's edge normalisation test.
    `.qetl.dag.jobs upsert (job;decl`kind;(`symbol$()),decl`inputs;(`symbol$()),decl`outputs);
    job}

/ A job's spec, or a refusal naming it.
/ @throws error when the job was never registered
def:{[job]
    if[not job in defined[];
        '"def: ",string[job]," is not a registered job"];
    jobs job}

/ Forget every registration. For tests, and for rebuilding the graph after
/ the underlying registries change.
reset:{[] jobs::0#jobs; ()}

/ The registry as a table, for reading rather than for lookup.
registry:{[]
    js:asc defined[];
    ([] job:js;
        kind:{(def[x])`kind} each js;
        inputs:{(def[x])`inputs} each js;
        outputs:{(def[x])`outputs} each js)}

/ ---------------------------------------------------------------- GRAPH

/ Which jobs write this table? Empty means nothing here produces it, which
/ makes it an external input rather than an error.
producers:{[table_name] js:defined[]; js where {[t;j] t in (def[j])`outputs}[table_name] each js}

/ Which jobs read this table?
consumers:{[table_name] js:defined[]; js where {[t;j] t in (def[j])`inputs}[table_name] each js}

/ Private: the empty edge table, so every return path has one shape.
/ @private
no_edges:{[] ([] upstream:`symbol$(); tbl:`symbol$(); downstream:`symbol$())}

/ Every edge in the graph, one row per (producer; table; consumer).
/ .
/ An input nothing produces still gets a row, with a null upstream - so a
/ drawing shows where data ENTERS the system rather than silently omitting
/ it. Dropping those rows would make an unconnected job look like a root.
edges:{[]
    js:defined[];
    if[0=count js; :no_edges[]];
    e:raze {[j]
        ins:(def[j])`inputs;
        if[0=count ins; :no_edges[]];
        raze {[j;t]
            ps:producers[t];
            $[0=count ps;
              ([] upstream:enlist `; tbl:enlist t; downstream:enlist j);
              ([] upstream:ps; tbl:count[ps]#t; downstream:count[ps]#j)]
          }[j] each ins
      } each js;
    $[0=count e; no_edges[]; e]}

/ Tables read by some job and written by none - where data enters.
external_inputs:{[]
    ins:distinct raze {(def[x])`inputs} each defined[];
    ins where 0=count each producers each ins}

/ Tables written by some job and read by none - where data comes to rest.
sinks:{[]
    outs:distinct raze {(def[x])`outputs} each defined[];
    outs where 0=count each consumers each outs}

/ Private: job-level dependency pairs, with external entry points dropped -
/ a null upstream is not a job and cannot be waited on.
/ @private
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
    remaining:defined[];
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
    remaining:defined[];
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

/ Private: the characters a d2 node id may contain.
/ @private
id_chars:.Q.a,.Q.A,.Q.n,"_"

/ Private: a d2-safe node id - every other character becomes "_".
/ .
/ An allow-list rather than a list of replacements, because the first version
/ replaced only dots and spaces and then met `demo_deals@demo_deals`, whose
/ `@` is invalid in an id and produced a diagram that silently failed to
/ render. Enumerating what is permitted cannot be outgrown that way;
/ enumerating what is forbidden always can.
/ .
/ A dot is the character that matters most here, and it matters MORE in d2
/ than it did in mermaid: `a.b` is not an invalid id in d2, it is a valid
/ reference to `b` nested inside `a`. So a job named with a dot would not
/ fail to render, it would render as a different graph - which is the worse
/ of the two failures, and the reason this allow-list is not relaxed.
/ @private
safe_id:{[s] c:string s; @[c;where not c in id_chars;:;"_"]}

/ A d2 diagram of the whole graph, as a newline-joined string.
/ .
/ d2, and not mermaid, because every committed diagram in this repository is
/ d2 under docs/diagrams/, rendered by scripts/generate/render_diagrams.py and
/ held to its source by a --check gate. Emitting the other language would mean
/ output that cannot be dropped into that directory without being redrawn, and
/ two diagram languages for a reader to know.
/ .
/ What that costs: GitHub renders a mermaid block natively and does not render
/ d2, so pasting this into an issue no longer draws itself. It is a fair
/ trade only because this output is produced INSIDE a running q process, where
/ nothing was rendering it anyway - the previous comment here argued the
/ committed-diagram case ("GitHub renders it, a reviewer sees a readable
/ diff") for a function whose output is never committed.
/ .
/ Text either way: a plain clone reads it, and a diagram change is a readable
/ diff. That part was true and still is.
/ .
/ An ASSERTED job - a reaction registered through `on_writing`, whose outputs
/ are a claim nothing checks - is drawn dashed: its node, and every edge it
/ is the upstream of, labelled "(asserted)". Its node is marked as well as
/ its edges because a reaction whose output nothing reads has no outgoing
/ edge to mark, and would otherwise look exactly as checked as a worker (#532).
d2:{[]
    e:edges[];
    asserted:asserted_jobs[];
    lines:enlist "direction: right";
    / An external input is a table nobody in the graph writes, so it is drawn
    / as a cylinder like the tables in the committed diagrams - and it needs
    / its own declaration line, because d2 takes a label from a declaration
    / rather than from inside an edge.
    ext:distinct exec tbl from e where null upstream;
    lines,:{"ext_",safe_id[x],": \"",string[x],"\" { shape: cylinder }"} each ext;
    lines,:distinct
        {[asserted;r] $[null r`upstream;
                "ext_",safe_id[r`tbl]," -> ",safe_id[r`downstream];
              r[`upstream] in asserted;
                safe_id[r`upstream]," -> ",safe_id[r`downstream],": ",string[r`tbl],
                    " (asserted) {style.stroke-dash: 3}";
                safe_id[r`upstream]," -> ",safe_id[r`downstream],": ",string r`tbl]
          }[asserted] each e;
    / A job with no edges at all would otherwise not appear.
    lonely:defined[] where {[j] 0=count ?[edges[];enlist (or;(=;`upstream;enlist j);
                                                            (=;`downstream;enlist j));0b;()]} each defined[];
    lines,:{safe_id[x],": \"",string[x],"\""} each lonely;
    lines,:{safe_id[x],".style.stroke-dash: 3"} each asserted inter defined[];
    "\n" sv lines}

/ The jobs in the graph whose outputs were ASSERTED rather than derived: the
/ reactions registered through `on_writing`, whose `derived` is 0b. A
/ reaction through `on_worker` reads its outputs from the worker's own
/ declaration, and one through plain `on` claims none, so neither is here.
/ @return the job names, as .qetl.dag.reaction_job builds them
/ @eg .qetl.dag.asserted_jobs[]
asserted_jobs:{[]
    r:select from reaction_edges[] where not derived, 0<count each outputs;
    `symbol$reaction_job'[r`dataset;r`reaction]}

/ The graph as JSON, for a viz tool that would rather not parse d2. Each edge
/ carries `asserted`, so a tool can draw a claimed edge differently from a
/ checked one without re-deriving which is which.
to_json:{[]
    asserted:asserted_jobs[];
    .j.j `jobs`edges`asserted_jobs`external_inputs`sinks`order!
        (registry[]; update asserted:upstream in asserted from edges[]; asserted;
         external_inputs[]; sinks[]; topological[])}

/ ----------------------------------------------------- ADOPTION (derive)

/ Register every bounded worker from .qetl.job.bounded.worker_cfg, deriving its inputs and
/ outputs from the source declaration it already names.
/ .
/ A worker reads the source's remote `table_name` and writes its `dataset`, so
/ its input is not restated on the worker - which is the whole point: a
/ worker that declared its own inputs could disagree with the source it
/ actually reads. Its output is the dataset because that is where publish
/ writes and what coverage records (#769).
/ A node naming a table that lives on an EXTERNAL source, as "table@source".
/ .
/ A table's identity in this graph is (location, name), not name - and the
/ shipped sources make that unavoidable rather than theoretical. Both of them
/ declare `table_name` and `target` as the SAME symbol: demo_deals reads a remote
/ `demo_deals` and writes a local `demo_deals`. Keyed on the bare name, a
/ worker therefore consumed exactly what it produced, and the first run of
/ adopt_all[] reported a cycle among both workers - correctly, given what it
/ had been told.
/ .
/ Qualifying the remote side fixes the model rather than the symptom. The
/ same trap in a different costume as `quote` versus `quotes`: two
/ different things one character - here, zero characters - apart.
/ .
/ Parseable on purpose, so a viz tool can split it back into source and table
/ rather than having to treat the node as opaque.
/ @eg .qetl.dag.external_ref[`demo_deals;`demo_deals]  ->  `demo_deals@demo_deals
external_ref:{[source;table_name] `$(string table_name),"@",string source}

/ Register every bounded worker into the job graph, from .qetl.job.bounded's own registry.
/ @return the worker names registered, empty when .qetl.job.bounded is not loaded
/ @eg .qetl.dag.adopt_workers[]
adopt_workers:{[]
    if[not `worker_cfg in key @[value;`.qetl.job.bounded;{()}]; :`$()];
    ws:.qetl.job.bounded.defined[];
    {[w]
        cfg:.qetl.job.bounded.def w;
        d:.qetl.source.def cfg`source;
        register[w;`kind`inputs`outputs!
            (`bounded; external_ref[cfg`source;d`table_name]; cfg`dataset)]
      } each ws;
    ws}

/ Register every continuous feeder from .qetl.job.continuous.feeds.
/ .
/ A feeder's input is external by definition - it tails a live feed, which
/ is why there is no coverage ledger on that path - so it declares
/ no inputs and appears as a root.
adopt_feeders:{[]
    if[not `feeds in key @[value;`.qetl.job.continuous;{()}]; :`$()];
    fs:key .qetl.job.continuous.feeds;
    {[f] register[f;`kind`inputs`outputs!(`continuous; `$(); .qetl.job.continuous.feeds f)]} each fs;
    fs}

/ Register every streaming job, from .qetl.job.stream's own registry.
/ .
/ Named by JOB, as a bounded worker is, not by the process that runs it: one
/ identity for every node, and the one reactions and coverage already use.
/ A job that is also a normalizer is registered as one, so a drawing shows
/ where shapes converge.
/ .
/ `first` because each declaration is stored ENLISTED - see .qetl.job.stream.jobs
/ on the same-keyed-dicts trap.
/ @return the job names registered, empty when .qetl.job.stream is not loaded
/ @eg .qetl.dag.adopt_streams[]
adopt_streams:{[]
    if[not `jobs in key @[value;`.qetl.job.stream;{()}]; :`$()];
    js:key .qetl.job.stream.jobs;
    norms:@[{key .qetl.job.stream.normalizer.registry};::;{`$()}];
    {[norms;j]
        d:first .qetl.job.stream.jobs j;
        register[j;`kind`inputs`outputs!
            ($[j in norms;`normalizer;`stream]; d`subscribe_to; d`publishes)]
      }[norms] each js;
    js}

/ Register the processes that run no declared job, if the generated bridge
/ has been loaded.
/ .
/ src/etl/generated/pipeline_dag.q defines register_pipelines, GENERATED
/ from Python's NON_JOB_PIPELINES: a process like tap1 has no q declaration
/ to read, so this is the one place it can come from. Streaming jobs are not
/ in it - adopt_streams reads them from q. Absent (a bare ETL process that
/ never loads the bridge), this returns empty rather than throwing.
/ @return the process names registered, empty when the bridge is not loaded
/ @eg .qetl.dag.adopt_pipelines[]
adopt_pipelines:{[]
    $[`register_pipelines in key `.qetl.dag; register_pipelines[]; `$()]}

/ Register every .qetl.reaction reaction as a job.
/ .
/ A reaction's INPUT is the dataset it watches, which is a fact - it is what
/ fires it. Its OUTPUT is whatever it declared: derived from the worker's own
/ declaration for `on_worker`, asserted by the caller for `on_writing`, and
/ empty for a plain `on`, which therefore appears as a terminal node.
/ .
/ WHY AN EMPTY OUTPUT IS A NODE AT ALL rather than being left out: the
/ reaction exists and reads that dataset, and a graph that omitted it would
/ show the dataset as a sink - "nothing consumes this" - which is a stronger
/ and wronger claim than "something consumes this and did not say what it
/ writes". `reaction_edges` names which is which so neither has to be guessed.
/ .
/ The job name is `<dataset>~<reaction>`: a reaction name is unique per
/ dataset rather than globally, so the dataset has to be part of the node's
/ identity or two reactions called `rebuild` would collapse into one node.
/ @return the job names registered, empty when .qetl.reaction is not loaded
/ @eg .qetl.dag.adopt_reactions[]
adopt_reactions:{[]
    if[not `reactions in key @[value;`.qetl.reaction;{()}]; :`$()];
    raze {[ds]
        rs:.qetl.reaction.for_dataset ds;
        {[ds;name;outs]
            job:reaction_job[ds;name];
            register[job;`kind`inputs`outputs!(`reaction;ds;outs)];
            job}[ds] .' flip (rs`name;rs`outputs)
      } each key .qetl.reaction.reactions}

/ The job name a reaction is registered under.
/ .
/ ALWAYS BUILD IT WITH THIS, never by hand: `~` cannot appear in a q symbol
/ LITERAL, so `\`demo_deals~rebuild` parses as `\`demo_deals ~ rebuild` - a
/ match against a variable called `rebuild` - and fails with a value error
/ naming that variable rather than anything about the graph. Measured while
/ writing the tests for this.
/ .
/ The separator is `~` and not `.` or `_` on purpose: `.` reads as a
/ namespace path and `_` already appears inside both dataset and reaction
/ names, so a name built with either could not be split back into its two
/ halves. The cost is that it must be constructed rather than typed, which
/ is what this function is for.
/ @param dataset the dataset the reaction watches
/ @param name the reaction's name, unique within that dataset
/ @return the job name, as a symbol
/ @eg .qetl.dag.reaction_job[`demo_deals;`rebuild_positions]
reaction_job:{[dataset;name] `$(string dataset),"~",string name}

/ Every reaction edge, and whether its output was derived or asserted.
/ .
/ The question a reader of the graph needs answered and cannot get from the
/ edges alone: a `bounded` job's output comes from its source declaration and
/ cannot disagree with what it does, while a reaction's may be a promise. This
/ says which, per reaction, so a drawing can mark an asserted edge rather than
/ presenting all of them as equally checked.
/ @return a table of dataset, reaction, outputs, derived
reaction_edges:{[]
    if[not `reactions in key @[value;`.qetl.reaction;{()}]; :([] dataset:`symbol$(); reaction:`symbol$(); outputs:(); derived:`boolean$())];
    raze {[ds]
        rs:.qetl.reaction.for_dataset ds;
        ([] dataset:count[rs]#ds; reaction:rs`name; outputs:rs`outputs; derived:rs`derived)
      } each key .qetl.reaction.reactions}

/ Rebuild the whole graph from every registry that declares one.
/ .
/ Resets first, so calling it twice gives the same graph rather than an
/ accumulation - and every registration below is reproducible from a
/ registry, so nothing is lost by clearing.
/ .
/ Reactions LAST, because adopt_reactions reads .qetl.reaction's registry and a
/ reaction's output may name a dataset a worker registered above - the order
/ does not matter to `register`, which takes what it is given, but it keeps
/ the graph's own layering readable.
adopt_all:{[]
    reset[];
    `workers`feeders`streams`pipelines`reactions!
        (adopt_workers[]; adopt_feeders[]; adopt_streams[]; adopt_pipelines[]; adopt_reactions[])}

/ Refuse a cyclic graph, leaving the registry as it was found.
/ .
/ The load-time check (#1090): src/etl/init.q calls this after the declarations
/ are in, so a cycle among workers, feeders, streams and reactions stops the
/ process that loaded it rather than waiting for someone to ask for an order.
/ It builds the graph from the registries (adopt_all) and orders it, then puts
/ `jobs` back - a check that left the graph populated would change what every
/ later reader of an unbuilt graph sees. Only what is loaded is checked, so a
/ selective load (.qetl.load.only) checks the subset it chose.
/ .
/ Reactions registered by hand AFTER the load are not covered; call this again.
/ @return the number of jobs in the acyclic graph
/ @throws error naming the jobs in the cycle ("topological: cycle among ...")
/ @eg .qetl.dag.check_acyclic[]
check_acyclic:{[]
    keep:jobs;
    r:@[{adopt_all[]; count topological[]};::;{(`err;x)}];
    jobs::keep;
    if[`err~first r; 'last r];
    r}

\d .
