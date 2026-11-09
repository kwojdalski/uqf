/ kafka_client_flow.q - the client-trade topic's mapping (#1481): a Kafka
/ record on `uqf.client.flow` becomes a row of client_flow.
/ .
/ The payload is one JSON object per record:
/ .
/   {"sym":"EURUSD","side":"buy","qty":1000000,"price":1.1,"client":"ACME","trade_id":100}
/ .
/ This is the generic worked example of a topic's mapping. It reads the
/ record's four coordinates and the six fields above and emits the row with the
/ payload columns ONLY: the framework appends the record's provenance (topic, partition,
/ offset, broker_time - .qetl.kafka.propagate), from which kafka_flow1 restores what it has processed. The
/ topic is DECLARED in streaming/kafka_flow.q, beside the job that owns it.
/ .
/ A trade that cannot be a trade is REFUSED, by name, rather than published: a
/ quantity or price that is not positive and finite, a side other than buy or
/ sell, or a null sym or trade_id (#2575). The row goes to kafka_dead_letter with
/ reason `mapping` and this message, and its neighbours in the batch publish
/ normally (kafka_ingest.q maps a refused batch row by row).
/ .
/ ANOTHER TOPIC is another transform like this one, declared by a decoder job
/ of its own (docs/services/kafka.md): nothing in the consumer, the envelope or
/ kafka_flow changes.

\d .

/ The record as the decoder hands it over: coordinates, then payload fields.
.qpipe.transform.kafka_client_flow.message:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$();
    sym:`symbol$(); side:`symbol$(); qty:`float$(); price:`float$(); client:`symbol$(); trade_id:`long$())

/ The mapping: refuse a record that cannot be a trade, else reorder into client_flow's columns.
/ @param m the decoded records
/ @return message_index (which record each row came from), then client_flow's payload columns (the provenance is appended by the framework)
/ @throws error naming each reason and its offending offsets: a quantity or price that is not positive and finite (a null is not positive), a side other than buy or sell, a null sym or trade_id
/ @eg cols .qpipe.transform.kafka_client_flow.map .qpipe.transform.kafka_client_flow.message
.qpipe.transform.kafka_client_flow.map:{[m]
    why:("quantity must be positive and finite";"price must be positive and finite";
        "side must be buy or sell";"sym must not be null";"trade_id must not be null");
    bad:((m[`qty]<=0f) or m[`qty]=0w; (m[`price]<=0f) or m[`price]=0w;
        not m[`side] in `buy`sell; null m`sym; null m`trade_id);
    hit:where any each bad;
    if[count hit; '"; " sv {[w;b;o] w,": offsets ",", " sv string o where b}[;;m`offset]'[why hit;bad hit]];
    select message_index:`long$i, sym, side, qty, price, client, trade_id from m}

.qetl.transform.define[`kafka_client_flow;`inputs`output`fn`examples!(
    enlist[`message]!enlist .qpipe.transform.kafka_client_flow.message;
    .qetl.kafka.topic.payload_schema[`client_flow];
    .qpipe.transform.kafka_client_flow.map;
    enlist `inputs`expected!(
        enlist[`message]!enlist ([] topic:2#`uqf.client.flow; partition:0 1j; offset:7 7j; broker_time:2#2026.10.20D09:00:00.000000000;
            sym:`EURUSD`GBPUSD; side:`buy`sell; qty:1000000 2000000f; price:1.1 1.27; client:`ACME`BETA; trade_id:100 101j);
        ([] message_index:0 1j; sym:`EURUSD`GBPUSD; side:`buy`sell; qty:1000000 2000000f; price:1.1 1.27;
            client:`ACME`BETA; trade_id:100 101j)))];

\d .
