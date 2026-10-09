/ source_local_hdb.q - the local transport: reading an HDB directory straight from its files.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ---------------------------------------------------------------- LOCAL
/ .
/ A `local source reads a kdb+ HDB directory on this machine straight from
/ its files: no process serves it, so no IPC and no credential beyond the
/ path. For an HDB that is not running, or one too large to be worth a
/ process, or a deployment that ships the files alone.
/ .
/ NOTHING IS LOADED GLOBALLY. `\l` would map the whole database at this
/ process's root and change its working directory, and a backfill writing
/ its own HDB cannot afford either. Tables are read one date partition at a
/ time, column by column.
/ .
/ EACH ENUMERATION IS DECODED AGAINST THE HDB'S OWN DOMAIN FILE. A plain
/ `get` of an enumerated column resolves it against whatever domain of that
/ name THIS process has loaded - for `sym`, the one .qetl.io.hdb keeps for
/ the HDB it writes - so reading another HDB that way silently returns the
/ wrong symbols. Each enumerated column is decoded by its positions against
/ the file named after ITS domain (`key` of the column: usually `sym`, but
/ a column can be enumerated against any domain), and a missing or too-short
/ domain file is refused rather than decoded to blanks.

/ ATTRIBUTES ARE NOT KEPT. What a read returns is decoded and razed from
/ partitions, so a p#, s# or g# on disk is gone from it. Filtering does not
/ need them; a caller that relies on one re-applies it.
/ .
/ WHAT IT COSTS. A read of [from;to) reads every partition that range touches,
/ WHOLE, every column unless the query names the ones it needs - read[(`trades;
/ `time`sym`px);from;to] reads three column files where read[`trades;from;to]
/ reads them all. An hourly backfill over one day therefore reads that day's
/ partition 24 times. That is the trade-off of reading files with no process
/ in between: for repeated, selective queries over a large HDB, serve it and
/ use an `ipc source instead.
/ .
/ WHAT LAYOUTS IT READS. Date partitions, directly under the root or across the
/ segments a par.txt names (a segmented HDB), with the sym file at the root -
/ native kdb+ layout. Month, year and int partitions are refused by name
/ rather than read as no data.

/ The directory a `local credential names, validated: a directory, holding a
/ sym file, a par.txt or at least one date partition, every segment present,
/ and partitioned by date. Called by .qetl.job.bounded.connect, so a
/ configured path that is wrong FAILS - it never falls back to the fixture,
/ which only an unset credential selects.
/ @param path the credential: an absolute path, as a string
/ @return the directory, as a file symbol - the source's "handle"
/ @throws error naming the path and what is wrong with it
/ @eg @[.qetl.source.local_root;"/no/such/hdb";{x}] like "*does not exist*"  ->  1b
local_root:{[path]
    if[0=count path; '"local: the credential is empty - set it to the HDB directory's path"];
    root:hsym `$path;
    k:key root;
    if[()~k; '"local: ",path," does not exist"];
    if[-11h=type k; '"local: ",path," is a file, not an HDB directory"];
    / Every segment and the layout are checked here, at connect, rather than
    / on the first window that happens to need them.
    ds:local_dates[root;-0Wd;0Wd];
    if[not (`sym in k) or (`par.txt in k) or 0<count ds;
        '"local: ",path," holds no sym file, no par.txt and no date partition - is it an HDB root?"];
    root}

/ The directories that hold `root`'s partitions: the root itself, or, for a
/ segmented HDB, every directory its par.txt names. A relative segment is
/ taken from the root, as kdb+ resolves it once \l has made the root the
/ working directory.
/ @param root the HDB directory, as a file symbol
/ @return the segment directories, as file symbols
/ @throws error naming a segment that is missing, unreadable or a file
local_segments:{[root]
    p:` sv root,`par.txt;
    if[()~key p; :enlist root];
    lines:{x where 0<count each x} trim each read0 p;
    if[0=count lines; '"local: ",(1_string p)," names no segments"];
    segs:{[root;l] hsym `$($["/"=first l; l; (1_string root),"/",l])}[root] each lines;
    {[p;s]
        k:key s;
        if[()~k; '"local: segment ",(1_string s)," named in ",(1_string p)," does not exist or cannot be read"];
        if[-11h=type k; '"local: segment ",(1_string s)," named in ",(1_string p)," is a file, not a directory"];
      }[p] each segs;
    segs}

/ Private: refuse a segment partitioned by month, year or int. Their
/ directories are not dates, so before this they were skipped in silence and
/ every window read as empty.
/ @private
local_check_layout:{[seg]
    ns:string key seg;
    cand:ns where {all x in "0123456789."} each ns;
    bad:cand where null "D"$cand;
    if[count bad;
        '"local: ",(1_string seg)," is partitioned by ",$[(first bad) like "????.??"; "month"; "year or int"],
         " (",first[bad],") - only date-partitioned HDBs are read; serve it and use an ipc source"];
    }

/ Every date partition of `root` within [d0;d1], across its segments: one row
/ per (date; directory), by date and then segment order. A date found in two
/ segments is two rows, and a read takes both, as a mapped HDB does.
/ @param root the HDB directory, as a file symbol
/ @param d0 first date, inclusive
/ @param d1 last date, inclusive
/ @return a table of date and dir, the partition directory as a file symbol
local_parts:{[root;d0;d1]
    segs:local_segments[root];
    local_check_layout each segs;
    t:raze {[seg]
        k:key seg;
        ds:"D"$string k;
        ok:where not null ds;
        ([] date:ds ok; dir:{` sv x,y}[seg] each k ok)} each segs;
    `date xasc select from t where date within (d0;d1)}

/ The date partitions present under `root`, within [d0;d1], in order - every
/ segment's, once each.
/ @param root the HDB directory, as a file symbol
/ @param d0 first date, inclusive
/ @param d1 last date, inclusive
/ @return the dates, ascending
local_dates:{[root;d0;d1] distinct exec date from local_parts[root;d0;d1]}

/ Private: an enumerated column, decoded against its own domain's file under
/ `root`. Refuses a domain file that is missing, or too short for the
/ positions the column holds - either would decode to blank symbols.
/ @param root the HDB directory, as a file symbol
/ @param c the column's name, for the error
/ @param v the column as read: an enumeration
/ @return the column as plain symbols
/ @private
local_decode:{[root;c;v]
    dom:key v;
    f:` sv root,dom;
    if[()~key f; '"local: column ",string[c]," is enumerated against `",string[dom],", but ",(1_string f)," does not exist"];
    vals:get f;
    if[11h<>type vals; '"local: ",(1_string f)," is not a symbol list - not a domain file"];
    ix:"j"$v;
    if[(count ix) and (max ix)>=count vals;
        '"local: column ",string[c]," needs ",string[1+max ix]," entries of `",string[dom],", and ",(1_string f)," holds ",string count vals];
    vals ix}

/ Private: columns `cs` of the splayed table at `base`, their first `n` rows
/ (0N for all), every enumeration decoded against its own domain's file - a
/ nested column of enumerations row by row.
/ @private
local_columns:{[root;base;cs;n]
    {[root;base;n;c]
        v:get hsym `$base,"/",string c;
        if[not null n; v:n sublist v];
        dec:{[root;c;x] $[(type x) within 20 76h; local_decode[root;c;x]; x]}[root;c];
        $[(type v) within 20 76h; dec v; (type v) in 0 77h; dec each v; v]
      }[root;base;n] each cs}

/ Private: one partition of one table, as a plain in-memory table with the
/ partition's `date` first (as a select from a mapped HDB has it). `want`
/ names the columns to read - every one when empty - and `n` how many rows
/ (0N for all). Empty list when that partition has no such table.
/ @private
local_partition:{[root;table;want;n;part]
    base:string ` sv part[`dir],table;
    if[()~key hsym `$base,"/.d"; :()];
    cs:get hsym `$base,"/.d";
    if[count want;
        absent:want except cs,`date;
        if[count absent;
            '"local: ",string[table]," in ",(1_base)," has no column(s) ",", " sv string absent];
        cs:want except `date];
    `date xcols update date:part`date from flip cs!local_columns[root;base;cs;n]}

/ The rows of `table` from every date partition that [from_ts;to_ts) touches
/ - whole partitions, to be filtered by the caller - read as
/ local_partition reads them.
/ @param root the HDB directory, as a file symbol
/ @param table the table's name, or (name; the columns to read) - a query that
/   names its columns reads only those column files
/ @param from_ts inclusive lower bound
/ @param to_ts exclusive upper bound
/ @return a plain table, attributes not kept; empty, in the table's shape,
/   when no partition in range holds it
/ @throws error when no partition anywhere in the HDB holds the table, or a
/   requested column is not in it
local_read:{[root;table;from_ts;to_ts]
    want:$[-11h=type table; `symbol$(); (),table 1];
    table:first table;
    if[not -11h=type table; '"local: read takes a table name, or (name;columns)"];
    / to_ts is EXCLUSIVE: a window ending at midnight touches no part of the
    / next day, so its partition is not read.
    ps:local_parts[root;`date$from_ts;`date$to_ts-1];
    parts:local_partition[root;table;want;0N] each ps;
    keep:where 0<count each parts;
    if[0=count keep; :0#local_partition[root;table;want;1;local_latest_part[root;table]]];
    / Partitions with different columns cannot be one table - a mapped HDB
    / refuses them too - and razing them would hand the query a list of
    / dicts that fails somewhere far from here.
    shapes:cols each parts keep;
    if[1<count distinct shapes;
        '"local: ",string[table],"'s columns differ between partitions ",
         (", " sv string ps[`date] keep where not shapes~\:first shapes)," and ",string first ps[`date] keep];
    raze parts keep}

/ Private: the newest partition holding `table`, as a row of local_parts.
/ @private
local_latest_part:{[root;table]
    ps:reverse local_parts[root;-0Wd;0Wd];
    hit:ps where {[table;p] not ()~key hsym `$(string ` sv p[`dir],table),"/.d"}[table] each ps;
    if[0=count hit; '"local: no partition of ",string[table]," under ",1_string root];
    first hit}

/ The most recent partition of `table`, read as local_read reads one.
/ @param root the HDB directory, as a file symbol
/ @param table the table's name
/ @return that partition's rows
/ @throws error when no partition holds the table
local_latest:{[root;table] local_partition[root;table;`symbol$();0N;local_latest_part[root;table]]}

/ The columns and types of `table`, as `meta` gives them, without reading a
/ partition: the newest partition's .d file and the first row of each column
/ - one row decoded, not the partition. What validate_live checks a local
/ source's declaration against.
/ @param root the HDB directory, as a file symbol
/ @param table the table's name
/ @return a table of c and t
/ @throws error when no partition holds the table

local_meta:{[root;table]
    select c, t from 0!meta local_partition[root;table;`symbol$();1;local_latest_part[root;table]]}

/ Run a local source's query: call `f` with a table reader over the HDB and
/ the window's bounds, traced as .qetl.source.ipc traces an IPC query - the
/ lambda's text and bounds at TRACE before, rows and ms after, a `failed line
/ that shares the request number when it throws, and the error rethrown.
/ With TRACE off it only calls.
/ .
/ `f` takes (read;from_ts;to_ts), where read[table;from_ts;to_ts] is
/ local_read over this HDB: so a query reads `read[`trades;from_ts;to_ts]`
/ where an IPC one selects from `trades on the far side, and filters the
/ rows itself, the partition being the unit read.
/ @param root the HDB directory, as a file symbol - the source's handle
/ @param f the query, taking (read;from_ts;to_ts)
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return what `f` returns
/ @eg count .qetl.source.local[`:/no/such/hdb;{[read;a;b] ([] x:a,b)};1;2]  ->  2
local:{[root;f;range_from;range_to]
    read:local_read[root];
    if[not .qetl.log.enabled`TRACE; :f[read;range_from;range_to]];
    t0:.z.p;
    req:`transport`request!(`local;.qetl.log.next_request[]);
    .[{.qetl.log.trc[x;y;z]};(`local;"query sent";
        (enlist[`call]!enlist call_text f),req,`range_from`range_to!(range_from;range_to));::];
    r:.[f;(read;range_from;range_to);{[req;t0;e]
        .[{.qetl.log.trc[x;y;z]};(`local;"query failed";
            req,`error`ms!(e;`long$(.z.p-t0)%1000000));::];
        'e}[req;t0]];
    .[{.qetl.log.trc[x;y;z]};(`local;"query returned";
        req,`rows`ms!(count r;`long$(.z.p-t0)%1000000));::];
    r}

\d .
