/ bounded_batching.q - compute batching inside a bounded window (#1921): .qetl.job.bounded.batching.
/ .
/ WHY. A window is the unit of DURABLE ownership: one fetch, one publish, one coverage record.
/ A transform over a large window can still be too big to run at once - a million executions
/ against their quote books - without the window itself being wrong. Shrinking `width` fixes
/ that by making more, smaller windows, which are also more coverage rows, more partitions
/ written and more reads of the same overlapping history. Batching splits the COMPUTE and
/ leaves the ownership alone.
/ .
/ WHAT IT IS. A worker may declare `batching`, a dictionary:
/ .
/   group_by    the primary rows' columns that identify an indivisible calculation group,
/               one symbol or several (a composite key). Every row with the same values
/               belongs to one group, and a group is NEVER split.
/   max_rows    the most primary rows one batch may hold. A batch takes whole groups.
/   max_groups  the most groups one batch may hold (default 0W: rows alone decide). 1 is
/               a batch per group; larger groups several, for a workload where groups
/               share context.
/   context     optional {[supporting;rows] -> supporting}: the supporting inputs a batch
/               of these primary rows needs, out of the window's. Without it every batch
/               is given all of them. Only for a source that declares supporting inputs.
/   source_read optional `window (default: the window's references are read once) or
/               `batch (each batch reads its own from the source). `batch needs a source
/               that declares batch_read, and replaces `context: the source narrows.
/   reuse_rows  optional, default 0: the most reference rows kept between batches so one
/               batch's read can serve the next. Needs source_read `batch.
/ .
/ WHAT THE WINDOW DOES WITH IT. The window is fetched ONCE, as it always was, so no batch
/ reads the source again. Its primary rows are planned into batches, each batch is
/ transformed on its own with its context, and the outputs are joined. That joined result
/ goes through the check, the publish and the coverage record exactly as an unbatched
/ window's does, so:
/ .
/   - coverage is never recorded after only some batches succeeded: a batch that fails
/     fails the window, before anything is written;
/   - readiness (#1927, #1942) is checked by the `ready` stage between fetch and transform,
/     over the whole window, so no batch starts on a window that is not ready and such a
/     window is never claimed;
/   - a retry has nothing half-written to duplicate, omit or delete, and a batch never
/     writes beside a sibling's output;
/   - `replace clears the window's whole ownership scope in one write, an empty result
/     and a row stamped after midnight included;
/   - the durable partitions are not touched: batching creates none.
/ .
/ SOURCE-SIDE READS (#1921). By default the window's references are read once, whole. A source that
/ declares `batch_read` (source_batch_read.q) lets the worker say `source_read:`batch: the window is
/ fetched as its primary rows and a presence summary of the references, `ready` judges the WHOLE
/ window on that summary exactly as before, and each batch then reads from the source only the
/ reference keys its own rows need (the source's `needs`, for the FX markouts the legs and the
/ conversion pairs). Nothing is written until every batch has been read and transformed, so all of
/ the above holds unchanged. `reuse_rows` lets a book one batch read serve the next, within that
/ many cached rows, least recently used out first, keyed by worker, source_version and window so
/ that a different version or window can never be served from it. Reuse lasts only within one
/ transform attempt; a retry starts with fresh references (bounded_batch_read.q).
/ .
/ WHAT THE WORKER CLAIMS. That its transform, given a group's rows and the context
/ declared for them, produces that group's output whatever other groups are in the window.
/ The framework cannot check that; tests that compare a batched with an unbatched run
/ can, and a worker's declaration is as good as its test.
/ .
/ Off unless declared: a worker without `batching` runs exactly as it did.

\d .

/ The keys a `batching` declaration may carry.
.qetl.job.bounded.batching.keys:`group_by`max_rows`max_groups`context`source_read`reuse_rows

/ ------------------------------------------------------------ DECLARATION

/ Private: a worker's `batching` declaration, validated and normalised to its four keys.
/ .
/ Refused at define, naming the key, so a typo fails the declaration and not the first
/ window of a backfill.
/ @param worker the worker's name
/ @param decl the worker's declaration, carrying `batching
/ @return the dictionary group_by, max_rows, max_groups, context - (::) for an absent context
/ @throws error naming what is wrong
/ @private
.qetl.job.bounded.batching.require:{[worker;decl]
    who:"define: ",string[worker],"'s batching";
    b:decl`batching;
    if[not $[99h=type b; 11h=type key b; 0b];
        'who," must be a dictionary of group_by, max_rows and optionally max_groups and context"];
    unknown:(key b) except .qetl.job.bounded.batching.keys;
    if[count unknown;
        'who," has ",(", " sv string unknown),", which it does not take - the keys are ",", " sv string .qetl.job.bounded.batching.keys];
    absent:`group_by`max_rows where not `group_by`max_rows in key b;
    if[count absent; 'who," needs ",", " sv string absent];
    src:.qetl.source.def decl`source;
    limits:.qetl.job.bounded.batching.require_limits[who;b];
    ctx:.qetl.job.bounded.batching.require_context[who;decl`source;src;b];
    rd:.qetl.job.bounded.batching.require_read[who;decl`source;src;b;ctx];
    `group_by`max_rows`max_groups`context`source_read`reuse_rows!(
        .qetl.job.bounded.batching.require_group_by[who;decl`source;src;b`group_by];
        limits 0;limits 1;ctx;rd 0;rd 1)}

/ Private: (source_read; reuse_rows), each checked against the source and the context.
/ @private
.qetl.job.bounded.batching.require_read:{[who;source;src;b;ctx]
    sr:$[`source_read in key b; b`source_read; `window];
    if[not (-11h=type sr) and sr in `window`batch; 'who,"'s source_read must be `window or `batch"];
    rr:$[`reuse_rows in key b; b`reuse_rows; 0];
    if[not (-7h=type rr) and (rr>=0) and rr<0W; 'who,"'s reuse_rows must be a non-negative, finite long - the most reference rows kept between batches"];
    if[(sr=`window) and rr>0; 'who,"'s reuse_rows needs source_read `batch: a window read whole has nothing to reuse"];
    if[sr=`batch;
        if[not 99h=type src`batch_read;
            'who,"'s source_read `batch needs a source that declares batch_read, and ",string[source]," declares none"];
        if[not (::)~ctx; 'who,"'s source_read `batch replaces context: the source reads each batch's own references"]];
    (sr;rr)}

/ Private: the group_by columns, as a symbol vector of the source's own columns.
/ @private
.qetl.job.bounded.batching.require_group_by:{[who;source;src;gb]
    if[not 11h=abs type gb; 'who,"'s group_by must be a symbol or symbol vector naming primary columns"];
    gb:(),gb;
    if[0=count gb; 'who,"'s group_by is empty - name the column(s) that identify a calculation group"];
    if[not count[gb]=count distinct gb; 'who,"'s group_by names a column more than once"];
    away:gb where not gb in src`columns;
    if[count away;
        'who,"'s group_by names ",(", " sv string away),", which source ",string[source],
         " does not return (",(", " sv string src`columns),")"];
    gb}

/ Private: (max_rows; max_groups), each a positive long, max_rows finite.
/ @private
.qetl.job.bounded.batching.require_limits:{[who;b]
    mr:b`max_rows;
    if[not (-7h=type mr) and (mr>0) and mr<0W;
        'who,"'s max_rows must be a positive, finite long - the most primary rows one batch holds"];
    mg:$[`max_groups in key b; b`max_groups; 0W];
    if[not (-7h=type mg) and mg>0;
        'who,"'s max_groups must be a positive long - the most groups one batch holds (0W for no limit)"];
    (mr;mg)}

/ Private: the context function, or (::) when none is declared.
/ @private
.qetl.job.bounded.batching.require_context:{[who;source;src;b]
    ctx:$[`context in key b; b`context; (::)];
    if[(::)~ctx; :ctx];
    if[not type[ctx] in 100 104h;
        'who,"'s context must be a function of (supporting; rows)"];
    if[0=count src`supporting;
        'who,"'s context narrows supporting inputs, and source ",string[source]," declares none"];
    if[(100h=type ctx) and not 2=count (value ctx) 1;
        'who,"'s context must take exactly two arguments: the window's supporting inputs and the batch's primary rows"];
    ctx}

/ ------------------------------------------------------------------- PLAN

/ Private: one id per row, equal for rows that agree on every group_by column.
/ .
/ Built by counting, a column at a time, rather than by joining the values into one
/ string: two different keys must never share an id because of what their values contain.
/ @private
.qetl.job.bounded.batching.group_ids:{[rows;gb]
    {[ids;c] n:count u:distinct c; (ids*n)+u?c}/[(count rows)#0j;rows each gb]}

/ Private: a group's key as the message names it.
/ @private
.qetl.job.bounded.batching.key_text:{[rows;gb;i] ", " sv {[rows;i;c] string[c],"=",-3!(rows c) i}[rows;i] each gb}

/ Private: the batch each group, in order, goes into - greedy, never splitting a group.
/ @param mr max_rows
/ @param mg max_groups
/ @param sizes the groups' row counts, in the order they are packed
/ @return one batch number per group, ascending from 0
/ @private
.qetl.job.bounded.batching.pack:{[mr;mg;sizes]
    step:{[mr;mg;s;sz]
        full:((s 1)>0) and ((((s 1)+sz)>mr) or (s 2)>=mg);
        $[full; (1+s 0;sz;1); (s 0;(s 1)+sz;1+s 2)]};
    first each step[mr;mg]\[(0;0;0);sizes]}

/ Plan a window's primary rows into batches of whole groups.
/ .
/ Groups are taken in ascending key order and packed greedily into batches, so a plan is
/ the same on every run and a batch's rows stay in the order the window returned them.
/ A group larger than max_rows is REFUSED: it cannot be split, and a plan that quietly
/ overran the limit would make the limit a suggestion.
/ @param spec a worker's batching declaration, as .qetl.job.bounded.batching.require returns it
/ @param rows the window's primary rows, a table
/ @return a list of ascending row-index vectors, one per batch; () for no rows
/ @throws error naming the group and the ways out when one is over max_rows
/ @eg .qetl.job.bounded.batching.plan[`group_by`max_rows`max_groups!(`sym;3;0W);([] sym:`b`a`b`a`c)]  ->  (1 3;0 2 4)
.qetl.job.bounded.batching.plan:{[spec;rows]
    if[0=count rows; :()];
    gb:(),spec`group_by;
    g:group .qetl.job.bounded.batching.group_ids[rows;gb];
    groups:value g;
    / ascending key order: the key columns of each group's first row, sorted
    firsts:gb#rows first each groups;
    order:exec o from gb xasc update o:i from firsts;
    groups:groups order;
    sizes:count each groups;
    big:where sizes>spec`max_rows;
    if[count big;
        '"batching: the group ",.qetl.job.bounded.batching.key_text[rows;gb;first groups first big]," has ",
         string[sizes first big]," rows, over max_rows ",string[spec`max_rows],
         " - a group cannot be split: raise max_rows, shorten the worker's width, or declare a finer group_by if its transform allows it"];
    batch:.qetl.job.bounded.batching.pack[spec`max_rows;spec`max_groups;sizes];
    {asc raze x} each groups value group batch}

/ ---------------------------------------------------------------- WINDOW TRANSFORM
/ .
/ Moved here from bounded_worker.q, which is at its line cap (#1865), with the one place that
/ decides between the two: a window's transform and a reaction's replay of it both go through
/ transform_window, so they cannot disagree about whether a window was batched.

/ Private: run the worker's transform over one fetched batch.
/ .
/ Narrowed to the contract's declared fields first. .qetl.source.validate accepts a
/ source returning MORE columns than it declares, and the transform declares
/ exactly the contract - so the extra columns are dropped here, where the
/ contract says what the job reads, rather than refused.
/ .
/ A source with supporting inputs hands a dict of tables (#617), and each is
/ narrowed to its own contract and passed under its own name. The primary
/ owns the window: when it is empty the window publishes nothing, so the
/ transform is not run and its declared output comes back empty - supporting
/ rows alone are context for nothing.
/ @return the transformed batch
/ @throws whatever the transform throws, or a schema refusal from .qetl.transform
/ @private
.qetl.job.bounded.transform_batch:{[worker;batch]
    cfg:.qetl.job.bounded.def worker;
    src:.qetl.source.def cfg`source;
    nm:cfg`transform;
    if[0=count src`supporting;
        :.qetl.transform.apply[nm;(.qetl.transform.input_names nm)!enlist (src`columns)#batch]];
    if[0=count batch src`table_name; :0#.qetl.transform.output_schema nm];
    sup:key src`supporting;
    given:((enlist src`table_name)!enlist (src`columns)#batch src`table_name),
        sup!{[batch;nm;contract] (cols contract)#batch nm}[batch]'[sup;value src`supporting];
    .qetl.transform.apply[nm;given]}

/ Private: transform a fetched window as its worker declares: in one go, or batch by
/ batch when the worker declares `batching (bounded_batching.q, #1921). The one place that
/ decides, so a window and a reaction's replay of it cannot disagree.
/ @param worker the worker's name
/ @param w the window, a dict of range_from and range_to
/ @param batch the fetched window
/ @return the transform's output for the window
/ @private
.qetl.job.bounded.transform_window:{[worker;w;batch]
    $[(::)~(.qetl.job.bounded.def worker)`batching;
        .qetl.job.bounded.transform_batch[worker;batch];
        .qetl.job.bounded.batching.apply[worker;w;batch]]}

/ ------------------------------------------------------------------ APPLY

/ Private: one batch's inputs: its primary rows and the context it needs.
/ @private
.qetl.job.bounded.batching.inputs:{[spec;table_name;batch;idx]
    rows:(batch table_name) idx;
    ctx:spec`context;
    given:enlist[table_name]!enlist rows;
    sup:(key batch) except table_name;
    $[(::)~ctx; given,sup#batch; given,ctx[sup#batch;rows]]}

/ Private: is this window's references read a batch at a time from the source? The worker
/ asks for it AND it is a live run: the fixture has no source to read from, and its window
/ already holds every reference.
/ @private
.qetl.job.bounded.batching.by_source:{[worker;spec]
    $[`batch=spec`source_read; 1b~.qetl.job.bounded.read_state[worker;`live]; 0b]}

/ Private: one batch, read (when the source reads it) and transformed; a failure names the batch.
/ @param st the values every batch shares: worker, spec, table_name, sup, batch, prim, n, w, bysrc
/ @param i the batch's position
/ @param idx the batch's primary row indexes
/ @return the batch's transformed output
/ @private
.qetl.job.bounded.batching.run_batch:{[st;i;idx]
    / the inputs are built INSIDE the trap: a context or a read that throws names its batch too
    r:@[{[st;idx]
            given:$[st`bysrc;
                .qetl.job.bounded.batching.source_inputs[st`worker;st`spec;st`table_name;st`w;(st`prim) idx];
                st`sup;
                .qetl.job.bounded.batching.inputs[st`spec;st`table_name;st`batch;idx];
                (st`prim) idx];
            (1b;.qetl.job.bounded.transform_batch[st`worker;given])}[st;];idx;{[e] (0b;e)}];
    if[not first r;
        '"batch ",string[1+i]," of ",string[st`n]," (",string[count idx]," rows): ",last r];
    last r}

/ Transform a fetched window batch by batch and join the outputs.
/ .
/ A batch that fails fails the whole call, naming the batch - the window then fails
/ before anything is published. A window with no primary rows is transformed as one,
/ as an unbatched window is, so its empty output keeps the transform's own shape.
/ .
/ With source_read `batch on a live run the window's supporting inputs are only the presence
/ summary `ready` judged: no batch is given them. Each reads its own from the source.
/ @param worker a defined worker with a batching declaration
/ @param w the window, a dict of range_from and range_to
/ @param batch the fetched window: a table, or a dictionary of the source's inputs
/ @return the joined output, as .qetl.job.bounded.transform_batch returns it
/ @private
.qetl.job.bounded.batching.apply:{[worker;w;batch]
    cfg:.qetl.job.bounded.def worker;
    src:.qetl.source.def cfg`source;
    spec:cfg`batching;
    sup:0<count src`supporting;
    prim:$[sup; batch src`table_name; batch];
    plan:.qetl.job.bounded.batching.plan[spec;prim];
    if[0=count plan; :.qetl.job.bounded.transform_batch[worker;batch]];
    .qetl.log.dbg[worker;"window planned into batches";
        `batches`rows`max_rows`max_groups!(count plan;count prim;spec`max_rows;spec`max_groups)];
    bysrc:.qetl.job.bounded.batching.by_source[worker;spec];
    if[bysrc; .qetl.job.bounded.batching.begin_reads[worker]];
    / the values every batch shares, in one dictionary: a q lambda takes at most eight arguments
    st:`worker`spec`table_name`sup`batch`prim`n`w`bysrc!(worker;spec;src`table_name;sup;batch;prim;count plan;w;bysrc);
    outs:.qetl.job.bounded.batching.run_batch[st]'[til count plan;plan];
    raze outs}

\d .
