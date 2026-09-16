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
    `ns`source`dataset`width!(`.qevbf;`demo_events;`event_tape;0D01:00:00)];
