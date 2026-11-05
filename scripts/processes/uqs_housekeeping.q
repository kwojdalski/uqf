/ uqs_housekeeping.q - point TorQ's housekeeping at the rules uqs generates,
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
/ retention window its tickerplant-log rule is aged by.

.hk.inputcsv:hsym `$getenv[`TORQDATA],"/housekeeping.csv";

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
