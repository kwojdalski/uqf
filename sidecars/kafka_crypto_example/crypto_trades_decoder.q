/ crypto_trades_decoder.q - the Kafka decoder job of the crypto example
/ (.qpipe.job.crypto_trades_decoder, #1492).
/ .
/ WHY A BUNDLE, AND NOT A JOB IN THE TREE. A crypto trade table is an example's
/ table: the tree's plant (src/etl/plant_tables.q) is the production schema and
/ should not grow a table that exists to be demonstrated. A sidecar bundle
/ (docs/guides/sidecar-bundles.md) adds the table, its mapping, its topics and
/ its job without editing the tree, and sidecars/kafka_example is the
/ precedent. This one differs from it only in being runnable end to end -
/ against a real broker and real TorQ processes: docs/guides/kafka-crypto-example.md.
/ .
/ Nothing here is new machinery. The marks, the deduplication on
/ (topic;partition;offset), the dead letters and the restart replay are
/ .qetl.kafka.decoder (src/etl/core/kafka_decoder.q); this file is a payload
/ shape, a mapping, two topic declarations and a job.
/ .
/ THE PAYLOAD is one JSON object per record, all fields required:
/ .
/   {"sym":"BTC-USD","trade_id":"BTC-000007","ts":"2026-10-20T09:00:01.250Z",
/    "price":65000.5,"qty":0.25,"side":"buy"}
/ .
/ trade_id is a STRING on the wire and never a number: a numeric id is
/ dead-lettered `malformed`, because a JSON number is read as a float and a long
/ id above 2^53 would be silently rounded. The decoder's typed fields have no
/ string type, so it lands as a symbol (case preserved). A production table of
/ millions of distinct ids would want a string column and a decoder type to
/ match; this example's volume does not, and the limit is written down here
/ rather than discovered.
/ .
/ A TRADE THE MAPPING REFUSES is dead-lettered with reason `mapping`: a
/ non-positive price or quantity, or a side that is not buy or sell. Everything
/ else the decoder refuses - not JSON, a missing field, a mistyped one, an empty
/ payload - it dead-letters itself, by its own reason.
/ .
/ TWO TOPICS, ONE TABLE. example.crypto.trades and example.crypto.trades.b
/ both map to crypto_trade, so offset 0 of partition 0 exists on both and the
/ dedupe has to keep them apart: the topic is part of the key.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

/ The record as the decoder hands it over: coordinates, then payload fields.
.qpipe.transform.crypto_trade_map.message:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$();
    sym:`symbol$(); trade_id:`symbol$(); ts:`timestamp$(); price:`float$(); qty:`float$(); side:`symbol$())

/ The mapping: refuse a trade that cannot have happened, else reorder into crypto_trade's columns.
/ @param m the decoded records
/ @return message_index (which record each row came from), then crypto_trade's payload columns (the framework appends topic, partition, offset and broker_time)
/ @throws error naming the offending offsets when a price or quantity is not positive, or a side is neither buy nor sell
/ @eg cols .qpipe.transform.crypto_trade_map.map .qpipe.transform.crypto_trade_map.message
.qpipe.transform.crypto_trade_map.map:{[m]
    side:lower m`side;
    bad:(m[`price]<=0f) or (m[`qty]<=0f) or not side in `buy`sell;
    if[any bad; '"not a trade (price and qty must be positive, side buy or sell): offsets ",", " sv string m[`offset] where bad];
    select message_index:`long$i, event_time:ts, sym, trade_id, price, qty, side from update side:lower side from m}

.qetl.transform.define[`crypto_trade_map;`inputs`output`fn`examples!(
    enlist[`message]!enlist .qpipe.transform.crypto_trade_map.message;
    .qetl.kafka.topic.payload_schema[`crypto_trade;0b];
    .qpipe.transform.crypto_trade_map.map;
    enlist `inputs`expected!(
        enlist[`message]!enlist ([] topic:2#`example.crypto.trades; partition:0 1j; offset:7 7j; broker_time:2#2026.10.20D09:00:00.002000000;
            sym:`$("BTC-USD";"ETH-USD"); trade_id:`$("BTC-000007";"ETH-000007"); ts:2#2026.10.20D09:00:00.000000000;
            price:65000.5 3200.25; qty:0.25 1.5; side:`buy`SELL);
        ([] message_index:0 1j; event_time:2#2026.10.20D09:00:00.000000000;
            sym:`$("BTC-USD";"ETH-USD"); trade_id:`$("BTC-000007";"ETH-000007");
            price:65000.5 3200.25; qty:0.25 1.5; side:`buy`sell)))];

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.crypto_trades_decoder.publish:.qetl.job.stream.unwired `crypto_trades_decoder;

/ marks, held and done: see .qetl.kafka.decoder.init.
.qetl.kafka.decoder.init `crypto_trades_decoder;

/ @param x dead-letter rows
/ @private
.qpipe.job.crypto_trades_decoder.publish_dead:{[x] .qpipe.job.crypto_trades_decoder.publish[`kafka_dead_letter;x]}

/ Publish decoded rows onto the plant table a topic declared: one literal branch per output table.
/ @param t the plant table
/ @param x its rows
/ @private
.qpipe.job.crypto_trades_decoder.publish_decoded:{[t;x]
    if[t=`crypto_trade; :.qpipe.job.crypto_trades_decoder.publish[`crypto_trade;x]];
    '"crypto_trades_decoder: has no publish for ",string[t]," - add it to publishes and to publish_decoded"}

/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.crypto_trades_decoder.on_batch:{[t;x] .qetl.kafka.decoder.on_batch[`crypto_trades_decoder;t;x]}

/ @return the number of rows published onto output tables
.qpipe.job.crypto_trades_decoder.on_replayed:{[] .qetl.kafka.decoder.on_replayed `crypto_trades_decoder}

.qetl.kafka.topic.define[`example.crypto.trades;`mapping`output`job!(`crypto_trade_map;`crypto_trade;`crypto_trades_decoder)];
.qetl.kafka.topic.define[`example.crypto.trades.b;`mapping`output`job!(`crypto_trade_map;`crypto_trade;`crypto_trades_decoder)];

.qetl.job.stream.define[`crypto_trades_decoder;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `cryptotrades1;
    enlist `kafka_message;
    `crypto_trade`kafka_dead_letter;
    .qpipe.job.crypto_trades_decoder.on_batch;
    1b;
    `crypto_trade`kafka_dead_letter;
    .qpipe.job.crypto_trades_decoder.on_replayed;
    "decodes the example.crypto.trades topics into crypto_trade, deduplicated on (topic;partition;offset), and dead-letters what it cannot decode - the runnable crypto example (docs/guides/kafka-crypto-example.md). Starts only when named, with a Kafka consumer";
    `marks`held`done)];

.qetl.kafka.decoder.check `crypto_trades_decoder;
