/ mapped_books.q - top-of-book reads against memory-mapped market_data (.qetl.mapped, #2288).
/ .
/ THE RULE. Market data can run to tens of terabytes. A markout run must never copy
/ it into a process heap: the as-of join runs against the MEMORY-MAPPED HDB partition
/ and touches only the pages it needs. Executions are small, so they are the side that
/ lives in memory. What a markout's memory grows with is the number of executions,
/ never the size of the market.
/ .
/ WHAT KEEPS A COLUMN MAPPED. `select cols from market_data where date=d` - the date
/ and nothing else in the where clause - hands back the columns as memory-mapped
/ vectors. ANY other where clause (a sym, a time range) materialises a copy of every
/ column it names. So nothing here, and nothing a markout calls, selects market_data
/ with a where beyond the date (scripts/gates/policy/markout_mapped.py refuses
/ one), and none touches a ladder: bid_prices and the other depth columns are never
/ read by a markout. The level-0 top is stored FLAT (bid, ask), beside a row number
/ (rid) that lets a consumer that does want a depth read only the matched rows' pages.
/ .
/ THE LAYOUT IT RELIES ON. A table's layout is its DECLARED SORT (`.qetl.plant.sorting`, src/etl/plant_tables.q): the
/ columns it is sorted by, in order, the first with p#. Nothing here names those columns. The rule read from the
/ declaration: every sort column but the last is an EQUALITY KEY - the first parted, so a (key; key; ...) is one contiguous
/ run of rows, found by galloping - and the LAST sort column is the AS-OF column, ascending inside each run, which the
/ search bisects. market_data is sorted by sym, source, source_time with p# on sym, so its runs are (sym; source) and its
/ as-of column is source_time; a table declared with another order (sym, venue, time) is searched by the same code. The
/ functions below take the order as their first argument: a table name (read from the declaration where it is loaded) or the
/ order itself (what a process that maps the partitions but never loaded the plant - the HDB - is handed). This module never
/ sorts or copies a partition: the storage layer's job is to write it in that order, and `require_arranged` refuses a
/ table that is not.
/ .
/ THE TOP-OF-BOOK TABLE (the markout core's input): sym, source, source_time, bid, ask,
/ clock. A (sym, source) is on one clock - the clock is a property of the feed that
/ writes the source - so the clock of the matched book is the clock of the series.
/ .
/ WHERE THINGS RUN. `tob_at` is run by the process that MAPS the partitions (the HDB, over
/ IPC, or this process when it loaded one): it returns one row per distinct book the
/ probes matched - memory proportional to the probes, whatever the market's size. It
/ calls only this namespace, which the HDB process loads (VENDORED_LOAD_OVERLAY).

\d .

/ The columns of a top-of-book table, and of its input contract.
.qetl.mapped.columns:`sym`source`source_time`bid`ask`clock

/ The order a table is arranged in, from its declared sort. An atom names a table, read from `.qetl.plant.sorting` (the process
/ must have loaded the plant); a list is the order itself, which a process without the plant (the HDB) is handed.
/ @param x a table name, or the sort columns in order
/ @return the sort columns in order: at least two - an equality key and the as-of column - and no repeats
/ @throws error when a table declares no sort, or the order is not two or more distinct columns
/ @eg .qetl.mapped.order_of `sym`source`source_time  ->  `sym`source`source_time
.qetl.mapped.order_of:{[x]
    o:$[-11h=type x;
        $[`sort_rules in key `.qetl.plant; exec column from .qetl.plant.sort_rules where table=x; `symbol$()];
        x];
    if[not (11h=type o) and (2<=count o) and (count o)=count distinct o;
        '"mapped: ",$[-11h=type x; string[x]," declares no sort order (.qetl.plant.sorting): an equality key and the as-of column are the least"; "an order is two or more distinct columns: the equality keys, then the as-of column"]];
    o}

/ A table up to this many rows has its order checked in full; a larger one (a mapped
/ partition) is checked by its attribute and by sampled neighbours, because reading every
/ row to prove an order is the scan this module exists to avoid.
.qetl.mapped.full_check_rows:1000000

/ How many neighbouring pairs are sampled in a table past full_check_rows.
.qetl.mapped.sample_pairs:2048

/ A column value as plain symbols/values: an enumeration (a mapped partition's sym) is decoded by
/ `value`; anything else is returned as it is. `value` of a plain SYMBOL would evaluate a variable,
/ which is why this checks the type first.
/ @param x a vector or an atom
/ @return x with its enumeration, if any, resolved
/ @eg .qetl.mapped.plain `a`b  ->  `a`b
.qetl.mapped.plain:{[x] $[(abs type x) within 20 76h; value x; x]}

/ Why a table is not in the order the search needs, or "" when it is.
/ .
/ Checks the first sort column carries p#, and that the rows are ascending by the order's columns: all of them for a small table,
/ and a sample of neighbouring pairs for a mapped one (past full_check_rows). The attribute is O(1) either way.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob the table (it needs the order's columns)
/ @return "" when arranged, else the reason
/ @eg .qetl.mapped.arranged[`sym`source`source_time;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())]  ->  ""
.qetl.mapped.arranged:{[order;tob]
    cs:.qetl.mapped.order_of order;
    if[not all cs in cols tob; :"lacks ",", " sv string cs except cols tob];
    n:count tob;
    if[n<2; :""];
    if[not `p=attr tob first cs; :(string first cs)," carries no p# attribute"];
    ix:$[n<=.qetl.mapped.full_check_rows; til n-1;
        asc .qetl.mapped.sample_pairs?n-1];
    / both rows of each pair, read by index: a mapped column pays only those pages
    pairs:{[tob;ix;c] ((tob c) ix; (tob c) ix+1)}[tob;ix] each cs;
    before:pairs[;0];
    after:pairs[;1];
    lt:after<'before;
    gt:after>'before;
    undecided:count[ix]#1b;
    bad:count[ix]#0b;
    i:0;
    while[i<count cs;
        bad:bad | undecided & lt i;
        undecided:undecided & not (lt i) | gt i;
        i+:1];
    $[any bad; "not ascending by ",", " sv string cs; ""]}

/ Refuse a table that is not in the order the search needs. The core calls this on its input instead of sorting it: sorting
/ is the storage layer's job, and a sort here would copy a mapped partition into the heap.
/ @param who the caller, for the message
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob the table
/ @return tob, unchanged
/ @throws error naming the caller and what is wrong
/ @eg .qetl.mapped.require_arranged[`eg;`sym`source`source_time;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())]  ->  0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())
.qetl.mapped.require_arranged:{[who;order;tob]
    why:.qetl.mapped.arranged[order;tob];
    if[count why; '(string who),": input is not arranged for an as-of join (",why,") - sort it by ",(", " sv string .qetl.mapped.order_of order)," and put p# on ",string[first .qetl.mapped.order_of order]," where it is written; the core never sorts or copies it"];
    tob}

/ Sort a SMALL in-memory table into its declared order and put p# on the first sort column: for the live buffer and for
/ fixtures, whose rows arrive in time order. Never for a mapped partition, which is written in order and must not be copied.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob a table
/ @return the same rows, sorted by the order, with p# on its first column
/ @eg attr (.qetl.mapped.arrange[`sym`source`source_time;([] sym:`b`a; source:`x`x; source_time:2#2026.09.17D10:00:00)])`sym  ->  `p
.qetl.mapped.arrange:{[order;tob]
    o:.qetl.mapped.order_of order;
    if[0=count tob; :@[tob;first o;`p#]];
    @[o xasc tob;first o;`p#]}

/ The index of the first element of s's sorted run in a sorted column, and one past its last: the
/ symbol-keyed binary searches below read O(log n) elements of a (possibly mapped) column.
/ @param col a column ascending within [lo;hi)
/ @param v the value
/ @param lo the first index to consider
/ @param hi one past the last
/ @return the first index in [lo;hi) whose element is greater than v
/ @private
.qetl.mapped.upper:{[col;v;lo;hi]
    while[lo<hi;
        mid:(lo+hi) div 2;
        $[v<.qetl.mapped.plain col mid; hi:mid; lo:mid+1]];
    lo}

/ The rows [lo;hi) of every run below one prefix of equality keys: the next key's runs inside it, found by galloping, and
/ so on down to the last equality key.
/ @param tob an arranged table
/ @param rest the equality keys still to split by
/ @param prefix the key values fixed so far
/ @param lo the first row of the prefix's run
/ @param hi one past its last
/ @return a list of rows (key values..., lo, hi)
/ @private
.qetl.mapped.split:{[tob;rest;prefix;lo;hi]
    if[0=count rest; :enlist prefix,(lo;hi)];
    c:first rest;
    out:();
    pos:lo;
    while[pos<hi;
        v:.qetl.mapped.plain (tob c) pos;
        end:.qetl.mapped.upper[tob c;v;pos;hi];
        out,:.qetl.mapped.split[tob;1_rest;prefix,enlist v;pos;end];
        pos:end];
    out}

/ The runs of a sorted, p#-attributed table: every combination of the order's equality keys (all its columns but the last) is one
/ contiguous run of rows, found by galloping - the first key's run from the p# attribute, then each next key's by binary search
/ inside it. Reads O(runs * log rows) elements, never a column.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob a table, arranged
/ @param firsts the values of the FIRST key to look for, or ` for every one
/ @return table of the equality keys, lo, hi: the rows [lo;hi) of each run
/ @eg .qetl.mapped.runs[`sym`source`source_time;.qetl.mapped.arrange[`sym`source`source_time;([] sym:`a`a`b; source:`x`y`x; source_time:3#2026.09.17D10:00:00)];`]`hi  ->  1 2 3
.qetl.mapped.runs:{[order;tob;firsts]
    o:.qetl.mapped.order_of order;
    ks:-1_o;
    empty:flip (ks,`lo`hi)!({[tob;k] 0#.qetl.mapped.plain tob k}[tob] each ks),(`long$();`long$());
    if[0=count tob; :empty];
    k1:first ks;
    all_firsts:.qetl.mapped.plain distinct tob k1;
    / a first key's run: from where it first appears to where the next first appears
    starts:(tob k1)?all_firsts;
    ends:(1_starts),count tob;
    keep:$[(` ~ firsts) or (::) ~ firsts; til count all_firsts; where all_firsts in firsts];
    rows:raze {[tob;rest;v;lo;hi] .qetl.mapped.split[tob;rest;enlist v;lo;hi]}[tob;1_ks]'[all_firsts keep;starts keep;ends keep];
    if[0=count rows; :empty];
    flip (ks,`lo`hi)!{[rows;i] rows[;i]}[rows] each til 2+count ks}

/ The equality keys a table has, for the first-key values given: each key combination once.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob a table, arranged
/ @param firsts the values of the first key to look for
/ @return table of the equality keys: one row per run
/ @eg .qetl.mapped.keys_for[`sym`source`source_time;.qetl.mapped.arrange[`sym`source`source_time;([] sym:`a`a`b; source:`x`y`x; source_time:3#2026.09.17D10:00:00)];enlist `a]`source  ->  `x`y
.qetl.mapped.keys_for:{[order;tob;firsts]
    (-1_.qetl.mapped.order_of order)#.qetl.mapped.runs[order;tob;distinct firsts]}

/ The first index in [lo;hi) whose element of an ascending column is after a value, for every probe at once: the bisections run
/ in lockstep, one gather per round.
/ @param col a column ascending within each [lo;hi)
/ @param lo the first index of each probe's run
/ @param hi one past its last
/ @param t each probe's value
/ @return an index per probe: hi where nothing is after
/ @private
.qetl.mapped.after:{[col;lo;hi;t]
    low:lo;
    h:hi;
    while[any low<h;
        act:where low<h;
        mid:((low act)+h act) div 2;
        go:(col mid)<=t act;
        low[act]:?[go;mid+1;low act];
        h[act]:?[go;h act;mid]];
    low}

/ Does each value meet a key condition? An atom (equal to it), or a list (one of them).
/ @param v a vector of values
/ @param s the condition
/ @return a boolean per value
/ @private
.qetl.mapped.test:{[v;s] $[0>type s; v=s; v in s]}

/ The POINTER of a condition: for each row of a table sorted by its declared order, the position of the latest row of the SAME RUN
/ (the same equality keys) at or before it that passes the condition, null when none does. Two vectorised passes over the rows, no
/ loop: a running maximum of the passing positions, and a running maximum of where each run starts to cut it off.
/ @param keycols the equality-key columns of the rows, in order, as plain vectors (a list of vectors)
/ @param ok a boolean per row: does it pass
/ @return a long per row
/ @eg .qetl.mapped.pointer[enlist `a`a`a`b`b;11010b]  ->  0 1 1 3 3
.qetl.mapped.pointer:{[keycols;ok]
    n:count ok;
    if[0=n; :`long$()];
    first_of_run:n#0b;
    first_of_run[0]:1b;
    i:0;
    while[i<count keycols;
        c:keycols i;
        first_of_run:first_of_run | 0b,(1_c)<>-1_c;
        i+:1];
    begins:maxs ?[first_of_run;til n;0];
    latest:maxs ?[ok;til n;-1];
    ?[latest>=begins;latest;0N]}

/ Check a variant's match against what a table can answer. A match is a dictionary column -> condition, of two kinds only:
/ an EQUALITY KEY of the declared order (a value, or a list of them), which picks the runs searched and reads nothing; and a
/ DECLARED CONDITION of the table (.qetl.plant.condition) with the value 1b, answered by the condition's pointer column. Anything
/ else - another stored column, a comparison, the as-of column - is refused, by name: a condition that is not declared has
/ no pointer, and answering it would be a scan of the window per probe. At most one condition per match: the pointers are
/ one per condition, and the latest row passing two of them is not derivable from either.
/ @param who the caller, for the message
/ @param order the table's declared sort (a table name, or the columns)
/ @param types dictionary column -> the absolute type of its values
/ @param conditions the names of the table's declared conditions
/ @param match column -> value, or an empty dictionary
/ @return the match, with its columns sorted
/ @throws error naming the column or condition: undeclared, not an equality key, an operand of the wrong type, or a second condition
/ @eg .qetl.mapped.require_match[`eg;`sym`source`source_time;`sym`source`source_time!11 11 12h;enlist `two_sided;enlist[`two_sided]!enlist 1b]  ->  enlist[`two_sided]!enlist 1b
.qetl.mapped.require_match:{[who;order;types;conditions;match]
    if[0=count match; :(`symbol$())!()];
    o:.qetl.mapped.order_of order;
    if[not (99h=type match) and 11h=abs type key match; '(string who),": match must be a dictionary of column to condition"];
    ks:-1_o;
    cs:(key match) inter conditions;
    if[1<count cs; '(string who),": match names the conditions ",(", " sv string cs)," - one condition per match: declare their conjunction as a condition of its own (.qetl.plant.condition)"];
    other:(key match) except conditions;
    if[count bad:other where not other in ks;
        '(string who),": match on ",(", " sv string bad),", which is neither a declared condition (",$[count conditions; ", " sv string conditions; "the table declares none"],") nor an equality key (",(", " sv string ks),") - declare it with .qetl.plant.condition so the writer stores its pointer"];
    if[count cs; if[not 1b~match first cs; '(string who),": match on condition ",string[first cs]," must be 1b: the latest book that passes it"]];
    if[count bad:other where not (abs types other)=abs type each match other; '(string who),": match on ",(", " sv string bad)," must hold a value of the key's own type"];
    (asc key match)#match}

/ The as-of join of probes against ONE table in the order a declared sort gives, mapped or not: for each probe (its equality keys;
/ the as-of value), the latest row of that run at or before it - no where clause, no copy of a column - and, with a match, the
/ latest that also meets it.
/ .
/ THE ORDER is the table's declared sort (.qetl.plant.sorting): the equality keys are every column but the last, and the last is
/ the as-of column. For market_data the runs are (sym; source) and the as-of column is source_time; the probes carry the same
/ column names. The matched row's own as-of value comes back as `quoted`, as aj's does not (aj returns the probe's).
/ .
/ WHY NOT aj. aj is this join, and keyed on three columns against a table sorted and parted as ours it returns the same
/ books - but it first groups the right table by its equality keys inside each parted first key, and does that by copying the
/ first key's columns into the heap for the call: at 100 million rows a day the heap's high-water mark jumped from 64MB to 192MB
/ around one aj of 200 probes (tests/q/run_mapped_heap.q), and it grows with the first key's rows - the market. A two-key aj
/ (one key, time) does not copy, but is wrong for several runs. So the search is done here, where it can be held to touching
/ only what it needs: the runs are found by galloping (`runs`), and every probe's binary search over its run's as-of column
/ runs in lockstep with all the others - about log2(longest run) rounds of one gather each, reading O(log n) elements per probe
/ and nothing else. tests/q/test_mapped_books.q holds it equal to aj over the whole table.
/ .
/ THE MATCH is a dictionary column -> value (see require_match). An equality key picks the runs searched. A DECLARED CONDITION
/ (value 1b) is answered by its pointer column `<name>_rid`, which the partition writer stored: the row the bisection found has
/ the position of the latest row of its run at or before it that passes, so the condition costs ONE indexed read of the pointer
/ and ONE of the as-of column at the pointer, for the staleness bound (the probe's `oldest`: no row older may be taken) - however
/ long the stretch of failing rows. There is no scan and no loop over the window anywhere.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob a table (arranged); rid and w (a promoted variant's stored weight, variant_run.q), when it has them, come back too
/ @param probes table of the order's equality keys and its as-of column - and `oldest`, with a condition in the match
/ @param extra further flat columns to bring back for each matched row, beside bid, ask, clock, rid and w
/ @param match column -> value, or an empty dictionary
/ @return the probes with quoted, bid, ask, clock (rid, w, and extra) of the latest row at or before each that meets the match; null where none
/ @throws error naming a column the table lacks (a condition's pointer included), or a probe set without oldest
/ @eg .qetl.mapped.asof_where[`sym`source`source_time;.qetl.mapped.arrange[`sym`source`source_time;([] sym:2#`a; source:2#`x; source_time:2026.09.17D10:00:00 2026.09.17D10:00:03; bid:1 0n; ask:2 3f; clock:2#`venue; ok_rid:0 0)];([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:05; oldest:enlist 2026.09.17D10:00:00);`symbol$();enlist[`ok]!enlist 1b]`bid  ->  enlist 1f
.qetl.mapped.asof_where:{[order;tob;probes;extra;match]
    o:.qetl.mapped.order_of order;
    ks:-1_o;
    ac:last o;
    n:count probes;
    / the match by what answers it: an equality key picks the runs, a condition's pointer column the book
    kmatch:(key match) where (key match) in ks;
    cond:(key match) except ks;
    pcs:{`$string[x],"_rid"} each cond;
    if[count miss:(extra,pcs) except cols tob; '"mapped: the table lacks ",", " sv string miss];
    carry:distinct (`bid`ask`clock),((`rid`w) inter cols tob),extra;
    none:probes,'flip (`quoted,carry)!enlist[n#0Np],{[tob;n;c] n#first 0#tob c}[tob;n] each carry;
    if[(0=n) or 0=count tob; :none];
    if[count cond; if[not `oldest in cols probes; '"mapped: a match on ",(", " sv string cond)," needs the probes' oldest: how far back a row may be"]];
    r:.qetl.mapped.runs[o;tob;distinct probes first ks];
    if[count kmatch; r:r where {[r;m;k] ok:(count r)#1b; i:0; while[i<count k; ok:ok & .qetl.mapped.test[r k i;m k i]; i+:1]; ok}[r;match;kmatch]];
    / each probe's run: [lo;hi) of its keys, empty when the table has none
    run:(ks xkey r) ks#probes;
    lo:0^run`lo;
    hi:0^run`hi;
    t:probes ac;
    col:tob ac;
    / lockstep bisection: the first index in [lo;hi) whose time is after the probe's
    idx:(.qetl.mapped.after[col;lo;hi;t])-1;
    / a null time sorts first: a run whose latest time at or before the probe is null has no book
    ok:(idx>=lo) & hi>lo;
    at:where ok;
    ok[at]:not null col idx at;
    if[count cond;
        / the row found, then the latest row of its run that passes: its pointer, and that row's own time against the oldest allowed
        at:where ok;
        ptr:n#0N;
        ptr[at]:(tob first pcs) idx at;
        ok:ok & not null ptr;
        at:where ok;
        back:col ptr at;
        fine:(back>=(probes`oldest) at) & ptr[at]>=lo at;
        ok[at]:fine;
        at:where ok;
        idx:@[idx;at;:;ptr at]];
    at:where ok;
    plain:{[x] $[(abs type x) within 20 76h; value x; x]};
    got:{[tob;n;at;idx;c] @[n#first 0#tob c;at;:;(tob c) idx at]}[tob;n;at;idx];
    quoted:@[n#0Np;at;:;col idx at];
    probes,'flip (`quoted,carry)!enlist[quoted],{[plain;got;c] plain got c}[plain;got] each carry}

/ The as-of join of probes against ONE table in the order a declared sort gives, with no condition and nothing extra: for each probe,
/ the latest row of its run at or before it. See asof_where.
/ @param order the table's declared sort (a table name, or the columns)
/ @param tob a table (arranged); rid and w, when it has them, come back too
/ @param probes table of the order's equality keys and its as-of column
/ @return the probes with quoted, bid, ask, clock (and rid) of the latest row at or before each; null where none
/ @eg .qetl.mapped.asof[`sym`source`source_time;.qetl.mapped.arrange[`sym`source`source_time;([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1f; ask:enlist 2f; clock:enlist `venue)];([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:05)]`bid  ->  enlist 1f
.qetl.mapped.asof:{[order;tob;probes]
    .qetl.mapped.asof_where[order;tob;probes;`symbol$();(`symbol$())!()]}

/ What tob_at, last_tops and newest call the three columns of a market's order while they work: the instrument, the venue and the
/ as-of column. Their declared names (the order the caller passes) are what the books come back under.
.qetl.mapped.roles:`sym`source`source_time

/ Private: the books a read returns, under the declared order's own column names (the first three columns are the roles').
/ @private
.qetl.mapped.declared:{[o;b] (o,3_cols b) xcol b}

/ The books a set of probes is priced against, read from the partitions of the global `market_data`
/ one date at a time, each by a DATE-ONLY select so every column stays memory-mapped.
/ .
/ Run in the process that maps market_data (the HDB, or this one). For each probe (sym; time) and each
/ source the sym has, the latest book at or before the time - from the partitions within `skew` days of
/ the time's date (the partition is the plant's receipt date, the time the source's stamp), else from
/ the earlier partitions back to `read_from`, one date-only read each, never a scan of a day. Returns the
/ DISTINCT books matched, so the memory it needs is the probes' and the answer's, not the market's.
/ .
/ The search takes market_data's declared sort as `order` (the caller reads it from .qetl.plant.sorting; the HDB never loaded the
/ plant): its first column is the instrument, its second the venue the probes are expanded over, its last the as-of column.
/ .
/ arg is a dictionary:
/   order      market_data's declared sort, three columns
/   probes     table sym, time - the instants to price (any order, duplicates welcome)
/   read_from  the oldest source_time a book may have
/   skew       how many days either side of a time's date its books may be stored
/   venues     `all, or the sources that count
/   match      optional: an equality key's value, or a declared condition -> 1b (asof_where); with it,
/   limit      how far back a book may be from its probe: the staleness bound a condition's pointer is held to
/ @param arg the dictionary above
/ @return a top-of-book table: sym, source, source_time, bid, ask, clock, rid, date - arranged; named as the order names its columns
/ @throws error when the partitions lack the flat columns (run `uqs data check --apply`), or the order is not three columns
/ @eg count .qetl.mapped.tob_at[`order`probes`read_from`skew`venues!(`sym`source`source_time;0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;`all)]  ->  0
.qetl.mapped.tob_at:{[arg]
    o:.qetl.mapped.order_of arg`order;
    if[3<>count o; '"mapped: tob_at reads books by an instrument, a venue and an as-of column, so the order must be three columns, not ",string count o];
    co:.qetl.mapped.roles;
    match:$[`match in key arg; arg`match; (`symbol$())!()];
    / the match speaks in the table's own column names; in here the three roles have theirs, and a condition is its pointer column
    rn:o!co;
    mk:`symbol$ {[rn;k] $[k in key rn; rn k; k]}[rn] each key match;
    cmatch:mk!value match;
    pcs:{`$string[x],"_rid"} each (key match) except o;
    sel:(co,`quoted,`bid`ask`clock`rid,pcs)!(o,(last o),`bid`ask`clock`rid,pcs);
    p:distinct arg`probes;
    empty:flip (co,`bid`ask`clock`rid`date)!(`symbol$();`symbol$();`timestamp$();`float$();`float$();`symbol$();`long$();`date$());
    if[0=count p; :.qetl.mapped.declared[o;.qetl.mapped.arrange[co;empty]]];
    skew:arg`skew;
    lo:(`date$arg`read_from)-skew;
    hi:(`date$max p`time)+skew;
    / `market_data, quoted: the coverage tool re-evaluates a function in its own namespace, where a bare name is not the global
    have:asc exec distinct date from select date from `market_data where date within (lo;hi);
    if[0=count have; :.qetl.mapped.declared[o;.qetl.mapped.arrange[co;empty]]];
    / a partition older than a column the search reads fails here, naming the fix, and not as a bare `bid in a select
    part:{[sel;x] .[{[sel;x] ?[`market_data;enlist (=;`date;x);0b;sel]};(sel;x);{[x;e] '"mapped: the partition of ",string[x]," lacks the column ",e,", which the search reads - run `uqs data check --apply`"}[x]]}[sel];
    pairs:distinct raze {[part;co;syms;d] .qetl.mapped.keys_for[co;part d;syms]}[part;co;distinct p`sym] each have;
    if[not `all~arg`venues; pairs:select from pairs where source in arg`venues];
    sp:exec source by sym from pairs;
    f:update pid:i from ungroup update source:sp sym from update source_time:time from p;
    if[0=count f; :.qetl.mapped.declared[o;.qetl.mapped.arrange[co;empty]]];
    / with a condition the search is bounded by the staleness: no row older than limit may be taken in place of a newer one that fails it
    if[count cmatch; f:update oldest:?[(source_time-arg`limit)>source_time; -0Wp; source_time-arg`limit] from f];
    dt:`date$f`time;
    one:{[part;co;cmatch;f;d;rows] j:.qetl.mapped.asof_where[co;part d;f rows;`symbol$();cmatch]; update date:d from select pid, quoted, bid, ask, clock, rid from j where not null quoted}[part;co;cmatch;f];
    near:raze {[one;dt;skew;d] one[d;where (abs dt-d)<=skew]}[one;dt;skew] each have;
    got:select last quoted, last bid, last ask, last clock, last rid, last date by pid from `pid`quoted`date xasc near;
    left:(exec pid from f) except exec pid from got;
    back:desc have;
    while[count[left] and count back;
        d:first back;
        back:1_back;
        rows:left where ((dt left)-d)>skew;
        if[count rows;
            m:one[d;rows];
            got,:select last quoted, last bid, last ask, last clock, last rid, last date by pid from m;
            left:left except exec pid from m]];
    books:select distinct sym, source, source_time:quoted, bid, ask, clock, rid, date from (f lj got) where not null quoted, quoted>=arg`read_from;
    .qetl.mapped.declared[o;.qetl.mapped.arrange[co;books]]}

/ The latest top of book of every (sym; source) strictly before an instant, with its market: what a job that
/ restarts needs to know of the market as it stood at the close of the previous day, without reading the market.
/ .
/ Run in the process that maps market_data, like tob_at. The partitions are walked newest first from the one
/ holding `before`, each by a DATE-ONLY select (every column stays memory-mapped), the (sym; source) runs found by
/ galloping and priced by `asof` at the instant: a few elements per run, never a scan of a day. A (sym; source)
/ already found in a newer partition is not looked for in an older one, so the walk back over a weekend or a
/ holiday reads only what is still missing, down to `read_from`.
/ .
/ The latest BOOK is returned, whether or not both its sides are quoted: the caller decides what a one-sided
/ book is worth (a mid needs both).
/ @param arg a dictionary: order (market_data's declared sort, three columns), before (the exclusive instant), read_from (the oldest source_time a book may have)
/ @return table sym, source, market, source_time, bid, ask, clock: one row per (sym; source) that has a book in range - under the order's own names for the first, second and last
/ @eg count .qetl.mapped.last_tops[`order`before`read_from!(`sym`source`source_time;2026.09.17D00:00:00;2026.09.10D00:00:00)]
.qetl.mapped.last_tops:{[arg]
    o:.qetl.mapped.order_of arg`order;
    if[3<>count o; '"mapped: last_tops reads books by an instrument, a venue and an as-of column, so the order must be three columns, not ",string count o];
    co:.qetl.mapped.roles;
    before:arg`before;
    empty:0#([] sym:`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());
    sel:(co,`market`bid`ask`clock)!(o,`market`bid`ask`clock);
    / `market_data, quoted: as in tob_at, a bare name is not the global under the coverage tool
    have:desc exec distinct date from select date from `market_data where date within (`date$arg`read_from;`date$before);
    got:empty;
    while[count have;
        d:first have;
        have:1_have;
        part:?[`market_data;enlist (=;`date;d);0b;sel];
        r:.qetl.mapped.runs[co;part;`];
        if[count r;
            r:update market:`symbol$.qetl.mapped.plain (part`market) lo from r;
            / only the (sym; source) pairs a newer partition has not answered
            r:r where not ((r`sym),'(r`source)) in (got`sym),'(got`source);
            if[count r;
                j:.qetl.mapped.asof[co;part;select sym, source, source_time:before-1 from r];
                j:select sym, source, source_time:quoted, bid, ask, clock from j where not null quoted, quoted>=arg`read_from;
                got,:select sym, source, market, source_time, bid, ask, clock from (j lj `sym`source xkey select sym, source, market from r)]]];
    (o 0;o 1;`market;o 2;`bid;`ask;`clock) xcol got}

/ The newest source_time among the books of one market in the newest partition of the global `market_data` that holds
/ any, read from the END of each (sym; source) run - the run is ascending, so its last row is its newest - and the
/ market from the run's first. No where clause, no scan of a column: a few elements per run.
/ .
/ Partitions are walked newest first and the walk stops at the first holding the market: the newest day can hold only
/ another market's books (a Saturday holds crypto alone), and reading it alone answered -0Wp for FX (#2571).
/ .
/ Run in the process that maps market_data, like tob_at.
/ @param arg a dictionary: order (market_data's declared sort, three columns), market (a symbol, or ` for every market)
/ @return the newest source_time, or -0Wp when no partition holds the market
/ @eg .qetl.mapped.newest[`order`market!(`sym`source`source_time;`crypto)]
.qetl.mapped.newest:{[arg]
    o:.qetl.mapped.order_of arg`order;
    if[3<>count o; '"mapped: newest reads books by an instrument, a venue and an as-of column, so the order must be three columns, not ",string count o];
    ds:desc exec date from select distinct date from `market_data;
    n:-0Wp;
    while[(n=-0Wp) and count ds; n:.qetl.mapped.newest_on[o;arg`market;first ds]; ds:1_ds];
    n}

/ Private: newest's read of one partition, -0Wp when it holds no book of the market.
/ @private
.qetl.mapped.newest_on:{[o;mkt;d]
    co:.qetl.mapped.roles;
    sel:$[null mkt; co!o; (co,`market)!o,`market];
    t:?[`market_data;enlist (=;`date;d);0b;sel];
    r:.qetl.mapped.runs[co;t;`];
    if[0=count r; :-0Wp];
    if[not null mkt; r:r where (.qetl.mapped.plain (t`market) r`lo)=mkt];
    $[count r; max .qetl.mapped.plain (t`source_time) (r`hi)-1; -0Wp]}

/ The instants a markout asks the market about, one per (sym; time): for each execution, each of its legs at the
/ execution's own instant and at every horizon from it. The ONLY thing a twin's source needs to know about
/ scoring to read the books that answer it; the core asks for exactly these (sym; instant) pairs and no others.
/ @param legs the market_data syms each execution is priced from, a symbol list per execution
/ @param t0 each execution's instant
/ @param horizons the spec's horizons, signed timespans
/ @return table sym, time, distinct
/ @eg .qetl.mapped.probes[enlist `EURUSD`USDJPY;enlist 2026.09.17D10:00:00;enlist 0D00:00:10]`time  ->  2026.09.17D10:00:00 2026.09.17D10:00:10 2026.09.17D10:00:00 2026.09.17D10:00:10
.qetl.mapped.probes:{[legs;t0;horizons]
    per:ungroup ([] sym:legs; t0:t0);
    if[0=count per; :([] sym:`symbol$(); time:`timestamp$())];
    steps:(enlist 0D),horizons;
    distinct ungroup `sym`time#update time:{[t;h] t+\:h}[t0;steps] from per}

/ The books a window's probes are priced against, read where the market is mapped (the HDB, over `h`) by tob_at, and
/ returned as the top-of-book table the core takes: sym, source, source_time, bid, ask and clock, arranged. rid
/ and date, which tob_at also returns, stay behind: a markout reads no depth.
/ @param h the handle to the process that maps market_data
/ @param probes table sym, time (probes)
/ @param read_from the oldest source_time a book may have
/ @param skew days either side of a time's date its books may be stored
/ @param venues `all, or the sources that count
/ @return the top-of-book table, memory proportional to the probes
/ @eg count .qetl.mapped.read_books[{value x};0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;`all]  ->  0
.qetl.mapped.read_books:{[h;probes;read_from;skew;venues]
    arg:`order`probes`read_from`skew`venues!(.qetl.mapped.order_of `market_data;probes;read_from;skew;venues);
    b:.qetl.source.ipc_call[h;{[a] .qetl.mapped.tob_at a};enlist arg];
    .qetl.mapped.columns#b}
