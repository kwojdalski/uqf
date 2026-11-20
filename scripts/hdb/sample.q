/ sample.q - copy a row-limited sample of HDB tables into another HDB
/ (#2812): `uqs data sample`.
/ .
/ Run as:  q scripts/hdb/sample.q <source root> <destination root>
/            <tables, comma-separated> <days, comma-separated YYYY.MM.DD>
/            <limit> <on-conflict: refuse|ignore|replace> <mode: plan|stage>
/            <stage dir, or "-" for plan>
/ .
/ WHY. A schema migration (`uqs data migrate`, `uqs data alter`) is rehearsed
/ on a small HDB, not on production volumes. A plain cp of a sample is not
/ one: a symbol column holds indices into its HDB's sym file, so copied into
/ another HDB it decodes to whatever that sym file holds at those indices -
/ every value plausible, every value wrong. So each enumerated column is
/ decoded against the SOURCE's domain file and enumerated against the
/ DESTINATION's (`:dst/sym?v, which appends under the file's own lock, as
/ seed.q does), never copied.
/ .
/ WHICH ROWS. At most <limit> rows per table per day, spread evenly over the
/ day's rows in their stored order (rows 0, n%k, 2*n%k ...), not the first k:
/ a table sorted p# by sym would otherwise sample one or two syms. A
/ subsequence of a sorted or grouped column stays so, so the source's
/ attribute is put back; a column it cannot be put back on is refused.
/ .
/ TWO MODES, ONE CHECK. Both read every table to sample and check its schema
/ - column names, order and types - against the destination's table when it
/ already holds one, else against the first sampled day, before anything is
/ written. `plan` then prints PLAN|<json> and writes nothing anywhere.
/ `stage` writes each sampled table under <stage dir>/<day>/<table>, outside
/ the destination root so no partial table is ever visible in it, reads each
/ back and compares the decoded values with what it meant to write, and
/ prints STAGED|<json>. uqs.stack.hdb_sample moves the staged tables in.
/ .
/ What is refused: a source and destination that are the same directory, a
/ segmented HDB (par.txt) on either side, a schema mismatch, a table the
/ source holds on none of the days, and - with on-conflict refuse - a table
/ the destination already holds on a sampled day. A day the source has a
/ partition for but lacks a table on, where the destination does not hold
/ the table either, gets an empty table of the same schema (status `fill`),
/ so every sampled partition holds every sampled table and the destination
/ stays queryable. A day the source has no partition for is `none`: nothing
/ is written for it.

args:.z.x;
if[8>count args; -2 "hdb_sample: needs 8 arguments - see the header"; exit 2];
src:hsym `$args 0;
dst:hsym `$args 1;
wanted:`$"," vs args 2;
days:asc distinct "D"$"," vs args 3;
limit:"J"$args 4;
conflict:`$args 5;
mode:`$args 6;
stage:$[(args 7)~(),"-"; `; hsym `$args 7];

fail:{[msg] -2 "hdb_sample: ",msg; exit 1};

if[any null days; fail["a day is not YYYY.MM.DD: ",args 3]];
if[(null limit) or limit<1; fail["the limit must be a whole number of rows, at least 1"]];
if[not conflict in `refuse`ignore`replace; fail["on-conflict must be refuse, ignore or replace"]];
if[not mode in `plan`stage; fail["the mode must be plan or stage"]];
if[(mode=`stage) and null stage; fail["stage needs a stage directory"]];
if[()~key src; fail["no source HDB at ",1_string src]];
if[src~dst; fail["the source and the destination are the same HDB"]];
if[not ()~key ` sv src,`par.txt; fail["the source is a segmented HDB (par.txt): not sampled yet"]];
if[not ()~key ` sv dst,`par.txt; fail["the destination is a segmented HDB (par.txt): not sampled yet"]];

/ The type of a stored column as one character: q's, except that an
/ enumeration is `s` - .Q.ty would need its domain loaded to say so.
ty:{[x] $[(type x) within 20 76; "s"; .Q.ty x]}

/ Two type characters agree: equal, or one is an empty general column, which
/ is how an empty nested column is written (as .Q.chk writes one too).
same_ty:{[a;b] (a=b) or ((a=" ") and b within "AZ") or (b=" ") and a within "AZ"}

/ Does root hold table t on day d?
holds:{[root;d;t] not ()~key ` sv .Q.par[root;d;t],`.d}

/ The column names and their type characters of a stored table.
schema:{[root;d;t]
    dir:.Q.par[root;d;t];
    c:get ` sv dir,`.d;
    (c;{[dir;c] ty get ` sv dir,c}[dir] each c)}

/ The partition days under a root, oldest first.
root_days:{[root]
    if[()~key root; :`date$()];
    e:key root;
    asc "D"$string e where e like "[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]"}

/ Why schema `got` cannot sit beside `want`, or "" when it can.
mismatch:{[want;got]
    if[not (want 0)~got 0;
        :"its columns are ",(", " sv string got 0)," but ",(", " sv string want 0)," are expected"];
    bad:where not same_ty'[want 1;got 1];
    if[count bad;
        i:first bad;
        :"column ",string[(got 0) i]," is type ",((got 1) i),", not ",(want 1) i];
    ""}

dst_days:root_days[dst];
src_days:root_days[src];
if[not any days in src_days;
    fail["the source holds no partition on any of the days: ",", " sv string days]];

/ The row count of a stored table, from its first column.
row_count:{[root;d;t]
    dir:.Q.par[root;d;t];
    c:get ` sv dir,`.d;
    $[count c; count get ` sv dir,first c; 0]}

/ The rows to take of n: all of them up to the limit, else k spread evenly.
pick:{[n] k:limit&n; $[k=n; til n; `long$floor (til k)*n%k]}

/ Pass one: every (day;table) the sample covers, checked before any write.
plan:([] day:`date$(); tbl:`symbol$(); status:`symbol$(); source_rows:`long$(); rows:`long$());
{[t]
    present:days where holds[src;;t] each days;
    if[not count present;
        fail["the source holds ",string[t]," on none of the days: ",", " sv string days]];
    in_dst:dst_days where holds[dst;;t] each dst_days;
    / The destination's own table is the schema to match, when it holds one.
    want:$[count in_dst; schema[dst;last in_dst;t]; schema[src;first present;t]];
    {[t;want;d]
        here:holds[src;d;t];
        if[here;
            why:mismatch[want;schema[src;d;t]];
            if[count why; fail string[t]," on ",string[d],": ",why," - nothing was written"]];
        taken:holds[dst;d;t];
        n:$[here; row_count[src;d;t]; 0];
        st:$[not d in src_days; `none;
            taken and conflict=`refuse; `conflict;
            taken and conflict=`ignore; `ignore;
            taken and not here; `absent;
            taken; `replace;
            here; `copy;
            `fill];
        `plan insert (d;t;st;n;$[st in `copy`replace; limit&n; 0]);
        }[t;want] each days;
    } each wanted;

conflicts:select from plan where status=`conflict;
if[count conflicts;
    fail "the destination already holds ",(", " sv {string[x]," on ",string y}'[conflicts`tbl;conflicts`day]),
        " - pass --on-conflict ignore to keep them or replace to sample over them; nothing was written"];

/ The report, as JSON: one row per (day;table).
report:{[]
    `source`destination`limit`days`tables`partitions!(1_string src;1_string dst;limit;
        string days;string wanted;
        update day:string day from plan)}

if[mode=`plan; -1 "PLAN|",.j.j report[]; exit 0];

/ A source column's sampled values, decoded when enumerated: (values;domain).
sampled:{[d;t;c;idx]
    raw:get ` sv .Q.par[src;d;t],c;
    sub:raw idx;
    if[(type raw) within 20 76;
        dom:key raw;
        :((get ` sv src,dom) `long$sub;dom;attr raw)];
    if[(77h<=type raw) and count sub;
        if[any (type each sub) within 20 76;
            fail string[t],".",string[c]," on ",string[d],
                ": a nested enumerated column is not sampled yet - nothing was moved in"]];
    (sub;`;attr raw)}

/ Values enumerated against the destination's domain file of the same name,
/ with the source's attribute put back.
encode:{[t;c;got]
    v:$[null got 1; got 0; (` sv dst,got 1)?got 0];
    a:got 2;
    if[null a; :v];
    @[{[a;v] a#v}[a];v;{[t;c;a;e] fail string[t],".",string[c],": cannot keep its `",string[a],
        "# attribute on the sample (",e,") - nothing was moved in"}[t;c;a]]}

/ An empty column shaped as the source's: an empty enumeration of its domain,
/ else 0# of the column.
empty_col:{[d0;t;c]
    raw:get ` sv .Q.par[src;d0;t],c;
    $[(type raw) within 20 76; (` sv dst,key raw)?`symbol$(); 0#raw]}

/ Decode a staged column against the destination, to compare.
decoded:{[v] $[(type v) within 20 76; (get ` sv dst,key v) `long$v; v]}

/ Pass two: stage each (day;table) to write, then read it back.
staged:0#plan;
w:where plan[`status] in `copy`replace`fill;
{[d;t;st]
    dir:` sv stage,(`$string d),t;
    if[st=`fill;
        d0:first days where holds[src;;t] each days;
        c:get ` sv .Q.par[src;d0;t],`.d;
        (` sv dir,`) set flip c!empty_col[d0;t] each c;
        `staged insert (d;t;st;0;0);
        :()];
    c:get ` sv .Q.par[src;d;t],`.d;
    idx:pick[row_count[src;d;t]];
    got:sampled[d;t;;idx] each c;
    (` sv dir,`) set flip c!encode[t]'[c;got];
    back:get ` sv dir,`.d;
    if[not back~c; fail string[t]," on ",string[d],": the staged columns differ from the source's"];
    ok:{[dir;c;want] want~decoded[get ` sv dir,c]}[dir]'[c;got[;0]];
    if[not all ok;
        fail string[t]," on ",string[d],": the staged ",(", " sv string c where not ok),
            " do not decode to the source's values - nothing was moved in"];
    `staged insert (d;t;st;row_count[src;d;t];count idx);
    }'[plan[`day] w;plan[`tbl] w;plan[`status] w];

-1 "STAGED|",.j.j `stage`staged!(1_string stage;update day:string day from staged);
exit 0
