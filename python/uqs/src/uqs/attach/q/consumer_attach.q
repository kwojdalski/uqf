/ consumer_attach.q - make ONE running rdb or wdb subscribe to ONE more table.
/ .
/ Uses TorQ's own subscription entry point, .sub.subscribe, which start-up uses
/ for every table: it is idempotent per table (reducesubs drops what is already
/ subscribed), creates the empty table from the tickerplant's schema when asked
/ and replays the tickerplant's log for it. So an rdb/wdb that already holds
/ other tables, their rows and their subscriptions is not touched.
/ .
/ .sub.subscribe ends in .sub.updatesubscriptions, which first deletes EVERY
/ inactive row of .sub.SUBSCRIPTIONS - other tables' rows too, the ones
/ .sub.autoreconnect resubscribes from when their tickerplant comes back. So the
/ table is put back as it was before the call, plus this table's new rows from
/ this tickerplant (#1445); a failed attempt puts it back exactly as it was.
/ .
/ A table already present is never modified: an existing table with the same
/ schema is subscribed to (no schema set); a different one is refused. When it
/ had no active subscription, the answer is "subscribed": the table pre-existed
/ but this call added its subscription (and, on an rdb, its .rdb.subtables entry
/ when "subtables" is true), which consumer_unsubscribe.q takes back (#1459).
/ .
/ @param req JSON {"table", "columns": [[name, typechar], ...], "token", "attachment", "join": true?}
/ @return JSON {"status": "created"|"owned"|"existing"|"subscribed"|"shared"|"refused"|"failed", ...}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tok:r`token;
 att:r`attachment;
 cs:`$r[`columns][;0];
 ty:first each r[`columns][;1];
 / an untyped column decides its stored type from each batch; a later batch of another shape
 / makes the wdb's write fail and aborts its end of day for every table (#1451)
 if[any ty=" ";:.j.j `status`detail!("refused";"untyped column(s) ",(", " sv string cs where ty=" ")," - declare a concrete type for each")];
 g:{@[value;x;y]};
 pt:.proc.proctype;
 if[not pt in `rdb`wdb;:.j.j `status`detail!("refused";"not an rdb or wdb")];
 own:g[`.uqfattach.owned;()!()];
 own:$[count own;(key own)!.j.k each value own;()!()];
 inl:t in key own;
 mine:$[inl;tok~own[t]`token;0b];
 had:t in tables[];
 / a name already bound to something that is not a table would be overwritten by the
 / subscribe's `set`, for every table (#1450); refuse before anything changes
 if[(not had) and ((t in key `.) or t in .Q.res,key .q);
  :.j.j `status`detail!("refused";"the name ",string[t]," is already defined on this process as something other than a table; subscribing would overwrite it")];
 if[had;
  mt:exec t from meta t;
  if[not $[(cols t)~cs;all (ty=mt)|(ty in .Q.A)&mt=" ";0b];
   :.j.j `status`detail!("refused";"the table exists with a different schema")]];
 if[had and inl and (not mine) and `join in key r;
  rec:own t;
   rec[`users]:distinct (),$[`users in key rec;rec`users;()],enlist att;
   .uqfattach.owned[t]:.j.j rec];
 subd:t in exec table from .sub.SUBSCRIPTIONS where active;
 tpt:$[pt~`rdb;.rdb.tickerplanttypes;.wdb.tickerplanttypes];
 if[subd and had;
  :.j.j `status`rows`owner!($[mine;"owned";inl;"shared";"existing"];count value t;$[inl;own[t]`attachment;""])];
 hs:.sub.getsubscriptionhandles[tpt;();()!()];
 if[0=count hs;:.j.j `status`detail!("refused";"no tickerplant connection")];
 h:first hs;
 pre:.sub.SUBSCRIPTIONS;
 newst:$[pt~`rdb;not t in .rdb.subtables;0b];
 res:.[{[t;had;h] .sub.subscribe[t;`;not had;1b;h]};(t;had;h);{(1b;x)}];
 ok:(t in tables[]) and t in exec table from .sub.SUBSCRIPTIONS where active;
 if[not ok;
  `.sub.SUBSCRIPTIONS set pre;
  if[(not had) and t in tables[];![`.;();0b;enlist t]];
  :.j.j `status`detail!("failed";$[10h=type res;res;(2=count res) and 1b~first res;last res;"subscription did not take"])];
 / every row as it was, but this tickerplant's inactive rows for t, which the new active one replaces
 `.sub.SUBSCRIPTIONS set (delete from pre where table=t,procname=h`procname,not active),select from .sub.SUBSCRIPTIONS where table=t,active;
 if[pt~`rdb;.rdb.subtables:distinct .rdb.subtables,t];
 if[(pt~`rdb) and `dataaccess in key .proc.params;
  .dataaccess.metainfo:.dataaccess.metainfo upsert .checkinputs.getmetainfo[]];
 if[not had;
  if[not `owned in key `.uqfattach;`.uqfattach.owned set ()!()];
  .uqfattach.owned[t]:.j.j `token`attachment`created!(tok;att;string .z.p)];
 .j.j `status`replayed`subtables!($[mine;"owned";had;$[inl;"shared";"subscribed"];"created"];count value t;newst)}
