.qcat.describe[`crypto_trade]:
    "One synthetic crypto trade decoded from a Kafka record on example.crypto.trades or example.crypto.trades.b, with the record's topic, partition and offset - the kafka_crypto_example bundle's output.";
/ Generated for a worked example, a few rows a second: read whole rather than held to a querypolicy.csv row a bundle cannot add.
.qcat.unbounded[`crypto_trade]:"synthetic example rows";
