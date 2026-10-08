/ stream_poll.q - polling feeds, and previewing one without running it
/ (.qetl.job.stream, beside stream_job.q).
/ .
/ A polling feed fetches a page from outside the stack, normalizes it,
/ publishes it and advances a cursor - all in one timer tick. Written as one
/ on_timer lambda, those steps cannot be run apart: the only way to see what
/ a feed would publish was to let it publish, and advance its cursor (#663).
/ .
/ So a feed may declare `poll` instead of `on_timer`: its steps as separate
/ functions. `define` builds the timer from them - `tick`, which checks the
/ cursor advances, publishes, and THEN saves it - and `preview` runs the same
/ fetch, normalize and advance check with neither the publish nor the save.
/ They are not a muted copy of the timer: preview never calls publish, never
/ saves a cursor,
/ and runs in a process (`uqs stream preview`) whose publish is the unwired
/ stub, so a publish reached any other way throws rather than lands.
/ .
/   poll    dict of
/     fetch        [cursor] -> the page after `cursor`, a table. The cursor
/                  is null on a first run; what that means is the feed's own
/                  policy, as for any continuous worker
/     normalize    [page] -> what is published: a table when the feed
/                  publishes one table, else a dictionary of table -> rows
/     next_cursor  [page] -> the cursor that acknowledges the page
/     source       optional symbol, a registered .qetl.source: lets preview
/                  say whether the page is live (its credential is set) or
/                  the fixture
/     cursor       optional symbol naming the cursor file, default the job
/     close        optional niladic, releasing whatever fetch opened; preview
/                  calls it on success and on failure
/     load, save, advances
/                  optional, all three or none: the cursor's own handling,
/                  for a cursor that is not a timestamp (#666). load[name]
/                  -> the saved cursor; save[name;cursor] after a publish;
/                  advances[current;proposed] -> 1b when proposed is
/                  strictly ahead. Absent, the cursor is a timestamp, kept
/                  by .qetl.job.continuous.load_cursor/save_cursor. For a
/                  compound cursor - (time, securityId, priceBookType) - the
/                  stock answer is .qetl.job.continuous.load_cursor_value,
/                  save_cursor_value and lexically_after[fields]
/     start_cursor optional [instant] -> the cursor positioned just before
/                  `instant`: fetching after it returns the rows at or after
/                  it. What `uqs stream preview --last` starts from (#681).
/                  A timestamp cursor needs none - it is instant minus 1ns -
/                  but a feed with its own load/save/advances must declare
/                  one, because only the feed knows its tie-breakers
/     page_limit   optional positive long, the most rows fetch returns in one
/                  page - reported by a recent-data preview, so a window
/                  holding more than one page says it was cut short
/ .
/ RECENT-DATA PREVIEW (preview_recent, #681). The saved cursor can be far
/ behind, and the feed's first-run lookback wider than a troubleshooter
/ wants. preview_recent takes a duration instead: it captures one UTC
/ instant `to`, asks the feed for the cursor before `to - duration` and
/ fetches after it, and keeps the rows in [from, to) - judged row by row
/ with the feed's own next_cursor and advances, so a compound cursor's
/ tie-breakers decide the edges exactly as they decide a run's progress.
/ The saved cursor is never read, let alone written, and the live poll is
/ not changed: the window lives only in the preview.
/ .
/ Subscribers are not previewable here: their input arrives from the plant,
/ so there is no page to fetch. Previewing one would need a supplied batch or
/ a replay with isolated state, which is a different feature.

\d .qetl.job.stream

/ What a poll declaration must carry.
poll_keys:`fetch`normalize`next_cursor

/ Private: check a declaration's `poll` and give it the timer built from it.
/ Called by `define` before it checks the timer pair.
/ @param job the job's name
/ @param decl the declaration, carrying `poll`
/ @return the declaration with its generated on_timer
/ @throws error naming what is wrong with the poll declaration
/ @private
poll_declared:{[job;decl]
    p:decl`poll;
    if[not 99h=type p;
        '"define: ",string[job],"'s poll must be a dictionary of fetch, normalize and next_cursor"];
    if[count missing:poll_keys where not poll_keys in key p;
        '"define: ",string[job],"'s poll is missing ",", " sv string missing];
    / `(enlist `close) inter`, never `` `close inter ``: inter indexes its left
    / argument, and an atom cannot be indexed - 'type at every define.
    fns:poll_keys,`close`start_cursor inter key p;
    if[count bad:fns where not is_callable each p fns;
        '"define: ",string[job],"'s poll ",(", " sv string bad)," must be functions"];
    custom:`load`save`advances inter key p;
    if[(count custom) and 3<>count custom;
        '"define: ",string[job],"'s poll declares ",(", " sv string custom),
         " - a custom cursor needs load, save and advances together, or none of them for a timestamp"];
    if[count bad:custom where not is_callable each p custom;
        '"define: ",string[job],"'s poll ",(", " sv string bad)," must be functions"];
    if[count bad:(`source`cursor inter key p) where not -11h=type each p `source`cursor inter key p;
        '"define: ",string[job],"'s poll ",(", " sv string bad)," must be symbols"];
    if[`page_limit in key p;
        if[not $[-7h=type p`page_limit; 0<p`page_limit; 0b];
            '"define: ",string[job],"'s poll page_limit must be a positive long - the most rows one fetch returns"]];
    if[count decl`subscribe_to;
        '"define: ",string[job]," declares poll and subscribes to ",(", " sv string decl`subscribe_to),
         " - a polling job fetches its own input; a job fed by the plant declares on_batch"];
    if[`on_timer in key decl;
        '"define: ",string[job]," declares both poll and on_timer - a polling job's timer is built from poll"];
    if[not `period in key decl;
        '"define: ",string[job]," declares poll without period - how often to poll"];
    decl[`on_timer]:{[job;unused] tick job}[job];
    decl}

/ The cursor file a polling job advances.
/ @param job the job's name
/ @return the cursor's name, as .qetl.job.continuous names cursors
cursor_name:{[job] p:(def job)`poll; $[`cursor in key p; p`cursor; job]}

/ Private: normalize's result as table -> rows, refusing a table the job
/ does not declare it publishes.
/ @param job the job's name
/ @param out what normalize returned
/ @return dict of published table -> rows
/ @throws error when the result is neither a table nor such a dictionary
/ @private
outputs:{[job;out]
    pubs:(def job)`publishes;
    if[.Q.qt out;
        if[1<>count pubs;
            '"normalize: ",string[job]," returned one table but publishes ",(", " sv string pubs)," - return a dictionary of table -> rows"];
        :(enlist first pubs)!enlist out];
    if[not 99h=type out;
        '"normalize: ",string[job]," must return a table, or a dictionary of published table -> rows"];
    if[count bad:(key out) except pubs;
        '"normalize: ",string[job]," returned ",(", " sv string bad),", which it does not declare in publishes"];
    out}

/ Private: the timestamp cursor's advance rule, .qetl.job.continuous.advance's.
/ @private
default_advances:{[current;proposed] (not null proposed) and (null current) or proposed>current}

/ How a polling job keeps its cursor: its own load, save and advances, or the
/ timestamp defaults. The one place both the timer and the preview get them,
/ so the two cannot disagree about what counts as progress.
/ @param job the job's name
/ @return dict of load [name], save [name;cursor] and advances [current;proposed]
cursor_ops:{[job]
    p:(def job)`poll;
    $[`load in key p;
      `load`save`advances#p;
      `load`save`advances!(.qetl.job.continuous.load_cursor;.qetl.job.continuous.save_cursor;default_advances)]}

/ One poll: fetch the page after the cursor, normalize it, check the cursor
/ it would move to, publish every table, then save that cursor. What a
/ polling job's generated on_timer calls.
/ .
/ The advance is checked BEFORE publishing: a cursor that would not move is a
/ page the next tick fetches again, so publishing it now would publish it
/ twice. The save comes after: a publish that throws leaves the cursor where
/ it was and the page is fetched again, rather than skipped.
/ @param job the job's name
/ @return dict of state (`idle or `published), rows and cursor
/ @throws error when the page does not fit its plant tables (.qetl.plant.problems),
/   the proposed cursor does not advance, or a step throws
tick:{[job]
    d:def job;
    p:d`poll;
    / A deployment's --live (#800): a feed whose source has no credential
    / publishes nothing, rather than its fixture.
    if[`fixture~liveness job; .qetl.source.refuse_fixture["tick: ",string job;p`source]];
    ops:cursor_ops job;
    name:cursor_name job;
    current:ops[`load] name;
    / Every line the fetch logs - a traced query above all - carries the job
    / and the cursor it fetched after.
    page:.qetl.log.with_context[`job`cursor!(job;current);p`fetch;enlist current];
    if[0=count page; :`state`rows`cursor!(`idle;0;current)];
    out:outputs[job;p[`normalize] page];
    / Checked before anything is published or saved: a page the plant would
    / mis-store is refused whole, so no table of it lands and the cursor stays
    / where it was - the page is fetched again once the job is fixed.
    if[count failures:raze contract_failures'[key out;value out];
        '"tick: ",string[job]," would publish what the plant does not take - ","; " sv failures];
    proposed:p[`next_cursor] page;
    if[not ops[`advances][current;proposed];
        '"tick: ",string[job],"'s next cursor ",(-3!proposed)," does not move past ",(-3!current),
         " - publishing it would publish the page again next tick"];
    pub:get ` sv (d`ns),`publish;
    {[pub;t;rows] pub[t;rows]}[pub]'[key out;value out];
    ops[`save][name;proposed];
    `state`rows`cursor!(`published;sum count each value out;proposed)}

/ Private: what is wrong with `rows` for plant table `t` - the plant's own
/ contract, .qetl.plant.problems, so the preview and the timer hold a page to
/ the same rules and the plant's nested element types are declared once. A
/ table the plant does not carry has nothing to compare against.
/ @return a list of messages, empty when it matches
/ @private
contract_failures:{[t;rows] $[t in .qetl.plant.names[]; .qetl.plant.problems[t;rows]; ()]}

/ Private: whether a polling job's page is live data, the fixture, or
/ unknown - known only when its poll names the source it reads.
/ @private
liveness:{[job]
    p:(def job)`poll;
    $[not `source in key p; `unknown; .qetl.source.has_credentials p`source; `live; `fixture]}

/ Preview one page of a polling job: what it would fetch, publish and advance
/ to, with nothing published and nothing saved.
/ .
/ Reads the job's cursor and never writes it; fetches through the job's own
/ fetch, so a live preview really queries the source; normalizes; and holds
/ each table against the plant's. A missing credential or a failing
/ conversion is the job's own error, raised - after `close`, which runs
/ either way.
/ @param job the job's name
/ @param n how many rows of each table to return as a sample
/ @return dict of job, mode (`next_page), state (`previewed, `idle or
/   `invalid), live, cursor, fetched, next_cursor, advances, rows and sample
/   (each table -> ...), and failures (messages; empty unless `invalid)
/ @throws error when the job is not a polling feed, or its fetch, normalize
/   or next_cursor throws
preview:{[job;n] run_preview[job;n;::]}

/ Preview the RECENT data of a polling job: the rows in [now - span, now),
/ fetched from a temporary cursor, with nothing published and nothing saved.
/ .
/ For troubleshooting a feed whose saved cursor or first-run lookback covers
/ more than is wanted. `now` is one UTC instant, captured once. The start
/ comes from the feed's start_cursor (instant minus 1ns for a timestamp
/ cursor); rows past the window's end are dropped by the feed's own
/ next_cursor and advances. The saved cursor is not read and the live poll is
/ not changed - this is a sample of recent data, not the job's next page.
/ @param job the job's name
/ @param n how many rows of each table to return as a sample
/ @param span the window's length, a positive timespan
/ @return preview's dict with mode `recent, cursor the temporary start
/   cursor, and window (from, to, start_cursor, end_cursor), kept (rows in
/   the window), page_limit and limited (the page was full, so the window
/   may hold rows the sample did not reach)
/ @throws error for a span that is not a positive timespan, a feed with its
/   own cursor and no start_cursor, or anything preview throws
preview_recent:{[job;n;span]
    if[not -16h=type span;
        '"preview_recent: span must be a timespan, e.g. 0D00:00:30 - got ",.Q.s1 span];
    if[not span>0D; '"preview_recent: span must be positive - got ",string span];
    run_preview[job;n;span]}

/ Private: preview's guard, the page, and `close` either way.
/ @private
run_preview:{[job;n;span]
    d:def job;
    if[count d`subscribe_to;
        '"preview: ",string[job]," subscribes to ",(", " sv string d`subscribe_to),
         " - its input arrives from the plant, so there is no page to fetch. Preview supports polling feeds"];
    if[not `poll in key d;
        '"preview: ",string[job]," declares no poll - its on_timer is one function, and running it would publish and advance. Declare poll (fetch, normalize, next_cursor) to preview it"];
    r:.[preview_page;(job;n;span);{[e] (`preview_failed;e)}];
    if[`close in key d`poll; @[(d`poll)`close;::;{[e] ::}]];
    if[(0h=type r) and `preview_failed~first r; 'last r];
    r}

/ Private: the [instant] -> cursor function a recent-data preview starts
/ from: the feed's start_cursor, or instant minus 1ns for a timestamp cursor.
/ @throws error for a feed with its own cursor and no start_cursor
/ @private
start_cursor_of:{[job]
    p:(def job)`poll;
    if[`start_cursor in key p; :p`start_cursor];
    if[`load in key p;
        '"preview: ",string[job]," keeps its own cursor (load, save, advances) and declares no start_cursor - ",
         "declare poll`start_cursor [instant] -> the cursor just before instant"];
    {[instant] instant-1}}

/ Private: the window a recent-data preview covers, from one captured instant.
/ @private
recent_window:{[job;span]
    / `from` is a qSQL keyword, so neither bound is named after its key
    window_end:.z.p;
    window_start:window_end-span;
    start:start_cursor_of job;
    `from`to`start_cursor`end_cursor!(window_start;window_end;start window_start;start window_end)}

/ Private: which rows of `page` lie in the window: after its start cursor and
/ not after its end cursor, each row's cursor being next_cursor of that row
/ alone, compared with the feed's own advances.
/ @private
in_window:{[job;window;page]
    p:(def job)`poll;
    ops:cursor_ops job;
    row_cursor:{[next_cursor;page;i] next_cursor enlist page i}[p`next_cursor;page;] each til count page;
    after_start:ops[`advances][window`start_cursor;] each row_cursor;
    after_end:ops[`advances][window`end_cursor;] each row_cursor;
    after_start and not after_end}

/ Private: preview's body, between the guard and `close`. `span` is (::)
/ for the next page after the saved cursor, else the recent window's length.
/ @private
preview_page:{[job;n;span]
    p:(def job)`poll;
    ops:cursor_ops job;
    recent:not span~(::);
    window:$[recent; recent_window[job;span]; (::)];
    current:$[recent; window`start_cursor; ops[`load] cursor_name job];
    / Every line the fetch logs - a traced query above all - carries the job
    / and the cursor it fetched after; a recent preview's, its window and
    / page limit too.
    limit:$[`page_limit in key p; p`page_limit; 0N];
    context:`job`cursor!(job;current);
    if[recent; context,:`window`page_limit!(window`from`to;limit)];
    fetched:.qetl.log.with_context[context;p`fetch;enlist current];
    page:$[recent; fetched where in_window[job;window;fetched]; fetched];
    base:`job`mode`live`cursor`fetched!(job;$[recent; `recent; `next_page];liveness job;current;count fetched);
    if[recent;
        base,:`window`kept`page_limit`limited!(window;count page;limit;(not null limit) and limit<=count fetched)];
    if[0=count page;
        :base,`state`next_cursor`advances`rows`sample`failures!(`idle;(::);0b;()!();()!();())];
    out:outputs[job;p[`normalize] page];
    failures:raze contract_failures'[key out;value out];
    next_cursor:p[`next_cursor] page;
    advances:ops[`advances][current;next_cursor];
    if[not advances;
        failures,:enlist "next_cursor ",(-3!next_cursor)," does not move past ",(-3!current),
            " - a run would refuse to advance"];
    base,`state`next_cursor`advances`rows`sample`failures!(
        $[count failures; `invalid; `previewed];next_cursor;advances;
        count each out;(n sublist) each out;failures)}

\d .
