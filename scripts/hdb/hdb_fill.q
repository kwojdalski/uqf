/ hdb_fill.q - make every HDB partition hold every declared
/ table, so a query that spans them can run at all.
/ .
/ Run as:  q scripts/hdb/hdb_fill.q <hdb root> <generated database.q>
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
/ A DERIVED DEFAULT, FOR ONE COLUMN (#1925). A column the partition lacks is
/ written as the declared type's null EXCEPT `clock` on `executions` and
/ `market_data`, which is attributed from the rows' own columns by the rules
/ in scripts/hdb/hdb_clock_rules.csv: whose clock a stored source_time is
/ depends on which feed wrote the row, and the feed is recoverable from
/ (market, source) for a book and from venue for a fill. Anything the rules
/ do not name is `unknown - never a guess, and never null, because null is
/ what a mapping that forgot to stamp looks like. The core refuses both. The
/ rule file is read only when such a column is actually missing, so a copy of
/ this script without it still repairs every other column.
/ .
/ A COLUMN THAT IS DELIBERATELY NOT DERIVED: crypto_trades.source_time (#1936). It
/ is the VENUE's own time for a fill, which the recorder began sending after the
/ table was first written. A partition older than that has no venue time to give,
/ and the only value to hand is the plant's receipt `time` - exactly what must not
/ be written into a venue-time column, because the markout core would then score a
/ receipt stamp against venue-clock books as if they shared a clock (#1923). So it
/ is filled with the declared null like any other column, by the default and not by
/ a rule, and the executions normalizer reads a null as "no venue stamp" and puts
/ the fill on the `receipt clock (src/etl/streaming/executions.q). The test that
/ pins it is test_fill_hdb_partitions.q's; `clock` above is the only derived column.
/ .
/ TWO LEVELS, THE SAME FAULT. A missing TABLE fails a query naming whichever
/ table sorts first; a missing COLUMN fails it naming that column in the
/ partition that lacks it - "./2026.01.01/book/venue. OS reports: No such
/ file or directory". Same cause (the schema grew after the partition was
/ written), same repair, so both happen here in one pass.
/ .
/ ONE MIGRATION, ONLY WHEN ASKED (#1999). A third argument, `migrate`, folds the three
/ configuration columns a markout row used to repeat (contract_version, rate_timing,
/ report_ccy) into configuration snapshots: per partition, one LEGACY snapshot for each
/ distinct triple found (recorded in the snapshot store, in $UQF_STATUS_DIR, as the
/ markout jobs record theirs), a `config_id` column naming each row's, and then the three
/ columns dropped. In that order, so an interruption leaves the old columns beside a good
/ config_id (the next run only drops them) and never a row without its configuration.
/ Without `migrate` an existing markout partition gets no config_id at all and is left as it
/ was - the bootstrap fill never deletes a column; `uqs data check` reports the gap. A partition older
/ than the columns has none to read: its rows are the contract's first version, with the
/ rate timing and reporting currency unknown.
/ .
/ TWO MORE THINGS ARE MIGRATED, ONLY WHEN ASKED (#2288), for market_data. A markout runs its
/ as-of join against the memory-mapped partitions and never copies them, which needs three things
/ of a partition the older ones lack, and `migrate` brings them:
/ .
/   bid, ask   the flat level-0 top of each book, derived from the stored ladders by the plant's
/              own rule (top_sides: a price someone could deal at, else null), a chunk of rows
/              at a time so the ladders stay mapped. Held back without `migrate`, like config_id:
/              a null there would read as a withdrawn book, and the bootstrap runs on every command
/   the order  rows sorted by sym, source, source_time with p# on sym (the plant's declared
/              `sorting`). A rewrite, so it is done in staging and swapped in whole under the HDB
/              write lock (hdb_sort_lib.q) - stop the stack first, as for any migration
/   rid        each row's position in that order, assigned with the sort (an older partition that
/              is already in order only gets the column)
/ .
/ `uqs data check` (dry) lists the partitions that lack the columns or are not in order
/ (scripts/hdb/hdb_sort_order.q); `uqs data check --apply` asks, then runs this with `migrate`.
/ .
/ ADDITIVE, ALWAYS - but for those migrations. A table directory that holds a `.d` is never emptied and a
/ column that exists is never rewritten, whatever is in it. A table or a
/ column a partition holds and database.q no longer declares is left alone:
/ that is history, and deleting history is not this script's business. A
/ column whose declared TYPE changed is reported by `uqs data check`
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
die:{[msg] -2 "hdb_fill: ",msg; exit 1}

hdb_root:hsym `$first .z.x;
schema_file:hsym `$.z.x 1;
/ the markout configuration migration (#1999) and the market_data one (#2288) run only when asked
migrate:(2<count .z.x) and "migrate"~.z.x 2;

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

/ The sort migration's functions and what it reads of src/etl (the plant's sort rules and row-number
/ columns, the HDB writer's staging and swap). Only a migration needs them.
sorted_tables:(`symbol$())!();
if[migrate;
    system"l ",script_dir,"hdb_sort_lib.q";
    load_etl[script_dir,"../../src/etl/"];
    sorted_tables:sort_rules[]];

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

/ The history-fill rules for `clock` (#1925): one row per (table, market, name)
/ in scripts/hdb/hdb_clock_rules.csv, found from this script's own path. Read
/ only when a clock column has to be filled.
/ @return the rules, columns tbl, market, name, clock
clock_rules:{[]
    f:hsym `$script_dir,"hdb_clock_rules.csv";
    if[()~key f; die "no hdb_clock_rules.csv beside this script - the clock history fill needs it (#1925)"];
    ("SSSS";enlist ",") 0: f}

/ The columns the FX markout tables gained with the markout contract (#1935) whose
/ missing values are DERIVED from the columns the stored rows already hold, not null:
/ the contract's move, quote_ccy, quote_amount, price, ref_t0 and ref_t1, FX's
/ pip_value, and the clock. What a stored row cannot say stays the declared null: rate and
/ report_amount, because no rate was ever declared for a stored row. The wide table already
/ held price, ref_t0 and ref_t1. config_id is derived too, but from a different place
/ (derived_config below): the snapshot store.
fx_derived:`fx_markout`fx_markout_wide!(`clock`quote_ccy`move`pip_value`quote_amount`price`ref_t0`ref_t1;`clock`quote_ccy`move`pip_value`quote_amount)

/ The markout datasets whose rows used to repeat their configuration (#1999), and the three
/ columns that were: the migration's inputs, dropped once config_id names the snapshot.
markout_tables:`fx_markout`fx_markout_wide`crypto_markout
legacy_config_cols:`contract_version`rate_timing`report_ccy

/ Is this column one whose missing values are derived, not null (#1925, #1935)?
/ @param table_name the table
/ @param col the column
/ @return 1b for `clock on executions and market_data (attributed by rule), and for a
/   derived contract column of the FX markout tables
derived_column:{[table_name;col]
    $[(table_name in markout_tables) and col=`config_id; 1b;
      table_name=`market_data; col in `clock`bid`ask`rid;
      table_name=`executions; col=`clock;
      table_name in key fx_derived; col in fx_derived table_name;
      0b]}

/ The columns an EXISTING partition of a table gets only when the migration is asked for, because they are
/ derived from the partition's stored columns and the bootstrap runs on every command: a markout table's
/ config_id (derived from the old configuration columns, which only the migration drops, #1999) and
/ market_data's flat top and row number (#2288).
/ @param table_name the table
/ @return the held-back columns
held_cols:{[table_name]
    $[table_name in markout_tables; enlist `config_id;
      table_name=`market_data; `bid`ask`rid;
      `symbol$()]}

/ One of market_data's flat columns, derived from the columns beside it. A partition that lacks the
/ ladders keeps the declared null: nothing is guessed.
/ @param root the HDB root
/ @param dir a table directory that holds a `.d`
/ @param col bid, ask or rid
/ @return the column, one value per row
derived_flat:{[root;dir;col]
    have:disk_cols dir;
    n:count get ` sv dir,first have;
    if[col=`rid; :til n];
    side:$[col=`bid; `bid_prices`bid_sizes; `ask_prices`ask_sizes];
    if[not all side in have; :declared_default[root;`market_data;col]];
    derived_side[dir;side 0;side 1]}

/ The stored columns the derivation of an FX markout column reads.
/ @param table_name fx_markout or fx_markout_wide
/ @param col the column to derive
/ @return the columns it needs, none for a constant
fx_needs:{[table_name;col]
    pf:$[table_name=`fx_markout; `pips; `total_pips];
    $[col=`clock; `$();
      col=`quote_ccy; enlist `sym;
      col=`move; `pip_factor,pf;
      col=`pip_value; `size`pip_factor;
      col=`quote_amount; `size`pip_factor,pf;
      col=`price; `venue`fill_id`horizon`kind`price_t0;
      `venue`fill_id`horizon`kind`price_t1]}

/ The value of column c on the rows whose kind is knd, looked up for every row of its
/ (venue; fill_id; horizon) - a long table repeats the execution's facts on each component.
/ @param d the stored columns, as a dictionary
/ @param knd the component to read
/ @param c the column to read
/ @return a float per row, null where the (execution, horizon) has no such component
by_component:{[d;knd;c]
    k:flip d`venue`fill_id`horizon;
    w:where (d`kind)=knd;
    (d[c] w) (k w)?k}

/ One FX markout column, derived from the columns beside it. A partition that lacks a
/ column the derivation reads keeps the declared null: nothing is guessed.
/ @param root the HDB root, whose sym file a symbol column enumerates against
/ @param table_name fx_markout or fx_markout_wide
/ @param dir a table directory that holds a `.d`
/ @param col the column to derive
/ @return the column, one value per row, enumerated if symbol
derived_fx:{[root;table_name;dir;col]
    n:count get ` sv dir,first disk_cols dir;
    need:fx_needs[table_name;col];
    if[not all need in disk_cols dir; :declared_default[root;table_name;col]];
    `sym set get ` sv root,`sym;
    d:$[count need; need!{[dir;c] v:get ` sv dir,c; $[type[v] within 20 76h; value v; v]}[dir] each need; ()!()];
    pips_col:$[table_name=`fx_markout; `pips; `total_pips];
    v:$[col=`clock; n#`unknown;
        col=`quote_ccy; [u:distinct d`sym; q:{`$-3#string x} each u; q u?d`sym];
        col=`move; (`float$d pips_col)%`float$d`pip_factor;
        col=`pip_value; (`float$d`size)%`float$d`pip_factor;
        col=`quote_amount; (`float$d`size)*(`float$d pips_col)%`float$d`pip_factor;
        col=`price; by_component[d;`total;`price_t0];
        col=`ref_t0; by_component[d;`execution;`price_t1];
        by_component[d;`total;`price_t1]];
    $[11h=abs type v; (.Q.en[root] flip enlist[`v]!enlist v)`v; v]}

/ Load the snapshot store and the markout configuration's legacy pin, once, from this script's own
/ tree (scripts/gates/ is two levels below src/), and refuse to go on if the status directory the
/ store lives in is not named: a migration that cannot record its snapshots must not drop the
/ columns they replace.
load_snapshot_store:{[]
    if[not ()~key `.qetl.snapshot; :1b];
    f:{[d;n] hsym `$d,"../../src/etl/",n} script_dir;
    needed:f each ("core/status.q";"core/backfill_state.q";"core/config_snapshot.q";"transforms/config_markout.q");
    if[any ()~/:key each needed; die "no src/etl beside this script - the markout configuration migration needs its snapshot store (#1999)"];
    system each "l ",/:1_/:string needed;
    / the status directory is status.q's to name; with neither of its variables set it refuses
    @[.qetl.status.status_dir;::;{[e] die "the markout configuration migration records snapshots in the status directory: set UQF_STATUS_DIR (or TORQDATA) first"}];
    1b}

/ A markout partition's config_id: the legacy snapshot each row's old configuration columns name.
/ .
/ One snapshot per DISTINCT (contract_version, rate_timing, report_ccy) in the partition,
/ recorded in the store (idempotently: the id is the triple's digest, so the same triple in
/ another partition, or on a second run, is the same snapshot). A column the partition lacks
/ reads as the old rows' own answer: contract_version 1, the rate timing and reporting
/ currency unknown.
/ @param root the HDB root, whose sym file a symbol column enumerates against
/ @param table_name a markout table
/ @param dir a table directory that holds a `.d`
/ @return the config_id column, enumerated
derived_config:{[root;table_name;dir]
    load_snapshot_store[];
    n:count get ` sv dir,first disk_cols dir;
    have:disk_cols dir;
    `sym set get ` sv root,`sym;
    read:{[dir;c] v:get ` sv dir,c; $[type[v] within 20 76h; value v; v]}[dir];
    cv:$[`contract_version in have; `long$read `contract_version; n#1];
    rt:$[`rate_timing in have; `symbol$read `rate_timing; n#`];
    rc:$[`report_ccy in have; `symbol$read `report_ccy; n#`];
    trip:flip `cv`rt`rc!(cv;rt;rc);
    u:distinct trip;
    ids:{[table_name;p] .qpipe.transform.config_markout.legacy_pin[table_name;p`cv;p`rt;p`rc]}[table_name] each u;
    v:`symbol$ids u?trip;
    -1 "  ",partition_name[dir],"/",string[table_name],": config_id from ",string[count u]," legacy snapshot(s) - ",
        ", " sv string distinct v;
    (.Q.en[root] flip enlist[`v]!enlist v)`v}

/ Drop one column from one table directory: out of `.d` first, so the table stops listing it
/ before its file goes, then the file. A kill between the two leaves an orphan file and a
/ table that is already right.
/ @param dir a table directory that holds a `.d`
/ @param col the column
drop_col:{[dir;col]
    (` sv dir,`.d) set (disk_cols dir) except col;
    @[hdel;` sv dir,col;{[e] (::)}];}

/ After a markout partition holds a config_id, the configuration columns it replaces go.
/ Only when config_id is there and as long as the table is, so nothing is dropped that the
/ snapshot does not already name.
/ @param table_name a markout table
/ @param dir a table directory that holds a `.d`
/ @return the columns dropped
drop_legacy:{[table_name;dir]
    d:disk_cols dir;
    old:legacy_config_cols inter d;
    if[(not `config_id in d) or 0=count old; :`$()];
    n:count get ` sv dir,first d except `config_id,old;
    if[n<>count get ` sv dir,`config_id; die "config_id is shorter than ",string[table_name]," in ",(1_string dir)," - the legacy columns are kept"];
    drop_col[dir;] each old;
    -1 "  ",partition_name[dir],"/",string[table_name],": dropped ",", " sv string old;
    old}

/ The clock of each (market, name) pair, by the rules: an exact market-and-name
/ rule wins over a `*` rule for that market, and a pair no rule names is
/ `unknown. Executions have no market, so both sides are null for them.
/ @param rules the rules of ONE table
/ @param m the pair's market, or null
/ @param nm the pair's source (market_data) or venue (executions)
/ @return the clock symbol
clock_of:{[rules;m;nm]
    hits:(exec clock from rules where market=m, name=nm),exec clock from rules where market=m, name=`$"*";
    $[count hits; first hits; `unknown]}

/ One partition's clock column, attributed from the columns beside it. A column
/ the partition lacks (a book older than `market`) leaves its rows `unknown.
/ @param root the HDB root, whose sym file the key columns enumerate against
/ @param table_name executions or market_data
/ @param dir a table directory that holds a `.d`
/ @return a symbol vector, one clock per row
derived_clock:{[root;table_name;dir]
    n:count get ` sv dir,first disk_cols dir;
    key_cols:$[table_name=`market_data; `market`source; enlist `venue];
    if[not all key_cols in disk_cols dir; :n#`unknown];
    `sym set get ` sv root,`sym;
    cols_read:{[dir;c] value get ` sv dir,c}[dir] each key_cols;
    pairs:$[table_name=`market_data; flip `market`name!cols_read; flip `market`name!(n#`;first cols_read)];
    rules:select from clock_rules[] where tbl=table_name;
    seen:distinct pairs;
    found:{[rules;p] clock_of[rules;p`market;p`name]}[rules] each seen;
    out:found seen?pairs;
    tally:count each group out;
    -1 "  ",partition_name[dir],"/",string[table_name],": clock from the rules - ",
        ", " sv {string[x]," ",string y}'[key tally;value tally];
    out}

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

/ Write a table, already run through .Q.en against root, as a splayed table at dir. PeachQ's
/ splayed `set` enumerates symbols again against a `sym` file it creates in dir's PARENT, the
/ date folder, where KDB-X refuses one (#2285). Column by column puts them against root's.
/ @param root the HDB root
/ @param dir the table directory to write
/ @param t the table
/ @return the directory
write_splay:{[root;dir;t]
    `sym set get ` sv root,`sym;
    c:cols t;
    {[dir;c;v] (` sv dir,c) set v}[dir]'[c;{$[11h=type x; `sym$x; x]} each t c];
    (` sv dir,`.d) set c;
    dir}

/ Repair one table directory.
/ @return `table if it wrote an empty table, `skipped for a directory that
/   exists with no .d, else the columns it added (empty when none was missing)
repair_dir:{[root;table_name;dir]
    if[()~key ` sv dir,`.d;
        / a directory that exists with no .d is not ours to write into
        if[not ()~key dir; :`skipped];
        write_splay[root;dir;.Q.en[root;0#value table_name]];
        :`table];
    absent:missing_cols[table_name;dir];
    / an existing partition gets the columns derived from its stored ones only when the migration is asked for
    if[not migrate; absent:absent except held_cols table_name];
    {[root;table_name;dir;col]
        v:$[not derived_column[table_name;col]; declared_default[root;table_name;col];
            col=`config_id; derived_config[root;table_name;dir];
            table_name in key fx_derived; derived_fx[root;table_name;dir;col];
            (table_name=`market_data) and col in `bid`ask`rid; derived_flat[root;dir;col];
            (.Q.en[root] flip enlist[`v]!enlist derived_clock[root;table_name;dir])`v];
        add1col[dir;col;v]}[root;table_name;dir] each absent;
    if[migrate and table_name in markout_tables; drop_legacy[table_name;dir]];
    / the declared order of a table that has one, and its row numbers (#2288): a rewrite, in staging
    if[migrate and table_name in key sorted_tables;
        dt:"D"$partition_name dir;
        if[sort_partition[root;dt;table_name;sorted_tables table_name];
            -1 "  ",partition_name[dir],"/",string[table_name],": rewritten in ",(", " sv string key sorted_tables table_name)," order, rows numbered"]];
    absent}

/ What is still wrong, read back from disk rather than taken from what the
/ repair says it did: every declared table must hold a .d that names every
/ declared column, and a file for each.
/ @return one line per problem, empty if the database is whole
problems_in:{[table_name;dir]
    if[()~key ` sv dir,`.d; :enlist "no table at ",1_string dir];
    / not migrating, an existing partition lacks the columns the migration derives, by design
    held:$[not migrate; held_cols table_name; `$()];
    lacks:missing_cols[table_name;dir] except held;
    if[count lacks; :enlist "columns not in .d of ",(1_string dir),": ",", " sv string lacks];
    declared_cols:(cols value table_name) except held;
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
    -2 "hdb_fill: left alone, no .d in: ",", " sv 1_'string dirs where skipped];

/ Check the result.
found:.[{[tb;dr] raze problems_in'[tb;dr]};(tabs;dirs);{[e] die "could not check the result: ",e}];
if[count found;
    -2 "hdb_fill: the database is still not whole:";
    -2 each "  ",/:found;
    exit 1];

-1 "";
-1 $[(written=0) and patched=0;
    "already rectangular - nothing to do";
    "filled ",string[written]," table directory(ies) and ",
      string[patched]," column(s)"];
exit 0
