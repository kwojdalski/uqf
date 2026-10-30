/ hdb_column_types.q - the HDB columns whose type on disk is not the one
/ database.q declares (#870).
/ .
/ Run as:  q scripts/gates/hdb_column_types.q <partitions file> <generated database.q>
/ .
/ The partitions file names the partitions to read, one directory per line -
/ what scripts/gates/hdb_layout.q found, as stack/hdb_layout.py writes it.
/ This script does not look for partitions itself: q's load finds them, in
/ the segments par.txt names when the database is segmented (#1783), and the
/ check reads exactly those.
/ .
/ fill_hdb_partitions.q fills what is MISSING - additive, idempotent, safe to
/ run on every bootstrap. A column that exists with another type is the one
/ thing it must not touch: "the declaration changed" and "the bytes on disk are
/ wrong" are different claims, and only a person can tell which, so a type
/ change is a migration. This only finds them. A partitioned query over a
/ column of two types fails, or worse returns a column of mixed meaning.
/ .
/ One line per finding - type_change|<partition directory>|<table>|<column>|<on disk>|<declared>,
/ each type as its .Q.t letter, `enum` for an enumerated symbol column -
/ then DONE. q exits 0 whatever happens while a script runs, so the caller
/ believes DONE, never the exit code.
/ .
/ What counts as the declared type is the column's in the empty table the
/ schema defines. A general list (type 0: nested and string columns) can be
/ stored several ways and is not judged. A symbol column is stored
/ enumerated against the HDB's sym file, so `enum` on disk matches `s`.
parts_file:hsym `$first .z.x;
schema_file:hsym `$.z.x 1;
if[()~key parts_file; '"hdb_column_types: no partitions file at ",string parts_file];
if[()~key schema_file; '"hdb_column_types: no schema at ",string schema_file];

/ The tables database.q defines: those that appear when it loads.
before_load:tables[];
system"l ",1_string schema_file;
declared:tables[] except before_load;

/ The partition directories to read, one per line of the partitions file.
parts:read0 parts_file;

/ A type as its .Q.t letter; `enum` for an enumeration.
letter:{[t] $[t within 20 76h; "enum"; enlist .Q.t abs t]}

/ Does `found`, a type on disk, store `want`, a declared type?
stores:{[found;want] $[want=0h; 1b; want=11h; (found=11h) or found within 20 76h; found=want]}

/ The findings for one table in one partition: one line per column whose
/ type on disk is not its declared type. Columns come from `.d`, as
/ fill_cols reads them.
check_table:{[part;t]
    dir:` sv (hsym `$part),t;
    dfile:` sv dir,`.d;
    if[()~key dfile; :()];
    want:{abs type x} each flip value t;
    present:(get dfile) inter key want;
    found:{[dir;c] type get ` sv dir,c}[dir] each present;
    bad:present where not stores'[found;want present];
    {[part;t;c;f;w] "|" sv ("type_change";part;string t;string c;letter f;letter w)}[part;t]'[
        bad;found present?bad;want bad]}

{[part] -1 each raze check_table[part] each declared} each parts;
-1 "DONE";
exit 0
