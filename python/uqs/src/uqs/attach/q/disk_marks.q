/ disk_marks.q - READ-ONLY: what ONE table left on THIS process's disk, for a
/ stack on another machine (#1651). Asked of the tickerplant, whose machine
/ holds the stack's storage, by detach's publication evidence (evidence.py),
/ which reads the same places itself when the stack is on its own machine:
/   * earlier days' logs: <logdir>/<day>/<plant>_<table><14 characters>;
/   * a partition of the table under each of `dirs` (the wdb's save directory,
/     the HDB): <dir>/<partition>/<table>.
/ A directory that does not exist holds nothing, as a glob of it finds nothing.
/ It changes nothing on the process.
/ .
/ @param req JSON {"logdir", "plant", "table", "dirs": ["path", ...]}
/ @return JSON {"logs": [{"name", "size"}], "partitions": [true|false per dir]}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:r`table;
 ls:{[d] $[d~"";();@[{(key hsym`$x) except `};d;()]]};
 pat:r[`plant],"_",t,14#"?";
 days:$[count r`logdir;string ls r`logdir;()];
 logs:raze {[ls;d;p;day] dir:d,"/",day;f:string ls dir;f:f where f like p;
  {[dir;n] `name`size!(n;hcount hsym`$dir,"/",n)}[dir] each f}[ls;r`logdir;pat] each days;
 has:{[t;ls;root] any {[t;root;p] not ()~key hsym`$root,"/",p,"/",t}[t;root] each string ls root}[t;ls];
 .j.j `logs`partitions!(logs;has each (),r`dirs)}
