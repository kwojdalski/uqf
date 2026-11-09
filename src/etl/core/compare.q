/ compare.q - one table on two endpoints, compared: its schema, a bounded
/ sample of its rows paired by the table's key, and its publication coverage
/ (.qetl.compare, #2033).
/ .
/ Validating local processing against production meant collecting both sides
/ by hand. `uqs data diff` reaches each side's gateway - this machine's stack,
/ or a deploy target through an ssh tunnel - and hands this file the two
/ addresses. Everything that decides what the comparison SAYS is here, in q:
/ which rows are read, how a filter's text becomes a typed value, how rows
/ are paired, what counts as a difference, and the six outcomes. uqs opens
/ the tunnels and prints; the frontend asks uqs; neither holds a rule.
/ .
/ NOTHING HERE IS A SECOND CONTRACT. Each side is read through what the
/ stack already serves:
/   schema    `meta` on the RDB, routed by the gateway (.gw.syncexec) - what
/             the frontend's catalog reads
/   rows      .uqf.browse on the gateway, under the caller's query policy
/             (scripts/torqcode/gateway/browse.q) - the frontend's /query
/   coverage  .qetl.coverage.claims on the gateway, composed and gapped with
/             .qetl.coverage.compose and .gaps - the frontend's /coverage
/ and rows are paired with .qetl.verify.compare_rows, on the row key and the
/ equivalence basis the table's producers declare (.qetl.verify.producers,
/ .qetl.plant.equivalence) - the rule `uqs job check` holds producers to.
/ .
/ A SAMPLE IS NOT THE TABLE. Each side is read to at most `limit` rows. A read
/ that stopped there is TRUNCATED: rows past the cut were never compared, and
/ because each side cut at its own place, a key the other side's sample lacks
/ says nothing - it may simply lie past that side's cut. So in a truncated
/ comparison only paired rows are judged, and the keys either side holds
/ alone are counted as unpaired, not reported missing. `equal_in_sample` is
/ the only "equal" there is, and its label says whether every row in the
/ window was read or only a sample - never that the tables are equal.
/ .
/ A TABLE WITH NO KEY CANNOT BE JUDGED FROM A CUT SAMPLE (#2065). Its rows
/ are matched whole, so a row whose value changed is not "changed" but one
/ row on each side alone - exactly what a truncated comparison discards as
/ unpaired. Pairing by position or by `time` is no remedy: `time` is stamped
/ on receipt, so the two environments' samples are cut at different rows and
/ share no common prefix. Such a comparison is `not_comparable`, never
/ `equal_in_sample`, however many rows differ. Read it whole (narrow the
/ window, raise the limit) or declare a key.
/ .
/ THE OUTCOMES, first that applies:
/   endpoint_unavailable  a side could not be reached or did not answer
/   missing_table         a side has no such table
/   differs               the schemas differ, the published coverage differs
/                         (one side has published part of the window the
/                         other has not), or the rows differ
/   not_comparable       the table declares no row key and a sample was cut,
/                         so no row could be judged (see above)
/   incomplete_coverage   both sides agree, but neither has published the
/                         whole window, so nothing past their coverage was
/                         compared
/   equal_in_sample       nothing differs in what was read
/ .
/ `time` is the destination's own (the tickerplant stamps it on receipt), so
/ it is left out of the value comparison - two environments receive the same
/ row at different times - but it is what a window is read by, as the plant
/ reads every table (.qetl.plant.time_column).

\d .

/ The outcomes a comparison reaches, in the order they are decided.
/ @eg .qetl.compare.outcomes  ->  `endpoint_unavailable`missing_table`differs`not_comparable`incomplete_coverage`equal_in_sample
.qetl.compare.outcomes:`endpoint_unavailable`missing_table`differs`not_comparable`incomplete_coverage`equal_in_sample

/ The tiers a sample is read from.
.qetl.compare.tiers:`rdb`hdb

/ A filter's operator, as typed, and the .uqf.browse operator it is.
.qetl.compare.ops:(enlist "=";"!=";enlist "<";"<=";enlist ">";">=")!`eq`ne`lt`le`gt`ge

/ How long opening a side may take, in milliseconds.
.qetl.compare.open_timeout:10000

/ The program the RDB runs for a table's schema: its columns and their types,
/ or nothing when it has no such table. Sent as text, with the table as its
/ argument, so a TorQ tier's `value` applies it (see the frontend's queries.py).
.qetl.compare.meta_program:"{[tn] $[tn in tables[]; select c, t from 0!meta tn; ()]}"

/ ------------------------------------------------------------ FILTERS

/ A filter as typed - COLUMN OP VALUE, e.g. sym=EURUSD, px>=1.1,
/ sym=EURUSD,GBPUSD (a list: = is in, != not in) - split into its parts.
/ @param f the filter's text
/ @return a dict column (symbol), op (a .uqf.browse operator), values (the
/   value texts; more than one only for = and !=)
/ @throws error naming the filter when it is not COLUMN OP VALUE
/ @eg .qetl.compare.parse_filter["sym=EURUSD,GBPUSD"]`op  ->  `in
.qetl.compare.parse_filter:{[f]
    f:(),f;
    i:first where f in "=!<>";
    if[null i; '"filter ",f,": expected COLUMN OP VALUE, e.g. sym=EURUSD - OP one of = != < <= > >="];
    w:1+(count[f]>i+1) and "="=f (i+1)&count[f]-1;
    op:f i+til w;
    o:.qetl.compare.ops op;
    if[null o; '"filter ",f,": ",op," is not an operator - one of = != < <= > >="];
    c:`$i#f;
    parts:"," vs (i+w)_f;
    if[(null c) or any 0=count each parts; '"filter ",f,": expected COLUMN OP VALUE, e.g. sym=EURUSD"];
    if[(1<count parts) and not o in `eq`ne; '"filter ",f,": only = and != take a list of values"];
    `column`op`values!(c;$[(`eq=o) and 1<count parts; `in; o];parts)}

/ One filter value's text as the type its column holds - the value /query's
/ filter means (#2472): a timespan given as digits is a count of nanoseconds,
/ the form the frontend sends one in (q's "N"$ reads "1234" as 12:34); a
/ timestamp goes through .qetl.coerce.to_timestamp, so a Z or +hh:mm offset
/ is applied ("P"$ drops or refuses it); a char is exactly one character.
/ @param ty the column's meta type character
/ @param v the value's text
/ @return the typed atom
/ @throws error when the column is nested, or the text is not one of its type
/ @eg .qetl.compare.typed["f";"1.5"]  ->  1.5
/ @eg .qetl.compare.typed["n";"1234"]  ->  0D00:00:00.000001234
/ @eg .qetl.compare.typed["p";"2026-09-13T06:00:00+02:00"]  ->  2026.09.13D04:00:00.000000000
.qetl.compare.typed:{[ty;v]
    if[ty=" "; '"a nested column cannot be filtered on"];
    if[(ty="c") and not 1=count v; '"\"",v,"\" is not a ",.qetl.verify.type_name ty];
    d:$["-"=first v; 1_v; v];
    a:$[ty="s"; `$v;
        ty="c"; first v;
        ty="p"; .qetl.coerce.to_timestamp v;
        (ty="n") and (0<count d) and all d in .Q.n; "n"$"J"$v;
        (upper ty)$v];
    if[(not ty in "sc") and null a; '"\"",v,"\" is not a ",.qetl.verify.type_name ty];
    a}

/ The filters as .uqf.browse takes them, each value typed by its column.
/ @param m the table's schema: a table c, t
/ @param fs the parsed filters (parse_filter)
/ @return (columns;operators;values)
/ @throws error naming a column the table does not have, or a value its type refuses
/ @eg first .qetl.compare.browse_filters[([] c:enlist `sym; t:enlist "s");enlist .qetl.compare.parse_filter "sym=EURUSD"]  ->  enlist `sym
.qetl.compare.browse_filters:{[m;fs]
    types:exec c!t from m;
    one:{[types;f]
        c:f`column;
        if[not c in key types; '"filter on ",string[c],": no such column"];
        vals:.[{[ty;texts] .qetl.compare.typed[ty] each texts};(types c;f`values);{[c;e] '"filter on ",string[c],": ",e}[c]];
        (c;f`op;$[f[`op] in `in`ne; vals; first vals])}[types] each fs;
    $[count one; (one[;0];one[;1];one[;2]); (`symbol$();`symbol$();())]}

/ ------------------------------------------------------------ ONE SIDE

/ Open one side: its address with the login, or the reason it could not be.
/ @param ep a dict name, address ("host:port", or "" when it was not reached),
/   error (why it was not reached, or "")
/ @param login "user:password"
/ @return a handle, or the reason as a string
.qetl.compare.open:{[ep;login]
    if[count ep`error; :ep`error];
    .[{[a;l] hopen (`$":",a,":",l;.qetl.compare.open_timeout)};(ep`address;login);
        {[a;e] "could not connect to ",a,": ",e}[ep`address]]}

/ What one side holds of a table: read through the stack's own contracts.
/ `h` is a handle, or a function of one list standing in for one (a fixture):
/ every read is `h (name; args...)`.
/ @param h the side's gateway
/ @param tbl the table
/ @param req a dict range_from, range_to, filters (parsed), limit,
/   source_version (` for none: coverage not checked), partition (` for none)
/ @return a dict available, reason, exists, schema (c, t), rows, truncated,
/   coverage ((::) when not checked, else a dict covered, gaps), filter_absent
/   (the filter columns the table lacks on this side; when any, the side is
/   returned with its schema and no rows, since the filter cannot apply -
/   `verdict` reports that as a schema difference, or refuses it when both
/   sides lack the column)
.qetl.compare.snapshot:{[h;tbl;req]
    none:`available`reason`exists`schema`rows`truncated`coverage`filter_absent!(1b;"";0b;([] c:`symbol$(); t:`char$());();0b;(::);`symbol$());
    m:@[h;(`.gw.syncexec;(.qetl.compare.meta_program;tbl);`rdb);{[e] (`failed;e)}];
    if[(0h=type m) and `failed~first m; :none,`available`reason!(0b;"reading ",string[tbl],"'s schema failed: ",last m)];
    if[0=count m; :none];
    m:select c, t from 0!m;
    lack:distinct (`symbol$({x`column} each req`filters)) except m`c;
    if[count lack; :none,`exists`schema`filter_absent!(1b;m;lack)];
    fl:.qetl.compare.browse_filters[m;req`filters];
    lim:req`limit;
    got:@[h;(`.uqf.browse;tbl;`time`time,fl 0;`ge`lt,fl 1;(req`range_from;req`range_to),fl 2;lim+1;.qetl.compare.tiers);{[e] (`failed;e)}];
    if[(0h=type got) and `failed~first got; :none,`available`reason`exists`schema!(0b;"reading ",string[tbl],"'s rows failed: ",last got;1b;m)];
    rows:0!got`rows;
    trimmed:(lim<count rows) or (count[rows]>=got`cap) and got[`cap]<=lim;
    cv:$[null req`source_version; (::); .qetl.compare.coverage[h;tbl;req]];
    if[10h=type cv; :none,`available`reason`exists`schema!(0b;cv;1b;m)];
    none,`exists`schema`rows`truncated`coverage!(1b;m;lim sublist rows;trimmed;cv)}

/ A side's published coverage of the window: the claims current now, composed,
/ and what of the window they leave out.
/ @param h the side's gateway
/ @param tbl the table, as a dataset
/ @param req as snapshot's
/ @return a dict covered, gaps (tables range_from, range_to), or why the
/   ledger could not be read, as a string
.qetl.compare.coverage:{[h;tbl;req]
    c:@[h;(`.qetl.coverage.claims;tbl;req`partition;req`source_version;.z.p);{[e] (`failed;e)}];
    if[(0h=type c) and `failed~first c; :"reading ",string[tbl],"'s coverage failed: ",last c];
    `covered`gaps!(.qetl.coverage.compose c;.qetl.coverage.gaps[req`range_from;req`range_to;c])}

/ ------------------------------------------------------------ THE VERDICT

/ The row key and basis a table is compared on: what its producers declare,
/ or - when none, or they disagree - every compared column as `events`, so a
/ row is matched whole.
/ @param tbl the table
/ @return a dict key (symbols, empty for whole rows), basis
/ @eg .qetl.compare.basis[`demo_deals]  ->  `key`basis!(enlist `deal_id;`events)
.qetl.compare.basis:{[tbl]
    p:@[.qetl.verify.producers;tbl;{[e] ([] key_cols:())}];
    kc:distinct {`#asc (),x} each p[`key_cols] where 0<count each p`key_cols;
    ks:$[1=count kc; first kc; `symbol$()];
    b:.qetl.verify.basis tbl;
    `key`basis!(ks;$[(null b) or 0=count ks; `events; b])}

/ The columns the destination stamps itself, left out of the comparison.
/ @eg .qetl.compare.generated `demo_deals  ->  enlist `time
.qetl.compare.generated:{[tbl] d:.qetl.verify.destination tbl; $[(::)~d; enlist .qetl.plant.time_column; d`generated]}

/ The schemas' differences: a column one side lacks, or holds as another type.
/ @param l the left schema (c, t)
/ @param r the right schema
/ @return a table column, left, right - a type's name, or "absent"
/ @eg count .qetl.compare.schema_differences[([] c:`a`b; t:"jf");([] c:`a`b; t:"jj")]  ->  1
.qetl.compare.schema_differences:{[l;r]
    lt:exec c!t from l; rt:exec c!t from r;
    every:(l`c) union r`c;
    name:{[ts;c] $[c in key ts; .qetl.verify.type_name ts c; "absent"]};
    d:every where not {[lt;rt;c] (c in key lt) and (c in key rt) and lt[c]=rt c}[lt;rt] each every;
    ([] column:d; left:name[lt] each d; right:name[rt] each d)}

/ Private: a side's coverage, for the report.
/ @private
.qetl.compare.coverage_out:{[c] $[(::)~c; `checked`covered`gaps!(0b;();()); `checked`covered`gaps!(1b;0!c`covered;0!c`gaps)]}

/ Private: the verdict before anything is known - the shape every outcome fills.
/ @private
.qetl.compare.blank:{[tbl]
    `table`outcome`label`truncated`key`basis`read`schema`rows`unpaired`coverage`reasons!(
        tbl;`;"";0b;`symbol$();`;0 0j;
        ([] column:`symbol$(); left:(); right:());
        .qetl.verify.no_differences[];0j;
        `left`right!2#enlist `checked`covered`gaps!(0b;();());
        `left`right!("";""))}

/ Private: the differences, said from the sides' names - only_left, only_right,
/ and each count under the side it is.
/ @private
.qetl.compare.sided:{[names;diffs]
    side:{[k] $[k=`missing; `only_left; k=`extra; `only_right; k]} each diffs`kind;
    said:{[names;k;d] $[k=`missing; "on ",names[0]," only"; k=`extra; "on ",names[1]," only";
        k=`duplicated; ssr[ssr[d;"first producer";names 0];"second";names 1]; d]}[names]'[diffs`kind;diffs`detail];
    update kind:side, detail:said from diffs}

/ Whether a table is matched whole rows at a time: its producers declare no
/ key, or a key column is not one both samples hold as one type. Under such a
/ pairing a changed row is a missing row and an extra one, so a cut sample
/ cannot judge it.
/ @param tbl the table
/ @param lr the left sample
/ @param rr the right sample
/ @return a boolean
/ @eg .qetl.compare.keyless[`demo_deals;([] deal_id:1 2j);([] deal_id:1 2j)]  ->  0b
.qetl.compare.keyless:{[tbl;lr;rr]
    b:.qetl.compare.basis[tbl];
    lt:exec c!t from meta lr; rt:exec c!t from meta rr;
    same:((cols[lr] inter cols rr) except .qetl.compare.generated[tbl]);
    same:same where lt[same]=rt same;
    (0=count b`key) or 0<count (b`key) except same}

/ The rows two samples hold, compared: paired on the table's key with
/ .qetl.verify.compare_rows, over the columns both hold as one type, the
/ destination's own (`time`) left out. In a truncated comparison a key either
/ sample holds alone may lie past the other side's cut, so only paired rows
/ are judged and the rest are counted as unpaired.
/ @param tbl the table
/ @param names the sides' names
/ @param lr the left sample
/ @param rr the right sample
/ @param trunc whether either sample was cut
/ @return a dict key, basis, rows (rowkey, kind, detail), unpaired
/ @eg (.qetl.compare.rows_compared[`demo_deals;("a";"b");([] deal_id:1 2j; rate:1 2f);([] deal_id:1 2j; rate:1 3f);0b])`unpaired  ->  0
.qetl.compare.rows_compared:{[tbl;names;lr;rr;trunc]
    b:.qetl.compare.basis[tbl];
    lt:exec c!t from meta lr; rt:exec c!t from meta rr;
    common:(cols[lr] inter cols rr) except .qetl.compare.generated[tbl];
    same:common where lt[common]=rt common;
    ks:$[count b`key; b`key; same];
    if[count ks except same; ks:same; b[`basis]:`events];
    diffs:$[(0=count ks) or 0=count[lr]+count rr; .qetl.verify.no_differences[];
        .qetl.verify.compare_rows[same#lr;same#rr;ks;`symbol$();b`basis]];
    unpaired:$[trunc; exec count i from diffs where kind in `missing`extra`duplicated; 0j];
    if[trunc; diffs:select from diffs where kind=`changed];
    `key`basis`rows`unpaired!(ks;b`basis;.qetl.compare.sided[names;diffs];unpaired)}

/ Private: "n thing" or "n things".
/ @private
.qetl.compare.counted:{[n;word] string[n]," ",word,$[1=n; ""; "s"]}

/ Private: the label of a comparison that differs, naming each aspect.
/ @private
.qetl.compare.differs_label:{[v;aspects]
    said:{[v;a] $[a=`schema; "schema (",.qetl.compare.counted[count v`schema;"column"],")";
        a=`coverage; "published coverage";
        "rows (",.qetl.compare.counted[count v`rows;"difference"],$[v`truncated; ", in a truncated sample"; ""],")"]}[v] each aspects;
    "differs - ",", " sv said}

/ Private: the label of a comparison in which nothing differs.
/ @private
.qetl.compare.equal_label:{[v;lim]
    $[v`truncated;
        "equal in a sample cut at ",string[lim]," rows per side - NOT proof the tables are equal: rows past the cut were not compared",
            $[v`unpaired; " (",.qetl.compare.counted[v`unpaired;"key"]," in one sample only, not judged)"; ""];
        "equal in the sample - every row in the window was read on both sides and compared (",string[first v`read]," rows)"]}

/ Private: the filter columns each snapshot lacks, as (left;right); a column
/ both lack is the filter's mistake, not a difference, and is refused.
/ @private
.qetl.compare.absent_filters:{[l;r]
    absent:{[s] $[`filter_absent in key s; s`filter_absent; `symbol$()]} each (l;r);
    both:(absent 0) inter absent 1;
    if[count both; '"filter on ",string[first both],": no such column"];
    absent}

/ Private: the verdict of a table where a filter column is absent on one side:
/ the filter cannot apply there, so the schema difference is the finding and no
/ rows are compared.
/ @private
.qetl.compare.filter_absent:{[v;names;ls;rs;absent]
    lacks:0<count each absent;
    v[`schema]:.qetl.compare.schema_differences[ls;rs];
    v[`reasons]:`left`right!{[a] $[count a; "lacks the filter column ",", " sv string a; ""]} each absent;
    said:"; " sv {[n;a] n," lacks the filter column ",", " sv string a}'[names where lacks;absent where lacks];
    v,`outcome`label!(`differs;.qetl.compare.differs_label[v;enlist `schema],"; rows not compared: ",said)}

/ Compare one table's two snapshots. Pure: reads nothing but its arguments
/ and the tree's declarations of the table's key and basis.
/ @param tbl the table
/ @param names the two sides' names, as strings (left;right)
/ @param l the left snapshot (snapshot's)
/ @param r the right snapshot
/ @param lim the row limit each side was read to
/ @return a dict table, outcome, label, truncated, key, basis, read (rows
/   read each side), schema (schema_differences), rows (rowkey, kind, detail),
/   unpaired (keys one truncated side holds alone, not judged), coverage (each
/   side's checked, covered, gaps), reasons (each side's, when unavailable)
/ @eg .qetl.compare.verdict[`t;("a";"b");`available`reason!(0b;"down");`available`reason!(0b;"down");10]`outcome  ->  `endpoint_unavailable
.qetl.compare.verdict:{[tbl;names;l;r;lim]
    v:.qetl.compare.blank[tbl];
    down:not (l`available;r`available);
    if[any down;
        v[`reasons]:`left`right!{[s;d] $[d; s`reason; ""]}'[(l;r);down];
        :v,`outcome`label!(`endpoint_unavailable;"endpoint unavailable - ",", " sv {[n;s] n,": ",s`reason}'[names where down;(l;r) where down])];
    gone:not (l`exists;r`exists);
    if[any gone; :v,`outcome`label!(`missing_table;"missing table - ",string[tbl]," is not on ",(" or " sv names where gone))];
    absent:.qetl.compare.absent_filters[l;r];
    if[any 0<count each absent; :.qetl.compare.filter_absent[v;names;l`schema;r`schema;absent]];
    trunc:any (l`truncated;r`truncated);
    v,:`truncated`read`schema`coverage!(trunc;(count l`rows;count r`rows);.qetl.compare.schema_differences[l`schema;r`schema];
        `left`right!.qetl.compare.coverage_out each (l`coverage;r`coverage));
    v,:.qetl.compare.rows_compared[tbl;names;l`rows;r`rows;trunc];
    gaps:{[c] $[(::)~c; (); 0!c`gaps]} each (l`coverage;r`coverage);
    checked:not (::)~l`coverage;
    aspects:(`schema`coverage`rows) where (0<count v`schema;checked and not (~). gaps;0<count v`rows);
    if[count aspects; :v,`outcome`label!(`differs;.qetl.compare.differs_label[v;aspects])];
    if[trunc and .qetl.compare.keyless[tbl;l`rows;r`rows];
        :v,`outcome`label!(`not_comparable;"not comparable - ",string[tbl]," declares no row key and a sample was cut at ",
            string[lim]," rows per side, so a changed row cannot be told from one past the cut; narrow the window or raise the limit to read it whole")];
    if[checked and 0<count first gaps;
        :v,`outcome`label!(`incomplete_coverage;"incomplete coverage - neither side has published the whole window (",
            .qetl.compare.counted[count first gaps;"gap"],"); the rows both have agree")];
    v,`outcome`label!(`equal_in_sample;.qetl.compare.equal_label[v;lim])}

/ ------------------------------------------------------------ THE RUN

/ Compare tables on two endpoints over one window.
/ @param left the left endpoint: a dict name, address, error (see open); or a
/   handle-like function (a fixture) under `h
/ @param right the right endpoint
/ @param tabs the tables, symbols
/ @param req a dict range_from, range_to, filters (the texts, as typed), limit,
/   source_version (` for none), partition (` for none), login ("user:password")
/ @return a dict left, right (the sides' names), range_from, range_to, limit, filters,
/   source_version, tables (one verdict per table), outcome (the first of
/   .qetl.compare.outcomes any table reached)
/ @throws error naming a filter that is not COLUMN OP VALUE, a limit that is
/   not positive, or an empty window
.qetl.compare.run:{[left;right;tabs;req]
    if[not req[`range_to]>req`range_from; '"the window must be non-empty: --from before --to"];
    if[not 0<req`limit; '"the row limit must be positive"];
    tabs:(),tabs;
    if[0=count tabs; '"name at least one table"];
    fs:.qetl.compare.parse_filter each req`filters;
    r2:@[req;`filters;:;fs];
    hs:{[login;ep] $[`h in key ep; ep`h; .qetl.compare.open[ep;login]]}[req`login] each (left;right);
    names:{[ep] ep`name} each (left;right);
    one:{[hs;names;r2;tbl]
        s:{[r2;tbl;h] $[10h=type h; `available`reason!(0b;h); .qetl.compare.snapshot[h;tbl;r2]]}[r2;tbl] each hs;
        .qetl.compare.verdict[tbl;names;s 0;s 1;r2`limit]}[hs;names;r2] each tabs;
    {[h] if[-6h=type h; @[hclose;h;{[e] ::}]]} each hs;
    worst:.qetl.compare.outcomes min .qetl.compare.outcomes?one`outcome;
    `left`right`range_from`range_to`limit`filters`source_version`partition`tables`outcome!(
        names 0;names 1;req`range_from;req`range_to;req`limit;req`filters;req`source_version;req`partition;one;worst)}

\d .
