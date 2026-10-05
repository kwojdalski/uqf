/ io_hdb.q - the HDB writer behind .qetl.io's manager contract: append each
/ window to its date's partition, then the atomic stage-and-swap that finishes
/ a partition, and the crash recovery that finishes what a killed run left.
/ .
/ Moved out of io_manager.q (#618) unchanged, in the same namespace. It is the
/ riskiest code the io layer has, and it sat beside the memory and discard
/ adapters, which are two lines each; on its own it is reviewed as what it is.
/ io_manager.q keeps the contract, the write strategies and the dispatch.
/ Loaded straight after io_manager.q (src/etl/init.q), so load order is as it was.

\d .qetl.io

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
/ leads with it (src/etl/plant_tables.q), and databento_book's
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
/ schema-driven repair (scripts/gates/fill_hdb_partitions.q) runs on every
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
/ Not safe to run beside end-of-day: both append to the HDB's sym file.

/ Partitions written and not yet finished: root, date, table.
touched:([] hdb_root:`symbol$(); dt:`date$(); tbl:`symbol$())

/ An HDB writer for one root and partition column.
/ @param root the HDB directory, as a file symbol, e.g. `:/data/hdb
/ @param partition_col the timestamp column that becomes `time`, and so picks
/   the partition, for a batch that has no `time` of its own, e.g. `deal_time
/ @return a manager carrying write and finish
/ @throws error when root is not a file symbol or partition_col not a symbol
/ @eg .qetl.io.hdb[`:/tmp/qio_eg_hdb;`deal_time]
hdb:{[root;partition_col]
    if[not (-11h=type root) and ":"=first string root;
        '"hdb: root must be a file symbol, e.g. `:/data/hdb"];
    if[not -11h=type partition_col;
        '"hdb: partition_col must be a symbol naming a timestamp column"];
    / finish_hdb takes a second, ignored argument so that finish_hdb[root;] is a
    / PROJECTION: on a one-argument function, finish_hdb[root] would be a call.
    `write`write_keyed`flush`finish`recover!(write_hdb[root;partition_col;;];write_hdb_keyed[root;partition_col;;;];
        flush_hdb[root;];finish_hdb[root;];recover_hdb[root;;;])}

/ Private: append one window into its date partitions.
write_hdb:{[root;partition_col;target;batch]
    if[0=count batch; :0];
    data:.Q.en[root;hdb_rows[partition_col;target;batch]];
    days:`date$data`time;
    {[root;target;data;days;d]
        part:hsym `$(string .Q.par[root;d;target]),"/";
        rows:data where days=d;
        existing:key part;
        / A partition a previous run finished carries p#sym; appending out of
        / order to it is not safe, so the attribute comes off here and
        / finish sorts and puts it back.
        if[`sym in existing; @[part;`sym;`#]];
        $[()~existing; part set rows; part upsert rows];
        `.qetl.io.touched upsert (root;d;target);
        }[root;target;data;days] each distinct days;
    count batch}

/ Private: the batch with the plant's `time` column, refused when it cannot
/ be partitioned or would land in the tickerplant's dates.
hdb_rows:{[partition_col;target;batch]
    if[not any (`time;partition_col) in cols batch;
        '"hdb: ",string[target],"'s batch has neither time nor ",string[partition_col]," to partition by"];
    data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist partition_col]];
    days:`date$data`time;
    if[any null days;
        '"hdb: ",string[target]," has rows with a null time - no partition to put them in"];
    / Names the REMEDY, not only the refusal. The rule is a boundary - today
    / is the plant's, yesterday and earlier are the backfill's, and
    / end-of-day is the handover - but an operator meeting it for the first
    / time meets it as a wall, after the fetch and the quality gate have both
    / passed, which is a confusing place to learn a policy.
    if[any days>=.z.d;
        '"hdb: ",string[target]," has rows dated ",string[max days],
         " - today and later belong to the tickerplant and end-of-day, not a backfill.",
         " Backfill a range ending on or before ",string[.z.d-1],
         ", or let today's rows arrive through the live path"];
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
write_hdb_keyed:{[root;partition_col;target;batch;opts]
    strategy:require_strategy opts`on_conflict;
    if[`append=strategy; :write_hdb[root;partition_col;target;batch]];
    / Shaped even when empty: a `replace of a day the source has emptied
    / still needs the target's columns to resolve against.
    data:.Q.en[root;hdb_rows[partition_col;target;batch]];
    days:`date$data`time;
    dates:distinct days;
    if[`replace=strategy;
        span:{[f;t] f+til 1+t-f}[`date$opts`range_from;`date$opts[`range_to]-1];
        dates:distinct dates,span where (span<.z.d) and
            {[root;target;d] not ()~key hsym `$(string .Q.par[root;d;target]),"/"}[root;target] each span];
    o:opts,`target`time_column!(target;`time);
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
        (part;d;plain resolve[strategy;existing;rows;o])
        }[root;target;data;days;strategy;o] each dates;
    / STAGED, THEN SWAPPED. Writing `set` straight onto the live partition
    / rewrote it column by column while the HDB had it mapped: a kill part way
    / left columns of different lengths, and a reload in between mapped a
    / half-written table. Every date is written whole into the staging area
    / first - an error there leaves the HDB exactly as it was - and only then
    / renamed into place, one directory at a time.
    {[root;target;step] stage[root;step 1;target;step 2]}[root;target] each plan;
    {[root;target;step] swap[root;step 1;target]; `.qetl.io.touched upsert (root;step 1;target);}[root;target] each plan;
    count batch}

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
/ `old`, which the next recover restores.

/ Private: the staging area for `root`, as a path string.
/ @param root the HDB root, a file symbol
staging:{[root] (1_string root),".staging"}

/ Private: one (date; table) under the staging area's `new or `old.
staged:{[root;kind;d;t] (staging root),"/",string[kind],"/",string[d],"/",string t}

/ Private: a partition's table directory, as a path string - through .Q.par,
/ so a segmented HDB's par.txt is honoured.
part_path:{[root;d;t] 1_string .Q.par[root;d;t]}

/ Private: write one date's resolved table into the staging area.
/ @return the staged path
stage:{[root;d;t;rows]
    p:staged[root;`new;d;t];
    system"rm -rf ",p;
    system"mkdir -p ",p;
    (hsym `$p,"/") set rows;
    p}

/ Private: rename a staged table into its partition, retiring the old one.
swap:{[root;d;t]
    live:part_path[root;d;t];
    old:staged[root;`old;d;t];
    system"rm -rf ",old;
    system"mkdir -p ",(staging root),"/old/",string d;
    / The date directory, whatever its segment: the live path less its table.
    system"mkdir -p ",(neg 1+count string t)_live;
    if[not ()~key hsym `$live; system"mv ",live," ",old];
    system"mv ",staged[root;`new;d;t]," ",live;
    system"rm -rf ",old;
    }

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
sweep_staging:{[root;target]
    s:staging root;
    if[()~key hsym `$s; :0];
    under:{[s;kind] $[()~key hsym `$s,"/",kind; `symbol$(); key hsym `$s,"/",kind]}[s];
    {[s;target;d] system"rm -rf ",s,"/new/",string[d],"/",string target}[s;target] each under "new";
    dates:under "old";
    pairs:{[target;d] (d;target)}[target] each dates where
        {[s;target;d] not ()~key hsym `$s,"/old/",string[d],"/",string target}[s;target] each dates;
    restored:{[root;pr]
        d:"D"$string pr 0;
        live:part_path[root;d;pr 1];
        old:staged[root;`old;d;pr 1];
        $[()~key hsym `$live;
            [system"mkdir -p ",(neg 1+count string pr 1)_live; system"mv ",old," ",live; 1];
            [system"rm -rf ",old; 0]]}[root] each pairs;
    / "j"$ before sum: with nothing to restore, `each` over no pairs gives a
    / general empty list, `sum` leaves it `()`, and recover's `restored>0`
    / threw 'type - every recover of a table with no old staging failed.
    sum "j"$restored}

/ Private: trim a partition whose columns have different lengths back to the
/ shortest, the state a kill part way through an APPEND leaves (write_hdb
/ upserts column by column). Every column ends at the same row again, so the
/ partition can be read; the rows dropped belonged to a window that was
/ never covered, which the next run writes again. recover then queues it for
/ finishing.
/ @return 1b when it repaired something
repair_torn:{[root;d;t]
    base:part_path[root;d;t];
    if[()~key hsym `$base,"/.d"; :0b];
    c:get hsym `$base,"/.d";
    n:{[base;c] count get hsym `$base,"/",string c}[base] each c;
    if[1=count distinct n; :0b];
    m:min n;
    {[base;m;c] f:hsym `$base,"/",string c; f set m#get f}[base;m] each c where n>m;
    1b}

/ Private: sort and attribute these partitions of `root`, fill every
/ partition with every table, and forget them.
/ @param root the HDB directory
/ @param todo a table of dt and tbl
/ @return how many partitions were finished
finish_parts:{[root;todo]
    {[root;d;t]
        base:string .Q.par[root;d;t];
        part:hsym `$base,"/";
        c:get hsym `$base,"/.d";
        order:(`sym`time inter c);
        if[count order; order xasc part];
        if[`sym in c; @[part;`sym;`p#]];
        }[root]'[todo`dt;todo`tbl];
    if[count todo; .Q.chk root];
    `.qetl.io.touched set touched except ([] hdb_root:count[todo]#root),'todo;
    count todo}

/ Private: is this partition finished, as finish_parts leaves it?
/ .
/ Read off the file, because `touched` lives in the process that wrote it and
/ dies with it. finish_parts puts p# on sym, and xasc leaves s# on time for a
/ table without sym; write_hdb takes p# OFF when it appends. So a partition
/ with neither was written and never finished. One with neither column has
/ nothing to judge by and reads as finished, as does one that does not exist.
is_finished:{[root;d;t]
    base:string .Q.par[root;d;t];
    if[()~key hsym `$base,"/.d"; :1b];
    c:get hsym `$base,"/.d";
    $[`sym in c; `p=attr get hsym `$base,"/sym";
      `time in c; `s=attr get hsym `$base,"/time";
      1b]}

/ Private: queue the partitions of `target` in [range_from;range_to) that
/ were written and never finished, for the next finish.
recover_hdb:{[root;target;from_ts;to_ts]
    if[not from_ts<to_ts; :0];
    / First what a killed keyed write left in staging, so the partitions read
    / below are the ones the HDB will map.
    restored:sweep_staging[root;target];
    span:{[f;t] f+til 1+t-f}[`date$from_ts;`date$to_ts-1];
    torn:span where repair_torn[root;;target] each span;
    if[(restored>0) or count torn;
        @[{.qetl.log.warn[`qetl.io;"repaired partitions a killed write left behind";x]};
          `restored`trimmed!(restored;torn);{[e] (::)}]];
    / A trimmed partition is queued whatever its attributes say: trimming
    / rewrote only the long columns, so p#sym can survive on a partition that
    / still needs re-sorting.
    ds:distinct torn,span where not is_finished[root;;target] each span;
    `.qetl.io.touched upsert ([] hdb_root:count[ds]#root; dt:ds; tbl:count[ds]#target);
    count ds}

/ Private: every partition this root was written to.
finish_hdb:{[root;ignored]
    finish_parts[root;distinct select dt, tbl from touched where hdb_root=root]}

/ Private: the partitions of this root dated wholly before `upto`, and how
/ many of its partitions are still open after them.
flush_hdb:{[root;upto]
    n:finish_parts[root;distinct select dt, tbl from touched where hdb_root=root, dt<`date$upto];
    `finished`pending!(n;count distinct select dt, tbl from touched where hdb_root=root)}

\d .
