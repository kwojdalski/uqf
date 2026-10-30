/ flink_vwap's raw records, as flink_vwap_streamer.py publishes them from outside q.
flink_vwap_raw:([]time:`timestamp$(); sym:`g#`symbol$(); window_end:`timestamp$(); vwap:`float$(); volume:`float$(); n:`long$())
/ flink_vwap1's output: one closed Flink window per sym, published once.
flink_vwap:([]time:`timestamp$(); sym:`g#`symbol$(); window_end:`timestamp$(); vwap:`float$(); volume:`float$(); n:`long$())
