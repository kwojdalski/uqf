/ venue_market.q - which market (asset class) an execution belongs to
/ (.qetl.venue, #2650).
/ .
/ `executions` is one tape for every market (executions.q), and each markout
/ dataset scores only its own market's fills. Nothing used to say which were
/ whose: the equity dataset named its venue list, and the crypto dataset
/ subtracted the equity dataset's list in three places - so a crypto process
/ loaded the equity files to be told what is not crypto. The answer belongs to
/ neither dataset, so it is here, in the core every process loads.
/ .
/ A venue belongs to exactly one market:
/ .
/   equity   a closed list - the exchanges the equity feed fills on
/   fx       a closed list - the FX feed's one venue
/   crypto   OPEN: any venue the recorder trades on (binance_spot, ...), so
/            crypto is whatever the closed lists do not claim
/ .
/ Closed markets list their venues and the open one is the remainder, because a
/ new crypto exchange is a recorder change and must not need an edit here,
/ while a new equity exchange changes what is scored and must. A venue added to
/ a closed list is therefore never also crypto.
/ .
/ WHAT WOULD OVERTURN THIS. `market_of` is a function of the venue, so it
/ cannot tell two markets apart on one venue. The day a venue carries two
/ (a broker routing equity and crypto), the answer is a `market` column on
/ `executions`, stamped by its normalizer as market_data already is (#886) and
/ added to the HDB partitions by scripts/hdb/hdb_fill.q - and
/ this file's `market_of` is then the rule that stamps it and fills the history.
/ .
/ Loaded by src/etl/init.q ahead of every declaration; touches nothing else.

\d .

/ The closed markets and their venues. The one place an equity or FX venue is named.
.qetl.venue.closed:`equity`fx!(enlist `XNAS;enlist `fx)

/ The market of every venue the closed lists do not claim.
.qetl.venue.open:`crypto

/ Every market there is.
.qetl.venue.markets:(key .qetl.venue.closed),.qetl.venue.open

/ The market of each venue.
/ @param venues a symbol list of venues, as `executions`venue` (a book's `source` is the same name)
/ @return a symbol list, one market each
/ @eg .qetl.venue.market_of `XNAS`fx`binance_spot  ->  `equity`fx`crypto
.qetl.venue.market_of:{[venues]
    names:raze {count[y]#x}'[key .qetl.venue.closed;value .qetl.venue.closed];
    claimed:raze value .qetl.venue.closed;
    (names,.qetl.venue.open) claimed?venues}

/ Which of some venues are a market's.
/ @param market equity, fx or crypto
/ @param venues a symbol list of venues
/ @return a boolean per venue
/ @throws error when the market is not one of .qetl.venue.markets
/ @eg .qetl.venue.in_market[`crypto;`XNAS`binance_spot]  ->  01b
.qetl.venue.in_market:{[market;venues]
    if[not market in .qetl.venue.markets; '"venue: no market ",string[market],"; the markets are ",", " sv string .qetl.venue.markets];
    market=.qetl.venue.market_of[venues]}

/ The venues of a CLOSED market, for a reader that has to enumerate them.
/ @param market equity or fx
/ @return a symbol list
/ @throws error when the market is open (crypto has no list) or unknown
/ @eg .qetl.venue.venues_of `equity  ->  enlist `XNAS
.qetl.venue.venues_of:{[market]
    if[market~.qetl.venue.open; '"venue: ",string[market]," is open: it is every venue the closed markets do not claim, and has no list"];
    if[not market in key .qetl.venue.closed; '"venue: no market ",string[market],"; the markets are ",", " sv string .qetl.venue.markets];
    .qetl.venue.closed market}

\d .
