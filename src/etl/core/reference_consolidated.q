/ reference_consolidated.q - combines that price off the CONSOLIDATED book: every venue's ladders merged (.qetl.reference, #3002).
/ .
/ A reference is SELECT x TRANSFORM x COMBINE (reference_variant.q). The transforms and combines of reference_library.q
/ reduce each venue's book to a bid, an ask and a weight BEFORE the venues meet, so no combine ever sees two venues' depth
/ together. A LADDER COMBINE does: the transform `ladders` passes each matched book's ladders through (it reduces
/ nothing), and the combine receives them grouped by instant. Two are declared here:
/ .
/   consolidated_micro   the micro-price of the cross-venue touch: the highest bid anywhere, the lowest ask anywhere, and at
/                        each the size summed across EVERY venue quoting exactly that price. (bid x ask size + ask x bid
/                        size) / (bid size + ask size), so it always lies inside the touch. bid, ask and mid are all it.
/   consolidated_vwap    the ladders merged across venues, best first; a buy fills `qty` off the merged asks (cheapest
/                        first) and a sell off the merged bids. bid is the sell price, ask the buy price, mid their midpoint.
/ .
/ A variant uses one by naming transform `ladders` and the combine; the parameters are the transform's:
/ .
/   qty          the quantity consolidated_vwap fills (default 1,000,000); consolidated_micro ignores it
/   crossed      what an instant whose consolidated touch is crossed (a bid above an ask, which two venues' stale
/                quotes make) is priced as: `null (the default) leaves the instant null, `as_is prices the crossed touch
/   invert       0b (default), or 1b to quote the pair inverted: every ladder's prices become 1/p, its bids become its
/                asks and the reverse, and each size is re-expressed in the other currency (size x price)
/   max_instants the most instants one evaluation prices; a larger batch is split into batches of this size, never grown
/ .
/ THE RULES, each a refusal or a null with a reason, never a guess (`reasons` returns the reason for each instant):
/   staleness     is the variant's SELECT's, applied to each venue's book BEFORE merging: the combine sees only the books the
/                 SELECT matched, so a stale venue is not in the consolidated book.
/   market        books of different markets (venue_market.q: spot vs perp, size in base vs contracts) are not merged. A
/                 variant whose venue LIST spans two markets is refused when it is registered; with venues `all the venue set
/                 is not known until the books are, so an instant whose books span two markets is null, reason `mixed_market.
/   depth         a VWAP whose quantity the merged levels cannot fill is null on that side (`insufficient_depth); never extrapolated.
/   crossed       per the `crossed` parameter.
/   a side no venue quotes is null; no matched book at all is reason `no_books.
/   only the books on the target's own clock count in `same`, as for every combine (reference_variant.q `folded`).
/ .
/ COST. A ladder combine holds the matched books' ladders in memory for the batch: instants x venues x levels. `max_instants`
/ bounds that, and is checked BEFORE the ladders are gathered (.qetl.variant.run), so a large batch is read and priced in
/ pieces - the pieces price what the whole would, instant by instant, because instants do not depend on each other.
/ .
/ WHAT IS NOT HERE. Fees and venue access (a venue the desk cannot trade on still sets the touch) are a later, declared
/ per-venue adjustment, not a silent default. The `market` of a book is its venue's (venue_market.q): the flat top the
/ selection reads does not carry market_data's `market` column, and a venue belongs to one market.

\d .

/ The reasons an instant may be null for, in the order they are tested.
.qetl.reference.consolidated.reasons_known:`ok`no_books`mixed_market`crossed`one_sided`insufficient_depth

/ The default number of instants priced at once by a ladder combine.
.qetl.reference.consolidated.default_limit:2000

/ The policies for a crossed consolidated touch.
.qetl.reference.consolidated.policies:`null`as_is

/ Invert a book's ladders: prices become 1/p, bids become asks and the reverse (the best bid is the cheapest inverted ask, so
/ the order a ladder is listed in flips with the prices), and each size is re-expressed in the other currency as size x price.
/ @param prices a ladder's prices
/ @param sizes the matching sizes
/ @return a dictionary prices, sizes: the same levels as the other side of the inverted pair, best first
/ @eg .qetl.reference.consolidated.invert_side[2 4f;10 20f]  ->  `prices`sizes!(0.5 0.25;20 80f)
.qetl.reference.consolidated.invert_side:{[prices;sizes]
    `prices`sizes!(%[1f;"f"$prices];sizes*"f"$prices)}

/ The venue ladders of a book as the quantities a merge reads: levels with a price and a positive size only.
/ @param prices a ladder's prices
/ @param sizes the matching sizes
/ @return a dictionary prices, sizes
/ @eg .qetl.reference.consolidated.levels[1 2 0n;3 0 4f]  ->  `prices`sizes!(enlist 1f;enlist 3f)
.qetl.reference.consolidated.levels:{[prices;sizes]
    p:"f"$prices;
    s:"f"$sizes;
    k:where (not null p) & (not null s) & s>0f;
    `prices`sizes!(p k;s k)}

/ The touch of merged levels: the best price and the size quoted AT it, summed.
/ @param prices merged prices of one side
/ @param sizes the sizes
/ @param buy 1b for the ask side (lowest is best), 0b for the bid side (highest)
/ @return a two-element float list: price, size - both null when there are no levels
/ @eg .qetl.reference.consolidated.touch[100.1 100.0 100.0;1 2 3f;1b]  ->  100 5f
.qetl.reference.consolidated.touch:{[prices;sizes;buy]
    if[0=count prices; :0n 0n];
    b:$[buy; min prices; max prices];
    (b; sum sizes where prices=b)}

/ The price of filling a quantity off merged levels: cheapest first for a buy, dearest first for a sell.
/ @param qty the quantity, positive
/ @param prices merged prices of one side
/ @param sizes the sizes
/ @param buy 1b to buy off asks, 0b to sell into bids
/ @return the volume-weighted price, null when the levels hold less than qty
/ @eg .qetl.reference.consolidated.fill[3f;100 100.5 100.1 100.6;1 2 2 2f;1b]  ->  100.06666666666666
.qetl.reference.consolidated.fill:{[qty;prices;sizes;buy]
    if[0=count prices; :0n];
    o:$[buy; iasc prices; idesc prices];
    .qetl.reference.fill_price[qty;prices o;sizes o]}

/ One instant's consolidated price from its books' ladders.
/ @param kind `micro or `vwap
/ @param params the transform's parameters (qty, crossed)
/ @param markets the market of each book
/ @param bp bid prices, a list of ladders, one per book
/ @param bs bid sizes
/ @param ap ask prices
/ @param as_ ask sizes
/ @return a dictionary bid, ask, mid, reason
/ @eg (.qetl.reference.consolidated.one[`micro;`qty`crossed`invert`max_instants!(1f;`null;0b;10);`crypto`crypto;(enlist 100f;`float$());(enlist 9f;`float$());(enlist 100.2;enlist 100.1);(enlist 1f;enlist 3f)])`mid  ->  100.075
.qetl.reference.consolidated.one:{[kind;params;markets;bp;bs;ap;as_]
    nul:`bid`ask`mid!3#0n;
    if[0=count markets; :nul,enlist[`reason]!enlist `no_books];
    if[1<count distinct markets; :nul,enlist[`reason]!enlist `mixed_market];
    b:.qetl.reference.consolidated.levels[raze bp;raze bs];
    a:.qetl.reference.consolidated.levels[raze ap;raze as_];
    tb:.qetl.reference.consolidated.touch[b`prices;b`sizes;0b];
    ta:.qetl.reference.consolidated.touch[a`prices;a`sizes;1b];
    if[(not null first tb) & (not null first ta) & (first tb)>first ta;
        if[`null~params`crossed; :nul,enlist[`reason]!enlist `crossed]];
    if[`micro~kind;
        if[(null first tb) | null first ta; :nul,enlist[`reason]!enlist `one_sided];
        bid_p:first tb; ask_p:first ta; bid_sz:last tb; ask_sz:last ta;
        lean:(bid_p*ask_sz)+(ask_p*bid_sz);
        px:lean%(bid_sz+ask_sz);
        :`bid`ask`mid`reason!(px;px;px;`ok)];
    q:params`qty;
    sell:.qetl.reference.consolidated.fill[q;b`prices;b`sizes;0b];
    buy:.qetl.reference.consolidated.fill[q;a`prices;a`sizes;1b];
    mid:$[(null sell) | null buy; 0n; 0.5*(sell+buy)];
    why:$[(null sell) | null buy; `insufficient_depth; `ok];
    `bid`ask`mid`reason!(sell;buy;mid;why)}

/ The consolidated price of every instant, with its reason.
/ @param kind `micro or `vwap
/ @param tid the instant (target index) each book belongs to
/ @param rows table source, bid_prices, bid_sizes, ask_prices, ask_sizes, one row per book
/ @param n the number of instants
/ @param params the transform's parameters
/ @return table bid, ask, mid, reason - n rows
/ @eg (.qetl.reference.consolidated.price[`micro;enlist 0;([] source:enlist `a; bid_prices:enlist enlist 100f; bid_sizes:enlist enlist 9f; ask_prices:enlist enlist 100.2; ask_sizes:enlist enlist 1f);1;`qty`crossed`invert`max_instants!(1f;`null;0b;10)])`mid  ->  enlist 100.18
.qetl.reference.consolidated.price:{[kind;tid;rows;n;params]
    mk:.qetl.venue.market_of rows`source;
    g:group tid;
    one:{[kind;params;mk;rows;ix]
        .qetl.reference.consolidated.one[kind;params;mk ix;(rows`bid_prices) ix;(rows`bid_sizes) ix;(rows`ask_prices) ix;(rows`ask_sizes) ix]}[kind;params;mk;rows];
    out:([] bid:n#0n; ask:n#0n; mid:n#0n; reason:n#`no_books);
    if[0=count g; :out];
    res:one each value g;
    ix:key g;
    update bid:@[bid;ix;:;res@\:`bid], ask:@[ask;ix;:;res@\:`ask], mid:@[mid;ix;:;res@\:`mid], reason:@[reason;ix;:;res@\:`reason] from out}

/ Private: a ladder combine registered under a name.
/ @private
.qetl.reference.consolidated.stub:{[name] {[nm;unused_tid;unused_px;unused_n] '"reference: ",string[nm]," reads ladders: price it through a variant whose transform is `ladders"}[name]}

/ Register a combine that reads ladders.
/ @param name the combine's name
/ @param check {[name;def] ...} run when a variant naming the combine is registered; it throws to refuse the definition
/ @param fn {[tid;rows;n;params] ...} over the matched books' ladders, returning a table bid, ask, mid and reason of n rows
/ @return name
/ @eg .qetl.reference.add_ladder_combine[`eg_ladder;{[nm;d] ::};{[tid;rows;n;params] ([] bid:n#0n; ask:n#0n; mid:n#0n; reason:n#`no_books)}]  ->  `eg_ladder
.qetl.reference.add_ladder_combine:{[name;check;fn]
    .qetl.reference.combines[name]:.qetl.reference.consolidated.stub name;
    .qetl.reference.ladder_combines[name]:enlist `fn`check!(fn;check);
    name}

/ The registration check of a consolidated combine: the transform, the parameters and the venue list.
/ @param name the variant's name
/ @param def the definition, as given
/ @return ::
/ @throws error when the transform is not `ladders, a parameter is invalid, or the venue list spans markets
/ @eg .qetl.reference.consolidated.check[`eg;`venues`stale`offset`transform`combine!(`a`b;`spec;0D;`ladders;`consolidated_micro)]  ->  ::
.qetl.reference.consolidated.check:{[name;def]
    if[not `ladders~def`transform; '"reference: ",string[name],"'s combine ",string[def`combine]," reads ladders, so its transform must be ladders"];
    p:.qetl.reference.transform[`ladders][`defaults],$[`params in key def; def`params; (`symbol$())!()];
    if[not (p`qty)>0f; '"reference: ",string[name],"'s qty must be positive"];
    if[not (p`crossed) in .qetl.reference.consolidated.policies; '"reference: ",string[name],"'s crossed must be one of ",", " sv string .qetl.reference.consolidated.policies];
    if[not (p`max_instants)>0; '"reference: ",string[name],"'s max_instants must be positive"];
    v:def`venues;
    if[$[11h=type v; 1<count distinct .qetl.venue.market_of v; 0b];
        '"reference: ",string[name]," merges venues of different markets (",(", " sv string distinct .qetl.venue.market_of v),"): their sizes are not comparable"];
    ::}

/ The transform that passes ladders through: the matched books' own, optionally inverted, with the flat bid, ask and weight.
.qetl.reference.add_transform[`ladders;`depth;`qty`crossed`invert`max_instants!(1000000f;`null;0b;.qetl.reference.consolidated.default_limit);{[slice;params]
    as_inverted:params`invert;
    base:`bid`ask`w!(slice`bid;slice`ask;.qetl.reference.top_size slice);
    if[not as_inverted; :(flip base),'flip (`bid_prices`bid_sizes`ask_prices`ask_sizes)!slice (`bid_prices`bid_sizes`ask_prices`ask_sizes)];
    ib:.qetl.reference.consolidated.invert_side'[slice`bid_prices;slice`bid_sizes];
    ia:.qetl.reference.consolidated.invert_side'[slice`ask_prices;slice`ask_sizes];
    / the inverted pair: its asks are the old bids and its bids the old asks; best first means ascending asks, descending bids
    ask_prices:ib@\:`prices; ask_sizes:ib@\:`sizes; bid_prices:ia@\:`prices; bid_sizes:ia@\:`sizes;
    ([] bid:%[1f;slice`ask]; ask:%[1f;slice`bid]; w:base`w; bid_prices:bid_prices; bid_sizes:bid_sizes; ask_prices:ask_prices; ask_sizes:ask_sizes)}];

/ The ladder fold `folded` hands a ladder combine: the matched books on the target's own clock (`same`) and on any (`any`).
/ @param def a variant's definition
/ @param j the matched books (chosen)
/ @param px the transform's table, one row per book of j, carrying the ladders
/ @param n the number of targets
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid)
/ @eg count (.qetl.reference.ladder_fold[`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`ladders;`consolidated_micro;(`symbol$())!());0#([] tid:`long$(); source:`symbol$(); clock:`symbol$(); tclock:`symbol$());([] bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:());0]`same)  ->  0
.qetl.reference.ladder_fold:{[def;j;px;n]
    lc:first .qetl.reference.ladder_combines def`combine;
    rows:([] tid:j`tid; source:j`source; clock:j`clock; target:j`tclock),'(`bid_prices`bid_sizes`ask_prices`ask_sizes)#px;
    same:select from rows where clock=target;
    run:{[lc;p;n;r] `bid`ask`mid#lc[`fn][r`tid;r;n;p]}[lc;def`params;n];
    `same`any!(run same;run rows)}

/ The consolidated combines' common function.
/ @private
.qetl.reference.consolidated.combine:{[kind;tid;rows;n;params] .qetl.reference.consolidated.price[kind;tid;rows;n;params]}

.qetl.reference.add_ladder_combine[`consolidated_micro;.qetl.reference.consolidated.check;.qetl.reference.consolidated.combine[`micro]];
.qetl.reference.add_ladder_combine[`consolidated_vwap;.qetl.reference.consolidated.check;.qetl.reference.consolidated.combine[`vwap]];

/ The reason each instant of a variant is null or priced, for the books on the target's own clock.
/ @param name a registered variant whose combine reads ladders
/ @param tob the books: a slice, with ladders
/ @param max_age the spec's reference age limit
/ @param syms the sym of each target
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return a symbol per target: ok, no_books, mixed_market, crossed, one_sided or insufficient_depth
/ @throws error when the variant's combine does not read ladders
/ @eg .qetl.reference.reasons[.qetl.reference.register[`eg_consolidated;`venues`stale`offset`transform`combine!(`all;`spec;0D;`ladders;`consolidated_micro)];0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;`symbol$();`timestamp$();`symbol$()]  ->  `symbol$()
.qetl.reference.reasons:{[name;tob;max_age;syms;times;clocks]
    def:.qetl.reference.def name;
    if[not (def`combine) in key .qetl.reference.ladder_combines; '"reference: ",string[name],"'s combine does not read ladders, so it has no reasons"];
    n:count syms;
    j:.qetl.reference.chosen[def;tob;max_age;syms;times;clocks];
    if[() ~ j; :n#`no_books];
    t:.qetl.reference.transform def`transform;
    px:(t[`fn][.qetl.reference.require_slice[name;tob];def`params]) j`rid;
    rows:([] tid:j`tid; source:j`source; clock:j`clock; target:j`tclock),'(`bid_prices`bid_sizes`ask_prices`ask_sizes)#px;
    same:select from rows where clock=target;
    kind:$[`consolidated_micro~def`combine; `micro; `vwap];
    (.qetl.reference.consolidated.price[kind;same`tid;same;n;def`params])`reason}

/ The most instants one evaluation of these variants may price: the smallest max_instants of those that read ladders.
/ @param names registered variant names
/ @return a long, 0W when none reads ladders
/ @eg .qetl.reference.instant_limit enlist `best_top  ->  0W
.qetl.reference.instant_limit:{[names]
    defs:.qetl.reference.def each distinct (),names;
    lad:defs where (defs@\:`combine) in key .qetl.reference.ladder_combines;
    $[count lad; min {(.qetl.reference.transform[x`transform][`defaults],x`params)`max_instants} each lad; 0W]}

/ Put the answers of several evaluations of the same targets, taken piece by piece, back into one answer in target order.
/ @param parts a list of dictionaries variant -> `same`any!(table; table)
/ @param names the variants
/ @return one such dictionary over all the targets
/ @eg count .qetl.reference.glue[2#enlist enlist[`v]!enlist `same`any!2#enlist ([] bid:enlist 1f; ask:enlist 2f; mid:enlist 1.5);enlist `v]`v  ->  2
.qetl.reference.glue:{[parts;names]
    one:{[parts;nm;w] raze {[nm;w;p] (p nm) w}[nm;w] each parts};
    names!{[one;parts;nm] `same`any!one[parts;nm] each `same`any}[one;parts] each names}

/ Evaluate a batch larger than the limit in pieces of at most the limit, and put the answers back in target order.
/ @param names the variants
/ @param tob the books
/ @param max_age the spec's reference age limit
/ @param syms the sym of each target
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @param lim the most instants per piece
/ @return as .qetl.variant.evaluate
/ @eg count .qetl.reference.split_evaluate[enlist `best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;3#`EURUSD;3#2026.09.17D10:00:00;3#`venue;2]  ->  1
.qetl.reference.split_evaluate:{[names;tob;max_age;syms;times;clocks;lim]
    names:distinct (),names;
    starts:lim*til ceiling (count syms)%lim;
    parts:{[names;tob;ma;sy;tm;ck;lim;s] ix:s+til lim&(count sy)-s; .qetl.variant.evaluate[names;tob;ma;sy ix;tm ix;ck ix]}[names;tob;max_age;syms;times;clocks;lim] each starts;
    .qetl.reference.glue[parts;names]}

/ Price targets over the mapped market in pieces: each piece is at most the limit of the variants' ladder combines, and the
/ limit is checked BEFORE a piece's ladders are gathered, so a large batch never holds more than one piece's ladders.
/ @param h the handle to the process that maps market_data
/ @param targets table sym, time, clock - the instants priced
/ @param read_from the oldest source_time a book may have under the spec's own age limit
/ @param skew days either side of a time's date its books may be stored
/ @param names the variants
/ @param max_age the spec's reference age limit
/ @return as .qetl.variant.evaluate
/ @eg count .qetl.variant.run[{value x};0#([] sym:`symbol$(); time:`timestamp$(); clock:`symbol$());2026.09.17D00:00:00;1;enlist `best_top;0D00:00:05]  ->  1
.qetl.variant.run:{[h;targets;read_from;skew;names;max_age]
    names:distinct (),names;
    lim:1|(.qetl.reference.instant_limit names)&count targets;
    starts:lim*til max 1,ceiling (count targets)%lim;
    parts:{[h;tg;rf;sk;names;ma;lim;s]
        piece:tg s+til lim&(count tg)-s;
        g:.qetl.variant.read[h;select sym, time from piece;rf;sk;names;ma];
        .qetl.variant.evaluate[names;g`slice;ma;piece`sym;piece`time;piece`clock]}[h;targets;read_from;skew;names;max_age;lim] each starts;
    .qetl.reference.glue[parts;names]}

\d .
