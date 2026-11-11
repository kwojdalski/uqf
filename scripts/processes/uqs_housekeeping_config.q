/ uqs_housekeeping_config.q - point TorQ's housekeeping at the rules uqs generates,
/ and define the one rule function they add.
/ .
/ Loaded by housekeeping1 BEFORE ${KDBCODE}/processes/housekeeping.q
/ (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py). housekeeping.q
/ reads its rules' path once, as `inputcsv:@[value;`.hk.inputcsv;...]`, when it
/ loads. TorQ's settings/housekeeping.q has set .hk.inputcsv by then, to the
/ site's appconfig/housekeeping.csv, which tars tickerplant logs; a value set
/ here is the one it keeps, and the site's file is not read (#2155).
/ .
/ The generated file is written into TORQDATA by bootstrap
/ (python/uqs/src/uqs/stack/housekeeping.py), which declares the replay
/ retention window its tickerplant-log rule is aged by, and the schedule
/ read here into .hk.runtimes - which housekeeping.q, too, reads once as it
/ loads, to set its timers (#2150).

.hk.inputcsv:hsym `$getenv[`TORQDATA],"/housekeeping.csv";
.hk.runtimes:exec runtime from (enlist "T";enlist ",") 0: hsym `$getenv[`TORQDATA],"/housekeeping_schedule.csv";

/ The HDB reload (.qtorq.reload_hdb) finds the HDBs among the servers TorQ
/ knows, and TorQ registers only the process types a process names here;
/ housekeeping names none. It connects when it reloads (.qtorq.hk.connect) -
/ hdbcompress's run and `uqs data compress`'s reload (.qtorq.hk.reload) -
/ not at startup: no handle held to an HDB all day for a rule that runs once.
/ `hdb is .qtorq.hdb_type, written out: torq_pipeline.q defines that after this loads.
.servers.CONNECTIONS:distinct @[value;`.servers.CONNECTIONS;`symbol$()],`hdb;

/ Register and open the HDBs named above. TorQ makes no connection at startup
/ unless asked (.servers.STARTUP), so each reload asks first; one it cannot
/ reach is the reload's to report, as it does.
/ @return ::
.qtorq.hk.connect:{[]
  @[.servers.startup;::;{[e] .lg.w[`housekeeping;"could not register the HDBs to reload: ",e]}];
  };

/ Ask every HDB to reload, connecting first: what `uqs data compress` sends
/ once, after the last partition it compressed (stack/compress.py, #2147).
/ @return `registered`opened`reloaded - .qtorq.reload_hdb's counts
.qtorq.hk.reload:{[] .qtorq.hk.connect[]; .qtorq.reload_hdb[]};

/ Whether TorQ's replay reads a gzipped log on this host: tickerlogreplay.q's
/ replayinner refuses one off Linux and before kdb+ 4.0.
/ @return boolean
.qtorq.hk.gzreplayable:{[] (.z.o like "l*") and .z.K>=4};

/ gzip one file into <file>.gz, replacing a copy already there.
/ @param keep boolean   keep the original beside the gzipped copy
/ @param file string    the file's path
/ @return boolean       false when gzip failed, which is logged
.qtorq.hk.gzip:{[keep;file]
  cmd:"gzip -f",$[keep;"k";""]," \"",file,"\"";
  @[{[c] .proc.sys c; 1b};cmd;{[f;e] .lg.e[`housekeeping;"Failed to zip ",f," : ",e]; 0b}[file]]};

/ The rule function for a segmented tickerplant's log directory for one day:
/ gzip it so that TorQ's replay reads it at every step. Never a tarball, which
/ replay cannot read at all.
/ .
/ Replay (tickerlogreplay.q's getstplogs) takes a gzipped `stpmeta` to mean
/ that every log beside it is gzipped, and otherwise reads the logs as they
/ were written. So each log is first copied gzipped beside its original, then
/ `stpmeta` is gzipped, and only then are the originals removed. A failure
/ before `stpmeta` leaves the directory as the plant wrote it, plus copies
/ replay does not read and a later run replaces; a failure after it leaves
/ originals replay no longer reads and a later run removes.
/ .
/ Where TorQ cannot replay a gzipped log at all, the directory is left as it is.
/ @param dir string   the directory, as housekeeping's find returns it
/ @return ::
tplogzip:{[dir]
  if[not .qtorq.hk.gzreplayable[];
    .lg.w[`housekeeping;"left ",dir," as written: TorQ replays a gzipped log only on Linux, with kdb+ 4.0 or later"];
    :(::)];
  names:string key hsym `$dir;
  plain:names where not names like "*.gz";
  logs:plain where not plain like "stpmeta";
  .lg.o[`housekeeping;"zipping tickerplant log directory ",dir];
  if[not all .qtorq.hk.gzip[1b] each (dir,"/"),/:logs; :(::)];
  if[any plain like "stpmeta"; if[not .qtorq.hk.gzip[0b;dir,"/stpmeta"]; :(::)]];
  {[f] @[hdel;hsym `$f;{[f;e] .lg.e[`housekeeping;"Failed to remove ",f," : ",e]}[f]]} each (dir,"/"),/:logs;
  };

/ Compress one HDB to the policy .cmp.inputcsv names, with .qtorq.compress.run_held
/ (torq_compress_survey.q): the selection and the hold `uqs data compress` makes, so a
/ partition the coverage ledger says is still being written is left alone (#2176).
/ @param dir string   the HDB root
/ @return table       .qtorq.compress.run_held's, a row per file changed
/ @throws what .qtorq.compress.run_held throws, and .cmp.inputcsv's undefined-name error where
/   TorQ's compress.q is not loaded
.qtorq.hk.compress:{[dir]
  conf:.cmp.inputcsv;
  conf:$[10h=type conf;conf;string conf];
  .qtorq.hk.connect[];
  .lg.o[`housekeeping;"compressing ",dir," to the compression policy ",conf];
  .qtorq.compress.run_held[hsym `$dir;conf]};

/ The rule function for the HDB (#2150): bring it to the declared compression
/ policy with TorQ's .cmp, through .qtorq.compress.run_held - partition by
/ partition under the HDB write lock, holding back what is still being written,
/ the checked apply, then the HDB reload when a file changed. Never housekeeping.q's
/ kdbzip, which compresses every file with one hard-coded algorithm. The
/ policy ages each table's partitions (its minage), so young ones are left
/ alone; every partition older than that is considered.
/ .
/ A failed run - .cmp refusing the config, or a file not brought to its target
/ state - is logged as an error, as housekeeping.q's rm and zip log theirs, and
/ returned as an empty list: housekeeping runs its rules one after another,
/ and a rule that raised would stop the rest of the run.
/ @param dir string   the HDB root, as housekeeping's find returns it
/ @return table       .qtorq.compress.run_held's, a row per file changed; () after a failure
hdbcompress:{[dir]
  r:@[.qtorq.hk.compress;dir;{[dir;e] .lg.e[`housekeeping;"Failed to compress ",dir," : ",e]; ()}[dir]];
  if[98h=type r; .lg.o[`housekeeping;"compressed ",string[count r]," file(s) under ",dir]];
  r};
