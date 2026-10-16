/ ledger_leave.q - an attachment that depended on a table another attachment
/ created stops depending on it.
/ .
/ Removes the attachment from the `users` of the table's ownership record, so
/ the creator's detach is no longer held back by it. Changes nothing else.
/ .
/ @param req JSON {"table", "attachment"}
/ @return JSON {"status": "left"|"absent", "users": [...]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 own:@[value;`.uqfattach.owned;()!()];
 own:$[count own;(key own)!.j.k each value own;()!()];
 if[not t in key own;:.j.j `status`users!("absent";())];
 rec:own t;
 if[not `users in key rec;:.j.j `status`users!("left";())];
 rec[`users]:rec[`users] except enlist r`attachment;
 .uqfattach.owned[t]:.j.j rec;
 .j.j `status`users!("left";rec`users)}
