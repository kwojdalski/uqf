/ verify_capture.q - what each producer returns for one closed source window, read
/ only (.qetl.verify, #1851).
/ .
/ Capture runs a producer's own fetch, transform and check over [from; to) and
/ keeps the rows. It does not publish, advance a cursor, write a checkpoint or
/ record coverage - and the proof is a snapshot of all four, taken before and
/ after, held equal (`snapshot`, `capture`).
/ .
/ NO PARALLEL FETCH PATH. A streaming job is walked with the same fetch,
/ normalize, next_cursor and advances its timer uses, and the same window
/ judgement `uqs job preview --from/--to` applies (.qetl.job.stream.in_window),
/ from a temporary cursor that exists only here. A bounded worker's window goes
/ through the worker's own stage_fetch, stage_transform and stage_check - the
/ stages a run takes before it publishes - and stops there: stage_publish, which
/ is where publication, coverage and the checkpoint begin, is the one stage not run.
/ .
/ A capture that throws or fails a window is a result with its `error`, never a
/ thrown error and never rows: the report then says UNVERIFIED with the reason,
/ because nothing was captured.

\d .

/ The most pages one streaming capture walks, so a feed whose cursor never passes
/ the window's end cannot hold the verifier forever.
.qetl.verify.max_pages:10000

/ Private: one capture's result, every key present whatever happened.
/ @private
.qetl.verify.capture_result:{[producer;mode]
    `producer`mode`rows`fetched`windows`failures`error`live`basis`limited!
        (producer;mode;::;0;0;();"";`unknown;"";0b)}

/ Private: a list of tables as one table, or (::) for none.
/ @private
.qetl.verify.joined:{[parts] $[0=count parts; ::; raze parts]}

/ Private: one page of the walk - fetch after the cursor, keep what the window
/ holds, normalize it, and move the cursor as the feed's own next_cursor says.
/ @return the walk's state, `done set when it should stop
/ @private
.qetl.verify.stream_step:{[job;dataset;p;ops;window;st]
    page:.qetl.log.with_context[`job`cursor!(job;st`cursor);p`fetch;enlist st`cursor];
    st[`fetched]:st[`fetched]+count page;
    st[`pages]:st[`pages]+1;
    if[0=count page; :@[st;`done;:;1b]];
    kept:page where .qetl.job.stream.in_window[job;window;page];
    if[count kept;
        out:.qetl.job.stream.outputs[job;p[`normalize] kept];
        st[`failures]:st[`failures],raze .qetl.job.stream.contract_failures'[key out;value out];
        if[dataset in key out; st[`rows]:st[`rows],enlist out dataset]];
    nxt:p[`next_cursor] page;
    / a page reaching past the window's end is the last one that matters
    if[(count kept)<count page; st[`done]:1b];
    if[not ops[`advances][st`cursor;nxt];
        st[`failures]:st[`failures],enlist "next_cursor ",(-3!nxt)," does not move past ",(-3!st`cursor)," - a run would refuse to advance";
        st[`done]:1b];
    st[`cursor]:nxt;
    if[st[`pages]>=.qetl.verify.max_pages; st[`done]:1b; st[`limited]:1b];
    st}

/ Walk a polling job over [from_ts; to_ts) from a temporary cursor, keeping what its
/ normalize returns for `dataset`. The saved cursor is not read and nothing is saved;
/ the job's `close` runs either way.
/ @param job the streaming job
/ @param dataset the table whose rows are kept
/ @param from_ts the window's first instant, a timestamp
/ @param to_ts the instant after its last, a timestamp after from_ts
/ @return a capture result: rows, fetched, windows (pages walked), failures (the plant's
/   own contract messages and a cursor that does not advance), live, basis, limited
/ @throws error when the job is not a polling feed, or its fetch, normalize or next_cursor throws
/ @eg count .qetl.verify.capture_stream[`demo_deals_feed;`demo_deals;2026.09.11D00:00:00.000000000;2026.09.13D00:00:00.000000000]`rows  ->  2
.qetl.verify.capture_stream:{[job;dataset;from_ts;to_ts]
    d:.qetl.job.stream.def job;
    if[not `poll in key d; '"capture: ",string[job]," declares no poll - its on_timer is one function that publishes; declare poll to verify it"];
    p:d`poll;
    ops:.qetl.job.stream.cursor_ops job;
    window:.qetl.job.stream.recent_window[job;(from_ts;to_ts)];
    st:`cursor`rows`fetched`pages`failures`done`limited!(window`start_cursor;();0;0;();0b;0b);
    end:.[{[job;dataset;p;ops;window;st] while[not st`done; st:.qetl.verify.stream_step[job;dataset;p;ops;window;st]]; st};
        (job;dataset;p;ops;window;st);{[e] (`capture_failed;e)}];
    if[`close in key p; @[p`close;::;{[e] ::}]];
    if[(0h=type end) and `capture_failed~first end; 'last end];
    r:.qetl.verify.capture_result[job;`stream];
    r[`rows]:.qetl.verify.joined end`rows;
    r[`fetched]:end`fetched;
    r[`windows]:end`pages;
    r[`failures]:end`failures;
    r[`limited]:end`limited;
    r[`live]:.qetl.job.stream.liveness job;
    r[`basis]:"stream: ",string[end`pages]," page(s) from a temporary cursor at ",(-3!window`start_cursor),"; saved cursor untouched; source ",string[r`live];
    r}

/ Private: one bounded window through the stages before publication.
/ @return (1b; the batch) on success, (0b; the failure dict) otherwise - a pair,
/   because a list of same-keyed dictionaries is a table in q
/ @private
.qetl.verify.bounded_window:{[worker;cfg;w]
    stages:.qetl.job.bounded.window_stages`fetch`transform`check;
    s:{[worker;s;stage] $[`failed in key s; s; stage[worker;s]]}[worker]/[(``cfg`window)!(::;cfg;w);stages];
    $[`failed in key s; (0b;s`failed); (1b;s`batch)]}

/ Private: the capture's body for one worker, between opening the source and closing it.
/ @private
.qetl.verify.bounded_body:{[worker;windows]
    cfg:.qetl.job.bounded.def worker;
    got:.qetl.verify.bounded_window[worker;cfg;] each windows;
    ok:got[;0];
    `rows`failed!(.qetl.verify.joined got[where ok;1];got[where not ok;1])}

/ Capture what a bounded worker would publish for [from_ts; to_ts): each window, as the
/ worker plans it, through its own fetch, transform and quality gate. Publication,
/ coverage and the checkpoint - stage_publish onward - are not run.
/ @param worker the bounded worker
/ @param from_ts the range's first instant, aligned as the worker requires
/ @param to_ts the instant after its last
/ @return a capture result; a window that failed fetch, transform or check puts its
/   message in `failures` and leaves `rows` unset, because a partial capture would pass for a whole one
/ @throws error when the range is reversed, empty or not aligned to the worker's grid
/ @eg .qetl.verify.capture_bounded[`demo_deals_backfill;2026.09.11D00:00:00.000000000;2026.09.13D00:00:00.000000000]`windows  ->  2
.qetl.verify.capture_bounded:{[worker;from_ts;to_ts]
    cfg:.qetl.job.bounded.def worker;
    .qetl.coverage.require_interval[from_ts;to_ts];
    .qetl.job.bounded.runtime.require_aligned[worker;cfg;from_ts;to_ts];
    windows:.qetl.job.bounded.runtime.windows[from_ts;to_ts;cfg`width];
    live:.qetl.source.has_credentials cfg`source;
    had:.qetl.job.bounded.read_state[worker;`handle];
    was_live:.qetl.job.bounded.read_state[worker;`live];
    opened:live and null had;
    .qetl.job.bounded.write_state[worker;`live;live];
    if[opened; .qetl.job.bounded.write_state[worker;`handle;.qetl.job.bounded.connect worker]];
    res:.[.qetl.verify.bounded_body;(worker;windows);{[e] (`capture_failed;e)}];
    if[opened;
        ((.qetl.source.for_source cfg`source)`close) .qetl.job.bounded.read_state[worker;`handle];
        .qetl.job.bounded.write_state[worker;`handle;0Ni]];
    .qetl.job.bounded.write_state[worker;`live;was_live];
    if[(0h=type res) and `capture_failed~first res; 'last res];
    r:.qetl.verify.capture_result[worker;`bounded];
    r[`windows]:count windows;
    r[`live]:$[live; `live; `fixture];
    r[`basis]:string[count windows]," window(s) of ",string[cfg`width]," over [",string[from_ts],"; ",string[to_ts],"); source ",string[r`live],
        "; stopped before publication, coverage and the checkpoint";
    r[`failures]:{[f] f[`message],$[count f`fields; " - ",-3!f`fields; ""]} each res`failed;
    if[count r`failures; :r];
    r[`rows]:res`rows;
    r[`fetched]:$[98h=type res`rows; count res`rows; 0];
    r}

\d .
