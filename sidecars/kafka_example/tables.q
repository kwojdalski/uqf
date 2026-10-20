/ example_quotes_decoder1's output: one top-of-book quote decoded from a Kafka record on example.quotes. topic, partition and offset are the record's coordinates; a restart reads them back to know what it handled.
example_quote:([]time:`timestamp$(); broker_time:`timestamp$(); sym:`g#`symbol$(); bid:`float$(); ask:`float$(); topic:`symbol$(); partition:`long$(); offset:`long$())
