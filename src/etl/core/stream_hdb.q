/ stream_hdb.q - a restarted streaming job's read of the HDB (#2460).
/ .
/ The same namespace as stream_job.q, loaded straight after it.

\d .

/ ------------------------------------------------------- THE HDB, AT A RESTART
/ .
/ A job whose state is rebuilt by replaying the plant's log for the day knows
/ nothing from before the day's first message. Where an answer from before is
/ still part of the answer - the last mid of a pair that has not quoted today
/ - the job reads it from the HDB when it has replayed, in its `on_replayed`
/ (a restart is the only time it needs to: running, it already holds it).

/ The handle to the process that maps market_data (the HDB), as the runner
/ gives it. Niladic. The default is none, 0Ni: a process with no HDB (the
/ in-process plant of run_stream.q, a test) restarts as it always did. The TorQ
/ runner (scripts/processes/torq_stream.q) sets it from .qtorq; src/ never
/ calls the adapter. A function standing in for a handle (`{value x}`) counts.
/ @return a handle, a function standing in for one, or 0Ni for none
/ @eg .qetl.job.stream.hdb_handle[]  ->  0Ni
.qetl.job.stream.hdb_handle:{[] 0Ni}

/ The latest top of book of every (sym; source) strictly before an instant, from
/ the HDB, read where it is mapped (.qetl.mapped.last_tops): memory proportional
/ to the number of (sym; source) pairs, never to the market. No HDB reachable is
/ logged, with the reason, and answers none: the job then restarts knowing only
/ today's log, which is what it did before it read the HDB.
/ @param before the exclusive instant, usually when the plant's log began
/ @param lookback how far before it a book may be and still count
/ @return table sym, source, market, source_time, bid, ask, clock
/ @eg count .qetl.job.stream.hdb_tops[2026.10.10D00:00:00;7D]  ->  0
.qetl.job.stream.hdb_tops:{[before;lookback]
    none:0#([] sym:`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());
    r:@[{h:.qetl.job.stream.hdb_handle[];
         $[$[-6h=type h; not null h; .qetl.declared.callable h];
            (1b;.qetl.source.ipc_call[h;{[a] .qetl.mapped.last_tops a};enlist x]);
            (0b;"no HDB handle - this process has none")]};
        `before`read_from!(before;before-lookback);{(0b;x)}];
    if[not first r;
        .qetl.log.ch.warn[`lifecycle;"could not read the previous day's books from the HDB - the job knows only what today's log holds";
            (enlist[`before]!enlist before),enlist[`reason]!enlist last r];
        :none];
    last r}

\d .
