/ consumer_unsubscribe.q - take back out of a running rdb or wdb ONE subscription
/ this attachment added to a table the consumer already held (#1459).
/ .
/ The inverse of consumer_attach.q's "subscribed" answer. The table was there
/ before the attachment, so it, its rows and its ownership are never touched:
/ only the .sub.SUBSCRIPTIONS rows for the table from this attachment's own
/ tickerplant (its procname, required) are removed, and - when "subtables" says
/ the attach added it - the rdb's .rdb.subtables entry. Rows from any other
/ tickerplant, and every other table's rows, stay.
/ .
/ @param req JSON {"table", "tickerplant", "dry_run", "subtables"?}
/ @return JSON {"status": "removed"|"absent"|"refused"|"dry_run", "problems": [...]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tp:$[`tickerplant in key r;`$r`tickerplant;`];
 if[null tp;:.j.j `status`problems!("refused";enlist "the request names no tickerplant")];
 subs:@[value;`.sub.SUBSCRIPTIONS;([]procname:`$();table:`$())];
 if[not t in exec table from subs where procname=tp;:.j.j `status`problems!("absent";())];
 if[r`dry_run;:.j.j `status`problems!("dry_run";())];
 delete from `.sub.SUBSCRIPTIONS where table=t,procname=tp;
 if[$[`subtables in key r;1b~r`subtables;0b];if[`subtables in key `.rdb;.rdb.subtables:.rdb.subtables except t]];
 .j.j `status`problems!("removed";())}
