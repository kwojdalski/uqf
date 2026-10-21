/ fill_hdb_partitions.q - make every HDB partition hold every declared
/ table, so a query that spans them can run at all.
/ .
/ Run as:  q scripts/gates/fill_hdb_partitions.q <hdb root> <generated database.q>
/ .
/ A THIN, SCHEMA-DRIVEN WRAPPER AROUND KX's dbmaint.q (#1423). This script
/ decides WHAT is missing, from database.q; dbmaint does the writing.
/ lib/kx-dbmaint/dbmaint.q is KX's own partitioned-database maintenance
/ utility, vendored byte-for-byte at a pinned commit (lib/kx-dbmaint/NOTICE.md)
/ and loaded below. Used from it:
/ .
/   allpaths  every partition's directory for a table, following par.txt in a
/             segmented database (the hand-rolled walk this replaces did not)
/   add1col   one column into one table directory, repeated to that
/             partition's own row count and appended to `.d` - and a no-op
/             for a column the directory already has
/ .
/ WHAT IS NOT dbmaint's, AND WHY. Everything that decides rather than writes:
/ .
/   - Which objects are missing. dbmaint knows no schema; its fixtable needs a
/     "good" partition to copy the column list from, and so cannot repair a
/     table that no partition holds - which is exactly the bootstrap case
/     (#348: the vendored sample holds `quote` and `trade`, and no partition
/     anywhere holds a uqf table yet). The declaration is the authority here,
/     the same file the tickerplant loads.
/   - `addtable` is NEVER called. It writes an empty table into every
/     partition without asking whether one is already there, so one call over
/     a database that was merely partly short would overwrite the partitions
/     that were not. Nor is add1table, the single-directory step under it:
/     it writes with the amend `@[dir;`;:;table]`, which PeachQ does not
/     implement ('nyi), and the bootstrap of a PeachQ stack depends on
/     writing the missing tables of the sample partitions. A directory this
/     script has just found to hold no table is written with `set`, the form
/     wdb.q's `inittable` uses, enumerated against the HDB's sym file.
/   - `addcol` is not called either. It walks every partition and refuses a
/     column name that is a q keyword; a declared name is the schema's own, and
/     refusing it half way through a database would leave the partitions
/     repaired unevenly. The per-directory add1col is the same write without
/     the interactive name check, driven from what each partition lacks.
/   - The declared default. A column the partition lacks is written as the
/     declared type's null; a symbol default is enumerated here, with .Q.en,
/     rather than by dbmaint's `enum`, which enumerates with a file-handle `?`
/     that PeachQ does not implement.
/   - Checking the result. dbmaint reports what it did, not whether the
/     database is now whole. The last step re-reads every partition and exits
/     1 naming anything still missing.
/ .
/ TWO LEVELS, THE SAME FAULT. A missing TABLE fails a query naming whichever
/ table sorts first; a missing COLUMN fails it naming that column in the
/ partition that lacks it - "./2026.01.01/book/venue. OS reports: No such
/ file or directory". Same cause (the schema grew after the partition was
/ written), same repair, so both happen here in one pass.
/ .
/ ADDITIVE, ALWAYS. A table directory that holds a `.d` is never emptied and a
/ column that exists is never rewritten, whatever is in it. A table or a
/ column a partition holds and database.q no longer declares is left alone:
/ that is history, and deleting history is not this script's business. A
/ column whose declared TYPE changed is reported by `uqs data hdb-check`
/ and not touched here - "the declaration changed" and "the bytes on disk are
/ wrong" are different claims, and only a person can tell which. dbmaint's
/ castcol, renamecol, deletecol and rentable load with the file and are called
/ by nothing here. A directory that exists but holds no `.d` is not a table this
/ script may write into: it is reported and left alone, and the check fails.
/ Running it twice changes nothing the second time.
/ .
/ WHAT THIS DOES NOT CLAIM. No transaction: a kill between two columns leaves
/ the partitions written so far repaired and the rest not, and the next run
/ finishes the job. No concurrent-writer safety: it takes no HDB write lock,
/ exactly as before (hdb_lock.q is the migration's, and the bootstrap runs
/ with the stack's writers stopped or not at all). And no reload: a running
/ HDB process must be told to \l . by whoever runs this.
/ .
/ PEACHQ. dbmaint.q loads there, and the four primitives above call nothing
/ but file reads and writes. Its `.os` helpers (cpy, del, ren) shell out to
/ cp/rm/mv through `system`; they back copycol, deletecol and renamecol, which
/ nothing here calls.

/ A refusal: said on stderr, and exit 1. A script q loads that signals does not
/ end the process - q prints the error and waits on stdin, which is closed, and
/ exits 0 - so the caller (hdb_fill.fill_hdb_partitions, which reads the exit
/ code) would be told a repair that never ran had succeeded.
die:{[msg] -2 "fill_hdb_partitions: ",msg; exit 1}

hdb_root:hsym `$first .z.x;
schema_file:hsym `$.z.x 1;

if[()~key hdb_root; die "no HDB at ",string hdb_root];
if[()~key schema_file; die "no schema at ",string schema_file];

/ dbmaint.q, found from this script's own path: scripts/gates/ is two levels
/ below the tree that holds lib/, in a checkout and in a release alike. .z.f
/ is the script as it was named on the command line, so a bare name resolves
/ against the directory q was started in. Never the working directory's lib/:
/ a script run from one tree must load the dbmaint that tree ships.
script_dir:{[f] $[0=count i:where "/"=f; ""; (1+last i)#f]} string .z.f;
dbmaint_file:hsym `$script_dir,"../../lib/kx-dbmaint/dbmaint.q";
if[()~key dbmaint_file; die "no dbmaint.q at ",string[dbmaint_file]," - lib/kx-dbmaint ships with every release"];
system"l ",1_string dbmaint_file;

/ Load the definitions at ROOT and take the difference, rather than into a
/ namespace of their own. `\d .hdbfill` would be tidier to read and does
/ not work: inside it, the root-level `schema_file` this script just
/ assigned does not resolve, which is invariant 5's trap in
/ scripts/processes/torq_pipeline.q wearing a different hat. Diffing
/ `tables[]` across the load names exactly what the file declares, with no
/ namespace to get lost in.
before_load:tables[];
system"l ",1_string schema_file;
declared:tables[] except before_load;

/ A partition directory's name: a date, the only scheme this stack writes.
/ allpaths accepts anything starting with a digit; a stray `2026.01.01.bak`
/ or a file called `1` is not a partition and must not get a table written
/ into it.
date_shaped:{[name] name like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"}

/ The partition a table directory sits in.
/ @param dir a table directory, `:<root>/<partition>/<table>
/ @return the partition's directory name, a string
partition_name:{[dir] first -2#"/" vs string dir}

/ Every date-partitioned directory dbmaint's allpaths names for a table, in
/ partition order. A table directory need not exist yet: allpaths builds the
/ path a partition WOULD hold it at, which is what lets a table absent from
/ every partition be written without a reference partition.
/ @param root the HDB root
/ @param table_name the table name
/ @return table directories, oldest partition first
table_dirs:{[root;table_name]
    dirs:allpaths[root;table_name];
    dirs:dirs where date_shaped each partition_name each dirs;
    dirs iasc partition_name each dirs}

/ The declared default for one column: the empty typed list, which add1col
/ repeats to the partition's own row count, so it is the declared type's null
/ for every row. A symbol column must point into root/sym or the database
/ cannot be mapped, so it is enumerated here.
/ @param root the HDB root, whose sym file a symbol column enumerates against
/ @param table_name the table whose root-level value carries the declared shape
/ @param col the column
/ @return an empty typed list, enumerated if symbol
declared_default:{[root;table_name;col]
    empty:0#(value table_name)col;
    $[11h=abs type empty; (.Q.en[root] flip enlist[`v]!enlist empty)`v; empty]}

/ The columns a table directory holds, from its `.d`: the authority for what a
/ splayed table holds, not the directory listing, because a nested column
/ writes TWO files (`bids` and `bids#`) and appears in `.d` once. This is
/ dbmaint's allcols, spelled with the path joined by `sv`: allcols reads
/ `get tabledir,`.d`, a symbol VECTOR used as a path, which PeachQ refuses
/ ('io). Reading is what every run does, including the run with nothing to
/ repair, so it must not be the step that fails there.
/ @param dir a table directory that holds a `.d`
/ @return the column names
disk_cols:{[dir] get ` sv dir,`.d}

/ The declared columns a table directory lacks.
/ @param table_name the table name
/ @param dir a table directory that holds a `.d`
/ @return the missing columns, in declared order
missing_cols:{[table_name;dir] (cols value table_name) except disk_cols dir}

/ Repair one table directory.
/ @return `table if it wrote an empty table, `skipped for a directory that
/   exists with no .d, else the columns it added (empty when none was missing)
repair_dir:{[root;table_name;dir]
    if[()~key ` sv dir,`.d;
        / a directory that exists with no .d is not ours to write into
        if[not ()~key dir; :`skipped];
        (` sv dir,`) set .Q.en[root;0#value table_name];
        :`table];
    absent:missing_cols[table_name;dir];
    {[root;table_name;dir;col] add1col[dir;col;declared_default[root;table_name;col]]}[root;table_name;dir] each absent;
    absent}

/ What is still wrong, read back from disk rather than taken from what the
/ repair says it did: every declared table must hold a .d that names every
/ declared column, and a file for each.
/ @return one line per problem, empty if the database is whole
problems_in:{[table_name;dir]
    if[()~key ` sv dir,`.d; :enlist "no table at ",1_string dir];
    lacks:missing_cols[table_name;dir];
    if[count lacks; :enlist "columns not in .d of ",(1_string dir),": ",", " sv string lacks];
    declared_cols:cols value table_name;
    files:` sv/:dir,/:declared_cols;
    gone:declared_cols where {()~key x} each files;
    $[count gone; enlist "column files missing in ",(1_string dir),": ",", " sv string gone; ()]}

-1 "hdb:        ", string hdb_root;
-1 "declared:   ", string[count declared], " table(s) in ", string schema_file;

/ One row per (table, partition): the directory the table lives or would live in.
dirs:raze {[root;table_name] table_dirs[root;table_name]}[hdb_root] each declared;
tabs:raze {[root;table_name] count[table_dirs[root;table_name]]#table_name}[hdb_root] each declared;
parts:partition_name each dirs;
partitions:asc distinct parts;
-1 "partitions: ", string[count partitions], " (", (", " sv partitions), ")";

/ No partition at all - an empty HDB, the state of a fresh install - is
/ already whole: there is nothing a declared table could be missing from, and
/ this script never creates a partition (#1513).
if[0=count partitions; -1 ""; -1 "already rectangular - nothing to do"; exit 0];

outcomes:.[{[tb;dr] repair_dir[hdb_root]'[tb;dr]};(tabs;dirs);{[e] die "repair stopped: ",e}];
/ Cast to boolean and long: `each` over an empty general list returns an
/ empty general list, and `sum` of that is () rather than 0, which would make
/ the report's `$[...]` condition a list and signal 'type (#1513).
wrote:"b"${x~`table} each outcomes;
skipped:"b"${x~`skipped} each outcomes;
added:not wrote or skipped;
written:sum wrote;
patched:sum "j"$count each outcomes where added;
{[parts;outcomes;wrote;added;part]
    here:parts~\:part;
    n:sum wrote and here;
    if[n>0; -1 "  ",part,": wrote ",string[n]," missing table(s)"];
    grew:raze outcomes where added and here;
    if[count grew;
        -1 "  ",part,": added ",string[count grew]," column(s) - ", ", " sv string grew];
    }[parts;outcomes;wrote;added] each partitions;
if[any skipped;
    -2 "fill_hdb_partitions: left alone, no .d in: ",", " sv 1_'string dirs where skipped];

/ Check the result.
found:.[{[tb;dr] raze problems_in'[tb;dr]};(tabs;dirs);{[e] die "could not check the result: ",e}];
if[count found;
    -2 "fill_hdb_partitions: the database is still not whole:";
    -2 each "  ",/:found;
    exit 1];

-1 "";
-1 $[(written=0) and patched=0;
    "already rectangular - nothing to do";
    "filled ",string[written]," table directory(ies) and ",
      string[patched]," column(s)"];
exit 0
