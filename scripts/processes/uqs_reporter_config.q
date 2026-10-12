/ uqs_reporter_config.q - point TorQ's reporter at the checks uqs generates.
/ .
/ Loaded by reporter1 BEFORE ${KDBCODE}/processes/reporter.q (VENDORED_LOAD_OVERLAY
/ in python/uqs/src/uqs/stack/procs.py). reporter.q reads its config path once,
/ as `inputcsv:@[value;`.rp.inputcsv;...]`, when it loads; every settings layer
/ (TorQ's, KDBSERVCONFIG's, the site's KDBAPPCONFIG) has loaded by then, so a
/ value set here is the one it keeps. The site's reporter.csv is not read.
/ .
/ The generated file is this tree's checks, validated against the deployment's
/ tables (python/uqs/src/uqs/stack/reporter.py), written into TORQDATA by
/ bootstrap on every start, beside the generated process.csv and database.q.

\d .rp

inputcsv:hsym `$getenv[`TORQDATA],"/reporter.csv";

/ When each check last warned: name -> time.
uqfalerted:(`symbol$())!`timestamp$();

/ The generated rows' result handler: a check that returns messages logs them
/ as one warning, at most once per `period` (a minute) per check.
/ @param period minute   the least time between two warnings for one check
/ @param data   dict     reporter.q's result: name, procname, result, ...
/ @return ::
uqfalert:{[period;data]
  msgs:data[`result]`messages;
  if[not count msgs; :(::)];
  if[(`timespan$period) > .proc.cp[] - 0p^uqfalerted data`name; :(::)];
  @[`.rp.uqfalerted;data`name;:;.proc.cp[]];
  .lg.w[`reporter;"check ",string[data`name]," on ",string[data`procname],": ",raze msgs];
  };

\d .

.lg.o[`reporter;"reporter checks: ",1_string .rp.inputcsv];
