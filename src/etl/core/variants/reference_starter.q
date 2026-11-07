/ reference_starter.q - the starter reference variants, beside variant #1 `best_top` (#2289).
/ .
/ A variant is a declaration: where the books come from (venues, staleness, offset), which transform reads a price
/ from each, how the books of the venues are combined. Nothing else changes to add one - no column, no backfill -
/ and tests/q/test_reference_starter.q holds each of these to a hand-computed price.
/ .
/   micro_median   the micro-price of each venue's top of book, the median across venues
/   vwap_1m_best   the price of filling 1,000,000 on each side, the best bid and the best ask across venues
/   sizew_mid      the level-0 mid, weighted across venues by the size quoted there
/   median_top     the level-0 bid and ask, the median across venues
/   depth3_wmean   the size-weighted price of the first three levels of each side, weighted across venues by that depth

\d .

.qetl.reference.register[`micro_median;`venues`stale`offset`transform`combine!(`all;`spec;0D;`micro;`median)];
.qetl.reference.register[`vwap_1m_best;`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`vwap;`best;enlist[`qty]!enlist 1000000f)];
.qetl.reference.register[`sizew_mid;`venues`stale`offset`transform`combine!(`all;`spec;0D;`mid_sized;`wmean)];
.qetl.reference.register[`median_top;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`median)];
.qetl.reference.register[`depth3_wmean;`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`topn_w;`wmean;enlist[`n]!enlist 3)];

\d .
