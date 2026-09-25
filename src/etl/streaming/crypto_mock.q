/ crypto_mock.q - a stand-in for cryptorust, publishing what its two kdb
/ recorders publish (.qpipe.job.crypto_mock).
/ .
/ WHY. The stack declares crypto_book and crypto_trades, and cryptorust's
/ kdb-market-data-recorder and kdb-fills-recorder fill them - but only when
/ cryptorust is running, with an OMS attached, against a live venue. Without
/ all three the tables stay empty, and an empty table cannot say whether the
/ wiring is wrong or the publisher is idle. This job makes the crypto half of
/ the stack demonstrable with nothing but q, the way fx_feed and
/ fx_trades_feed already make the FX half demonstrable.
/ .
/ WHAT IT MIMICS, and how closely. The wire shape is the recorder's row for
/ row - see build_real_upd_message in cryptorust's
/ src/bin/kdb_fills_recorder.rs: one message per fill, every column a
/ ONE-ELEMENT typed vector, side as a signed long, fee in the quote
/ currency, a unique exchange_fill_id, and no `time` (the plant stamps it).
/ The fill model is that repository's QuoteFillSimulator
/ (src/components/fill/fill_simulator.rs): the maker's bid and ask each fill
/ with a probability, positive toxicity lifts the ask side and negative hits
/ the bid, and a fill takes a Beta-distributed fraction of the quoted size,
/ capped. Per-venue fill hazards and maker fees are the values in that
/ repository's config/default.yaml.
/ .
/ WHAT IT DOES NOT EMIT. crypto_sim_fills. That table is the paper
/ strategy's own artifact - fills against a probabilistic model, carrying no
/ venue and no exchange id - and a position engine must never consume it.
/ tests/q/test_stack_tables.q already refuses to let the two fill tables
/ collapse into one; this file does not publish the one that would tempt it.
/ .
/ It is a SIMULATION, and crude where cruder is clearer: a symmetric random
/ walk for each mid, a fixed ladder, a toxicity that drifts rather than
/ being derived from flow. Nothing here is a market model.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qpipe.job.crypto_mock

/ Where rows go. A stub until .qetl.job.stream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `crypto_mock;

/ --------------------------------------------------------------- MARKET

/ The venues this mock trades on, and each one's maker fee in basis points
/ and fill hazard at the touch in fills per second - cryptorust's own
/ config/default.yaml values for venue_fees_bps and base_hazard_per_s.
venues:`binance_spot`bybit_spot
maker_fee_bps:10 8f
hazard_per_s:1.5 1.5

/ The symbols, spelled the way cryptorust spells them - BASE-QUOTE with a
/ hyphen, not the FX-style EURUSD. The quote currency after the hyphen is
/ what a fee is charged in.
syms:`$("BTC-USDT";"ETH-USDT";"BTC-USDC")

/ Starting mid per symbol, in syms order.
mid0:62000 2450 61990f

/ The size the maker quotes at, in base units, per symbol - half a bitcoin,
/ five ether. Float, matching the schema.
quote_size:0.5 5 0.5

/ Half the spread the maker quotes, as a fraction of mid: one basis point.
half_spread:0.0001

/ Ladder depth and the spacing between levels, as a fraction of mid.
n_levels:3
level_step:0.0001

/ How often the maker is actually at the touch. A market maker is outquoted
/ more often than not, and a mock that filled every tick on every side
/ would flood the position table with a row per fill faster than anyone
/ could read it.
quote_share:0.3

/ Beta parameters for the fraction of a quote that fills, and the cap on
/ it. Symmetric around a half: a partial fill is the ordinary case.
fill_alpha:2
fill_beta:2
max_fill_fraction:0.8

/ How strongly toxicity skews the two sides' fill probabilities -
/ QuoteFillSimulator's toxicity_factor.
toxicity_factor:0.5

/ ---------------------------------------------------------------- STATE

/ The current mid per (venue; sym), walked every tick. One row per pair of
/ them, because the two venues drift apart the way real ones do.
n_market:(count venues)*count syms
market:([] venue:raze (count syms)#/:venues; sym:n_market#syms; mid:n_market#mid0; toxicity:n_market#0f)

/ The last fill id issued, per venue - monotonic within a process.
last_id:venues!(count venues)#0

/ ----------------------------------------------------------- FILL MODEL

/ A Beta(a;b) draw for integer a and b, with no special function: the a-th
/ smallest of a+b-1 uniforms IS Beta(a;b). Two lines, and exact.
/ @param a the first shape parameter, a positive integer
/ @param b the second, likewise
/ @return one draw in [0;1]
/ @eg (.qpipe.job.crypto_mock.beta_draw[2;2]) within 0 1f -> 1b
beta_draw:{[a;b] (asc (a+b-1)?1f) a-1}

/ A hazard rate over a horizon, as a probability: 1-exp(-hazard*horizon),
/ which is what "expected fills per second at the touch" means once the
/ question is "did it fill this tick".
/ @param hazard fills per second
/ @param horizon seconds
/ @return the probability of at least one fill
/ @eg .qpipe.job.crypto_mock.hazard_to_prob[0;1] -> 0f
hazard_to_prob:{[hazard;horizon] 1-exp neg hazard*horizon}

/ The two sides' fill probabilities under a given toxicity - the exact
/ skew QuoteFillSimulator applies: positive toxicity is buy-heavy flow,
/ which lifts the ASK more; negative is sell-heavy and hits the BID.
/ @param p the unskewed probability
/ @param tox flow toxicity in [-1;1]
/ @return (bid probability; ask probability), each clipped to [0;1]
/ @eg .qpipe.job.crypto_mock.skewed[0.5;0f] -> 0.5 0.5
skewed:{[p;tox]
    f:.qpipe.job.crypto_mock.toxicity_factor;
    1&0|(p*1+f*0|neg tox; p*1+f*0|tox)}

/ Walk one mid: the same symmetric random walk .qsynth uses for FX, +/-5bp.
/ @param m the current mid
/ @return the next
walk:{[m] .qsynth.drift_one m}

/ Drift one toxicity reading: pulled back toward zero and nudged, so it
/ wanders in [-1;1] and changes sign every so often rather than sitting on
/ one side. A stand-in for the flow-derived figure cryptorust computes.
/ @param toxicity the current toxicity
/ @return the next, clipped to [-1;1]
/ @eg (.qpipe.job.crypto_mock.drift_toxicity 0f) within -1 1f -> 1b
drift_toxicity:{[toxicity] 1&-1|(0.9*toxicity)+0.1*-1+2*rand 1f}

/ ------------------------------------------------------------ THE BOOK

/ One tick's crypto_book rows from the current market: a ladder each side,
/ level-0-first, the shape .qbook and the frontend consume.
/ .
/ Takes the market as an argument and returns rows, so a tick's shape is
/ checkable with no tickerplant. Every column is a vector as long as the
/ market has rows; the four ladder columns are lists of vectors.
/ `source_time` is .z.p, one instant for the whole tick: the mock has no
/ venue to be stamped by, and a healthy live feed is one where the venue's
/ stamp and the plant's are milliseconds apart. It is emitted rather than
/ left out because .u.upd takes columns POSITIONALLY - a publisher one column
/ short of its plant table misaligns every column after the gap, silently,
/ and no contract test covers a feed.
/ @param mkt the market table
/ @return the rows: source_time, venue, sym, bid_prices, bid_sizes, ask_prices, ask_sizes
/ @eg count first .qpipe.job.crypto_mock.book_rows[.qpipe.job.crypto_mock.market] 3 -> 3
book_rows:{[mkt]
    n:.qpipe.job.crypto_mock.n_levels;
    unit:.qpipe.job.crypto_mock.quote_size .qpipe.job.crypto_mock.syms?mkt`sym;
    step:.qpipe.job.crypto_mock.level_step*mkt`mid;
    (count[mkt]#.z.p; mkt`venue; mkt`sym;
        .qsynth.levels_one[;;-1;n] .' flip (mkt`mid;step);
        {[u;n] u*1+til n}[;n] each unit;
        .qsynth.levels_one[;;1;n] .' flip (mkt`mid;step);
        {[u;n] u*1+til n}[;n] each unit)}

/ ------------------------------------------------------------ THE FILLS

/ The maker's quote for one market row, as (bid; ask) prices.
/ @param mid the mid
/ @return (bid; ask)
/ @eg .qpipe.job.crypto_mock.touch 10000f -> 9999 10001f
touch:{[mid] mid*(1-.qpipe.job.crypto_mock.half_spread;1+.qpipe.job.crypto_mock.half_spread)}

/ The quote currency of a symbol: what comes after the hyphen.
/ @param s a symbol like `$"BTC-USDT"
/ @return `USDT
/ @eg .qpipe.job.crypto_mock.quote_ccy `$"BTC-USDT" -> `USDT
quote_ccy:{[s] `$last "-" vs string s}

/ One fill's rows, in the recorder's exact wire shape.
/ .
/ Every column is a ONE-ELEMENT VECTOR, never a bare atom - the same shape
/ build_real_upd_message sends, and the shape a tickerplant needs to take a
/ row count from. Fee is size*price*bps, in the quote currency, because
/ that is what a spot venue charges a maker.
/ @param venue the venue
/ @param s the symbol
/ @param side 1 for a buy (the bid filled), -1 for a sell (the ask filled)
/ @param price the fill price
/ @param size the filled size, base units
/ @param id the exchange fill id
/ @return the rows: sym, venue, side, trade_price, size, fee, fee_currency, exchange_fill_id
/ @eg count .qpipe.job.crypto_mock.fill_rows[`binance_spot;`$"BTC-USDT";1;62000f;0.25;`x] -> 8
fill_rows:{[venue;s;side;price;size;id]
    bps:.qpipe.job.crypto_mock.maker_fee_bps .qpipe.job.crypto_mock.venues?venue;
    (enlist s; enlist venue; enlist side; enlist price; enlist size;
        enlist size*price*bps%10000; enlist .qpipe.job.crypto_mock.quote_ccy s; enlist id)}

/ Decide the fills for one market row this tick, given the draws.
/ .
/ The draws are ARGUMENTS - (at touch?; bid uniform; ask uniform; bid
/ fraction; ask fraction) - so this function is deterministic and its
/ decisions can be asserted; on_timer supplies the randomness. Returns a
/ list of (side; price; size) triples, empty when nothing filled.
/ @param row one market row: venue, sym, mid, toxicity
/ @param horizon the tick length in seconds
/ @param draws (at_touch; u_bid; u_ask; frac_bid; frac_ask)
/ @return zero, one or two (side; price; size) triples
/ @eg count .qpipe.job.crypto_mock.decide[first .qpipe.job.crypto_mock.market;1;(1b;0f;0f;0.5;0.5)] -> 2
decide:{[row;horizon;draws]
    if[not draws 0; :()];
    h:.qpipe.job.crypto_mock.hazard_per_s .qpipe.job.crypto_mock.venues?row`venue;
    p:.qpipe.job.crypto_mock.skewed[.qpipe.job.crypto_mock.hazard_to_prob[h;horizon];row`toxicity];
    q:.qpipe.job.crypto_mock.quote_size .qpipe.job.crypto_mock.syms?row`sym;
    bidask:.qpipe.job.crypto_mock.touch row`mid;
    cap:.qpipe.job.crypto_mock.max_fill_fraction;
    out:();
    if[(draws 1)<p 0; out,:enlist (1;bidask 0;q*cap&draws 3)];
    if[(draws 2)<p 1; out,:enlist (-1;bidask 1;q*cap&draws 4)];
    out}

/ Private: the draws for one market row.
draws:{[]
    (rand[1f]<.qpipe.job.crypto_mock.quote_share; rand 1f; rand 1f;
        .qpipe.job.crypto_mock.beta_draw[.qpipe.job.crypto_mock.fill_alpha;.qpipe.job.crypto_mock.fill_beta];
        .qpipe.job.crypto_mock.beta_draw[.qpipe.job.crypto_mock.fill_alpha;.qpipe.job.crypto_mock.fill_beta])}

/ Private: issue the next fill id for a venue - `<venue>-<n>`, unique
/ within a run the way an exchange's are unique within a venue.
next_id:{[venue]
    n:1+.qpipe.job.crypto_mock.last_id venue;
    .qpipe.job.crypto_mock.last_id[venue]:n;
    `$string[venue],"-",string n}

/ Private: publish every fill decided for one market row.
publish_fills:{[horizon;row]
    decided:.qpipe.job.crypto_mock.decide[row;horizon;.qpipe.job.crypto_mock.draws[]];
    {[row;f]
        .qpipe.job.crypto_mock.publish[`crypto_trades;
            .qpipe.job.crypto_mock.fill_rows[row`venue;row`sym;f 0;f 1;f 2;
                .qpipe.job.crypto_mock.next_id row`venue]]}[row] each decided;
    count decided}

/ The tick length, in seconds - the horizon the fill hazards are converted
/ over. Kept beside the declaration's period, which it must match.
tick_seconds:1f

/ Walk the market, publish its book, then the fills against it.
/ .
/ The book goes out first: a fill at a price the book has not yet shown is
/ a fill nobody can explain from the tape.
on_timer:{[]
    `.qpipe.job.crypto_mock.market set update mid:.qpipe.job.crypto_mock.walk each mid,
        toxicity:.qpipe.job.crypto_mock.drift_toxicity each toxicity from .qpipe.job.crypto_mock.market;
    .qpipe.job.crypto_mock.publish[`crypto_book;.qpipe.job.crypto_mock.book_rows .qpipe.job.crypto_mock.market];
    .qpipe.job.crypto_mock.publish_fills[.qpipe.job.crypto_mock.tick_seconds] each .qpipe.job.crypto_mock.market;
    }

\d .

.qetl.job.stream.define[`crypto_mock;`procname`subscribe_to`publishes`period`on_timer`note!(
    `cryptomock1;
    `symbol$();
    `crypto_book`crypto_trades;
    0D00:00:01.000;
    .qpipe.job.crypto_mock.on_timer;
    "stands in for cryptorust's two kdb recorders. startwithall:0: start it INSTEAD of them, never as well as - it publishes onto the same two tables, and an invented ladder or fill must not interleave with a real one")];
