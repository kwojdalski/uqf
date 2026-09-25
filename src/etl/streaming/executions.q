/ executions.q - the `executions` normalizer: every fill table the stack
/ carries, published as one (.qsub.executions).
/ .
/ Named `executions` and not `fills` because `fills` is a q builtin - the
/ forward-fill verb - and a table by that name on the plant would shadow it
/ in every process that holds the table, the RDB and the HDB included.
/ .
/ Two tables carry the same fact in two shapes. `trades` is the FX fill -
/ time, sym, side, trade_price, size, pip_factor - and `crypto_trades` is
/ cryptorust's - the same five plus venue, fee, fee_currency and
/ exchange_fill_id. A position book does not care which market a fill came
/ from, and until this file existed it had to: posbook subscribed to one
/ and crypto_posbook to the other, running one transform two ways.
/ .
/ THE CANONICAL EXECUTION. The columns every fill has, and the columns a market
/ may have that the others get as nulls:
/ .
/   source_time  when the source stamped it. NOT `time`: the plant stamps
/                that on the way out, so a normalized fill has two
/                timestamps - when it happened, and when it was normalized
/                - and conflating them would make every FX fill look like
/                it printed the instant it was reshaped.
/   sym          the instrument, as its own market spells it
/   venue        `fx for the FX feed, the exchange for crypto
/   side         +1 buy / -1 sell, the convention every fill table here has
/   size         base units
/   price        the fill price, in quote units
/   fee          what the venue charged, in fee_ccy. Zero for FX, whose
/                feed prices the spread into the fill instead.
/   fee_ccy      null where there is no fee
/   fill_id      the venue's own id where there is one, null otherwise
/ .
/ Nothing is DROPPED that a downstream job reads: pip_factor is not here
/ because nothing downstream of a fill reads it - .qexec's markout family
/ takes it off `trades` directly, and stays subscribed there.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qsub.executions

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `executions;

/ The canonical output. No `time`: the plant stamps it.
executions:([] source_time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); side:`long$();
    size:`float$(); price:`float$(); fee:`float$(); fee_ccy:`symbol$(); fill_id:`symbol$())

/ What each mapping reads - the source table as the plant delivers it.
trades:([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$();
    size:`float$(); pip_factor:`long$())
crypto_trades:([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); side:`long$();
    trade_price:`float$(); size:`float$(); fee:`float$(); fee_currency:`symbol$();
    exchange_fill_id:`symbol$())

/ The venue an FX fill is attributed to. The demo's FX feed is one venue,
/ and naming it is what lets a downstream group by venue without a null
/ standing in for "the FX one".
fx_venue:`fx

/ An FX fill as a canonical execution: the venue is fixed, there is no fee and
/ no id.
/ @param batch a trades batch
/ @return canonical executions
from_trades:{[batch]
    select source_time:time, sym, venue:.qsub.executions.fx_venue, side, size, price:trade_price,
        fee:0f, fee_ccy:`, fill_id:` from batch}

/ A crypto fill as a canonical execution: a rename, because the recorder's shape
/ is already the canonical one with different spellings.
/ @param batch a crypto_trades batch
/ @return canonical executions
from_crypto_trades:{[batch]
    select source_time:time, sym, venue, side, size, price:trade_price,
        fee, fee_ccy:fee_currency, fill_id:exchange_fill_id from batch}

\d .

.qxf.define[`executions_from_trades;`inputs`output`fn`examples!(
    (enlist `trades)!enlist .qsub.executions.trades;
    .qsub.executions.executions;
    .qsub.executions.from_trades;
    enlist `inputs`expected!(
        (enlist `trades)!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01;
            sym:`EURUSD`USDJPY; side:1 -1; trade_price:1.085 149.5; size:1e6 5e5;
            pip_factor:10000 100);
        ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:01; sym:`EURUSD`USDJPY;
            venue:`fx`fx; side:1 -1; size:1e6 5e5; price:1.085 149.5; fee:0 0f;
            fee_ccy:``; fill_id:``)))];

.qxf.define[`executions_from_crypto_trades;`inputs`output`fn`examples!(
    (enlist `crypto_trades)!enlist .qsub.executions.crypto_trades;
    .qsub.executions.executions;
    .qsub.executions.from_crypto_trades;
    enlist `inputs`expected!(
        (enlist `crypto_trades)!enlist ([] time:enlist 2026.09.17D10:00:02;
            sym:enlist `$"BTC-USDT"; venue:enlist `binance_spot; side:enlist -1;
            trade_price:enlist 62000f; size:enlist 0.25; fee:enlist 15.5;
            fee_currency:enlist `USDT; exchange_fill_id:enlist `$"binance_spot-1");
        ([] source_time:enlist 2026.09.17D10:00:02; sym:enlist `$"BTC-USDT";
            venue:enlist `binance_spot; side:enlist -1; size:enlist 0.25;
            price:enlist 62000f; fee:enlist 15.5; fee_ccy:enlist `USDT;
            fill_id:enlist `$"binance_spot-1")))];

.qnorm.define[`executions;`procname`output`input`startwithall`note!(
    `executions1;
    .qsub.executions.executions;
    `trades`crypto_trades!`executions_from_trades`executions_from_crypto_trades;
    1b;
    "every fill table as one: trades and crypto_trades -> executions")];
