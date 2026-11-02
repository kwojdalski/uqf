/ verify_compare.q - do the producers return the same rows? (.qetl.verify, #1851)
/ .
/ Business keys, values and multiplicities - never counts alone. Two producers
/ that each return three rows can disagree about every one of them, and
/ the comparison pairs rows by key before it looks at anything else.
/ .
/ WHAT IS COMPARED is the columns both producers return, minus what the destination
/ generates (derived from its schema: .qetl.verify.destination). WHAT "THE SAME"
/ MEANS is the dataset's declared basis (.qetl.plant.equivalence), because one
/ schema does not imply one semantics:
/   events       the multiset of rows. A key twice in one producer and once in
/                the other is a difference; so is any changed value
/   revisions    the sequence of rows per key, in the order each producer returned them
/   final_state  the last row per key; earlier revisions are not compared
/ A dataset that declares no basis is not compared at all.
/ .
/ A comparison that cannot be made says UNVERIFIED with the reason - one producer,
/ an empty sample, a failed or truncated capture, an undeclared key or basis - and
/ is never a PASS.

\d .

/ The empty differences table.
/ @return a table rowkey (text), kind, detail
/ @eg count .qetl.verify.no_differences[]  ->  0
.qetl.verify.no_differences:{[] ([] rowkey:(); kind:`symbol$(); detail:())}

/ Private: a row's key as text, for a report.
/ @private
.qetl.verify.key_text:{[ks;row] ", " sv {string[x],"=",.qf.util.full y}'[ks;row ks]}

/ Private: one column's cells equal, row by row, floats within the transform
/ framework's own tolerance.
/ @private
.qetl.verify.cells_equal:{[x;y]
    $[0h=type x; x~'y;
      type[x] in 9 8h; (null[x]=null y) & (null x) | .qetl.transform.float_tolerance>=abs x-y;
      x=y]}

/ Private: a producer's rows reduced to what the basis compares, each row numbered
/ within its key: events sorted so that order does not matter, revisions as returned,
/ final_state the last row only.
/ @private
.qetl.verify.prepared:{[rows;ks;basis]
    rest:(cols rows) except ks;
    rows:$[basis=`events; (ks,rest) xasc rows; rows];
    if[basis=`final_state;
        g:group ks#rows;
        rows:rows asc last each value g];
    ![rows;();ks!ks;(enlist `n)!enlist (til;(count;`i))]}

/ The differences between two producers' rows, paired by key.
/ @param a the reference producer's rows
/ @param b the other producer's rows
/ @param ks the business key columns, in both
/ @param skip columns to leave out of the comparison (what the destination generates)
/ @param basis `events, `revisions or `final_state
/ @return a table rowkey, kind, detail. kind is `missing (in a, not in b), `extra (in b,
/   not in a), `duplicated (the key is in both, a different number of times) or
/   `changed (a paired row's values differ - detail names each column, a then b)
/ @eg .qetl.verify.compare_rows[([] k:1 2j;v:1 2j);([] k:1 3j;v:1 2j);enlist `k;`symbol$();`events]`kind  ->  `missing`extra
.qetl.verify.compare_rows:{[a;b;ks;skip;basis]
    common:((cols a) inter cols b) except skip;
    a:common#a; b:common#b;
    pa:.qetl.verify.prepared[a;ks;basis];
    pb:.qetl.verify.prepared[b;ks;basis];
    ka:distinct ks#a; kb:distinct ks#b;
    out:.qetl.verify.no_differences[];
    miss:ka where not ka in kb;
    xtra:kb where not kb in ka;
    out,:([] rowkey:.qetl.verify.key_text[ks] each miss; kind:(count miss)#`missing; detail:(count miss)#enlist "in the first producer only");
    out,:([] rowkey:.qetl.verify.key_text[ks] each xtra; kind:(count xtra)#`extra; detail:(count xtra)#enlist "in the second producer only");
    ca:?[a;();ks!ks;(enlist `c)!enlist (count;`i)];
    cb:?[b;();ks!ks;(enlist `c)!enlist (count;`i)];
    if[basis<>`final_state;
        cb2:(ks,`cb_c)#![0!cb;();0b;(enlist `cb_c)!enlist `c];
        dup:0!select from (ca ij ks xkey cb2) where c<>cb_c;
        out,:([] rowkey:.qetl.verify.key_text[ks] each dup; kind:(count dup)#`duplicated;
            detail:{"first producer ",string[x`c]," time(s), second ",string x`cb_c} each dup)];
    / paired rows: the same key at the same position within it
    rest:(cols pa) except ks,`n;
    if[0=count rest; :out];
    bn:`$"b_",/:string rest;
    bt:flip ((ks,`n)!pb ks,`n),bn!pb rest;
    j:pa ij (ks,`n) xkey bt;
    if[0=count j; :out];
    neq:{[j;c] not .qetl.verify.cells_equal[j c;j `$"b_",string c]}[j] each rest;
    bad:where any neq;
    detail:{[j;rest;neq;i]
        "; " sv {[j;i;c] string[c],": ",.qf.util.full[j[c] i]," -> ",.qf.util.full j[`$"b_",string c] i}[j;i] each rest where neq[;i]}[j;rest;neq] each bad;
    out,([] rowkey:.qetl.verify.key_text[ks] each j bad; kind:(count bad)#`changed; detail:detail)}

/ Private: a verdict that is not a verdict.
/ @private
.qetl.verify.unverified:{[reason] `status`reason`findings`basis!(`UNVERIFIED;reason;.qetl.verify.no_differences[];"")}

/ Do the producers of `dataset` return equivalent rows? Reads a capture, compares
/ nothing else, and writes nothing.
/ @param dataset the dataset
/ @param cap what .qetl.verify.capture returned
/ @return a dict of status (`PASS`FAIL`UNVERIFIED), reason (why UNVERIFIED, else ""),
/   findings (the differences, the producer that differs from the first named) and basis
/ @eg .qetl.verify.cross_mode[`demo_deals;.qetl.verify.capture[`demo_deals;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000]]`status  ->  `PASS
.qetl.verify.cross_mode:{[dataset;cap]
    p0:cap`producers;
    p:select from p0 where mode in `stream`bounded;
    names:p`producer;
    if[0=count names; :.qetl.verify.unverified "no producer that can be captured fills ",string dataset];
    if[1=count names; :.qetl.verify.unverified "one producer (",string[first names],") - there is nothing to compare it with"];
    basis:.qetl.verify.basis dataset;
    if[null basis; :.qetl.verify.unverified "equivalence basis undeclared - the dataset declares whether events, revisions or its final state must match (.qetl.plant.equivalence)"];
    got:{[c;n] first c n}[cap`captures] each names;
    errs:{x`error} each got;
    if[any 0<count each errs; :.qetl.verify.unverified "; " sv errs where 0<count each errs];
    if[any {x`limited} each got; :.qetl.verify.unverified "a capture stopped at the page limit, so the window was not read to its end"];
    rows:{[r] r`rows} each got;
    filled:{(98h=type x) and 0<count x} each rows;
    if[0=count where filled; :.qetl.verify.unverified "empty sample - no producer returned a row in the window"];
    base:rows first where filled;
    rows:{[b;r] $[98h=type r; r; 0#b]}[base] each rows;
    kc:{[x] asc (),x} each p`key_cols;
    if[0=count raze kc; :.qetl.verify.unverified "no row key declared by any producer or its source - rows cannot be paired"];
    if[1<count distinct kc; :.qetl.verify.unverified "producers declare different row keys - see the schema check"];
    ks:first kc;
    d:.qetl.verify.destination dataset;
    skip:$[(::)~d; `symbol$(); d`generated];
    if[count lost:ks where not all {[ks;r] ks in cols r}[ks] each rows; :.qetl.verify.unverified "key column ",(", " sv string lost)," is not returned by every producer"];
    cmp:{[ref;ks;skip;basis;n;r] update producer:n from .qetl.verify.compare_rows[ref;r;ks;skip;basis]}[first rows;ks;skip;basis];
    found:raze cmp'[1_ names;1_ rows];
    shared:((cols first rows) except skip);
    text:string[basis]," on ",(", " sv string ks),"; compared ",(", " sv string shared),
        $[count skip; "; left out, generated by the destination: ",", " sv string skip; ""],
        "; reference ",string first names;
    `status`reason`findings`basis!($[count found; `FAIL; `PASS];"";found;text)}

\d .
