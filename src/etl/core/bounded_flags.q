/ bounded_flags.q - a bounded run's command line, parsed once (.qetl.job.bounded).
/ Split from bounded_worker.q to keep that file under its line cap (#1865);
/ loaded straight after it. Same namespace, unchanged: version_from_flags calls
/ default_version, resolved at call time.

\d .

/ ------------------------------------------------- A RUN FROM A COMMAND LINE
/ .
/ ONE PARSER FOR EVERY LAUNCHER. scripts/processes/torq_backfill.q (the fleet
/ path, `uqs job backfill`) and scripts/jobs/run_backfill.q (the TorQ-free dev
/ path) each kept their own, and they drifted: the dev one refused a run with
/ no -version while the fleet one fell back to the worker's declared default,
/ so a command line did not move between them unchanged, as run_backfill.q
/ promised it would. Both load the ETL tree, so both call this.

/ The flags a run cannot do without. -version is not among them: a worker
/ that declares a default source_version runs under it (version_from_flags).
.qetl.job.bounded.required_flags:`worker`from`to

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
.qetl.job.bounded.require_flags:{[opts]
    missing:.qetl.job.bounded.required_flags where not (.qetl.job.bounded.required_flags in key opts) and 0<count each opts .qetl.job.bounded.required_flags;
    if[count missing;
        '"backfill: missing ",(", " sv "-",/:string missing),
         " - a backfill with no range would publish the wrong window and record it as covered"];
    .qetl.job.bounded.required_flags!first each opts .qetl.job.bounded.required_flags}

/ The source_version a run records coverage under: -version when given,
/ else the worker's declared default, else a refusal - a worker that declares
/ none is one whose source can be restated, and guessing the release there
/ files a restatement under the old one. A -version given with no value
/ (`-version $V` with V unset) is refused like an empty -from (#2405): only an
/ absent -version falls back to the default.
/ @param opts the parsed command line, as .Q.opt returns it
/ @param worker the worker's name
/ @return the version, a symbol
/ @throws error when neither is there, or -version is given with no value
.qetl.job.bounded.version_from_flags:{[opts;worker]
    if[`version in key opts;
        v:$[count opts`version; `$first opts`version; `];
        if[null v;
            '"backfill: -version is given with no value - name the release of the source the run records coverage under, or leave -version out for the worker's default"];
        :v];
    dv:.qetl.job.bounded.default_version worker;
    if[null dv;
        '"backfill: missing -version - ",string[worker]," declares no default source_version, so a run must say which release of the source it records coverage under"];
    dv}

/ The worker and run specification a command line names, parsed and typed.
/ An empty or reversed range is refused here, before anything is started,
/ rather than at the worker's own validation after init.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return a dict of worker and spec (source_version, range_from, range_to)
/ @throws error when a flag is missing, a bound is not a q timestamp, or -from is not before -to
.qetl.job.bounded.spec_from_flags:{[opts]
    f:.qetl.job.bounded.require_flags opts;
    from_ts:"P"$f`from;
    to_ts:"P"$f`to;
    if[null from_ts; '"backfill: -from is not a timestamp: ",f`from];
    if[null to_ts;   '"backfill: -to is not a timestamp: ",f`to];
    if[to_ts<=from_ts; '"backfill: -from is not before -to"];
    worker:`$f`worker;
    `worker`spec!(worker;
        `source_version`range_from`range_to!(.qetl.job.bounded.version_from_flags[opts;worker];from_ts;to_ts))}
