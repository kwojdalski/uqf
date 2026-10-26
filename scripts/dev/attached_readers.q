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

/ Ask one reader to reload the way a native wdb does at end of day: `(`reload; date)`, which a TorQ
/ HDB answers by re-mapping its database - and then ask it whether it SERVES the dataset. A reload
/ that succeeds is not queryable: a partitioned HDB takes its table list from its LATEST partition,
/ so a table written only into an older date is invisible to it until the latest partition holds the
/ table too (a native end of day writes it; `uqs data hdb-check --fix` fills it).
/ @param user the user to connect as
/ @param pass its password
/ @param dataset the table the run wrote
/ @param r the reader: name, host, port
/ @return (1b; "") when it reloaded and serves the table, else (0b; why)
.qdev.attached.reload_one:{[user;pass;dataset;r]
    addr:`$":",string[r`host],":",string[r`port],":",user,":",pass;
    h:@[hopen;(addr;10000);{[e] e}];
    if[10h=type h; :(0b;"cannot connect: ",h)];
    res:.[{[h;ds] h(`reload;.z.d);
            $[h({[t] t in tables[]};ds); (1b;"");
              (0b;"reloaded, but its HDB does not define ",string[ds]," yet: an HDB takes its tables from its latest partition, which does not hold it - it appears after the stack's next end of day writes the table, or once the latest partition is filled (uqs data hdb-check --fix)")]};
          (h;dataset);{[e] (0b;"reload failed: ",e)}];
    @[hclose;h;{[e] (::)}];
    res}

/ Ask every named reader, and say how it went.
/ @return dict: registered, opened (reached), reloaded, and failed as "name: why" strings
.qdev.attached.reload_all:{[]
    rs:.qdev.attached.readers_named;
    pass:getenv `UQF_ATTACH_REACH_PASSWORD;
    out:.qdev.attached.reload_one[.qdev.attached.reach_user;pass;.qdev.attached.dataset] each rs;
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
