/ kafka_client_flow.q - the client-trade topic's mapping (#1481): a Kafka
/ record on `uqf.client.flow` becomes a row of client_flow.
/ .
/ The payload is one JSON object per record:
/ .
/   {"sym":"EURUSD","side":"buy","qty":1000000,"price":1.1,"client":"ACME","trade_id":100}
/ .
/ This is the generic worked example of a topic declaration. It reads the
/ record's four coordinates and the six fields above and emits the row with the
/ coordinates kept: kafka_flow1 restores what it has processed from them.
/ .
/ A trade that cannot be a trade is REFUSED, by name, rather than published: a
/ quantity or price that is not positive. The row goes to kafka_dead_letter with
/ reason `mapping` and this message, and its neighbours in the batch publish
/ normally (kafka_ingest.q maps a refused batch row by row).
/ .
/ ANOTHER TOPIC is another file like this one: declare the transform with its
/ own payload fields and point .qetl.kafka.topic.define at it. Nothing in the
/ consumer, the envelope or kafka_flow changes - except that kafka_flow's
/ `publishes` list is read from the declared topics, so a new OUTPUT table is
/ picked up when the job is defined.

\d .

/ The record as the decoder hands it over: coordinates, then payload fields.
.qpipe.transform.kafka_client_flow.message:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$();
    sym:`symbol$(); side:`symbol$(); qty:`float$(); price:`float$(); client:`symbol$(); trade_id:`long$())

/ The mapping: refuse a non-positive quantity or price, else reorder into client_flow's columns.
/ @param m the decoded records
/ @return client_flow's published columns
/ @throws error naming the offending offsets when any quantity or price is not positive
/ @eg cols .qpipe.transform.kafka_client_flow.map .qpipe.transform.kafka_client_flow.message
.qpipe.transform.kafka_client_flow.map:{[m]
    bad:(m[`qty]<=0f) or m[`price]<=0f;
    if[any bad; '"quantity and price must be positive: offsets ",", " sv string m[`offset] where bad];
    select broker_time, sym, side, qty, price, client, trade_id, topic, partition, offset from m}

.qetl.transform.define[`kafka_client_flow;`inputs`output`fn`examples!(
    enlist[`message]!enlist .qpipe.transform.kafka_client_flow.message;
    .qetl.plant.published `client_flow;
    .qpipe.transform.kafka_client_flow.map;
    enlist `inputs`expected!(
        enlist[`message]!enlist ([] topic:2#`uqf.client.flow; partition:0 1j; offset:7 7j; broker_time:2#2026.10.20D09:00:00.000000000;
            sym:`EURUSD`GBPUSD; side:`buy`sell; qty:1000000 2000000f; price:1.1 1.27; client:`ACME`BETA; trade_id:100 101j);
        ([] broker_time:2#2026.10.20D09:00:00.000000000; sym:`EURUSD`GBPUSD; side:`buy`sell; qty:1000000 2000000f; price:1.1 1.27;
            client:`ACME`BETA; trade_id:100 101j; topic:2#`uqf.client.flow; partition:0 1j; offset:7 7j)))];

.qetl.kafka.topic.define[`uqf.client.flow;`mapping`output!(`kafka_client_flow;`client_flow)];

\d .
