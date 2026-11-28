/ test_mapped_match.q - the as-of search over a table's DECLARED sort, and a condition's POINTER column (src/etl/core/mapped_books.q,
/ mapped_pointers.q, #3000).
/ .
/ Claims, each held to a brute force and not to "it ran":
/   * nothing is hard-wired to market_data's columns: the equality keys and the as-of column are read from an order, so a table
/     sorted by region, sym, venue, ts - or by sym, time - is searched by the same code and gives what a scan gives
/   * the pointer a condition stores is, for each row, the position of the latest row of its own run at or before it that passes -
/     null when none, and never a row of the run before it
/   * the writers store it: the staged-partition writer reads back what a scan gives, and a corrupt column is named
/   * a match returns the latest row at or before the probe that passes the condition, within the probe's oldest bound, null where
/     none does - by one read of the pointer, with no loop through the failing rows
/   * a match may name only an equality key or a declared condition, and is refused by name otherwise

/ Books of three sources: a sym's run is ascending by a unique time, a tenth of each side is withdrawn, two clocks.
.qt.qetl.mapped.match.market:{[n;seed]
    system "S ",string seed;
    t:2026.09.17D10:00:00+400000000*(neg n)?n;
    rows:([] sym:n?`EURUSD`GBPUSD; source:n?`a`b`c; source_time:t; market:n?`fx`crypto; clock:n?`venue`receipt;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:{?[x<0.1;0n;2+x]} n?1f);
    .qetl.mapped.arrange[`sym`source`source_time] rows}

/ The pointer of each row by a scan: the latest position of the same keys at or before it that passes, null if none.
.qt.qetl.mapped.match.scan_pointer:{[keycols;ok]
    n:count ok;
    {[keycols;ok;i]
        same:(til count ok)<=i;
        j:0;
        while[j<count keycols; same:same & (keycols j)=(keycols j) i; j+:1];
        w:where ok & same;
        $[count w; last w; 0N]}[keycols;ok] each til n}

/ The latest row of each probe's run at or before its time and no older than its `oldest`, among those a flag accepts, by a scan:
/ the quoted time, null where none.
.qt.qetl.mapped.match.brute:{[tob;order;probes;keep]
    ks:-1_order;
    ac:last order;
    {[tob;ks;ac;keep;p]
        m:keep & (tob[ac]<=p ac) & tob[ac]>=p`oldest;
        i:0;
        while[i<count ks; m:m & (tob ks i)=p ks i; i+:1];
        r:tob[ac] where m;
        $[count r; last r; 0Np]}[tob;ks;ac;keep] each probes}

/ Probes of the books' own runs, each looking back at most `back`.
.qt.qetl.mapped.match.probes:{[tob;order;m;back;seed]
    system "S ",string seed;
    ks:-1_order;
    ac:last order;
    ix:m?count tob;
    p:flip (ks!(tob ks) @\: ix),enlist[ac]!enlist 2026.09.17D10:00:00+m?600000000000;
    @[p;`oldest;:;(p ac)-back]}

/ --- the pointer ------------------------------------------------------------------------------------------

.qt.qetl.mapped.match.test_the_pointer_is_the_latest_passing_row_of_the_same_run_and_null_when_none:{[t]
    / two runs: a passes at 1 and 3; b passes at 6 only. b's first rows must not point into a
    kcols:enlist `a`a`a`a`b`b`b;
    ok:0101001b;
    .qunit.assertEquals[.qetl.mapped.pointer[kcols;ok];0N 1 1 3 0N 0N 6;"a failing row points at the latest passing row before it in its run; before any, null"];
    .qunit.assertEquals[.qetl.mapped.pointer[kcols;1111111b];til 7;"every row passing: each points at itself"];
    .qunit.assertEquals[.qetl.mapped.pointer[kcols;0000000b];7#0N;"none passing: all null"];
    .qunit.assertEquals[.qetl.mapped.pointer[kcols;0000001b];0N 0N 0N 0N 0N 0N 6;"a pass in the last run reaches no earlier run"];
    .qunit.assertEquals[count .qetl.mapped.pointer[enlist `symbol$();`boolean$()];0;"an empty table has no pointers"];
    / random, over three keys, against a scan
    system "S 71";
    n:3000;
    kc:(n?`a`b;n?`x`y;n?3);
    o:`k0`k1`k2;
    srt:iasc flip o!kc;
    kc:kc@\:srt;
    okv:0<n?3;
    .qunit.assertEquals[.qetl.mapped.pointer[kc;okv];.qt.qetl.mapped.match.scan_pointer[kc;okv];"three equality keys: the pointer equals a scan, run boundaries included"]}

.qt.qetl.mapped.match.test_the_staged_writer_stores_the_pointer_and_a_corrupt_column_is_named:{[t]
    dir:first system "mktemp -d";
    root:hsym `$dir;
    n:600;
    system "S 72";
    rows:([] time:2026.09.17D10:00:00+til n; sym:n?`EURUSD`GBPUSD; source:n?`a`b`c; market:n#`fx; source_time:2026.09.17D10:00:00+400000000*(neg n)?n;
        bid:{?[x<0.2;0n;1+x]} n?1f; ask:{?[x<0.2;0n;2+x]} n?1f; clock:n#`venue; rid:n#0N; two_sided_rid:n#0N);
    rows:`sym`source`source_time xasc rows;
    part:hsym `$dir,"/2026.09.17/market_data/";
    part set .Q.en[root;rows];
    .qetl.io.assign_row_ids[part;`market_data];
    on_disk:{[p;c] get ` sv p,c};
    rid:on_disk[part;`rid];
    ptr:on_disk[part;`two_sided_rid];
    ok:(not null rows`bid) & not null rows`ask;
    kc:(rows`sym;rows`source);
    .qunit.assertEquals[rid;til n;"the row numbers are written"];
    .qunit.assertEquals[ptr;.qt.qetl.mapped.match.scan_pointer[kc;ok];"and each row's pointer, equal to a scan"];
    .qunit.assertTrue[(any null ptr) and any not null ptr;"the fixture has runs with no passing row yet, and runs with one"];
    .qunit.assertEquals[.qetl.mapped.pointers.reason[part;`market_data];"";"a check of the written partition finds nothing"];
    (` sv part,`two_sided_rid) set ptr+1;
    .qunit.assertEquals[.qetl.mapped.pointers.reason[part;`market_data];"two_sided_rid does not hold each row's pointer";"a column that disagrees is named"];
    .qunit.assertEquals[.qetl.mapped.pointers.compute[`:/no/such/dir;`no_such_table];(`symbol$())!();"a table with no condition has no pointers"];
    system "rm -rf ",dir}

/ --- the search over market_data's order -------------------------------------------------------------------

.qt.qetl.mapped.match.test_a_condition_gives_the_latest_passing_row_inside_the_window_and_null_where_none:{[t]
    tob:.qetl.mapped.with_pointers[`market_data;.qt.qetl.mapped.match.market[1500;41];enlist `two_sided];
    order:`sym`source`source_time;
    p:.qt.qetl.mapped.match.probes[tob;order;300;0D00:00:20;42];
    plain:.qetl.mapped.asof[order;tob;`sym`source`source_time#p];
    got:.qetl.mapped.asof_where[order;tob;p;`symbol$();enlist[`two_sided]!enlist 1b];
    keep:(not null tob`bid) & not null tob`ask;
    .qunit.assertEquals[got`quoted;.qt.qetl.mapped.match.brute[tob;order;p;keep];"the latest two-sided book inside the window"];
    .qunit.assertTrue[any (got`quoted)<plain`quoted;"a probe whose latest book is one-sided gets an earlier two-sided one"];
    .qunit.assertTrue[all (null got`quoted) | (got`quoted)>=p`oldest;"never older than the probe allows"];
    / the oldest bound: a window too short for the previous passing book has none; widening it finds one
    near:@[p;`oldest;:;(p`source_time)-0D00:00:01];
    far:@[p;`oldest;:;(p`source_time)-0D00:10:00];
    a:.qetl.mapped.asof_where[order;tob;near;`symbol$();enlist[`two_sided]!enlist 1b];
    b:.qetl.mapped.asof_where[order;tob;far;`symbol$();enlist[`two_sided]!enlist 1b];
    .qunit.assertEquals[a`quoted;.qt.qetl.mapped.match.brute[tob;order;near;keep];"a one-second window, against a scan"];
    .qunit.assertTrue[(sum null a`quoted)>sum null b`quoted;"widen it and probes that had no book find one: ",string[sum null a`quoted]," -> ",string sum null b`quoted];
    .qunit.assertTrue[all (null a`quoted) | (b`quoted)>=a`quoted;"the wider window never returns an older row"];
    / the books come back with their other columns
    ix:where not null got`quoted;
    .qunit.assertTrue[all not null (got`bid) ix;"the matched row is two-sided: it has a bid"];
    .qunit.assertTrue[all not null (got`ask) ix;"and an ask"]}

.qt.qetl.mapped.match.test_an_equality_key_in_the_match_picks_the_runs_searched:{[t]
    tob:.qetl.mapped.with_pointers[`market_data;.qt.qetl.mapped.match.market[1500;45];enlist `two_sided];
    order:`sym`source`source_time;
    p:.qt.qetl.mapped.match.probes[tob;order;300;0D00:00:30;46];
    k:.qetl.mapped.asof_where[order;tob;p;`symbol$();enlist[`source]!enlist `a`b];
    want:.qt.qetl.mapped.match.brute[tob;order;p;(tob`source) in `a`b];
    .qunit.assertEquals[k`quoted;want;"a key condition keeps the runs it names"];
    .qunit.assertTrue[all (null k`quoted) | not (k`source)=`c;"no book of source c"];
    both:.qetl.mapped.asof_where[order;tob;p;`symbol$();`source`two_sided!(`a;1b)];
    keep:(not null tob`bid) & (not null tob`ask) & (tob`source)=`a;
    .qunit.assertEquals[both`quoted;.qt.qetl.mapped.match.brute[tob;order;p;keep];"a key and a condition together"]}

.qt.qetl.mapped.match.test_extra_columns_come_back_for_the_matched_rows_and_a_missing_one_is_refused_by_name:{[t]
    tob:.qetl.mapped.with_pointers[`market_data;.qt.qetl.mapped.match.market[600;47];enlist `two_sided];
    order:`sym`source`source_time;
    p:.qt.qetl.mapped.match.probes[tob;order;100;0D00:00:30;48];
    got:.qetl.mapped.asof_where[order;tob;p;enlist `market;(`symbol$())!()];
    at:where not null got`quoted;
    ref:(`sym`source`source_time xkey select sym, source, source_time, market from tob) ([] sym:got`sym; source:got`source; source_time:got`quoted);
    .qunit.assertEquals[(got`market) at;(ref`market) at;"the declared column of the matched row"];
    .qunit.assertThrows[{[a] .qetl.mapped.asof_where[a 0;a 1;a 2;enlist `nope;(`symbol$())!()]};(order;tob;p);"mapped: the table lacks nope";"a column the table lacks is named"];
    .qunit.assertThrows[{[a] .qetl.mapped.asof_where[a 0;delete two_sided_rid from a 1;a 2;`symbol$();enlist[`two_sided]!enlist 1b]};(order;tob;p);"mapped: the table lacks two_sided_rid";"a table without the condition's pointer column is refused, naming it"];
    .qunit.assertThrows[{[a] .qetl.mapped.asof_where[a 0;a 1;`sym`source`source_time#a 2;`symbol$();enlist[`two_sided]!enlist 1b]};(order;tob;p);"mapped: a match on two_sided needs the probes' oldest*";"a condition needs the window it is searched in"]}

/ --- nothing is hard-wired to market_data's order ---------------------------------------------------------

/ A desk's quotes: sorted by region, sym, venue, ts - three equality keys and an as-of column that is not called source_time -
/ with a pointer column for the condition qty > 50.
.qt.qetl.mapped.match.desk:{[n;seed]
    system "S ",string seed;
    rows:([] region:n?`emea`apac; sym:n?`a`b`c; venue:n?`x`y; ts:2026.09.17D10:00:00+400000000*(neg n)?n;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:n?2f; clock:n#`venue; qty:n?100f);
    rows:.qetl.mapped.arrange[`region`sym`venue`ts] rows;
    @[rows;`big_rid;:;.qetl.mapped.pointer[rows`region`sym`venue;(rows`qty)>50f]]}

.qt.qetl.mapped.match.test_a_table_declared_with_another_order_is_searched_by_the_same_code:{[t]
    order:`region`sym`venue`ts;
    tob:.qt.qetl.mapped.match.desk[2000;49];
    .qunit.assertEquals[.qetl.mapped.arranged[order;tob];"";"arranged by the order it is handed"];
    .qunit.assertEquals[.qetl.mapped.arranged[`sym`source`source_time;tob];"lacks source, source_time";"and not by market_data's"];
    r:.qetl.mapped.runs[order;tob;`];
    .qunit.assertEquals[cols r;`region`sym`venue`lo`hi;"one column per equality key"];
    .qunit.assertEquals[sum (r`hi)-r`lo;count tob;"the runs hold every row once"];
    p:.qt.qetl.mapped.match.probes[tob;order;400;0D00:00:30;50];
    plain:.qetl.mapped.asof[order;tob;`region`sym`venue`ts#p];
    ref:aj[`region`sym`venue`ts;`region`sym`venue`ts#p;select region, sym, venue, ts, quoted:ts from tob];
    .qunit.assertEquals[plain`quoted;ref`quoted;"the plain search is aj over the declared keys"];
    m:`big`venue!(1b;`x);
    got:.qetl.mapped.asof_where[order;tob;p;enlist `qty;m];
    want:.qt.qetl.mapped.match.brute[tob;order;p;((tob`qty)>50f) & (tob`venue)=`x];
    .qunit.assertEquals[got`quoted;want;"a key condition and a declared condition, over three equality keys"];
    ix:where not null got`quoted;
    .qunit.assertTrue[all (got`qty) ix >50f;"the declared extra column comes from the matched row, which passes"];
    / two columns: an equality key and an as-of column called time
    two:.qetl.mapped.arrange[`sym`time] select sym, time:ts, bid, ask, clock, qty from tob;
    two:@[two;`big_rid;:;.qetl.mapped.pointer[enlist two`sym;(two`qty)>50f]];
    q:@[flip `sym`time!(p`sym;p`ts);`oldest;:;(p`ts)-0D00:00:30];
    g2:.qetl.mapped.asof_where[`sym`time;two;q;`symbol$();enlist[`big]!enlist 1b];
    w2:.qt.qetl.mapped.match.brute[two;`sym`time;q;(two`qty)>50f];
    .qunit.assertEquals[g2`quoted;w2;"an order of two columns with the as-of column called time"]}

.qt.qetl.mapped.match.test_the_order_is_read_from_the_declared_sort_and_a_table_without_one_is_refused:{[t]
    .qunit.assertEquals[.qetl.mapped.market_order[];exec column from .qetl.plant.sort_rules where table=`market_data;"market_data's order is its declared sort"];
    .qunit.assertEquals[.qetl.mapped.market_order[];`sym`source`source_time;"unchanged from before it was read from the declaration"];
    .qunit.assertThrows[{.qetl.mapped.order_of `no_such_table_declared};"mapped: no_such_table_declared declares no sort order*";"a table with no declared sort has no order"];
    .qunit.assertThrows[{.qetl.mapped.order_of enlist `sym};"mapped: an order is two or more distinct columns*";"an order needs an equality key and an as-of column"]}

/ --- a match is refused by name unless it names an equality key or a declared condition -------------------

.qt.qetl.mapped.match.test_a_match_on_anything_else_is_refused_by_name:{[t]
    types:`sym`source`source_time`bid`ask`clock!11 11 12 9 9 11h;
    f:{[a] .qetl.mapped.require_match[`eg;`sym`source`source_time;a 0;enlist `two_sided;a 1]};
    .qunit.assertEquals[f (types;enlist[`two_sided]!enlist 1b);enlist[`two_sided]!enlist 1b;"a declared condition is accepted"];
    .qunit.assertEquals[f (types;enlist[`source]!enlist `a`b);enlist[`source]!enlist `a`b;"so is an equality key, one value or a list"];
    .qunit.assertThrows[f;(types;enlist[`bid]!enlist 1.5);"eg: match on bid, which is neither a declared condition (two_sided) nor an equality key (sym, source) - declare it with .qetl.plant.condition*";"a stored column that is no key and no declared condition"];
    .qunit.assertThrows[f;(types;enlist[`nope]!enlist 1b);"eg: match on nope, which is neither a declared condition*";"a name nothing declares"];
    .qunit.assertThrows[f;(types;enlist[`source_time]!enlist 2026.09.17D10:00:00);"eg: match on source_time, which is neither a declared condition*";"the as-of column is not a key to match"];
    .qunit.assertThrows[f;(types;enlist[`two_sided]!enlist 0b);"eg: match on condition two_sided must be 1b: the latest book that passes it";"a condition must hold"];
    .qunit.assertThrows[f;(types;enlist[`source]!enlist 5);"eg: match on source must hold a value of the key's own type";"an operand of the wrong type"];
    .qunit.assertThrows[{[a] .qetl.mapped.require_match[`eg;`sym`source`source_time;a;`two_sided`other;`two_sided`other!11b]};types;"eg: match names the conditions two_sided, other - one condition per match*";"two conditions cannot be answered by one pointer"]}
