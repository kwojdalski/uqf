/ example_quotes_decoder.q - a Kafka decoder job a SIDECAR adds (.qpipe.job.example_quotes_decoder).
/ .
/ Everything a new topic needs, in this one file and the bundle's tables.q:
/ .
/   the payload's shape and its mapping   .qpipe.transform.example_quote_map
/   the topic, owned by this job           .qetl.kafka.topic.define
/   the output table's publish             publish_decoded (a literal branch)
/   the job, with its own process          .qetl.job.stream.define
/ .
/ The marks, the deduplication on (topic;partition;offset), the dead letters
/ and the restart replay are .qetl.kafka.decoder (src/etl/core/kafka_decoder.q);
/ nothing in the tree is edited. kafka_flow1 sees the topic in the generated
/ load plan and leaves its records to this job.
/ .
/ The payload is one JSON object per record:
/ .
/   {"sym":"EURUSD","bid":1.0850,"ask":1.0852}
/ .
/ A crossed quote (bid above ask) is refused by name and dead-lettered with
/ reason `mapping`.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

/ The record as the decoder hands it over: coordinates, then payload fields.
.qpipe.transform.example_quote_map.message:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$();
    sym:`symbol$(); bid:`float$(); ask:`float$())

/ The mapping: refuse a crossed quote, else reorder into example_quote's columns.
/ @param m the decoded records
/ @return example_quote's payload columns (the framework appends topic, partition, offset and broker_time)
/ @throws error naming the offending offsets when any bid is above its ask
/ @eg cols .qpipe.transform.example_quote_map.map .qpipe.transform.example_quote_map.message
.qpipe.transform.example_quote_map.map:{[m]
    bad:m[`bid]>m`ask;
    if[any bad; '"bid above ask: offsets ",", " sv string m[`offset] where bad];
    select sym, bid, ask from m}

.qetl.transform.define[`example_quote_map;`inputs`output`fn`examples!(
    enlist[`message]!enlist .qpipe.transform.example_quote_map.message;
    .qetl.kafka.topic.payload_schema[`example_quote;0b];
    .qpipe.transform.example_quote_map.map;
    enlist `inputs`expected!(
        enlist[`message]!enlist ([] topic:2#`example.quotes; partition:0 1j; offset:7 7j; broker_time:2#2026.10.20D09:00:00.000000000;
            sym:`EURUSD`GBPUSD; bid:1.085 1.265; ask:1.0852 1.2653);
        ([] sym:`EURUSD`GBPUSD; bid:1.085 1.265; ask:1.0852 1.2653)))];

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.example_quotes_decoder.publish:.qetl.job.stream.unwired `example_quotes_decoder;

/ marks, held and done: see .qetl.kafka.decoder.init.
.qetl.kafka.decoder.init `example_quotes_decoder;

/ @param x dead-letter rows
/ @private
.qpipe.job.example_quotes_decoder.publish_dead:{[x] .qpipe.job.example_quotes_decoder.publish[`kafka_dead_letter;x]}

/ Publish decoded rows onto the plant table a topic declared: one literal branch per output table.
/ @param t the plant table
/ @param x its rows
/ @private
.qpipe.job.example_quotes_decoder.publish_decoded:{[t;x]
    if[t=`example_quote; :.qpipe.job.example_quotes_decoder.publish[`example_quote;x]];
    '"example_quotes_decoder: has no publish for ",string[t]," - add it to publishes and to publish_decoded"}

/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.example_quotes_decoder.on_batch:{[t;x] .qetl.kafka.decoder.on_batch[`example_quotes_decoder;t;x]}

/ @return the number of rows published onto output tables
.qpipe.job.example_quotes_decoder.on_replayed:{[] .qetl.kafka.decoder.on_replayed `example_quotes_decoder}

.qetl.kafka.topic.define[`example.quotes;`mapping`output`job!(`example_quote_map;`example_quote;`example_quotes_decoder)];

.qetl.job.stream.define[`example_quotes_decoder;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `examplequotes1;
    enlist `kafka_message;
    `example_quote`kafka_dead_letter;
    .qpipe.job.example_quotes_decoder.on_batch;
    1b;
    `example_quote`kafka_dead_letter;
    .qpipe.job.example_quotes_decoder.on_replayed;
    "decodes the example.quotes Kafka topic into example_quote, deduplicated on (topic;partition;offset), and dead-letters what it cannot decode - a worked example of a sidecar decoder. Starts only when named, with a Kafka consumer";
    `marks`held`done)];

.qetl.kafka.decoder.check `example_quotes_decoder;
