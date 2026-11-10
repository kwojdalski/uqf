/ markout_job.q - the declaration a markout streaming job hands to .qetl.job.stream.at_horizons (.qpipe.transform.markout_job, #2651).
/ .
/ A markout job is 26 keys of a horizon declaration. Every asset class used to write them as a positional list
/ under one key list: reordering one side, swapping two same-typed values (the two timespans, the two
/ symbols), or changing the list in only one of four files re-labelled a value rather than failing - a
/ same-length mismatch is not an error in q. So the declaration is built here, by NAME, in one place:
/ a job states only what differs, and the constants every markout job shares are written once.
/ .
/ SHARED: the 1s timer, source-stamped event and reference times, readiness
/ on both, the per-symbol history keyed by symbol and source, the (venue; fill_id) identity, the pinned-config
/ check, `hold on failure, and the shared reference buffer. A job that needs one of them different is not
/ declaring a markout job of this shape, and says so by calling at_horizons directly.
/ .
/ THE JOB STATES: procname, the tables it reads and publishes (events, reference, outputs), transform, horizon,
/ legs, lookback, max_age, remember, lateness, expire_after, keep, note, ephemeral (what a restart loses) and
/ on_start. The tables are stated, not defaulted, because uqs reads them from this text without running q
/ (python/uqs/src/uqs/model/declarations.py) to build the process table: a default would be a second copy of
/ the topology in Python, the drift that reader exists to prevent.

\d .

/ The keys of a horizon declaration a markout job states itself, in the order they are listed.
.qpipe.transform.markout_job.own:`procname`events`reference`transform`outputs`horizon`legs`lookback`max_age`remember`lateness`expire_after`keep`note`ephemeral`on_start

/ The keys of the declaration a markout job produces, in the order the jobs always declared them.
.qpipe.transform.markout_job.keys:`procname`events`reference`transform`outputs`horizon`period`event_time`reference_time`ready_on`legs`lookback`max_age`by`retain_by`identity`remember`lateness`expire_after`keep`note`ephemeral`on_start`check`on_fail`shared_reference

/ The values every markout job shares. Evaluated when it is read, so the pinned-config check is the one loaded by then.
/ @return the shared part of the declaration, by key
/ @eg key .qpipe.transform.markout_job.shared[]  ->  `period`event_time`reference_time`ready_on`by`retain_by`identity`check`on_fail`shared_reference
.qpipe.transform.markout_job.shared:{[]
    `period`event_time`reference_time`ready_on`by`retain_by`identity`check`on_fail`shared_reference!(
        0D00:00:01;`source_time;`source_time;`both;`sym;`sym`source;`venue`fill_id;
        .qpipe.transform.config_markout.check_pinned;`hold;1b)}

/ The horizon declaration of a markout job: what the job states, named, plus what every markout job shares.
/ The job passes (key; value) PAIRS, not a key list and a value list, so a value sits beside the name it is
/ for: there is no second list to fall out of step with the first.
/ @param name the job name, for the message that refuses a declaration
/ @param pairs a list of (key; value) pairs, one for each of .qpipe.transform.markout_job.own, in any order
/ @return the declaration, all of .qpipe.transform.markout_job.keys, for .qetl.job.stream.at_horizons
/ @throws error naming every key a markout job must state and did not, states twice, or states and does not own
/ @eg (.qpipe.transform.markout_job.decl[`demo;{(x;x)} each .qpipe.transform.markout_job.own])`ready_on  ->  `both
.qpipe.transform.markout_job.decl:{[name;pairs]
    who:"markout_job.decl: ",string name;
    if[not all 2=count each pairs; 'who," takes (key; value) pairs"];
    ks:first each pairs;
    if[not 11h=type ks; 'who," takes (key; value) pairs with a symbol key"];
    own:.qpipe.transform.markout_job.own;
    twice:where 1<count each group ks;
    if[count twice; 'who," states ",(", " sv string twice)," more than once"];
    absent:own except ks;
    if[count absent; 'who," does not state ",", " sv string absent];
    extra:ks except own;
    if[count extra; 'who," states ",(", " sv string extra)," - not a key a markout job owns; the shared ones are in markout_job.shared"];
    full:(.qpipe.transform.markout_job.shared[]),ks!last each pairs;
    .qpipe.transform.markout_job.keys#full}

\d .
