/ probe.q - READ-ONLY state of one TorQ process, as JSON.
/ .
/ Sent over IPC as one lambda by uqs.attach.remote. It changes nothing on the
/ process (no set, no insert, no subscribe). Every variable it reads goes
/ through g, a guarded lookup, so a process that lacks one (another proctype,
/ another TorQ) answers with a default and the Python side decides whether the
/ default is acceptable. Lambdas have no closures: helpers take g explicitly.
/ .
/ @param req JSON {"tables": ["name", ...]} - the tables the caller cares about
/ @return JSON string
{[req]
 r:.j.k $[10h=type req;req;string req];
 want:`$(),r`tables;
 g:{@[value;x;y]};
 pt:.proc.proctype;
 cnt:{[t] $[t in tables[];count value t;-1]};
 sch:{[t] $[t in tables[];flip (string exec c from meta t;string exec t from meta t);()]};
 prm:{[k] $[k in key .proc.params;"," sv .proc.params k;""]};
 own:g[`.uqfattach.owned;()!()];
 base:`procname`proctype`stackid`torq`kdb`port`host`schemafile`tplogdir`procfile`owned!(
  string .proc.procname;string pt;prm`stackid;.proc.getversion[];string .z.K;`long$system"p";string .z.h;
  prm`schemafile;prm`tplogdir;prm`procfile;string each key own);
 ext:()!();
 if[pt in `segmentedtickerplant`segmentedchainedtickerplant;
  subs:g[`.stpps.subrequestall;()!()];
  filt:g[`.stpps.subrequestfiltered;([]tbl:`$())];
  logs:g[`currlog;([tbl:`$()]logname:`$();handle:`int$())];
  mc:g[`.stplg.msgcount;()!()];
  rc:g[`.stplg.rowcount;()!()];
  ext:`chained`multilog`batchmode`createlogs`errmode`replayperiod`kdbtplog`dldir`custom`tables`published`schemas`subscribers`filtered`logs`msgcount`rowcount`buffered!(
   g[`.sctp.chainedtp;0b];string g[`.stplg.multilog;`];string g[`.stplg.batchmode;`];g[`..createlogs;0b];g[`.stplg.errmode;0b];string g[`.stplg.replayperiod;`];
   string g[`.stplg.kdbtplog;`];string g[`.stplg.dldir;`];string each key g[`.stplg.custommode;()!()];
   string each tables[];string each g[`.stpps.t;`$()];
   want!sch each want;
   want!{[s;t] $[t in key s;count s t;0]}[subs]each want;
   want!{[f;t] count select from f where tbl=t}[filt]each want;
   want!{[l;t] $[t in exec tbl from l;string exec logname from l where tbl=t;()]}[logs]each want;
   want!{[m;t] $[t in key m;m t;0]}[mc]each want;
   want!{[m;t] $[t in key m;m t;0]}[rc]each want;
   want!cnt each want)];
 if[pt~`rdb;
  subs:g[`.sub.SUBSCRIPTIONS;([]table:`$();active:`boolean$())];
  ext:`subscribeto`subfiltered`ignorelist`subtables`tables`schemas`rows`hdbdir`reloadenabled`tickerplanttypes`subscribed!(
   string each (),g[`.rdb.subscribeto;`];g[`.rdb.subfiltered;0b];string each (),g[`.rdb.ignorelist;`$()];string each (),g[`.rdb.subtables;`$()];
   string each tables[];want!sch each want;want!cnt each want;
   string g[`.rdb.hdbdir;`];g[`.rdb.reloadenabled;0b];string each (),g[`.rdb.tickerplanttypes;`$()];
   string exec table from subs where active)];
 if[pt~`wdb;
  subs:g[`.sub.SUBSCRIPTIONS;([]table:`$();active:`boolean$())];
  sp:g[`.sort.params;([]tabname:`$();att:`$();column:`$();sort:`boolean$())];
  ext:`subtabs`subsyms`ignorelist`mode`writedownmode`savedir`hdbdir`tables`schemas`rows`tickerplanttypes`subscribed`sortrows!(
   string each (),g[`.wdb.subtabs;`];string each (),g[`.wdb.subsyms;`];string each (),g[`.wdb.ignorelist;`$()];string g[`.wdb.mode;`];string g[`.wdb.writedownmode;`];
   string g[`.wdb.savedir;`];string g[`.wdb.hdbdir;`];string each tables[];want!sch each want;want!cnt each want;
   string each (),g[`.wdb.tickerplanttypes;`$()];string exec table from subs where active;
   select tabname:string tabname,att:string att,col:string column,sort from sp)];
 if[pt~`discovery;
  ext:enlist[`services]!enlist select procname:string procname,proctype:string proctype,hpup:string hpup from .servers.SERVERS];
 .j.j base,ext}
