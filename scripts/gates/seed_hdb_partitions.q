/ seed_hdb_partitions.q - copy one runtime's HDB partitions into another's
/ (#766): `uqs data seed`.
/ .
/ Run as:  q scripts/gates/seed_hdb_partitions.q <source hdb root> <target hdb root>
/            <target database.q> <tables, comma-separated, or "-" for all>
/            <first date or "-"> <last date or "-"> <overwrite: 0 or 1>
/ .
/ WHY. Each runtime keeps its own HDB, and a fresh one starts from the
/ starter pack's two sample partitions. Comparing torq against uqf on the
/ same history means giving torq that history: the partitions of the tables
/ both runtimes declare, copied across.
/ .
/ WHY NOT cp -r. A symbol column in a splayed table holds indices into its
/ HDB's `sym` file, not symbols. Two HDBs enumerate in different orders, so a
/ copied column decodes, in the target, to whatever its target sym file
/ holds at those indices - every value plausible, every value wrong. So each
/ column is read, decoded against the SOURCE's domain, and re-enumerated
/ against the TARGET's sym file.
/ .
/ BY HAND, NOT .Q.en. `enumerate` below is what .Q.en does - read the sym
/ file, append the new symbols, write it back, cast - spelled out because
/ PeachQ's .Q.en returns symbols unenumerated and it has no file `?`. One
/ path, correct on both, and testable without a KDB-X licence.
/ .
/ Column by column (`.d`, then each file) rather than `get` of the table
/ directory: it reads the same bytes, and it is what PeachQ supports too.
/ .
/ TWO PASSES. Every partition to copy is read and checked first; only when
/ all of them pass is anything written. A schema mismatch is refused naming
/ the table and the column, with the target exactly as it was. The source is
/ only ever read.
/ .
/ What is refused: a table the target's schema does not declare, and a
/ column whose type differs from the declaration or that the declaration
/ does not have. A column the source lacks is not refused: the filler, run
/ straight after, adds it as nulls. A partition the target already holds a
/ table for is skipped, unless overwrite is 1.

args:.z.x;
if[7>count args; -2 "seed_hdb_partitions: needs 7 arguments - see the header"; exit 2];
src_root:hsym `$args 0;
dst_root:hsym `$args 1;
schema_file:hsym `$args 2;
/ Each argument is a string, so "-" is compared as one: (),"-", not the char.
none:{[a] a~(),"-"};
asked:$[none args 3; `symbol$(); `$"," vs args 3];
first_date:$[none args 4; 0Nd; "D"$args 4];
last_date:$[none args 5; 0Nd; "D"$args 5];
overwrite:(args 6)~(),"1";

fail:{[msg] -2 "seed_hdb_partitions: ",msg; exit 1};

if[()~key src_root; fail "no source HDB at ",string src_root];
if[()~key dst_root; fail "no target HDB at ",string dst_root];
if[()~key schema_file; fail "no target schema at ",string schema_file];
if[src_root~dst_root; fail "the source and the target are the same HDB"];

/ The target's declared tables, loaded at root as the filler does.
before_load:tables[];
system"l ",1_string schema_file;
declared:tables[] except before_load;

wanted:$[count asked; asked; declared];
if[count undeclared:wanted except declared;
    fail "the target does not declare ",(", " sv string undeclared),
        " - only a table both runtimes declare can be seeded"];

/ The partition dates under a root, oldest first, within the asked range.
parts:{[root]
    entries:key root;
    d:asc "D"$string entries where entries like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]";
    / An absent bound is an infinite one, atom with atom: `vector^atom` is
    / 'nyi on KDB-X, so the d^bound this used to be refused every seed there.
    lo:-0Wd^first_date;
    hi:0Wd^last_date;
    d where (d>=lo) and d<=hi}

/ A source column's values, with an enumerated one decoded against the
/ SOURCE's domain file - loaded fresh each time, because .Q.en below
/ replaces the same global with the target's.
decoded:{[root;col]
    if[not 20h=type col; :col];
    domain:key col;
    domain set get ` sv root,domain;
    value col}

/ One source partition's table, decoded, as (columns;values;attributes).
read_table:{[root;part;table_name]
    dir:.Q.par[root;part;table_name];
    c:get ` sv dir,`.d;
    raw:{[dir;c] get ` sv dir,c}[dir] each c;
    (c;decoded[root] each raw;attr each raw)}

/ Why `got`'s columns cannot be written as `table_name`, or "" if they can.
mismatch:{[table_name;got]
    want:exec c!t from meta value table_name;
    have:(got 0)!{.Q.ty x} each got 1;
    if[count extra:(key have) except key want;
        :"column(s) ",(", " sv string extra)," that the target does not declare"];
    bad:c where (want c)<>have c:key have;
    if[count bad;
        :"column ",string[first bad]," is ",(have first bad)," in the source but ",
            (want first bad)," in the target's declaration"];
    ""}

/ Pass one: what to copy, every one read and checked.
plan:([] part:`date$(); table_name:`symbol$());
skipped:0;
{[part]
    {[part;table_name]
        if[()~key .Q.par[src_root;part;table_name]; :()];
        if[(not overwrite) and not ()~key ` sv .Q.par[dst_root;part;table_name],`.d;
            `skipped set skipped+1; :()];
        why:mismatch[table_name;read_table[src_root;part;table_name]];
        if[count why; fail string[table_name]," on ",string[part],": ",why,
            " - nothing was written"];
        `plan insert (part;table_name);
        }[part] each wanted;
    } each parts src_root;

/ A symbol vector enumerated against the TARGET's sym file, extending the
/ file with any symbol it lacks: .Q.en's work, by hand (see the header).
enumerate:{[root;v]
    if[not 11h=type v; :v];
    file:` sv root,`sym;
    `sym set @[get;file;`symbol$()];
    if[count new:(distinct v) except sym; `sym set sym,new; file set sym];
    `sym$v}

/ The source's attribute put back (`p# on sym, as the wdb sorts it) where
/ the interpreter has it; PeachQ does not, and the column stays plain.
with_attr:{[a;v] $[null a; v; @[{x#y}[a];v;{[v;e] v}[v]]]}

/ Write a splayed table column by column, then `.d` - the same bytes a `set`
/ of the table writes, without PeachQ's splayed set, which re-enumerates an
/ already enumerated column under a partition and stores the wrong indices.
write_table:{[dir;c;vals]
    {[dir;c;v] (` sv dir,c) set v}[dir]'[c;vals];
    (` sv dir,`.d) set c;
    count first vals}

/ Pass two: write them, re-enumerated against the target, attributes kept.
{[part;table_name]
    got:read_table[src_root;part;table_name];
    n:write_table[.Q.par[dst_root;part;table_name];got 0;with_attr'[got 2;enumerate[dst_root] each got 1]];
    -1 "  ",string[part]," ",string[table_name],": ",string[n]," row(s)";
    }'[plan`part;plan`table_name];

-1 "seeded ",string[count plan]," table partition(s) from ",string[src_root]," into ",string[dst_root],
    $[skipped; "; skipped ",string[skipped]," the target already holds (overwrite to replace)"; ""];
exit 0
