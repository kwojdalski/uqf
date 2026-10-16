/ stp_register.q - register ONE table in a running segmented tickerplant.
/ .
/ TorQ builds a tickerplant's tables, log handles and pub/sub state once, at
/ start, from its schema file (segmentedtickerplant.q: loadschemas,
/ generateschemas, .stplg.init); there is no API to add a table to a running
/ one. This does for ONE table what those three do for all of them, WITHOUT
/ re-running them: .stpps.attrstrip rewrites every live table and .stplg.init
/ reopens every log, which would touch the buffered rows and log handles of
/ tables that are not ours.
/ .
/ ONE CALL IS ONE ATOMIC UNIT. A q process handles one message at a time, so
/ nothing publishes in the middle of this. If a step throws, the steps already
/ done are undone inside this same call, and the answer says so.
/ .
/ A table that is already there is NEVER modified: the answer is "existing"
/ (or "owned" when our own token is on it, from an earlier run) with whether
/ its schema matches.
/ .
/ @param req JSON {"table", "columns": [[name, typechar], ...], "token", "attachment", "join": true?}
/ @return JSON {"status": "created"|"owned"|"shared"|"existing"|"refused"|"failed", ...}
/ .
/ "shared" is a table another attachment created: with "join" this attachment is added
/ to that record's users, so the creator's detach knows it is still needed.
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tok:r`token;
 att:r`attachment;
 cs:`$r[`columns][;0];
 ty:first each r[`columns][;1];
 g:{@[value;x;y]};
 own:g[`.uqfattach.owned;()!()];
 if[t in tables[];
  inl:t in key own;
  mine:$[inl;tok~own[t]`token;0b];
  same:((cols t)~cs) and (exec t from meta t)~ty;
  if[same and inl and (not mine) and `join in key r;
   .uqfattach.owned[t;`users]:distinct (),$[`users in key own t;own[t]`users;()],enlist att];
  own:g[`.uqfattach.owned;()!()];
  :.j.j `status`matches`published`owner`users!(
   $[mine;"owned";inl;"shared";"existing"];same;t in g[`.stpps.t;`$()];
   $[inl;own[t]`attachment;""];$[inl;$[`users in key own t;(),own[t]`users;()];()])];
 if[g[`.sctp.chainedtp;0b];:.j.j `status`detail!("refused";"a chained tickerplant takes its tables from its parent")];
 if[not g[`.stplg.multilog;`] in `tabperiod`tabular;
  :.j.j `status`detail!("refused";"logging mode ",string[g[`.stplg.multilog;`]]," shares logs between tables")];
 empty:flip cs!{$[x=" ";();x$()]}each ty;
 steps:(
  {[t;e] t set e};
  {[t;e] .stpps.t:distinct .stpps.t,t; .stpps.schemas[t]:e; .stpps.tabcols[t]:cols e; .stpps.schemasnoattributes[t]:e};
  {[t;e] .stplg.updtab[t]:{(enlist(count first x)#y),x}};
  {[t;e] .stplg.t:distinct .stplg.t,t; .stplg.logtabs:distinct .stplg.logtabs,t; .stplg.rolltabs:distinct .stplg.rolltabs,t; .stplg.msgcount[t]:0; .stplg.rowcount[t]:0};
  {[t;e] .stplg.openlog[.stplg.multilog;.stplg.dldir;t;.z.p+.eodtime.dailyadj]};
  {[t;e] .stpm.updmeta[.stplg.multilog][`open;enlist t;.z.p+.eodtime.dailyadj]});
 undo:(
  {[t] ![`.;();0b;enlist t]};
  {[t] .stpps.t:.stpps.t except t; .stpps.schemas:(enlist t)_.stpps.schemas; .stpps.tabcols:(enlist t)_.stpps.tabcols; .stpps.schemasnoattributes:(enlist t)_.stpps.schemasnoattributes};
  {[t] .stplg.updtab:(enlist t)_.stplg.updtab};
  {[t] .stplg.t:.stplg.t except t; .stplg.logtabs:.stplg.logtabs except t; .stplg.rolltabs:.stplg.rolltabs except t; .stplg.msgcount:(enlist t)_.stplg.msgcount; .stplg.rowcount:(enlist t)_.stplg.rowcount};
  {[t] ln:exec first logname from `..currlog where tbl=t; h:exec first handle from `..currlog where tbl=t; if[not null h;@[hclose;h;::]]; delete from `..currlog where tbl=t; if[not null ln;@[hdel;ln;::]]};
  {[t] ln:exec first logname from `..currlog where tbl=t; .stpm.metatable:delete from .stpm.metatable where logname=ln; .stpm.setmeta[.stplg.dldir;.stpm.metatable]});
 i:0;
 bad:"";
 while[(i<count steps) and 0=count bad;
  res:.[{[f;a] (0b;f . a)};(steps i;(t;empty));{(1b;x)}];
  $[first res;bad:last res;i+:1]];
 if[count bad;
  failed:();
  j:i-1;
  while[j>=0;
   u:.[{[f;t] f t;0b};(undo j;t);{1b}];
   if[u;failed,:j];
   j-:1];
  :.j.j `status`detail`step`undone`undo_failed!("failed";bad;i;i-count failed;failed)];
 if[not `owned in key `.uqfattach;`.uqfattach.owned set ()!()];
 .uqfattach.owned[t]:`token`attachment`created!(tok;att;string .z.p);
 ln:exec first logname from `..currlog where tbl=t;
 .j.j `status`logs`handle!("created";enlist `name`size!(string ln;hcount ln);exec first handle from `..currlog where tbl=t)}
