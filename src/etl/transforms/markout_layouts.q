/ markout_layouts.q - the three layouts of a markout dataset (.qpipe.transform.markout_layouts, #1932).
/ .
/ A markout dataset is stored LONG: a row per (execution, horizon, component).
/ Two other layouts are read from it and are never recalculated or stored:
/ .
/   wide          a row per (execution, horizon). The base component (`total`)
/                 keeps its measure columns' names, as the contract's Q2 says;
/                 every other component's measures are prefixed with the
/                 component, so a leg's pips is leg1_pips.
/   nested wide   a row per execution. The columns that are one value per
/                 execution stay atoms; every other wide column is a list with
/                 one item per horizon, in horizon order, so the lists are
/                 aligned by position.
/ .
/ The layouts know nothing of FX. A domain hands over a LAYOUT SPEC, a dictionary:
/ .
/   columns     the long dataset's columns, in order (kind and step among them)
/   execution   the columns that identify an execution
/   horizon     the horizon column
/   shared      the columns that are per (execution, horizon) and repeat on each
/               component row
/   own         the columns that belong to a component row: its measures
/   base        the kind whose measures are unprefixed in a wide row
/   components  a table of kind, step: every other component, in wide order
/   present     the own column that is null exactly when a component is absent
/   per_execution  the shared columns that are one value per execution
/ .
/ Component presence is read from the `present` column, not stored, so
/ wide -> long returns exactly the rows that went in. A spec that does not
/ classify every column once, or data that carries a component the spec does not
/ declare, is refused: a layout that silently dropped a column or a leg would
/ break the reconciliation the long form exists to hold.
/ .
/ Row order is part of the contract: executions in order of first appearance,
/ horizons ascending within one, components in spec order within a horizon.

\d .

/ The columns a long dataset carries for its key beyond the execution and horizon.
.qpipe.transform.markout_layouts.long_keys:`kind`step

/ The wide column a component's own column becomes: the base keeps the name, any other
/ component prefixes it with kind (and step when it has one), e.g. leg1_pips.
/ @private
.qpipe.transform.markout_layouts.wide_name:{[spec;knd;stp;col]
    if[knd=spec`base; :col];
    $[stp=0; `$(string knd),"_",string col; `$(string knd),(string stp),"_",string col]}

/ Refuse a spec that does not classify each long column exactly once.
/ @param spec the layout spec
/ @return the spec
/ @throws error naming the missing key, the unclassified or doubly classified columns, or a wide name collision
/ @eg .qpipe.transform.markout_layouts.require_spec[.qpipe.transform.markout_layouts.sample_spec[]]`base  ->  `total
.qpipe.transform.markout_layouts.require_spec:{[spec]
    if[not 99h=type spec; '"markout_layouts: spec must be a dictionary"];
    miss:`columns`execution`horizon`shared`own`base`components`present`per_execution except key spec;
    if[count miss; '"markout_layouts: spec is missing ",", " sv string miss];
    classes:(spec`execution),(enlist spec`horizon),(spec`shared),(spec`own),.qpipe.transform.markout_layouts.long_keys;
    g:group classes;
    if[count d:where 1<count each g;
        '"markout_layouts: columns classified more than once: ",", " sv string d];
    if[count u:(spec`columns) except classes; '"markout_layouts: columns not classified: ",", " sv string u];
    if[count u:classes except spec`columns; '"markout_layouts: classified but not in columns: ",", " sv string u];
    if[not (spec`present) in spec`own; '"markout_layouts: present must be an own column"];
    if[not all (spec`per_execution) in spec`shared; '"markout_layouts: per_execution must be shared columns"];
    c:spec`components;
    if[not (cols c)~`kind`step; '"markout_layouts: components must be a table of kind, step"];
    if[any (c`kind)=spec`base; '"markout_layouts: components must not repeat the base kind"];
    if[(count c)<>count distinct c; '"markout_layouts: components must be distinct"];
    names:raze .qpipe.transform.markout_layouts.component_names[spec] each til count c;
    all_names:(spec`execution),(enlist spec`horizon),(spec`shared),(spec`own),names;
    g:group all_names;
    if[count d:where 1<count each g;
        '"markout_layouts: wide names collide: ",", " sv string d];
    spec}

/ The wide column names of one declared component, by its position in the spec.
/ @private
.qpipe.transform.markout_layouts.component_names:{[spec;i]
    c:(spec`components) i;
    .qpipe.transform.markout_layouts.wide_name[spec;c`kind;c`step;] each spec`own}

/ All components in wide order, the base first, as kind, step.
/ @private
.qpipe.transform.markout_layouts.all_components:{[spec]
    ([] kind:enlist[spec`base],(spec`components)`kind; step:0,(spec`components)`step)}

/ Refuse a long dataset the layouts cannot honour: wrong columns, a component the spec
/ does not declare, a duplicate key, a shared column that varies within one
/ (execution, horizon), or a (execution, horizon) without its base row.
/ @private
.qpipe.transform.markout_layouts.require_long:{[spec;lng]
    if[not 98h=type lng; '"markout_layouts: long must be a table"];
    if[not (asc cols lng)~asc spec`columns; '"markout_layouts: long has columns ",(", " sv string cols lng)," but the spec has ",", " sv string spec`columns];
    id:(spec`execution),spec`horizon;
    k:id,.qpipe.transform.markout_layouts.long_keys;
    if[(count lng)<>count distinct k#lng; '"markout_layouts: duplicate (execution, horizon, kind, step) in long"];
    got:distinct select kind, step from lng;
    if[count und:got except .qpipe.transform.markout_layouts.all_components[spec];
        '"markout_layouts: components not declared by the spec: ",", " sv {(string x`kind),"/",string x`step} each und];
    n:count distinct id#lng;
    if[(count distinct (id,spec`shared)#lng)<>n;
        '"markout_layouts: a shared column varies within one (execution, horizon)"];
    if[n<>count select from lng where kind=spec`base;
        '"markout_layouts: an (execution, horizon) has no ",(string spec`base)," row"];
    lng}

/ The wide layout of a long dataset: a row per (execution, horizon).
/ @param spec the layout spec
/ @param lng the long rows
/ @return wide rows: execution, horizon, shared, the base's own columns, then each declared component's own columns prefixed
/ @throws error when the spec or the data break the layout's rules
/ @eg count .qpipe.transform.markout_layouts.wide[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.sample_long[]]  ->  2
.qpipe.transform.markout_layouts.wide:{[spec;lng]
    .qpipe.transform.markout_layouts.require_spec spec;
    .qpipe.transform.markout_layouts.require_long[spec;lng];
    id:(spec`execution),spec`horizon;
    want:id,(spec`shared),spec`own;
    base:?[lng;enlist (=;`kind;enlist spec`base);0b;want!want];
    / executions in first-appearance order, horizons ascending within one
    rows:flip value flip (spec`execution)#lng;
    base_rows:flip value flip (spec`execution)#base;
    / Sorting metadata is separate: every payload column belongs to the caller.
    ordering:([] execution:(distinct rows)?base_rows; horizon:base spec`horizon; idx:til count base);
    ordering:`execution`horizon xasc ordering;
    j:id xkey base ordering`idx;
    c:spec`components;
    n:0;
    while[n<count c;
        t:?[lng;((=;`kind;enlist c[n;`kind]);(=;`step;c[n;`step]));0b;(id,spec`own)!id,spec`own];
        t:id xkey (id,.qpipe.transform.markout_layouts.component_names[spec;n]) xcol t;
        j:j lj t;
        n+:1];
    0!j}

/ The long rows of a wide dataset: the inverse of wide, with a wide row's components
/ in spec order. A component is present when its `present` column is not null.
/ @param spec the layout spec
/ @param wde the wide rows
/ @return long rows in the spec's column order
/ @throws error when the spec is wrong or the wide rows lack a column
/ @eg count .qpipe.transform.markout_layouts.long[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.wide[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.sample_long[]]]  ->  4
.qpipe.transform.markout_layouts.long:{[spec;wde]
    .qpipe.transform.markout_layouts.require_spec spec;
    if[not 98h=type wde; '"markout_layouts: wide must be a table"];
    keep:(spec`execution),(spec`horizon),spec`shared;
    comps:.qpipe.transform.markout_layouts.all_components[spec];
    parts:(); row_ids:`long$(); component_ids:`long$();
    n:0;
    while[n<count comps;
        $[n=0; wn:spec`own; wn:.qpipe.transform.markout_layouts.component_names[spec;n-1]];
        if[count m:(keep,wn) except cols wde; '"markout_layouts: wide has no column ",", " sv string m];
        p:(keep,wn)#wde;
        p:(keep,spec`own) xcol p;
        p:update kind:comps[n;`kind], step:comps[n;`step] from p;
        selected:where not null p spec`present;
        parts,:enlist p selected;
        row_ids,:selected;
        component_ids,:(count selected)#n;
        n+:1];
    ordering:`row`component xasc ([] row:row_ids; component:component_ids; idx:til count row_ids);
    r:(raze parts) ordering`idx;
    (spec`columns)#r}

/ The nested-wide layout of a long dataset: a row per execution. The per-execution
/ columns are atoms; every other wide column is a list with one item per horizon,
/ in horizon order, so horizons, measures, legs and statuses are aligned vectors.
/ @param spec the layout spec
/ @param lng the long rows
/ @return one row per execution
/ @throws error when the spec or data break the layout's rules, or a per-execution column varies
/ @eg count .qpipe.transform.markout_layouts.nested[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.sample_long[]]  ->  1
.qpipe.transform.markout_layouts.nested:{[spec;lng]
    w:.qpipe.transform.markout_layouts.wide[spec;lng];
    ex:spec`execution;
    pe:spec`per_execution;
    if[(count distinct (ex,pe)#w)<>count distinct ex#w;
        '"markout_layouts: a per-execution column varies across horizons"];
    cs:(cols w) except ex;
    agg:cs!{$[x in y; (first;x); x]}[;pe] each cs;
    n:0!?[w;();ex!ex;agg];
    / qSQL groups sort their keys; restore the wide rows' first appearance.
    n (ex#n)?distinct ex#w}

/ The wide rows of a nested-wide dataset: the inverse of nested.
/ @param nst the nested rows
/ @return wide rows, one per (execution, horizon)
/ @throws error when the list columns are not all the same length within a row
/ @eg count .qpipe.transform.markout_layouts.unnest[.qpipe.transform.markout_layouts.nested[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.sample_long[]]]  ->  2
.qpipe.transform.markout_layouts.unnest:{[nst]
    ungroup nst}

/ Canonical row order of a long dataset: executions in first appearance, horizons
/ ascending, components in spec order. What long returns from a wide.
/ @param spec the layout spec
/ @param lng the long rows
/ @return the long rows in canonical order
/ @eg count .qpipe.transform.markout_layouts.canonical[.qpipe.transform.markout_layouts.sample_spec[];.qpipe.transform.markout_layouts.sample_long[]]  ->  4
.qpipe.transform.markout_layouts.canonical:{[spec;lng]
    .qpipe.transform.markout_layouts.long[spec;.qpipe.transform.markout_layouts.wide[spec;lng]]}

/ The layout spec of a dataset written under the markout contract (markout_core.q):
/ the component's own columns are move, quote_amount, report_amount and status (the
/ contract's 14, 15, 22 and 23) plus the domain's; every other column is per
/ (execution, horizon). The execution is (venue, fill_id), the base is `total`,
/ and the columns one value per execution are the contract's execution facts
/ present in the dataset. A domain overrides any key with a dict join.
/ @param columns the long dataset's columns, in order
/ @param components a table of kind, step: the components besides `total`, in wide order
/ @param extra_own the domain's own columns, e.g. FX's leg, invert, price_t0, price_t1, pips
/ @return the layout spec
/ @eg .qpipe.transform.markout_layouts.core_spec[`venue`fill_id`horizon`kind`step`move`status;([] kind:enlist `execution; step:enlist 0);`symbol$()]`base  ->  `total
.qpipe.transform.markout_layouts.core_spec:{[columns;components;extra_own]
    own:(`move`quote_amount`report_amount`status inter columns),extra_own;
    shared:columns except `venue`fill_id`horizon`kind`step,own;
    `columns`execution`horizon`shared`own`base`components`present`per_execution!(
        columns;`venue`fill_id;`horizon;shared;own;`total;components;`status;
        shared inter `source_time`clock`sym`side`size`price`quote_ccy`report_ccy)}

/ ----------------------------------------------------------- A WORKED SAMPLE
/ A domain-neutral spec: a `total` and a `part` per horizon, one measure.

/ @private
.qpipe.transform.markout_layouts.sample_spec:{[]
    `columns`execution`horizon`shared`own`base`components`present`per_execution!(
        `venue`fill_id`horizon`kind`step`sym`move`status;`venue`fill_id;`horizon;`sym;`move`status;`total;
        ([] kind:enlist `part; step:enlist 1); `status;enlist `sym)}

/ One execution, two horizons, a total and a part each.
/ @private
.qpipe.transform.markout_layouts.sample_long:{[]
    ([] venue:4#`a; fill_id:4#`x1; horizon:0D00:00:01 0D00:00:01 0D00:00:10 0D00:00:10; kind:4#`total`part;
        step:4#0 1; sym:4#`ABC; move:1.5 1 2.5 2f; status:4#`ok)}

\d .
