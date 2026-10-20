/ crypto_book_decoder.q - decode cryptorust's Kafka book snapshots into a wide
/ book table (.qpipe.job.crypto_book_decoder, #1495).
/ .
/ cryptorust's kafka-market-data-recorder publishes sampled full books to
/ `cryptorust.book_snapshots`, keyed `venue:symbol`. kafka_feed1 can already put
/ those records on `kafka_message`, undecoded; this job turns the ones of that
/ topic into rows of crypto_book_snapshot - the top five levels a side as wide
/ columns, a null float where a side is shorter - and sends what it cannot read
/ (not JSON, a missing field, an unknown schema_version or kind) to
/ kafka_dead_letter. The payload and the mapping are in
/ transforms/kafka_crypto_book.q.
/ .
/ NOT crypto_book. That table is the kdb recorder's vector-shaped book (a row
/ holds a whole side as lists) and stays as it is; this one is the same fact for
/ a desk that wants a flat row it can filter and join, at a sampled cadence.
/ .
/ The marks, the deduplication on (topic;partition;offset), the dead letters and
/ the restart replay are .qetl.kafka.decoder (core/kafka_decoder.q) - the same
/ machinery as kafka_flow, which this job copies the shape of. A redelivered
/ record is dropped, and a record that arrived while the job was down is handled
/ when it restarts.
/ .
/ SUBSCRIBE: `UQF_KAFKA_TOPICS=cryptorust.book_snapshots uqs start kafka_feed1`,
/ with this decoder started beside it (docs/services/kafka.md). It is in the
/ crypto profile and not in a default start: until a consumer publishes to
/ kafka_message it would hold one of the sixteen licensed plant connections to
/ consume nothing.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\d .

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
.qpipe.job.crypto_book_decoder.publish:.qetl.job.stream.unwired `crypto_book_decoder;

/ marks, held and done: see .qetl.kafka.decoder.init.
.qetl.kafka.decoder.init `crypto_book_decoder;

/ @param x dead-letter rows
/ @private
.qpipe.job.crypto_book_decoder.publish_dead:{[x] .qpipe.job.crypto_book_decoder.publish[`kafka_dead_letter;x]}

/ Publish decoded rows onto the plant table a topic declared: one literal branch per output table.
/ @param t the plant table
/ @param x its rows
/ @private
.qpipe.job.crypto_book_decoder.publish_decoded:{[t;x]
    if[t=`crypto_book_snapshot; :.qpipe.job.crypto_book_decoder.publish[`crypto_book_snapshot;x]];
    '"crypto_book_decoder: has no publish for ",string[t]," - add it to publishes and to publish_decoded"}

/ Handle a batch: see .qetl.kafka.decoder.on_batch.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
.qpipe.job.crypto_book_decoder.on_batch:{[t;x] .qetl.kafka.decoder.on_batch[`crypto_book_decoder;t;x]}

/ After the replay: handle what was never handled (.qetl.kafka.decoder.on_replayed).
/ @return the number of rows published onto output tables
.qpipe.job.crypto_book_decoder.on_replayed:{[] .qetl.kafka.decoder.on_replayed `crypto_book_decoder}

.qetl.kafka.topic.define[`cryptorust.book_snapshots;`mapping`output`job!(`kafka_crypto_book;`crypto_book_snapshot;`crypto_book_decoder)];

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`crypto_book_decoder;`procname`subscribe_to`publishes`on_batch`replay`restore_from`on_replayed`note`state!(
    `crypto_book_decoder1;
    enlist `kafka_message;
    `crypto_book_snapshot`kafka_dead_letter;
    .qpipe.job.crypto_book_decoder.on_batch;
    1b;
    `crypto_book_snapshot`kafka_dead_letter;
    .qpipe.job.crypto_book_decoder.on_replayed;
    "decodes cryptorust's Kafka book snapshots (topic cryptorust.book_snapshots) from kafka_message into crypto_book_snapshot, the top five levels a side as wide columns, deduplicated on (topic;partition;offset), and sends what it cannot decode to kafka_dead_letter. The records come from a Kafka consumer (kafka_feed1 with UQF_KAFKA_TOPICS=cryptorust.book_snapshots), so it does not start with the stack: it would hold a plant connection to consume nothing";
    `marks`held`done)];

.qetl.kafka.decoder.check `crypto_book_decoder;
