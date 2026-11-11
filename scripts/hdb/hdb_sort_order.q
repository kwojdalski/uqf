/ hdb_sort_order.q - the HDB partitions that are not in their declared sort order (#2288).
/ .
/ Run as:  q scripts/hdb/hdb_sort_order.q <hdb root> <partitions file>
/ .
/ The root holds the sym file the enumerated columns are read against. The partitions file names the partitions to read, one directory per line - what
/ scripts/hdb/hdb_layout.q found, as stack/hdb_layout.py writes it. Reads, writes nothing:
/ it is the DRY half of the sort migration `uqs data check --apply` runs (hdb_fill.q).
/ .
/ A table that declares a sort rule (src/etl/plant_tables.q `sorting`) is stored sorted by its
/ columns with their attributes, and holds its row numbers if it declares a column for them. A
/ partition written before the rule, or by something that did not follow it, is listed here
/ with what is wrong, because a markout's as-of join against it would be wrong or would scan.
/ .
/ One line per finding - unsorted|<partition directory>|<table>|<why> - then DONE. q exits 0
/ whatever happens while a script runs, so the caller believes DONE, never the exit code.
hdb_root:hsym `$first .z.x;
parts_file:hsym `$.z.x 1;
if[()~key parts_file; '"hdb_sort_order: no partitions file at ",string parts_file];
/ a database with no enumerated column has no sym file, and nothing to decode
if[not ()~key ` sv hdb_root,`sym; `sym set get ` sv hdb_root,`sym];

/ src/etl, found from this script's own path (scripts/gates/ is two levels below the tree).
script_dir:{[f] $[0=count i:where "/"=f; ""; (1+last i)#f]} string .z.f;
system "l ",script_dir,"hdb_sort_lib.q";
load_etl[script_dir,"../../src/etl/"];

rules:sort_rules[];

/ Each partition directory's own table directories, judged against their rules.
{[part]
    {[part;tbl]
        dir:` sv (hsym `$part),tbl;
        if[()~key ` sv dir,`.d; :()];
        why:unsorted_reason[dir;tbl;rules tbl];
        if[count why; -1 "|" sv ("unsorted";part;string tbl;why)]}[part] each key rules} each read0 parts_file;
-1 "DONE";
exit 0
