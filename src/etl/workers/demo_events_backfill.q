/ demo_events_backfill.q - the event-tape bounded worker (.qevbf).
/ .
/ A declaration over the generic shell (#124). This is the file that made
/ #124 worth doing: written against the old copy-the-glue pattern it would
/ have been 238 lines, 234 of them duplicated from demo_deals_backfill.q and
/ needing hand-maintenance every time the framework moved.
/ .
/ Window width is 0D01:00:00, not demo_deals' 1D, because an event tape is
/ far denser than a deal feed - an hour of events is a comparable amount of
/ work to a day of deals, and a window that takes an hour of wall-clock to
/ fetch is a window whose failure costs an hour.

\d .qevbf

worker_name:`demo_events_backfill

/ --- the contract's required globals (ETL-01, ETL-02) --------------------

/ Declared here, not in the shell: require_contract checks for them in THIS
/ namespace, and moving them would make the check inspect the shell and pass
/ vacuously for every worker. Written by .qbw.init.
source_version:`;
range_from:0Np;
range_to:0Np;

handle:0Ni;

/ Per-worker accumulators - one set each, or two workers in one process
/ would share their running totals.
progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ Materialisation metadata for one window (gap 2.3).
/ .
/ The framework already records rows, source_version and dry_run, because it
/ can know those without reading a column. Everything here needs to know what
/ a column MEANS, which is exactly why the hook is the worker's and not the
/ shell's:
/ .
/   event_span      min and max event time actually present. A window is
/                   [from;to) by declaration; this is what ARRIVED in it, and
/                   the two differing is the first sign of a source whose
/                   clock or timezone is not what the declaration assumes.
/   distinct_syms   how many instruments the window touched - a sudden drop
/                   is a partial extract that still published rows, which a
/                   row count alone reads as success.
/   trade_events    the terminal-event share. `event_tape` mixes adds with
/                   cancels and trades, so rows alone say nothing about how
/                   much of the window is actual execution.
/ .
/ An empty batch is legal (ETL-07 records a zero-row window deliberately), so
/ every aggregate here must survive it - hence the count guard rather than
/ min/max over an empty column, which would yield infinities and record them
/ as though they were observations.
facts:{[batch]
    if[0=count batch; :(enlist `event_span)!enlist "empty window"];
    `event_span`distinct_syms`trade_events!
        ((string min batch`time),"/",string max batch`time;
         count distinct batch`sym;
         sum `trade=batch`action)}

/ --- the contract's required methods, delegated -------------------------

/ Ordinary names in this namespace that happen to delegate. A worker needing
/ a genuinely different publish path defines its own here and the shell does
/ not object - .qbw is a default, not an owner.
spec:{[] .qbw.spec worker_name}
init:{[run_spec] .qbw.init[worker_name;run_spec]}
plan:{[cursor] .qbw.plan[worker_name;cursor]}
fetch:{[from_ts;to_ts] .qbw.fetch[worker_name;from_ts;to_ts]}
publish:{[batch] .qbw.publish[worker_name;batch]}
checkpoint:{[cursor] .qbw.checkpoint[worker_name;cursor]}
run:{[] .qbw.run worker_name}
cleanup:{[] .qbw.cleanup worker_name}

\d .

.qbw.define[`demo_events_backfill;
    `ns`source`dataset`width`facts!
        (`.qevbf;`demo_events;`event_tape;0D01:00:00;.qevbf.facts)];
