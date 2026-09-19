/ superbook.q - latest direct liquidity across sources (.qsub.superbook).
/ .
/ Keeps one full snapshot per (sym, source), never a concatenated history.
/ Publishes one sorted ladder per pair with aligned source and time vectors.
/ Source timestamps are also watermarks: expiry does not erase them and let
/ a delayed older snapshot resurrect withdrawn liquidity.

\d .qsub.superbook

publish:.qstream.unwired `superbook;
books:`sym`source xkey .qsub.market_data.market_data
superbook:([] sym:`symbol$(); as_of:`timestamp$(); bid_prices:(); bid_sizes:(); bid_sources:(); bid_times:(); ask_prices:(); ask_sizes:(); ask_sources:(); ask_times:())
/ The demo feeds tick every 500ms. Override for the actual feed SLA.
max_age:0D00:00:05

/ Remove non-executable levels, keeping prices and base sizes aligned.
/ @param prices a numeric vector
/ @param sizes the matching numeric vector in base currency
/ @return a table of positive finite price and size
/ @throws when vectors are malformed or their lengths differ
/ @eg .qsub.superbook.levels[1.1 1.2;100 0f] -> ([] price:enlist 1.1; size:enlist 100f)
levels:{[prices;sizes]
    if[not all (type each (prices;sizes)) in 0 6 7 8 9h;
        '"superbook: prices and sizes must be numeric vectors"];
    if[(count prices)<>count sizes; '"superbook: prices and sizes differ in length"];
    if[any (0h=type each (prices;sizes)) and 0<count each (prices;sizes);
        '"superbook: prices and sizes must be numeric vectors"];
    ladder:([] price:`float$prices; size:`float$sizes);
    select from ladder where price>0, price<0w, size>0, size<0w}

/ Replace newer source snapshots. Equal timestamps use arrival order.
/ Empty or unusable sides replace the old side too; zero size is withdrawal.
/ Future-dated rows are ignored, so they cannot poison a source's watermark.
/ @param state latest snapshots keyed by sym and source
/ @param batch market_data rows; a plant time column may also be present
/ @param as_of UTC processing timestamp
/ @return updated keyed snapshots, without mutating state
/ @throws when columns, identities, timestamps or level vectors are malformed
/ @eg count .qsub.superbook.replace_books[.qsub.superbook.books;.qsub.market_data.market_data;2026.09.19D10:00:00.000000000] -> 0
replace_books:{[state;batch;as_of]
    wanted:cols .qsub.market_data.market_data;
    missing:wanted except cols batch;
    if[count missing; '"superbook: missing columns ",", " sv string missing];
    rows:wanted#batch;
    problems:.qxf.problems[.qsub.market_data.market_data;rows;1b];
    if[count problems; '"superbook: ","; " sv problems];
    if[any null rows`source_time; '"superbook: source_time must not be null"];
    if[(any null rows`source) or not all .qccy.is_ccy_pair each rows`sym;
        '"superbook: a canonical FX pair and non-null source are required"];
    i:0;
    while[i<count rows;
        row:rows i;
        bid_levels:levels[row`bid_prices;row`bid_sizes];
        ask_levels:levels[row`ask_prices;row`ask_sizes];
        row[`bid_prices`bid_sizes]:(bid_levels`price;bid_levels`size);
        row[`ask_prices`ask_sizes]:(ask_levels`price;ask_levels`size);
        previous:state `sym`source#row;
        newer:(null previous`source_time) or (row`source_time)>=previous`source_time;
        if[newer and (row`source_time)<=as_of; state:state upsert row];
        i+:1];
    state}

/ Flatten one side without merging liquidity belonging to different sources.
/ @param rows current source snapshots for one pair
/ @param side 1 for bids, -1 for asks
/ @return price, size, source and source_time sorted best-first
/ @eg count .qsub.superbook.side_levels[.qsub.market_data.market_data;1] -> 0
side_levels:{[rows;side]
    price_col:$[side=1;`bid_prices;`ask_prices];
    size_col:$[side=1;`bid_sizes;`ask_sizes];
    n:count each rows price_col;
    ladder:([] price:`float$raze rows price_col; size:`float$raze rows size_col;
        source:`symbol$raze n#'rows`source;
        source_time:`timestamp$raze n#'rows`source_time);
    $[side=1; `price xdesc ladder; `price xasc ladder]}

/ Aggregate fresh source snapshots. Known pairs with no liquidity get empty
/ ladders, explicitly clearing downstream opportunities even in a quiet market.
/ @param state latest source snapshots keyed by sym and source
/ @param as_of UTC processing timestamp
/ @param age maximum quote age, inclusive at the boundary
/ @return unkeyed superbook snapshots, one row per known pair
/ @eg count .qsub.superbook.snapshot[`sym`source xkey 0#.qsub.market_data.market_data;2026.09.19D10:00:00.000000000;0D00:00:05] -> 0
snapshot:{[state;as_of;age]
    all_books:0!state;
    cutoff:as_of-age;
    fresh:select from all_books where source_time>=cutoff, source_time<=as_of;
    result:0#.qsub.superbook.superbook;
    pairs:distinct all_books`sym;
    i:0;
    while[i<count pairs;
        pair:pairs i;
        current:select from fresh where sym=pair;
        bids:side_levels[current;1];
        asks:side_levels[current;-1];
        result:result upsert (pair;as_of;bids`price;bids`size;bids`source;bids`source_time;
            asks`price;asks`size;asks`source;asks`source_time);
        i+:1];
    result}

/ Publish current snapshots, including empty books after expiry.
/ @param as_of UTC timestamp, explicit for replay and deterministic tests
/ @return nothing
/ No @eg: this reads `books` and publishes, so what it does depends entirely
/ on process state a one-line example cannot set up honestly - and an
/ example whose result depends on whatever ran before it is worse than
/ none. tests/q/test_superbook.q drives it against a book it builds itself.
refresh:{[as_of]
    rows:snapshot[books;as_of;max_age];
    if[count rows; .qsub.superbook.publish[`superbook;rows]];
    }

/ Consume a canonical market_data batch and publish the recomputed books.
/ @param tbl incoming table name
/ @param batch full source snapshots
/ @return nothing
/ @eg .qsub.superbook.on_batch[`unrelated;()]
on_batch:{[tbl;batch]
    if[not tbl=`market_data; :()];
    now:.z.p;
    `.qsub.superbook.books set replace_books[books;batch;now];
    refresh now;
    }

/ Recompute on the clock so silence withdraws stale liquidity.
/ @return nothing
/ No @eg, for refresh's reason: it is `refresh .z.p` and shows nothing a
/ reader can act on without the state behind it.
on_timer:{[] refresh .z.p;}

\d .

.qstream.register[`superbook;`procname`subscribes`publishes`on_batch`timer_period`on_timer!(
    `superbook1;
    enlist `market_data;
    enlist `superbook;
    .qsub.superbook.on_batch;
    0D00:00:00.500;
    .qsub.superbook.on_timer)];
