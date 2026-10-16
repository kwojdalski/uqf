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
/ day will write; eod_date is the process's current trading date. Dates are
/ "" when unknown, and unknown is never read as safe.
/ .
/ @param req JSON, ignored (kept so every operation takes one string)
/ @return JSON string
{[req]
 g:{@[value;x;y]};
 s:{[v] $[(null v) or 0=count v;"";string v]};
 pt:g[`.proc.proctype;`];
 base:`procname`proctype`now`eod_date`next_roll!(string g[`.proc.procname;`];string pt;s .z.p;s g[`.eodtime.d;0Nd];s g[`.eodtime.nextroll;0Np]);
 ext:()!();
 if[pt~`wdb;
  gp:@[{.wdb.getpartition[]};::;{0Nd}];
  ext:`hdbdir`savedir`mode`writedownmode`partition`permitreload`reloadcomplete!(
   string g[`.wdb.hdbdir;`];string g[`.wdb.savedir;`];string g[`.wdb.mode;`];string g[`.wdb.writedownmode;`];
   s gp;g[`.wdb.permitreload;0b];g[`.wdb.reloadcomplete;1b])];
 if[pt~`rdb;
  ext:`hdbdir`savetables`reloadenabled!(string g[`.rdb.hdbdir;`];g[`.rdb.savetables;1b];g[`.rdb.reloadenabled;0b])];
 .j.j base,ext}
