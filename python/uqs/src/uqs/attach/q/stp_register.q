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
/ ONE CALL, BEST-EFFORT IN-PROCESS ROLLBACK - not atomic. A q process handles one
/ message at a time, so nothing publishes in the middle of this. Before each step
/ the globals it touches are snapshotted (a global that did not exist is recorded
/ as absent). If step i throws, steps i..0 are undone in reverse - INCLUDING the
/ failing step's partial effects (#1448): the step's own cleanup (log handle and a
/ log file it created; the stpmeta file) then the snapshot restore (absent globals
/ are deleted, not set to null). The answer's "undone" counts the steps (0..i)
/ fully undone and "undo_failed" names the ones that were not; it never claims
/ more. Effects outside the snapshot (a log file a failing openlog created without
/ recording it) can still survive.
/ .
/ A table that is already there is NEVER modified: the answer is "existing"
/ (or "owned" when our own token is on it, from an earlier run) with whether
/ its schema matches.
/ .
/ A name that is not a table but is already bound on this process - a TorQ global
/ (upd, a view such as loghandles, sym), a namespace, a q keyword - is REFUSED before
/ anything changes: `t set e` would overwrite it for every table (#1450). The probe
/ and preflight refuse it first; this is the check that holds when something
/ defined the name between plan and apply.
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
 / an untyped column decides its stored type from each batch; a later batch of another shape
 / makes the wdb's write fail and aborts its end of day for every table (#1451)
 if[any ty=" ";:.j.j `status`detail!("refused";"untyped column(s) ",(", " sv string cs where ty=" ")," - declare a concrete type for each")];
 g:{@[value;x;y]};
 own:g[`.uqfattach.owned;()!()];
 own:$[count own;(key own)!.j.k each value own;()!()];
 if[(not t in tables[]) and ((t in key `.) or t in .Q.res,key .q);
  :.j.j `status`detail!("refused";"the name ",string[t]," is already defined on this process as something other than a table; registering it would overwrite it")];
 if[t in tables[];
  inl:t in key own;
  mine:$[inl;tok~own[t]`token;0b];
  mt:exec t from meta t;
  same:$[(cols t)~cs;all (ty=mt)|(ty in .Q.A)&mt=" ";0b];
  if[same and inl and (not mine) and `join in key r;
   rec:own t;
   rec[`users]:distinct (),$[`users in key rec;rec`users;()],enlist att;
   .uqfattach.owned[t]:.j.j rec];
  own:g[`.uqfattach.owned;()!()];
  own:$[count own;(key own)!.j.k each value own;()!()];
  :.j.j `status`matches`published`owner`users!(
   $[mine;"owned";inl;"shared";"existing"];same;t in g[`.stpps.t;`$()];
   $[inl;own[t]`attachment;""];$[inl;$[`users in key own t;(),own[t]`users;()];()])];
 if[g[`.sctp.chainedtp;0b];:.j.j `status`detail!("refused";"a chained tickerplant takes its tables from its parent")];
 if[not g[`.stplg.multilog;`] in `tabperiod`tabular;
  :.j.j `status`detail!("refused";"logging mode ",string[g[`.stplg.multilog;`]]," shares logs between tables")];
 empty:flip cs!{$[x in " ",.Q.A;();x$()]}each ty;
 p:.z.p+.eodtime.dailyadj;
 / the log file step 4 opens, and whether it is there before - only a file this call creates is deleted
 lfile:.[{[m;d;t;p] .stplg.logname[m][d;t;p]};(.stplg.multilog;.stplg.dldir;t;p);{`}];
 lfresh:$[null lfile;0b;not type key lfile];
 rec:.j.j `token`attachment`created!(tok;att;string .z.p);
 steps:(
  {[t;e;p;o] t set e};
  {[t;e;p;o] .stpps.t:distinct .stpps.t,t; .stpps.schemas[t]:e; .stpps.tabcols[t]:cols e; .stpps.schemasnoattributes[t]:e};
  {[t;e;p;o] .stplg.updtab[t]:{(enlist(count first x)#y),x}};
  {[t;e;p;o] .stplg.t:distinct .stplg.t,t; .stplg.logtabs:distinct .stplg.logtabs,t; .stplg.rolltabs:distinct .stplg.rolltabs,t; .stplg.msgcount[t]:0; .stplg.rowcount[t]:0};
  {[t;e;p;o] .stplg.openlog[.stplg.multilog;.stplg.dldir;t;p]};
  {[t;e;p;o] .stpm.updmeta[.stplg.multilog][`open;enlist t;p]};
  {[t;e;p;o] if[not `owned in key `.uqfattach;`.uqfattach.owned set ()!()]; .uqfattach.owned[t]:o});
 / the globals each step writes: snapshotted before it runs, restored if it or a later step throws
 touches:(
  enlist t;
  `.stpps.t`.stpps.schemas`.stpps.tabcols`.stpps.schemasnoattributes;
  enlist `.stplg.updtab;
  `.stplg.t`.stplg.logtabs`.stplg.rolltabs`.stplg.msgcount`.stplg.rowcount;
  enlist `currlog;
  enlist `.stpm.metatable;
  enlist `.uqfattach.owned);
 / effects outside those globals, run with a context dict: before the restore, and after it
 before:({};{};{};{};
  {[c] old:$[first c[`snap]0;exec handle from last c[`snap]0 where tbl=c`t;`int$()];
   {@[hclose;x;::]}each (exec handle from `..currlog where tbl=c`t,not null handle) except old;
   if[c`fresh;@[hdel;c`file;::]]};
  {};{});
 after:({};{};{};{};{};{[c] .stpm.setmeta[.stplg.dldir;.stpm.metatable]};{});
 snap:{[n] .[{(1b;value x)};enlist n;{(0b;::)}]};
 / a global that was absent before the step is deleted, not set to null; one already gone is left alone
 drop:{[n] s:string n; $["."=first s;[k:last where s="."; ns:`$k#s; l:`$(k+1)_s];[ns:`.; l:n]]; if[l in key ns;![ns;();0b;enlist l]]};
 restore:{[drop;n;v] $[first v;n set last v;drop n]}[drop];
 / every part of an undo is tried; a step counts as undone only when none of its parts threw
 try:{[f;a] .[{[f;a] f . a;0b};(f;a);{1b}]};
 snaps:();
 i:0;
 bad:"";
 while[(i<count steps) and 0=count bad;
  snaps,:enlist snap each touches i;
  res:.[{[f;a] (0b;f . a)};(steps i;(t;empty;p;rec));{(1b;x)}];
  $[first res;bad:last res;i+:1]];
 if[count bad;
  failed:();
  j:i;
  while[j>=0;
   c:`t`snap`file`fresh!(t;snaps j;lfile;lfresh);
   / one statement per part: the items of a list literal are evaluated right to left
   u:try[before j;enlist c];
   u:u|any try[restore;]each flip (touches j;c`snap);
   u:u|try[after j;enlist c];
   if[u;failed,:j];
   j-:1];
  :.j.j `status`detail`step`undone`undo_failed!("failed";bad;i;(i+1)-count failed;failed)];
 ln:exec first logname from `..currlog where tbl=t;
 .j.j `status`logs`handle!("created";enlist `name`size!(string ln;hcount ln);exec first handle from `..currlog where tbl=t)}
