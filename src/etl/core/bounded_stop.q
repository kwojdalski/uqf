/ bounded_stop.q - a clean stop for a bounded run: a stop file, checked between windows (.qetl.job.bounded.stop, #2679, #2677).
/ .
/ A run cannot be stopped by a signal. KDB-X acts on SIGTERM only at q's idle loop, which a window
/ loop never reaches; before that it fails whatever system call is in flight with 'os, and SIGINT
/ raises 'stop inside whatever is being evaluated. Either lands in the middle of a window - a
/ failed window, or a lock half taken. So a stop is a REQUEST the run reads at the one place it
/ is safe to stop: the boundary between two windows.
/ .
/ The request is a file. Its path is derived from the run in one place, `path` below, and
/ python/uqs reads the rule from the facts generated from here (STOP_FILE_SUFFIX, and the
/ name `<worker>.<pid>` that `path` builds) - so the launcher and the lifecycle cannot
/ disagree about where it is:
/ .
/   <status_dir>/<worker>.<pid>.stop
/ .
/ beside the worker's own lock (`<worker>.lock`) and status file. It names the process, not just the
/ worker, so it is unambiguous: a request left behind for a run that has since ended names a pid
/ no later run has, and stops nothing. Anyone may create it - `touch` - for a run they can see
/ the pid of (the owner record, the lock, `uqs job backfill`'s own line).
/ .
/ What a stop does. Before each window the run looks for its file. If it is there, the run does
/ NOT start that window: every window it did start has finished or failed exactly as it would
/ have, so no window is recorded complete that did not complete, and the ledger holds exactly the
/ completed ones. The run then finishes its store as any run does (the HDB writer's sort and
/ attribute, the readers' reload), advances to the `stopped phase - the status file, the beat and
/ the run ledger all say `stopped - removes the file, and exits with `code`. Its locks are released
/ by `run`, as on every exit. A re-run plans what is not covered and does the rest.
/ .
/ Its own file because bounded_worker.q is held to its line budget (q-module-size).

\d .

/ The suffix of a stop file.
.qetl.job.bounded.stop.suffix:".stop"

/ The exit code of a run that stopped on request. Not 0 - the range is not done - and not 1, which
/ is a failure: a scheduler must be able to tell "an operator stopped it" from "it broke", and
/ the CLI keeps 2 for a usage error and 3 for rows written but not yet queryable.
.qetl.job.bounded.stop.code:4i

/ Where a run's stop file is: in the status directory, named for the worker and the process.
/ @param worker the worker's name
/ @param pid the process id of the run
/ @return the path, a string
/ @eg .qetl.job.bounded.stop.path[`demo_deals_backfill;1234i] like "*/demo_deals_backfill.1234.stop"  ->  1b
.qetl.job.bounded.stop.path:{[worker;pid] (.qetl.status.status_dir[]),"/",string[worker],".",string[pid],.qetl.job.bounded.stop.suffix}

/ Whether a stop has been asked of THIS process's run of a worker.
/ @param worker the worker's name
/ @return 1b when its stop file exists
/ @eg .qetl.job.bounded.stop.requested`no_such_worker  ->  0b
.qetl.job.bounded.stop.requested:{[worker] not ()~key hsym `$.qetl.job.bounded.stop.path[worker;.z.i]}

/ Remove this process's stop file for a worker; nothing to remove is not an error.
/ @param worker the worker's name
/ @return the worker
/ @eg .qetl.job.bounded.stop.clear`no_such_worker  ->  `no_such_worker
.qetl.job.bounded.stop.clear:{[worker]
    @[hdel;hsym `$.qetl.job.bounded.stop.path[worker;.z.i];{[e] (::)}];
    worker}

/ Why a run stopped, for the status file's error field and the run's log.
/ @param progress the run's progress counters (windows_completed, windows_failed)
/ @param planned how many windows the run planned
/ @return a sentence
/ @eg .qetl.job.bounded.stop.why[`windows_completed`windows_failed!2 0;5]  ->  "stopped on request after 2 of 5 window(s) - the rest are uncovered, so a re-run completes the range"
.qetl.job.bounded.stop.why:{[progress;planned]
    done:progress[`windows_completed]+progress`windows_failed;
    "stopped on request after ",string[done]," of ",string[planned]," window(s) - the rest are uncovered, so a re-run completes the range"}

/ Run a bounded worker's windows in order, looking for a stop request before each.
/ .
/ Only BETWEEN windows - never inside one - so a window is untouched by a stop: it completes or
/ fails as it would anyway. A stop found before the first window stops the run before it does
/ anything. The windows not reached are not planned again, not covered and not checkpointed.
/ @param worker the worker's name
/ @param windows the planned windows, each a row with range_from and range_to
/ @return 1b when the run stopped on request before the last window, 0b when it ran them all
/ @private
.qetl.job.bounded.run_windows:{[worker;windows]
    i:0;
    while[i<count windows;
        if[.qetl.job.bounded.stop.requested[worker];
            .qetl.log.ch.info[`lifecycle;"stop requested - not starting the next window";
                (enlist[`worker]!enlist worker),`stop_file`windows_done`windows_left!(`$.qetl.job.bounded.stop.path[worker;.z.i];i;count[windows]-i)];
            :1b];
        .qetl.job.bounded.do_window[worker] windows i;
        i+:1];
    0b}

\d .
