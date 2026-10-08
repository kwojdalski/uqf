/ status_intervals.q - does a converted tree's status and interval code still
/ work? (#856)
/ .
/ Run from the root of a tree - the original, or one written by
/ scripts/portable/flatten_contexts.py - by its --check, or by hand:
/ .
/   cd build/portable && q /path/to/status_intervals.q -q
/ .
/ Loading is not enough, and neither is a zero exit: q exits 0 after a script
/ that threw part-way. So every check compares a computed value with the one
/ it must be, and only a run that reaches the end prints PORTABLE_CHECK_OK.
/ Anything else prints PORTABLE_CHECK_FAILED and why, and exits 1.
/ .
/ The status checks write to a directory of their own under /tmp and remove
/ it; nothing else on the machine is touched. This file uses no nested
/ context itself, so it runs on the 4.0 build it is checking for.

.pc.fail:{[what] -2 "PORTABLE_CHECK_FAILED: ",what; exit 1}
.pc.eq:{[what;got;want] if[not got~want; .pc.fail what," gave ",(-3!got),", expected ",-3!want]}
.pc.throws:{[what;f;prefix]
    r:@[{(0b;x[])};f;{(1b;x)}];
    if[not first r; .pc.fail what," did not throw"];
    if[not prefix~(count prefix)#last r; .pc.fail what," threw ",last r]}
.pc.load:{[path] @[system;"l ",path;{[p;e] .pc.fail p," did not load: ",e}[path]]}

.pc.load "src/etl/core/intervals.q";
.pc.load "src/etl/core/status.q";

/ ------------------------------------------------------------ intervals

.pc.eq["require_interval";.qetl.coverage.require_interval[1;3];1 3];
.pc.throws["require_interval of an empty range";{.qetl.coverage.require_interval[2;2]};
    "require_interval: must be non-empty"];
/ compose calls nothing else, but gaps calls require_interval and compose by
/ their unqualified names: the binding the conversion has to keep.
.pc.eq["compose";
    .qetl.coverage.compose ([] range_from:1 2 5; range_to:2 3 6);
    ([] range_from:1 5; range_to:3 6)];
.pc.eq["gaps";
    .qetl.coverage.gaps[0;10;([] range_from:5 2; range_to:7 3)];
    ([] range_from:0 3 7; range_to:2 5 10)];
.pc.eq["gaps when nothing is covered";
    .qetl.coverage.gaps[0;10;([] range_from:`long$(); range_to:`long$())];
    ([] range_from:enlist 0; range_to:enlist 10)];
.pc.throws["gaps of a reversed range";{.qetl.coverage.gaps[5;1;([] range_from:1#0; range_to:1#1)]};
    "require_interval: must be non-empty"];

/ --------------------------------------------------------------- status

.pc.dir:"/tmp/uqf_portable_check_",string .z.i;
`UQF_STATUS_DIR setenv .pc.dir;
.pc.eq["status_dir";.qetl.status.status_dir[];.pc.dir];
.pc.spec:`source_version`range_from`range_to!(`v1;2026.01.01D00:00;2026.01.02D00:00);
.pc.progress:`cursor`rows_published`windows_completed!(0Np;0;0);
.pc.write:{[state;err] .qetl.status.write_status[`portable_check;`pc1;state;.pc.spec;.pc.progress;err]};

.pc.eq["no status yet";.qetl.status.previous_state`pc1;`];
.pc.eq["write_status";.pc.write[`starting;""];.pc.dir,"/airflow_status_pc1.txt"];
.pc.eq["previous_state after starting";.qetl.status.previous_state`pc1;`starting];
.pc.write[`running;""];
.pc.write[`completed;""];
.pc.eq["the file carries the state";
    `$(.j.k first read0 hsym `$.pc.dir,"/airflow_status_pc1.txt")`state;`completed];
.pc.throws["completed -> running";{.pc.write[`running;""]};
    "require_transition: completed -> running is forbidden"];
.pc.throws["an unknown state";{.pc.write[`paused;""]};"write_status: unknown state paused"];
.pc.throws["failed without an error";{.pc.write[`failed;""]};
    "write_status: a failed state must carry an error string"];
.pc.write[`failed;"boom"];
.pc.eq["previous_state after failed";.qetl.status.previous_state`pc1;`failed];

system "rm -rf ",.pc.dir;
-1 "PORTABLE_CHECK_OK status_intervals";
exit 0
