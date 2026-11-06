/ writer_state.q - READ-ONLY: what this process would write into an HDB, and when.
/ .
/ Sent over IPC as one lambda by uqs.attach.backfill_coordination to the wdb and
/ rdb processes of an attached stack, to decide whether a backfill into their HDB
/ can be coordinated with them (#1454). Changes nothing; every variable goes
/ through g, so a process lacking one answers its default and the Python side
/ decides what that means.
/ .
/ now and next_roll are THIS process's clock and end-of-day roll (.eodtime), so the
/ distance to end of day is measured where end of day runs. partition is the
/ date the wdb is currently filling (.wdb.getpartition), the one native end of
/ day will write (TorQ wdb.q: currentpartition, advanced by endofday and fixpartition).
/ An rdb reports partitions: every date it still holds, .rdb.getpartition[] (TorQ rdb.q:
/ rdbpartition, which endofday extends with date+1 and rmdtfromgetpar shrinks only once
/ the reload completes), so a date awaiting reload is still listed. .eodtime.d is NOT
/ exported: TorQ sets it at process start and neither end-of-day handler advances it, so
/ it says nothing about the date a long-running writer is filling (#1914). Dates are ""
/ (partitions: an empty list or a "" element) when unknown, and unknown is never read as
/ safe.
/ .
/ The calendar the stack files its rows by (#2193): rolltimeoffset, the roll zone's adjustment and the
/ data zone's adjustment, each in nanoseconds, and native_day, .eodtime.getday of this clock - the
/ date the stack is filling, in ITS calendar. A process without .eodtime answers TorQ's defaults (0:
/ UTC midnight), so an absent calendar reads as UTC.
/ .
/ @param req JSON, ignored (kept so every operation takes one string)
/ @return JSON string
{[req]
 g:{@[value;x;y]};
 s:{[v] $[(null v) or 0=count v;"";string v]};
 pt:g[`.proc.proctype;`];
 base:`procname`proctype`now`next_roll!(string g[`.proc.procname;`];string pt;s .z.p;s g[`.eodtime.nextroll;0Np]);
 cal:`rolltimeoffset`rolltz_adj`datatz_adj`native_day!(
  string "j"$g[`.eodtime.rolltimeoffset;0D];
  string "j"$@[{.eodtime.adjtime .z.p};::;{0D}];
  string "j"$g[`.eodtime.dailyadj;0D];
  s @[{.eodtime.getday .z.p};::;{0Nd}]);
 ext:()!();
 if[pt~`wdb;
  gp:@[{.wdb.getpartition[]};::;{0Nd}];
  ext:`hdbdir`savedir`mode`writedownmode`partition`permitreload`reloadcomplete!(
   string g[`.wdb.hdbdir;`];string g[`.wdb.savedir;`];string g[`.wdb.mode;`];string g[`.wdb.writedownmode;`];
   s gp;g[`.wdb.permitreload;0b];g[`.wdb.reloadcomplete;1b])];
 if[pt~`rdb;
  rp:@[{(),.rdb.getpartition[]};::;{0#0Nd}];
  ext:`hdbdir`savetables`reloadenabled`partitions!(string g[`.rdb.hdbdir;`];g[`.rdb.savetables;1b];g[`.rdb.reloadenabled;0b];
   $[14h=type rp;s each rp;enlist""]) ];
 .j.j base,cal,ext}
