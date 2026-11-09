/ hdb_sort_lib.q - what a declared sort rule means for a stored partition, and the
/ migration that brings an older one to it (#2288).
/ .
/ Loaded by two scripts and run by neither:
/   hdb_sort_order.q       the DRY report `uqs data check` prints: which partitions
/                          are not in their declared order, with their attribute
/   fill_hdb_partitions.q  the migration (`uqs data check --apply`, after asking)
/ .
/ THE RULE. A table that declares a sort rule (src/etl/plant_tables.q `sorting`) is stored
/ sorted by its sort columns, with the declared attribute on each. market_data is sorted by
/ sym, source, source_time with p# on sym, so an as-of join keyed on those three
/ binary-searches the memory-mapped columns instead of copying them. A table that declares a
/ row-number column (`row_ids`) also holds each row's position in that order. This file reads
/ those two declarations from the plant itself: there is no second list of tables here.
/ .
/ SORTING IS A REWRITE, so it is done like every other rewrite of a live partition
/ (io_hdb.q, #2184): on a copy in the staging area beside the root, swapped in with two
/ renames, holding the root's write lock. A kill leaves the old partition or the new one.
/ Never xasc in place: it rewrites one column file at a time, and a kill part way leaves some
/ columns permuted and the rest not, which no length check detects.
/ .
/ WHAT IT COSTS. The sort key columns of ONE partition are read whole to work out the order,
/ then each column is rewritten in that order, so the memory it needs is a partition's three
/ key columns and the permutation - not its ladders. A partition already in order, with its
/ row numbers right, is not touched.

/ The rows of a ladder column read at a time when the flat top of book is derived.
top_chunk:1000000

/ Load what the migration needs of src/etl: the plant's declarations (sort rules, row-number
/ columns) and the HDB writer's staging and swap. Idempotent.
/ @param etl the src/etl directory, as a string ending in "/"
load_etl:{[etl]
    if[`order_partition in key `.qetl.io; :(::)];
    needed:etl,/:("core/status.q";"core/backfill_state.q";"core/log.q";"core/io_hdb.q";"plant_registry.q";"plant_tables.q");
    if[any ()~/:key each hsym each `$needed; '"hdb_sort_lib: no src/etl at ",etl];
    system each "l ",/:needed;}

/ The declared sort rules: table -> sort column -> attribute (` for none), for every table that
/ declares one.
/ @return a dictionary of table to a column-to-attribute dictionary
sort_rules:{[]
    t:.qetl.plant.sort_rules;
    tabs:distinct exec table from t;
    tabs!{[t;tb] exec column!att from t where table=tb}[t] each tabs}

/ A column of a table directory, enumerations decoded to symbols.
/ @param dir the table directory
/ @param c the column
/ @return the column's values
decoded_col:{[dir;c] v:get ` sv dir,c; $[type[v] within 20 76h; value v; v]}

/ Does a table directory number its rows right: its row-number column, where the table has one,
/ holds each row's position? Read a chunk at a time.
/ @param dir a table directory that holds a `.d`
/ @param tbl the table
/ @return the empty string when it does or has no such column, else why not
row_ids_reason:{[dir;tbl]
    if[not tbl in key .qetl.plant.row_ids; :""];
    col:.qetl.plant.row_ids tbl;
    have:get ` sv dir,`.d;
    if[not col in have; :""];
    ids:get ` sv dir,col;
    n:count ids;
    starts:top_chunk*til ceiling n%top_chunk;
    ok:all {[ids;n;s] i:s+til top_chunk&n-s; all i=ids i}[ids;n] each starts;
    $[ok; ""; string[col]," does not hold each row's position"]}

/ Is the table directory in the order its rule declares, with each column's attribute and its
/ row numbers right? A rule naming a column the partition lacks is not judged.
/ @param dir a table directory that holds a `.d`
/ @param tbl the table
/ @param rule sort column -> attribute, in sort order
/ @return the empty string when it is, else why not
unsorted_reason:{[dir;tbl;rule]
    have:get ` sv dir,`.d;
    if[not all (key rule) in have; :""];
    n:count get ` sv dir,first key rule;
    / nothing to put in order, no attribute worth having: an empty table (the filler writes one into a
    / partition that lacks it) or a single row
    if[n<2; :""];
    kt:flip (key rule)!decoded_col[dir] each key rule;
    / the order is judged on the values: the attribute alone cannot tell sorted from grouped
    if[not (til n)~iasc kt; :"not sorted by ",", " sv string key rule];
    atts:(where not null rule)#rule;
    found:attr each get each ` sv/:dir,/:key atts;
    lacks:key[atts] where not (value atts)=found;
    if[count lacks; :"no ",", " sv {string[y],"# on ",string x}'[lacks;atts lacks]];
    row_ids_reason[dir;tbl]}

/ One row's flat top-of-book side: the first level when it is a price someone could deal at,
/ else null. The rule of .qf.mkt.top_sides / .qf.mkt.level_ok, restated here because this
/ script loads none of the library: a price and its size both positive and finite.
/ @param px a ladder of prices, best first
/ @param sz the ladder of sizes
/ @return a float
top_side:{[px;sz]
    if[0=count px; :0n];
    f:"f"$first px;
    s:"f"$first sz;
    $[(0<f)&(0w>f)&(0<s)&0w>s; f; 0n]}

/ The flat level-0 side of every row of a partition, from its stored ladders, a chunk of
/ rows at a time so the ladders stay mapped.
/ @param dir the table directory
/ @param px_col the price ladder column, e.g. `bid_prices
/ @param sz_col the size ladder column
/ @return a float per row
derived_side:{[dir;px_col;sz_col]
    px:get ` sv dir,px_col;
    sz:get ` sv dir,sz_col;
    n:count px;
    starts:top_chunk*til ceiling n%top_chunk;
    "f"$raze {[px;sz;n;s] i:s+til top_chunk&n-s; top_side'[px i;sz i]}[px;sz;n] each starts}

/ Bring one partition of a table to its declared order, attributes and row numbers, swapped in
/ whole. Needs src/etl loaded (load_etl): the staging area and the swap are io_hdb's own, so a
/ kill is recovered by the same sweep. A partition already as declared is left alone.
/ @param root the HDB root, a file symbol
/ @param dt the partition's date
/ @param table_name the table
/ @param rule sort column -> attribute
/ @return 1b when it rewrote the partition, 0b when it was already as declared
sort_partition:{[root;dt;table_name;rule]
    dir:.Q.par[root;dt;table_name];
    if[()~key ` sv dir,`.d; :0b];
    / enumerated columns are read against the HDB's own sym file
    `sym set get ` sv root,`sym;
    why:unsorted_reason[dir;table_name;rule];
    if[0=count why; :0b];
    resort:why like "not sorted*";
    .qetl.io.locked[root;{[root;dt;table_name;rule;resort]
        base:.qetl.io.stage_copy[root;dt;table_name];
        part:hsym `$base,"/";
        / the order is worked out on the staged copy and every column rewritten in it; a partition that is
        / in order only has its row numbers rewritten. Attributes ride along with the rewrite.
        $[resort; .qetl.io.order_partition[part;rule;table_name]; [.qetl.io.sort_and_attribute[part;rule]; .qetl.io.assign_row_ids[part;table_name]]];
        / a sort that dropped a row would be swapped in as the partition
        want:count get ` sv .Q.par[root;dt;table_name],first key rule;
        got:count get hsym `$base,"/",string first key rule;
        if[want<>got; '"sort of ",string[table_name]," ",string[dt]," changed its row count"];
        .qetl.io.swap[root;dt;table_name]};(root;dt;table_name;rule;resort)];
    1b}
