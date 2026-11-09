/ synthetic_market.q - the invented FX market this demo publishes and
/ consumes (.qf.demo).
/ .
/ Four feed processes - torq_fx_feed.q, torq_quotes_feed.q,
/ torq_wide_book_feed.q, torq_fx_trades_feed.q - each carried their own copy
/ of the same four constants and the same random walk, at the root of their
/ own process. They run in separate processes, so nothing collided and
/ nothing compared them either: `drift_one` was written out four times and a
/ change to one was invisible to the other three. The wizard's feed template
/ was a fifth copy.
/ .
/ One definition here, loaded by src/etl/init.q, tested by
/ tests/q/test_synthetic_market.q - which is the part that could not exist
/ while this lived in a process script, because a feed script connects to a
/ tickerplant as it loads.
/ .
/ It is a SIMULATION, deliberately crude: a level that wanders, a fixed
/ spread and a fixed ladder shape. It stands in for a market data feed in a
/ demo and nothing here should be read as a market model.

\d .

/ The pairs every FX feed and ETL in this demo produces. The markout and
/ position jobs filter incoming quotes to these, because the vendored
/ starter pack publishes its own equity quotes onto the same table.
/ .
/ EURJPY is here to CLOSE A TRIANGLE. The other four are all USD-legged, so
/ every route between two non-USD currencies passes through USD exactly
/ once and never returns - there is no cycle, and therefore no cross-
/ currency arbitrage to find. With EURJPY quoted directly, EURUSD x USDJPY
/ has something to be compared against, which is what cross_arbitrage.q
/ does.
.qf.demo.pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD`EURJPY

/ Starting mid for each pair, in pairs order.
/ .
/ EURJPY starts at 1.0850 * 149.50, so the triangle begins consistent. It
/ does not stay that way: each pair wanders INDEPENDENTLY below, so the three
/ drift apart and the synthetic and direct prices cross each other from
/ time to time. That is what gives the cross-arbitrage detector something
/ to report, and it is also why those reports are an artefact of three
/ unrelated random walks rather than anything resembling a market.
.qf.demo.spot:1.0850 1.2650 149.50 0.6550 162.2075

/ One pip for each pair, in pairs order - from .qf.px.pip_size, the one rule,
/ rather than a list kept in step with `pairs` by hand. Also the half-spread
/ each feed quotes around the mid.
.qf.demo.pip:.qf.px.pip_size .qf.demo.pairs

/ The size every quote is published at, and the size the cross job prices.
.qf.demo.size_unit:1000000

/ One tick of a pair's price: a symmetric random walk, +/-5bp of the current
/ level.
/ .
/ Random by nature, so the property a test can assert is the BOUND, not the
/ value: a tick never moves more than 5bp, and never changes sign.
/ @param s the current level, a float atom or vector
/ @return the next level, same shape
/ @eg .qf.demo.drift_one 1.0  ->  a float within 5bp of 1.0
/ A vector takes one draw per level: `rand 1f` is a single draw, which moved
/ every pair by the same factor (#2627).
.qf.demo.drift_one:{[s] s*1+0.0005*-1+2*$[0>type s; rand 1f; (count s)?1f]}

/ THE SHARED LEVEL (#2562, #2561). The FX feeds run in separate processes,
/ and each used to walk its own copy of `spot`: within minutes UQFFX and
/ UQFDEPTH sat hundreds of bps apart, so the arbitrage job reported every
/ pair active, last_value flipped between sources, and fills priced off the
/ starting spot drifted ever further from the quotes. Processes share no
/ memory, so the one level they agree on is a function of the wall clock:
/ each pair is `spot` moved by a sum of slow cycles, the same at the same
/ instant in every process. Bounded by construction - it wanders within
/ `swing` of spot and never drifts off - and each pair has its own phases,
/ so pairs still move apart and the triangle still crosses now and then.

/ The cycle lengths, in nanoseconds: incommensurate, so the sum never repeats
/ in any span a demo runs for.
.qf.demo.cycles:`long$0D00:01 0D00:07 0D00:31 0D01:53 0D07:37

/ The largest proportional move from spot, reached only when every cycle peaks
/ together.
.qf.demo.swing:0.005

/ Each pair's phase in each cycle, in [0;2pi) - fixed, and different per pair.
.qf.demo.phases:{[i] 6.283185307179586*mod[;1f] 0.6180339887*(1+i)*1+til count .qf.demo.cycles} each til count .qf.demo.pairs

/ The shared mid of every pair at an instant.
/ @param t a timestamp
/ @return one mid per pair, in pairs order, within swing of spot
/ @eg .qf.demo.level[2026.01.01D00:00]~.qf.demo.level 2026.01.01D00:00  ->  1b
.qf.demo.level:{[t]
    angle:6.283185307179586*((`long$t) mod .qf.demo.cycles)%.qf.demo.cycles;
    .qf.demo.spot*exp .qf.demo.swing*avg each sin angle+/:.qf.demo.phases}

/ One source's mid at an instant: the shared level plus its own jitter, at
/ most a quarter pip either way - so two sources' mids are never more than
/ half a pip apart, well inside the two-pip spread every feed quotes, and
/ no pair of sources ever shows a crossed or arbitrageable book.
/ @param t a timestamp
/ @return one mid per pair, in pairs order, within a quarter pip of level t
/ @eg all (.qf.demo.pip%4)>=abs .qf.demo.source_level[2026.01.01D00:00]-.qf.demo.level 2026.01.01D00:00  ->  1b
.qf.demo.source_level:{[t]
    lv:.qf.demo.level t;
    lv+.qf.demo.pip*0.25*-1+2*(count lv)?1f}

/ A level-0-first price ladder for one pair: `levels` prices, `step` apart,
/ starting one step away from mid, so level 0 is never exactly mid.
/ @param mid the pair's current mid
/ @param step per-level spacing, usually the pair's pip
/ @param dir -1 for the bid side (descending from mid), 1 for the ask side
/ @param levels how many levels deep
/ @return a float vector, levels long, level-0-first
/ @eg .qf.demo.levels_one[1.1;0.0001;-1;3]  ->  1.0999 1.0998 1.0997
.qf.demo.levels_one:{[mid;step;dir;levels] mid+dir*step*1+til levels}

/ The size at each level: size_unit, 2*size_unit, ... - thinner at the top
/ of book and deeper further away. A fixed shape, the same for every pair
/ and tick, which is enough depth realism for a proof of concept.
/ @param levels how many levels deep
/ @return a float vector, levels long, level-0-first
/ @eg .qf.demo.levels_size 3  ->  1000000 2000000 3000000f
/ Float, as its plant columns declare (fx_orderbook's bid_sizes, "F"): size_unit
/ is a long, and the longs it made were stored as they came (#1074).
.qf.demo.levels_size:{[levels] "f"$.qf.demo.size_unit*1+til levels}

\d .
