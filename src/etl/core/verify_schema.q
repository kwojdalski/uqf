/ verify_schema.q - which producers fill a dataset, and whether each one's output
/ fits the destination's schema (.qetl.verify, #1851).
/ .
/ A dataset can be filled by more than one job: a streaming job that publishes it
/ and a bounded worker that backfills it. `uqs job check <dataset>` asks whether
/ they agree - with the destination and with each other - and this file is the
/ first half of that: discovery, and the schema.
/ .
/ NOTHING HERE IS A SECOND CONTRACT. A producer's declared output is what its
/ declaration already says: a bounded worker's `transform` output, a streaming
/ job's `transform` when it declares one. The destination is the plant's own
/ schema for the table (.qetl.plant), and the rows a producer actually returns
/ are held to it too, nested element types included (.qetl.plant.elements).
/ .
/ A producer that declares no output - a polling feed with no `transform` - has
/ nothing to compare until a sample of its rows is captured; the schema check
/ then reads UNVERIFIED rather than PASS, because nothing was compared.
/ .
/ WHAT THE DESTINATION GENERATES is derived, not listed: the columns of the
/ plant's schema that its published shape lacks (`time`, which the tickerplant
/ stamps). Cross-mode comparison leaves those out.

\d .

/ The three verdicts a check can reach. UNVERIFIED is not a soft PASS: it says the
/ check did not establish the property, for a reason the report names.
.qetl.verify.statuses:`PASS`FAIL`UNVERIFIED

/ q type characters, spelled out for a finding's expected/actual.
.qetl.verify.type_names:(" bgxhijefcspmdznuvt")!("mixed list";"boolean";"guid";"byte";"short";"int";"long";"real";"float";"char";"symbol";"timestamp";"month";"date";"datetime";"timespan";"minute";"second";"time")

/ A type character as a name, with the character kept for the reader who searches by it.
/ @param c a q type character
/ @return e.g. "long (j)"
/ @eg .qetl.verify.type_name "j"  ->  "long (j)"
.qetl.verify.type_name:{[c] $[c in key .qetl.verify.type_names; .qetl.verify.type_names[c]," (",(1#c),")"; "type ",1#c]}

/ The empty findings table every check returns, so one shape reaches the report.
/ @return a table producer, destination, column, problem, expected, actual, basis
/ @eg count .qetl.verify.no_findings[]  ->  0
.qetl.verify.no_findings:{[]
    ([] producer:`symbol$(); destination:`symbol$(); column:`symbol$(); problem:`symbol$(); expected:(); actual:(); basis:`symbol$())}

/ One finding row.
/ @param producer the job whose output is wrong
/ @param destination the dataset it fills
/ @param column the column, or null for a finding about the whole table
/ @param problem one of `missing`extra`order`type`nested`key
/ @param expected what the destination takes, as text
/ @param actual what the producer has, as text
/ @param basis `declared (its declaration) or `sample (rows it returned)
/ @return a one-row findings table
/ @eg count .qetl.verify.finding[`a;`b;`c;`type;"x";"y";`declared]  ->  1
.qetl.verify.finding:{[producer;destination;column;problem;expected;actual;basis]
    ([] producer:enlist producer; destination:enlist destination; column:enlist column;
        problem:enlist problem; expected:enlist expected; actual:enlist actual; basis:enlist basis)}

/ ------------------------------------------------------------ EQUIVALENCE BASIS

/ What "the same rows" means for a dataset, as its plant table declares it
/ (.qetl.plant.equivalence, src/etl/plant_tables.q): `events, `revisions or
/ `final_state - or null when it declares none, which makes cross-mode equivalence
/ UNVERIFIED rather than a guess.
/ @param dataset the dataset, a symbol
/ @return the basis, or `
/ @eg .qetl.verify.basis `demo_deals  ->  `events
.qetl.verify.basis:{[dataset] $[dataset in key .qetl.plant.equivalence_of; .qetl.plant.equivalence_of dataset; `]}

/ ------------------------------------------------------------ DISCOVERY

/ Private: the key columns a producer holds its rows to: its own declaration, else
/ its source's row_key.
/ @private
.qetl.verify.key_of:{[own;source]
    k:(),own;
    if[count k; :k];
    $[(not null source) and source in key .qetl.source.sources; (),(.qetl.source.def source)`row_key; `symbol$()]}

/ Every producer that fills `dataset`, as the job graph says: .qetl.dag.producers, built
/ from the streaming jobs' `outputs`, the bounded workers' `outputs` and the reactions'
/ outputs. Nothing here groups producers a second time. The graph is rebuilt from the
/ registries for the call and put back as it was, so asking changes nothing.
/ @param dataset the dataset, a symbol
/ @return a table producer, mode (`stream, `bounded or `reaction), source, key_cols (the
/   row key, from the producer or else its source; read it as `(),`, since a table
/   column of one-symbol lists comes back as atoms), declared (the output schema its
/   declaration states, or (::))
/ @eg .qetl.verify.producers[`demo_deals]`mode  ->  `stream`bounded
.qetl.verify.producers:{[dataset]
    keep:.qetl.dag.jobs;
    .qetl.dag.adopt_all[];
    names:.qetl.dag.producers dataset;
    .qetl.dag.jobs::keep;
    streams:names inter .qetl.job.stream.defined[];
    workers:names inter .qetl.job.bounded.defined[];
    others:names except streams,workers;
    srow:{[j]
        d:.qetl.job.stream.def j;
        p:$[`poll in key d; d`poll; ()!()];
        src:$[`source in key p; p`source; `];
        `producer`mode`source`key_cols`declared!(j;`stream;src;.qetl.verify.key_of[$[`row_key in key p; p`row_key; `symbol$()];src];
            $[null d`transform; (::); .qetl.transform.output_schema d`transform])};
    brow:{[w]
        c:.qetl.job.bounded.def w;
        `producer`mode`source`key_cols`declared!(w;`bounded;c`source;.qetl.verify.key_of[c`target_key;c`source];
            .qetl.transform.output_schema c`transform)};
    orow:{[j] `producer`mode`source`key_cols`declared!(j;`reaction;`;`symbol$();(::))};
    rows:(srow each streams),(brow each workers),orow each others;
    flip `producer`mode`source`key_cols`declared!(
        `symbol$rows@\:`producer; `symbol$rows@\:`mode; `symbol$rows@\:`source; rows@\:`key_cols; rows@\:`declared)}

/ The destination a dataset lands in: the plant's table of that name.
/ @param dataset the dataset, a symbol
/ @return a dict published (the empty table a producer fills), generated (the
/   columns the destination stamps itself) and schema; or (::) when the plant
/   carries no such table, so there is nothing to hold a producer to
/ @eg .qetl.verify.destination[`demo_deals]`generated  ->  enlist `time
.qetl.verify.destination:{[dataset]
    if[not dataset in .qetl.plant.names[]; :(::)];
    s:.qetl.plant.shape dataset;
    p:.qetl.plant.published dataset;
    `schema`published`generated!(s;p;(cols s) except cols p)}

/ ------------------------------------------------------------ SCHEMA

/ Private: the declared element type of a nested column, or " " when any value goes.
/ @private
.qetl.verify.element_of:{[dataset;c]
    e:exec element from .qetl.plant.elements where table=dataset, column=c;
    $[count e; first e; " "]}

/ Private: the nested columns' rows that hold the wrong element type.
/ @private
.qetl.verify.nested_findings:{[producer;dataset;rows;c;basis]
    e:.qetl.verify.element_of[dataset;c];
    if[(e=" ") or 0=count rows; :.qetl.verify.no_findings[]];
    bad:where not (.Q.t?lower e)=type each rows c;
    if[0=count bad; :.qetl.verify.no_findings[]];
    .qetl.verify.finding[producer;dataset;c;`nested;
        "every row a ",.qetl.verify.type_name[lower e]," list";
        "row ",string[first bad]," holds type ",string[type (rows c) first bad],"h",
            $[1<count bad; " (and ",string[count[bad]-1]," more rows)"; ""];basis]}

/ What is wrong with one producer's output, as findings: columns missing or extra,
/ the order they come in, each column's q type, and a nested column's element type
/ in every row of a sample.
/ @param producer the producer's name
/ @param dataset the destination
/ @param output what the producer outputs: its declared (empty) table or the rows it returned
/ @param basis `declared or `sample
/ @return a findings table, empty when the output fits the destination
/ @throws error when the plant carries no table named `dataset`
/ @eg count .qetl.verify.schema_findings[`p;`demo_deals;.qetl.plant.published `demo_deals;`declared]  ->  0
.qetl.verify.schema_findings:{[producer;dataset;output;basis]
    want:.qetl.plant.published dataset;
    got:0!output;
    wt:exec c!t from meta want;
    gt:exec c!t from meta got;
    missing:(cols want) except cols got;
    extra:(cols got) except cols want;
    out:.qetl.verify.no_findings[];
    out,:raze {[p;d;wt;b;c] .qetl.verify.finding[p;d;c;`missing;.qetl.verify.type_name[wt c];"column absent";b]}[producer;dataset;wt;basis] each missing;
    out,:raze {[p;d;gt;b;c] .qetl.verify.finding[p;d;c;`extra;"column absent";.qetl.verify.type_name[gt c];b]}[producer;dataset;gt;basis] each extra;
    common:(cols want) inter cols got;
    / the same columns in a different order: a positional publish would land each
    / value in its neighbour's column
    if[(not count[missing]|count extra) and not (cols want)~cols got;
        out,:raze {[p;d;w;g;b;c] .qetl.verify.finding[p;d;c;`order;
            "position ",string w?c;"position ",string g?c;b]}[producer;dataset;cols want;cols got;basis] each common where (cols want)<>cols got];
    typed:common where (not wt[common]=" ") & wt[common]<>gt common;
    out,:raze {[p;d;wt;gt;b;c] .qetl.verify.finding[p;d;c;`type;.qetl.verify.type_name[wt c];.qetl.verify.type_name[gt c];b]}[producer;dataset;wt;gt;basis] each typed;
    out,:raze .qetl.verify.nested_findings[producer;dataset;got;;basis] each common where wt[common]=" ";
    out}

/ The row-key findings: a key column the destination does not take, and producers
/ that declare different keys for one dataset.
/ @param dataset the destination
/ @param producers .qetl.verify.producers' table
/ @return a findings table
/ @eg count .qetl.verify.key_findings[`demo_deals;.qetl.verify.producers`demo_deals]  ->  0
.qetl.verify.key_findings:{[dataset;producers]
    want:cols .qetl.plant.published dataset;
    out:.qetl.verify.no_findings[];
    out,:raze {[w;d;pr;i]
        k:(),(pr`key_cols) i;
        bad:k where not k in w;
        $[count bad;
            raze {[d;p;k] .qetl.verify.finding[p;d;k;`key;"a key column the destination takes";"not a destination column";`declared]}[d;(pr`producer) i] each bad;
            .qetl.verify.no_findings[]]}[want;dataset;producers] each til count producers;
    / (), because a table column of one-symbol lists comes back as atoms
    kc:(),/:producers`key_cols;
    ks:{asc x} each kc where 0<count each kc;
    if[1<count distinct ks;
        out,:.qetl.verify.finding[`;dataset;`;`key;"one row key for the dataset";
            "; " sv {string[x]," keys on ",", " sv string y}'[producers[`producer] where 0<count each kc;ks];`declared]];
    out}

\d .
