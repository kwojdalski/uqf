/ reference_library.q - the transforms and combines a reference variant is built from, beyond the flat `top` and `best` (.qetl.reference, #2289).
/ .
/ A variant is SELECT x TRANSFORM x COMBINE (reference_variant.q). The transforms here read a book's DEPTH, so
/ they run over the gathered slice (variant_run.q) - the few books the variants matched, with their ladders - and
/ never over the market. Each is a pure function of one book:
/ .
/   micro      the micro-price, (bid x ask size + ask x bid size) / (bid size + ask size) at level 0: the mid
/              leaned toward the thinner side. bid and ask are both the micro-price; the weight is the size quoted.
/   vwap       the price of filling `qty` into each side: the bid side sells it down the bid ladder, the ask
/              side buys it up the ask ladder. A side whose ladder is thinner than `qty` is null - it cannot be filled.
/   mid_sized  the level-0 bid and ask, weighted by the size quoted at level 0 on both sides.
/   topn_w     the size-weighted average price of the first `n` levels of each side, weighted by the size
/              of those levels.
/ .
/ A combine is a pure function of the books matched to ONE instant across venues:
/ .
/   median     the median of each side and of the books' mids.
/   wmean      the mean of each side and of the books' mids, weighted by the transform's `w`.
/ .
/ A transform returns the table bid, ask and optionally w; the combine receives it with the book's clock. A null side
/ is a side no one quotes: it is left out of a side's mean and median, and a book with either side null has no mid.
/ .
/ Q TRAP. `n#x` WRAPS when x is shorter than n (3#1 2 is 1 2 1): every "first n levels" here is `n sublist`.

\d .

/ A table a depth transform may read: it has the ladders, and its `rid` is its own row index.
/ @param variant the variant, for the message
/ @param tob the table
/ @return tob
/ @throws error naming the variant when tob lacks the ladders or its rid is not 0, 1, 2, ...
/ @eg count .qetl.reference.require_slice[`eg;([] bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:(); rid:`long$())]  ->  0
.qetl.reference.require_slice:{[variant;tob]
    need:`bid_prices`bid_sizes`ask_prices`ask_sizes`rid;
    if[count miss:need where not need in cols tob; '"reference: ",string[variant]," reads depth, so the books must carry ",(", " sv string miss)," - read them with .qetl.variant.read"];
    if[not (tob`rid)~til count tob; '"reference: ",string[variant]," reads depth, so the books' rid must be their row index (.qetl.variant.slice)"];
    tob}

/ The weight of a book: the size quoted at level 0 of both sides, a missing side counting for nothing.
/ @param slice books with bid_sizes and ask_sizes
/ @return a float per book
/ @eg .qetl.reference.top_size[([] bid_sizes:enlist 1 2f; ask_sizes:enlist 3 4f)]  ->  enlist 4f
.qetl.reference.top_size:{[slice]
    side:{[s] 0f^{$[count x; first x; 0n]} each s};
    (side slice`bid_sizes)+side slice`ask_sizes}

/ The price of filling a quantity into one side of one ladder: the first levels, to the quantity, volume-weighted.
/ @param qty the quantity to fill, positive
/ @param prices the ladder's prices, best first
/ @param sizes the ladder's sizes
/ @return the volume-weighted price, null when the ladder holds less than the quantity
/ @eg .qetl.reference.fill_price[150f;100 101 102f;100 100 100f]  ->  100.33333333333333
.qetl.reference.fill_price:{[qty;prices;sizes]
    if[qty>sum sizes; :0n];
    behind:sums[sizes]-sizes;
    taken:0f|sizes&qty-behind;
    (sum taken*prices)%qty}

/ The size-weighted price of the first n levels of one side of one ladder.
/ @param n how many levels
/ @param prices the ladder's prices, best first
/ @param sizes the ladder's sizes
/ @return the size-weighted price of the levels there are, null when there are none or they hold no size
/ @eg .qetl.reference.level_price[2;100 101 102f;100 300 500f]  ->  100.75
.qetl.reference.level_price:{[n;prices;sizes]
    p:n sublist prices;
    s:n sublist sizes;
    $[0f<sum s; (sum p*s)%sum s; 0n]}

.qetl.reference.add_transform[`micro;`depth;(`symbol$())!();{[slice;params]
    bs:{$[count x; first x; 0n]} each slice`bid_sizes;
    as:{$[count x; first x; 0n]} each slice`ask_sizes;
    live:(not null slice`bid) & (not null slice`ask) & (not null bs) & (not null as) & 0f<bs+as;
    px:?[live; ((slice[`bid]*as)+slice[`ask]*bs)%bs+as; 0n];
    ([] bid:px; ask:px; w:.qetl.reference.top_size slice)}];

.qetl.reference.add_transform[`vwap;`depth;enlist[`qty]!enlist 1000000f;{[slice;params]
    q:params`qty;
    if[not q>0f; '"vwap: qty must be positive"];
    ([] bid:.qetl.reference.fill_price[q]'[slice`bid_prices;slice`bid_sizes]; ask:.qetl.reference.fill_price[q]'[slice`ask_prices;slice`ask_sizes])}];

.qetl.reference.add_transform[`mid_sized;`depth;(`symbol$())!();{[slice;params]
    ([] bid:slice`bid; ask:slice`ask; w:.qetl.reference.top_size slice)}];

.qetl.reference.add_transform[`topn_w;`depth;enlist[`n]!enlist 3;{[slice;params]
    k:params`n;
    if[not k>0; '"topn_w: n must be positive"];
    b:.qetl.reference.level_price[k]'[slice`bid_prices;slice`bid_sizes];
    a:.qetl.reference.level_price[k]'[slice`ask_prices;slice`ask_sizes];
    sz:{[k;s] sum k sublist s};
    ([] bid:b; ask:a; w:(sz[k] each slice`bid_sizes)+sz[k] each slice`ask_sizes)}];

/ A promoted variant's transform: the price its transform stored, flat, beside the book (.qetl.variant.promote).
.qetl.reference.add_transform[`stored;`top;(`symbol$())!();{[rows;params] ([] bid:rows`bid; ask:rows`ask; w:rows`w)}];

/ The median of each side and of the books' mids, per instant; null where no book has the side.
.qetl.reference.combines[`median]:{[tid;px;n]
    t:([] tid:tid; bid:px`bid; ask:px`ask; mid:?[(null px`bid)|null px`ask;0n;0.5*(px`bid)+px`ask]);
    g:0!select b:med bid, a:med ask, m:med mid by tid from t;
    ([] bid:@[n#0n;g`tid;:;g`b]; ask:@[n#0n;g`tid;:;g`a]; mid:@[n#0n;g`tid;:;g`m])};

/ The weighted mean of each side and of the books' mids, per instant; a book without the side adds neither weight nor price to it.
.qetl.reference.combines[`wmean]:{[tid;px;n]
    w:$[`w in cols px; px`w; (count tid)#1f];
    mid:?[(null px`bid)|null px`ask;0n;0.5*(px`bid)+px`ask];
    t:([] tid:tid; bid:px`bid; ask:px`ask; mid:mid; w:w);
    g:0!select b:(sum w*bid)%sum w*not null bid, a:(sum w*ask)%sum w*not null ask, m:(sum w*mid)%sum w*not null mid by tid from t;
    ([] bid:@[n#0n;g`tid;:;g`b]; ask:@[n#0n;g`tid;:;g`a]; mid:@[n#0n;g`tid;:;g`m])};

\d .
