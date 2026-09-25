/ demo_events_backfill.q - the event-tape bounded worker (.qwrk.demo_events_backfill).
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

\d .qwrk.demo_events_backfill

/ Materialisation metadata for one window.
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
/ An empty batch is legal (coverage records a zero-row window deliberately), so
/ every aggregate here must survive it - hence the count guard rather than
/ min/max over an empty column, which would yield infinities and record them
/ as though they were observations.
facts:{[batch]
    if[0=count batch; :(enlist `event_span)!enlist "empty window"];
    `event_span`distinct_syms`trade_events!
        ((string min batch`time),"/",string max batch`time;
         count distinct batch`sym;
         sum `trade=batch`action)}

\d .

/ Pass-through, like demo_deals_backfill: the tape is published as fetched,
/ and the example is the source's own hand-written fixture.
.qxf.passthrough[`demo_events_passthrough;`batch;0#.qfeed.demo_events.fixture[];.qfeed.demo_events.fixture[]];

.qbw.define[`demo_events_backfill;
    `source`dataset`width`transform`facts`procname`note!
        (`demo_events;`event_tape;0D01:00:00;`demo_events_passthrough;.qwrk.demo_events_backfill.facts;
         `events_backfill1;
         "bounded: see deals_backfill1")];
