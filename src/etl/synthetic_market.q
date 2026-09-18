/ synthetic_market.q - the invented FX market this demo publishes and
/ consumes (.qsynth).
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
/ It is a SIMULATION, deliberately crude: a symmetric random walk with a
/ fixed spread and a fixed ladder shape. It stands in for a market data feed
/ in a demo and nothing here should be read as a market model.

\d .qsynth

/ The four pairs every FX feed and ETL in this demo produces. The markout
/ and position jobs filter incoming quotes to these, because the vendored
/ starter pack publishes its own equity quotes onto the same table.
pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD

/ Starting mid for each pair, in pairs order.
spot:1.0850 1.2650 149.50 0.6550

/ One pip for each pair, in pairs order - 0.01 for the JPY pair, 0.0001 for
/ the rest. Also the half-spread each feed quotes around the mid.
pip:0.0001 0.0001 0.01 0.0001

/ The size every quote is published at, and the size the cross job prices.
size_unit:1000000

/ One tick of a pair's price: a symmetric random walk, +/-5bp of the current
/ level.
/ .
/ Random by nature, so the property a test can assert is the BOUND, not the
/ value: a tick never moves more than 5bp, and never changes sign.
/ @param s the current level, a float atom or vector
/ @return the next level, same shape
/ @eg .qsynth.drift_one 1.0  ->  a float within 5bp of 1.0
drift_one:{[s] s*1+0.0005*-1+2*rand 1f}

/ A level-0-first price ladder for one pair: `levels` prices, `step` apart,
/ starting one step away from mid, so level 0 is never exactly mid.
/ @param mid the pair's current mid
/ @param step per-level spacing, usually the pair's pip
/ @param dir -1 for the bid side (descending from mid), 1 for the ask side
/ @param levels how many levels deep
/ @return a float vector, levels long, level-0-first
/ @eg .qsynth.levels_one[1.1;0.0001;-1;3]  ->  1.0999 1.0998 1.0997
levels_one:{[mid;step;dir;levels] mid+dir*step*1+til levels}

/ The size at each level: size_unit, 2*size_unit, ... - thinner at the top
/ of book and deeper further away. A fixed shape, the same for every pair
/ and tick, which is enough depth realism for a proof of concept.
/ @param levels how many levels deep
/ @return a float vector, levels long, level-0-first
/ @eg .qsynth.levels_size 3  ->  1000000 2000000 3000000
levels_size:{[levels] .qsynth.size_unit*1+til levels}

\d .
