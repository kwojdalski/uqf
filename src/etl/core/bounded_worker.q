/ bounded_worker.q - the generic bounded-worker shell (.qetl.job.bounded).
/ .
/ Issue #124. demo_deals_backfill.q was 238 lines of which FOUR were
/ worker-specific - worker_name, source_name, dataset, width - and the other
/ 234 were the same glue any bounded worker needs, because that is exactly
/ what .qetl.job.bounded.state / .qetl.job.bounded.runtime / .qetl.coverage / .qetl.source already define. A second worker
/ would have duplicated those 234 lines to change four, and then needed
/ keeping in step by hand: the framework moved four times in one day while
/ #46 was being built, and a duplicate would have fallen behind on two of
/ them.
/ .
/ So the glue lives here once, parameterised, and a worker is a declaration.
/ .
/ WHY STATE STILL LIVES IN THE WORKER'S OWN NAMESPACE
/ .
/ The obvious design is for this file to hold the state. It cannot: the contract's
/ contract, enforced by .qetl.job.bounded.state.require_contract, requires
/ source_version/range_from/range_to to be names in the WORKER's namespace,
/ so that "is this worker complete" stays a deterministic check rather than
/ a code-review question. Moving them here would make every worker pass the
/ contract vacuously - the check would be inspecting the shell, not the
/ worker.
/ .
/ So the shell reads and writes through the worker's namespace
/ (read_state/write_state below), and the names live there. The cost is two
/ indirection helpers; the benefit is that require_contract still means
/ something.
/ .
/ HOW A WORKER INHERITS THEM (#227)
/ .
/ define STAMPS them. Until #227 every worker file carried the same twenty
/ lines by hand - six globals and eight one-line delegators - which was #124's
/ duplication again at a smaller scale: a fourth worker pasted the block a
/ fourth time, and the delegator list was a second copy of
/ .qetl.job.bounded.state.bounded_worker_methods that nothing kept in step. Now define
/ writes every inherited name the worker has not defined itself into the
/ worker's namespace, from the shell's own signatures (see inherit), and a
/ worker file is its transform, its optional check and facts, and one define.
/ .
/ WHAT A WORKER MAY STILL OVERRIDE
/ .
/ Everything, and the override is honoured. A name the worker defines BEFORE
/ its define call is left alone, and run and do_window reach plan, fetch and
/ publish through the worker's namespace (see `own`) rather than calling the
/ shell's directly - which is what the first version of this file promised
/ and did not do: its run loop called .qetl.job.bounded.fetch whatever the worker had
/ defined, so an override was reachable from the prompt and from nowhere
/ else. A worker with a genuinely different publish path defines its own,
/ and the shell is a default rather than a framework that owns the worker.

\d .qetl.job.bounded

/ worker -> its configuration. `source` is the worker's registered .qetl.source
/ source, `dataset` the name coverage is recorded under, `width` its window
/ size, `transform` the registered .qetl.transform transform its rows go through
/ between fetch and publish, and `ns` its namespace - DERIVED by define,
/ never supplied: see worker_root.
/ .
/ Named `worker_cfg`, not `cfg`: `cfg` is the LOCAL in define, init, plan,
/ fetch and publish, and a q local shadows a namespace global of the same
/ name - so `cfg[worker]:...` inside define would have written to the
/ parameter and left this registry silently empty. Not `cfgs` either, which
/ it was: a pluralised contraction is not a word.
/ .
/ A KEYED TABLE declared with its columns (#512), not a dictionary of
/ dictionaries: that collapses into a table on the first entry, depends on
/ every config having one key set, and on PeachQ throws on the second row.
/ Every config still has one key set - normalised gives each optional key its
/ default - but now the table is declared rather than emergent. The typed
/ columns hold what define validates to one type; the general ones hold a
/ function, a dictionary, a vector or (::). A row is .qetl.job.bounded.def;
/ the names are .qetl.job.bounded.defined.
worker_cfg:([name:`symbol$()] source:`symbol$(); dataset:`symbol$(); width:`timespan$();
    transform:`symbol$(); ns:`symbol$(); partition:`symbol$(); procname:`symbol$(); note:();
    on_conflict:`symbol$(); source_version:`symbol$(); target_key:(); check:(); io:(); facts:())

/ Every defined worker's name.
/ @return a symbol vector, empty when no worker has been defined
/ @eg .qetl.job.bounded.defined[]
defined:{[] (key worker_cfg)`name}

/ `transform` is REQUIRED, not optional like `check`. A check is a guard a
/ worker may honestly have no use for; a transform is the job itself. A
/ worker that publishes what it fetched says so by declaring a pass-through
/ transform, with examples - which makes "this job changes nothing" a tested
/ claim rather than an absence nobody decided.
required_cfg:`source`dataset`width`transform

/ Every worker instance lives under this one namespace, as
/ .qpipe.job.<worker>: .qpipe.job.demo_deals_backfill, .qpipe.job.upstream_trades_backfill.
/ .
/ The library's own modules are flat by convention (one file, one
/ `\d .q<abbrev>`), and workers used to follow suit - .qddbf, .qevbf,
/ .qupbf, .qdbnbf - which put four instances of one shape beside .qetl.job.bounded,
/ .qetl.coverage and .qetl.source as if they were four more frameworks, and made each
/ instance's namespace a second name to invent, spell and keep in step with
/ the worker's registered name. Nesting them under one root separates
/ "the framework" from "what runs on it", lets `key `.qpipe.job` list every
/ loaded worker, and means a worker has exactly ONE name: define derives
/ the namespace from it, so there is nothing to keep in step.
worker_root:`.qpipe.job

/ The namespace a worker's implementation lives in. The single spelling of
/ the `.qpipe.job.<worker>` rule; every caller that needs the namespace goes
/ through here or through the `ns` key define stores from it.
/ @param worker the worker's name
/ @return the namespace symbol, e.g. `.qpipe.job.demo_deals_backfill
/ @eg .qetl.job.bounded.namespace `demo_deals_backfill  ->  `.qpipe.job.demo_deals_backfill
namespace:{[worker] ` sv worker_root,worker}

/ ------------------------------------------------------- INHERITANCE

/ Private: a run's progress before it has made any.
/ @private
no_progress:`windows_planned`windows_completed`windows_failed`rows_published`cursor`reactions_owed!(0;0;0;0;0Np;0)

/ The globals every worker starts with. The first three are the contract's
/ contract; `handle` is the live connection or 0Ni on the fixture; `progress`
/ and `last_batch` are the run accumulators, one set PER WORKER because a q
/ lambda does not close over an enclosing local, so `each` over windows needs
/ a named place for the running totals - and two workers in one process must
/ not share it. `phase` is where the worker's run lifecycle stands - see
/ advance_phase.
initial_state:`source_version`range_from`range_to`handle`progress`last_batch`last_window`phase!
    (`;0Np;0Np;0Ni;no_progress;();`range_from`range_to!0N 0Np;`)

/ The methods every worker gets: the contract's five, taken from the
/ contract itself so the two cannot drift, plus the three the launcher and
/ the tests call through the worker's namespace.
inherited_methods:.qetl.job.bounded.state.bounded_worker_methods,`spec`run`cleanup

/ Private: the delegating lambda for one method, built from the shell
/ function's own parameter list so its signature is the shell's minus
/ `worker`. For `fetch it is
/   {[from_ts;to_ts] .qetl.job.bounded.fetch[`demo_deals_backfill;from_ts;to_ts]}
/ which is what the hand-written one used to say, and what `.qpipe.job.x.fetch`
/ at the prompt still shows. A real lambda rather than a projection for
/ that readability, and because the niladic ones - run[], cleanup[] - have
/ no projection form: a projection with every argument supplied is a call.
/ @private
delegate:{[worker;nm]
    / value of the NAME is the function; value of the function is its
    / parse tree, whose second element is the parameter list.
    args:1_(value value ` sv `.qetl.job.bounded,nm)[1];
    value "{[",(";" sv string args),"] .qetl.job.bounded.",string[nm],"[",.Q.s1[worker],
        $[count args; ";",";" sv string args; ""],"]}"}

/ Private: give a worker namespace every inherited name it has not defined.
/ .
/ Only the ABSENT names, which is the whole override mechanism: a worker
/ that defined its own `publish` before calling define keeps it. It is also
/ what makes a reload safe - a worker file re-runs its own define, and the
/ state its previous run left behind is not reset under it.
/ @private
inherit:{[worker;ns]
    have:.qetl.job.bounded.state.ns_names ns;
    globals:(key initial_state) except have;
    {[ns;nm;v] (` sv ns,nm) set v}[ns;;]'[globals;initial_state globals];
    methods:inherited_methods except have;
    {[ns;worker;nm] (` sv ns,nm) set delegate[worker;nm]}[ns;worker;] each methods;
    }

/ The partition every worker fills when it does not declare one.
/ .
/ ` is .qetl.coverage's "this dataset has no partition dimension" sentinel, so an
/ existing worker that names no partition keeps recording and reading exactly
/ the rows it always did. Declaring `partition` is what opts a dataset into
/ being filled by several workers at once (#185); not declaring it leaves the
/ old one-worker-per-dataset behaviour in place, refusal and all.
unpartitioned:`

/ The keys a worker MAY declare. Absent ones are filled with (::) at
/ registration, which is not tidiness - it is load-bearing.
/ .
/ `config` is a dictionary of dictionaries, and q coerces same-keyed dicts
/ into a TABLE. That coercion was happening by accident: with two workers
/ declaring different keys, `value config` became a table anyway and every
/ worker silently acquired every other worker's keys - demo_events_backfill
/ reported a `check` it had never declared. Worse, a worker declaring a key
/ no earlier worker had made registration fail outright with 'mismatch,
/ because a table cannot gain a column that way.
/ .
/ Normalising every config to the same key set makes the table shape
/ INTENDED rather than emergent: registration order stops mattering, a new
/ optional key is one entry here, and no worker is handed a key it did not
/ ask for with a value it did not choose.
/ `facts` is the worker's hook into materialisation metadata: a
/ monadic function from the fetched batch to a dict of symbol labels, whose
/ result is attached to the window's materialisation. The framework records
/ what it can know without a schema - rows, source_version, dry_run - and
/ this is where anything needing domain knowledge goes: the min and max of
/ the time column, a null fraction on the column that matters, a checksum.
/ Optional, for the same reason `check` is: metadata written to satisfy a
/ requirement rather than to be read is worse than none.
optional_cfg:`check`io`facts`partition

/ Declare a worker's configuration.
/ .
/ Validated here rather than at first use, because a declaration is one
/ literal with nothing to defer - the same reasoning .qetl.source.define follows
/ and the opposite of .qetl.job.bounded.state.register, whose methods appear as a file
/ loads.
/ @param worker the worker's name
/ @param decl dict of source, dataset, width, transform, and optionally
/   check, facts, partition, io, procname (default `<worker>1), note,
/   on_conflict (default `upsert - see .qetl.io's CONFLICTS), target_key
/   (default the source's row_key - see require_target_key) and
/   source_version (the default release a run records coverage under; none
/   by default, so a run must name one)
/ @throws error naming every missing or malformed field at once
define:{[worker;decl]
    / Both execution modes share .qpipe.job, so a name cannot belong to both.
    if[worker in key @[value;`.qetl.job.stream.jobs;{()}];
        '"define: ",string[worker]," is already a streaming job - job names must be unique across execution modes"];
    missing:required_cfg where not required_cfg in key decl;
    if[count missing;
        '"define: ",string[worker]," is missing ",", " sv string missing];
    / The namespace is not configurable, and a supplied one is refused rather
    / than overwritten: a worker declared with `ns`.qddbf would be looked
    / for under .qpipe.job.demo_deals_backfill regardless, and the author would
    / learn that from a contract failure at init naming twelve missing
    / methods rather than from define naming the key.
    if[`ns in key decl;
        '"define: ",string[worker],"'s namespace is derived - .qpipe.job.",string[worker]," - not configured; drop the ns key"];
    / A key no column holds would have nowhere to go: refused by name, where
    / the collapsed dictionary this was refused it with a bare 'mismatch.
    unknown:(key decl) except cols value worker_cfg;
    if[count unknown;
        '"define: ",string[worker]," declares ",(", " sv string unknown),
         ", which a worker does not take - the keys are ",", " sv string cols value worker_cfg];
    decl[`ns]:namespace worker;
    if[not -16h=type decl`width;
        '"define: ",string[worker],"'s width must be a timespan, e.g. 1D"];
    if[not (decl`width)>0D00:00;
        '"define: ",string[worker],"'s width must be positive - a zero width plans infinitely many empty windows"];
    / Validate the io manager HERE, not at first write. A worker with a
    / malformed manager should fail at declaration, not halfway through a
    / backfill having already fetched a window it is now unable to store.
    .qetl.io.for_cfg decl;
    .qetl.source.def decl`source;
    require_transform[worker;decl];

    / Refuse two workers filling one dataset AND PARTITION (#60, #185).
    / .
    / This used to refuse on the dataset alone, because coverage had no
    / partition dimension: stage_completion recorded (dataset; version;
    / range; rows), so two workers writing one dataset produced rows nothing
    / could tell apart. That refusal was correct given the schema and it was
    / also the ceiling on parallelism - one worker per dataset, however large
    / the range.
    / .
    / Coverage now carries `partition`, so the pair is what has to be unique.
    / Two workers on one dataset filling `EURUSD and `USDJPY record rows that
    / no read composes, because every read filters partition= with equality.
    / Two workers on the SAME pair are still refused, and for the unchanged
    / reason: their coverage would compose and a gap-ridden range would read
    / as complete.
    / .
    / The unpartitioned sentinel is a partition like any other here, so two
    / workers that both decline to declare one still clash - which keeps every
    / existing worker's guarantee exactly as it was.
    part:$[`partition in key decl; decl`partition; unpartitioned];
    if[not -11h=type part;
        '"define: ",string[worker],"'s partition must be a symbol, or ` for a dataset with no partition dimension"];
    / Resolved before storage, so `worker_cfg` never holds (::) here and the clash
    / comparison below is symbol against symbol. Every other optional key can
    / be absent because nothing compares them; this one is compared.
    decl[`partition]:part;
    / The process that runs this worker, and a line on why it is deployed the
    / way it is. Both feed the uqs process registry, which is DERIVED
    / from these declarations rather than kept as a second list. Resolved
    / before storage for the reason partition is: worker_cfg's rows share one
    / shape, and a column that is a symbol on one row and (::) on the next
    / stops the next assignment fitting.
    proc:$[`procname in key decl; decl`procname; `$string[worker],"1"];
    if[not -11h=type proc;
        '"define: ",string[worker],"'s procname must be a symbol naming the process that runs it, e.g. `",string[worker],"1"];
    decl[`procname]:proc;
    note:$[`note in key decl; decl`note; ""];
    if[not 10h=type note;
        '"define: ",string[worker],"'s note must be a string"];
    decl[`note]:note;
    / What a write does with a row whose row_key is already there. Resolved
    / before storage for the reason partition is, and checked here so a typo
    / fails the declaration rather than the first window.
    oc:$[`on_conflict in key decl; decl`on_conflict; .qetl.io.default_strategy];
    if[not -11h=type oc;
        '"define: ",string[worker],"'s on_conflict must be a symbol, e.g. `upsert"];
    decl[`on_conflict]:.qetl.io.require_strategy oc;
    / The source_version a run uses when it names none - for a source that is
    / never restated, where every run would otherwise be told `v1 forever.
    / ` declares no default, and then a run must name one: for a source that
    / CAN be restated, a default would file the restatement under the old
    / version, and every window would read as already covered.
    dv:$[`source_version in key decl; decl`source_version; `];
    if[not -11h=type dv;
        '"define: ",string[worker],"'s source_version must be a symbol, e.g. `v1 - the release a run records coverage under when it names none"];
    decl[`source_version]:dv;
    decl[`target_key]:require_target_key[worker;decl];
    / Mask over the WHOLE registry first, then drop this worker - filtering the key
    / list before applying the mask pairs a shortened list with a full-length
    / boolean, which q indexes without complaint and which reports the wrong
    / worker as the claimant.
    ds:decl`dataset;
    clash:exec name from worker_cfg where dataset=ds, partition=part;
    clash:clash except worker;
    if[count clash;
        '"define: ",string[worker]," declares dataset ",string[decl`dataset],
         / PARENTHESISED. q evaluates right to left, so
         / `", " sv string clash, " - two workers..."` makes `sv` join the
         / EXPLANATION character by character - the message came out as
         / "fx_rates_eurusd,  , -,  , t, w, o,  , w, o, r, k, e, r, s". The
         / test only checked that the claimant's name appeared, which it
         / did, immediately before the wreckage.
         "[",string[part],"], already claimed by ",(", " sv string clash),
         " - two workers on one dataset and partition produce coverage rows nothing can tell apart"];

    / Normalise to the full key set before storing - see optional_cfg.
    stored:normalised decl;
    `.qetl.job.bounded.worker_cfg upsert (worker,stored cols value worker_cfg);
    / Last, so a refused declaration leaves no half-stamped namespace behind.
    inherit[worker;decl`ns];
    worker}

/ Private: the declared transform exists and reads exactly this worker's
/ source.
/ .
/ A source with one input: the transform reads one input, and its schema must
/ be the source contract's fields and types. A source with supporting inputs
/ (#617): the transform reads exactly the source's inputs, by name, and each
/ input's schema must be that input's contract. Checked at declaration so a
/ transform written against a different shape than the source delivers fails
/ when the worker is defined, not on the first window of a backfill. A
/ transform taking as_of is refused: a window has no single instant it is
/ "as of", and choosing one here would be guessing at what the transform
/ means by it.
/ @private
require_transform:{[worker;cfg]
    who:"define: ",string[worker];
    if[not -11h=type cfg`transform;
        'who,"'s transform must be the name of a .qetl.transform transform"];
    d:.qetl.transform.def cfg`transform;
    src:.qetl.source.def cfg`source;
    several:0<count src`supporting;
    if[(not several) and not 1=count d`inputs;
        'who,"'s transform ",string[cfg`transform]," must read exactly one input, the fetched batch"];
    if[d`as_of;
        'who,"'s transform ",string[cfg`transform]," takes as_of, which a bounded window cannot supply"];
    contract:flip (src`columns)!{[c] $[c within "AZ"; (); c$()]} each src`types;
    if[not several;
        p:.qetl.transform.problems[contract;first value d`inputs;0b];
        if[count p;
            'who,"'s transform ",string[cfg`transform]," does not read source ",string[cfg`source],"'s contract: ","; " sv p];
        :(::)];
    contracts:((enlist src`table_name)!enlist contract),src`supporting;
    if[not (asc key d`inputs)~asc key contracts;
        'who,"'s transform ",string[cfg`transform]," must read source ",string[cfg`source],"'s inputs ",
         (", " sv string key contracts)," - it reads ",", " sv string key d`inputs];
    p:raze {[ins;contracts;nm]
        (string[nm],": "),/:.qetl.transform.problems[contracts nm;ins nm;0b]
      }[d`inputs;contracts] each key contracts;
    if[count p;
        'who,"'s transform ",string[cfg`transform]," does not read source ",string[cfg`source],"'s contracts: ","; " sv p];
    }

/ Private: the columns on_conflict matches rows by, in the TARGET's names.
/ .
/ The source's row_key names source columns - the contract requires it - but
/ publish matches the transformed batch, and a transform may rename the very
/ columns the key is made of (databento's symbol/ts_event become sym/time,
/ upstream_trades' ex becomes venue). So the key the writer uses is the
/ worker's to declare, defaulting to the source row_key for a transform that
/ keeps its names. Either way it must be among the transform's declared
/ output columns: checked here, so a key the transform drops fails the
/ declaration rather than every window with an error that only names a column.
/ @return the target key as a symbol vector
/ @private
require_target_key:{[worker;decl]
    k:$[`target_key in key decl; decl`target_key; .qetl.source.row_key decl`source];
    if[not 11h=abs type k;
        '"define: ",string[worker],"'s target_key must be a symbol or symbol vector naming output columns"];
    k:(),k;
    out:cols (.qetl.transform.def decl`transform)`output;
    absent:k where not k in out;
    if[count absent;
        '"define: ",string[worker],"'s ",$[`target_key in key decl;"target_key";"source row_key"],
         " names ",(", " sv string absent)," which transform ",string[decl`transform],
         " does not output (",(", " sv string out),")",
         $[`target_key in key decl;"";" - declare target_key in the output's column names"]];
    k}

/ The source_version a run of `worker` uses when it names none, or ` when the
/ worker declares no default and a run must name one.
/ @param worker the worker's name
/ @return a symbol
/ @eg .qetl.job.bounded.default_version `demo_deals_backfill  ->  `
default_version:{[worker] (def worker)`source_version}

/ ------------------------------------------------- A RUN FROM A COMMAND LINE
/ .
/ ONE PARSER FOR EVERY LAUNCHER. scripts/processes/torq_backfill.q (the fleet
/ path, `uqs backfill`) and scripts/dev/run_backfill.q (the TorQ-free dev
/ path) each kept their own, and they drifted: the dev one refused a run with
/ no -version while the fleet one fell back to the worker's declared default,
/ so a command line did not move between them unchanged, as run_backfill.q
/ promised it would. Both load the ETL tree, so both call this.

/ The flags a run cannot do without. -version is not among them: a worker
/ that declares a default source_version runs under it (version_from_flags).
required_flags:`worker`from`to

/ Refuse unless every required flag has a value, naming all that do not.
/ .
/ .Q.opt keeps each flag's words as a list of strings, so the value is the
/ first of them. Missing is either not given at all, or given with nothing
/ after it - which .Q.opt maps to an empty list - so `-from` with no value is
/ refused here by name rather than failing later as a type error. Presence is
/ tested with `in key` rather than by indexing, because what a dictionary
/ returns for an absent key depends on its value list's prototype.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return flag -> its value, as a string
/ @throws error naming every missing flag
/ @eg .qetl.job.bounded.require_flags `worker`from`to!(enlist "demo";enlist "2026.09.13";enlist "2026.09.14")  ->  `worker`from`to!("demo";"2026.09.13";"2026.09.14")
require_flags:{[opts]
    missing:required_flags where not (required_flags in key opts) and 0<count each opts required_flags;
    if[count missing;
        '"backfill: missing ",(", " sv "-",/:string missing),
         " - a backfill with no range would publish the wrong window and record it as covered"];
    required_flags!first each opts required_flags}

/ The source_version a run records coverage under: -version when given,
/ else the worker's declared default, else a refusal - a worker that declares
/ none is one whose source can be restated, and guessing the release there
/ files a restatement under the old one.
/ @param opts the parsed command line, as .Q.opt returns it
/ @param worker the worker's name
/ @return the version, a symbol
/ @throws error when neither is there
version_from_flags:{[opts;worker]
    if[(`version in key opts) and 0<count opts`version; :`$first opts`version];
    dv:default_version worker;
    if[null dv;
        '"backfill: missing -version - ",string[worker]," declares no default source_version, so a run must say which release of the source it records coverage under"];
    dv}

/ The worker and run specification a command line names, parsed and typed.
/ An empty or reversed range is refused here, before anything is started,
/ rather than at the worker's own validation after init.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return a dict of worker and spec (source_version, range_from, range_to)
/ @throws error when a flag is missing, a bound is not a q timestamp, or -from is not before -to
spec_from_flags:{[opts]
    f:require_flags opts;
    from_ts:"P"$f`from;
    to_ts:"P"$f`to;
    if[null from_ts; '"backfill: -from is not a timestamp: ",f`from];
    if[null to_ts;   '"backfill: -to is not a timestamp: ",f`to];
    if[to_ts<=from_ts; '"backfill: -from is not before -to"];
    worker:`$f`worker;
    `worker`spec!(worker;
        `source_version`range_from`range_to!(version_from_flags[opts;worker];from_ts;to_ts))}

/ Private: a config carrying every optional key, absent ones as (::).
/ @private
normalised:{[cfg]
    missing:optional_cfg where not optional_cfg in key cfg;
    if[0=count missing; :cfg];
    cfg,missing!count[missing]#enlist (::)}

/ One worker's configuration, or a refusal naming it.
/ .
/ Throws rather than returning a null for the same reason .qetl.source.def
/ does: a caller handed an empty dict fails later and somewhere else.
/ @param worker the defined worker's name, as a symbol
/ @return the config dict (source, dataset, width, transform, partition,
/   and the derived ns)
/ @throws error naming the worker when define was never called for it
/ @eg .qetl.job.bounded.def `demo_deals_backfill
def:{[worker]
    if[not worker in defined[];
        '"def: ",string[worker]," has no configuration - call .qetl.job.bounded.define first"];
    worker_cfg worker}

/ One worker's partition, resolved.
/ .
/ Exists so no call site repeats `$[`partition in key cfg;...;`]`. define
/ stores the resolved value, so this is a plain lookup - but going through a
/ named function means a worker's partition has exactly one spelling wherever
/ it is needed, which is what the coverage reads depend on.
/ @param worker the defined worker's name
/ @return its partition symbol, ` when it declared none
/ @eg .qetl.job.bounded.partition_of `demo_deals_backfill
partition_of:{[worker] (def worker)`partition}

/ ------------------------------------------------------- WORKER STATE

/ Private: read and write a global in the WORKER's namespace. See the header
/ for why the state cannot live here.
/ .
/ Named read_state/write_state, not get/put: `get` is a q BUILTIN (the
/ counterpart of `set`), so defining it in a namespace throws `assign at
/ load time and aborts the rest of the file - leaving .qetl.job.bounded half-populated
/ while the enclosing script carries on. Seventh reserved-name collision in
/ this repository, after desc, tables, sv, load, var and save.
/ @private
read_state:{[worker;nm] value ` sv ((def worker)`ns),nm}
write_state:{[worker;nm;v] (` sv ((def worker)`ns),nm) set v}

/ Private: one of the worker's own methods - the inherited delegate, or the
/ worker's override. run and do_window go through here rather than calling
/ the shell's plan, fetch and publish directly; that is what makes an
/ override take effect, and the delegate calls back into the shell's
/ function by its full name, so there is no loop.
/ @private
own:{[worker;nm] read_state[worker;nm]}

/ The worker's current run specification.
spec:{[worker] `source_version`range_from`range_to!read_state[worker] each `source_version`range_from`range_to}

/ ------------------------------------------------------------------ INIT

/ Resolve everything that can fail BEFORE any work happens.
/ .
/ Order is deliberate, cheapest and most-likely-misconfigured first:
/ configuration, then the contract, then the source and its fixture, then
/ the coverage ledger's shape, then the lock, then the connection. A worker
/ that takes a lock and then finds its configuration wrong has to release it
/ again; one that connects and then fails the contract has burned a
/ connection for nothing.
/ @param worker the worker's name
/ @param run_spec dict of source_version, range_from, range_to
/ @return the run specification, as stored
/ A GUARD, with the work in init_body below (#492).
/ .
/ init TAKES the instance lock and then keeps going - credential lookup,
/ connect, the source contract - so anything that throws after that line
/ leaves the lock behind. #490 put this same guard on `run` and missed this
/ path entirely, because a failed init means `run` is never called: an
/ unreachable source leaked a lock on every attempt.
/ .
/ RELEASES ON THE FAILURE BRANCH ONLY, which is where this differs from
/ `run`. A successful init must KEEP the lock - holding it across the run is
/ the whole reason it was taken - so only the throw path cleans up.
/ @param worker the worker's name
/ @param run_spec the run specification - source_version, range_from, range_to
/ @return whatever init_body returns
init:{[worker;run_spec]
    / Before the guard below, not inside it: a refusal must write nothing,
    / and the guard's failure path writes the status file - the file this
    / refusal exists to keep the first worker's.
    claim_process[.qetl.run.proc_name[];worker];
    / Each init begins the lifecycle afresh, so its first transition checks the
    / status file for a run an earlier process left unfinished.
    write_state[worker;`phase;`];
    r:.[{[w;s] (1b; init_body[w;s])};(worker;run_spec);{[e] (0b;e)}];
    if[not first r; fail_run[worker;last r]; cleanup worker; 'last r];
    last r}

/ The bounded worker this TorQ process belongs to, ` until one initialises.
process_worker:`

/ Private: refuse a second bounded worker in one TorQ process (#608).
/ .
/ Two pieces of a worker's state are really the PROCESS's under TorQ: the
/ status file is named for the procname, not the worker, and the backfill
/ process sets the process-wide .qetl.io.default to an HDB writer
/ partitioned by ITS worker's time column. A second worker would have its
/ transitions validated against the first's file, or overwrite it, and
/ would partition by the first worker's column - silently. One worker per
/ process was the convention (torq_backfill.q takes one -worker); this
/ makes it the rule.
/ .
/ The same worker initialising again is allowed - a rerun at a new
/ source_version is exactly that. Plain q, with no procname, is unaffected:
/ there the status file is the worker's own, and the tests run many
/ workers in one process.
/ @param procname the process's TorQ name, ` in plain q
/ @param worker the worker's name
/ @return the worker's name
/ @throws error naming both workers when the process already belongs to
/   another one
/ @private
claim_process:{[procname;worker]
    if[null procname; :worker];
    held:.qetl.job.bounded.process_worker;
    if[(not null held) and not worker~held;
        '"init: ",string[worker]," refused - process ",string[procname]," already runs ",
         string[held],". One bounded worker per process: they would share its status file and HDB writer"];
    .qetl.job.bounded.process_worker:worker;
    worker}

/ ---------------------------------------------------------------- STATUS
/ .
/ What an orchestrator reads: .qetl.status's file for this process -
/ starting, running, then idle, completed or failed. Airflow's sensor
/ (python/uqf_airflow_provider) polls it and nothing else, so a run that
/ never writes it leaves the sensor waiting until its timeout. Until this
/ section the only writer was .qetl.job.bounded.state.fail, which nothing on
/ the live path called.
/ .
/ Written in every mode that runs windows, dry_run included: the file says
/ how the PROCESS ended, which an orchestrator running a rehearsal needs as
/ much as a real run. It records no data.

/ Private: the instance this run reports as - TorQ's procname under a stack,
/ the worker's own name in plain q.
/ @param worker the worker's name
/ @return the instance id naming the status file
/ @private
instance:{[worker] p:.qetl.run.proc_name[]; $[null p; worker; p]}

/ Private: the progress fields the status file carries, zero before the run
/ has made any.
/ @param worker the worker's name
/ @return dict of cursor, rows_published, windows_completed and reactions_owed
/ @private
progress_now:{[worker]
    d:`cursor`rows_published`windows_completed`reactions_owed!(0Np;0;0;0);
    p:@[read_state[worker;];`progress;{[e] ()!()}];
    $[99h=type p; d,(key[d] inter key p)#p; d]}

/ ------------------------------------------------------------ RUN PHASE
/ .
/ One run is recorded in three places an outsider reads: the heartbeat, the
/ status file Airflow polls, and the run ledger (etl_runs). Each used to be
/ written by its own call at each step, in its own vocabulary, and run_body
/ decided what to write next by reading the status file back off disk - so a
/ lifecycle edge one record missed was found only at runtime (#600, #610).
/ .
/ Now the worker holds one phase in memory, advance_phase is the only thing
/ that moves it, and the three records are derived from the move by this
/ table. The status file has no `partial, so a partial run reads `failed
/ there - one row here, not a conditional at a call site. .qetl.status still
/ refuses an illegal transition in the file: that is the guard across
/ processes, where memory does not reach.
/ .
/ The checkpoint, coverage and reaction ledgers are per window, not per run,
/ and each already has one writer (finish_window, notify_published), so they
/ are not derived from the phase.
phases:([phase:`ready`running`idle`completed`partial`failed]
    beat:`starting`running`idle`completed`partial`failed;
    status:`starting`running`idle`completed`failed`failed;
    run_ledger:`none`begin`finish`finish`finish`finish)

/ Private: where each phase may go next. ` is a worker before init. Any phase
/ may fail; a finished run may only begin again, through `ready.
/ @private
phase_edges:(!). flip (
    (`;          `ready`failed);
    (`ready;     `running`failed);
    (`running;   `idle`completed`partial`failed);
    (`idle;      `ready`failed);
    (`completed; `ready`failed);
    (`partial;   `ready`failed);
    (`failed;    `ready`failed))

/ Private: move a worker's run to its next phase, and write every record the
/ move implies.
/ .
/ In a fixed order: the ledger row opens before the status file is written,
/ so the file names the run it describes, and closes after it, because
/ closing clears the current run. Every write on the way to `failed is
/ protected - that path runs while an error is being reported, and a write
/ that fails there must not replace it. The run ledger is closed only for a
/ run that opened a row, which an init failing never did.
/ .
/ The status file is read once per init, on the first move: a run an
/ earlier process left `starting or `running died without an outcome, and
/ .qetl.status rightly refuses either going straight to `starting. So the
/ death is written down first, then the start.
/ @param worker the worker's name
/ @param to the next phase, a key of phases
/ @param err the error, "" unless `to` is `partial or `failed
/ @return the new phase
/ @throws error naming both phases when the move is not in phase_edges
/ @private
advance_phase:{[worker;to;err]
    was:read_state[worker;`phase];
    if[not to in phase_edges was;
        '"advance_phase: ",string[worker]," cannot go from ",$[null was; "before init"; string was]," to ",string to];
    row:phases to;
    call:$[`failed~to;
        {[worker;f;a] .[f;a;{[worker;e] .qetl.log.err[worker;"could not record the failure";enlist[`error]!enlist e]}[worker]]}[worker];
        {[f;a] f . a}];
    if[(null was) and `ready~to; call[record_orphan;enlist worker]];
    if[`begin~row`run_ledger; call[begin_run;enlist worker]];
    call[.qetl.hb.beat;(worker;row`beat)];
    call[.qetl.status.write_status;(worker;instance worker;row`status;spec worker;progress_now worker;err)];
    closed:$[(`finish~row`run_ledger) and `running~was; call[end_run;(to;run_totals worker)]; (1b;"")];
    write_state[worker;`phase;to];
    / A run whose ledger row could not be closed did not end the way the file
    / just said (#675): Airflow would pass it, the process exit 0, and its
    / etl_runs row read `running forever. So it fails - the file then names
    / the open row's run_id and why. Already failing, it is only logged.
    if[not first closed;
        .[{.qetl.log.err[x;y;z]};(worker;"the run ledger could not record this run's outcome";
            `outcome`error!(to;last closed));::];
        if[not `failed~to;
            to:advance_phase[worker;`failed;
                "the run ledger could not record this run's ",string[to]," outcome (",(last closed),
                ") - its etl_runs row stays open"]];
        / Release the run in memory, after the file has named it: the row
        / stays open on disk, but this process must not refuse every later
        / run as "already in flight".
        @[{.qetl.run.release[]};::;{[e] (::)}]];
    to}

/ Private: record a run an earlier process never finished as failed.
/ @param worker the worker's name
/ @private
record_orphan:{[worker]
    id:instance worker;
    if[(.qetl.status.previous_state id) in `starting`running;
        .qetl.status.write_status[worker;id;`failed;spec worker;progress_now worker;
            "the previous run never recorded an outcome - it was killed or crashed"]]}

/ Private: fail the worker's run, never throwing - it runs on the error path.
/ @param worker the worker's name
/ @param e the error, as caught
/ @private
fail_run:{[worker;e]
    msg:$[10h=type e; e; -11h=type e; string e; .Q.s1 e];
    @[{[a] advance_phase . a};(worker;`failed;$[count msg; msg; "failed"]);
      {[worker;e2] .qetl.log.err[worker;"could not record the failure";enlist[`error]!enlist e2]}[worker]]}

/ Private: every check that needs neither a ledger nor the source - the
/ run's spec, the worker's contract, the source's fixture, the conflict
/ strategy. Shared by init and by the validate and plan modes, so the three
/ cannot disagree about what a valid run is.
/ @param worker the worker's name
/ @param run_spec dict of source_version, range_from, range_to
/ @return the run specification, as stored
/ @private
check_static:{[worker;run_spec]
    cfg:def worker;
    write_state[worker;`source_version;run_spec`source_version];
    write_state[worker;`range_from;run_spec`range_from];
    write_state[worker;`range_to;run_spec`range_to];

    if[null run_spec`source_version;
        '"init: source_version must be set - coverage under one source release says nothing about another"];
    .qetl.coverage.require_interval[run_spec`range_from;run_spec`range_to];

    .qetl.job.bounded.state.register[worker;cfg`ns];
    .qetl.job.bounded.state.require_contract worker;
    .qetl.log.dbg[worker;"init: worker contract satisfied";enlist[`ns]!enlist cfg`ns];

    .qetl.source.validate_fixture cfg`source;
    .qetl.log.dbg[worker;"init: source fixture satisfies the contract";enlist[`source]!enlist cfg`source];
    on_conflict worker;
    spec worker}

/ The validate mode: is this a run that could start? Checks the declaration,
/ the contract, the fixture, the range and the conflict strategy, and reports
/ whether a credential is configured - without using it, though a sources.csv
/ row must resolve. Reads no ledger,
/ takes no lock, opens no source and writes nothing, so it is safe anywhere,
/ CI included.
/ @param worker the worker's name
/ @param run_spec dict of source_version, range_from, range_to
/ @return dict describing the run that would start
/ @throws whatever check_static throws - the first thing wrong with the run -
/   or resolve_setting, for a source configured by a broken row
validate:{[worker;run_spec]
    cfg:def worker;
    s:check_static[worker;run_spec];
    src:.qetl.source.def cfg`source;
    / A configured row is checked here too - a stub, a transport mismatch or
    / an unset secret variable - so validate catches what init would. Its
    / result may hold a secret, so it is dropped.
    if[`settings~.qetl.source.credential_origin cfg`source; .qetl.source.resolve_setting cfg`source];
    s,`state`worker`source`target`dataset`width`windows`on_conflict`live!
        (`validated;worker;cfg`source;src`target;cfg`dataset;cfg`width;
         count .qetl.job.bounded.runtime.windows[s`range_from;s`range_to;cfg`width];
         on_conflict worker;.qetl.source.has_credentials cfg`source)}

/ The plan mode: what would a run fetch? Validates, then reads the coverage
/ ledger and the checkpoint - read-only - and returns the windows a run would
/ fetch now. Takes no lock and opens no source, so it cannot say how many
/ rows those windows hold; a dry run can.
/ @param worker the worker's name
/ @param run_spec dict of source_version, range_from, range_to
/ @return validate's report, with the planned windows, how many there are,
/   and the checkpoint the run would resume from
plan_only:{[worker;run_spec]
    v:validate[worker;run_spec];
    .qetl.coverage.attach[];
    cursor:.qetl.job.bounded.state.load_checkpoint[worker;spec worker];
    ws:own[worker;`plan][cursor];
    v,`state`planned`cursor`plan!(`planned;count ws;cursor;ws)}

/ The initialisation itself. Never call this directly - `init` is what
/ releases the lock when this throws.
/ @param worker the worker's name
/ @param run_spec the run specification - source_version, range_from, range_to
/ @return the worker's name
init_body:{[worker;run_spec]
    cfg:def worker;
    check_static[worker;run_spec];

    / Validate the ledger's shape before trusting a read of it (#60). Only
    / bites when the ledger already existed, i.e. when another process
    / created it - which is exactly when its shape is evidence rather than
    / our own assumption.
    .qetl.coverage.attach[];

    / Same reasoning one table over: create it and verify its shape
    / here, in a live path, rather than leaving a checker that never fires.
    .qetl.hb.attach[];
    advance_phase[worker;`ready;""];

    .qetl.job.bounded.state.acquire_lock worker;

    / Live only when a credential is configured. An absent credential is an
    / explicit statement that this is a demo, NOT a fallback for a failed
    / connection - falling back on failure would turn an outage into
    / silently synthetic data that coverage then records as complete.
    / A WARNING that says how to fix it, not just that it happened: the variable
    / to set, what its value looks like for this source's transport (an ODBC
    / connection string, or host:port for kdb+ IPC), an example, and where it
    / has to be exported - the shell `uqs backfill` runs in, whose environment
    / the process inherits. Not an error: no credential is the declared way
    / to run on the fixture, and a demo stack runs like that on purpose.
    / .
    / The example comes from .qetl.source.credential_example, which asks the
    / SOURCE first. It used to be a two-way branch on transport alone, and
    / that was wrong in a way that wasted an operator's time: every ODBC
    / source in this tree is a DuckDB file, and all three were shown a
    / SingleStore string asking for a SERVER, PORT, UID and PWD that DuckDB
    / has no concept of - for a database no source here connects to.
    / .
    / The wording says "a path or a password" rather than "the secret" for
    / the same reason. A DuckDB credential holds nothing secret; it is a file
    / path, kept in the environment because an absolute local path is
    / machine-specific and this repository is public. Calling that a secret
    / teaches an operator that the rule is theatre.
    / `transport`, not `var`: var is a q builtin (variance).
    live:.qetl.source.has_credentials cfg`source;
    if[not live;
        tr:.qetl.source.for_source cfg`source;
        .qetl.log.warn[worker;"no credential - running on the source's fixture, not live data. To go live: give the source a row in sources.csv (`uqs config sources` shows which file is read), or export the variable below in the shell you run `uqs backfill` from, then run it again. A secret is only ever read from the environment - a row names its variable - so a password stays out of this repository and off the command line";
            `variable`settings_row`expects`example!(
                .qetl.source.credential_var cfg`source;
                "," sv (string cfg`source;string (.qetl.source.def cfg`source)`transport;.qetl.source.credential_example cfg`source;"");
                tr`expects;
                .qetl.source.credential_example cfg`source)]];
    write_state[worker;`handle;$[live; connect worker; 0Ni]];

    .qetl.log.register[];
    .qetl.log.info[worker;"initialised";
        `source_version`range_from`range_to`live!
        (run_spec`source_version;run_spec`range_from;run_spec`range_to;
         not null read_state[worker;`handle])];

    spec worker}

/ Private: open the live connection. Separate from init so the failure is
/ attributable, and so a test can exercise init without one.
/ .
/ The source's transport opens it - .qetl.source.transport's `open`: an ipc
/ credential is host:port, an odbc one a connection string, a local one an
/ HDB directory's path, validated there so a wrong path fails the run rather
/ than quietly reading the fixture.
/ @private
connect:{[worker]
    source:(def worker)`source;
    cred:.qetl.source.require_credentials source;
    transport:(.qetl.source.def source)`transport;
    .qetl.log.dbg[worker;"connecting to the source";`source`transport!(source;transport)];
    opener:(.qetl.source.transport_def transport)`open;
    @[opener;cred;{[source;e] '.qetl.job.bounded.connect_error[source;e]}[source]]}

/ The error connect throws when it cannot reach a source.
/ .
/ SHORT ON PURPOSE, and assembled rather than written inline, because q
/ SILENTLY truncates a thrown string at 254 bytes - measured, not the 255
/ this tree said before: a 254-byte string survives a throw intact and a
/ 255-byte one comes back 254. singlestore_odbc.q's
/ require_available already keeps its own message short for that reason - and
/ this one WRAPPED that message inside a longer one, recreating the bug a
/ layer up. The composed string ran to 286 bytes, q threw 254, and the tail
/ it dropped was "...record synthetic data as covered" - the half that says
/ WHY a backfill refuses. Measured on 2026.09.26 against a real run.
/ .
/ The driver's own text comes FIRST because it is the actionable half: it
/ names what is missing and how to install it. Ours follows, and is trimmed
/ rather than the driver's if the two together still do not fit.
/ @param source the source that could not be reached
/ @param e the opener's error text
/ @return the message to throw, never longer than q will carry
/ @eg 256>count .qetl.job.bounded.connect_error[`crypto_market_data;"driver not loaded"]  ->  1b
connect_error:{[source;e]
    msg:"connect: ",string[source]," unreachable - ",e,
        ". Not falling back to the fixture: that would record synthetic data as covered";
    $[254<count msg; 254#msg; msg]}

/ ------------------------------------------------------------------ PLAN

empty_windows:{[] ([] range_from:`timestamp$(); range_to:`timestamp$())}

/ The windows still to do.
/ .
/ ONE narrowing: coverage. Every gap in the range is planned, at this
/ source_version, whatever the cursor says - the body below explains what
/ went wrong when the cursor was a hard lower bound, and `cursor` is now an
/ unread parameter kept for the signature its callers already pass.
/ .
/ This header used to describe TWO narrowings, cursor first and coverage
/ second, which is what the function did before a restatement could withdraw
/ coverage the cursor had already passed. The body changed and the header did
/ not, so the two disagreed about the thing the function is for.
plan:{[worker;cursor]
    cfg:def worker;
    s:spec worker;
    / ONE as_of for the whole plan, captured here rather than read per call
    / Calling .z.p inside each coverage read would plan against a
    / ledger that could be superseded midway, so a range could be reported
    / both covered and uncovered within a single planning pass - and the
    / resulting window list would correspond to no coherent belief about the
    / data at any instant.
    as_of:.z.p;
    / COVERAGE DECIDES WHAT IS LEFT. The cursor is a resumption hint and
    / nothing more.
    / .
    / This used to plan from the cursor as a hard lower bound, and the
    / cursor of a finished run is range_to. So after a restatement withdrew
    / a window's coverage, a re-run with the same spec found its
    / cursor at the end, consulted coverage for nothing, and reported idle
    / over a range the ledger itself said was missing - supersede worked and
    / nothing would ever refill what it withdrew. advanced_to's own comment
    / had even described the shape ("they simply never get filled while
    / that checkpoint stands") and accepted it. Found by
    / tests/q/run_two_instances.q, the first place a supersede was followed
    / by a same-spec re-run.
    / .
    / Now every gap in the whole range is planned, whatever the cursor says.
    / The cursor still does the one thing it is for: when the range BEHIND it
    / is fully covered - the ordinary resume after an interruption - the
    / gaps all lie at or after it and the plan starts there, exactly as
    / before. When a gap lies behind it, the gap wins, because a gap is a
    / fact about the data and a cursor is a note about a previous run.
    todo:.qetl.job.bounded.runtime.remaining[cfg`dataset;cfg`partition;s`source_version;as_of;s`range_from;s`range_to];
    if[0=count todo; :empty_windows[]];
    / one set of windows per uncovered sub-range, then flattened - a gap in
    / the middle must not be bridged by a window spanning it.
    ws:raze {[width;w] .qetl.job.bounded.runtime.windows[w`range_from;w`range_to;width]}[cfg`width] each todo;
    .qetl.log.dbg[worker;"planned";`gaps`windows`width`cursor!(count todo;count ws;cfg`width;cursor)];
    ws}

/ ------------------------------------------------------- FETCH / PUBLISH

/ Fetch one window under the retry policy.
/ .
/ Transport failures retry; data failures do not, because retrying a schema
/ mismatch produces the same mismatch more slowly. with_retry returns a dict
/ rather than throwing, so the caller decides what a terminal failure means -
/ here, the window fails and the run moves on.
/ .
/ Every fetched window is validated against the source contract.
/ Not belt-and-braces: a source that dropped a column returns rows where the
/ missing column reads as a NULL in most q code, so without this the worker
/ publishes nulls and records the window as covered.
fetch:{[worker;from_ts;to_ts]
    cfg:def worker;
    h:read_state[worker;`handle];
    / The fifth, unused parameter is what makes this a projection. With four
    / it was a full application: the query ran HERE, before with_retry, so a
    / failing query skipped the retries and the failed-window path and threw
    / out of the run, and a working one handed with_retry a table that `t[]`
    / happened to return unchanged.
    / The source joins the window's log context for the fetch and its retries.
    r:.qetl.log.with_context[enlist[`source]!enlist cfg`source;
        .qetl.job.bounded.runtime.with_retry;
        (.qetl.job.bounded.runtime.policy[];
         {[source;h;from_ts;to_ts;unused] last .qetl.source.fetch_window[source;h;from_ts;to_ts]}[cfg`source;h;from_ts;to_ts])];
    .qetl.log.dbg[worker;"fetch attempted";
        `range_from`range_to`state`attempts`rows!(from_ts;to_ts;r`state;r`attempts;
            $[`ok~r`state; count .qetl.source.primary[cfg`source;r`result]; 0N])];
    if[`failed~r`state; :r];
    .qetl.source.validate[cfg`source;r`result];
    r}

/ Publish a window's rows into the worker's dataset.
/ .
/ The dataset, not the source's `target` (#769): coverage, reactions, the run
/ ledger and the job graph are all keyed on the dataset, so the rows must land
/ there too. A source's target is only the dataset its first worker is named
/ after - every shipped worker fills it - and a second worker over the same
/ source with a dataset of its own fills that, not the first one's table.
/ .
/ Returns the row count, which finish_window records as rows_published. Zero
/ is legal and meaningful: an empty window is positive evidence the
/ range was examined and held nothing.
publish:{[worker;batch]
    cfg:def worker;
    src:.qetl.source.def cfg`source;
    w:read_state[worker;`last_window];
    opts:`on_conflict`row_key`time_column`range_from`range_to!
        (on_conflict worker;cfg`target_key;src`time_column;w`range_from;w`range_to);
    .qetl.io.write_keyed[.qetl.io.for_cfg cfg;cfg`dataset;batch;opts]}

/ The conflict strategy this run writes under: the operator's override when
/ one is set - on_conflict in config, UQF_ON_CONFLICT, or `uqs backfill
/ --on-conflict` - else what the worker declared.
/ .
/ An override exists for the one-off: a worker that upserts by default, run
/ once with `replace to clear rows its source has since withdrawn.
/ @param worker the worker's name
/ @return the strategy, a symbol
on_conflict:{[worker]
    v:.qetl.cfg.raw `on_conflict;
    cfg:def worker;
    .qetl.io.require_strategy $[count v; `$v;
        `on_conflict in key cfg; cfg`on_conflict; .qetl.io.default_strategy]}

/ Save the cursor. Present because the contract requires it; the
/ write goes through .qetl.job.bounded.state so the checkpoint's spec-binding is not
/ re-implemented.
checkpoint:{[worker;cursor] .qetl.job.bounded.state.save_checkpoint[worker;spec worker;cursor]}

/ ------------------------------------------------------------------- RUN

/ Run one bounded pass to completion.
/ .
/ a window that exhausts its retries is TERMINAL for that window and
/ the run continues: failing the whole pass would throw away the windows that
/ did succeed, and their coverage is what makes the retry cheap. Failed
/ windows stay uncovered, so the next run plans them again.
/ .
/ A GUARD, with the work in run_body below. init takes the instance lock and
/ only cleanup releases it, so until #490 every way out of run_body that was
/ not `return` leaked it: a window that threw past the retry policy, a
/ contract error, an operator's ctrl-c. The run then logged `failed` and the
/ next one refused to start, naming a pid that had already exited.
/ .
/ The idiom is with_file_lock's, one file over in backfill_state.q: capture
/ (ok; value), release, then re-raise what was caught. An error path that
/ skips the release is how one failed run wedges every later one.
/ @param worker the worker's name
/ @return the run's result dictionary
run:{[worker]
    r:@[{[w] (1b; run_body w)};worker;{[e] (0b;e)}];
    if[not first r; fail_run[worker;last r]];
    cleanup worker;
    if[not first r; 'last r];
    last r}

/ Private: queue what an earlier, interrupted run of this worker wrote and
/ never finished, over this run's range.
/ .
/ A run killed after recording a window's coverage and before finishing its
/ HDB partition leaves that partition unsorted, without p#sym - correct rows
/ that as-of joins read wrongly. Coverage now calls the window done, so a
/ re-run plans nothing for it and finishes nothing, forever. Read off the
/ files, because the to-do list the killed run held died with it. Not on a
/ dry run, which changes nothing on disk.
/ @return how many partitions were queued
/ @private
recover_unfinished:{[worker]
    if[not .qetl.job.bounded.runtime.allows`finish_store; :0];
    s:spec worker;
    n:.qetl.io.recover[.qetl.io.for_cfg def worker;(def worker)`dataset;s`range_from;s`range_to];
    if[n>0;
        .qetl.log.warn[worker;"found partitions an earlier run wrote and never finished - finishing them with this run";
            enlist[`partitions]!enlist n]];
    n}

/ Private: the io manager's end-of-run step, behind the dry-run gate. A dry
/ run wrote nothing, so a store has nothing to finish - and an HDB writer's
/ finish asks the HDB to reload, which a rehearsal must not.
/ @param worker the worker's name
/ @private
finish_store:{[worker]
    if[.qetl.job.bounded.runtime.allows`finish_store; .qetl.io.finish .qetl.io.for_cfg def worker]}

/ The run itself. Never call this directly - `run` is what releases the lock.
/ @param worker the worker's name
/ @return the run's result dictionary
run_body:{[worker]
    / Every run declares its own start. A second run in the same process - a
    / rerun that finds nothing to do, or an operator calling run[] again -
    / begins from the last run's outcome, and .qetl.status rightly refuses
    / going from there to `idle or `running without a new `starting.
    if[not `ready~read_state[worker;`phase]; advance_phase[worker;`ready;""]];
    / The run's OWN cursor starts null, not at the loaded checkpoint. The
    / checkpoint's job is to inform the plan; from there the cursor tracks
    / what THIS run has published, in the order it publishes it. Seeding it
    / from the checkpoint broke restatement: a finished run's checkpoint is
    / range_to, so the first refilled window ended at or before it and
    / advanced_to refused a cursor that "stood still" - the strict-forward
    / rule, correct within a run, applied across two. The run then died after
    / doing the work but before recording it.
    write_state[worker;`progress;no_progress];
    / `running from here, not from the first window: recovering what an
    / interrupted run left and replaying failed reactions both write. Opens
    / the run's ledger row too, so every window this run materialises is
    / attributable to it, and an execution that dies mid-flight leaves a row
    / reading `running rather than no trace - see .qetl.run's header.
    advance_phase[worker;`running;""];
    recovered:recover_unfinished worker;
    replayed:replay_reactions worker;
    cursor:.qetl.job.bounded.state.load_checkpoint[worker;spec worker];
    windows:own[worker;`plan][cursor];
    write_state[worker;`progress;@[read_state[worker;`progress];`windows_planned;:;count windows]];
    if[0=count windows;
        / "ran, found no work" is a SUCCESS, not a failure. An
        / orchestrator that cannot tell them apart retries a successful
        / no-op forever.
        / INFO, not DEBUG: "the run did nothing" is the question this answers.
        s:spec worker;
        .qetl.log.info[worker;"idle - every window in the range is already covered at this source_version";
            `source_version`range_from`range_to!(s`source_version;s`range_from;s`range_to)];
        / Finish what recover_unfinished found, which is the only thing an
        / idle run has to do - and only then, so an idle run with nothing to
        / repair does not ask the HDB to reload for nothing.
        if[(recovered>0) or replayed>0; finish_store worker];
        / Where coverage already stands, and what it still owes, for the
        / status file. An idle run that could not replay an owed reaction
        / is not idle: a derived dataset is stale (#632).
        owed:owed_reactions worker;
        write_state[worker;`progress;@[read_state[worker;`progress];`cursor`reactions_owed;:;(cursor;count owed)]];
        / The phase REACHED, which is `failed if the run ledger refused it.
        ended:advance_phase[worker;$[count owed; `partial; `idle];$[count owed; owed_error owed; ""]];
        :`state`windows_completed`windows_failed`rows_published`cursor`reactions_owed!
            (ended;0;0;0;cursor;count owed)];
    do_window[worker] each windows;
    / The io manager's end-of-run step, after the LAST window and whatever
    / its outcome: a window that failed wrote nothing, but the ones that
    / succeeded may have left a store that is not finished data until this
    / runs (the HDB writer sorts and attributes its partitions here). A
    / manager with no finish - memory, discard - makes this a no-op, and a
    / dry run, which wrote nothing, gives it nothing to do.
    finish_store worker;
    / A run whose windows all landed but whose reactions did not is
    / `partial, not `completed (#632): the worker's own data is complete, a
    / dataset derived from it is stale, and green would say otherwise to
    / Airflow and the browser. The coverage stays recorded either way, and
    / the next run re-fires what is owed before anything else.
    owed:owed_reactions worker;
    write_state[worker;`progress;@[read_state[worker;`progress];`reactions_owed;:;count owed]];
    p:read_state[worker;`progress];
    result:`state`windows_completed`windows_failed`rows_published`cursor`reactions_owed!
        ($[(p[`windows_failed]>0) or 0<count owed;`partial;`completed];
         p`windows_completed;p`windows_failed;p`rows_published;p`cursor;count owed);
    / one summary line per run at INFO - the aggregate a fleet view wants,
    / without the per-window noise that stays at DEBUG.
    .qetl.log.info[worker;"run finished";result];
    / The terminal move, so a finished worker does not read as wedged. The
    / per-window beat inside do_window is the one that catches a worker
    / stuck mid-window, which is the case a status file cannot show - it
    / says `running` and keeps saying it.
    why:();
    if[p[`windows_failed]>0;
        why,:enlist string[p`windows_failed]," of ",string[count windows]," window(s) failed - they stay uncovered, so a re-run retries them"];
    if[count owed; why,:enlist owed_error owed];
    / Report the phase reached, not the one asked for: a run whose ledger
    / close failed ends `failed (#675), and its result and exit code say so.
    result[`state]:advance_phase[worker;result`state;$[count why; "; " sv why; ""]];
    / No cleanup here: `run` above releases on EVERY exit, this one included.
    / .
    / The release used to live on this line, which made it the happy path's
    / privilege - a run that threw never reached it. Before that it was on
    / the failure branch only, so a run that FAILED unlocked and a run that
    / SUCCEEDED did not. Moving it into the guard is what finally covers
    / both, and the operator's instinctive fix - `rm <worker>.lock` - never
    / worked anyway, because acquire_lock uses mkdir and the lock is a
    / directory.
    / .
    / In `run` rather than in the process script, because torq_backfill.q is
    / not the only caller: a test, or an operator at a q prompt, runs init
    / and run directly and leaked one just as readily. A bounded run has no
    / state to carry past its terminal state, so the run ending IS the
    / session ending. cleanup is idempotent, so a caller that also cleans up
    / (every test teardown does) is unaffected.
    result}

/ The exit code an orchestrator should see for a terminal run state.
/ .
/ HERE, not in scripts/processes/torq_backfill.q, because the states are this
/ file's vocabulary and this file already states what they mean - see `ran,
/ found no work is a SUCCESS` in run. Keeping the mapping next to the process
/ that exits let the two drift, and they did: torq_backfill.q read
/ `$[`completed~state; 0; 1]`, reasoning carefully about `partial` not being
/ success and never considering `idle` at all. So a run that correctly found
/ every window already covered exited 1, and Airflow - which reads the code -
/ marked it failed and retried it. A no-op retried forever is the exact
/ failure run's own comment warns about.
/ .
/ `partial` IS a failure, and deliberately: some windows failed, coverage
/ never claimed them, and a retry should pick them up.
/ @param state a run's terminal state
/ @return 0i when an orchestrator should read success, 1i otherwise
/ @eg .qetl.job.bounded.exit_code `completed  ->  0i
/ @eg .qetl.job.bounded.exit_code `idle  ->  0i
/ @eg .qetl.job.bounded.exit_code `partial  ->  1i
/ @eg .qetl.job.bounded.exit_code `failed  ->  1i
/ @eg .qetl.job.bounded.exit_code `planned  ->  0i
exit_code:{[state] $[state in `completed`idle`validated`planned; 0i; 1i]}

/ Private: open this execution's run, tolerating an absent .qetl.run.
/ .
/ Wrapped for the same reason .qetl.coverage.current_run is: run.q is not a load-time
/ dependency of this file, and a worker loaded by one of the minimal test
/ loaders should still run. Attribution is an addition to what a run records,
/ never a precondition for running one.
/ .
/ It records what the run was asked to do - dataset, source_version, range
/ and window width - the same columns for every worker, so etl_runs reads as
/ one table of every backfill.
/ @private
begin_run:{[worker]
    / Not on a dry run: a rehearsal's row in etl_runs reads as a run.
    if[not .qetl.job.bounded.runtime.allows`record_run; :(::)];
    / Attached HERE, as init attaches the coverage ledger: a runner that did not
    / (scripts/dev/run_backfill.q, or any script driving a worker) got a begin
    / that failed silently, then an end that could not record, and a run that
    / did all its work reported `failed and exited 1.
    @[{[w] cfg:def w; s:spec w;
        .qetl.run.attach[];
        .qetl.run.begin[w;`dataset`source_version`range_from`range_to`width!
            (cfg`dataset;s`source_version;s`range_from;s`range_to;cfg`width)]};
      worker;{[w;e] .qetl.log.warn[w;"could not open a run in the run ledger";enlist[`error]!enlist e];}[worker]]}

/ Private: close this execution's run with its outcome.
/ .
/ The run's state is the worker's own result state - `completed, `partial or
/ `idle - rather than a separate vocabulary, so a reader of etl_runs and a
/ reader of the worker's log see the same word for the same outcome.
/ .
/ With the run's counts: windows planned, completed and failed, and rows
/ published.
/ Never throws - it runs after the status file already says how the run
/ ended - but no longer swallows: (1b;"") when the row is closed or there is
/ none to close, (0b;error) when the close failed, for advance_phase to act
/ on (#675). A close that failed silently left the file `completed and the
/ row `running for good.
/ @private
end_run:{[state;counts]
    if[not .qetl.job.bounded.runtime.allows`record_run; :(1b;"")];
    .[{.qetl.run.finish[x;y]; (1b;"")};(state;counts);{[e] (0b;$[10h=type e; e; .Q.s1 e])}]}

/ Private: the counts the worker's progress holds, as end_run records them.
/ @private
run_totals:{[worker]
    p:read_state[worker;`progress];
    run_counts[p`windows_planned;p`windows_completed;p`windows_failed;p`rows_published]}

/ Private: a run's counts, as end_run records them.
/ @private
run_counts:{[planned;completed;failed;rows]
    `windows_planned`windows_completed`windows_failed`rows_published!(planned;completed;failed;rows)}

/ Private: one window, end to end. Accumulates into the worker's own
/ `progress` rather than returning, because a q lambda does not close over an
/ enclosing local and `each` over windows needs somewhere to put the totals.
/ Private: run a worker's declared data-quality check over one batch.
/ .
/ Returns the FAILURES, so an empty result means the batch passed - the same
/ convention .qdqc.summarize_checks already uses, and reusing it means a
/ worker can hand that function's output straight back with no adapter.
/ .
/ A worker that declares no check returns no failures. That is deliberate:
/ the framework does not force a check, because a check written to satisfy a
/ requirement rather than to catch something is worse than none - it reads as
/ protection while asserting nothing. What the framework does guarantee is
/ that a check, once declared, is unskippable.
/ .
/ The check runs in DRY RUN too. It reads the batch and publishes nothing, so
/ suppressing it would only hide the one signal a dry run could give about
/ the data - and "would this have published garbage" is exactly what a dry
/ run is for.
/ @param worker the worker's name
/ @param batch the fetched rows, before publication
/ @return a table of failures, empty when the batch is acceptable
/ @throws error when a declared check is not callable, or returns a
/   non-table, naming the worker
/ @private
run_check:{[worker;batch]
    cfg:def worker;
    if[not `check in key cfg; :no_failures[]];
    c:cfg`check;
    if[(::)~c; :no_failures[]];
    if[not 100h=type c;
        '"run_check: ",string[worker],"'s check must be a function taking the batch"];
    r:c batch;
    / A check that returns something other than a table is refused rather
    / than truthiness-tested: `if[count r]` on a stray atom would pass or
    / fail on the value's LENGTH, which is a plausible wrong answer.
    if[not .Q.qt r;
        '"run_check: ",string[worker],"'s check must return a table of failures - an empty one means the batch passed"];
    r}

/ The empty failure table, so every path returns one shape - and what a
/ worker's quality check returns when a batch passes (docs/guides/new-pipeline.md).
/ @return an empty table of check, status and detail
no_failures:{[] ([] check:`symbol$(); status:`symbol$(); detail:())}

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
transform_batch:{[worker;batch]
    cfg:def worker;
    src:.qetl.source.def cfg`source;
    nm:cfg`transform;
    if[0=count src`supporting;
        :.qetl.transform.apply[nm;(.qetl.transform.input_names nm)!enlist (src`columns)#batch]];
    if[0=count batch src`table_name; :0#.qetl.transform.output_schema nm];
    sup:key src`supporting;
    given:((enlist src`table_name)!enlist (src`columns)#batch src`table_name),
        sup!{[batch;nm;contract] (cols contract)#batch nm}[batch]'[sup;value src`supporting];
    .qetl.transform.apply[nm;given]}

/ Private: one window, end to end - fetch, transform, check, publish, record.
/ .
/ Accumulates into the worker's own `progress` rather than returning, because
/ a q lambda does not close over an enclosing local and `each` over windows
/ needs somewhere to put the totals.
/ @param worker the worker's name
/ @param w a row carrying range_from and range_to
/ @return 1b when the window completed, 0b when it failed and the run
/   should continue with the next one
/ @private
do_window:{[worker;w]
    / Everything logged while this window runs - the fetch, its requests,
    / the transform, a sidecar's own stages, the write - carries the run,
    / worker and window, and the scope ends with the window, thrown or not.
    run:.qetl.run.current[];
    ctx:(`worker`range_from`range_to!(worker;w`range_from;w`range_to)),
        $[null run; ()!(); enlist[`run]!enlist run];
    .qetl.log.with_context[ctx;window_body;(worker;w)]}

/ Private: one window, fetch to publish - do_window's body, inside its log
/ context.
/ .
/ The stages that can fail a window run in order from window_stages, each
/ taking and returning the window's state. The first to fail stops the rest,
/ and window_failed is the one place a failure is logged and counted - so a
/ new stage is a new row there, not a fifth copy of the failure path. What a
/ published window does next stays inline below: it is a sequence, not a
/ list of gates.
/ @param worker the worker's name
/ @param w the window, a dict of range_from and range_to
/ @return 1b when the window was published, 0b when it failed
/ @private
window_body:{[worker;w]
    cfg:def worker;
    .qetl.log.dbg[worker;"window start";`range_from`range_to!(w`range_from;w`range_to)];
    / The null key keeps the state a general dictionary whatever is added.
    s:{[worker;s;stage] $[`failed in key s; s; stage[worker;s]]}[worker]/[
        (``cfg`window)!(::;cfg;w);
        value window_stages];
    if[`failed in key s; :window_failed[worker;w;s`failed]];
    out:s`batch;
    r:s`result;
    .qetl.log.dbg[worker;"window published";
        `range_from`range_to`rows`dry_run!(w`range_from;w`range_to;r`rows_published;r`dry_run)];
    / Materialisation metadata, recorded HERE rather than in
    / finish_window because this is the only place the batch itself is in
    / hand - finish_window receives a niladic publisher, not rows.
    record_facts[worker;cfg;w;out;r];
    / THE PUBLICATION EVENT (.qetl.reaction). One place rows enter a dataset on this
    / path, so this is where downstream work hears about it - with the range
    / in hand, rather than a timer discovering it later by diffing coverage.
    / .
    / After record_facts, so a reaction reading the materialisation sees it.
    / Suppressed on a dry run, which published nothing: firing there would
    / make a rehearsal trigger real downstream work.
    / .
    / Protected like begin_run for the same reason - react.q is not a load
    / time dependency and a minimal loader must still run a worker.
    if[.qetl.job.bounded.runtime.allows`notify_reactions;
        / The batch and the IO manager go with it: a reaction reads what was
        / published rather than wherever it was written, and writes its own
        / output where this worker wrote - under .qetl.io.hdb there is no
        / root table to read back, and a table kept in memory dies with the
        / process (#541).
        @[{[a] .qetl.reaction.notify_published . a};
          (cfg`dataset;w`range_from;w`range_to;out;.qetl.io.for_cfg cfg);
          {[e] (::)}]];
    write_state[worker;`progress;
        @[@[@[read_state[worker;`progress];`windows_completed;+;1];`rows_published;+;r`rows_published];
          `cursor;advanced_to[worker];w`range_to]];
    / FINISH WHAT IS BEHIND THIS WINDOW. Windows run in order, so a store can
    / finish whatever lies wholly before this one's end now rather than at
    / the run's end - the HDB writer sorts those partitions, and its on_ready
    / lets the deployment make them queryable while the run goes on. After
    / the cursor and coverage, so nothing is shown that the run has not
    / recorded. A no-op for a manager without flush, and on a dry run, which
    / wrote nothing.
    if[.qetl.job.bounded.runtime.allows`finish_store; .qetl.io.flush[.qetl.io.for_cfg cfg;w`range_to]];
    .qetl.hb.beat_window[worker];
    1b}

/ Private: a stage's failure - the message window_failed logs, and the fields
/ it logs after the window's range.
/ @private
failed_with:{[s;message;fields] @[s;`failed;:;`message`fields!(message;fields)]}

/ Private: the one failure path for a window.
/ .
/ ERROR, not a throw: a failed window is terminal for that window and the run
/ continues. Nothing is published, no coverage is staged, and the next run
/ plans the window again because coverage never claimed it. Recording it with
/ the window and the stage's own fields is what makes "which windows failed
/ and why" answerable from the log rather than from a debugger.
/ @return 0b, do_window's answer for a failed window
/ @private
window_failed:{[worker;w;f]
    .qetl.log.err[worker;f`message;(`range_from`range_to!(w`range_from;w`range_to)),f`fields];
    write_state[worker;`progress;@[read_state[worker;`progress];`windows_failed;+;1]];
    0b}

/ Private: FETCH the window from the source, through the worker's own fetch so
/ an override still applies. A fetch that gave up carries its classified kind
/ and the attempts it made.
/ @private
stage_fetch:{[worker;s]
    w:s`window;
    f:own[worker;`fetch][w`range_from;w`range_to];
    if[`failed~f`state;
        :failed_with[s;"window failed";`kind`attempts`error!(f`kind;f`attempts;f`error)]];
    @[s;`batch;:;f`result]}

/ Private: TRANSFORM, between fetch and the quality gate, so the gate judges
/ the rows that will actually be published. A throwing transform fails the
/ window like a failed fetch.
/ @private
stage_transform:{[worker;s]
    out:@[transform_batch[worker;];s`batch;{[e] (`transform_failed;e)}];
    if[(0h=type out) and `transform_failed~first out;
        :failed_with[s;"window failed transform";`transform`error!((s`cfg)`transform;last out)]];
    @[s;`batch;:;out]}

/ Private: the DATA QUALITY GATE, between transform and publish.
/ .
/ Before this existed the sequence was fetch, publish, record coverage as
/ complete - so a window of nulls, or one with every price at zero, was
/ recorded as covered and read as published forever. The ledger could record
/ a lie, and nothing anywhere would say so.
/ .
/ A failed check fails the window like a failed fetch. That is the behaviour
/ that makes a check safe to add to an existing worker - the worst case is
/ work redone, never data lost and never a gap silently marked complete. A
/ check that is not a function, or returns no table, still throws: that is
/ the worker's bug, not the window's data.
/ @private
stage_check:{[worker;s]
    bad:run_check[worker;s`batch];
    if[count bad;
        :failed_with[s;"window failed data quality";`failures`detail!(count bad;.qrender.full bad)]];
    s}

/ Private: PUBLISH the batch through finish_window, which owns the dry-run
/ gate and stages coverage.
/ .
/ The publish function is NILADIC by finish_window's contract, and a
/ fully-applied projection in q is a CALL rather than a deferred one - so the
/ batch goes through the worker's own `last_batch` global and the niladic
/ reads it. Building the argument any other way would publish before the
/ dry-run gate could suppress it. `last_window` is the window publish writes
/ into, for a strategy that acts on a range (`replace clears what the target
/ held inside it).
/ .
/ A publish that throws - an on_conflict `fail, a row the store refuses -
/ fails THIS window and the run goes on. It used to end the run. A write that
/ got partway is safe to repeat under the default `upsert, which is what
/ makes failing just the window sound.
/ @private
stage_publish:{[worker;s]
    cfg:s`cfg; w:s`window;
    write_state[worker;`last_batch;s`batch];
    write_state[worker;`last_window;`range_from`range_to!(w`range_from;w`range_to)];
    r:@[{[a] (1b;.qetl.job.bounded.runtime.finish_window . a)};
        (worker;cfg`dataset;cfg`partition;spec worker;w`range_from;w`range_to;publish_pending[worker]);
        {[e] (0b;e)}];
    if[not first r; :failed_with[s;"window failed to publish";enlist[`error]!enlist last r]];
    @[s;`result;:;last r]}

/ Private: the stages that can fail a window, in the order they run. Each is
/ {[worker;s]} over the window's state - `cfg, `window, and what the stages
/ before it added - and returns that state with its own result added, or
/ failed_with's failure. Defined after them, because it holds their values.
/ @private
window_stages:`fetch`transform`check`publish!(stage_fetch;stage_transform;stage_check;stage_publish)

/ Private: attach this window's metadata to the materialisation.
/ .
/ Two sources, deliberately separated:
/ .
/   framework facts   rows, source_version, dry_run - true of every
/                     materialisation, knowable without reading a single
/                     column, so no worker has to remember to record them.
/   declared facts    whatever the worker's optional `facts` function
/                     returns for this batch. Anything needing to know what
/                     a column MEANS lives here, because the framework
/                     cannot know it.
/ .
/ A failure in a worker's own facts function must not fail the window. The
/ rows are already published and the coverage already staged at this point,
/ so throwing here would turn a successful materialisation into a failed one
/ over a metadata bug - exactly backwards. The failure is logged instead, so
/ it is visible without being fatal.
/ .
/ The whole call is protected for the same reason begin_run is: run.q may not
/ be loaded under a minimal loader, and metadata is an addition rather than a
/ precondition.
/ @private
record_facts:{[worker;cfg;w;batch;r]
    framework:`rows`source_version`dry_run!
        (r`rows_published;(spec worker)`source_version;r`dry_run);
    declared:$[(::)~cfg`facts;
        ()!();
        @[{[f;b] f b}[cfg`facts;];batch;
          {[worker;w;e]
            .qetl.log.err[worker;"facts function failed";
                `range_from`range_to`error!(w`range_from;w`range_to;e)];
            ()!()}[worker;w]]];
    if[not 99h=type declared;
        .qetl.log.err[worker;"facts function returned a non-dictionary";
            `range_from`range_to!(w`range_from;w`range_to)];
        declared:()!()];
    / A dry run computes the facts - "what would this window have recorded"
    / is worth seeing - and records none of them.
    if[not .qetl.job.bounded.runtime.allows`record_facts;
        :.qetl.log.info[worker;"dry run - facts not recorded";
            `range_from`range_to`facts!(w`range_from;w`range_to;framework,declared)]];
    @[{[a] .qetl.run.record . a};
      (cfg`dataset;w`range_from;w`range_to;framework,declared);
      {[e] (::)}]}

/ Private: the new cursor, refusing any move that is not strictly forward.
/ .
/ It was asked whether backfill is strictly oldest-first, and whether the order
/ matters to correctness or only to observability. It is oldest-first by
/ construction - windows[] builds starts as from_ts+width*til n, and remaining
/ hands back ascending sub-ranges. The order used to matter to CORRECTNESS:
/ plan[] took this cursor as a hard lower bound, so a window processed out
/ of order pushed the cursor past windows still uncovered and "they simply
/ never got filled while that checkpoint stood". plan[] no longer does that
/ - coverage decides what is left, and a gap behind the cursor is planned -
/ so the consequence is gone. The invariant stays, because a cursor that
/ moves backwards or stands still would still mean a window was processed
/ out of the order the plan produced, which is a bug worth refusing loudly
/ rather than one the new plan[] happens to survive.
/ .
/ So the ordering was load-bearing and unenforced. .qetl.job.continuous.advance already
/ refuses a non-strictly-forward continuous cursor for a closely related
/ reason; this is the same invariant on the bounded path, which had a plain
/ assignment. One comparison, and the asymmetry is gone.
/ @throws error when the cursor would stand still or move backwards
/ @private
advanced_to:{[worker;current;next_cursor]
    if[(not null current) and not next_cursor>current;
        '"cursor for ",string[worker]," would move from ",string[current],
         " to ",string[next_cursor]," - plan uses it as a lower bound, so a ",
         "cursor that is not strictly forward skips windows that are still uncovered"];
    next_cursor}

/ Private: a NILADIC publisher for one worker's pending batch. A projection
/ with its last argument still missing, so finish_window's dry-run gate can
/ choose not to call it at all.
/ `unused`, not `_`: an underscore parameter makes the application throw
/ `'match`, because `_` is q's DROP/CUT operator rather than an ordinary
/ name. It parses and even projects without complaint, then fails only when
/ applied - so the first draft of this file loaded cleanly, planned windows
/ correctly, and failed on the first publish with a bare `'match` naming
/ nothing. Eighth reserved-name class here, and the first that is
/ punctuation rather than a word.
/ @private
publish_pending:{[worker] publish_last_batch[worker;]}

publish_last_batch:{[worker;unused] own[worker;`publish] read_state[worker;`last_batch]}

/ Release everything the worker holds. Safe on the failure branch too, since
/ release_lock is a no-op when not held.
cleanup:{[worker]
    h:read_state[worker;`handle];
    / The transport's `close`: hclose, the ODBC close, or for a local source's
    / directory nothing at all.
    closer:(.qetl.source.for_source (def worker)`source)`close;
    if[not null h; closer h; write_state[worker;`handle;0Ni]];
    .qetl.job.bounded.state.release_lock worker}

\d .
