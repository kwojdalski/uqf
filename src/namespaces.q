/ namespaces.q - which namespaces this tree owns, and how they nest (.qns).
/ .
/ The naming convention as code. The rule used to be "every namespace is a
/ flat .q<abbrev>",
/ and four tools each carried their own copy of the enumeration that rule
/ implied - `(key `) where like "q*"` - in the contract surface export, the
/ coverage driver, the documentation-coverage ratchet and the reseeder.
/ Framework modules nest under .qetl; concrete sources, shared transforms
/ and jobs nest under .qpipe. A flat scan sees only those two containers
/ and silently drops the modules and instances from every tool.
/ One enumeration here, that the tools share, is what stops that: a tool
/ that lists namespaces asks this file rather than assuming the shape.
/ .
/ A namespace is a dictionary whose keys include the empty symbol - `\d`
/ creates that back-reference, and it is what distinguishes .qpipe.job (a
/ namespace holding namespaces) from .qetl.job.bounded.worker_cfg (a dictionary holding
/ configs). The root is the one exception: it has nothing to point back to,
/ so is_namespace answers 0b for it. No walk here starts at root.

\d .qns

/ Is this value a namespace, as `\d` creates them?
/ .
/ The key type is checked BEFORE looking for the empty symbol. A dictionary
/ is 99h whatever its keys are, and .qetl.source.coercers is keyed by type CHARS -
/ so `` ` in key v `` on it compares a symbol against a char vector and
/ throws a bare 'type from inside a scan whose caller is nowhere near it.
/ A keyed table is 99h too, and its `key` is a table.
/ @param v any value
/ @return 1b for a namespace dictionary, 0b for anything else
/ @eg .qns.is_namespace value `.qetl.job.bounded  ->  1b
/ @eg .qns.is_namespace .qetl.job.bounded.worker_cfg  ->  0b
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
/ qualified and in depth-first order: `.qetl.job.bounded, `.qpipe.job, `.qpipe.job.demo_deals_backfill.
/ .
/ Ownership is the `.q` prefix the convention ties to filenames, minus q's own `.q`
/ (KX's, 180-odd names that would swamp any listing). Test scaffolding such
/ as .qunit is owned here in the sense that matters - it is loaded in this
/ process and prefixed the same way - so callers that want the LIBRARY drop
/ it themselves, as the documentation ratchet does; this function reports
/ what exists, not what should be documented.
/ @return symbol list of namespace names, each with its leading dot
/ @eg `.qetl.job.bounded in .qns.owned[]  ->  1b
/ @eg `.qpipe.job.demo_deals_backfill in .qns.owned[]  ->  1b
owned:{[]
    top:(key `) where (string key `) like "q*";
    top:asc top except `q;
    raze descend each ` sv/: `,'top}

/ The namespaces that hold FUNCTIONS: owned[] minus the containers, which is
/ what a coverage tool, a contract export or a documentation scan wants.
/ .qpipe.job holds only its workers, so it is a container; a namespace holding
/ both is reported, and its nested children are reported separately.
/ @return symbol list, a subset of owned[]
/ @eg `.qpipe.job in .qns.functional[]  ->  0b
/ @eg `.qpipe.job.demo_deals_backfill in .qns.functional[]  ->  1b
functional:{[]
    nss:owned[];
    nss where {[ns]
        d:value ns;
        ks:(key d) except `;
        any {[ns;k] 100h=type @[value;` sv ns,k;{[e] (::)}]}[ns] each ks} each nss}

\d .
