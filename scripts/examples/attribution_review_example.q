// attribution_review_example.q - the desk-review report for the markout
// attribution convention (#1213), built from a small deterministic fixture.
//
// WHY THIS EXISTS. .qf.mkt.attribution_convention (proposed_v1) is a PROPOSED
// default that no desk has approved. This prints what it would do, with
// numbers, so a desk can read it and say yes or no: the route of every
// pair, the routes that differ from a naive "via USD" route, and for a set of
// trades how each pair's markout splits across its route legs, how much is
// left as residual, and how often a prescribed leg had no book.
//
// THE FIXTURE is invented and deterministic (no random numbers, no clock): five
// books at three instants, and a buy and a sell in each of six pairs -
//   EURUSD, GBPUSD  G10, one leg each
//   USDPLN          EM via EUR: EURUSD (inverted) + EURPLN
//   AUDPLN          two currencies bridging via EUR: EURAUD (inverted) + EURPLN
//   EURMXN          EM via USD: EURUSD + USDMXN
//   GBPPLN          has its own book, but the prescribed EURGBP leg has none:
//                   that leg is `unavailable`, never rerouted
// The numbers are illustrative, not market data.
//
// Run from the repository root: q scripts/examples/attribution_review_example.q

\c 400 1000
\l src/init.q
\l src/etl/core/log.q

.qetl.log.debug 1b;

t_zero:2026.01.01D00:00:00.000000000;
one_second:0D00:00:01;

/ One top-of-book row: a mid with a 2-pip spread, deep enough to fill any size.
mk_row:{[tm;sym;mid]
    `time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes!(tm;sym;enlist mid-0.0001;enlist 1e9;enlist mid+0.0001;enlist 1e9)};

/ (sym; mid at 0s; mid at 1s; mid at 2s)
mids:(
    (`EURUSD;1.1000;1.1200;1.1300);(`GBPUSD;1.2500;1.2480;1.2520);
    (`EURPLN;4.3000;4.3500;4.3400);(`EURAUD;1.6000;1.6200;1.6100);
    (`USDMXN;17.00;17.10;17.20);(`GBPPLN;5.40;5.45;5.48));
quotes:`sym`time xasc flip flip raze {[s] (mk_row[t_zero;s 0;s 1];mk_row[t_zero+one_second;s 0;s 2];mk_row[t_zero+2*one_second;s 0;s 3])} each mids;

pairs:`EURUSD`GBPUSD`USDPLN`AUDPLN`EURMXN`GBPPLN;
trades:([] time:(2*count pairs)#t_zero,t_zero+one_second; sym:raze pairs,'pairs; side:(2*count pairs)#1 -1);
.qetl.log.info[`attribution_review;"fixture - ",.Q.s1[count quotes]," quotes, ",.Q.s1[count trades]," trades in ",.Q.s1[count pairs]," pairs";()!()];

/ ==== the route table, narrowed to the fixture's pairs ====
conv:.qf.mkt.attribution_convention;
.qetl.log.info[`attribution_review;"convention ",.Q.s1[conv`version]," status ",.Q.s1[conv`status]," - PROPOSED, not approved";()!()];
route_table:.qf.mkt.attribution_route_table conv;
.qetl.log.info[`attribution_review;"route table for the fixture's pairs (differs_from_usd = the route is not the via-USD one):";()!()];
show select from route_table where pair in pairs;

/ ==== the pairs the desk has to look at ====
diffs:.qf.mkt.attribution_route_differences conv;
.qetl.log.info[`attribution_review;.Q.s1[count diffs]," of ",.Q.s1[count route_table]," pairs route differently from via-USD; the first rows:";()!()];
show 8#diffs;

/ ==== the review ====
review:.qf.exec.attribution_review_with[conv;quotes;trades;enlist one_second;10000;1];
.qetl.log.info[`attribution_review;"review - per pair and route leg: trades, total/mean pips, share of the pair's total, unavailable count:";()!()];
show review;

/ ==== checks: the same invariants the unit tests pin ====
parts:select s:sum share by pair from review where kind in `leg`residual;
if[any 1e-9<abs 1f-parts`s;
    .qetl.log.err[`attribution_review;"leg shares plus residual share should be 1 for every pair";()!()];
    exit 1];
if[not 2=first exec n_unavailable from review where pair=`GBPPLN,kind=`leg,leg=`EURGBP;
    .qetl.log.err[`attribution_review;"GBPPLN's EURGBP leg should be unavailable for both trades";()!()];
    exit 1];
differs_ok:(`AUDPLN in diffs`pair) and not any `EURUSD`GBPUSD in diffs`pair;
if[not differs_ok;
    .qetl.log.err[`attribution_review;"AUDPLN should differ from via-USD and the G10 directs should not";()!()];
    exit 1];

// exit 0
