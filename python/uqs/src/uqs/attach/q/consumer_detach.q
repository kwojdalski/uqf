/ consumer_detach.q - take ONE table this attachment attached back out of a
/ running rdb or wdb, if and only if it is still empty and still ours.
/ .
/ The inverse of consumer_attach.q: drops the table, its subscription record
/ and the rdb's subtables entry. It refuses (changing nothing) when the table
/ holds rows - those are data, and detaching is not data deletion - or carries
/ another attachment's ownership record, or others are listed as using it.
/ .
/ Only what this attachment's own tickerplant gave the table is removed: the
/ subscription rows are matched on "tickerplant" (its procname, required), and a table
/ that is also subscribed from ANOTHER tickerplant is refused, never dropped from
/ under that subscription.
/ .
/ @param req JSON {"table", "token", "dry_run", "tickerplant", "follows_plant"?}
/ .
/ "follows_plant" says the tickerplant's table is this attachment's, which makes the
/ consumer's copy ours even with no record of its own: a consumer that restarted
/ rebuilt the table from the tickerplant and lost its in-memory record.
/ @return JSON {"status": "removed"|"absent"|"refused"|"dry_run"|"partial", "problems": [...]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tok:r`token;
 g:{@[value;x;y]};
 pt:.proc.proctype;
 own:g[`.uqfattach.owned;()!()];
 own:$[count own;(key own)!.j.k each value own;()!()];
 subs:g[`.sub.SUBSCRIPTIONS;([]procname:`$();table:`$())];
 if[(not t in tables[]) and (not t in key own) and not t in exec table from subs;:.j.j `status`problems!("absent";())];
 problems:();
 follow:`follows_plant in key r;
 tp:$[`tickerplant in key r;`$r`tickerplant;`];
 other:exec distinct procname from subs where table=t,not procname=tp;
 problems,:$[t in key own;$[tok~own[t]`token;();enlist "the ownership record is another attachment's"];follow;();enlist "no ownership record for the table"];
 problems,:$[t in key own;$[`users in key own t;$[count own[t]`users;enlist "used by attachment(s) ",", " sv own[t]`users;()];()];()];
 problems,:$[null tp;enlist "the request names no tickerplant";()];
 problems,:$[count other;enlist "also subscribed from another tickerplant: ",", " sv string other;()];
 problems,:$[t in tables[];$[count value t;enlist "the table holds ",(string count value t)," row(s)";()];()];
 if[pt~`wdb;problems,:$[t in exec tablename from g[`.wdb.tabsizes;([tablename:`$()]rowcount:`long$())];enlist "the wdb has written the table to disk";()]];
 if[count problems;:.j.j `status`problems!("refused";problems)];
 if[r`dry_run;:.j.j `status`problems!("dry_run";())];
 steps:(
  {[t;tp] delete from `.sub.SUBSCRIPTIONS where table=t,procname=tp};
  {[t;tp] if[`subtables in key `.rdb;.rdb.subtables:.rdb.subtables except t]};
  {[t;tp] if[t in tables[];![`.;();0b;enlist t]]};
  {[t;tp] if[`owned in key `.uqfattach;.uqfattach.owned:(enlist t)_.uqfattach.owned]});
 try:{[f;t;tp] .[{[f;t;tp] f[t;tp];""};(f;t;tp);{x}]};
 errs:{x where 0<count each x}try[;t;tp]each steps;
 .j.j `status`problems!($[count errs;"partial";"removed"];errs)}
