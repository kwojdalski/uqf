/ stp_unregister.q - take ONE table this attachment registered back out of a
/ running segmented tickerplant, if and only if that is provably safe.
/ .
/ The inverse of stp_register.q, and stricter: registering a table adds state
/ nobody else could have been using; removing it must prove nobody has. Every
/ condition is checked INSIDE this one call, against the process as it is now,
/ before the first change:
/ .
/   * the ownership record is ours (same token) and no other attachment is
/     listed as using the table;
/   * nothing was ever published: no buffered rows, no counted messages, and
/     every log file of the table, today's and earlier days', is exactly the
/     empty header the plant wrote when it opened it;
/   * no subscriber but the consumers this attachment attached, and no filtered
/     subscription.
/ .
/ If any fails, NOTHING is changed and the answer lists why. With "dry_run" the
/ same checks run and the answer says whether removal would go ahead.
/ .
/ Only files named exactly <plant>_<table><14 digits> under the plant's log
/ directory are ever deleted. A removal that fails part way reports which
/ steps did not complete; each step is safe to repeat.
/ .
/ @param req JSON {"table", "token", "empty_size", "expect_subscribers", "dry_run"}
/ @return JSON {"status": "removed"|"absent"|"refused"|"dry_run"|"partial", "problems": [...]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 tok:r`token;
 esz:`long$r`empty_size;
 nsub:`long$r`expect_subscribers;
 g:{@[value;x;y]};
 own:g[`.uqfattach.owned;()!()];
 own:$[count own;(key own)!.j.k each value own;()!()];
 kt:g[`.stplg.kdbtplog;`];
 root:$[":"=first string kt;kt;hsym kt];
 days:$[11h=type k:key root;k;`$()];
 pat:(string .proc.procname),"_",(string t),"??????????????";
 found:{[root;pat;d] k:key ` sv root,d;$[11h=type k;{[b;f] ` sv b,f}[` sv root,d]each k where (string each k) like pat;`$()]}[root;pat]each days;
 live:exec logname from `..currlog where tbl=t;
 mrows:g[`.stpm.metatable;([]logname:`$();tbls:())];
 listed:exec logname from mrows where t in/: tbls;
 logs:distinct (`$()),(raze found),live,listed;
 gone:(not t in tables[]) and not t in key own;
 if[gone;:.j.j `status`problems!("absent";())];
 problems:();
 problems,:$[t in key own;$[tok~own[t]`token;();enlist "the ownership record is another attachment's"];enlist "no ownership record for the table"];
 problems,:$[t in key own;$[`users in key own t;$[count own[t]`users;enlist "used by attachment(s) ",", " sv own[t]`users;()];()];()];
 problems,:$[t in tables[];$[count value t;enlist "the table holds buffered rows";()];()];
 problems,:$[0<0^g[`.stplg.msgcount;()!()] t;enlist "messages were published to it";()];
 problems,:raze {[esz;f] $[esz=@[hcount;f;-1];();enlist "log ",(string f)," is not the empty header"]}[esz]each logs;
 problems,:$[nsub<count g[`.stpps.subrequestall;()!()] t;enlist "subscribers other than the attached consumers";()];
 problems,:$[count select from g[`.stpps.subrequestfiltered;([]tbl:`$())] where tbl=t;enlist "a filtered subscription exists";()];
 if[count problems;:.j.j `status`problems!("refused";problems)];
 if[r`dry_run;:.j.j `status`problems!("dry_run";())];
 steps:(
  {[t;l] .stpps.subrequestall:(enlist t)_.stpps.subrequestall;.stpps.subrequestfiltered:delete from .stpps.subrequestfiltered where tbl=t};
  {[t;l] .stpps.t:.stpps.t except t;.stpps.schemas:(enlist t)_.stpps.schemas;.stpps.tabcols:(enlist t)_.stpps.tabcols;.stpps.schemasnoattributes:(enlist t)_.stpps.schemasnoattributes};
  {[t;l] .stplg.updtab:(enlist t)_.stplg.updtab;.stplg.t:.stplg.t except t;.stplg.logtabs:.stplg.logtabs except t;.stplg.rolltabs:.stplg.rolltabs except t;.stplg.msgcount:(enlist t)_.stplg.msgcount;.stplg.rowcount:(enlist t)_.stplg.rowcount};
  {[t;l] {@[hclose;x;::]}each exec handle from `..currlog where tbl=t,not null handle;delete from `..currlog where tbl=t};
  {[t;l] {hdel x}each l};
  {[t;l] .stpm.metatable:delete from .stpm.metatable where t in/: tbls;.stpm.setmeta[.stplg.dldir;.stpm.metatable]};
  {[t;l] ![`.;();0b;enlist t]};
  {[t;l] .uqfattach.owned:(enlist t)_.uqfattach.owned});
 try:{[f;t;l] .[{[f;t;l] f[t;l];""};(f;t;l);{x}]};
 errs:{x where 0<count each x}try[;t;logs]each steps;
 .j.j `status`problems!($[count errs;"partial";"removed"];errs)}
