/ bounded_once.q - --once for a bounded worker: one planned window (.qetl.job.bounded.once, #2798).
/ .
/ A bounded run plans every uncovered window in its range and works through them. Under --once
/ it works through the FIRST of them and stops, and the others stay uncovered, so the next run
/ plans them again. The window itself is untouched: do_window still validates, publishes,
/ checkpoints and records coverage in that order, and a window that fails fails as it always
/ did. Rate and resource limits are the worker's own and are not relaxed - a single window
/ is the same size a window always is.
/ .
/ WHO ENDS THE RUN. `limit`, called by run_body at the one place the plan is known, and nothing
/ else: the runner owns termination, so no worker declares a one-shot loop of its own.
/ .
/ ONE RUN, NOT THE PROCESS. The request names a worker and is CONSUMED by the first run of it
/ that reaches `limit`, so a run a reaction starts inside the publisher's window (react.q's
/ on_worker) is a fresh invocation and is never cut to one window: "one invocation under its
/ parent's execution" is that reaction running, whole, once.
/ .
/ The outcome is derived from the run's terminal state, in one place (`outcome`), and the exit
/ code from the outcome (.qetl.once.codes) - so torq_backfill.q and the TorQ-free runner agree.

\d .

/ The worker whose NEXT run is a one-window run, ` when none is.
.qetl.job.bounded.once.target:`

/ Ask for the next run of a worker to be one window.
/ @param worker the worker's name
/ @return the worker
/ @eg .qetl.job.bounded.once.request[`demo_deals_backfill]; .qetl.job.bounded.once.withdraw[]  ->  `demo_deals_backfill
.qetl.job.bounded.once.request:{[worker] `.qetl.job.bounded.once.target set worker; worker}

/ Withdraw a request that was never used.
/ @return the worker that was asked for, or `
/ @eg .qetl.job.bounded.once.withdraw[]  ->  `
.qetl.job.bounded.once.withdraw:{[] w:.qetl.job.bounded.once.target; `.qetl.job.bounded.once.target set `; w}

/ The windows a run will do: all of them, or - when this worker's run was asked to be one window
/ - the first. Consumes the request either way a run reaches it.
/ @param worker the worker's name
/ @param windows the planned windows, a table
/ @return the windows to run
/ @eg count .qetl.job.bounded.once.limit[`no_such_worker;([] range_from:2#2026.09.13D00:00:00; range_to:2#2026.09.14D00:00:00)]  ->  2
.qetl.job.bounded.once.limit:{[worker;windows]
    if[not worker~.qetl.job.bounded.once.target; :windows];
    .qetl.job.bounded.once.withdraw[];
    if[0=count windows; :windows];
    .qetl.log.ch.info[`lifecycle;"once: running the first planned window only - the rest stay uncovered for the next run";
        (enlist[`worker]!enlist worker),`planned`left!(count windows;count[windows]-1)];
    1#windows}

/ The --once outcome of a bounded run's terminal state.
/ @param state the run's state: completed, idle, partial, failed or stopped
/ @return `done for a window that ran, `no_work when every window was already covered, `stopped
/ for an operator's stop, and `failed for anything else
/ @eg .qetl.job.bounded.once.outcome `idle  ->  `no_work
.qetl.job.bounded.once.outcome:{[state] $[state~`completed; `done; state~`idle; `no_work; state~`stopped; `stopped; `failed]}

/ End a --once bounded run: write its outcome and leave with the code (.qetl.once.conclude).
/ @param ctx a .qetl.once context
/ @param result what the worker's run returned: state, and when the run got that far
/ windows_completed and rows_published; a failure carries `error
/ @return the outcome document
.qetl.job.bounded.once.conclude:{[ctx;result]
    o:.qetl.job.bounded.once.outcome[result`state];
    reason:$[o~`no_work; "all_covered"; o~`done; ""; "the run ended ",string result`state];
    n:{[r;k] $[k in key r; `long$r k; 0]}[result];
    detail:$[`error in key result; $[10h=type e:result`error; e; -3!e]; ""];
    .qetl.once.conclude[ctx;o;reason;n`windows_completed;n`rows_published;detail]}

\d .
