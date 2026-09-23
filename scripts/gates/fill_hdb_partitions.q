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
/ ADDITIVE, ALWAYS. A table directory that exists is never touched, whatever
/ is in it. A table a partition holds and database.q no longer declares is
/ left alone: that is history, and deleting history is not this script's
/ business. Running it twice changes nothing the second time.

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
fill_one:{[root;part;tbl]
    dir:` sv (.Q.par[root;part;tbl];`);
    if[not ()~key dir; :0b];
    dir set .Q.en[root;0#get tbl];
    1b}

partitions:parts hdb_root;

-1 "hdb:        ", string hdb_root;
-1 "declared:   ", string[count declared], " table(s) in ", string schema_file;
-1 "partitions: ", string[count partitions], " (", (", " sv string partitions), ")";

written:0;
{[root;declared;part]
    made:{[root;part;tbl] fill_one[root;part;tbl]}[root;part] each declared;
    n:sum made;
    if[n>0; -1 "  ",string[part],": wrote ",string[n]," missing table(s)"];
    `written set written+n;
    }[hdb_root;declared] each partitions;

-1 "";
-1 $[written=0;
    "already rectangular - nothing to do";
    "filled ",string[written]," table directory(ies)"];
exit 0
