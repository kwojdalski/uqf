/ attached_calendar.q - TorQ's own calendar for an attached backfill (#2193).
/ .
/ A TorQ stack names a row's partition .eodtime.getday of its UTC instant: shifted by the roll zone's
/ adjustment, less rolltimeoffset. src/ may not read TorQ, so the launcher scripts (attached_backfill.q,
/ remote_receiver.q, and the tests) load this: it fills the hooks .qetl.io.use_calendar calls. The
/ functions are TorQ's, loaded from the vendored tree over a timezone table that holds the two
/ adjustments the stack's writer_state reported - nothing here re-derives a day.

\d .

/ Load eodtime.q for a stack that rolls at `offset_ns` in a zone `adj_ns` from UTC.
/ @param offset_ns the stack's rolltimeoffset, nanoseconds
/ @param adj_ns the roll zone's adjustment, nanoseconds
/ @throws error when eodtime.q cannot be found (run from the repository root)
.qdev.calendar.adopt:{[offset_ns;adj_ns]
    f:"lib/torq/code/common/eodtime.q";
    if[()~key hsym `$f; '"calendar: ",f," not found - run from the repository root"];
    `.tz.t set ([] timezoneID:`uqf_roll`uqf_data; gmtDateTime:2#1970.01.01D00:00:00.000000000; adjustment:(`timespan$adj_ns;0D));
    `.eodtime.rolltimeoffset set `timespan$offset_ns;
    `.eodtime.rolltimezone set `uqf_roll;
    `.eodtime.datatimezone set `uqf_data;
    system "l ",f;
    (::)}

/ The stack's getday over a list of UTC instants: TorQ's own.
/ @param times a timestamp or a list of them
/ @return the date or dates
.qdev.calendar.day:{[times] .eodtime.getday times}

.qetl.io.calendar_adopter:.qdev.calendar.adopt
.qetl.io.native_day_fn:.qdev.calendar.day

\d .
