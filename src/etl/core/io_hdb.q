/ io_hdb.q - the HDB writer behind .qetl.io's manager contract: append each
/ window to its date's partition, then the atomic stage-and-swap that finishes
/ a partition, and the crash recovery that finishes what a killed run left.
/ .
/ Moved out of io_manager.q (#618) unchanged, in the same namespace. It is the
/ riskiest code the io layer has, and it sat beside the memory and discard
/ adapters, which are two lines each; on its own it is reviewed as what it is.
/ io_manager.q keeps the contract, the write strategies and the dispatch.
/ Loaded straight after io_manager.q (src/etl/init.q), so load order is as it was.

\d .

/ ---------------------------------------------------------------- HDB

/ Writes each window straight into the HDB partition for its rows' own date,
/ bypassing the tickerplant.
/ .
/ WHY NOT THE TICKERPLANT, for a backfill. The plant stamps `time` on
/ receipt, the RDB holds today, and end-of-day writes everything into
/ TODAY's partition - so a September deal published now lands in today's
/ date, where a query for its own day never finds it. And every subscriber
/ downstream would treat old rows as live ones. History belongs in the
/ partition of the day it happened, written directly.
/ .
/ WHAT write DOES, per window. Refuses rows dated today or later: that
/ partition belongs to the tickerplant and end-of-day, and two writers there
/ collide. Partitions by `time` when the batch has one - every stack table
/ leads with it (src/etl/plant_tables.q), and eq_orderbook's
/ transform already fills it with the event time - and otherwise adds `time`
/ as a copy of `partition_col` (demo_deals and duckdb_deals carry deal_time),
/ so the partition gets the plant's shape and a query on time finds the row
/ in its own date. Enumerates symbols against the HDB's sym file, then appends to
/ <root>/<date>/<target>/, creating it on first write.
/ .
/ WHAT finish DOES, once per run. Every partition this run wrote to is
/ sorted by sym then time and given `p#sym` - appending window by window
/ leaves neither - and .Q.chk fills in tables a partition lacks, so a query
/ across dates does not fail on one the backfill created with a single table
/ in it. .Q.chk takes its table list from the most recent partition, which
/ in a running stack is end-of-day's and holds every table; the full,
/ schema-driven repair (scripts/hdb/hdb_fill.q) runs on every
/ uqs command's bootstrap.
/ .
/ WHAT flush DOES, after every window. The same work as finish, for only the
/ partitions dated wholly before the window's end: windows run in order, so
/ nothing later in the run writes to them again, and finishing them now
/ rather than at the end is what lets a running HDB show a backfill day by
/ day. It reports how many partitions are still open, because a reload while
/ one is mid-append would map a half-written, unsorted partition.
/ .
/ Neither tells a running HDB to reload: that needs the stack, so the
/ manager's on_ready does it, set by scripts/processes/torq_backfill.q.
/ .
/ Every call holds the root's write lock (hdb_lock, #1083), so backfills
/ writing one root queue rather than lose each other's rows or interleave
/ appends to its sym file. TorQ's end-of-day does not take it - the vendored
/ tree is not edited - so a backfill is still not safe beside end-of-day:
/ both append to the sym file. Backfills never write today's partition.
/ .
/ ON PEACHQ. PeachQ cannot upsert onto a splayed table on disk, so write
/ rewrites a partition it appends to whole. It has no on-disk attributes and
/ no .Q.chk, so there finish
/ sorts each partition and does neither: no p#sym, no s#time, no filling of
/ missing tables - uqs's bootstrap fills those from the schema instead
/ (scripts/hdb/hdb_fill.q). With no attribute to read, a
/ partition counts as finished when it is in finish's order.

/ Is this PeachQ? The one test this tree uses for it, as uqs's own
/ (python/uqs/src/uqs/interpreter.py): PeachQ defines .pq.load_natives.
.qetl.io.on_peachq:not -7h=type @[value;`.pq.load_natives;{0N}]

/ How long an HDB write waits for another writer on the same root (#1083).
/ A keyed write rewrites a whole date partition, so the holder may be busy
/ for well over the ledger's few seconds; a dead holder is broken at once.
.qetl.io.hdb_lock_wait:0D00:10

/ The lock every writer to one HDB root holds (#1083), beside the root as
/ its staging area is, so every process writing the root shares it whatever
/ its own status directory. By ROOT, not by worker, date or table: workers
/ partitioned over one dataset write the same date's table, a keyed write
/ reads a partition, resolves and swaps it back - so two at once lose
/ whichever swapped first - and every table in a root enumerates against
/ the one sym file. Held for one write, flush, finish or recover call, so
/ writers queue for a window rather than for a run.
/ .
/ ONE PATH PER ROOT, HOWEVER IT IS SPELLED (#1168). A trailing slash put the
/ lock INSIDE the root (`:/data/hdb/ -> /data/hdb/.write.lock), and a relative
/ root left it relative; uqs data migrate takes this lock through q
/ (scripts/hdb/hdb_lock.q) with the root made absolute, so a writer given
/ either spelling held a different lock than the migration and neither
/ excluded the other. Nor did `.`, `..` or `//` inside it (#2311): `:/data/hdb/.
/ and `:./hdb each took a lock of their own. So the path is made absolute and
/ then normalised: empty and `.` parts dropped, each `..` taking off the part
/ before it. Lexical only: symlinks are not resolved, so a root reached
/ through two different links is still two locks.
/ @param root the HDB root, a file symbol
/ @return the lock directory's path, absolute
/ @eg .qetl.io.hdb_lock `:/data/hdb  ->  "/data/hdb.write.lock"
/ @eg .qetl.io.hdb_lock `:/data/hdb/  ->  "/data/hdb.write.lock"
/ @eg .qetl.io.hdb_lock `:/data/x/..//hdb/.  ->  "/data/hdb.write.lock"
.qetl.io.hdb_lock:{[root]
    p:1_string root;
    if[not "/"=first p; p:(system"cd"),"/",p];
    s:"/" vs p;
    s:s where not (s~\:"") or s~\:enlist ".";
    s:{[kept;part] $[part~".."; -1_kept; kept,enlist part]}/[();s];
    "/",("/" sv s),".write.lock"}

/ Private: run f . args holding root's write lock.
/ @private
.qetl.io.locked:{[root;f;args]
    .qetl.job.bounded.state.with_lock_at[.qetl.io.hdb_lock[root];.qetl.io.hdb_lock_wait;f;args]}

/ Partitions written and not yet finished: root, date, table.
.qetl.io.touched:([] hdb_root:`symbol$(); dt:`date$(); tbl:`symbol$())

/ The dates of the partitions written since `touched` held `n` rows - what one window's publish
/ added (#2549). A table that has shrunk meanwhile (a finish ran) gives all of it: more
/ partitions to check, never fewer.
/ @param n the row count taken before the publish
/ @return the sorted distinct dates
/ @eg .qetl.io.touched_since count .qetl.io.touched  ->  `date$()
.qetl.io.touched_since:{[n] asc distinct ((n*n<=count .qetl.io.touched)_.qetl.io.touched)`dt}

/ An HDB writer for one root and partition column.
/ @param root the HDB directory, as a file symbol, e.g. `:/data/hdb
/ @param partition_col the timestamp column that becomes `time`, and so picks
/   the partition, for a batch that has no `time` of its own, e.g. `deal_time
/ @return a manager carrying write and finish
/ @throws error when root is not a file symbol or partition_col not a symbol
/ @eg .qetl.io.hdb[`:/tmp/qio_eg_hdb;`deal_time]
.qetl.io.hdb:{[root;partition_col]
    if[not (-11h=type root) and ":"=first string root;
        '"hdb: root must be a file symbol, e.g. `:/data/hdb"];
    if[not -11h=type partition_col;
        '"hdb: partition_col must be a symbol naming a timestamp column"];
    / Each entry takes root's write lock for the one call (#1083). Lambdas over
    / the arguments, not projections of `locked`: a fully-applied projection
    / is a CALL, and would run the write before the lock is taken.
    / finish takes a second, ignored argument so it stays a function of one.
    `write`write_keyed`flush`finish`recover`eod_waiting`days_of!(
        {[r;p;t;b] .qetl.io.locked[r;.qetl.io.write_hdb;(r;p;t;b)]}[root;partition_col];
        {[r;p;t;b;o] .qetl.io.locked[r;.qetl.io.write_hdb_keyed;(r;p;t;b;o)]}[root;partition_col];
        {[r;u] .qetl.io.locked[r;.qetl.io.flush_hdb;(r;u)]}[root];
        {[r;x] .qetl.io.locked[r;.qetl.io.finish_hdb;(r;x)]}[root];
        {[r;a;b;c] .qetl.io.locked[r;.qetl.io.recover_hdb;(r;a;b;c)]}[root];
        / which dates end of day has not yet handed over - the plan reads it (#2186)
        .qetl.io.eod_waiting[root];
        / the root's own partition calendar, so a plan's range maps to the dates written (#2193)
        .qetl.io.days_of[root])}

/ The partition date of each time: the calendar date, except in a root a scoped manager was made
/ for, where it is the native stack's own (.qetl.io.scoped_day_of, #2193).
/ @param root the HDB root
/ @param times a timestamp or a list of them
/ @return the date or dates
/ @eg .qetl.io.days_of[`:/no/such/root;2026.01.02D10:00:00.000000000]  ->  2026.01.02
.qetl.io.days_of:{[root;times] $[.qetl.io.is_scoped root; .qetl.io.scoped_day_of times; `date$times]}

/ Private: append one window into its date partitions.
/ @private
.qetl.io.write_hdb:{[root;partition_col;target;batch]
    if[0=count batch; :0];
    .qetl.io.restore_retired[root;target];
    data:.qetl.io.hdb_rows[root;partition_col;target;batch];
    days:.qetl.io.days_of[root;data`time];
    / Every date's types are checked before any is touched, and before .Q.en
    / extends the sym file (#2329).
    {[root;target;data;days;d] .qetl.io.require_part_types[hsym `$(string .Q.par[root;d;target]),"/";data where days=d]
        }[root;target;data;days] each distinct days;
    data:.Q.en[root;data];
    {[root;target;data;days;d]
        part:hsym `$(string .Q.par[root;d;target]),"/";
        rows:data where days=d;
        existing:key part;
        / A partition a previous run finished carries p#sym; appending out of
        / order to it is not safe, so the attribute comes off here and
        / finish sorts and puts it back.
        / A declared rule's other attributes (s#time, say) would refuse the
        / append the same way, so every column of the rule loses its own.
        if[not .qetl.io.on_peachq;
            {[part;existing;col] if[col in existing; @[part;col;`#]]}[part;existing] each
                $[count r:.qetl.io.declared_rule[target]; key r; `sym]];
        / PeachQ cannot upsert onto a splayed table on disk ('type), so there
        / the partition is read back and written again whole.
        / The FIRST write of a table into a date is staged and swapped in like a
        / keyed one (#2185): a bare `set` writes .d and then the column files one
        / by one, so a kill left a partition the HDB could not read and recover
        / threw on. Staged, it appears whole or not at all.
        $[()~existing; [.qetl.io.stage[root;d;target;rows]; .qetl.io.swap[root;d;target]];
          .qetl.io.on_peachq; [.qetl.io.write_splay[root;part;.Q.en[root] t,(cols t:.qetl.io.read_part[part])#rows]; ::];
          part upsert rows];
        `.qetl.io.touched upsert (root;d;target);
        }[root;target;data;days] each distinct days;
    count batch}

/ Private: refuse rows whose column types are not their partition's own (#2329).
/ .
/ upsert onto a splayed table appends column by column, so a batch with one
/ column of another type threw part-way and left the partition's columns of
/ different lengths; on PeachQ, whose append rewrites the partition whole,
/ it retyped the rows already there. scripts/dev/attached_forced.q makes the
/ same check for its writer (#2208). Every column the partition has must be
/ in the batch with its type; a general-list column on disk takes anything.
/ Read through read_part on PeachQ, which cannot take `meta` of a directory.
/ @private
.qetl.io.require_part_types:{[part;rows]
    if[()~key part; :1b];
    native:exec c!t from meta $[.qetl.io.on_peachq; .qetl.io.read_part[part]; part];
    batch:exec c!t from meta rows;
    k:key native;
    bad:k where not (" "=native k) or native[k]=batch k;
    if[0=count bad; :1b];
    '"hdb: the batch does not match the partition ",(1_string part)," - nothing was written; ",
        "; " sv {[n;b;c] string[c],": partition ",n[c],", batch ",$[not c in key b; "(absent)"; " "=b c; "a general list"; b c]}[native;batch] each bad}

/ Private: write a table, already run through .Q.en against root, as a splayed table at dir.
/ .
/ On PeachQ `set` of a splayed table enumerates its symbol columns AGAIN, against a `sym`
/ file it creates in dir's PARENT - the date folder, once the table is in its partition (#2285).
/ A partitioned HDB keeps one sym, at its root, and KDB-X refuses one inside a partition. So
/ there each column is cast against root's sym and written file by file, then .d: the same bytes
/ KDB-X writes, with the sym file where it belongs. Elsewhere it is a plain `set`.
/ @private
.qetl.io.write_splay:{[root;dir;t]
    if[not .qetl.io.on_peachq; dir set t; :dir];
    `sym set get ` sv root,`sym;
    c:cols t;
    {[dir;c;v] (` sv dir,c) set v}[dir]'[c;{$[11h=type x; `sym$x; x]} each t c];
    (` sv dir,`.d) set c;
    dir}

/ Private: a partition on disk as a table, symbols decoded - what PeachQ
/ appends to by writing the partition again whole.
/ @private
.qetl.io.read_part:{[part]
    base:-1_string part;
    c:get hsym `$base,"/.d";
    flip c!{[base;x] v:get hsym `$base,"/",string x; $[type[v] within 20 76h; value v; v]}[base] each c}

/ Private: the batch with the plant's `time` column, refused when it cannot
/ be partitioned, would land in the tickerplant's dates, or would land in a
/ date end of day has not yet handed over (io_hdb_eod.q).
/ @private
.qetl.io.hdb_rows:{[root;partition_col;target;batch]
    tc:.qetl.plant.time_column;
    if[not any (tc;partition_col) in cols batch;
        '"hdb: ",string[target],"'s batch has neither time nor ",string[partition_col]," to partition by"];
    data:$[tc in cols batch; batch; tc xcols ![batch;();0b;enlist[tc]!enlist partition_col]];
    days:.qetl.io.days_of[root;data tc];
    if[any null days;
        '"hdb: ",string[target]," has rows with a null time - no partition to put them in"];
    .qetl.io.require_handed_over[root;target;distinct days];
    data}

/ Private: write a batch under opts`on_conflict, one date partition at a time.
/ .
/ Every partition the batch touches is read, resolved and rewritten whole -
/ which is why `append stays a separate, faster path. Under `replace, every
/ date the WINDOW covers is visited too, rows or not: a day the source has
/ emptied is exactly the day that must lose what it had. Days from today on
/ are never cleared - they are the tickerplant's.
/ .
/ Resolved for every date BEFORE any is written, so a `fail on the third
/ date leaves the first two untouched rather than half a window written.
/ .
/ THE SPILL (#884). A row's key is one row, wherever a writer put it. A
/ stream job stamps a row when it RECEIVES it - at or after the event - so
/ near midnight it can file under the next day a row whose backfill twin
/ files under this one: both writers, one key, two partitions, and a
/ `replace of this day alone would leave the live copy as a duplicate.
/ So under `replace, the partition holding the window's END loses every
/ row whose key the window wrote AND whose time column lies inside the
/ window (#2197: by key alone it deleted the next day's own rows). Only when the key leaves out the time
/ column - with time in the key, a later partition cannot hold the same
/ key - and never in today's partition, which is the tickerplant's: a
/ refill of yesterday cannot reach a row still on its way to end-of-day.
/ Not on PeachQ, which cannot rewrite a partition already on disk: there a
/ replace leaves the spill, as it always did.
/ @private
.qetl.io.write_hdb_keyed:{[root;partition_col;target;batch;opts]
    strategy:.qetl.io.require_strategy opts`on_conflict;
    .qetl.io.restore_retired[root;target];
    if[`append=strategy; :.qetl.io.write_hdb[root;partition_col;target;batch]];
    / Shaped even when empty: a `replace of a day the source has emptied
    / still needs the target's columns to resolve against.
    data:.Q.en[root;.qetl.io.hdb_rows[root;partition_col;target;batch]];
    days:.qetl.io.days_of[root;data`time];
    dates:distinct days;
    / NEW KEYS ARE APPENDED, not resolved into a rewrite (#1086). Every
    / window used to read its date whole, resolve and swap it back, so a
    / day filled window by window was rewritten once per window. Under
    / upsert, ignore and fail, a batch none of whose keys a date already
    / holds resolves to that date plus the batch - folded within itself, per date, as
    / resolve folds it - so it is appended, as the append path does. Only
    / when EVERY date is clean: one clash sends the whole batch the slow way,
    / so a `fail on one date still leaves every date untouched. replace
    / always rewrites - it removes the window's rows. Not on PeachQ, whose
    / append path rewrites a partition whole anyway (it cannot upsert onto a
    / splayed table), so there is nothing to save and the key probe would
    / read a partition the way PeachQ cannot.
    if[(not .qetl.io.on_peachq) and not `replace=strategy;
        kc:(),opts`row_key;
        / The fold IS resolve's: each date's rows resolved against an empty
        / table, one call per date as the slow path makes one per partition
        / (#1160; the hand copy drifted once, #1142).
        fo:(`target,())!(),target;
        folds:{[strategy;kc;data;days;fo;d]
            .qetl.io.resolve[strategy;0#data;data where days=d;fo,enlist[`row_key]!enlist kc]}[strategy;kc;data;days;fo] each dates;
        fold:raze folds;
        clean:{[root;target;kc;rows;d]
            part:hsym `$(string .Q.par[root;d;target]),"/";
            if[()~key part; :1b];
            if[not (exec t from meta rows)~exec t from meta part; :0b];
            not any (kc#rows) in ?[part;();0b;kc!kc]}[root;target;kc]'[folds;dates];
        if[all clean; .qetl.io.write_hdb[root;partition_col;target;fold]; :count batch]];
    if[`replace=strategy;
        span:{[f;t] f+til 1+t-f}[.qetl.io.days_of[root;opts`range_from];.qetl.io.days_of[root;opts[`range_to]-1]];
        dates:distinct dates,span where (span<.z.d) and
            {[root;target;d] not ()~key hsym `$(string .Q.par[root;d;target]),"/"}[root;target] each span;
        / a visited day is written too, rows or not (#2186)
        .qetl.io.require_eod_done[root;target;dates]];
    / The window is cleared on opts`time_column - the column it was cut on, as
    / the output holds it (#972) - while the PARTITION is still by `time`. They
    / differ when a transform moves time (markouts: trade_time+horizon), and
    / then only the visited dates are cleared: the window's own days and the
    / batch's. A row an earlier run filed on a later day, which this batch no
    / longer produces, is not reached.
    o:opts,enlist[`target]!enlist target;
    plan:{[root;target;data;days;strategy;o;d]
        part:hsym `$(string .Q.par[root;d;target]),"/";
        rows:data where days=d;
        / A COPY, not the mapped table: the partition is about to be
        / rewritten from the result, and a result that still pointed into
        / the files being overwritten would read them mid-write.
        / Copied by indexing every column, not by -9!-8!: on KDB-X that
        / round trip returns a mapped splayed table still (so `in` throws
        / 'splay), and turns an enumerated sym into plain symbols (so the
        / splayed `set` below throws 'type). Indexing keeps the enumeration.
        existing:$[()~key part; 0#rows; flip {x til count x} each flip select from get part];
        (part;d;.qetl.io.plain .qetl.io.resolve[strategy;existing;rows;o])
        }[root;target;data;days;strategy;o] each dates;
    if[(`replace=strategy) and (not .qetl.io.on_peachq) and not any (`time,opts`time_column) in (),opts`row_key;
        plan,:.qetl.io.spill[root;target;data;opts;dates]];
    / STAGED, THEN SWAPPED. Writing `set` straight onto the live partition
    / rewrote it column by column while the HDB had it mapped: a kill part way
    / left columns of different lengths, and a reload in between mapped a
    / half-written table. Every date is written whole into the staging area
    / first - an error there leaves the HDB exactly as it was - and only then
    / renamed into place, one directory at a time.
    {[root;target;step] .qetl.io.stage[root;step 1;target;step 2]}[root;target] each plan;
    {[root;target;step] .qetl.io.swap[root;step 1;target]; `.qetl.io.touched upsert (root;step 1;target);}[root;target] each plan;
    count batch}

/ Private: the partition after a `replace window, without the window's late-filed twins:
/ rows whose key the window wrote AND whose time column lies inside the window.
/ .
/ Empty when there is nothing to do: the window ends at midnight on a day
/ already planned, on today or later, on a day with no such partition, or
/ on one holding none of the keys.
/ @param root the HDB root
/ @param target the table
/ @param data the window's rows, enumerated
/ @param opts the write's options: row_key and range_to
/ @param dates the dates already planned
/ @return () or one (part;date;rows) step, as write_hdb_keyed plans them
/ @private
.qetl.io.spill:{[root;target;data;opts;dates]
    d:.qetl.io.days_of[root;opts`range_to];
    part:hsym `$(string .Q.par[root;d;target]),"/";
    if[(d in dates) or (d>=.z.d) or ()~key part; :()];
    kc:(),opts`row_key;
    existing:flip {x til count x} each flip select from get part;
    / A twin is a row of the window filed late: its own time column still lies
    / inside [range_from, range_to). A key alone proves nothing - the next
    / day's row of the same account is a different row (#2197).
    t:existing opts`time_column;
    twin:(t>=opts`range_from) and t<opts`range_to;
    hit:twin and (kc#existing) in kc#data;
    keep:existing where not hit;
    $[count[keep]<count existing; enlist (part;d;.qetl.io.plain keep); ()]}

/ ------------------------------------------------------------ STAGING
/ .
/ A sibling of the HDB root, so every rename is within one filesystem and so
/ the HDB, which loads every directory under its root, never sees a staged or
/ retired table. `new/<date>/<table>` holds what is about to be swapped in,
/ `old/<date>/<table>` what was just swapped out. Both are empty between
/ writes; whatever a killed run left there is put right by sweep_staging.
/ .
/ The swap is two renames, not one: rename(2) cannot replace a non-empty
/ directory, and the call that exchanges two (renameat2) is Linux-only. So a
/ reader can, for an instant, find the table absent from that date - never
/ half-written - and a kill between the two renames leaves the old copy in
/ `old`, which the next recover restores, or the next write, flush, finish
/ or retire of that table, whichever comes first (restore_retired, #2496).

/ Private: the staging area for `root`, as a path string.
/ @param root the HDB root, a file symbol
/ @private
.qetl.io.staging:{[root] (1_string root),".staging"}

/ Private: one (date; table) under the staging area's `new or `old.
/ @private
.qetl.io.staged:{[root;kind;d;t] (.qetl.io.staging[root]),"/",string[kind],"/",string[d],"/",string t}

/ Private: a partition's table directory, as a path string - through .Q.par,
/ so a segmented HDB's par.txt is honoured.
/ @private
.qetl.io.part_path:{[root;d;t] 1_string .Q.par[root;d;t]}

/ Private: a path as one shell word (#2187). An HDB root may hold a space, and
/ an unquoted path split into several words made rm -rf delete a sibling.
/ @private
.qetl.io.sh:{[cmd;paths]
    paths:$[10h=type paths; enlist paths; paths];
    system cmd," "," " sv .qetl.job.bounded.state.shell_word each paths}

/ Private: the step marker of the stage-and-swap protocol. A no-op; the kill
/ tests replace it with a function that throws, which leaves the disk exactly
/ as a kill at that step would, because no cleanup follows a throw.
/ @param name the step just completed
/ @private
.qetl.io.step:{[name] (::)}

/ Private: write one date's resolved table into the staging area.
/ @return the staged path
/ @private
.qetl.io.stage:{[root;d;t;rows]
    p:.qetl.io.staged[root;`new;d;t];
    .qetl.io.sh["rm -rf";p];
    .qetl.io.sh["mkdir -p";p];
    .qetl.io.step`staged_dir;
    .qetl.io.write_splay[root;hsym `$p,"/";rows];
    .qetl.io.step`staged;
    p}

/ Private: stage a COPY of the live partition, for finish to sort and attribute
/ there (#2184). The copy is of the files as they are, so enumerations,
/ attributes and compression carry over.
/ @return the staged path
/ @private
.qetl.io.stage_copy:{[root;d;t]
    p:.qetl.io.staged[root;`new;d;t];
    .qetl.io.sh["rm -rf";p];
    .qetl.io.sh["mkdir -p";p];
    .qetl.io.sh["cp -R";(.qetl.io.part_path[root;d;t],"/.";p)];
    .qetl.io.step`staged;
    p}

/ Private: rename a staged table into its partition, retiring the old one.
/ @private
.qetl.io.swap:{[root;d;t]
    live:.qetl.io.part_path[root;d;t];
    old:.qetl.io.staged[root;`old;d;t];
    / A retired copy whose live table is missing is the only copy of the
    / partition (#2496): a writer killed between the renames left it.
    / Refused, never deleted - restore_retired puts it back first.
    if[(()~key hsym `$live) and not ()~key hsym `$old;
        '"hdb: ",string[t]," on ",string[d]," is only in ",old," - a killed write left it there; ",
         "refusing to delete it (run recover)"];
    .qetl.io.sh["rm -rf";old];
    .qetl.io.sh["mkdir -p";(.qetl.io.staging[root]),"/old/",string d];
    / The date directory, whatever its segment: the live path less its table.
    .qetl.io.sh["mkdir -p";(neg 1+count string t)_live];
    if[not ()~key hsym `$live; .qetl.io.sh["mv";(live;old)]];
    .qetl.io.step`live_retired;
    .qetl.io.sh["mv";(.qetl.io.staged[root;`new;d;t];live)];
    .qetl.io.step`swapped_in;
    .qetl.io.sh["rm -rf";old];
    }

/ Remove one date's table from the HDB, through the staging area.
/ .
/ The live directory is renamed into `old` and only then deleted, so a reader
/ sees the table whole or absent - never a half-removed one - and a kill
/ between the rename and the delete leaves it in `old`, which sweep_staging
/ puts back (the partition reads as it did, and retention, which supersedes
/ coverage BEFORE it removes, simply removes it again). A date directory left
/ with no table at all is removed too: the HDB loads every directory under its
/ root and an empty one fails a reload.
/ .
/ Does not tell a running HDB to reload; that needs the stack (see on_ready).
/ @param root the HDB root, a file symbol
/ @param d the date
/ @param t the table
/ @return 1b when a table was removed, 0b when there was none
/ @eg .qetl.io.retire[`:/tmp/qio_eg_hdb;2026.01.02;`iodeals]
.qetl.io.retire:{[root;d;t]
    .qetl.io.restore_retired[root;t];
    live:.qetl.io.part_path[root;d;t];
    if[()~key hsym `$live; :0b];
    old:.qetl.io.staged[root;`old;d;t];
    .qetl.io.sh["rm -rf";old];
    .qetl.io.sh["mkdir -p";(.qetl.io.staging[root]),"/old/",string d];
    .qetl.io.sh["mv";(live;old)];
    .qetl.io.sh["rm -rf";old];
    datedir:(neg 1+count string t)_live;
    if[0=count key hsym `$datedir; .qetl.io.sh["rmdir";datedir]];
    1b}

/ Private: put right what a run killed mid-write left in the staging area,
/ for one table.
/ .
/ An `old` table whose partition is missing was swapped out and its
/ replacement never swapped in: it is put back, so the partition reads as it
/ did before the write began - the window was never covered, so the next run
/ writes it again. An `old` table whose partition is present was replaced
/ and only its removal was lost. A `new` table is a write that never reached
/ its swap, and goes.
/ .
/ ONE TABLE, because several backfills may write one HDB at once, each its
/ own table: sweeping another process's staging would restore or delete a
/ write it is in the middle of.
/ @param root the HDB root
/ @param target the table to sweep
/ @return how many tables were restored
/ @private
.qetl.io.sweep_staging:{[root;target]
    s:.qetl.io.staging[root];
    if[()~key hsym `$s; :0];
    under:{[s;kind] $[()~key hsym `$s,"/",kind; `symbol$(); key hsym `$s,"/",kind]}[s];
    {[s;target;d] .qetl.io.sh["rm -rf";s,"/new/",string[d],"/",string target]}[s;target] each under "new";
    dates:under "old";
    pairs:{[target;d] (d;target)}[target] each dates where
        {[s;target;d] not ()~key hsym `$s,"/old/",string[d],"/",string target}[s;target] each dates;
    restored:{[root;pr]
        d:"D"$string pr 0;
        live:.qetl.io.part_path[root;d;pr 1];
        old:.qetl.io.staged[root;`old;d;pr 1];
        $[()~key hsym `$live;
            [.qetl.io.sh["mkdir -p";(neg 1+count string pr 1)_live]; .qetl.io.sh["mv";(old;live)]; 1];
            [.qetl.io.sh["rm -rf";old]; 0]]}[root] each pairs;
    / "j"$ before sum: with nothing to restore, `each` over no pairs gives a
    / general empty list, `sum` leaves it `()`, and recover's `restored>0`
    / threw 'type - every recover of a table with no old staging failed.
    sum "j"$restored}

/ Private: sweep_staging for one table at the start of a write, flush, finish
/ or retire, not only at a run's start (#2496).
/ .
/ Every writer holds the root's lock for the whole call, so anything in staging
/ for this table now was left by a writer that died. A kill between a swap's
/ two renames leaves the date without the table and every row of it in `old`.
/ A sibling worker already mid-run, whose own recover ran before the kill, read
/ that date as empty, staged only its window, and its swap deleted the `old`
/ copy: the rest of the partition was gone for good.
/ @return how many tables were restored
/ @private
.qetl.io.restore_retired:{[root;target]
    restored:.qetl.io.sweep_staging[root;target];
    if[restored>0;
        @[{.qetl.log.ch.warn[`storage;"restored a partition a killed write left in staging";x]};
          `table`restored!(target;restored);{[e] (::)}]];
    restored}

/ Private: set aside a table whose .d lists a column that has no file (#2185).
/ .
/ That is a first write a kill cut short (before the first write was staged) or
/ a .Q.chk fill a kill cut short: the rows it held were never covered, so the
/ next run writes them again. The directory is MOVED to the staging area's
/ quarantine, not deleted - whatever else is in it stays inspectable - and the
/ date then reads as absent, which an HDB can load.
/ @return 1b when it set a table aside
/ @private
.qetl.io.quarantine_incomplete:{[root;d;t]
    base:.qetl.io.part_path[root;d;t];
    if[()~key hsym `$base,"/.d"; :0b];
    c:get hsym `$base,"/.d";
    if[all {[base;c] not ()~key hsym `$base,"/",string c}[base] each c; :0b];
    q:(.qetl.io.staging[root]),"/quarantine/",string[d],"/",string t;
    .qetl.io.sh["rm -rf";q];
    .qetl.io.sh["mkdir -p";(.qetl.io.staging[root]),"/quarantine/",string d];
    .qetl.io.sh["mv";(base;q)];
    1b}

/ Private: trim a partition whose columns have different lengths back to the
/ shortest, the state a kill part way through an APPEND leaves (write_hdb
/ upserts column by column). Every column ends at the same row again, so the
/ partition can be read; the rows dropped belonged to a window that was
/ never covered, which the next run writes again. recover then queues it for
/ finishing. A table with a column file missing is set aside instead
/ (quarantine_incomplete), and is not "repaired": there is nothing left to finish.
/ @return 1b when it repaired something
/ @private
.qetl.io.repair_torn:{[root;d;t]
    base:.qetl.io.part_path[root;d;t];
    if[()~key hsym `$base,"/.d"; :0b];
    if[.qetl.io.quarantine_incomplete[root;d;t]; :0b];
    c:get hsym `$base,"/.d";
    n:{[base;c] count get hsym `$base,"/",string c}[base] each c;
    if[1=count distinct n; :0b];
    m:min n;
    {[base;m;c] f:hsym `$base,"/",string c; f set m#get f}[base;m] each c where n>m;
    1b}

/ Private: the declared sort rule of `tbl` as column -> attribute in sort order
/ (` for none), or the empty dictionary when the table declares none (#1861).
/ .qetl.plant is loaded after this file, so it is read at call time, and a
/ process that never loaded it - or a table the plant does not know, as a
/ scoped writer's - has no rule: the default applies.
/ @param tbl the table
/ @return a dictionary of sort column to attribute, empty when nothing is declared
/ @private
.qetl.io.declared_rule:{[tbl]
    if[not `sort_rules in key `.qetl.plant; :(`symbol$())!`symbol$()];
    exec column!att from .qetl.plant.sort_rules where table=tbl}

/ Private: how a partition of `tbl` with columns `c` is finished: the declared
/ sort rule, else sym then time with p# on sym - a table with no sym gets no
/ attribute, and xasc leaves s# on its first sort column.
/ @param tbl the table
/ @param c the partition's columns
/ @return a dictionary of sort column to attribute, in sort order
/ @private
.qetl.io.sort_rule:{[tbl;c]
    if[count r:.qetl.io.declared_rule[tbl]; :r];
    order:`sym`time inter c;
    order!{$[x=`sym;`p;`]} each order}

/ Private: sort a splayed partition on PeachQ by these columns, file by file.
/ .
/ PeachQ's xasc over a path re-enumerates the symbol columns against a `sym` file it makes beside
/ the table (#2285), which leaves their indices relative to that file and not to the root's. So
/ the permutation is worked out from the decoded key columns and applied to each column file as
/ it is, enumerated indices untouched.
/ @private
.qetl.io.sort_files:{[part;order_cols]
    base:-1_string part;
    c:get hsym `$base,"/.d";
    key_cols:{[base;col] v:get hsym `$base,"/",string col; $[type[v] within 20 76h; value v; v]}[base] each order_cols;
    idx:exec ix from order_cols xasc update ix:i from flip order_cols!key_cols;
    {[base;idx;col] f:hsym `$base,"/",string col; f set (get f) idx}[base;idx] each c;}

/ Private: sort a partition by its rule and put each attribute on, which is every
/ column file this finish rewrites.
/ @private
.qetl.io.sort_and_attribute:{[part;rule]
    if[count rule; $[.qetl.io.on_peachq; .qetl.io.sort_files[part;key rule]; (key rule) xasc part]];
    / PeachQ keeps no attribute (.qetl.io.on_peachq).
    if[not .qetl.io.on_peachq;
        {[part;col;a] if[not null a; @[part;col;#[a;]]]}[part]'[key rule;value rule]];
    }

/ Private: give a finished partition its row numbers, for a table that declares a row-number column
/ (.qetl.plant.row_ids): each row's position in the partition AS NOW SORTED. Written after the sort,
/ never before, because the sort is what fixes a row's position; and in the staged copy with it, so
/ the swap puts the sorted rows and their numbers in together.
/ @param part the staged table directory, a file symbol with a trailing slash
/ @param tbl the table
/ @private
.qetl.io.assign_row_ids:{[part;tbl]
    if[not `row_ids in key `.qetl.plant; :(::)];
    if[not tbl in key .qetl.plant.row_ids; :(::)];
    col:.qetl.plant.row_ids tbl;
    base:-1_string part;
    c:get hsym `$base,"/.d";
    others:c except col;
    if[(not col in c) or 0=count others; :(::)];
    (hsym `$base,"/",string col) set til count get hsym `$base,"/",string first others;}

/ Private: sort a staged partition by its rule, put each attribute on, and number its rows.
/ @private
.qetl.io.order_partition:{[part;rule;tbl]
    .qetl.io.sort_and_attribute[part;rule];
    .qetl.io.assign_row_ids[part;tbl]}

/ Private: what this partition is compressed with when it is finished: the
/ table's write-time triple, or the empty list. Never on PeachQ, which has no
/ -19! and no .z.zd compression (#2153); and nothing where the policy module is
/ not loaded.
/ @private
.qetl.io.write_triple:{[tbl;part]
    if[.qetl.io.on_peachq; :()];
    if[not `for_write in key `.qetl.compression; :()];
    .qetl.compression.for_write[tbl;part]}

/ Private: sort and attribute these partitions of `root`, fill every
/ partition with every table, and forget them.
/ @param root the HDB directory
/ @param todo a table of dt and tbl
/ @return how many partitions were finished
/ @private
.qetl.io.finish_parts:{[root;todo]
    / Nothing to finish: and PeachQ throws 'nyi on each-both over empty lists.
    if[0=count todo; :0];
    .qetl.io.restore_retired[root;] each distinct todo`tbl;
    {[root;d;t]
        / Gone since it was queued (quarantined by recover, or retired): nothing to finish.
        if[()~key hsym `$.qetl.io.part_path[root;d;t],"/.d"; :(::)];
        c:get hsym `$.qetl.io.part_path[root;d;t],"/.d";
        rule:.qetl.io.sort_rule[t;c];
        / SORTED IN STAGING, NEVER IN PLACE (#2184). xasc rewrites a splayed
        / table one column file at a time, so a kill part way through left some
        / columns permuted and the rest not - every column the same length,
        / nothing to detect - and the next finish then "finished" the scramble.
        / The sort, the attributes and the compression run on a copy in staging;
        / the swap puts the finished copy in with two renames. A kill leaves the
        / old partition or the new one, and sweep_staging restores or discards.
        base:.qetl.io.stage_copy[root;d;t];
        part:hsym `$base,"/";
        / WRITE-TIME COMPRESSION (#2151), when the table's policy says `write or
        / `both. The sort is the write that touches every column file, so it is
        / what runs under .z.zd - the way TorQ's own end-of-day sort does
        / (.wdb.setcompression) - and .z.zd is put back after, so nothing else
        / this process writes is compressed. A partition already compressed
        / gets no triple: it is not compressed again, and xasc leaves it as it is.
        zd:.qetl.io.write_triple[t;part];
        .qetl.compression.with_zd[zd;.qetl.io.order_partition;(part;rule;t)];
        if[(count zd) and count rule; .qetl.compression.apply_columns[t;part]];
        .qetl.io.step`sorted;
        .qetl.io.swap[root;d;t];
        }[root]'[todo`dt;todo`tbl];
    / Not over a native stack's root (io_hdb_scoped.q): it would fill dates and
    / tables nobody asked about.
    / .Q.chk fills EVERY partition of the root, so it must not run while one
    / waits for end of day: its empty tables there are what makes movetohdb abort (#2186).
    if[not .qetl.io.on_peachq or .qetl.io.is_scoped root;
        $[count w:.qetl.io.eod_waiting[root;.qetl.io.part_dates root];
            .qetl.log.ch.warn[`eod;"not filling the HDB's missing tables - a partition is still waiting for end of day";enlist[`waiting]!enlist w];
            .Q.chk root]];
    `.qetl.io.touched set .qetl.io.touched except ([] hdb_root:count[todo]#root),'todo;
    count todo}

/ Private: is this partition finished, as finish_parts leaves it?
/ .
/ Read off the file, because `touched` lives in the process that wrote it and
/ dies with it. finish_parts puts p# on sym, and xasc leaves s# on time for a
/ table without sym; write_hdb takes p# OFF when it appends. So a partition
/ with neither was written and never finished. One with neither column has
/ nothing to judge by and reads as finished, as does one that does not exist.
/ @private
.qetl.io.is_finished:{[root;d;t]
    base:string .Q.par[root;d;t];
    if[()~key hsym `$base,"/.d"; :1b];
    c:get hsym `$base,"/.d";
    / A declared rule is judged by its own attributes, or by its order where
    / it declares none (and always on PeachQ, which keeps none).
    if[count r:.qetl.io.declared_rule[t];
        / `#`, not `r where`: indexing a dictionary by its keys gives a list of
        / values, whose key is no column name (#1891)
        have:(where not null r)#r;
        :$[.qetl.io.on_peachq or 0=count have;
            .qetl.io.in_order[base;key r];
            all {[base;col;a] a=attr get hsym `$base,"/",string col}[base]'[key have;value have]]];
    if[.qetl.io.on_peachq; :.qetl.io.in_order[base;`sym`time inter c]];
    $[`sym in c; `p=attr get hsym `$base,"/sym";
      `time in c; `s=attr get hsym `$base,"/time";
      1b]}

/ Private: is the partition at `base` sorted by `order`, finish's sort columns?
/ How PeachQ, which keeps no attribute, tells a finished partition.
/ @private
.qetl.io.in_order:{[base;order]
    if[0=count order; :1b];
    t:flip order!{[base;x] get hsym `$base,"/",string x}[base] each order;
    t~order xasc t}

/ Private: queue the partitions of `target` in [range_from;range_to) that
/ were written and never finished, for the next finish.
/ @private
.qetl.io.recover_hdb:{[root;target;from_ts;to_ts]
    if[not from_ts<to_ts; :0];
    / First what a killed keyed write left in staging, so the partitions read
    / below are the ones the HDB will map.
    restored:.qetl.io.sweep_staging[root;target];
    span:{[f;t] f+til 1+t-f}[.qetl.io.days_of[root;from_ts];.qetl.io.days_of[root;to_ts-1]];
    / A table with a column file missing is set aside first (#2185), so the trim
    / below never meets a column it cannot read.
    quarantined:span where .qetl.io.quarantine_incomplete[root;;target] each span;
    torn:span where .qetl.io.repair_torn[root;;target] each span;
    if[(restored>0) or (count torn) or count quarantined;
        @[{.qetl.log.ch.warn[`storage;"repaired partitions a killed write left behind";x]};
          `restored`trimmed`quarantined!(restored;torn;quarantined);{[e] (::)}]];
    / A trimmed partition is queued whatever its attributes say: trimming
    / rewrote only the long columns, so p#sym can survive on a partition that
    / still needs re-sorting.
    ds:distinct torn,span where not .qetl.io.is_finished[root;;target] each span;
    `.qetl.io.touched upsert ([] hdb_root:count[ds]#root; dt:ds; tbl:count[ds]#target);
    count ds}

/ Private: every partition this root was written to.
/ @private
.qetl.io.finish_hdb:{[root;ignored]
    .qetl.io.finish_parts[root;distinct select dt, tbl from .qetl.io.touched where hdb_root=root]}

/ Private: the partitions of this root dated wholly before `upto`, and how
/ many of its partitions are still open after them.
/ @private
.qetl.io.flush_hdb:{[root;upto]
    n:.qetl.io.finish_parts[root;distinct select dt, tbl from .qetl.io.touched where hdb_root=root, dt<.qetl.io.days_of[root;upto]];
    `finished`pending!(n;count distinct select dt, tbl from .qetl.io.touched where hdb_root=root)}

\d .
