/ crypto_markout.q - the crypto domain module of the markout contract (.qpipe.transform.crypto_markout, #1934).
/ .
/ WHAT THIS IS. What the markout core (markout_core.q, #1929) needs to know
/ about a CRYPTO instrument and nothing else: a domain dictionary (resolve,
/ convert, keys) and the crypto presentation measures (bps, and bps net of the
/ fee). It owns no queue, no clock rule, no horizon expansion and no dataset:
/ those are the core's, and the dataset and job are #1936.
/ .
/ THE RESOLVER, per instrument (the contract's outputs):
/ .
/   ok         the symbol parses as BASE-QUOTE
/   quote_ccy  the QUOTE half, parsed from the symbol: BTC-USDT gives USDT
/   legs       the market_data syms the reference is read from: the one sym
/              itself, crypto being single-leg
/   invert     one flag a leg, always false: a venue's book is never inverted
/ .
/ A symbol that is not BASE-QUOTE is not `ok`: the core excludes it as
/ `no_route`, rather than guessing a currency. The resolver is pure - it reads
/ the symbol and never which books exist.
/ .
/ THE REFERENCE is the core's: the best mid across venues within max_age,
/ built by .qf.mkt.best_across_venues on the execution's own clock. This module
/ does not restate it; a single leg with no inversion is exactly that mid.
/ .
/ THE CONVERSION. A quote currency is worth report_ccy through the market_data
/ sym QUOTE-REPORT (USDT-USD), never inverted, when a spec's rates names a
/ source for it. A fixed rate (USDT = 1 USD) is the spec's, never assumed here.
/ .
/ THE MEASURES, domain columns on the core's rows. move is the core's
/ side*(ref-price).
/ .
/   markout_bps      10000*move%price: positive when the market moved in the
/                    fill's favour. crypto_markout's own figure.
/   fee_quote        the fee in the QUOTE currency. A fee is a cost when
/                    positive and a rebate when negative. In the quote currency
/                    it is taken as is; in the BASE currency it is fee*price,
/                    valued at the fill. A zero fee is zero in any currency. Any
/                    other fee_ccy (an exchange token, say) cannot be valued
/                    without a rate this module does not have, so it is null
/                    rather than an under-count.
/   fee_bps          10000*fee_quote%(size*price): the fee as a share of notional
/   markout_net_bps  on `total rows, markout_bps - fee_bps: what the fill earned
/                    after the fee. Null whenever fee_bps is, so an unvalued fee
/                    never reads as a free one, and null on every other component
/                    (a fee is charged once per fill, not once per component).
/ .
/ Clocks are the core's to enforce; the examples in tests/q/test_crypto_domain.q
/ carry `clock` on both inputs.

\d .

/ ------------------------------------------------------------ THE RESOLVER

/ A crypto symbol split into its base and quote currencies. Exactly one "-"
/ with both sides non-empty is BASE-QUOTE; anything else has neither.
/ @param syms symbol list
/ @return table base, quote aligned with syms; both null where the symbol is not BASE-QUOTE
/ @eg exec quote from .qpipe.transform.crypto_markout.split_sym `$("BTC-USDT";"BTCUSDT")  ->  `USDT`
.qpipe.transform.crypto_markout.split_sym:{[syms]
    if[0=count syms; :([] base:`symbol$(); quote:`symbol$())];
    parts:{"-" vs string x} each syms;
    good:{(2=count x) and all 0<count each x} each parts;
    base:?[good;`$first each parts;`];
    quote:?[good;`$last each parts;`];
    ([] base:base; quote:quote)}

/ The currency a crypto symbol is quoted in, parsed from the symbol.
/ @param syms symbol list
/ @return symbol list aligned with syms; null where the symbol is not BASE-QUOTE
/ @eg .qpipe.transform.crypto_markout.quote_ccy `$("BTC-USDT";"ETH-BTC")  ->  `USDT`BTC
.qpipe.transform.crypto_markout.quote_ccy:{[syms] (.qpipe.transform.crypto_markout.split_sym[syms])`quote}

/ The resolver contract for crypto: how each instrument is priced.
/ @param spec the markout spec, a dictionary; crypto declares no key of its own yet
/ @param syms the distinct instruments being scored
/ @return table sym, ok, quote_ccy, legs, invert, one row per sym, in order
/ @throws error when spec is not a dictionary
/ @eg exec ok from .qpipe.transform.crypto_markout.resolve[()!();`$("BTC-USDT";"junk")]  ->  10b
.qpipe.transform.crypto_markout.resolve:{[spec;syms]
    / typed, whatever the caller handed over: a join with () is general on PeachQ, and the core matches syms exactly
    syms:`symbol$syms,();
    if[not 99h=type spec; '"crypto_markout: spec must be a dictionary"];
    q:.qpipe.transform.crypto_markout.quote_ccy[syms];
    ([] sym:syms; ok:not null q; quote_ccy:q; legs:enlist each syms; invert:(count syms)#enlist enlist 0b)}

/ ----------------------------------------------------------- THE MEASURES

/ The fee in the quote currency (see the header for the cases).
/ @param sym instrument symbols, BASE-QUOTE
/ @param price fill prices
/ @param fee fees as the executions carry them
/ @param fee_ccy the currency each fee is in
/ @return float vector; null where the fee is null or cannot be valued
/ @eg .qpipe.transform.crypto_markout.fee_quote[enlist `$"BTC-USDT";enlist 62000f;enlist 0.0001;enlist `BTC]  ->  ,6.2
.qpipe.transform.crypto_markout.fee_quote:{[sym;price;fee;fee_ccy]
    s:.qpipe.transform.crypto_markout.split_sym[sym,()];
    inq:fee_ccy=s`quote;
    inb:fee_ccy=s`base;
    ?[null fee;0n;?[0f=fee;0f;?[inq;fee;?[inb;fee*price;0n]]]]}

/ The conversion leg of each quote currency: the market_data sym QUOTE-REPORT,
/ never inverted. A quote currency equal to the reporting one needs none, and
/ the core never asks.
/ @param spec the spec
/ @param quote_ccys symbol list
/ @param report_ccy the reporting currency
/ @return table ok, leg, invert, one row per quote currency
/ @eg .qpipe.transform.crypto_markout.convert[()!();enlist `USDT;`USD]`leg  ->  enlist `$"USDT-USD"
.qpipe.transform.crypto_markout.convert:{[spec;quote_ccys;report_ccy]
    if[not 99h=type spec; '"crypto_markout: spec must be a dictionary"];
    ok:(not null quote_ccys) and not null report_ccy;
    leg:?[ok;`$(string quote_ccys),\:"-",string report_ccy;`];
    ([] ok:ok; leg:leg; invert:(count quote_ccys)#0b)}

/ The crypto measures for the core's rows: markout_bps, fee_quote, fee_bps and
/ markout_net_bps (see the header), joined to the fills' fees by (venue; fill_id).
/ @param rows the core's result rows
/ @param fees table venue, fill_id, fee, fee_ccy
/ @return the rows with markout_bps, fee_quote, fee_bps, markout_net_bps appended
/ @eg cols .qpipe.transform.crypto_markout.measures[0#.qpipe.transform.markout_core.result;([] venue:`symbol$(); fill_id:`symbol$(); fee:`float$(); fee_ccy:`symbol$())]  ->  (cols .qpipe.transform.markout_core.result),`markout_bps`fee_quote`fee_bps`markout_net_bps
.qpipe.transform.crypto_markout.measures:{[rows;fees]
    / the empty case joins the column dictionaries: `,'` of two empty tables is () on PeachQ
    if[0=count rows; :flip (flip rows),flip ([] markout_bps:`float$(); fee_quote:`float$(); fee_bps:`float$(); markout_net_bps:`float$())];
    f:2!select venue, fill_id, fee, fee_ccy from fees;
    j:f (rows`venue),'rows`fill_id;
    fq:.qpipe.transform.crypto_markout.fee_quote[rows`sym;rows`price;j`fee;j`fee_ccy];
    bps:10000*(rows`move)%rows`price;
    fbps:10000*fq%(rows`size)*rows`price;
    net:?[(rows`kind)=`total;bps-fbps;0n];
    rows,'([] markout_bps:bps; fee_quote:fq; fee_bps:fbps; markout_net_bps:net)}

/ Score executions through the core under the crypto domain, with the crypto
/ measures appended.
/ @param spec the markout spec (horizons, report_ccy, rate_timing, optionally max_age and rates)
/ @param exe executions: the core's columns and fee, fee_ccy
/ @param md market_data, with clock
/ @return the core's rows, then markout_bps, fee_quote, fee_bps, markout_net_bps
/ @throws error when the spec is refused by the core
/ @eg count .qpipe.transform.crypto_markout.score[`horizons`report_ccy`rate_timing!(enlist 0D00:00:10;`USD;`execution);([] source_time:`timestamp$(); venue:`symbol$(); sym:`symbol$(); side:`long$(); size:`float$(); price:`float$(); fill_id:`symbol$(); clock:`symbol$(); fee:`float$(); fee_ccy:`symbol$());0#.qpipe.transform.markout_core.market_data_in]  ->  0
.qpipe.transform.crypto_markout.score:{[spec;exe;md]
    r:.qpipe.transform.markout_core.calc[.qpipe.transform.crypto_markout.domain;spec;exe;md];
    .qpipe.transform.crypto_markout.measures[r;select venue, fill_id, fee, fee_ccy from exe]}

/ The crypto domain, as the core takes it (the way .qpipe.transform.fx_domain.domain
/ is): resolve, convert and no spec keys of its own. A fee-adjusted measure is a
/ presentation column, so there is no attribute and no availability rule.
/ revision is the rules that price a pair - BASE-QUOTE parsed from the symbol, the
/ conversion pair BASE-REPORT - recorded by the configuration snapshot (#1999); bump
/ it with a change to resolve or convert.
.qpipe.transform.crypto_markout.revision:`crypto_r1

.qpipe.transform.crypto_markout.domain:`resolve`convert`keys`name`revision`datasets!(.qpipe.transform.crypto_markout.resolve;.qpipe.transform.crypto_markout.convert;`symbol$();`crypto;.qpipe.transform.crypto_markout.revision;enlist `crypto_markout)

\d .
