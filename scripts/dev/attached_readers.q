/ attached_readers.q - the HDB readers of an attached destination: who they are, how each is
/ asked to reload, and what the asking left owed (#1454, split out for #1650).
/ .
/ Loaded by scripts/dev/attached_backfill.q, which writes the destination itself, and by
/ scripts/dev/remote_receiver.q, which writes it for a backfill extracted on another machine:
/ both tell the same verified readers, in the same way, once rows are finished.

\d .

/ ------------------------------------------------------------ READERS
/ .
/ Reached with the credentials the launcher passes, never the runtime's own: an attached stack is
/ somebody else's, and its HDB readers are the verified ones for this destination.

/ The readers named on the command line.
/ @param opts the parsed command line
/ @return a table of name, host and port - an empty one for none, typed so `name` reads as
/   a symbol list either way (an untyped () made the closing RESULT line throw)
.qdev.attached.readers:{[opts]
    v:first opts`readers;
    if[v~"_none_"; :([] name:`symbol$(); host:`symbol$(); port:`long$())];
    {[s] p:"@" vs s; hp:":" vs p 1; `name`host`port!(`$p 0;`$hp 0;"J"$hp 1)} each "," vs v}

/ The columns and types of the table a run writes, as a partition of it holds them: the
/ declared output (a bounded worker's transform, a receiver's contract) with `time` first, as
/ the HDB writer files it (.qetl.io.hdb_rows copies the source's time column to `time`).
/ A column the declaration leaves general (type " ", a nested column) is not compared by type.
/ @param out the declared output, an empty table
/ @return dict columns (symbols) and types (a char per column)
/ @eg .qdev.attached.shape_of[([] time:`timestamp$(); sym:`symbol$())]`types  ->  "ps"
.qdev.attached.shape_of:{[out]
    m:select c, t from 0!meta out;
    m:$[`time in m`c; m; ([] c:enlist `time; t:enlist "p"),m];
    `columns`types!(m`c;m`t)}

/ The shape the run's partitions are expected to have: set by the runner (shape_of).
.qdev.attached.shape:`columns`types!(`symbol$();"")

/ Evaluated BY THE READER, over IPC, and nothing here is a row scan: it keys directories, reads
/ the `.d` of a partition and the types of its columns (a splayed table is mapped, not read). It
/ stays in the root context and names only q, so it serialises as a plain lambda and works on any
/ HDB that holds a par root - the root is an argument, `:. for the process's own database.
/ @param root the HDB root as a file symbol
/ @param ds the table
/ @param dates the dates a query is promised to be served for
/ @param want_cols the columns each of those partitions must hold
/ @param want_types the type char of each column; " " is not compared
/ @return strings, one per problem - empty when the reader can serve the dates
/ @eg .qdev.attached.inspect[`:.;`no_such;`date$();`symbol$();""]  ->  ,"no date partitions are mapped: not a partitioned database"
.qdev.attached.inspect:{[root;ds;dates;want_cols;want_types]
    pv:@[{.Q.pv};::;{[e] `date$()}];
    if[0=count pv; :enlist "no date partitions are mapped: not a partitioned database"];
    problems:();
    latest:max pv;
    if[()~key .Q.par[root;latest;ds];
        problems,:enlist "latest partition ",string[latest]," has no ",string[ds]," directory, so the database does not list the table"];
    problems,:raze {[root;ds;want_cols;want_types;d]
        p:.Q.par[root;d;ds];
        if[()~key p; :enlist string[d],": no ",string[ds]," directory"];
        have:@[get;` sv p,`.d;{[e] `symbol$()}];
        gone:want_cols where not want_cols in have;
        out:$[count gone; enlist string[d],": ",string[ds]," lacks column(s) ",", " sv string gone; ()];
        m:@[{select c, t from 0!meta x};hsym `$string[p],"/";{[e] ()}];
        if[()~m; :out,enlist string[d],": ",string[ds]," cannot be read as a table"];
        want:want_cols!want_types;
        / plain indexing, not qSQL: a name inside a where clause is a column before it is a local
        have_cols:(m`c) where (m`c) in want_cols;
        wanted:want have_cols;
        found:(m`t) (m`c)?have_cols;
        wrong:have_cols where (" "<>wanted) and wanted<>found;
        out,$[count wrong; enlist string[d],": ",string[ds]," has incompatible type for ",", " sv string wrong; ()]}[root;ds;want_cols;want_types] each asc distinct (),dates;
    problems}

/ Ask one reader to reload the way a native wdb does at end of day: `(`reload; date)`, which a TorQ
/ HDB answers by re-mapping its database - and then ask it whether it SERVES the data (#1773). A reload
/ that succeeds is not queryable: a partitioned HDB takes its table list from its LATEST partition, so
/ a table written only into an older date is invisible to it until the latest partition holds the table
/ too (a native end of day writes it; `uqs data check --attach NAME --apply` fills it) - and a query
/ restricted to a covered date is served only if THAT date's partition holds the table with the
/ expected columns, which the table list says nothing about. So the reader is asked, by metadata only,
/ about every date the release covers. Unrestricted queries over older dates are not promised.
/ @param user the user to connect as
/ @param pass its password
/ @param need dict: dataset, dates, columns, types - what the reader must serve
/ @param r the reader: name, host, port
/ @return (1b; "") when it reloaded and serves the dates, else (0b; why)
.qdev.attached.reload_one:{[user;pass;need;r]
    addr:`$":",string[r`host],":",string[r`port],":",user,":",pass;
    h:@[hopen;(addr;10000);{[e] e}];
    if[10h=type h; :(0b;"cannot connect: ",h)];
    fix:"uqs data check --attach NAME --apply";
    res:.[{[h;n;fix]
            ds:n`dataset;
            h(`reload;.z.d);
            if[not h({[t] t in tables[]};ds);
                :(0b;"reloaded, but its HDB does not define ",string[ds]," yet: an HDB takes its tables from its latest partition, which does not hold it - it appears after the stack's next end of day writes the table, or once the latest partition is filled (",fix,")")];
            bad:h(.qdev.attached.inspect;`:.;ds;n`dates;n`columns;n`types);
            $[0=count bad; (1b;"");
              (0b;"reloaded, but cannot serve ",string[ds]," for the covered dates (",string[count distinct n`dates]," checked): ",("; " sv bad),
                  " - the rows are written and covered; fill the older partitions with ",fix)]};
          (h;need;fix);{[e] (0b;"reload failed: ",e)}];
    @[hclose;h;{[e] (::)}];
    res}

/ Ask every named reader, and say how it went.
/ @param dates the dates every reader must be able to serve: those of the claims being released
/ @return dict: registered, opened (reached), reloaded, and failed as "name: why" strings
.qdev.attached.reload_all:{[dates]
    rs:.qdev.attached.readers_named;
    pass:getenv `UQF_ATTACH_REACH_PASSWORD;
    need:(`dataset`dates!(.qdev.attached.dataset;asc distinct `date$dates)),.qdev.attached.shape;
    out:.qdev.attached.reload_one[.qdev.attached.reach_user;pass;need] each rs;
    ok:first each out;
    failed:{[r;o] (string r`name),": ",o 1}'[rs where not ok;out where not ok];
    reached:{not (x 1) like "cannot connect*"} each out;
    `registered`opened`reloaded`failed!(count rs;sum "j"$reached;sum "j"$ok;failed)}

/ What has been finished and not yet reloaded, and when the last reload was.
.qdev.attached.reload_state:`dirty`last!(0b;0Np)

/ The table the run writes: what a reader must serve for the rows to be queryable.
.qdev.attached.dataset:`

/ The readers that failed the last reload, for the closing line.
.qdev.attached.last_failed:()

/ Seconds between mid-run reloads: hdb_reload_seconds, else 30 (0 reloads at every finished day).
.qdev.attached.reload_interval:{[]
    v:.qetl.cfg.raw `hdb_reload_seconds;
    n:$[0=count v; 30; null j:"J"$v; 30; j<0; 30; j];
    n*0D00:00:01}
