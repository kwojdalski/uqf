/ microstructure_venues.q - the reference price across venues (.qmicro, #886).
/ .
/ Split out of microstructure.q by concern, as #970 split source_contract.q:
/ the namespace is still .qmicro and no public name changed. This is the one
/ crypto reference crypto_markout, posbook and last_value price from.

\d .qmicro

/ ------------------------------------------- A REFERENCE PRICE ACROSS VENUES

/ The oldest a venue's top of book may be and still set a cross-venue
/ reference price: one policy for every job that prices against the market
/ rather than one venue - crypto_markout's markouts and posbook's crypto
/ marks (#886). Five seconds, superbook's expiry window for FX.
reference_max_age:0D00:00:05

/ The best mid across venues at each target (sym; time).
/ .
/ Per venue, the latest top of book at or before the target (aj); dropped
/ when older than max_age. Then the highest bid and the lowest ask over the
/ venues left. Null when either side has no live venue - a venue that went
/ quiet must not set the best price.
/ @param tob top of book rows: table time, sym, venue, bid, ask; a side the
/   venue did not quote is null
/ @param targets table sym, time - the instants to price
/ @param max_age the oldest a venue's book may be and still count
/ @return a float vector aligned with targets
/ @eg .qmicro.best_mid_across_venues[([] time:2026.09.17D10:00:00 2026.09.17D10:00:00; sym:2#`$"BTC-USDT"; venue:`a`b; bid:62000 62004f; ask:62010 62008f);([] sym:enlist `$"BTC-USDT"; time:enlist 2026.09.17D10:00:01);0D00:00:05]  ->  ,62006f
best_mid_across_venues:{[tob;targets;max_age] (best_across_venues[tob;targets;max_age])`mid}

/ The best bid, best ask and their mid across venues at each target - the
/ quote best_mid_across_venues prices from, for a consumer that publishes
/ the sides too (last_value, #990). Same rules: per venue the latest top at
/ or before the target, dropped when older than max_age; a side with no
/ live venue is null, and so is the mid.
/ @param tob top of book rows: table time, sym, venue, bid, ask
/ @param targets table sym, time - the instants to price
/ @param max_age the oldest a venue's book may be and still count
/ @return a table bid, ask, mid aligned with targets
/ @eg .qmicro.best_across_venues[([] time:2026.09.17D10:00:00 2026.09.17D10:00:00; sym:2#`$"BTC-USDT"; venue:`a`b; bid:62000 62004f; ask:62010 62008f);([] sym:enlist `$"BTC-USDT"; time:enlist 2026.09.17D10:00:01);0D00:00:05]  ->  ([] bid:enlist 62004f; ask:enlist 62008f; mid:enlist 62006f)
best_across_venues:{[tob;targets;max_age]
    / nothing to price, or nothing to price from: no aj (PeachQ refuses one
    / with no targets against a non-empty book - #990)
    if[(0=count tob) or 0=count targets; :([] bid:(count targets)#0n; ask:(count targets)#0n; mid:(count targets)#0n)];
    tob:`sym`time xasc tob;
    per_venue:{[tob;targets;max_age;v]
        j:aj[`sym`time;targets;select sym, time, bid, ask, quoted:time from tob where venue=v];
        live:(not null j`quoted) & max_age>=(j`time)-j`quoted;
        (?[live;j`bid;0n];?[live;j`ask;0n])}[tob;targets;max_age] each distinct tob`venue;
    / null is the smallest float, so it never wins a max but always wins a
    / min: asks are filled high before taking the lowest, then put back
    best_bid:max per_venue[;0];
    best_ask:min 0w^per_venue[;1];
    best_ask:?[best_ask=0w;0n;best_ask];
    mid:0.5*best_bid+best_ask;
    ([] bid:best_bid; ask:best_ask; mid:?[null best_bid;0n;mid])}
\d .
