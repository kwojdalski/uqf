/ io_hdb_eod.q - the end-of-day fence on a backfill's HDB writes (#2186).
/ .
/ THE HAZARD. TorQ's end of day for D sorts D in the wdb's save directory and
/ then moves it into hdb/D with .wdb.movetohdb. If hdb/D already exists and ANY
/ table is in both places, movetohdb aborts the whole move: the HDB serves D
/ as empty tables and the real day stays stranded in the wdb. A backfill that
/ writes D before that move creates hdb/D, and its finish's .Q.chk fills it
/ with an empty copy of every table - so every table the wdb holds clashes.
/ "Before UTC today" (io_hdb.q's hdb_rows) is not the handover: yesterday is
/ writable from 00:00:00, while end of day is still sorting it.
/ .
/ THE SIGNAL is TorQ's own, the wdb's save directory ($KDBWDB, the wdb's
/ `savedir`). The wdb keeps D there until movetohdb has moved it out, and
/ opens the NEXT partition (idbreload[pt+1] -> initmissingtables) only after
/ the move. So a date d is handed over when the directory holds no d and does
/ hold a later partition; it is WAITING when d is still there, or when nothing
/ later has opened yet (end of day has not run). No clock rule: a wdb that is
/ late, slow or down keeps its dates waiting for exactly as long as it takes.
/ .
/ Registered per HDB root (watch_eod), by whatever knows the stack
/ (scripts/processes/torq_backfill.q). A root with no wdb registered, or whose
/ directory holds no partition at all (a fresh stack with nothing live to
/ move), has nothing to wait for. WHAT WOULD OVERTURN THAT: a wdb that only
/ writes at end of day, with no intraday save, leaves its directory empty
/ until the move - the fence would admit D then. TorQ's default saves
/ intraday, so the directory is populated; if a deployment turns that off, ask
/ the wdb (a readiness hook) rather than its directory.

\d .

/ HDB root (as a string) -> the wdb save directory (as a string) whose end of day feeds it.
.qetl.io.eod_wdb:(`symbol$())!()

/ Tell the writer which wdb directory hands partitions to this HDB root.
/ @param root the HDB root, a file symbol
/ @param wdb_dir the wdb's save directory, a file symbol
/ @return the registry
/ @eg .qetl.io.watch_eod[`:/no/such/hdb;`:/no/such/wdbhdb]
.qetl.io.watch_eod:{[root;wdb_dir]
    .qetl.io.eod_wdb[root]:enlist string wdb_dir;
    .qetl.io.eod_wdb}

/ Private: the dates of the partitions under a directory, as a date list.
/ @private
.qetl.io.part_dates:{[dir]
    k:key dir;
    $[(11h=type k); d:"D"$string k; d:`date$()];
    asc d where not null d}

/ Which of these dates has end of day not yet handed over to the HDB?
/ @param root the HDB root, a file symbol
/ @param dates a list of dates
/ @return those dates (before today) still waiting for end of day, ascending
/ @eg .qetl.io.eod_waiting[`:/no/such/hdb;2026.01.01 2026.01.02]  ->  `date$()
.qetl.io.eod_waiting:{[root;dates]
    dates:asc distinct d where (d:(),dates)<.z.d;
    if[not root in key .qetl.io.eod_wdb; :`date$()];
    p:.qetl.io.part_dates hsym `$first .qetl.io.eod_wdb root;
    if[0=count p; :`date$()];
    dates where (dates in p) or not dates<max p}

/ Refuse a write into a date end of day has not yet handed over.
/ @param root the HDB root, a file symbol
/ @param target the table, for the message
/ @param dates the dates the write would touch
/ @throws error naming the dates and the way out
/ @eg .qetl.io.require_eod_done[`:/no/such/hdb;`t;2026.01.01]
.qetl.io.require_eod_done:{[root;target;dates]
    w:.qetl.io.eod_waiting[root;dates];
    if[count w;
        '"hdb: ",string[target]," would write ",string[first w],
         " into the HDB before TorQ's end of day has moved that date there:",
         " its movetohdb would abort and the live day never reach the HDB.",
         " Backfill up to ",string[first[w]-1],", or run again once end of day has finished"];
    1b}

/ Refuse a write into any day that is not the HDB's yet: today and later
/ belong to the tickerplant, and yesterday and earlier are the HDB's only once
/ TorQ's end of day has moved them (require_eod_done). The one rule every HDB
/ writer shares: hdb_rows (backfills) and `uqs data replay` both call it.
/ @param root the HDB root, a file symbol
/ @param target the table (or other writer name), for the message
/ @param days the dates the write would touch
/ @return 1b when every day is handed over
/ @throws error naming the dates and the way out
/ @eg .qetl.io.require_handed_over[`:/no/such/hdb;`t;2026.01.01]
.qetl.io.require_handed_over:{[root;target;days]
    / Names the REMEDY, not only the refusal. The rule is a boundary - today
    / is the plant's, yesterday and earlier are the backfill's, and
    / end-of-day is the handover - but an operator meeting it for the first
    / time meets it as a wall, after the fetch and the quality gate have both
    / passed, which is a confusing place to learn a policy.
    days:(),days;
    if[any days>=.z.d;
        '"hdb: ",string[target]," has rows dated ",string[max days],
         " - today and later belong to the tickerplant and end-of-day, not a backfill.",
         " Backfill a range ending on or before ",string[.z.d-1],
         ", or let today's rows arrive through the live path"];
    .qetl.io.require_eod_done[root;target;distinct days]}

/ Which of these dates are the HDB's to write: before today, and end of day
/ has handed them over.
/ @param root the HDB root, a file symbol
/ @param dates a list of dates
/ @return those dates, ascending
/ @eg .qetl.io.handed_over[`:/no/such/hdb;2026.01.01 2026.01.02]  ->  2026.01.01 2026.01.02
.qetl.io.handed_over:{[root;dates]
    dates:asc distinct d where (d:(),dates)<.z.d;
    dates except .qetl.io.eod_waiting[root;dates]}

/ The days of a range that TorQ's end of day has not yet handed over to the
/ HDB a worker writes into (#2186). None when its io manager does not know
/ (memory, discard, an HDB with no wdb registered).
/ @param cfg the worker's configuration dictionary
/ @param range_from the inclusive start
/ @param range_to the exclusive end
/ @return the dates waiting for end of day, ascending
/ @eg .qetl.io.eod_waiting_for[()!();2026.09.10D00:00;2026.09.11D00:00]  ->  `date$()
.qetl.io.eod_waiting_for:{[cfg;range_from;range_to]
    m:.qetl.io.for_cfg cfg;
    if[not `eod_waiting in key m; :`date$()];
    / The manager's own day calendar when it has one (a scoped root's, #2193).
    f:$[`days_of in key m; m`days_of; {`date$x}];
    d:f range_from;
    m[`eod_waiting] d+til 0|1+(f range_to-1)-d}

/ Private: the first instant in [lo; hi) that the day calendar f names `day`
/ or later, or hi when none does. f must never decrease; a bisection, because
/ a native calendar is a function of an instant with no inverse to ask.
/ @private
.qetl.io.first_instant_of:{[f;lo;hi;day]
    if[day<=f lo; :lo];
    / f lo < day throughout, and f hi >= day or hi is the bound
    while[1<"j"$hi-lo;
        mid:lo+(hi-lo) div 2;
        $[day<=f mid; hi:mid; lo:mid]];
    hi}

/ Clip a plan's end to the first day end of day has not handed over, as
/ complete_through clips it to what the source can vouch for: the rest stays
/ uncovered, the next run plans it, and the clip is logged at WARN naming the
/ dates, so a plan never silently drops a day.
/ .
/ The day starts where the manager's own calendar says it does (#2397): a
/ native stack that rolls at 17:00 UTC starts day D at D-1 17:00, and a clip at
/ D's UTC midnight would leave the plan holding rows of D that the write then
/ refuses, failing the whole window. A partition boundary lies within a day of
/ its UTC midnight, so the search spans the two days around it.
/ @param worker the worker's name, for the log
/ @param cfg the worker's configuration dictionary
/ @param range_from the inclusive start
/ @param range_to the exclusive end
/ @return range_to, or the first instant of the first waiting day when that is earlier
/ @eg .qetl.io.clip_to_eod[`w;()!();2026.09.10D00:00;2026.09.11D00:00]  ->  2026.09.11D00:00:00.000000000
.qetl.io.clip_to_eod:{[worker;cfg;range_from;range_to]
    w:.qetl.io.eod_waiting_for[cfg;range_from;range_to];
    if[0=count w; :range_to];
    .qetl.log.ch.warn[`eod;"range clipped to the last day end of day has handed over - the rest stays uncovered for the next run";
        `worker`waiting_for_eod`requested_to!(worker;w;range_to)];
    m:.qetl.io.for_cfg cfg;
    f:$[`days_of in key m; m`days_of; {`date$x}];
    day:first w;
    lo:range_from|`timestamp$day-1;
    hi:range_to&`timestamp$day+1;
    .qetl.io.first_instant_of[f;lo;hi;day]}
