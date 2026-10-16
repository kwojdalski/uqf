/ consumer_attach.q - make ONE running rdb or wdb subscribe to ONE more table.
/ .
/ Uses TorQ's own subscription entry point, .sub.subscribe, which start-up uses
/ for every table: it is idempotent per table (reducesubs drops what is already
/ subscribed), creates the empty table from the tickerplant's schema when asked
/ and replays the tickerplant's log for it. So an rdb/wdb that already holds
/ other tables, their rows and their subscriptions is not touched.
/ .
/ A table already present is never modified: an existing table with the same
/ schema is subscribed to (no schema set); a different one is refused.
/ .
/ @param req JSON {"table", "columns": [[name, typechar], ...], "token", "attachment"}
/ @return JSON {"status": "created"|"owned"|"existing"|"refused"|"failed", ...}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tok:r`token;
 att:r`attachment;
 cs:`$r[`columns][;0];
 ty:first each r[`columns][;1];
 g:{@[value;x;y]};
 pt:.proc.proctype;
 if[not pt in `rdb`wdb;:.j.j `status`detail!("refused";"not an rdb or wdb")];
 own:g[`.uqfattach.owned;()!()];
 mine:$[t in key own;tok~own[t]`token;0b];
 had:t in tables[];
 if[had;
  if[not ((cols t)~cs) and (exec t from meta t)~ty;
   :.j.j `status`detail!("refused";"the table exists with a different schema")]];
 subd:t in exec table from .sub.SUBSCRIPTIONS where active;
 tpt:$[pt~`rdb;.rdb.tickerplanttypes;.wdb.tickerplanttypes];
 if[subd and had;
  :.j.j `status`rows!($[mine;"owned";"existing"];count value t)];
 hs:.sub.getsubscriptionhandles[tpt;();()!()];
 if[0=count hs;:.j.j `status`detail!("refused";"no tickerplant connection")];
 res:.[{[t;had;h] .sub.subscribe[t;`;not had;1b;h]};(t;had;first hs);{(1b;x)}];
 ok:(t in tables[]) and t in exec table from .sub.SUBSCRIPTIONS where active;
 if[not ok;
  delete from `.sub.SUBSCRIPTIONS where table=t;
  if[(not had) and t in tables[];![`.;();0b;enlist t]];
  :.j.j `status`detail!("failed";$[10h=type res;res;(2=count res) and 1b~first res;last res;"subscription did not take"])];
 if[pt~`rdb;.rdb.subtables:distinct .rdb.subtables,t];
 if[(pt~`rdb) and `dataaccess in key .proc.params;
  .dataaccess.metainfo:.dataaccess.metainfo upsert .checkinputs.getmetainfo[]];
 if[not had;
  if[not `owned in key `.uqfattach;`.uqfattach.owned set ()!()];
  .uqfattach.owned[t]:`token`attachment`created!(tok;att;string .z.p)];
 .j.j `status`replayed!($[had;"existing";"created"];count value t)}
