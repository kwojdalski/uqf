/ hdb_column_types.q - the HDB columns whose type on disk is not the one
/ database.q declares (#870).
/ .
/ Run as:  q scripts/gates/hdb_column_types.q <hdb root> <generated database.q>
/ .
/ fill_hdb_partitions.q fills what is MISSING - additive, idempotent, safe to
/ run on every bootstrap. A column that exists with another type is the one
/ thing it must not touch: "the declaration changed" and "the bytes on disk are
/ wrong" are different claims, and only a person can tell which, so a type
/ change is a migration. This only finds them. A partitioned query over a
/ column of two types fails, or worse returns a column of mixed meaning.
/ .
/ One line per finding - type_change|<partition>|<table>|<column>|<on disk>|<declared>,
/ each type as its .Q.t letter, `enum` for an enumerated symbol column -
/ then DONE. q exits 0 whatever happens while a script runs, so the caller
/ believes DONE, never the exit code.
/ .
/ What counts as the declared type is the column's in the empty table the
/ schema defines. A general list (type 0: nested and string columns) can be
/ stored several ways and is not judged. A symbol column is stored
/ enumerated against the HDB's sym file, so `enum` on disk matches `s`.
hdb_root:hsym `$first .z.x;
schema_file:hsym `$.z.x 1;
if[()~key hdb_root; '"hdb_column_types: no HDB at ",string hdb_root];
if[()~key schema_file; '"hdb_column_types: no schema at ",string schema_file];

/ The tables database.q defines: those that appear when it loads.
before_load:tables[];
system"l ",1_string schema_file;
declared:tables[] except before_load;

/ The partition directories, as fill_hdb_partitions.q reads them.
parts:{[root]
    entries:key root;
    asc entries where entries like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"}

/ A type as its .Q.t letter; `enum` for an enumeration.
letter:{[t] $[t within 20 76h; "enum"; enlist .Q.t abs t]}

/ Does `found`, a type on disk, store `want`, a declared type?
stores:{[found;want] $[want=0h; 1b; want=11h; (found=11h) or found within 20 76h; found=want]}

/ The findings for one table in one partition: one line per column whose
/ type on disk is not its declared type. Columns come from `.d`, as
/ fill_cols reads them.
check_table:{[root;part;t]
    dir:` sv root,part,t;
    dfile:` sv dir,`.d;
    if[()~key dfile; :()];
    want:{abs type x} each flip value t;
    present:(get dfile) inter key want;
    found:{[dir;c] type get ` sv dir,c}[dir] each present;
    bad:present where not stores'[found;want present];
    {[part;t;c;f;w] "|" sv ("type_change";string part;string t;string c;letter f;letter w)}[part;t]'[
        bad;found present?bad;want bad]}

{[root;part] -1 each raze check_table[root;part] each declared}[hdb_root] each parts hdb_root;
-1 "DONE";
exit 0
