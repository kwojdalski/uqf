/ namespaces.q - which namespaces this tree owns, and how they nest (.qns).
/ .
/ N-01 as code. The rule used to be "every namespace is a flat .q<abbrev>",
/ and four tools each carried their own copy of the enumeration that rule
/ implied - `(key `) where like "q*"` - in the contract surface export, the
/ coverage driver, the documentation-coverage ratchet and the reseeder.
/ The ETL tree's instances now nest - workers under .qwrk
/ (.qwrk.demo_deals_backfill, see .qbw.worker_root) and sources under .qfeed
/ (.qfeed.demo_deals) - so a flat scan sees `qwrk` and `qfeed` as two
/ namespaces holding no functions and silently drops every worker and every
/ source from every one of those tools.
/ One enumeration here, that the tools share, is what stops that: a tool
/ that lists namespaces asks this file rather than assuming the shape.
/ .
/ A namespace is a dictionary whose keys include the empty symbol - `\d`
/ creates that back-reference, and it is what distinguishes .qwrk (a
/ namespace holding namespaces) from .qbw.worker_cfg (a dictionary holding
/ configs). The root is the one exception: it has nothing to point back to,
/ so is_namespace answers 0b for it. No walk here starts at root.

\d .qns

/ Is this value a namespace, as `\d` creates them?
/ .
/ The key type is checked BEFORE looking for the empty symbol. A dictionary
/ is 99h whatever its keys are, and .qsrc.coercers is keyed by type CHARS -
/ so `` ` in key v `` on it compares a symbol against a char vector and
/ throws a bare 'type from inside a scan whose caller is nowhere near it.
/ A keyed table is 99h too, and its `key` is a table.
/ @param v any value
/ @return 1b for a namespace dictionary, 0b for anything else
/ @eg .qns.is_namespace value `.qbw  ->  1b
/ @eg .qns.is_namespace .qbw.worker_cfg  ->  0b
is_namespace:{[v]
    if[not 99h=type v; :0b];
    if[not 11h=type key v; :0b];
    ` in key v}

/ Private: the namespaces DIRECTLY under one, fully qualified.
children:{[ns]
    d:@[value;ns;{[e] (::)}];
    if[not is_namespace d; :`symbol$()];
    ks:(key d) except `;
    ks:ks where {[ns;k] is_namespace @[value;` sv ns,k;{[e] (::)}]}[ns] each ks;
    ` sv/: ns,/:ks}

/ Private: a namespace and, recursively, everything nested under it.
/ Depth-first, the parent before its children.
descend:{[ns] ns,raze descend each children ns}

/ Every namespace this tree owns, leaves and containers alike, fully
/ qualified and in depth-first order: `.qbw, `.qwrk, `.qwrk.demo_deals_backfill.
/ .
/ Ownership is the `.q` prefix N-01 ties to filenames, minus q's own `.q`
/ (KX's, 180-odd names that would swamp any listing). Test scaffolding such
/ as .qunit is owned here in the sense that matters - it is loaded in this
/ process and prefixed the same way - so callers that want the LIBRARY drop
/ it themselves, as the documentation ratchet does; this function reports
/ what exists, not what should be documented.
/ @return symbol list of namespace names, each with its leading dot
/ @eg `.qbw in .qns.owned[]  ->  1b
/ @eg `.qwrk.demo_deals_backfill in .qns.owned[]  ->  1b
owned:{[]
    top:(key `) where (string key `) like "q*";
    top:asc top except `q;
    raze descend each ` sv/: `,'top}

/ The namespaces that hold FUNCTIONS: owned[] minus the containers, which is
/ what a coverage tool, a contract export or a documentation scan wants.
/ .qwrk holds only its workers, so it is a container; a namespace holding
/ both is reported, and its nested children are reported separately.
/ @return symbol list, a subset of owned[]
/ @eg `.qwrk in .qns.functional[]  ->  0b
/ @eg `.qwrk.demo_deals_backfill in .qns.functional[]  ->  1b
functional:{[]
    nss:owned[];
    nss where {[ns]
        d:value ns;
        ks:(key d) except `;
        any {[ns;k] 100h=type @[value;` sv ns,k;{[e] (::)}]}[ns] each ks} each nss}

\d .
