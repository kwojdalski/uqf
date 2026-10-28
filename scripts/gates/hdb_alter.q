/ hdb_alter.q - check, fill or change ONE table across a partitioned HDB,
/ touching only the partitions that hold it, with a plan first, a journal
/ before the first write, an undo, and a final check (#1755).
/ .
/ Run as (KDB-X only):
/   q scripts/gates/hdb_alter.q -hdb DIR -table T [OPERATION] [-apply] [-json]
/   q scripts/gates/hdb_alter.q -hdb DIR -undo JOURNAL [-apply] [-json]
/ .
/ OPERATION is at most one of:
/   (none)                 the consistency check alone
/   -fill                  write an empty T into every partition that lacks it
/                          (schema: T from -schema FILE when that file defines it,
/                          else the partition -template DATE, else the latest one)
/   -reorder c1,c2,...     every existing column, in a new order (.d only)
/   -rename old=new        a column file renamed, and .d
/   -attr col:p|s|g|u|none the column's attribute (its header)
/   -cast col:TYPE         the column's data, written to col.uqf-new beside it
/                          and renamed over it; the original is hard-linked to
/                          col.uqf-orig until a clean final check, then deleted
/ .
/ Without -apply nothing is written, not even the journal: the run prints the
/ plan and exits 0 when it is valid, 1 when it is refused. With -apply the
/ same plan is carried out. The script never asks: a person runs `uqs data
/ hdb-alter` / `hdb-check`, which show the plan and ask before -apply.
/ .
/ WHY NOT dbmaint's OWN reordercols, renamecol, castcol, setattrcol. They walk
/ every partition dbmaint's allpaths names and fail on the first that lacks
/ the table, so a table a backfill wrote into a few dates cannot be changed at
/ all, and a run that fails half way leaves the partitions changed unevenly -
/ and a database whose partitions disagree fails every cross-date query.
/ dbmaint (lib/kx-dbmaint, vendored, never edited) is loaded for allpaths,
/ which follows par.txt in a segmented database, and validcolname. Everything
/ that decides, and each write, is here, per partition that holds the table.
/ .
/ CONSISTENCY FIRST. Every partition holding the table must agree on its
/ columns, their order and their types (attributes aside), or the run is
/ refused before any write, naming each shape and the partitions holding it.
/ A partition with a table directory but no .d is reported and never written.
/ .
/ JOURNAL. Before the first write, <hdb>.uqf-alter/<stamp>-<table>.json
/ (beside the HDB: `\l` would try to load a directory inside it)
/ records the operation and each touched partition's .d and attributes. A run
/ that stops part way (killed, a full disk) leaves it "started": the next run
/ of the SAME operation resumes it, skipping partitions already changed; any
/ other operation on the table is refused until `-undo JOURNAL` reverses it.
/ -undo reverses a completed or an interrupted run, and is previewed unless
/ -apply. A completed -cast is reversed by casting back to the journalled
/ type: exact when the cast was lossless, which the plan reports per run.
/ .
/ FINAL CHECK. After the last write every partition holding the table is read
/ again; the run exits 1 naming each that does not have the target shape.
/ .
/ NOT HERE: deleting columns or tables, re-enumerating symbols (a -cast to or
/ from symbol is refused), coordination with running writers and reloading
/ readers - the uqs front end checks the first and does the second.
/ .
/ -journal PATH names the journal a new run writes (default: a timestamped
/ file in <hdb>.uqf-alter/); `uqs` passes the one its dry run showed.
/ .
/ -test_interrupt_after N stops the process (exit 3) after N partitions have
/ been written: the test suite's stand-in for a kill part way through.

opts:.Q.opt .z.x;
as_json:`json in key opts;
applying:`apply in key opts;

/ The text of option k, "" when absent.
/ @param k the option name
/ @return its value, as a string
opt_text:{[k] $[k in key opts; " " sv opts k; ""]}

/ The report, built up as the run goes and printed once (JSON with -json). The
/ null key's (::) keeps the values a general list, so any type can be added.
report:(enlist `)!enlist (::);

/ Record one field of the report.
/ @param k the field
/ @param v its value
note:{[k;v] @[`report;k;:;v];}

/ A line for a person; nothing with -json, whose stdout is the JSON alone.
say:{[s] if[not as_json; -1 s];}

/ Print the report: the JSON document with -json, nothing more otherwise
/ (each line was said as it was decided).
emit:{[] if[as_json; -1 .j.j 1_report];}

/ Refuse: say why on stderr (and in the JSON), and exit 1. A q script that
/ signals does not end the process, so a refusal must exit itself.
/ @param msg why
refuse:{[msg]
    note[`status;"refused"];
    note[`refused;msg];
    emit[];
    if[not as_json; -2 "hdb_alter: refused: ",msg];
    exit 1}

/ Stop the run with exit 1 once something was written: the JSON says how far.
/ @param msg what failed
fail:{[msg]
    note[`status;"failed"];
    note[`failed;msg];
    emit[];
    if[not as_json; -2 "hdb_alter: FAILED: ",msg];
    exit 1}

/ ----------------------------------------------------------------- arguments

if[not `hdb in key opts; refuse "-hdb DIR is required"];
hdb_text:opt_text`hdb;
if[()~key hsym `$hdb_text; refuse "no HDB at ",hdb_text];
/ Absolute and without symlinks: the journal sits beside it, and names it.
/ "(" first, so q's system does not read a leading `cd` as its own \cd.
hdb_text:first system "(cd '",hdb_text,"' && pwd -P)";
hdb:hsym `$hdb_text;
note[`hdb;hdb_text];

operations:`fill`reorder`rename`attr`cast`undo;
chosen:operations inter key opts;
if[1<count chosen; refuse "one operation per run, not ",", " sv "-",/:string chosen];
op:$[count chosen; first chosen; `check];
arg:$[op in `check`fill; ""; opt_text op];
if[(op in `reorder`rename`attr`cast`undo) and 0=count arg; refuse "-",string[op]," needs a value"];

/ dbmaint.q, found from this script's own path, as fill_hdb_partitions.q finds
/ it: scripts/gates/ is two levels below the tree holding lib/ in a checkout, a
/ release, and the temporary directory `uqs data hdb-alter --attach` copies
/ both into on a stack's host.
script_dir:{[f] $[0=count i:where "/"=f; ""; (1+last i)#f]} string .z.f;
dbmaint_file:hsym `$script_dir,"../../lib/kx-dbmaint/dbmaint.q";
if[()~key dbmaint_file; refuse "no dbmaint.q at ",1_string dbmaint_file];
system"l ",1_string dbmaint_file;

/ PeachQ cannot write a partitioned database natively (the .d amend is 'nyi),
/ so this script is KDB-X only, and says so rather than failing half way.
/ The probe is .qetl.io.on_peachq's (src/etl/core/io_hdb.q).
if[not -7h=type @[value;`.pq.load_natives;{[e] 0N}]; refuse "KDB-X only: PeachQ cannot write a partitioned HDB"];

/ The HDB's symbol domain, so enumerated columns can be read and a template
/ taken from a partition carries its enumeration.
sym_file:` sv hdb,`sym;
if[not ()~key sym_file; sym:get sym_file];

/ ---------------------------------------------------------------- partitions

/ A partition directory's name: a date, the only scheme this stack writes.
date_shaped:{[name] name like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"}

/ The partition a table directory sits in, by name.
partition_of:{[dir] first -2#"/" vs string dir}

/ The same, as a symbol: what partitions are compared by.
part_sym:{[dir] `$partition_of dir}

/ Whether a path exists.
present:{[p] not ()~key p}

/ Whether a table directory holds a table: a .d.
has_d:{[dir] present ` sv dir,`.d}

/ A file in a table directory.
in_dir:{[dir;name] ` sv dir,name}

/ Every date partition's directory for a table, oldest first; allpaths
/ follows par.txt. The directory need not exist.
table_dirs:{[t]
    ds:allpaths[hdb;t];
    ds:ds where date_shaped each partition_of each ds;
    ds iasc partition_of each ds}

/ A column's type as one character: q's type letter, "s" for a symbol or an
/ enumeration, "*" for a general or nested column, "?" for a missing file.
type_char:{[t] $[t within 1 19h; .Q.t t; t within 20 76h; "s"; "*"]}

/ A column's type on disk.
col_type:{[dir;c] f:in_dir[dir;c]; $[present f; type_char type get f; "?"]}

/ A column's attribute on disk, ` for none.
col_attr:{[dir;c] f:in_dir[dir;c]; $[present f; attr get f; `]}

/ What a partition holds: its .d, and each column's type.
shape_of:{[dir] c:get in_dir[dir;`.d]; `cols`types!(c;col_type[dir] each c)}

/ A shape as text: "time:p, sym:s, px:f".
describe_shape:{[s] ", " sv (string s`cols),'":",'s`types}

/ A shape for JSON.
shape_json:{[s] `cols`types!(string s`cols;s`types)}

/ A shape from JSON.
shape_from:{[j] `cols`types!(`$j`cols;j`types)}

/ ------------------------------------------------------------------ journal

/ BESIDE the HDB, never in it: `\l` loads every entry of a database's root
/ that is not a partition as a variable, so a directory of JSON journals in
/ the root would stop every HDB process loading it ('parse). The same
/ convention as an attached backfill's <hdb>.staging and <hdb>.write.lock.
journal_dir:hsym `$hdb_text,".uqf-alter";

/ A path for a shell command, quoted.
shell_path:{[p] "'",(1_string p),"'"}

/ Rename a file (or directory) in place, as `mv` does.
move:{[a;b] system "mv ",shell_path[a]," ",shell_path b;}

/ Write a JSON document whole: to a temporary file, then renamed over.
write_json:{[path;obj]
    tmp:hsym `$(1_string path),".tmp";
    tmp 0: enlist .j.j obj;
    move[tmp;path];}

/ Read a JSON document.
read_json:{[path] .j.k raze read0 path}

/ Every journal of this HDB, as (path; contents).
journals:{[]
    if[()~key journal_dir; :()];
    fs:(key journal_dir) where (key journal_dir) like "*.json";
    {(x;read_json x)} each ` sv/:journal_dir,/:fs}

/ ------------------------------------------------------------- the operation

/ Type names a -cast accepts, beside the type letters themselves. Symbol is
/ not among them: casting to or from it changes the enumeration.
cast_types:`boolean`guid`byte`short`int`long`real`float`char`timestamp`month`date`datetime`timespan`minute`second`time!"bgxhijefcpmdznuvt";

/ A "name=value" or "name:value" pair.
pair:{[s;what]
    i:first where s in "=:";
    if[null i; refuse what," must be NAME=VALUE, not ",s];
    (`$i#s;(1+i)_s)}

journal:();
journal_path:`;
undoing:op~`undo;

if[undoing;
    journal_path:hsym `$arg;
    if[()~key journal_path; refuse "no journal at ",arg];
    journal:read_json journal_path;
    op:`$journal`operation;
    arg:journal`argument];

tbl:$[undoing; `$journal`table; `$opt_text`table];
if[null tbl; refuse "-table T is required"];
note[`table;string tbl];
note[`operation;string op];
note[`argument;arg];
note[`mode;$[undoing; "undo"; "operation"]];
note[`applied;applying];

/ The operation's parameters: the column it works on and what it becomes.
target_col:`;
new_value:`;
reorder_to:`symbol$();
if[op~`reorder; reorder_to:`$"," vs arg];
if[op~`rename;
    p:pair[arg;"-rename"]; target_col:p 0; new_value:`$p 1;
    if[not validcolname new_value; refuse string[new_value]," is not a valid column name"]];
if[op~`attr;
    p:pair[arg;"-attr"]; target_col:p 0; new_value:`$p 1;
    if[not new_value in `p`s`g`u`none; refuse "-attr takes p, s, g, u or none, not ",p 1];
    if[new_value~`none; new_value:`]];
cast_to:" ";
if[op~`cast;
    p:pair[arg;"-cast"]; target_col:p 0;
    cast_to:$[(1=count p 1) and (first p 1) in value cast_types; first p 1;
              (`$p 1) in key cast_types; cast_types `$p 1;
              refuse "-cast cannot cast to ",p[1],": symbol changes the enumeration; give one of ",", " sv string key cast_types]];

/ ---------------------------------------------------------- where the table is

dirs:table_dirs tbl;
holding:dirs where has_d each dirs;
strays:dirs where (present each dirs) and not has_d each dirs;
lacking:dirs where not present each dirs;
note[`partitions_holding;partition_of each holding];
note[`partitions_lacking;partition_of each lacking];
note[`partitions_without_d;partition_of each strays];
say "hdb:        ",hdb_text;
say "table:      ",string tbl;
say "holding:    ",string[count holding]," partition(s)",$[count holding; " (",(", " sv partition_of each holding),")"; ""];
if[count lacking; say "lacking:    ",string[count lacking]," partition(s) (",(", " sv partition_of each lacking),")"];
if[count strays; say "no .d:      ",(", " sv partition_of each strays)," - a directory named ",string[tbl]," with no .d, never written"];
if[0=count dirs; refuse "no date partition in ",hdb_text];

shapes:shape_of each holding;
distinct_shapes:distinct shapes;
note[`shapes;{[s;sh] (shape_json s),enlist[`partitions]!enlist partition_of each holding where sh~\:s}[;shapes] each distinct_shapes];

/ Each distinct shape and the partitions that hold it, for a refusal.
shape_lines:{[]
    {[s] "    ",describe_shape[s],"  <- ",", " sv partition_of each holding where shapes~\:s} each distinct_shapes}

/ ---------------------------------------------------- source and target shape

/ The interrupted run of this table, if one was left: (path; journal) or ().
open_run:{[]
    js:journals[];
    js:js where {[t;j] (t~`$j[1]`table) and (j[1]`status) in ("started";"undoing")}[tbl] each js;
    $[count js; first js; ()]}

resumed:0b;
if[not undoing;
    open:open_run[];
    if[count open;
        j:open 1;
        same:(op~`$j`operation) and arg~j`argument;
        if[(not same) or (j`status)~"undoing";
            refuse "an interrupted run on ",string[tbl]," left ",(1_string open 0)," (",j[`operation]," ",j[`argument],", ",j[`status],"): finish it with the same operation, or -undo it, first"];
        resumed:1b; journal_path:open 0; journal:j]];
note[`resumed;resumed];

source_shape:$[resumed or undoing; shape_from journal`source; count holding; first distinct_shapes; ()];
if[(not resumed or undoing) and 1<count distinct_shapes;
    refuse "the partitions holding ",string[tbl]," disagree on columns, order or types:\n","\n" sv shape_lines[]];

/ The shape a partition has once the operation is done.
target_of:{[s]
    c:s`cols; tys:s`types;
    $[op~`reorder; `cols`types!(reorder_to;tys c?reorder_to);
      op~`rename; `cols`types!(@[c;where c=target_col;:;new_value];tys);
      op~`cast; `cols`types!(c;@[tys;where c=target_col;:;cast_to]);
      s]}

/ --------------------------------------------------------------------- check

if[op~`check;
    note[`status;"planned"];
    say "shape:      ",$[count holding; describe_shape source_shape; "no partition holds it"];
    emit[];
    exit 0];

/ ---------------------------------------------------------------------- fill

/ An empty table enumerated against the HDB's sym file without writing it:
/ an empty symbol column adds no symbol, so the shared file is left alone.
enumerated_empty:{[t]
    sc:where 11h=type each flip t;
    $[0=count sc; t;
      not ()~key sym_file; @[t;sc;{[c] `sym$c}];
      .Q.en[hdb;t]]}

if[op~`fill;
    / an empty declared table counts 0 like no table at all: say which it is apart
    is_declared:0b;
    if[count schema_text:opt_text`schema;
        before_load:tables[];
        system "l ",schema_text;
        if[is_declared:tbl in tables[] except before_load; declared:0#value tbl]];
    template_dir:$[count tmpl:opt_text`template; holding where (partition_of each holding)~\:tmpl; -1#holding];
    if[(not is_declared) and 0=count template_dir;
        refuse $[count tmpl; "no partition ",tmpl," holds ",string tbl;
                 "no partition holds ",string[tbl]," and no -schema declares it: there is no schema to fill from"]];
    if[(not is_declared) and 1<count distinct_shapes;
        refuse "the partitions holding ",string[tbl]," disagree, so none is a template:\n","\n" sv shape_lines[]];
    template:$[is_declared; declared; 0#get ` sv first[template_dir],`];
    template_shape:`cols`types!(cols template;type_char each type each value flip template);
    note[`schema_from;$[is_declared; "declaration"; "partition ",partition_of first template_dir]];
    note[`target;shape_json template_shape];
    say "schema:     ",$[is_declared; "declared in ",opt_text`schema; "partition ",partition_of first template_dir];
    say "shape:      ",describe_shape template_shape;
    if[count other:holding where not shapes~\:template_shape;
        refuse "partitions holding ",string[tbl]," differ from the template (",describe_shape[template_shape],"), which a fill never rewrites:\n","\n" sv shape_lines[]];
    note[`steps;{[d] `partition`actions!(partition_of d;enlist "write an empty ",string[tbl])} each lacking];
    {[d] say "  ",partition_of[d],": write an empty ",string tbl} each lacking;
    if[0=count lacking; say "nothing to do: every partition holds ",string tbl];
    if[not applying;
        note[`status;"planned"];
        if[count lacking; say "dry run: nothing written"];
        emit[]; exit 0];
    empty:enumerated_empty template;
    .[{[e;ds] {[e;d] (` sv d,`) set e} [e] each ds};(empty;lacking);{[e] fail "writing an empty table stopped: ",e}];
    found:{[s;d] $[not has_d d; partition_of[d],": no table"; (shape_of d)~s; ""; partition_of[d],": ",describe_shape shape_of d]}[template_shape] each table_dirs tbl;
    found:found where 0<count each found;
    note[`final_check;`clean`problems!(0=count found;found)];
    if[count found; fail "final check: ",("; " sv found)];
    note[`status;"applied"];
    say "filled ",string[count lacking]," partition(s); final check clean";
    emit[];
    exit 0];

/ ------------------------------------------------------- the per-partition state

/ Where a partition stands against the operation: `source (not yet changed),
/ `target (changed), `partial (a -rename stopped between its file and its .d),
/ or `other (neither: it disagrees with the run).
/ @param tgt the target shape
/ @param d the table directory
state_of:{[tgt;d]
    s:shape_of d;
    $[op~`attr;
        $[not s~source_shape; `other; new_value~col_attr[d;target_col]; `target; `source];
      op~`rename;
        $[s~source_shape; `source; s~tgt; `target;
          (target_col in s`cols) and present in_dir[d;new_value]; `partial; `other];
      op~`cast;
        $[s~tgt; `target;
          not s~source_shape; `other;
          any present each in_dir[d;] each `$string[target_col],/:(".uqf-orig";".uqf-new"); `partial;
          `source];
      s~source_shape; `source;
      s~tgt; `target;
      `other]}

if[0=count holding; refuse "no partition holds ",string tbl];
target_shape:target_of source_shape;
note[`source;shape_json source_shape];
note[`target;shape_json target_shape];

/ ------------------------------------------------------------ the operation's plan

if[not undoing;
    if[op in `rename`attr`cast;
        if[not target_col in source_shape`cols; refuse "no column ",string[target_col]," in ",string tbl]];
    if[op~`reorder;
        if[not (asc reorder_to)~asc source_shape`cols;
            refuse "-reorder must list exactly the columns of ",string[tbl],": ",", " sv string source_shape`cols]];
    if[(op~`rename) and new_value in source_shape`cols; refuse string[new_value]," is already a column of ",string tbl];
    if[op~`cast;
        was:source_shape[`types] source_shape[`cols]?target_col;
        if[was in "s*"; refuse "-cast cannot cast ",string[target_col],", a ",$[was="s"; "symbol column: that changes the enumeration"; "nested column"]];
        if[was=cast_to; refuse string[target_col]," is already of type ",cast_to]]];

states:state_of[target_shape] each holding;
/ For -undo the journal names the partitions the run touched; the rest are not its.
journalled:$[count journal; journal`partitions; ()];
journal_parts:$[count journalled; `$journalled`partition; `symbol$()];
mine:$[undoing; (part_sym each holding) in journal_parts; count[holding]#1b];
if[any mine and states=`other;
    refuse "partitions of ",string[tbl]," match neither ",describe_shape[source_shape]," nor ",describe_shape[target_shape],":\n","\n" sv shape_lines[]];

/ The files one partition's step writes, renames or deletes, as text.
/ @param st the partition's state
step_of:{[st]
    c:string target_col;
    $[op~`reorder; enlist "write .d";
      op~`rename; $[st~`partial; enlist "write .d"; ("rename ",c," -> ",string new_value;"write .d")];
      op~`attr; enlist "set ",c,"'s attribute to ",$[null new_value; "none"; string new_value];
      op~`cast; ("write ",c,".uqf-new as ",cast_to;"link ",c," -> ",c,".uqf-orig";"rename ",c,".uqf-new -> ",c);
      ()]}

/ The undo of one partition, as text: what -undo would do there.
undo_step_of:{[d;st]
    c:string target_col;
    $[st~`source; ();
      op~`reorder; enlist "write .d as before";
      op~`rename; $[st~`partial; enlist ("rename ",string[new_value]," -> ",c);
                    ("rename ",string[new_value]," -> ",c;"write .d as before")];
      op~`attr; enlist "set ",c,"'s attribute back";
      op~`cast; $[present in_dir[d;`$c,".uqf-orig"]; enlist "rename ",c,".uqf-orig -> ",c;
                  enlist "cast ",c," back to ",source_shape[`types] source_shape[`cols]?target_col];
      ()]}

todo:$[undoing; holding where mine and not states=`source; holding where not states=`target];
todo_states:states holding?todo;
skipped_done:$[undoing; (); holding where states=`target];
steps:$[undoing; undo_step_of'[todo;todo_states]; step_of each todo_states];
note[`steps;{[d;a] `partition`actions!(partition_of d;a)}'[todo;steps]];
note[`partitions_touched;partition_of each todo];
note[`partitions_already_done;partition_of each skipped_done];

say "operation:  ",$[undoing; "undo of "; ""],string[op]," ",arg;
say "shape:      ",describe_shape source_shape;
say "becomes:    ",describe_shape $[undoing; source_shape; target_shape];
{[d;a] say "  ",partition_of[d],": ",", " sv a}'[todo;steps];
if[count skipped_done; say "  already done: ",", " sv partition_of each skipped_done];

/ A -cast rewrites every touched partition's column: what that costs, and
/ whether casting back restores it exactly (what -undo does once the
/ originals are gone).
if[(op~`cast) and not undoing;
    files:in_dir[;target_col] each todo;
    note[`bytes_rewritten;sum hcount each files];
    say "rewrites:   ",string[sum hcount each files]," bytes";
    back:source_shape[`types] source_shape[`cols]?target_col;
    tried:{[to;back;f] v:get f; .[{[to;back;v] w:to$v; (v~back$w;1b)};(to;back;v);{[e] (0b;0b)}]}[cast_to;back] each files;
    if[not all last each tried;
        refuse string[target_col]," cannot be cast to ",cast_to," in ",", " sv partition_of each todo where not last each tried];
    lossy:todo where not first each tried;
    note[`lossy;partition_of each lossy];
    if[count lossy;
        say "WARNING:    the cast loses information in ",(", " sv partition_of each lossy),": once the run completes, -undo can only cast back"]];

/ An attribute is applied in memory first: one the data does not satisfy
/ (s# on unsorted values, u# on duplicates) is refused here, not part way.
if[(op~`attr) and (not undoing) and not null new_value;
    bad:todo where not {[a;f] .[{[a;f] a#get f; 1b};(a;f);{[e] 0b}]}[new_value] each in_dir[;target_col] each todo;
    if[count bad; refuse "the data in ",(", " sv partition_of each bad)," does not satisfy `",string[new_value],"#"]];

if[0=count todo;
    note[`status;"planned"];
    say "nothing to do";
    emit[];
    exit 0];

/ -journal names it: the front end passes the path its dry run showed, so the
/ journal a person was told about is the one written.
if[not undoing;
    journal_path:$[resumed; journal_path;
        count opt_text`journal; hsym `$opt_text`journal;
        ` sv journal_dir,`$(ssr[;":";""] ssr[;".";""] 23#string .z.p),"-",string[tbl],".json"]];
note[`journal;1_string journal_path];
say "journal:    ",1_string journal_path;

if[not applying;
    note[`status;"planned"];
    say "dry run: nothing written";
    emit[];
    exit 0];

/ -------------------------------------------------------------------- writing

/ The journal, written before the first write: each touched partition's .d
/ and attributes, so -undo can put them back.
if[not undoing or resumed;
    if[not resumed;
        system "mkdir -p ",shell_path journal_dir;
        entry:{[d] c:get in_dir[d;`.d]; `partition`dir`d`attrs!(partition_of d;1_string d;string c;string col_attr[d] each c)};
        journal:`version`hdb`table`operation`argument`status`created`source`target`partitions!(
            1;hdb_text;string tbl;string op;arg;"started";string .z.p;
            shape_json source_shape;shape_json target_shape;entry each todo);
        write_json[journal_path;journal]]];
if[undoing;
    journal[`status]:"undoing";
    write_json[journal_path;journal]];

interrupt_after:$[count t:opt_text`test_interrupt_after; "J"$t; 0N];
written:0;

/ After each partition: the test suite's stand-in for a kill.
tick:{[]
    written::written+1;
    if[(not null interrupt_after) and written>=interrupt_after;
        -2 "hdb_alter: stopped after ",string[written]," partition(s) (-test_interrupt_after)";
        exit 3];}

/ The journal's entry for a partition.
entry_of:{[d] journalled journal_parts?part_sym d}

/ The suffixes of a column's files: the column, and the # and ## files a
/ nested one keeps beside it.
suffixes:{[d;c] sx:("";"#";"##"); sx where present each in_dir[d;] each `$string[c],/:sx}

/ Rename a column's files, every one of them.
rename_files:{[d;from;to]
    {[d;from;to;sx] move[in_dir[d;`$string[from],sx];in_dir[d;`$string[to],sx]]}[d;from;to] each suffixes[d;from];}

/ Write a column's value: to name.uqf-new, then renamed over the column. The
/ original stays reachable as name.uqf-orig (a hard link) until it is dropped.
/ @param keep whether to keep the original as .uqf-orig
replace_col:{[d;c;v;keep]
    f:in_dir[d;c];
    fresh:in_dir[d;`$string[c],".uqf-new"];
    orig:in_dir[d;`$string[c],".uqf-orig"];
    .[fresh;();:;v];
    if[keep;
        if[present orig; hdel orig];
        system "ln ",shell_path[f]," ",shell_path orig];
    move[fresh;f];}

/ One partition, forwards.
forward:{[d;st]
    c:target_col;
    if[op~`reorder; @[d;`.d;:;reorder_to]];
    if[op~`rename;
        if[st~`source; rename_files[d;c;new_value]];
        dc:get in_dir[d;`.d];
        @[d;`.d;:;@[dc;where dc=c;:;new_value]]];
    if[op~`attr; @[d;c;new_value#]];
    if[op~`cast;
        v:get in_dir[d;c];
        a:attr v;
        w:cast_to$v;
        if[not null a; w:a#w];
        replace_col[d;c;w;1b]];
    tick[];}

/ One partition, backwards, from its journal entry.
backward:{[d]
    e:entry_of d;
    c:target_col;
    was_d:`$e`d;
    if[op~`reorder; @[d;`.d;:;was_d]];
    if[op~`rename;
        if[present in_dir[d;new_value]; rename_files[d;new_value;c]];
        @[d;`.d;:;was_d]];
    if[op~`attr; @[d;c;(`$e[`attrs] was_d?c)#]];
    if[op~`cast;
        orig:in_dir[d;`$string[c],".uqf-orig"];
        stale:in_dir[d;`$string[c],".uqf-new"];
        if[present stale; hdel stale];
        $[present orig; move[orig;in_dir[d;c]];
          [back:source_shape[`types] source_shape[`cols]?c;
           a:`$e[`attrs] was_d?c;
           w:back$get in_dir[d;c];
           if[not null a; w:a#w];
           replace_col[d;c;w;0b]]]];
    tick[];}

.[{[ds;sts] $[undoing; backward each ds; forward'[ds;sts]]};(todo;todo_states);
    {[e] fail "the run stopped part way (",e,"): the journal ",(1_string journal_path)," holds what -undo needs"}];

/ ---------------------------------------------------------------- final check

/ Every partition holding the table, read again, against what it should now be.
/ @return one line per partition that is not as it should be
problems:{[]
    now:table_dirs tbl;
    hold:now where has_d each now;
    want:$[undoing; source_shape; target_shape];
    bad:{[want;d] s:shape_of d; $[s~want; ""; partition_of[d],": ",describe_shape s]}[want] each hold;
    if[op~`attr;
        bad:bad,{[d]
            mine:(part_sym d) in journal_parts;
            if[undoing and not mine; :""];
            e:$[undoing; entry_of d; ()];
            want:$[undoing; `$e[`attrs] (`$e`d)?target_col; new_value];
            $[want~col_attr[d;target_col]; "";
              partition_of[d],": ",string[target_col],"'s attribute is `",string col_attr[d;target_col]]} each hold];
    if[op~`cast;
        bad:bad,{[d] $[present in_dir[d;`$string[target_col],".uqf-new"]; partition_of[d],": a stray ",string[target_col],".uqf-new"; ""]} each hold];
    bad where 0<count each bad}

found:problems[];
note[`final_check;`clean`problems!(0=count found;found)];
if[count found;
    journal[`status]:$[undoing; "undo-failed"; "failed"];
    write_json[journal_path;journal];
    fail "final check:\n  ","\n  " sv found];

/ A clean forward -cast drops the originals it kept: the run is complete.
if[(op~`cast) and not undoing;
    origs:{[d] in_dir[d;`$string[target_col],".uqf-orig"]} each todo;
    hdel each origs where present each origs];

journal[`status]:$[undoing; "undone"; "completed"];
journal[`finished]:string .z.p;
write_json[journal_path;journal];
note[`status;"applied"];
say $[undoing; "undone"; "applied"]," in ",string[count todo]," partition(s); final check clean";
emit[];
exit 0
