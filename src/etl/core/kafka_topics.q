/ kafka_topics.q - what a Kafka record becomes: the per-topic mapping registry
/ and the decoder (.qetl.kafka.topic), with no transport in sight.
/ .
/ TRANSPORT AND DECODING ARE TWO THINGS. Whatever holds the Kafka
/ subscription (the q feed, the legacy Python consumer, a test) turns a record
/ into one ENVELOPE row and publishes it, undecoded, on `kafka_message`:
/ topic, partition, offset, key, the broker's timestamp and the raw payload.
/ What the payload MEANS is decided here, downstream, per topic - so a second
/ topic with its own wire shape is a declaration, not a second consumer.
/ .
/ A TOPIC IS DECLARED LIKE A NORMALIZER SOURCE. One declared .qetl.transform
/ per topic, the mapping, reads ONE table: the record's four coordinates
/ (topic, partition, offset, broker_time) followed by the payload's fields,
/ each typed by the table the transform declares. The decoder fills that
/ table from the payload; the transform's output is the payload's columns only, and
/ the framework appends the provenance (below) to make rows of a declared plant
/ table. tests/q/test_transform.q verifies the transform's examples on every
/ run, so a mapping is tested before a topic ever points at it.
/ .
/   define[topic; `mapping`output!(transform; plant table)]  and optionally
/   `format (default `json). A new wire format is a function in
/   .qetl.kafka.decoders - Avro or protobuf would register one there - and
/   the mapping above it does not change.
/ .
/ A TOPIC BELONGS TO ONE DECODER JOB (#1481). Declare it with `job` - the
/ streaming job that decodes it - and put the declaration in that job's own
/ file: the topic's mapping then loads wherever the job does, which a
/ selective load (.qetl.load.only) relies on, and the load plan reads the same
/ line to write down which job owns which topic (.qetl.load.topics). A second
/ job declaring a topic is refused here, and at generation across processes.
/ kafka_decoder.q is what a job does with the topics it owns.
/ .
/ THE PROVENANCE CONTRACT (#1511). Every decoded output table carries the same
/ four columns, with the same types, whichever decoder fills it:
/ topic (symbol), partition (long), offset (long), broker_time (timestamp).
/ They are how the deduplication survives a restart - kafka_flow rebuilds what
/ it has processed from the rows it already published - and what lets a desk
/ name the exact Kafka record a row came from. broker_time is the Kafka
/ RECORD's timestamp, as the consumer read it; it is never a payload time, and
/ never stands in for one: a payload that has no time leaves the payload's own
/ column null. A mapping does not build these columns. It returns the payload's
/ columns only and the framework appends the four from the record
/ (.qetl.kafka.propagate, kafka_ingest.q), so no decoder can spell them
/ differently. A table that lacks one, or types it differently, is refused at
/ define, not discovered at the first restart.
/ .
/ ONE RECORD, MANY ROWS. A mapping may return several rows for one record if
/ its topic is declared `expand`. The output table then also carries
/ row_index (long): the row's position among the rows its record produced,
/ 0 for the first. The mapping returns a leading message_index column (which
/ record of the batch the row came from, non-decreasing) and the framework
/ numbers the rows. A one-to-one mapping returns message_index too, a
/ permutation of the batch's positions: provenance follows it, so a mapping
/ that sorts or groups its rows still gets each row's own record. The index depends only on the mapping's output for that
/ record, so a retry or a replay gives the same one. A record's rows are
/ published together and handled together: the deduplication keys on the
/ record's coordinates, and (coordinates, row_index) is a unique key of an
/ expanded row. A one-to-one table does not carry row_index.
/ .
/ KEYS AND HEADERS stay on the raw envelope (kafka_message.msg_key). Headers are
/ not carried: nothing in this tree reads one, and a column added to a live
/ table breaks its tickerplant log's replay (docs/services/kafka.md).
/ .
/ A RECORD IS NEVER DROPPED SILENTLY. process (kafka_ingest.q) sends a record
/ it cannot map - no declaration for its topic, an empty or undecodable
/ payload, a missing or mistyped field, a row the mapping refuses - to the
/ dead-letter table with the reason and the payload as received.
/ .
/ JSON NUMBERS ARE FLOATS. q reads every JSON number as a float, so an
/ integer above 2^53 would lose its low digits with nothing erroring. A long
/ field therefore refuses a number past 2^53 and accepts the same value sent
/ as a string, which is read exactly. Refusing is the point: a corrupted
/ trade id is worse than a dead-lettered record.

\d .

/ The provenance contract: the four columns every decoded output table carries
/ and every mapping's input starts with, and their meta types (see the header).
.qetl.kafka.provenance:`topic`partition`offset`broker_time!"sjjp"
.qetl.kafka.coordinates:key .qetl.kafka.provenance
.qetl.kafka.coordinate_types:value .qetl.kafka.provenance

/ The column an expanded output also carries: the row's position within its record.
.qetl.kafka.row_index:`row_index

/ The column an expanding mapping returns first: which record of the batch each row came from.
.qetl.kafka.message_index:`message_index

/ The meta types a payload field may be declared as: float, long, symbol,
/ timestamp, boolean; "F", a JSON array of numbers (a float list); and " ", a
/ JSON array of arrays of numbers (a list of float lists - q's meta type for a
/ column of nested lists). The two array types are what lets a record carry
/ [[price,size],...] without the mapping parsing JSON itself.
.qetl.kafka.field_types:"fjspbF "

/ The first whole number a JSON (float) number cannot be told from its neighbour: 2^53 and 2^53+1 read as the same float, so only values below it are exact.
.qetl.kafka.exact_limit:9007199254740992f

/ topic -> its declaration. Keyed, so one row per topic and a second define
/ of a topic replaces it. `owner` is the decoder job, null for a topic declared
/ without one (a test's, or a worked example read before there were decoders).
.qetl.kafka.topic.registry:([topic:`symbol$()] mapping:`symbol$(); output:`symbol$(); format:`symbol$(); owner:`symbol$(); expand:`boolean$())

/ ------------------------------------------------------------- THE DECODERS

/ Private: one JSON value as the declared type, or an error naming the field.
/ @param name the field
/ @param t its meta type: f j s p or b
/ @param v the value .j.k produced
/ @return the typed atom
/ @throws error naming the field and what was wrong with it
/ @private
.qetl.kafka.field:{[name;t;v]
    who:string[name],": ";
    / `and` over a string would be a list: test the type first, in a $[].
    num:$[-9h=type v; not null v; 0b];
    if[t="f";
        if[num; :v];
        'who,"expected a number"];
    if[t="j";
        if[num;
            if[(v=floor v) and (abs v)<.qetl.kafka.exact_limit; :"j"$v];
            'who,"expected a whole number below 2^53 - send larger identifiers as strings"];
        if[(10h=type v) and 0<count v;
            n:.qetl.coerce.to_long v;
            if[not null n; :n]];
        'who,"expected a whole number"];
    if[t="s";
        if[(10h=type v) and 0<count trim v; :.qetl.coerce.to_symbol_cased v];
        'who,"expected a non-empty string"];
    if[t="p";
        if[10h=type v;
            ts:.qetl.coerce.to_timestamp v;
            if[not null ts; :ts]];
        'who,"expected an ISO-8601 timestamp string"];
    if[t="b";
        if[-1h=type v; :v];
        'who,"expected true or false"];
    if[t="F";
        / .j.k reads [] as the empty general list and [1,2] as a float vector.
        if[(9h=type v) or (0h=type v) and 0=count v; :"f"$v];
        'who,"expected an array of numbers"];
    if[t=" ";
        if[(0h=type v) and all {(9h=type x) or 0=count x} each v; :{"f"$x} each v];
        'who,"expected an array of arrays of numbers"];
    'who,"has the undecodable declared type ",string t}

/ Decode a JSON object payload into the declared fields.
/ .
/ Extra fields in the payload are ignored - a producer adding one must not
/ dead-letter every record - and the raw payload is on kafka_message anyway.
/ A declared field that is absent, null or the wrong type is an error.
/ @param schema dict field -> meta type (f j s p b), in the mapping's order
/ @param payload the payload as a string
/ @return the field values, in the schema's order
/ @throws error saying what was wrong: empty, not JSON, not an object, a missing field, a mistyped field
/ @eg .qetl.kafka.decode_json[`px`sym!"fs"; "{\"px\":1.5,\"sym\":\"EURUSD\"}"]  ->  (1.5;`EURUSD)
.qetl.kafka.decode_json:{[schema;payload]
    if[0=count payload; '"empty payload"];
    j:@[.j.k;payload;{'"not valid JSON: ",x}];
    if[not 99h=type j; '"the payload is not a JSON object"];
    names:key schema;
    if[count miss:names except key j; '"missing field(s): ",", " sv string miss];
    .qetl.kafka.field'[names;value schema;j names]}

/ Wire format -> its decoder, [schema;payload] -> field values.
.qetl.kafka.decoders:(enlist `json)!enlist .qetl.kafka.decode_json

/ ----------------------------------------------------------------- DECLARING

/ Private: the mapping's input table, its payload fields and their types.
/ @param who the caller's name, for the message
/ @param mapping the transform's name
/ @return dict of `input` (the input's name) and `schema` (field -> meta type)
/ @throws error when the input is not coordinates followed by typed fields
/ @private
.qetl.kafka.topic.input_of:{[who;mapping]
    d:.qetl.transform.def mapping;
    if[1<>count d`inputs;
        'who,": transform ",string[mapping]," takes ",string[count d`inputs]," inputs - a mapping reads the one record"];
    tbl:first value d`inputs;
    meta_t:exec c!t from meta tbl;
    lead:count .qetl.kafka.coordinates;
    if[not (lead#cols tbl)~.qetl.kafka.coordinates;
        'who,": transform ",string[mapping],"'s input must begin with ",", " sv string .qetl.kafka.coordinates];
    if[not (meta_t .qetl.kafka.coordinates)~.qetl.kafka.coordinate_types;
        'who,": transform ",string[mapping],"'s coordinate columns must be symbol, long, long, timestamp"];
    fields:lead _ cols tbl;
    if[0=count fields; 'who,": transform ",string[mapping]," declares no payload fields"];
    if[count bad:fields where not meta_t[fields] in .qetl.kafka.field_types;
        'who,": transform ",string[mapping],"'s fields ",(", " sv string bad)," must be float, long, symbol, timestamp, boolean, a float list or a list of float lists"];
    `input`schema!(first key d`inputs;fields!meta_t fields)}

/ Private: refuse a topic another job already owns - and a define with no job over a topic that has one.
/ .
/ A define without `job` used to skip this check and upsert over the owner's
/ entry (#1522): the plan still routed the topic to the owner, which then
/ decoded it into the new output and threw. A topic with an owner is only ever
/ redefined by that owner.
/ @param who the caller's name, for the message
/ @param topic the topic
/ @param owner the job declaring it, null for none
/ @throws error naming both the owner and the job (or "no job") declaring it, from this process or from the generated plan
/ @private
.qetl.kafka.topic.require_free:{[who;topic;owner]
    claims:.qetl.kafka.topic.claims[];
    if[(topic in key claims) and not owner=claims topic;
        'who," is owned by ",string[claims topic],", not ",$[null owner; "a define with no job"; string owner]," - a topic is decoded by exactly one job"]}

/ The columns a mapping returns for an output table: the payload's, in the table's order - the declared output of a topic's mapping transform.
/ @param out the plant table
/ @return an empty table: message_index first, then the payload columns
/ @eg cols .qetl.kafka.topic.payload_schema[`client_flow]  ->  `message_index`sym`side`qty`price`client`trade_id
.qetl.kafka.topic.payload_schema:{[out]
    pub:.qetl.plant.published out;
    pay:(cols[pub] except .qetl.kafka.coordinates,.qetl.kafka.row_index)#pub;
    flip (enlist[.qetl.kafka.message_index],cols pay)!(enlist `long$()),value flip pay}

/ Private: refuse an output table that does not carry the provenance contract.
/ @param who the caller's name, for the message
/ @param out the plant table
/ @param expand 1b when the topic expands
/ @throws error naming the missing or mistyped columns, or a row_index the topic does not use
/ @private
.qetl.kafka.topic.require_provenance:{[who;out;expand]
    pub:.qetl.plant.published out;
    if[count lost:.qetl.kafka.coordinates except cols pub;
        'who,": ",string[out]," carries no ",(", " sv string lost)," - an output must carry the record's provenance (topic, partition, offset, broker_time), or a restart cannot tell which records it already handled"];
    t:exec c!t from meta pub;
    if[count bad:where not t[.qetl.kafka.coordinates]=.qetl.kafka.coordinate_types;
        bad:.qetl.kafka.coordinates bad;
        'who,": ",string[out],"'s ",", " sv {string[x]," must be a ",("sjp"!("symbol";"long";"timestamp")) .qetl.kafka.provenance x} each bad];
    ri:.qetl.kafka.row_index;
    if[expand and not ri in cols pub;
        'who,": ",string[out]," carries no ",string[ri]," - an expanding topic numbers the rows of a record"];
    if[expand and not "j"=t ri; 'who,": ",string[out],"'s ",string[ri]," must be a long"];
    if[(not expand) and ri in cols pub;
        'who,": ",string[out]," carries ",string[ri]," but the topic does not expand - declare `expand or drop the column"]}

/ Declare how one topic's records become rows of a plant table.
/ @param topic the Kafka topic name, as a symbol
/ @param decl dict of mapping (a registered transform) and output (a plant table), and optionally format (default `json), job (the decoder job that owns the topic) and expand (1b when a record may become several rows; the output then carries row_index)
/ @return the topic
/ @throws error naming what is wrong: an unregistered mapping, an input that is not coordinates then typed fields, an output that is not the plant table's shape or lacks or mistypes topic/partition/offset/broker_time, an unknown format, a topic another job already owns
/ @eg decl:`mapping`output`job!(`kafka_client_flow;`client_flow;`kafka_flow); .qetl.kafka.topic.define[`uqf.client.flow;decl]
.qetl.kafka.topic.define:{[topic;decl]
    who:"define: kafka topic ",string topic;
    if[not -11h=type topic; '"define: a kafka topic is named by a symbol"];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    if[count miss:`mapping`output except key decl; 'who," is missing ",", " sv string miss];
    fmt:$[`format in key decl; decl`format; `json];
    owner:$[`job in key decl; decl`job; `];
    if[not -11h=type owner; 'who,"'s job must be a symbol"];
    expand:$[`expand in key decl; decl`expand; 0b];
    if[not -1h=type expand; 'who,"'s expand must be a boolean"];
    .qetl.kafka.topic.require_free[who;topic;owner];
    if[not fmt in key .qetl.kafka.decoders;
        'who," declares format ",string[fmt],", which has no decoder - known: ",", " sv string key .qetl.kafka.decoders];
    if[not (decl`mapping) in .qetl.transform.defined[];
        'who," maps through transform ",string[decl`mapping],", which is not registered - a mapping is a declared transform, so its examples are verified"];
    .qetl.kafka.topic.input_of[who;decl`mapping];
    out:decl`output;
    if[not out in .qetl.plant.names[];
        'who," publishes to ",string[out],", which the plant does not define"];
    .qetl.kafka.topic.require_provenance[who;out;expand];
    p:.qetl.transform.problems[.qetl.kafka.topic.payload_schema[out];(.qetl.transform.def decl`mapping)`output;1b];
    if[count p; 'who,": transform ",string[decl`mapping]," does not return the payload columns of ",string[out],": ","; " sv p];
    `.qetl.kafka.topic.registry upsert (topic;decl`mapping;out;fmt;owner;expand);
    topic}

/ One topic's declaration, or a refusal naming it.
/ @param topic the topic
/ @return dict of mapping, output and format
/ @throws error naming the topic and the declared ones
/ @eg .qetl.kafka.topic.def[`uqf.client.flow]`output
.qetl.kafka.topic.def:{[topic]
    if[not topic in key .qetl.kafka.topic.registry;
        '"topic: ",string[topic]," has no declaration - declared: ",", " sv string key[.qetl.kafka.topic.registry]`topic];
    .qetl.kafka.topic.registry topic}

/ Every declared topic.
/ @return a symbol vector
/ @eg .qetl.kafka.topic.defined[]
.qetl.kafka.topic.defined:{[] (key .qetl.kafka.topic.registry)`topic}

/ The plant tables the declared topics publish to.
/ @return a symbol vector, each table once
/ @eg `client_flow in .qetl.kafka.topic.outputs[]
.qetl.kafka.topic.outputs:{[] distinct exec output from .qetl.kafka.topic.registry}

/ Which job decodes each topic: the generated plan's claims (every topic the
/ tree and its installed bundles declare, whichever process loads them), with
/ this process's own declarations over them.
/ .
/ The plan is what lets one process know another's topics: kafka_flow1 never
/ loads a sidecar's decoder, but must not dead-letter the sidecar's records.
/ @return dict topic -> the owning job
/ @eg .qetl.kafka.topic.claims[]`uqf.client.flow  ->  `kafka_flow
.qetl.kafka.topic.claims:{[]
    plan:$[`topics in key `.qetl.load; .qetl.load.topics; (`symbol$())!`symbol$()];
    r:exec topic!owner from .qetl.kafka.topic.registry where not null owner;
    plan,r}

/ The topics a job owns.
/ @param job the decoder job
/ @return a symbol vector
/ @eg .qetl.kafka.topic.owned `kafka_flow  ->  enlist `uqf.client.flow
.qetl.kafka.topic.owned:{[job] exec topic from .qetl.kafka.topic.registry where owner=job}

/ Every topic a consumer may subscribe to: those declared here and those the
/ plan says another process's decoder owns.
/ @return a symbol vector
/ @eg `uqf.client.flow in .qetl.kafka.topic.known[]
.qetl.kafka.topic.known:{[] distinct .qetl.kafka.topic.defined[],key .qetl.kafka.topic.claims[]}
