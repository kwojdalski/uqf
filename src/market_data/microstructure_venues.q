/ microstructure_venues.q - the reference price across venues (.qmicro, #886).
/ .
/ Split out of microstructure.q by concern, as #970 split source_contract.q:
/ the namespace is still .qmicro and no public name changed. This is the one
/ crypto reference crypto_markout, posbook and last_value price from.

\d .qmicro

/ ------------------------------------------------- HOW A MARKET IS PRICED

/ How each market in market_data is priced (#998): on its own book's level-0
/ mid, or across venues - the best bid and ask over live venues. One rule per
/ market, read by every consumer that prices from market_data (posbook,
/ last_value), so a market added without one is refused by name rather than
/ falling into whichever branch each consumer wrote.
price_rules:`fx`crypto!`own_book`across_venues

/ The pricing rule for each market.
/ @param markets symbol list
/ @return the rule for each, aligned
/ @throws when a market has no rule, naming it
/ @eg .qmicro.price_rule `fx`crypto`fx  ->  `own_book`across_venues`own_book
price_rule:{[markets]
    u:distinct markets except key price_rules;
    if[count u; '"price_rule: no pricing rule for market ",(", " sv string u)," - add it to .qmicro.price_rules"];
    price_rules markets}

/ Each venue's top of book, for the markets priced across venues: the venue
/ is the book's source and its time the venue's source_time. A side with no
/ usable level (.qbook.top_sides) is null and the top is still kept: the
/ venue withdrew that side, so it must stop setting a price on it rather
/ than leave its previous quote counting.
/ @param x market_data rows
/ @return table sym, venue, time, bid, ask, in batch order
/ @eg exec bid from .qmicro.venue_tops ([] sym:2#`$"BTC-USDT"; market:2#`crypto; source:`a`b; source_time:2#2026.09.17D10:00:00; bid_prices:(enlist 62000f;`float$()); ask_prices:(enlist 62010f;enlist 62008f))  ->  62000 0n
venue_tops:{[x]
    x:x where `across_venues=price_rule x`market;
    s:.qbook.top_sides x;
    ([] sym:x`sym; venue:x`source; time:x`source_time; bid:s`bid; ask:s`ask)}

/ The mid of each book, for the markets priced on their own book: halfway
/ between level 0 of each side. A book with no usable level on a side gives
/ no mid and is dropped: an empty ladder is the source withdrawing its book,
/ and the last mid seen is a better mark than none.
/ @param x market_data rows
/ @return table sym, market, source, source_time, bid, ask, mid, in batch order
/ @eg exec mid from .qmicro.own_book_mids ([] sym:`EURUSD`GBPUSD; market:`fx`fx; source:`a`a; source_time:2#2026.09.17D10:00:00; bid_prices:(enlist 1.0849;`float$()); ask_prices:(enlist 1.0851;enlist 1.27))  ->  ,1.085
own_book_mids:{[x]
    x:x where `own_book=price_rule x`market;
    s:.qbook.top_sides x;
    t:([] sym:x`sym; market:x`market; source:x`source; source_time:x`source_time; bid:s`bid; ask:s`ask);
    t:t where (not null t`bid) & not null t`ask;
    update mid:(bid+ask)%2 from t}

/ ------------------------------------------- A REFERENCE PRICE ACROSS VENUES

/ The oldest a venue's top of book may be and still set a cross-venue
/ reference price: one policy for every job that prices against the market
/ rather than one venue - crypto_markout's markouts and posbook's crypto
/ marks (#886). Five seconds, superbook's expiry window for FX.
reference_max_age:0D00:00:05

/ How far a source's clock may lead this host's and its book still count as
/ current: market_data's publish check refuses a book dated further ahead
/ (#1021), and superbook takes it as its default (#1033). Every multi-venue
/ feed has some skew; past this, the stamp is wrong rather than early.
max_clock_lead:0D00:00:00.250

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

/ The tops in a batch that advance each venue's held top of book: per
/ (sym; venue) the newest in the batch, kept only when its time is at least
/ the held one's. A delayed or replayed book older than the venue's current
/ one is dropped, so it cannot rewind the price; an equal time follows
/ arrival order. The one rule last_value and posbook hold their per-venue
/ tops by (#1049) - `time` is the venue's source_time, never the plant's.
/ @param tob the held tops, keyed by sym and venue, with a `time` column
/ @param tops table sym, venue, time, bid, ask - a batch's tops, any order
/ @return the rows of tops to upsert into tob, one per (sym; venue) at most
/ @eg exec bid from .qmicro.newer_venue_tops[2!([] sym:enlist `$"BTC-USDT"; venue:enlist `a; time:enlist 2026.09.17D10:00:01; bid:enlist 100f; ask:enlist 101f);([] sym:2#`$"BTC-USDT"; venue:`a`b; time:2#2026.09.17D10:00:00; bid:90 95f; ask:91 96f)]  ->  ,95f
newer_venue_tops:{[tob;tops]
    t:0!select by sym, venue from `time xasc tops;
    held:(exec (sym,'venue)!time from 0!tob) t[`sym],'t`venue;
    t where (null held) or t[`time]>=held}

/ The rows that advance each sym's held price: per sym the newest in the
/ batch by source_time (an equal time follows arrival order), kept only when
/ it is at least as new as the one held. newer_venue_tops' rule, one level
/ up - for a price held per sym rather than per venue: last_value's latest
/ row and posbook's FX mark (#1088), so a late, older book moves neither.
/ @param held sym -> the source_time of the price held now
/ @param rows a table with sym and source_time, any order
/ @return the rows to apply, one per sym at most
/ @eg exec mid from .qmicro.newer_by_sym[enlist[`EURUSD]!enlist 2026.09.17D10:00:01;([] sym:`EURUSD`GBPUSD; source_time:2#2026.09.17D10:00:00; mid:1.1 1.27)]  ->  ,1.27
newer_by_sym:{[held;rows]
    t:0!select by sym from `source_time xasc rows;
    h:held t`sym;
    t where (null h) or t[`source_time]>=h}
\d .
