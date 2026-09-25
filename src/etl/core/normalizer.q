/ normalizer.q - the job kind that takes many differently-shaped sources and
/ publishes one canonical table (.qnorm).
/ .
/ WHY A KIND, AND NOT JUST A JOB THAT SUBSCRIBES TO TWO TABLES. A streaming
/ job could always subscribe to `trades` and `crypto_trades` and branch on
/ the table name in its handler. Two jobs did exactly that - posbook and
/ crypto_posbook - and the branch was the whole difference between them:
/ the same transform, two subscriptions, two ways of reading a mid. A
/ third venue would have meant a third job, or a third branch in a handler
/ that by then knows about three tape formats.
/ .
/ The thing they had in common is worth naming: N tables carrying the same
/ FACT in different SHAPES, and one shape everything downstream should see.
/ That is what a normalizer is. The shell here owns the dispatch (which
/ source arrived), the validation (does every mapping produce the one
/ shape) and the publish; an instance declares its output and one mapping
/ per source, and nothing else.
/ .
/ EVERY MAPPING IS A DECLARED .qxf TRANSFORM. Not a bare function, for the
/ reason every job's computation is one: a transform carries its input
/ schema, its output schema and worked examples, and tests/q/test_transform.q
/ verifies every registered transform on every run of the suite. So a
/ normalizer's mappings are tested before the normalizer exists, and define
/ below has one more thing to check that nothing else can: that every
/ mapping's declared OUTPUT is the canonical table, column for column and
/ type for type. A mapping that drifts fails at load.
/ .
/ WHAT THE SHELL DOES WITH A BATCH. Looks the source up, projects the
/ batch onto the columns that source's transform declares (the wire carries
/ `time` and whatever else the plant stamps; the mapping reads what it
/ said it reads), applies the transform, and publishes the rows onto the
/ canonical table. The instance's file never sees a batch.
/ .
/ REGISTERED AS A STREAMING JOB, because it is one: the runner subscribes,
/ wires publish and installs upd exactly as for any other job. define
/ performs the .qstream.register itself - subscribes is the source list,
/ publishes is the output, on_batch is the dispatcher - so an instance
/ cannot declare its edges differently from its mappings.

\d .qnorm

/ name -> its declaration, ENLISTED, for the reason .qstream.jobs enlists:
/ same-keyed dicts collapse into a table and a later, differently-keyed
/ declaration is then refused with a bare 'mismatch.
registry:(`symbol$())!();

/ What every normalizer must declare.
required_keys:`procname`output`sources

/ Declare a normalizer: its canonical output and one transform per source.
/ .
/ Refuses, by name, a source whose transform is not registered, takes
/ more than one input, or produces anything but the canonical table. The
/ last is the check this kind exists for - and it is exact, not "has the
/ same columns": type and order too, because the output is published
/ positionally and a mapping that emits size before price lands as a
/ table whose sizes are prices.
/ @param name the normalizer's name, e.g. `executions - also its output table and its .qsub namespace
/ @param decl dict of procname, output (an empty unkeyed table, no `time`), sources (source table -> transform name), and optionally autostart (boolean) and note (string)
/ @return the name
/ @throws error naming every problem it finds first
define:{[name;decl]
    who:"define: normalizer ",string[name];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    missing:required_keys where not required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    out:decl`output;
    if[not 98h=type out; 'who,"'s output must be an unkeyed table - it is published, and a keyed table cannot be"];
    if[0=count cols out; 'who,"'s output has no columns"];
    if[`time in cols out;
        'who,"'s output carries `time` - the plant stamps its own, and a source's own stamp belongs in a column named for what it is"];
    / Deployment facts, as on .qstream.register - optional, and read by the
    / uqs process registry, which is derived from these declarations.
    if[(`autostart in key decl) and not -1h=type decl`autostart;
        'who,"'s autostart must be a boolean, 1b to start with the stack"];
    if[(`note in key decl) and not 10h=type decl`note; 'who,"'s note must be a string"];
    srcs:decl`sources;
    if[not (99h=type srcs) and (11h=type key srcs) and 11h=type value srcs;
        'who,"'s sources must be a dictionary of source table -> transform name, symbols both"];
    if[0=count srcs; 'who," has no sources - a normalizer of nothing normalizes nothing"];
    check_source[who;out]'[key srcs;value srcs];
    registry[name]:enlist decl;
    / The dispatcher is ALSO set as .qsub.<name>.on_batch, so a normalizer
    / instance has the same surface as every other job - a reader, a test
    / or the runner reaching for a job's handler finds it in the one place.
    handler:dispatch[name;;];
    (` sv (.qstream.namespace name),`on_batch) set handler;
    .qstream.register[name;`procname`subscribes`publishes`on_batch!(
        decl`procname; key srcs; enlist name; handler)];
    name}

/ Private: one source's transform, held to the canonical output.
check_source:{[who;out;src;xf]
    if[not xf in key .qxf.registry;
        'who,": source ",string[src]," maps through transform ",string[xf],", which is not registered - a mapping is a declared transform, so its examples are verified"];
    d:.qxf.declaration xf;
    if[1<>count d`inputs;
        'who,": source ",string[src],"'s transform ",string[xf]," takes ",string[count d`inputs]," inputs - a mapping reads one source"];
    p:.qxf.problems[out;d`output;1b];
    if[count p;
        'who,": source ",string[src],"'s transform ",string[xf]," does not produce the canonical table: ","; " sv p];
    1b}

/ One normalizer's declaration, or a refusal naming it.
/ @param name the normalizer
/ @return the declaration dict
/ @throws error when nothing was defined under that name
/ @eg .qnorm.declaration[`executions]`sources
declaration:{[name]
    if[not name in key registry;
        '"declaration: ",string[name]," is not a defined normalizer - defined: ",", " sv string key registry];
    first registry name}

/ Every defined normalizer.
/ @return a symbol vector
/ @eg `executions in .qnorm.defined[] -> 1b
defined:{[] key registry}

/ The canonical rows for one source batch: the pure half, with no publish.
/ .
/ The batch is PROJECTED onto the columns the source's transform declares
/ before the transform sees it. A subscriber's batch carries `time` and
/ any attribute the plant applied; a mapping declares what it reads, and
/ the shell hands it exactly that - so the same mapping serves a replayed
/ batch, a live one and a hand-built one in a test.
/ @param name the normalizer
/ @param src the source table the batch arrived on
/ @param batch the rows, as a table
/ @return the canonical rows
/ @throws error when src is not one of the normalizer's sources, or the batch lacks a declared column
/ @eg cols .qnorm.normalize[`executions;`trades;([] time:enlist 2026.09.17D10:00:00; sym:enlist `EURUSD; side:enlist 1; trade_price:enlist 1.085; size:enlist 1e6; pip_factor:enlist 10000)]
normalize:{[name;src;batch]
    d:declaration name;
    srcs:d`sources;
    if[not src in key srcs;
        '"normalize: ",string[src]," is not a source of ",string[name]," - its sources are ",", " sv string key srcs];
    xf:srcs src;
    ins:.qxf.declaration[xf]`inputs;
    want:cols first value ins;
    .qschema.require_cols[`normalize;`$(string src)," batch for ",string name;batch;want];
    .qxf.apply[xf;(enlist first key ins)!enlist want#batch]}

/ Private: the dispatcher every normalizer registers as its on_batch -
/ normalize, then publish through the instance's own wired seam.
/ .
/ A batch on a table that is not a source is dropped rather than refused:
/ the plant delivers only what was subscribed to, so this can only happen
/ from a test or a hand call, and neither should take the job down.
dispatch:{[name;src;batch]
    if[not src in key declaration[name]`sources; :()];
    if[0=count batch; :()];
    rows:normalize[name;src;batch];
    if[0=count rows; :()];
    (get ` sv (.qstream.namespace name),`publish)[name;rows];
    }

\d .
