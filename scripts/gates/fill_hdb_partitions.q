/ fill_hdb_partitions.q - make every HDB partition hold every declared
/ table, so a query that spans them can run at all.
/ .
/ Run as:  q scripts/gates/fill_hdb_partitions.q <hdb root> <generated database.q>
/ .
/ WHY NOT JUST .Q.chk. Because it cannot do the case that matters. .Q.chk
/ fills a missing table by copying its schema from a partition that HAS
/ it, so it works only once some partition is complete. At bootstrap none
/ is: the vendored sample ships two partitions holding `quote` and `trade`,
/ and no partition anywhere holds a uqf table yet. .Q.chk would walk the
/ database and correctly change nothing, leaving it exactly as broken
/ (#348).
/ .
/ So the schema comes from database.q - the same file the tickerplant
/ loads, which is the definition of what the HDB is expected to hold - and
/ each missing table is written empty from it, the way
/ lib/torq/code/processes/wdb.q's `inittable` does for the partition the
/ wdb is writing.
/ .
/ TWO LEVELS, THE SAME FAULT. A missing TABLE fails a query naming whichever
/ table sorts first; a missing COLUMN fails it naming that column in the
/ partition that lacks it - "./2026.01.01/book/venue. OS reports: No such
/ file or directory". Same cause (the schema grew after the partition was
/ written), same repair, so both happen here in one pass.
/ .
/ A missing column is written as the declared type's null, repeated to the
/ partition's own row count, and its name appended to `.d`. Symbol columns
/ are enumerated against the HDB's sym file: a raw symbol vector in a
/ splayed table is unreadable by anything that maps the database, which is
/ the same reason fill_one calls .Q.en.
/ .
/ This is dbmaint.q's `addcol` in miniature - KX's own utility, which this
/ tree carries only as orphaned test data inside the vendored TorQ tree
/ (lib/torq/tests/dataaccess/queryorder/hdb/dbmaint.q, loaded by nothing).
/ Vendoring 150 lines to call one of them, when the one is six lines built
/ from the schema this script already loads, would leave castcol, renamecol
/ and rentable reachable from no live path.
/ .
/ ADDITIVE, ALWAYS. A table directory that exists is never emptied and a
/ column that exists is never rewritten, whatever is in it. A table or a
/ column a partition holds and database.q no longer declares is left alone:
/ that is history, and deleting history is not this script's business. A
/ column whose declared TYPE changed is reported by `uqs hdb-check`
/ and not touched here - "the declaration changed" and "the bytes on disk
/ are wrong" are different claims, and only a person can tell which.
/ Running it twice changes nothing the second time.

hdb_root:hsym `$first .z.x;
schema_file:hsym `$.z.x 1;

if[()~key hdb_root; '"fill_hdb_partitions: no HDB at ",string hdb_root];
if[()~key schema_file; '"fill_hdb_partitions: no schema at ",string schema_file];

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

/ The partition directories, by name. A date-shaped directory only: `sym`
/ is the enumeration domain, not a partition, and par.txt names a
/ segmented database's roots.
/ @return the partitions as DATES, oldest first
parts:{[root]
    entries:key root;
    dated:entries where entries like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]";
    asc "D"$string dated}

/ Write one empty table into one partition, enumerated against the HDB's
/ own sym file - a symbol column written without .Q.en is unreadable by
/ anything that maps the database.
/ @param part the partition as a DATE - .Q.par builds the path from a
/   date, and the directory name is what `key` hands back as a symbol, so
/   the conversion happens once, in `parts`, rather than at each call site
/ @return 1b if it wrote, 0b if the table was already there
fill_one:{[root;part;table_name]
    dir:` sv (.Q.par[root;part;table_name];`);
    if[not ()~key dir; :0b];
    dir set .Q.en[root;0#get table_name];
    1b}

/ Add the declared columns a partition's copy of a table does not have.
/ .
/ `.d` is the authority for what a splayed table holds, not the directory
/ listing: a nested column writes TWO files (`bids` and `bids#`) and appears
/ in `.d` once, so listing the directory would ask for a column named
/ `bids#` that no schema declares.
/ .
/ The row count comes from a column the partition already has, because that
/ is the only thing that knows how long this partition is - the schema
/ carries types, not counts, and a default vector of the wrong length is a
/ corrupt table that still maps.
/ @param part the partition as a DATE
/ @param table_name the table name, whose root-level value carries the declared shape
/ @return the columns written, empty if the partition was already complete
fill_cols:{[root;part;table_name]
    dir:.Q.par[root;part;table_name];
    dfile:` sv dir,`.d;
    / no .d means no table directory - fill_one's job, and it ran first
    if[()~key dfile; :`$()];
    present:get dfile;
    absent:(cols value table_name) except present;
    if[0=count absent; :`$()];
    rows:count get ` sv dir,first present;
    {[root;dir;rows;table_name;c]
        vals:rows#(value table_name)c;
        / 11h either sign: a symbol column must point into root/sym or the
        / database cannot be mapped. .Q.en takes a table, so wrap and unwrap.
        if[11h=abs type vals; vals:(.Q.en[root] ([] c:vals))`c];
        (` sv dir,c) set vals;
        @[dir;`.d;,;c];
        }[root;dir;rows;table_name] each absent;
    absent}

partitions:parts hdb_root;

-1 "hdb:        ", string hdb_root;
-1 "declared:   ", string[count declared], " table(s) in ", string schema_file;
-1 "partitions: ", string[count partitions], " (", (", " sv string partitions), ")";

written:0;
patched:0;
{[root;declared;part]
    made:{[root;part;table_name] fill_one[root;part;table_name]}[root;part] each declared;
    n:sum made;
    if[n>0; -1 "  ",string[part],": wrote ",string[n]," missing table(s)"];
    `written set written+n;
    / Columns AFTER tables, and in the same pass over the partition: a table
    / this run just created is already complete, so fill_cols finds nothing
    / in it and the two steps do not fight.
    grew:raze {[root;part;table_name] fill_cols[root;part;table_name]}[root;part] each declared;
    if[count grew;
        -1 "  ",string[part],": added ",string[count grew]," column(s) - ",
           ", " sv string grew];
    `patched set patched+count grew;
    }[hdb_root;declared] each partitions;

-1 "";
-1 $[(written=0) and patched=0;
    "already rectangular - nothing to do";
    "filled ",string[written]," table directory(ies) and ",
      string[patched]," column(s)"];
exit 0
