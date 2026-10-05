/ stream_poll.q - polling feeds, and previewing one without running it
/ (.qetl.job.stream, beside stream_job.q).
/ .
/ A polling feed fetches a page from outside the stack, normalizes it,
/ publishes it and advances a cursor - all in one timer tick. Written as one
/ on_timer lambda, those steps cannot be run apart: the only way to see what
/ a feed would publish was to let it publish, and advance its cursor (#663).
/ .
/ So a feed may declare `poll` instead of `on_timer`: its steps as separate
/ functions. `define` builds the timer from them - .qetl.job.continuous.poll_once,
/ which publishes and THEN advances - and `preview` runs the same fetch and
/ normalize with neither the publish nor the advance. They are not a muted
/ copy of the timer: preview never calls publish, never calls save_cursor,
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
poll_declared:{[job;decl]
    p:decl`poll;
    if[not 99h=type p;
        '"define: ",string[job],"'s poll must be a dictionary of fetch, normalize and next_cursor"];
    if[count missing:poll_keys where not poll_keys in key p;
        '"define: ",string[job],"'s poll is missing ",", " sv string missing];
    if[count bad:(poll_keys,`close inter key p) where not is_callable each p poll_keys,`close inter key p;
        '"define: ",string[job],"'s poll ",(", " sv string bad)," must be functions"];
    if[count bad:(`source`cursor inter key p) where not -11h=type each p `source`cursor inter key p;
        '"define: ",string[job],"'s poll ",(", " sv string bad)," must be symbols"];
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

/ One poll: fetch a page, normalize it, publish every table, advance the
/ cursor - publish first, as .qetl.job.continuous.poll_once orders it. What
/ a polling job's generated on_timer calls.
/ @param job the job's name
/ @return dict of state (`idle or `published), rows and cursor
tick:{[job]
    d:def job;
    p:d`poll;
    pub:get ` sv (d`ns),`publish;
    send:{[job;p;pub;page]
        out:outputs[job;p[`normalize] page];
        {[pub;t;rows] pub[t;rows]}[pub]'[key out;value out];
        sum count each value out}[job;p;pub];
    .qetl.job.continuous.poll_once[cursor_name job;p`fetch;send;p`next_cursor]}

/ Private: what is wrong with `rows` for plant table `t`, by name, order
/ and type. A table the plant does not carry has nothing to compare against.
/ @return a list of messages, empty when it matches
contract_failures:{[t;rows]
    if[not t in .qetl.plant.names[]; :()];
    want:0!meta .qetl.plant.published t;
    got:0!meta 0!rows;
    if[(want[`c];want[`t])~(got[`c];got[`t]); :()];
    enlist string[t]," has columns ",(" " sv string got`c)," typed \"",got[`t],
        "\" - the plant takes ",(" " sv string want`c)," typed \"",want[`t],"\""}

/ Private: whether a polling job's page is live data, the fixture, or
/ unknown - known only when its poll names the source it reads.
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
/ @return dict of job, state (`previewed, `idle or `invalid), live, cursor,
/   fetched, next_cursor, advances, rows and sample (each table -> ...),
/   and failures (messages; empty unless state is `invalid)
/ @throws error when the job is not a polling feed, or its fetch, normalize
/   or next_cursor throws
preview:{[job;n]
    d:def job;
    if[count d`subscribe_to;
        '"preview: ",string[job]," subscribes to ",(", " sv string d`subscribe_to),
         " - its input arrives from the plant, so there is no page to fetch. Preview supports polling feeds"];
    if[not `poll in key d;
        '"preview: ",string[job]," declares no poll - its on_timer is one function, and running it would publish and advance. Declare poll (fetch, normalize, next_cursor) to preview it"];
    r:.[preview_page;(job;n);{[e] (`preview_failed;e)}];
    if[`close in key d`poll; @[(d`poll)`close;::;{[e] ::}]];
    if[(0h=type r) and `preview_failed~first r; 'last r];
    r}

/ Private: preview's body, between the guard and `close`.
preview_page:{[job;n]
    p:(def job)`poll;
    current:.qetl.job.continuous.load_cursor cursor_name job;
    page:p[`fetch] current;
    base:`job`live`cursor`fetched!(job;liveness job;current;count page);
    if[0=count page;
        :base,`state`next_cursor`advances`rows`sample`failures!(`idle;0Np;0b;()!();()!();())];
    out:outputs[job;p[`normalize] page];
    failures:raze contract_failures'[key out;value out];
    next_cursor:p[`next_cursor] page;
    advances:(not null next_cursor) and (null current) or next_cursor>current;
    if[not advances;
        failures,:enlist "next_cursor ",string[next_cursor]," does not move past ",string[current],
            " - a run would refuse to advance"];
    base,`state`next_cursor`advances`rows`sample`failures!(
        $[count failures; `invalid; `previewed];next_cursor;advances;
        count each out;(n sublist) each out;failures)}

\d .
