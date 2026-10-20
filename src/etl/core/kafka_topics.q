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
/ table from the payload; the transform's output is rows for a declared plant
/ table. tests/q/test_transform.q verifies the transform's examples on every
/ run, so a mapping is tested before a topic ever points at it.
/ .
/   define[topic; `mapping`output!(transform; plant table)]  and optionally
/   `format (default `json). A new wire format is a function in
/   .qetl.kafka.decoders - Avro or protobuf would register one there - and
/   the mapping above it does not change.
/ .
/ THE OUTPUT MUST CARRY topic, partition AND offset. They are how the
/ deduplication survives a restart: kafka_flow rebuilds what it has
/ processed from the rows it already published, and a table that dropped the
/ coordinates could not say which records those were. It is also what lets a
/ desk point at any output row and name the exact Kafka record it came from.
/ Refused at define, not discovered at the first restart.
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

/ The four columns every mapping's input starts with, and their meta types.
.qetl.kafka.coordinates:`topic`partition`offset`broker_time
.qetl.kafka.coordinate_types:"sjjp"

/ The meta types a payload field may be declared as: float, long, symbol,
/ timestamp, boolean.
.qetl.kafka.field_types:"fjspb"

/ The first whole number a JSON (float) number cannot be told from its neighbour: 2^53 and 2^53+1 read as the same float, so only values below it are exact.
.qetl.kafka.exact_limit:9007199254740992f

/ topic -> its declaration. Keyed, so one row per topic and a second define
/ of a topic replaces it.
.qetl.kafka.topic.registry:([topic:`symbol$()] mapping:`symbol$(); output:`symbol$(); format:`symbol$())

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
        'who,": transform ",string[mapping],"'s fields ",(", " sv string bad)," must be float, long, symbol, timestamp or boolean"];
    `input`schema!(first key d`inputs;fields!meta_t fields)}

/ Declare how one topic's records become rows of a plant table.
/ @param topic the Kafka topic name, as a symbol
/ @param decl dict of mapping (a registered transform) and output (a plant table), and optionally format (default `json)
/ @return the topic
/ @throws error naming what is wrong: an unregistered mapping, an input that is not coordinates then typed fields, an output that is not the plant table's shape or lacks topic/partition/offset, an unknown format
/ @eg decl:`mapping`output!(`kafka_client_flow;`client_flow); .qetl.kafka.topic.define[`uqf.client.flow;decl]
.qetl.kafka.topic.define:{[topic;decl]
    who:"define: kafka topic ",string topic;
    if[not -11h=type topic; '"define: a kafka topic is named by a symbol"];
    if[not 99h=type decl; 'who,"'s declaration must be a dictionary"];
    if[count miss:`mapping`output except key decl; 'who," is missing ",", " sv string miss];
    fmt:$[`format in key decl; decl`format; `json];
    if[not fmt in key .qetl.kafka.decoders;
        'who," declares format ",string[fmt],", which has no decoder - known: ",", " sv string key .qetl.kafka.decoders];
    if[not (decl`mapping) in .qetl.transform.defined[];
        'who," maps through transform ",string[decl`mapping],", which is not registered - a mapping is a declared transform, so its examples are verified"];
    .qetl.kafka.topic.input_of[who;decl`mapping];
    out:decl`output;
    if[not out in .qetl.plant.names[];
        'who," publishes to ",string[out],", which the plant does not define"];
    if[count lost:`topic`partition`offset except cols .qetl.plant.published out;
        'who,": ",string[out]," carries no ",(", " sv string lost)," - an output must carry the record's coordinates, or a restart cannot tell which records it already handled"];
    p:.qetl.transform.problems[.qetl.plant.published out;(.qetl.transform.def decl`mapping)`output;1b];
    if[count p; 'who,": transform ",string[decl`mapping]," does not produce ",string[out],": ","; " sv p];
    `.qetl.kafka.topic.registry upsert (topic;decl`mapping;out;fmt);
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
