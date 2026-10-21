/ kafka_crypto_book.q - the cryptorust book-snapshot topic's mapping (#1495): a
/ Kafka record on `cryptorust.book_snapshots` becomes a row of
/ crypto_book_snapshot, the top five levels a side as wide columns.
/ .
/ The payload is one JSON object per record, as kafka-market-data-recorder
/ writes it (cryptorust docs/kafka_market_data.md):
/ .
/   {"schema_version":1,"kind":"book_snapshot","sampled_at_ms":1760950800100,
/    "exchange_timestamp_ms":1760950800050,"local_timestamp_ms":1760950800060,
/    "venue":"binance","symbol":"BTC-USDT",
/    "bids":[[65000.5,0.25],[65000.0,1.5]],"asks":[[65001.0,0.4]],
/    "source_metadata":{...}}
/ .
/ Times are epoch MILLISECONDS as JSON numbers, read exactly (below 2^53) and
/ turned into timestamps here. bids and asks are [price,size] pairs, best
/ first, truncated by the producer to its top_n_levels (default 5): fewer
/ levels, or none, are normal and leave null floats in the missing columns; more
/ than five are cut to five. source_metadata is not read - the raw payload stays
/ on kafka_message.
/ .
/ A RECORD THIS MAPPING CANNOT STAND BEHIND is refused, by name, and goes to
/ kafka_dead_letter with reason `mapping`: a schema_version other than 1, a kind
/ other than book_snapshot, a level that is not a [price,size] pair (a null
/ price or size is not a number: serde_json writes a NaN or infinite f64 as
/ null), a timestamp outside [2000.01.01; 2100.01.01) as epoch milliseconds
/ (microseconds overflow past it, seconds land in 1970). Its neighbours in the batch publish normally. The
/ topic is DECLARED in streaming/crypto_book_decoder.q, beside its job.

\d .

/ How many levels a side keeps in the wide table.
.qpipe.transform.kafka_crypto_book.depth:5

/ The record as the decoder hands it over: coordinates, then payload fields.
.qpipe.transform.kafka_crypto_book.message:([] topic:`symbol$(); partition:`long$(); offset:`long$(); broker_time:`timestamp$();
    schema_version:`long$(); kind:`symbol$(); sampled_at_ms:`long$(); exchange_timestamp_ms:`long$(); local_timestamp_ms:`long$();
    venue:`symbol$(); symbol:`symbol$(); bids:(); asks:())

/ Epoch milliseconds as a timestamp.
/ @param ms epoch milliseconds
/ @return the timestamp
/ @eg .qpipe.transform.kafka_crypto_book.ts 1760950800100j  ->  2025.10.20D09:00:00.100000000
.qpipe.transform.kafka_crypto_book.ts:{[ms] 1970.01.01D00:00:00.000000000+1000000*ms}

/ One column of a side: field i (0 price, 1 size) of level n, null where the book has no level n.
/ @param side per row, the list of [price,size] pairs
/ @param n the level
/ @param i the field in the pair
/ @return a float vector, a row each
/ @private
.qpipe.transform.kafka_crypto_book.level:{[side;n;i] {[n;i;s] $[n<count s; s[n;i]; 0nf]}[n;i] each side}

/ The mapping: refuse what is not a version-1 book snapshot, else lay the levels out wide.
/ @param m the decoded records
/ @return message_index (which record each row came from), then crypto_book_snapshot's payload columns (the provenance is appended by the framework)
/ @throws error naming the offending offsets when a record is of another version or kind, has a level that is not a pair, or a timestamp outside the plausible epoch-millisecond window
/ @eg cols .qpipe.transform.kafka_crypto_book.map .qpipe.transform.kafka_crypto_book.message
.qpipe.transform.kafka_crypto_book.map:{[m]
    pairs:{all (2=count each x) and not any each null x};
    badpair:not (pairs each m`bids) and pairs each m`asks;
    / plausible epoch milliseconds, [2000.01.01; 2100.01.01): a unit slip is refused, not published
    inwin:{[w;x] (x>=w 0) and x<w 1}[946684800000 4102444800000j];
    badtime:not (inwin m`sampled_at_ms) and (inwin m`exchange_timestamp_ms) and inwin m`local_timestamp_ms;
    bad:(m[`schema_version]<>1) or (m[`kind]<>`book_snapshot) or badpair or badtime;
    if[any bad; '"not a version-1 book_snapshot with [price,size] levels and epoch-millisecond timestamps: offsets ",", " sv string m[`offset] where bad];
    n:til .qpipe.transform.kafka_crypto_book.depth;
    px:{[m;s;n] .qpipe.transform.kafka_crypto_book.level[m s;n;0]}[m];
    sz:{[m;s;n] .qpipe.transform.kafka_crypto_book.level[m s;n;1]}[m];
    head:`sym`venue`source_time`local_time`sampled_time!(m`symbol;m`venue;
        .qpipe.transform.kafka_crypto_book.ts[m`exchange_timestamp_ms];
        .qpipe.transform.kafka_crypto_book.ts[m`local_timestamp_ms];
        .qpipe.transform.kafka_crypto_book.ts[m`sampled_at_ms]);
    names:{`$x,/:string y};
    levels:(names["bid_px";n],names["bid_sz";n],names["ask_px";n],names["ask_sz";n])!(px[`bids]'[n]),(sz[`bids]'[n]),(px[`asks]'[n]),sz[`asks]'[n];
    flip (enlist[`message_index]!enlist `long$til count m),head,levels}

.qetl.transform.define[`kafka_crypto_book;`inputs`output`fn`examples!(
    enlist[`message]!enlist .qpipe.transform.kafka_crypto_book.message;
    .qetl.kafka.topic.payload_schema[`crypto_book_snapshot];
    .qpipe.transform.kafka_crypto_book.map;
    / a full book; a one-level bid side and a two-level ask side; two bids and an empty ask side
    enlist `inputs`expected!(
        enlist[`message]!enlist ([] topic:3#`cryptorust.book_snapshots; partition:0 0 1j; offset:10 11 5j; broker_time:3#2025.10.20D09:00:00.120000000;
            schema_version:3#1j; kind:3#`book_snapshot;
            sampled_at_ms:3#1760950800100j; exchange_timestamp_ms:3#1760950800050j; local_timestamp_ms:3#1760950800060j;
            venue:`binance`binance`kraken; symbol:`$("BTC-USDT";"BTC-USDT";"ETH-USD");
            bids:(
                (65000.5 0.25;64999.5 1.5;64999 2f;64998.5 0.1;64998 3f);
                enlist 65001.5 0.5;
                (3200.5 1f;3200 2f));
            asks:(
                (65001 0.4;65001.5 0.6;65002 1f;65002.5 1.2;65003 2.2);
                (65002 0.7;65002.5 0.8);
                ()));
        ([] message_index:0 1 2j; sym:`$("BTC-USDT";"BTC-USDT";"ETH-USD"); venue:`binance`binance`kraken;
            source_time:3#2025.10.20D09:00:00.050000000; local_time:3#2025.10.20D09:00:00.060000000; sampled_time:3#2025.10.20D09:00:00.100000000;
            bid_px0:65000.5 65001.5 3200.5;
            bid_px1:64999.5 0n 3200.0;
            bid_px2:64999.0 0n 0n;
            bid_px3:64998.5 0n 0n;
            bid_px4:64998.0 0n 0n;
            bid_sz0:0.25 0.5 1.0;
            bid_sz1:1.5 0n 2.0;
            bid_sz2:2.0 0n 0n;
            bid_sz3:0.1 0n 0n;
            bid_sz4:3.0 0n 0n;
            ask_px0:65001.0 65002.0 0n;
            ask_px1:65001.5 65002.5 0n;
            ask_px2:65002.0 0n 0n;
            ask_px3:65002.5 0n 0n;
            ask_px4:65003.0 0n 0n;
            ask_sz0:0.4 0.7 0n;
            ask_sz1:0.6 0.8 0n;
            ask_sz2:1.0 0n 0n;
            ask_sz3:1.2 0n 0n;
            ask_sz4:2.2 0n 0n)))];

\d .
