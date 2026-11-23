/ disk_marks.q - READ-ONLY: what ONE table left on THIS process's disk, for a
/ stack on another machine (#1651). Asked of the tickerplant, whose machine
/ holds the stack's storage, by detach's publication evidence (evidence.py),
/ which reads the same places itself when the stack is on its own machine:
/   * earlier days' logs: <logdir>/<day>/<plant>_<table><14 characters>, or
/     the same gzipped (`.gz`), as housekeeping leaves them, sized by what the
/     gzip trailer says the plant wrote (#2913);
/   * a partition of the table under each of `dirs` (the wdb's save directory,
/     the HDB): <dir>/<partition>/<table>, and the same under each segment the
/     dir's par.txt names, where TorQ's wdb moves a partition (.Q.par, #2913).
/ A directory that does not exist holds nothing, as a glob of it finds nothing.
/ What exists and cannot be read is named in `unread`: evidence counts it as
/ publication, because absence is unproven.
/ It changes nothing on the process.
/ .
/ @param req JSON {"logdir", "plant", "table", "dirs": ["path", ...]}
/ @return JSON {"logs": [{"name", "size"}], "partitions": [true|false per dir],
/   "unread": ["what could not be read", ...]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:r`table;
 ls:{[d] $[d~"";();@[{(key hsym`$x) except `};d;()]]};
 pat:r[`plant],"_",t,14#"?";
 days:$[count r`logdir;string ls r`logdir;()];
 files:raze {[ls;d;p;day] dir:d,"/",day;f:string ls dir;
  (dir,"/"),/:f where (f like p) or f like p,".gz"}[ls;r`logdir;pat] each days;
 written:{[f] h:hsym`$f;c:hcount h;
  $[not f like "*.gz";c;c<4;'"truncated";0x0 sv 0x00000000,reverse read1(h;c-4;4)]};
 sizes:{[written;f] @[written;f;-1]}[written] each files;
 ok:sizes>=0;
 logs:{[f;n] `name`size!(last "/" vs f;n)}'[files where ok;sizes where ok];
 segs:{[root] f:hsym`$root,"/par.txt";
  if[()~key f;:enlist root];
  named:trim read0 f;
  named:named where 0<count each named;
  root,{[root;s] $["/"=first s;s;root,"/",s]}[root] each named};
 roots:{[segs;d] $[d~"";enlist d;@[segs;d;`unreadable]]}[segs] each (),r`dirs;
 has:{[t;ls;root] any {[t;root;p] not ()~key hsym`$root,"/",p,"/",t}[t;root] each string ls root}[t;ls];
 bad:-11h=type each roots;
 held:{[has;bad;rs] $[bad;0b;any has each rs]}[has]'[bad;roots];
 unread:("the earlier log ",/:(files where not ok),\:" cannot be read"),
  ("the par.txt in ",/:((),r`dirs) where bad),\:" cannot be read";
 .j.j `logs`partitions`unread!(logs;held;unread)}
