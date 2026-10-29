/ Databento MBP-10 as the live feed handler publishes it - the source
/ contract's own fields, so a live row and an ODBC-backfilled row are the
/ same shape by construction. Written by the external Python feed handler
/ rather than by any q process (see external/databento_feed.py), the same way
/ crypto_book below is written by cryptorust.
/ .
/ Forty per-level columns because that is what Databento sends; folding
/ them into four vectors is databento1's job, not this table's.
databento_mbp10:([]time:`timestamp$(); ts_event:`timestamp$(); sym:`g#`symbol$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_px_00:`float$(); bid_sz_00:`long$(); ask_px_00:`float$(); ask_sz_00:`long$(); bid_px_01:`float$(); bid_sz_01:`long$(); ask_px_01:`float$(); ask_sz_01:`long$(); bid_px_02:`float$(); bid_sz_02:`long$(); ask_px_02:`float$(); ask_sz_02:`long$(); bid_px_03:`float$(); bid_sz_03:`long$(); ask_px_03:`float$(); ask_sz_03:`long$(); bid_px_04:`float$(); bid_sz_04:`long$(); ask_px_04:`float$(); ask_sz_04:`long$(); bid_px_05:`float$(); bid_sz_05:`long$(); ask_px_05:`float$(); ask_sz_05:`long$(); bid_px_06:`float$(); bid_sz_06:`long$(); ask_px_06:`float$(); ask_sz_06:`long$(); bid_px_07:`float$(); bid_sz_07:`long$(); ask_px_07:`float$(); ask_sz_07:`long$(); bid_px_08:`float$(); bid_sz_08:`long$(); ask_px_08:`float$(); ask_sz_08:`long$(); bid_px_09:`float$(); bid_sz_09:`long$(); ask_px_09:`float$(); ask_sz_09:`long$())

/ The folded book, republished by databento1 - the shape .qf.mkt and
/ .qf.mkt.cross_book_at read. Carries `ts_event` as well as `time`: the
/ tickerplant stamps `time` on receipt, and a book that knew only when it
/ ARRIVED could not tell a stale feed from a fast one.
eq_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); ts_event:`timestamp$(); action:`symbol$(); side:`symbol$(); price:`float$(); size:`long$(); sequence:`long$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
nested[`eq_orderbook;`bid_prices`bid_sizes`ask_prices`ask_sizes!"FJFJ"];
