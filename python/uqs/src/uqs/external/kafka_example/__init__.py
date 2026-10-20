"""A reproducible Kafka-to-kdb+ crypto example (#1492).

A disposable broker (`broker`), a deterministic synthetic publisher (`batch`,
`publisher`) and a staged verifier that queries the real RDB (`verify`), for
the `kafka_crypto_example` sidecar bundle. Nothing here is part of the plant
or of the orchestrator proper: `confluent-kafka` and `kola` are imported only
where a command needs them, so importing this package needs neither.

The walkthrough is docs/guides/kafka-crypto-example.md.
"""
