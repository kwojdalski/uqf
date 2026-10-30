.qcat.hidden[`flink_vwap_raw]:
    "the RAW flink_vwap records, published by flink_vwap_streamer.py outside q and read only by flink_vwap, which reshapes them into flink_vwap";
.qcat.describe[`flink_vwap]:
    "Volume-weighted average price, total volume and trade count for one sym over one tumbling window, computed by an Apache Flink job outside q; each window appears once, keyed by its window_end";
.qcat.unbounded[`flink_vwap]:
    "one row per sym per one-minute window Flink closes - a few thousand a day";
