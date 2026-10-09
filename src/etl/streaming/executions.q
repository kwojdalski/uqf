/ executions.q - the `executions` normalizer: every fill table the stack
/ carries, published as one (.qpipe.job.executions).
/ .
/ Named `executions` and not `fills` because `fills` is a q builtin - the
/ forward-fill verb - and a table by that name on the plant would shadow it
/ in every process that holds the table, the RDB and the HDB included.
/ .
/ Three tables carry the same fact in three shapes. `trades` is the FX fill -
/ time, sym, side, trade_price, size, pip_factor - `crypto_trades` is
/ cryptorust's - the same five plus venue, fee, fee_currency and
/ exchange_fill_id - and `orders` is the desk's order flow, whose FILLED rows
/ are fills with a book and a product. A position book does not care which
/ market a fill came from, and until this file existed it had to: posbook
/ subscribed to one and crypto_posbook to the other, running one transform
/ two ways, and fxpositions1 read `orders` on its own (#885).
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
/   fill_id      the venue's own id where there is one, null otherwise; an
/                order's id for a filled order
/   book         the desk book a fill is booked to, null where the source
/                has none - only `orders` carries it
/   product      likewise: spot, forward, ... for a desk fill
/ .
/ Nothing is DROPPED that a downstream job reads: pip_factor is not here
/ because nothing downstream of a fill reads it - .qexec's markout family
/ takes it off `trades` directly, and stays subscribed there.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qpipe.job.executions

/ Where rows go. A stub until .qetl.job.stream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `executions;

/ The canonical output. No `time`: the plant stamps it.
executions:.qetl.plant.published `executions

/ What each mapping reads - the source table as the plant delivers it.
trades:.qetl.plant.shape `trades
crypto_trades:.qetl.plant.shape `crypto_trades
orders:.qetl.plant.shape `orders

/ The status that makes an order a fill. One value, named once: an order
/ cancelled or rejected never traded, and netting one into a position is
/ wrong in a way that looks like nothing until someone reconciles.
filled_status:`filled

/ The venue an FX fill is attributed to. The demo's FX feed is one venue,
/ and naming it is what lets a downstream group by venue without a null
/ standing in for "the FX one".
fx_venue:`fx

/ An FX fill as a canonical execution: the venue is fixed, there is no fee and
/ no id.
/ @param batch a trades batch
/ @return canonical executions
from_trades:{[batch]
    select source_time:time, sym, venue:.qpipe.job.executions.fx_venue, side, size, price:trade_price,
        fee:0f, fee_ccy:`, fill_id:`, book:`, product:` from batch}

/ A crypto fill as a canonical execution: a rename, because the recorder's shape
/ is already the canonical one with different spellings.
/ @param batch a crypto_trades batch
/ @return canonical executions
from_crypto_trades:{[batch]
    select source_time:time, sym, venue, side, size, price:trade_price,
        fee, fee_ccy:fee_currency, fill_id:exchange_fill_id, book:`, product:` from batch}

/ A filled order as a canonical execution, booked to its book and product.
/ The orders feed is the FX desk's, so its venue is the FX one; the order's
/ id is the fill's. Every other status is dropped: it never traded.
/ @param batch an orders batch, any statuses
/ @return canonical executions, filled orders only
from_orders:{[batch]
    fs:.qpipe.job.executions.filled_status;
    select source_time:time, sym, venue:.qpipe.job.executions.fx_venue, side, size, price,
        fee:0f, fee_ccy:`, fill_id:`$string order_id, book, product from batch where order_status=fs}

\d .

.qetl.transform.define[`executions_from_trades;`inputs`output`fn`examples!(
    (enlist `trades)!enlist .qpipe.job.executions.trades;
    .qpipe.job.executions.executions;
    .qpipe.job.executions.from_trades;
    enlist `inputs`expected!(
        (enlist `trades)!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01;
            sym:`EURUSD`USDJPY; side:1 -1; trade_price:1.085 149.5; size:1e6 5e5;
            pip_factor:.qccy.pip_factor `EURUSD`USDJPY);
        ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:01; sym:`EURUSD`USDJPY;
            venue:`fx`fx; side:1 -1; size:1e6 5e5; price:1.085 149.5; fee:0 0f;
            fee_ccy:``; fill_id:``; book:``; product:``)))];

.qetl.transform.define[`executions_from_crypto_trades;`inputs`output`fn`examples!(
    (enlist `crypto_trades)!enlist .qpipe.job.executions.crypto_trades;
    .qpipe.job.executions.executions;
    .qpipe.job.executions.from_crypto_trades;
    enlist `inputs`expected!(
        (enlist `crypto_trades)!enlist ([] time:enlist 2026.09.17D10:00:02;
            sym:enlist `$"BTC-USDT"; venue:enlist `binance_spot; side:enlist -1;
            trade_price:enlist 62000f; size:enlist 0.25; fee:enlist 15.5;
            fee_currency:enlist `USDT; exchange_fill_id:enlist `$"binance_spot-1");
        ([] source_time:enlist 2026.09.17D10:00:02; sym:enlist `$"BTC-USDT";
            venue:enlist `binance_spot; side:enlist -1; size:enlist 0.25;
            price:enlist 62000f; fee:enlist 15.5; fee_ccy:enlist `USDT;
            fill_id:enlist `$"binance_spot-1"; book:enlist `; product:enlist `)))];

/ A cancelled order is dropped; the filled ones keep their book and product.
.qetl.transform.define[`executions_from_orders;`inputs`output`fn`examples!(
    (enlist `orders)!enlist .qpipe.job.executions.orders;
    .qpipe.job.executions.executions;
    .qpipe.job.executions.from_orders;
    enlist `inputs`expected!(
        (enlist `orders)!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:02;
            order_id:1 2 3; sym:`EURUSD`EURUSD`USDJPY; book:`london`london`newyork;
            product:`spot`spot`fwd; side:1 -1 1; size:1e6 4e5 5e5; price:1.085 1.086 149.5;
            order_status:`filled`cancelled`filled);
        ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:02; sym:`EURUSD`USDJPY;
            venue:`fx`fx; side:1 1; size:1e6 5e5; price:1.085 149.5; fee:0 0f;
            fee_ccy:``; fill_id:`1`3; book:`london`newyork; product:`spot`fwd)))];

.qetl.job.stream.normalize[`executions;`procname`output`input`start_with_all`note!(
    `executions1;
    .qpipe.job.executions.executions;
    `trades`crypto_trades`orders!`executions_from_trades`executions_from_crypto_trades`executions_from_orders;
    1b;
    "every fill as one tape: trades, crypto_trades and filled orders -> executions")];
