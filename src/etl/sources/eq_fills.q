/ eq_fills.q - equity fills from a csv drop (.qpipe.source.eq_fills, #1981).
/ .
/ The bundled instance of the generic fills source (src/etl/core/source_fills.q):
/ a file of executions from a broker or a venue's drop copy, declared by the mapping
/ below and read as the canonical shape `executions` carries. NO REAL EQUITY SOURCE
/ EXISTS YET. The column names here (exec_time, exchange, exec_id, ticker, buy_sell,
/ qty, px, commission) are a plausible analogue, not a vendor's; pointing this at real
/ data is a change to the mapping, the side encoding and the clock, and nothing else.
/ A site whose fills sit in a database declares its own source over the odbc transport
/ the same way (.qetl.source.fills.declare, transport `odbc) and a worker over it.
/ .
/ Without UQF_SOURCE_CRED_EQ_FILLS (the path of the file) the worker runs on `fixture`,
/ the same rows as tests/q/data/eq_fills.csv, which a test holds to be the file's parse.
/ .
/ THE CLOCK IS `venue`: the exchange's own execution time, which is the clock the
/ databento equity books (eq_orderbook, market_data) are on, so these fills can be scored
/ against them. A drop whose time is when the broker received the fill declares `receipt
/ and the markout core will refuse to score it against those books.

\d .

.qpipe.source.eq_fills.source_name:`eq_fills

/ The mapping: canonical field -> this file's column.
.qpipe.source.eq_fills.columns_map:`venue`fill_id`sym`side`size`price`source_time`fee`fee_ccy!`exchange`exec_id`ticker`buy_sell`qty`px`exec_time`commission`commission_ccy

/ The side encoding: this file spells a buy B and a sell S.
.qpipe.source.eq_fills.sides:`B`S!1 -1

/ The credential is the path of the file.
.qpipe.source.eq_fills.credential_example:"/data/drops/equity_fills.csv"

/ The declared parts of the source (name, table, window column, row key, columns, types, zone, transport, raw).
.qpipe.source.eq_fills.declared:.qetl.source.fills.declare[.qpipe.source.eq_fills.source_name;
    `transport`table_name`columns`sides`clock`tz!(`csv;`fills;.qpipe.source.eq_fills.columns_map;.qpipe.source.eq_fills.sides;`venue;`UTC)]

/ One window of fills from the file.
/ @param h the file, from the csv transport's open
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the fills in the window, in the contract's shape
.qpipe.source.eq_fills.query:{[h;range_from;range_to] .qetl.source.fills.read[`eq_fills;h;range_from;range_to]}

/ Five equity fills over two days: the rows of tests/q/data/eq_fills.csv as the contract
/ types them, in the order a window returns them.
/ @return the fills
/ @eg count .qpipe.source.eq_fills.fixture[]  ->  5
.qpipe.source.eq_fills.fixture:{[]
    ([] source_time:2026.02.25D14:30:00.020000000 2026.02.25D14:30:00.500000000 2026.02.25D20:59:50.000000000 2026.02.25D20:59:55.000000000 2026.02.26D14:31:00.000000000;
        venue:5#`XNAS;
        fill_id:`x0001`x0002`x0003`x0004`x0005;
        sym:`AAPL`META`AAPL`META`AAPL;
        side:1 -1 -1 1 1;
        size:100 10 100 5 50f;
        price:271.6 643.2 271.5 643 272f;
        fee:0.35 0.1 0.35 0.05 0.18;
        fee_ccy:5#`USD;
        clock:5#`venue)}

.qetl.source.define[.qpipe.source.eq_fills.source_name;
    .qpipe.source.eq_fills.declared,`query`fixture`credential_example!(.qpipe.source.eq_fills.query;.qpipe.source.eq_fills.fixture;.qpipe.source.eq_fills.credential_example)];

\d .
