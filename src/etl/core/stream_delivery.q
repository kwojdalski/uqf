/ stream_delivery.q - what a polling feed's tick does to know where a page
/ went (.qetl.job.stream.delivery, #1651).
/ .
/ A tick publishes a page and then saves the cursor past it
/ (stream_poll.q). Between the two the page is IN DOUBT: a crash, a kill or
/ a publish that threw part-way leaves the cursor where it was, and the next
/ tick fetches the same page again - some or all of which may already be in
/ the tickerplant. Before this file the page was simply sent again, and
/ nothing said so.
/ .
/ THE MARKER. Before publishing, the tick writes `<cursor>.inflight` beside
/ its cursor: the cursor it is moving from, the one it proposes, and the
/ page's row count. It is removed once the cursor is saved. A tick that
/ finds one still there knows the previous page may or may not have landed:
/ .
/   - its cursor equals the marker's `to`: the save happened and the removal
/     did not. Nothing is in doubt; the marker is removed.
/   - otherwise the page after the cursor is in doubt, and it is settled:
/       * with a declared poll `row_key` and a reader wired, the reader is
/         asked which of the page's keys the published table already holds,
/         and only the absent rows are published;
/       * without either, the page is published again, as before, but logged
/         as possibly duplicated with its cursor range, and counted in
/         .qetl.stream_health.
/ .
/ THE ACKNOWLEDGEMENT. A job whose poll declares `ack` 1b has each publish
/ confirmed: the transport's logged count for the table is read before and
/ after, and a count that did not advance by the rows the publisher reports
/ sending fails the tick - the cursor does not move.
/ .
/ None of this is exactly-once. The reader sees what its process holds, not
/ what the tickerplant logged; the marker is renamed into place, never
/ fsynced; and a page republished without a key is a duplicate when the
/ first copy landed. The guide (docs/guides/attach-existing-torq.md, "What a
/ publish acknowledges") states what is and is not guaranteed.
/ .
/ TorQ-free, like the rest of src/: the reader and the logged count are
/ functions a runner wires (`wire`). torq_stream.q wires them to the rdb
/ and to the segmented tickerplant's counters (.qtorq.present_keys,
/ .qtorq.logged_rows).

\d .

/ The functions a runner may wire for a job: present[table; keys] -> one
/ boolean per key row, 1b where the published table already holds it; and
/ logged[table] -> how many rows of the table the plant has logged.
.qetl.job.stream.delivery.hook_keys:`present`logged

/ Per job, each wired hook. Two dicts rather than one dict of dicts: q turns
/ a dict whose values are same-keyed dicts into a table.
.qetl.job.stream.delivery.present:(`symbol$())!()
.qetl.job.stream.delivery.logged:(`symbol$())!()

/ Private: refuse a malformed `row_key` or `ack` in a poll declaration.
/ Called by poll_declared.
/ @param job the job's name
/ @param decl the declaration, carrying `poll` and `publishes`
/ @return nothing - it throws naming the job and the key
/ @private
.qetl.job.stream.delivery.check:{[job;decl]
    p:decl`poll;
    if[`row_key in key p;
        k:p`row_key;
        if[not $[11h=abs type k; 0<count k; 0b];
            '"define: ",string[job],"'s poll row_key must be a symbol or symbols - the columns that identify one published row"];
        k:(),k;
        if[`time in k;
            '"define: ",string[job],"'s poll row_key includes time - the tickerplant stamps its own, so a republished row never matches"];
        plant:((),decl`publishes) inter .qetl.plant.names[];
        if[count bad:plant where not all each k in/: cols each .qetl.plant.published each plant;
            '"define: ",string[job],"'s poll row_key ",(", " sv string k)," is not in the columns of ",", " sv string bad]];
    if[(`ack in key p) and not -1h=type p`ack;
        '"define: ",string[job],"'s poll ack must be a boolean - 1b to confirm each publish reached the tickerplant's log"];
    }

/ Wire the delivery hooks a runner provides for `job`. Either may be left
/ out: a job that declares `row_key` without a reader republishes an in-doubt
/ page as possibly duplicated, and one that declares `ack` without a logged
/ count fails every tick that publishes, saying why.
/ @param job the job's name
/ @param hooks dict of some of hook_keys -> function
/ @return the job's name
/ @throws error for a key that is not a hook, or a hook that is not callable
/ @eg .qetl.job.stream.delivery.wire[`fx_feed;`present`logged!({[t;k] (count k)#0b};{[t] 0})]  ->  `fx_feed
.qetl.job.stream.delivery.wire:{[job;hooks]
    if[not 99h=type hooks; '"delivery.wire: ",string[job],"'s hooks must be a dictionary of ",", " sv string .qetl.job.stream.delivery.hook_keys];
    if[count bad:(key hooks) except .qetl.job.stream.delivery.hook_keys;
        '"delivery.wire: ",string[job]," was given ",(", " sv string bad),", which is not a delivery hook"];
    if[count bad:(key hooks) where not .qetl.job.stream.is_callable each value hooks;
        '"delivery.wire: ",string[job],"'s ",(", " sv string bad)," must be functions"];
    if[`present in key hooks; .qetl.job.stream.delivery.present[job]:hooks`present];
    if[`logged in key hooks; .qetl.job.stream.delivery.logged[job]:hooks`logged];
    job}

/ Forget a job's wired hooks.
/ @param job the job's name
/ @return the job's name
.qetl.job.stream.delivery.unwire:{[job]
    .qetl.job.stream.delivery.present:(enlist job) _ .qetl.job.stream.delivery.present;
    .qetl.job.stream.delivery.logged:(enlist job) _ .qetl.job.stream.delivery.logged;
    job}

/ Private: the job's wired hook `nm`, or (::) when none is.
/ @private
.qetl.job.stream.delivery.hook:{[job;nm]
    d:get ` sv `.qetl.job.stream.delivery,nm;
    $[job in key d; d job; (::)]}

/ ------------------------------------------------------------- THE MARKER

/ Where a polling job's in-flight marker lives: beside its cursor.
/ @param name the cursor's name (.qetl.job.stream.cursor_name)
/ @return the path, a string
.qetl.job.stream.delivery.marker_path:{[name] (.qetl.job.bounded.state.lock_dir[]),"/",string[name],".inflight"}

/ The in-flight marker, or (::) when there is none.
/ @param name the cursor's name
/ @return dict of from, to, rows and at; or (::)
/ @throws error when the marker and its .bak are both unreadable
.qetl.job.stream.delivery.marker:{[name]
    path:.qetl.job.stream.delivery.marker_path[name];
    $[()~key hsym `$path; (::); .qetl.job.bounded.state.durable_get path]}

/ Write the marker before a page is published: the page from `from` to `to`
/ holding `rows` rows may reach the plant from here on.
/ @param name the cursor's name
/ @param from the cursor the page was fetched after
/ @param to the cursor the page would move it to
/ @param rows the page's row count, every table together
/ @return the path written
.qetl.job.stream.delivery.mark:{[name;from;to;rows]
    system"mkdir -p ",.qetl.job.bounded.state.shell_word .qetl.job.bounded.state.lock_dir[];
    .qetl.job.bounded.state.durable_set[.qetl.job.stream.delivery.marker_path[name];`from`to`rows`at!(from;to;rows;.z.p)]}

/ Remove the marker, once the cursor past its page is saved.
/ @param name the cursor's name
/ @return the path removed
.qetl.job.stream.delivery.unmark:{[name] .qetl.job.bounded.state.durable_remove .qetl.job.stream.delivery.marker_path[name]}

/ The previous page's marker when that page is still in doubt, else (::).
/ A marker whose `to` is the saved cursor is left over from a tick that
/ saved and did not get to remove it: that page was acknowledged, and the
/ marker is removed here.
/ @param name the cursor's name
/ @param current the cursor just loaded
/ @return the marker, or (::)
.qetl.job.stream.delivery.in_doubt:{[name;current]
    m:.qetl.job.stream.delivery.marker[name];
    if[m~(::); :m];
    if[current~m`to;
        .qetl.job.stream.delivery.unmark[name];
        .[{.qetl.log.dbg[x;y;z]};(name;"in-flight marker cleared - its page was saved";`from`to!m`from`to);::];
        :(::)];
    m}

/ ------------------------------------------------------- SETTLING A DOUBT

/ What to publish of a page fetched while the previous one is in doubt.
/ .
/ No marker: the page, as it is. A declared row_key with a wired reader: the
/ rows of each table whose keys the reader does not find. Otherwise: the
/ page, logged as possibly duplicated and counted.
/ @param job the job's name
/ @param m the marker in_doubt returned, or (::)
/ @param out the page, as table -> rows
/ @return table -> the rows to publish
/ @throws error when the reader throws or answers with something other than
/   one boolean per key - the tick fails, and nothing is published
.qetl.job.stream.delivery.settle:{[job;m;out]
    if[m~(::); :out];
    p:(.qetl.job.stream.def job)`poll;
    present:.qetl.job.stream.delivery.hook[job;`present];
    keyed:(`row_key in key p) and not present~(::);
    sent:sum .qetl.job.stream.row_count'[key out;value out];
    if[keyed;
        kept:.qetl.job.stream.delivery.absent[job;present;(),p`row_key]'[key out;value out];
        out:(key out)!kept;
        n:sum count each kept;
        .qetl.stream_health.record_doubt[job;1b;sent-n;m];
        .[{.qetl.log.warn[x;y;z]};(job;"in-doubt page resolved by row key";
            `from`to`fetched`already_published`publishing!(m`from;m`to;sent;sent-n;n));::];
        :out];
    why:$[`row_key in key p; "no reader of the published tables is wired"; "the job declares no poll row_key"];
    .qetl.stream_health.record_doubt[job;0b;sent;m];
    .[{.qetl.log.warn[x;y;z]};(job;"in-doubt page published again - possibly duplicated";
        `from`to`rows`reason!(m`from;m`to;sent;why));::];
    out}

/ Private: the rows of one table the reader does not already hold.
/ @private
.qetl.job.stream.delivery.absent:{[job;present;k;t;rows]
    if[not $[.Q.qt rows; 1b; t in .qetl.plant.names[]];
        '"tick: ",string[job],"'s ",string[t]," rows are not a table, so its row_key cannot pick them out"];
    r:$[.Q.qt rows; 0!rows; .qetl.job.stream.as_rows[cols .qetl.plant.published t;rows]];
    if[count miss:k except cols r;
        '"tick: ",string[job],"'s row_key ",(", " sv string miss)," is not a column of its ",string[t]," rows"];
    seen:present[t;k#r];
    if[not $[1h=type seen; count[seen]=count r; 0b];
        '"tick: the reader of ",string[t]," must answer one boolean per key row - got ",-3!seen];
    r where not seen}

/ ------------------------------------------------------------- PUBLISHING

/ Publish every table of a page through the job's `publish`, confirming each
/ with the plant when the job's poll declares `ack` 1b.
/ @param job the job's name
/ @param out the page, table -> rows
/ @return what each publish returned
/ @throws error from a publish, or for a publish the plant did not count
.qetl.job.stream.delivery.send:{[job;out]
    d:.qetl.job.stream.def job;
    pub:get ` sv (d`ns),`publish;
    if[not $[`ack in key d`poll; (d`poll)`ack; 0b]; :{[pub;t;rows] pub[t;rows]}[pub]'[key out;value out]];
    logged:.qetl.job.stream.delivery.hook[job;`logged];
    if[logged~(::);
        '"tick: ",string[job]," declares poll ack, but its runner wires no logged count to confirm a publish with - nothing was published"];
    .qetl.job.stream.delivery.acked[job;pub;logged]'[key out;value out]}

/ Private: one publish, confirmed. The count is read on either side of the
/ publish, so it must advance by at least the rows the publisher reports
/ sending - its return when that is a long, else none.
/ @private
.qetl.job.stream.delivery.acked:{[job;pub;logged;t;rows]
    before:logged t;
    r:pub[t;rows];
    n:$[-7h=type r; r; 0];
    after:logged t;
    if[after<before+n;
        '"tick: ",string[job],"'s publish of ",string[n]," row(s) onto ",string[t],
         " was not acknowledged - the tickerplant's logged count went from ",string[before]," to ",string[after],
         ", so the cursor stays and the page is in doubt"];
    .[{.qetl.log.dbg[x;y;z]};(job;"publish acknowledged";`table`rows`logged!(t;n;after));::];
    r}

\d .
