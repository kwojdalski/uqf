/ hdb_reader.q - READ-ONLY identity of one HDB reader process, as JSON.
/ .
/ Sent over IPC as one lambda by uqs.attach.backfill, to every process
/ discovery lists as an hdb, to decide which of them SERVE a given
/ destination (and so which are told to reload after a backfill into it).
/ Changes nothing: no set, no insert, no reload. A process that lacks a
/ variable answers a default, and the Python side decides what that means.
/ .
/ hdbdir is the directory the database was loaded from: loading a database
/ directory makes it the process's working directory, so `system"pwd"` is the
/ path its partitions are mapped from. partitions is how many partition values
/ the process currently maps (.Q.PV), i.e. what it will show a query before a
/ reload.
/ .
/ @param req JSON, ignored (kept so every operation takes one string)
/ @return JSON string
{[req]
 g:{@[value;x;y]};
 prm:g[`.proc.params;()!()];
 stack:$[`stackid in key prm;"," sv prm`stackid;""];
 pv:g[`.Q.PV;()];
 .j.j `procname`proctype`stackid`hdbdir`partitions`lastpartition`port`host!(
  string g[`.proc.procname;`];string g[`.proc.proctype;`];stack;first system"pwd";
  count pv;$[count pv;string last pv;""];`long$system"p";string .z.h)}
