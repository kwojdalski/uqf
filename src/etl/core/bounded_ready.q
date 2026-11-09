/ bounded_ready.q - a bounded window's readiness stage (.qetl.job.bounded, #1927).
/ Split from bounded_worker.q to keep that file under its line cap; loaded just
/ BEFORE it, because bounded_worker.q's window_stages holds this stage's value
/ when it loads. Same namespace, unchanged: the stage calls failed_with, resolved
/ at call time.

\d .

/ Private: READINESS, between fetch and transform (#1927).
/ .
/ Asks the source whether this window can be scored: a window whose reference
/ data is absent must not be scored and then claimed. A source says what is
/ unmet through its `ready` hook; a window with anything unmet fails here, so
/ nothing is published and no coverage is staged, the run ends partial naming
/ what was missing, and the next run plans the window again. A hook that
/ throws, or returns something other than a list of strings, fails the
/ window too: a gate that cannot answer must not say yes.
/ @private
.qetl.job.bounded.stage_ready:{[worker;s] / noqa: QF016 every stage takes (worker;state)
    w:s`window;
    src:.qetl.source.def (s`cfg)`source;
    r:@[{[a] (1b;a[0][a 1;a 2;a 3])};(src`ready;s`batch;w`range_from;w`range_to);{[e] (0b;e)}];
    if[not first r;
        :.qetl.job.bounded.failed_with[s;"window failed its readiness check";enlist[`error]!enlist last r]];
    unmet:last r;
    / A string is a list of chars, so "x" would read as a list of one thing; only a
    / general list of strings (or the empty list) is an answer.
    well_formed:(0h=type unmet) and all 10h=type each unmet;
    if[not well_formed;
        :.qetl.job.bounded.failed_with[s;"window failed its readiness check";
            enlist[`error]!enlist "ready must return a list of strings, one per unmet requirement, or ()"]];
    if[count unmet;
        :.qetl.job.bounded.failed_with[s;"window not ready - nothing published or claimed, the next run plans it again";enlist[`unmet]!enlist unmet]];
    s}

/ Private: one window through every stage of window_stages before publish -
/ what a run would publish, without publishing it. Read from window_stages
/ rather than listed, so a stage added there is not one these callers skip:
/ verify capture and the reaction replay each left readiness out once (#2398,
/ #2399). Here rather than in bounded_worker.q, which is at its line cap;
/ window_stages is read at call time.
/ @param worker the worker's name
/ @param cfg the worker's configuration dictionary
/ @param w the window, a dict of range_from and range_to
/ @return the window's state: `batch when every stage passed, `failed (failed_with's dict) when one did not
/ @private
.qetl.job.bounded.stages_before_publish:{[worker;cfg;w]
    stages:value (enlist `publish) _ .qetl.job.bounded.window_stages;
    {[worker;s;stage] $[`failed in key s; s; stage[worker;s]]}[worker]/[(``cfg`window)!(::;cfg;w);stages]}

\d .
