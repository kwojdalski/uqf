/ Integration check in a fresh q process. Argument: a NEW disposable directory.
\l src/metadata/metatables.q
\l scripts/torq_metatables.q

if[1<>count .z.x;'"supply one disposable HDB directory"];
db:hsym `$first .z.x;
fixture:([]sym:`EURUSD`EURUSD`GBPUSD;venue:`EBS`EBS`REUTERS;size:10 20 30f);
.Q.dpft[db;2026.09.01;`sym;`fixture];
fixture:([]sym:enlist`EURUSD;venue:enlist`EBS;size:enlist 40f);
.Q.dpft[db;2026.09.02;`sym;`fixture];
system "l ",first .z.x;

assert:{[ok;msg] if[not ok;-2 msg;exit 1]};
totals:.qmeta.definition[`fixture;`date;`symbol$();()!()];
result:.qmeta.collect[totals;2026.09.01 2026.09.02 2026.09.03];
assert[3 1 0j~result`rows;"HDB partition totals/empty slice"];
assert[2026.09.01 2026.09.02 2026.09.03~result`date;"source partitions preserved"];
metrics:`rows`notional!((count;`i);(sum;`size));
grouped:.qmeta.definition[`fixture;`date;`sym`venue;metrics];
result:.qmeta.collect[grouped;enlist 2026.09.01];
assert[2 1j~result`rows;"HDB grouped counts"];
assert[30 30f~result`notional;"HDB custom aggregate"];
assert[2026.09.01 2026.09.01~result`date;"HDB bounded partition filter"];
empty:.qmeta.collect[grouped;enlist 2026.09.03];
assert[0=count empty;"missing partition has no invented groups"];
assert[(0#result)~empty;"empty HDB grouped result preserves schema"];
repeated:.qmeta.refresh[result;grouped;enlist 2026.09.01];
assert[(delete meta_observed_at from result)~delete meta_observed_at from repeated;"repeat refresh does not double count"];
adapted:.dqe.uqf_metatable[`fx_counts;`fixture;`date;enlist 2026.09.01;`sym`venue;metrics];
assert[2 1j~(adapted`fx_counts)`rows;"DQE adapter against loaded HDB"];
-1 "metatables HDB: 9 checks passed";
exit 0
