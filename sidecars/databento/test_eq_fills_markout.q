/ test_eq_fills_markout.q - the equity fills (.qpipe.source.eq_fills) scored against
/ the databento equity books (the eq_orderbook fold, mapped onto market_data), #1981.
/ .
/ Both sides are on the venue clock, so the markout core scores one against the other. The
/ books are the databento_mbp10 fixture's, folded by the same transform the live job and the
/ backfill apply, then mapped by .qpipe.job.eq_orderbook.to_market_data.
/ .
/ HAND-WORKED, on 2026-02-25 (EST, so the regular session is [14:30:00, 21:00:00) UTC):
/ .
/   x0001  AAPL buy 100 at 271.60, 14:30:00.020. The fixture's AAPL books: bid 271.45 /
/          ask 271.66 at 14:30:00.016039462, and the same touch at .024566996 - mid 271.555
/          both times. At +5ms the move is 271.555 - 271.60 = -0.045, worth -4.5 USD.
/   x0002  META sell 10 at 643.20, 14:30:00.500. META's book: bid 642 / ask 643 at
/          14:30:00.003676482, mid 642.5, the latest at +5ms (the next is at 14:30:01).
/          The move is -(642.5 - 643.20) = +0.70, worth +7.0 USD.
/   x0003  AAPL sell 100 at 271.50, 20:59:50, books around the close written here (20:59:49 mid
/          271.50, 20:59:54 mid 271.40, 21:00:20 mid 270.10). At +5s the move is +0.10; at +30s the
/          horizon is 21:00:20, after the close, so the pair is unavailable.

.qt.qpipe.source.eq_fills.session:{[]
    ([] sym:enlist `; open_time:enlist 2026.02.25D14:30:00.000000000; close_time:enlist 2026.02.25D21:00:00.000000000)}

/ The fixture's fills as executions: the source's rows through the worker's own transform.
.qt.qpipe.source.eq_fills.executions:{[]
    e:.qetl.transform.apply[`eq_fills_to_executions;enlist[`batch]!enlist .qpipe.source.eq_fills.fixture[]];
    (cols .qpipe.transform.markout_core.executions_in)#e}

/ The databento fixture's books on market_data, in the columns the core reads.
.qt.qpipe.source.eq_fills.books:{[]
    folded:.qpipe.transform.eq_orderbook.to_book .qpipe.source.databento_mbp10.fixture[];
    (cols .qpipe.transform.markout_core.market_data_in)#.qpipe.job.eq_orderbook.to_market_data (cols .qpipe.job.eq_orderbook.book_in)#folded}

.qt.qpipe.source.eq_fills.close_book:{[tm;bid;ask]
    ([] source_time:enlist tm; sym:enlist `AAPL; source:enlist `XNAS; bid:enlist bid; ask:enlist ask; clock:enlist `venue)}

.qt.qpipe.source.eq_fills.score:{[horizons;books]
    spec:`horizons`report_ccy`rate_timing`max_age`sessions!(horizons;`USD;`execution;0D00:00:02;.qt.qpipe.source.eq_fills.session[]);
    .qpipe.transform.markout_core.score[.qpipe.transform.eq_domain.domain;spec;.qt.qpipe.source.eq_fills.executions[];books]}

.qt.qpipe.source.eq_fills.at:{[r;id;knd;hz;col]
    first (r col) where ((r`fill_id)=id) & ((r`kind)=knd) & (r`horizon)=hz}

.qt.qpipe.source.eq_fills.test_the_books_and_the_fills_share_a_clock:{[t]
    .qunit.assertEquals[distinct (.qt.qpipe.source.eq_fills.books[])`clock;enlist `venue;"the databento books are venue-clocked"];
    .qunit.assertEquals[distinct (.qt.qpipe.source.eq_fills.executions[])`clock;enlist `venue;"and so are the declared fills"]};

.qt.qpipe.source.eq_fills.test_a_fill_is_scored_against_the_databento_books:{[t]
    r:.qt.qpipe.source.eq_fills.score[enlist 0D00:00:00.005;.qt.qpipe.source.eq_fills.books[]];
    at:.qt.qpipe.source.eq_fills.at[r];
    .qunit.assertEquals[at[`x0001;`total;0D00:00:00.005;`status];`ok;"AAPL buy, +5ms"];
    .qunit.assertTrue[1e-9>abs at[`x0001;`total;0D00:00:00.005;`move]+0.045;"271.555 - 271.60"];
    .qunit.assertTrue[1e-9>abs at[`x0001;`total;0D00:00:00.005;`report_amount]+4.5;"times 100 shares"];
    .qunit.assertEquals[at[`x0002;`total;0D00:00:00.005;`status];`ok;"META sell, +5ms"];
    .qunit.assertTrue[1e-9>abs at[`x0002;`total;0D00:00:00.005;`move]-0.7;"-(642.5 - 643.20)"];
    .qunit.assertTrue[1e-9>abs at[`x0002;`total;0D00:00:00.005;`report_amount]-7;"times 10 shares"]};

.qt.qpipe.source.eq_fills.test_a_horizon_after_the_close_is_unavailable_whatever_the_books_say:{[t]
    c:.qt.qpipe.source.eq_fills.close_book;
    books:raze (c[2026.02.25D20:59:49.000000000;271.4;271.6];c[2026.02.25D20:59:54.000000000;271.3;271.5];c[2026.02.25D21:00:20.000000000;270f;270.2]);
    r:.qt.qpipe.source.eq_fills.score[0D00:00:05 0D00:00:30;books];
    at:.qt.qpipe.source.eq_fills.at[r];
    .qunit.assertEquals[at[`x0003;`total;0D00:00:05;`status];`ok;"+5s is 20:59:55, inside the session"];
    .qunit.assertTrue[1e-9>abs at[`x0003;`total;0D00:00:05;`move]-0.1;"the :54 mid 271.40 against the 271.50 fill: +0.10 for a sell"];
    .qunit.assertEquals[at[`x0003;`total;0D00:00:30;`status];`unavailable;"+30s is 21:00:20, after the close"];
    .qunit.assertTrue[null at[`x0003;`total;0D00:00:30;`move];"no overnight gap is scored as execution quality"]};
