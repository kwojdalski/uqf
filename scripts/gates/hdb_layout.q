/ hdb_layout.q - the partitions an HDB has and the tables each holds, as q
/ discovers them: segmented (par.txt) or not (#1783).
/ .
/ Run as:  q scripts/gates/hdb_layout.q <hdb root>
/ .
/ Prints one line, layout|<json>, then DONE. q exits 0 whatever happens while
/ a script runs, so the caller believes DONE, never the exit code - and a run
/ without it is a failure to report, never an HDB without partitions. The
/ JSON is
/   segmented   whether the root holds a par.txt
/   segments    the directories par.txt names, as it names them
/   tables      q's own table list once loaded: tables[], which a partitioned
/               database takes from its LATEST partition
/   partitions  one per partition q maps, oldest first: its date, the
/               directory holding it (the root, or the segment) and
/               {table: the files in that table's directory}
/   problems    why the layout cannot be judged whole; empty when it can
/ .
/ q decides what the partitions are: the database is loaded with `\l`, and
/ they are read back from .Q.pv and .Q.pd - so a date held by two segments is
/ two partitions here, as it is to every query. Nothing in this script
/ restates q's rules for finding them. What it adds is what `\l` will not say:
/   - a segment par.txt names that does not exist - q loads the others and
/     says nothing, so its partitions vanish from every query unannounced;
/   - a segment q cannot read, and partition directories in a root that also
/     holds par.txt - both refuse the load, and are named here with the cause
/     rather than with q's bare error ('part).
/ .
/ READ-ONLY. `\l` maps the database and reads its sym file; nothing is
/ written, created or repaired. Like every HDB process loading this root, it
/ would also run a .q script someone left in it.
/ .
/ One q process per inspection, whatever the number of partitions: its cost
/ is q's start, the load, and a directory listing of each partition's tables.
/ Every name is under .qhdblayout, because `\l` assigns each file in the root
/ as a variable at the top level, and must clobber nothing of this script's -
/ nor may a table of this script's join the database's own table list.

.qhdblayout.root_text:first .z.x;
.qhdblayout.root:hsym `$.qhdblayout.root_text;

/ The report, and the line that says it was finished.
.qhdblayout.emit:{[segmented;segments;tbls;parts;problems]
    -1 "layout|",.j.j `segmented`segments`tables`partitions`problems!(segmented;segments;tbls;parts;problems);
    -1 "DONE";
    exit 0}

if[()~key .qhdblayout.root; .qhdblayout.emit[0b;();();();enlist "no HDB at ",.qhdblayout.root_text]];

/ PeachQ's `\l` maps no partitioned database, so it would report every HDB
/ as one without partitions: refused, never answered. The probe is
/ .qetl.io.on_peachq's (src/etl/core/io_hdb.q), as hdb_alter.q's.
if[not -7h=type @[value;`.pq.load_natives;{[e] 0N}];
    .qhdblayout.emit[0b;();();();enlist "KDB-X only: PeachQ's \\l maps no partitioned database, so it cannot say which partitions this one has"]];

/ A partition directory's name: a date, the only scheme this stack writes.
.qhdblayout.date_shaped:{[name] name like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"}

.qhdblayout.par:` sv .qhdblayout.root,`par.txt;
.qhdblayout.segmented:not ()~key .qhdblayout.par;
.qhdblayout.segments:$[.qhdblayout.segmented; {[l] l where 0<count each l} trim read0 .qhdblayout.par; ()];

/ A segment as q reaches it: relative to the root, which `\l` makes the
/ working directory.
.qhdblayout.resolved:{[s] $["/"=first s; s; .qhdblayout.root_text,"/",s]}

/ Why one segment cannot be loaded from, or "". `key` answers a directory
/ with a symbol list, a file with its own name, a missing path with (), and
/ an unreadable one with an error - a string, which `key` never returns.
.qhdblayout.segment_problem:{[s]
    k:@[key;hsym `$.qhdblayout.resolved s;{[e] e}];
    if[10h=type k; :"segment ",s," in par.txt cannot be read: ",k];
    if[()~k; :"segment ",s," in par.txt does not exist - q loads the other segments without a word, so its partitions are missing from every query"];
    if[-11h=type k; :"segment ",s," in par.txt is a file, not a directory"];
    ""}

.qhdblayout.problems:$[.qhdblayout.segmented; {[p] p where 0<count each p} .qhdblayout.segment_problem each .qhdblayout.segments; ()];
.qhdblayout.stray:$[.qhdblayout.segmented; asc {[ns] ns where .qhdblayout.date_shaped each ns} string key .qhdblayout.root; ()];
if[count .qhdblayout.stray;
    .qhdblayout.problems,:enlist .qhdblayout.root_text," holds partition directories (",(", " sv .qhdblayout.stray),") beside par.txt - q loads only the segments par.txt names, and refuses a root holding both ('part)"];
if[count .qhdblayout.problems; .qhdblayout.emit[.qhdblayout.segmented;.qhdblayout.segments;();();.qhdblayout.problems]];

/ `cd` first: `\l` refuses a path holding a space ('nyi).
.qhdblayout.loaded:@[{[r] system "cd ",r; system "l ."; ""};.qhdblayout.root_text;{[e] e}];
if[count .qhdblayout.loaded;
    .qhdblayout.emit[.qhdblayout.segmented;.qhdblayout.segments;();();enlist "q cannot load the HDB at ",.qhdblayout.root_text,": ",.qhdblayout.loaded]];
.qhdblayout.tables:string tables[];

/ The files in a table directory.
.qhdblayout.files_of:{[dir] es:key dir; es where {[dir;e] -11h=type key ` sv dir,e}[dir] each es}

/ The tables a partition directory holds - its subdirectories - and their files.
.qhdblayout.tables_of:{[dir]
    es:key dir;
    ts:es where {[dir;e] 11h=type key ` sv dir,e}[dir] each es;
    ts!string .qhdblayout.files_of each ` sv/:dir,/:ts}

/ A partition's directory as text: `:. is the root itself.
.qhdblayout.dir_text:{[d] $[d~`:.; .qhdblayout.root_text; 1_string d]}

/ One partition: its date, the directory holding it, and what it holds.
.qhdblayout.partition:{[d;p]
    dir:` sv d,`$string p;
    held:@[.qhdblayout.tables_of;dir;{[e] e}];
    if[10h=type held; '"cannot list ",(.qhdblayout.dir_text d),"/",string[p],": ",held];
    `partition`dir`tables!(string p;.qhdblayout.dir_text d;held)}

/ .Q.pv and .Q.pd are defined once a partitioned database is loaded; an empty
/ root defines neither, and has no partition.
.qhdblayout.pv:@[value;`.Q.pv;{[e] ()}];
.qhdblayout.pd:@[value;`.Q.pd;{[e] ()}];
.qhdblayout.parts:.[{[pd;pv] .qhdblayout.partition'[pd;pv]};(.qhdblayout.pd;.qhdblayout.pv);{[e] e}];
if[10h=type .qhdblayout.parts;
    .qhdblayout.emit[.qhdblayout.segmented;.qhdblayout.segments;.qhdblayout.tables;();enlist .qhdblayout.parts]];
.qhdblayout.emit[.qhdblayout.segmented;.qhdblayout.segments;.qhdblayout.tables;.qhdblayout.parts;()]
