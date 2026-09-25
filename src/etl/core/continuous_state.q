/ continuous_state.q - the continuous-worker poll-and-cursor pattern
/ (.qetl.job.continuous).
/ .
/ The continuous-worker pattern: "implement continuous workers as long-running
/ poll loops: load a local cursor at startup, publish a transformed page,
/ then advance and persist the cursor. Do not publish a resume-completion
/ claim merely because a continuous cursor advanced."
/ .
/ THE ASYMMETRY WITH BOUNDED WORKERS IS DELIBERATE
/ .
/ There is no registry here and no enforced contract, unlike
/ .qetl.job.bounded.state.bounded_workers. Bounded workers get both because the
/ thing being prevented is specific: "a bounded worker must not silently
/ become an unbounded tailer". A continuous worker is already unbounded, so
/ there is no such failure to prevent, and a registry with nothing to enforce
/ is ceremony. Issue #62 asks whether that should change; this
/ file deliberately does not pre-empt that answer by inventing a contract.
/ .
/ THE SENTENCE THIS FILE EXISTS TO ENFORCE
/ .
/ "Do not publish a resume-completion claim merely because a continuous
/ cursor advanced." A continuous cursor moving forward means "I have seen up
/ to here", NOT "everything up to here is published and complete". Those are
/ different claims, and conflating them is how a dataset gets declared
/ complete because a tailer happened to get far enough. So this file has NO
/ path to .qetl.coverage.stage_completion, and `advance` refuses a cursor that would
/ go backwards - the one way a tailer can silently re-publish.
/ .
/ Freshness is reported instead, which is the honest statement a consumer of
/ continuous output can actually use. That it is not a cross-worker contract
/ is #62's open question, and `freshness` says so at the point of use.

\d .qetl.job.continuous

/ ----------------------------------------------------------------- STATE

/ Where a continuous worker's cursor lives. Shares the status directory with
/ bounded workers' checkpoints, but the FILENAME differs (.cursor, not
/ .checkpoint) so the two can never be read for each other - a bounded
/ resume reading a tailer's cursor would skip history it never published.
cursor_path:{[worker] (.qetl.job.bounded.state.lock_dir[]),"/",string[worker],".cursor"}

/ Load the cursor at startup.
/ .
/ Returns 0Np when there is none, which a worker reads as "start from
/ whatever its own policy says" - typically now, or a configured lookback.
/ Deliberately NOT an error: a first run has no cursor, and treating that as
/ a failure would make every fresh deployment need manual seeding.
/ .
/ Unlike .qetl.job.bounded.state.load_checkpoint this takes no run specification, because a
/ continuous worker has no bounded run to compare against. That is the whole
/ structural difference between the two kinds of state, and it is why they
/ are separate functions rather than one with a flag.
load_cursor:{[worker]
    raw:@[{first read0 hsym `$x};cursor_path worker;{""}];
    if[0=count raw; :0Np];
    saved:@[{.j.k x};raw;{()!()}];
    if[not `cursor in key saved; :0Np];
    @[{"P"$x};saved`cursor;{0Np}]}

/ Persist the cursor AFTER the page it acknowledges has been published
/.
/ .
/ The ordering is the requirement. Persisting first and publishing second
/ means a crash between them loses the page for good: the cursor says it was
/ handled and nothing will ever fetch it again. Publishing first risks
/ re-publishing the page on restart, which retry-safe publication tolerates.
/ Under-claim over over-claim, exactly as bounded coverage does it.
save_cursor:{[worker;cursor]
    dir:.qetl.job.bounded.state.lock_dir[];
    system"mkdir -p ",dir;
    path:cursor_path worker;
    (hsym `$path) 0: enlist .j.j `cursor`saved_at!(cursor;.z.p);
    path}

/ Forget a feeder's saved cursor, so its next run starts from the source's
/ own beginning.
/ .
/ Deliberately separate from advance: losing a cursor is a decision, not a
/ side effect of moving one. Continuous output has no coverage ledger to
/ notice re-published pages, so this is destructive in a way the bounded
/ path's checkpoint is not.
/ @param worker the feeder's name, as a symbol
/ @return the worker's name
/ @eg .qetl.job.continuous.clear_cursor `fx_feed_2
clear_cursor:{[worker]
    system"rm -f ",cursor_path worker;
    cursor_path worker}

/ ---------------------------------------------------------------- ADVANCE

/ Advance a cursor, refusing to move it backwards.
/ .
/ A cursor going backwards is the one way a tailer silently re-publishes: it
/ re-reads a page it has already handled, and because continuous output has
/ no coverage ledger, nothing anywhere records that it happened twice. A
/ bounded worker is protected from the same mistake by its coverage
/ precheck; a continuous worker has only this.
/ .
/ Equal is also refused. A page that does not move the cursor means the poll
/ found nothing new, and calling advance for it would write the file on every
/ idle tick - turning a quiet source into constant disk churn, and making
/ `saved_at` useless as a signal of when progress last actually happened.
/ @throws error when the new cursor is not strictly ahead
advance:{[worker;current;next_cursor]
    if[null next_cursor;
        '"advance: refusing a null cursor for ",string[worker]," - a cursor that cannot be compared cannot be trusted to move forward"];
    if[(not null current) and not next_cursor>current;
        '"advance: refusing to move ",string[worker],"'s cursor from ",
         string[current]," to ",string[next_cursor],
         " - a continuous cursor that does not move strictly forward re-publishes pages, and continuous output has no coverage ledger to notice"];
    save_cursor[worker;next_cursor];
    next_cursor}

/ ------------------------------------------------------------- FRESHNESS

/ How current is this worker's output?
/ .
/ This is the honest statement a consumer of continuous output can make, and
/ it is deliberately NOT a completeness claim. "I have seen up to
/ 09:41" says nothing about whether everything before 09:41 is published -
/ only that the tailer has passed it.
/ .
/ Issue #62 asks whether continuous workers need a framework-level public
/ freshness contract. They do not have one: this is per-worker, there is no
/ cross-worker aggregate, and nothing consuming continuous output has a
/ sanctioned way to ask "is this fresh enough". Reporting the gap in the
/ return value beats leaving a caller to assume there is a guarantee.
/ @return dict of cursor, lag (now minus cursor) and a stated caveat
freshness:{[worker]
    c:load_cursor worker;
    `worker`cursor`lag`is_completeness_claim!
        (worker;c;$[null c; 0Nn; .z.p-c];0b)}

/ ----------------------------------------------------- DATASET FRESHNESS

/ worker -> the dataset it feeds (decided on #62).
/ .
/ The one dict #62 needed. It is the only registry continuous workers
/ have, and it is deliberately NOT a contract: registering says which
/ dataset a tailer feeds, nothing about how it must behave. #62's
/ "no enforced lifecycle for continuous workers" stands.
feeds:(`symbol$())!`symbol$()

/ Declare which dataset a continuous worker feeds.
/ @eg .qetl.job.continuous.register_feeder[`fx_feed_2;`quotes]
register_feeder:{[worker;dataset]
    feeds[worker]:dataset;
    worker}

feeders_of:{[dataset] key[feeds] where value[feeds]=dataset}

/ How current is a DATASET, across every worker that feeds it?
/ .
/ A dataset is only as fresh as its SLOWEST feeder. Three tailers with
/ cursors at 09:41, 09:41 and 09:12 mean the dataset is current to 09:12,
/ not 09:41 - anything after 09:12 may be missing the third feed's rows.
/ So the cursor reported is the MINIMUM over feeders and the lag the
/ MAXIMUM, and the laggard is NAMED, because "the dataset is 29 minutes
/ behind" is a symptom and "fx_feed_2 is 29 minutes behind" is a diagnosis.
/ .
/ A feeder with no cursor at all makes the dataset not-fresh, for the same
/ reason a single never-run worker is not fresh: a missing feed is the
/ worst possible lag, not a feed to ignore. Its cursor is reported as null
/ and it is the laggard.
/ .
/ Still not a completeness claim, and the payload says so. This aggregates
/ "seen up to here" across feeders; it does not say everything before that
/ point is published. That distinction is the continuous-worker pattern's, and it survives
/ aggregation unchanged.
/ @return dict of dataset, cursor (min), lag (max), laggard, feeders, and
/   is_completeness_claim (always 0b)
/ @throws error when no worker is registered as feeding the dataset - a
/   dataset with no declared feeder cannot have a freshness, and reporting
/   one would be the silent kind of wrong
dataset_freshness:{[dataset]
    ws:feeders_of dataset;
    if[0=count ws;
        '"dataset_freshness: no worker is registered as feeding ",string[dataset],
         " - call register_feeder first, because a freshness for an unfed dataset would be invented"];
    fs:freshness each ws;
    cursors:fs[;`cursor];
    / null sorts first under min? No - `min` ignores nulls. A null cursor
    / must WIN (it is the worst lag), so pick the laggard explicitly.
    laggard:$[any null cursors; first ws where null cursors; ws cursors?min cursors];
    c:$[any null cursors; 0Np; min cursors];
    `dataset`cursor`lag`laggard`feeders`is_completeness_claim!
        (dataset;c;$[null c; 0Nn; .z.p-c];laggard;ws;0b)}

/ Is a dataset within `tolerance` of now, across all its feeders?
dataset_is_fresh:{[dataset;tolerance]
    f:dataset_freshness dataset;
    $[null f`cursor; 0b; (f`lag)<=tolerance]}

/ Is this worker's output within `tolerance` of now?
/ .
/ A missing cursor is NOT fresh, and that matters: a worker that has never
/ run has no cursor, and defaulting the answer to true would report a
/ never-started tailer as up to date.
is_fresh:{[worker;tolerance]
    f:freshness worker;
    $[null f`cursor; 0b; (f`lag)<=tolerance]}

/ ------------------------------------------------------------------ POLL

/ One poll iteration: fetch a page, publish it, advance.
/ .
/ Returns a dict rather than looping, so the LOOP belongs to the worker's own
/ timer and this function stays testable without one. That follows the
/ pure/impure split: the page's transformation and the cursor arithmetic are
/ deterministic and unit-testable, while the timer that calls this lives in
/ the shell.
/ .
/ An empty page is a SUCCESS with state `idle, not a failure and not an
/ error. A tailer on a quiet source is working correctly, and an orchestrator
/ that cannot tell "nothing new" from "broken" alerts all night on a healthy
/ process.
/ @param worker the worker's name
/ @param fetch_page a unary function taking the current cursor and returning
/   a table - the page after that cursor
/ @param publish_page a unary function taking the page and returning a row
/   count
/ @param next_cursor a unary function taking the page and returning the
/   cursor that acknowledges it
/ @return dict of state (`idle or `published), rows and cursor
poll_once:{[worker;fetch_page;publish_page;next_cursor]
    current:load_cursor worker;
    page:fetch_page current;
    if[0=count page;
        :`state`rows`cursor!(`idle;0;current)];
    / publish, THEN advance. See save_cursor on why this order.
    rows:publish_page page;
    c:advance[worker;current;next_cursor page];
    `state`rows`cursor!(`published;rows;c)}

\d .
