/ test_reference_consolidated.q - the consolidated-book combines (src/etl/core/reference_consolidated.q, #3002).
/ .
/ The claims, each held to a brute-force merge-and-price written here with plain loops, not to "it ran":
/   * consolidated_micro and consolidated_vwap equal the brute force over random books - ties at the touch, a side no venue
/     quotes, a quantity the merged depth cannot fill, a crossed touch
/   * the worked examples of the issue: 100.067 consolidated against 100.267-100.30 per venue; 100.075 against 100.18
/   * a stale venue is excluded before merging; books of different markets are refused or nulled by name
/   * the live buffer and the gathered/mapped road price the same books alike
/   * a batch over max_instants is split, with the same answer, and is split BEFORE the ladders are gathered

.qt.qetl.consolidated.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-consolidated"];
    system"mkdir -p build/test-status-consolidated";
    }

/ One book: a row of the top-of-book slice, ladders given best first.
.qt.qetl.consolidated.book:{[sy;src;t;bp;bs;ap;as_]
    ([] sym:enlist sy; source:enlist src; source_time:enlist t; clock:enlist `venue; bid:enlist first bp,0n; ask:enlist first ap,0n;
        bid_prices:enlist bp; bid_sizes:enlist bs; ask_prices:enlist ap; ask_sizes:enlist as_)}

.qt.qetl.consolidated.slice:{[books] .qetl.variant.slice raze books}

/ The variant the tests register: venues, stale, crossed policy, qty and limit.
.qt.qetl.consolidated.reg:{[nm;comb;ch]
    d:`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`ladders;comb;(`symbol$())!());
    if[not 99h=type ch; ch:(`symbol$())!()];
    .qetl.reference.register[nm;d,ch];
    nm}

.qt.qetl.consolidated.forget:{[names] `.qetl.reference.variants set names _ .qetl.reference.variants}

.qt.qetl.consolidated.t0:2026.09.17D10:00:00

.qt.qetl.consolidated.one_target:{[names;sl;ma] .qetl.variant.evaluate[names;sl;ma;enlist `BTCUSDT;enlist .qt.qetl.consolidated.t0+0D00:00:01;enlist `venue]}

/ Run f . args with `market_data` standing in for the mapped table, and put the root back.
.qt.qetl.consolidated.with_market:{[md;f;args]
    had:$[`market_data in key `.; (1b;get `market_data); (0b;::)];
    `market_data set md;
    r:.[f;args;{[e] (`threw;e)}];
    $[first had; `market_data set last had; ![`.;();0b;enlist `market_data]];
    if[`threw~first r; 'last r];
    r}

/ BRUTE FORCE. The consolidated book as a list of (price; size) built level by level, priced with loops and no shared code.
.qt.qetl.consolidated.brute_side:{[books;side;buy]
    lv:();
    i:0;
    while[i<count books;
        p:books[i;$[side~`bid;0;2]]; s:books[i;$[side~`bid;1;3]];
        k:0;
        while[k<count p; if[(not null p k) and (not null s k) and s[k]>0f; lv,:enlist (p k;s k)]; k+:1];
        i+:1];
    lv}

.qt.qetl.consolidated.brute:{[kind;qty;crossed_null;books]
    bl:.qt.qetl.consolidated.brute_side[books;`bid;0b];
    al:.qt.qetl.consolidated.brute_side[books;`ask;1b];
    / the touch: the best price and the sum of every size at exactly that price
    tp:{[lv;buy] if[0=count lv; :0n 0n]; b:lv[0;0]; i:0; while[i<count lv; if[buy and lv[i;0]<b; b:lv[i;0]]; if[(not buy) and lv[i;0]>b; b:lv[i;0]]; i+:1]; z:0f; i:0; while[i<count lv; if[lv[i;0]=b; z+:lv[i;1]]; i+:1]; (b;z)};
    tb:tp[bl;0b];
    ta:tp[al;1b];
    nul:3#0n;
    if[(not null first tb) and (not null first ta) and (first tb)>first ta; if[crossed_null; :nul]];
    if[kind~`micro;
        if[(null first tb) or null first ta; :nul];
        m:(((first tb)*last ta)+(first ta)*last tb)%(last tb)+last ta;
        :(m;m;m)];
    / vwap: walk the merged levels cheapest first by repeated selection
    walk:{[lv;qty;buy]
        left:qty; cost:0f; rest:lv;
        while[(left>0f) and 0<count rest;
            j:0; i:0;
            while[i<count rest; if[$[buy; rest[i;0]<rest[j;0]; rest[i;0]>rest[j;0]]; j:i]; i+:1];
            take:left&rest[j;1];
            cost+:take*rest[j;0];
            left-:take;
            rest:rest (til count rest) except j];
        $[left>0f; 0n; cost%qty]};
    sell:walk[bl;qty;0b];
    buy:walk[al;qty;1b];
    (sell;buy;$[(null sell) or null buy; 0n; 0.5*sell+buy])}

/ Two floats are the same price, or both null.
.qt.qetl.consolidated.same:{[a;b] $[(null a) and null b; 1b; (null a) or null b; 0b; 1e-9>abs a-b]}

/ Random books of n venues at one instant, on a coarse price grid so ties at the touch and crossings happen.
.qt.qetl.consolidated.random_books:{[seed;n]
    system "S ",string seed;
    t:.qt.qetl.consolidated.t0;
    mid:100f;
    {[t;mid;i]
        lb:1+first 1?4; la:1+first 1?4;
        / prices in whole ticks of 0.1, so equal prices are equal floats and ties and crossings happen
        bp:(1000-lb?6)%10f; ap:(998+la?8)%10f; bp:desc bp; ap:asc ap;
        bs:`float$1+lb?3; as_:`float$1+la?3;
        .qt.qetl.consolidated.book[`BTCUSDT;`$"v",string i;t;bp;bs;ap;as_]}[t;mid] each til n}

.qt.qetl.consolidated.rows_of:{[books] (raze books)}

.qt.qetl.consolidated.test_the_issue_examples:{[t]
    t0:.qt.qetl.consolidated.t0;
    / asks A: 1@100.0, 2@100.5; B: 2@100.1, 2@100.6; bids kept far away
    a:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;enlist 99f;enlist 5f;100 100.5;1 2f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 98f;enlist 5f;100.1 100.6;2 2f];
    .qt.qetl.consolidated.reg[`cv3;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 3f];
    r:.qt.qetl.consolidated.one_target[`cv3;.qt.qetl.consolidated.slice (a;b);0D00:01:00];
    .qunit.assertTrue[1e-9>abs 100.06666666666667-first ((r`cv3)`same)`ask;"buying 3 off the merged asks: 1@100.0 + 2@100.1 = 100.067"];
    per_a:.qetl.reference.fill_price[3f;100 100.5;1 2f];
    .qunit.assertTrue[(per_a>100.3) or null per_a;"one venue alone cannot fill 3 at that price: its VWAP is 100.333 or null"];
    / the micro example: A bid 100 x9, ask 100.2 x1; B offers 100.1 x3 (and bids far lower)
    ma:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;enlist 100f;enlist 9f;enlist 100.2;enlist 1f];
    mb:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 99f;enlist 4f;enlist 100.1;enlist 3f];
    .qt.qetl.consolidated.reg[`cm;`consolidated_micro;()];
    s:.qt.qetl.consolidated.slice (ma;mb);
    c:(.qt.qetl.consolidated.one_target[`cm;s;0D00:01:00]`cm)`same;
    own_a:((100f*1f)+100.2*9f)%10f;
    .qunit.assertTrue[1e-9>abs 100.075-first c`mid;"consolidated micro 100.075"];
    .qunit.assertTrue[own_a>100.1;"venue A's own micro, 100.18, lies outside the touch B offers (ask 100.1)"];
    .qunit.assertTrue[(first c`mid)<=100.1;"the consolidated micro is inside the consolidated touch"];
    .qt.qetl.consolidated.forget `cv3`cm};

.qt.qetl.consolidated.test_both_combines_equal_the_brute_force_over_random_books:{[t]
    names:`cm_n`cm_a`cv_n`cv_a`cv_big;
    .qt.qetl.consolidated.reg[`cm_n;`consolidated_micro;()];
    .qt.qetl.consolidated.reg[`cm_a;`consolidated_micro;enlist[`params]!enlist enlist[`crossed]!enlist `as_is];
    .qt.qetl.consolidated.reg[`cv_n;`consolidated_vwap;enlist[`params]!enlist `qty`crossed!(2f;`null)];
    .qt.qetl.consolidated.reg[`cv_a;`consolidated_vwap;enlist[`params]!enlist `qty`crossed!(2f;`as_is)];
    .qt.qetl.consolidated.reg[`cv_big;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 40f];
    seen:`crossed`depth`priced!000b;
    i:0;
    while[i<150;
        books:.qt.qetl.consolidated.random_books[100+i;2+i mod 4];
        s:.qt.qetl.consolidated.slice books;
        r:.qt.qetl.consolidated.one_target[names;s;0D00:01:00];
        bk:{[b] (b[0;`bid_prices];b[0;`bid_sizes];b[0;`ask_prices];b[0;`ask_sizes])} each books;
        / each expectation: (variant; kind; qty; crossed-null)
        {[r;bk;e]
            got:first each ((r e 0)`same)`bid`ask`mid;
            want:.qt.qetl.consolidated.brute[e 1;e 2;e 3;bk];
            .qunit.assertTrue[all .qt.qetl.consolidated.same'[got;want];string[e 0]," equals the brute force: ",(.Q.s1 got)," against ",.Q.s1 want]}[r;bk] each ((`cm_n;`micro;1f;1b);(`cm_a;`micro;1f;0b);(`cv_n;`vwap;2f;1b);(`cv_a;`vwap;2f;0b);(`cv_big;`vwap;40f;1b));
        mx:max each bk[;0]; mn:min each bk[;2];
        if[(max mx)>min mn; seen[`crossed]:1b];
        if[null first ((r`cv_big)`same)`ask; seen[`depth]:1b];
        if[not null first ((r`cv_n)`same)`mid; seen[`priced]:1b];
        i+:1];
    .qt.qetl.consolidated.forget names;
    .qunit.assertTrue[all seen;"the random books reach a crossed touch, a thin side and a priced instant, so the comparison is not null against null: ",.Q.s1 seen]};

.qt.qetl.consolidated.test_ties_at_the_touch_add_their_sizes:{[t]
    t0:.qt.qetl.consolidated.t0;
    a:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;100 99f;2 5f;101 102f;3 4f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 100f;enlist 6f;enlist 101f;enlist 1f];
    .qt.qetl.consolidated.reg[`cm;`consolidated_micro;()];
    c:((.qt.qetl.consolidated.one_target[`cm;.qt.qetl.consolidated.slice (a;b);0D00:01:00])`cm)`same;
    / bid 100 x(2+6), ask 101 x(3+1): (100*4 + 101*8)/12
    .qunit.assertTrue[1e-9>abs (first c`mid)-((100f*4f)+101f*8f)%12f;"the sizes at the tied price are summed across venues"];
    .qt.qetl.consolidated.forget enlist `cm};

.qt.qetl.consolidated.test_a_crossed_touch_is_null_by_default_and_priced_when_declared:{[t]
    t0:.qt.qetl.consolidated.t0;
    a:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;enlist 101f;enlist 1f;enlist 102f;enlist 1f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 99f;enlist 1f;enlist 100f;enlist 1f];
    s:.qt.qetl.consolidated.slice (a;b);
    .qt.qetl.consolidated.reg[`cm_n;`consolidated_micro;()];
    .qt.qetl.consolidated.reg[`cm_a;`consolidated_micro;enlist[`params]!enlist enlist[`crossed]!enlist `as_is];
    r:.qt.qetl.consolidated.one_target[`cm_n`cm_a;s;0D00:01:00];
    .qunit.assertTrue[null first ((r`cm_n)`same)`mid;"crossed (bid 101 above ask 100): null by default"];
    .qunit.assertTrue[1e-9>abs 100.5-first ((r`cm_a)`same)`mid;"as_is prices the crossed touch"];
    why:.qetl.reference.reasons[`cm_n;s;0D00:01:00;enlist `BTCUSDT;enlist t0+0D00:00:01;enlist `venue];
    .qunit.assertEquals[why;enlist `crossed;"and says why"];
    .qunit.assertThrows[.qetl.reference.register[`cm_bad;];`venues`stale`offset`transform`combine`params!(`all;`spec;0D;`ladders;`consolidated_micro;enlist[`crossed]!enlist `guess);"reference: cm_bad's crossed must be one of*";"an unknown policy is refused by name"];
    .qt.qetl.consolidated.forget `cm_n`cm_a};

.qt.qetl.consolidated.test_insufficient_depth_is_null_never_extrapolated:{[t]
    t0:.qt.qetl.consolidated.t0;
    a:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;100 99f;1 1f;101 102f;1 1f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 98f;enlist 1f;enlist 103f;enlist 1f];
    s:.qt.qetl.consolidated.slice (a;b);
    .qt.qetl.consolidated.reg[`cv;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 3f];
    .qt.qetl.consolidated.reg[`cv4;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 4f];
    r:.qt.qetl.consolidated.one_target[`cv`cv4;s;0D00:01:00];
    .qunit.assertTrue[1e-9>abs ((101f+102f+103f)%3f)-first ((r`cv)`same)`ask;"three fits the three asks"];
    .qunit.assertTrue[null first ((r`cv4)`same)`ask;"four does not: null"];
    .qunit.assertEquals[.qetl.reference.reasons[`cv4;s;0D00:01:00;enlist `BTCUSDT;enlist t0+0D00:00:01;enlist `venue];enlist `insufficient_depth;"with its reason"];
    .qt.qetl.consolidated.forget `cv`cv4};

.qt.qetl.consolidated.test_a_stale_venue_is_excluded_before_merging:{[t]
    t0:.qt.qetl.consolidated.t0;
    fresh:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;enlist 100f;enlist 1f;enlist 101f;enlist 1f];
    stale:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0-0D00:10:00;enlist 100.5;enlist 1f;enlist 100.6;enlist 1f];
    .qt.qetl.consolidated.reg[`cm;`consolidated_micro;()];
    c:((.qt.qetl.consolidated.one_target[`cm;.qt.qetl.consolidated.slice (fresh;stale);0D00:00:05])`cm)`same;
    alone:((.qt.qetl.consolidated.one_target[`cm;.qt.qetl.consolidated.slice enlist fresh;0D00:00:05])`cm)`same;
    .qunit.assertEquals[c;alone;"the ten-minute-old venue is not in the consolidated book: the answer is the fresh venue's alone"];
    wide:((.qt.qetl.consolidated.one_target[`cm;.qt.qetl.consolidated.slice (fresh;stale);0D00:20:00])`cm)`same;
    .qunit.assertTrue[(first wide`mid)<>first alone`mid;"control: a limit that admits it changes the price, so the exclusion was staleness"];
    .qt.qetl.consolidated.forget enlist `cm};

.qt.qetl.consolidated.test_books_of_different_markets_are_not_merged:{[t]
    t0:.qt.qetl.consolidated.t0;
    / XNAS is an equity venue, binance_spot crypto (venue_market.q)
    a:.qt.qetl.consolidated.book[`BTCUSDT;`XNAS;t0;enlist 100f;enlist 1f;enlist 101f;enlist 1f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`binance_spot;t0;enlist 100f;enlist 1f;enlist 101f;enlist 1f];
    s:.qt.qetl.consolidated.slice (a;b);
    .qt.qetl.consolidated.reg[`cm;`consolidated_micro;()];
    r:.qt.qetl.consolidated.one_target[enlist `cm;s;0D00:01:00];
    .qunit.assertTrue[null first ((r`cm)`same)`mid;"venues of two markets: null"];
    .qunit.assertEquals[.qetl.reference.reasons[`cm;s;0D00:01:00;enlist `BTCUSDT;enlist t0+0D00:00:01;enlist `venue];enlist `mixed_market;"reason mixed_market"];
    .qunit.assertThrows[.qetl.reference.register[`cm_mixed;];`venues`stale`offset`transform`combine!(`XNAS`binance_spot;`spec;0D;`ladders;`consolidated_micro);"reference: cm_mixed merges venues of different markets*";"a venue LIST spanning two markets is refused when registered"];
    .qunit.assertThrows[.qetl.reference.register[`cm_top;];`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`consolidated_micro);"reference: cm_top's combine consolidated_micro reads ladders*";"a ladder combine over a flat transform is refused"];
    .qt.qetl.consolidated.forget enlist `cm};

.qt.qetl.consolidated.test_an_inverted_pair_inverts_prices_swaps_sides_and_reexpresses_sizes:{[t]
    t0:.qt.qetl.consolidated.t0;
    a:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;100 99f;2 4f;101 102f;3 5f];
    b:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist 100.5;enlist 1f;enlist 101.5;enlist 2f];
    s:.qt.qetl.consolidated.slice (a;b);
    / the inverted books by hand: bids 1/101 etc become asks; sizes x price
    ia:.qt.qetl.consolidated.book[`BTCUSDT;`va;t0;(%[1f;101];%[1f;102]);(3f*101f;5f*102f);(%[1f;100];%[1f;99]);(2f*100f;4f*99f)];
    ib:.qt.qetl.consolidated.book[`BTCUSDT;`vb;t0;enlist %[1f;101.5];enlist 2f*101.5;enlist %[1f;100.5];enlist 1f*100.5];
    .qt.qetl.consolidated.reg[`cv_inv;`consolidated_vwap;enlist[`params]!enlist `qty`invert!(1f;1b)];
    .qt.qetl.consolidated.reg[`cv_pln;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 1f];
    got:((.qt.qetl.consolidated.one_target[enlist `cv_inv;s;0D00:01:00])`cv_inv)`same;
    want:((.qt.qetl.consolidated.one_target[enlist `cv_pln;.qt.qetl.consolidated.slice (ia;ib);0D00:01:00])`cv_pln)`same;
    .qunit.assertTrue[all 1e-12>abs (got`bid)-want`bid;"inverting the ladders inside the combine is inverting the books beforehand: bid"];
    .qunit.assertTrue[all 1e-12>abs (got`ask)-want`ask;"ask"];
    .qunit.assertTrue[(first got`bid)<first got`ask;"and the inverted bid is below the inverted ask"];
    .qt.qetl.consolidated.forget `cv_inv`cv_pln};

.qt.qetl.consolidated.test_live_and_gathered_roads_agree:{[t]
    books:.qt.qetl.consolidated.random_books[300;4];
    md:raze books;
    md:update market:`crypto, date:`date$source_time from md;
    md:update rid:til count i by date from md;
    names:`cm`cv;
    .qt.qetl.consolidated.reg[`cm;`consolidated_micro;enlist[`params]!enlist enlist[`crossed]!enlist `as_is];
    .qt.qetl.consolidated.reg[`cv;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 2f];
    syms:3#`BTCUSDT;
    times:.qt.qetl.consolidated.t0+0D00:00:01 0D00:00:02 0D00:00:03;
    clocks:3#`venue;
    live:.qetl.variant.live[names;reverse md;0D00:01:00;syms;times;clocks];
    gathered:.qt.qetl.consolidated.with_market[md;{[a] .qetl.variant.run[{value x};a`targets;a`rf;1;a`names;0D00:01:00]};enlist `targets`rf`names!(([] sym:syms; time:times; clock:clocks);.qt.qetl.consolidated.t0-1D;names)];
    .qunit.assertEquals[gathered;live;"the books read through the mapped gather price what the live buffer does"];
    .qunit.assertTrue[not null first ((live`cm)`same)`mid;"and the comparison is not null against null (as_is, so a crossed random book still prices)"];
    .qt.qetl.consolidated.forget names};

.qt.qetl.consolidated.test_a_batch_over_the_limit_is_split_and_gives_the_same_answer:{[t]
    books:.qt.qetl.consolidated.random_books[400;4];
    s:.qt.qetl.consolidated.slice books;
    n:25;
    syms:n#`BTCUSDT;
    times:.qt.qetl.consolidated.t0+0D00:00:01*1+til n;
    clocks:n#`venue;
    .qt.qetl.consolidated.reg[`cv_one;`consolidated_vwap;enlist[`params]!enlist `qty`max_instants!(2f;1000)];
    .qt.qetl.consolidated.reg[`cv_cap;`consolidated_vwap;enlist[`params]!enlist `qty`max_instants!(2f;7)];
    .qunit.assertEquals[.qetl.reference.instant_limit enlist `cv_cap;7;"the limit is the variant's"];
    .qunit.assertEquals[.qetl.reference.instant_limit `best_top`cv_cap`cv_one;7;"the smallest of those that read ladders"];
    .qunit.assertEquals[.qetl.reference.instant_limit enlist `best_top;0W;"a variant that reads no ladders has none"];
    whole:.qetl.variant.evaluate[enlist `cv_one;s;0D00:01:00;syms;times;clocks];
    split:.qetl.variant.evaluate[enlist `cv_cap;s;0D00:01:00;syms;times;clocks];
    .qunit.assertEquals[(split`cv_cap)`same;(whole`cv_one)`same;"seven at a time, the same 25 prices as all at once"];
    .qunit.assertEquals[count ((split`cv_cap)`same);n;"and in target order"];
    / the mapped road checks the limit before it reads: a gather that is asked for more than 7 probes throws
    h:{[lim;x] if[lim<count x[1;`probes]; '"gather asked for ",string[count x[1;`probes]]," probes"]; .qetl.mapped.gather x[1]}[7];
    md:update market:`crypto, date:`date$source_time from raze books;
    md:update rid:til count i by date from md;
    r:.qt.qetl.consolidated.with_market[md;{[a] .qetl.variant.run[a`h;a`targets;.qt.qetl.consolidated.t0-1D;1;enlist `cv_cap;0D00:01:00]};enlist `h`targets!(h;([] sym:syms; time:times; clock:clocks))];
    .qunit.assertEquals[(r`cv_cap)`same;(whole`cv_one)`same;"no gather saw more than 7 instants, and the 7+7+7+4 pieces put together are the whole"];
    unsplit:{[md;a] .qt.qetl.consolidated.with_market[md;{[a] .qetl.variant.read[a`h;([] sym:a`syms; time:a`times);.qt.qetl.consolidated.t0-1D;1;enlist `cv_cap;0D00:01:00]};enlist a]}[md];
    .qunit.assertThrows[unsplit;`h`syms`times!(h;syms;times);"gather asked for 25 probes";"control: the guard does fire on an unsplit read of 25"];
    .qt.qetl.consolidated.forget `cv_one`cv_cap};

.qt.qetl.consolidated.test_the_per_instant_memory_is_measured_and_bounded:{[t]
    / 1000 instants, 6 venues, 10 levels a side: the books a ladder combine holds
    v:6; lv:10; n:1000;
    t0:.qt.qetl.consolidated.t0;
    mk:{[lv;t0;k;j] .qt.qetl.consolidated.book[`BTCUSDT;`$"v",string j;t0+0D00:00:01*k;100-0.1*til lv;lv#1f;100.5+0.1*til lv;lv#1f]}[lv;t0];
    md:raze {[mk;v;k] raze mk[k;] each til v}[mk;v] each til n;
    s:.qetl.variant.slice md;
    .qt.qetl.consolidated.reg[`cv;`consolidated_vwap;enlist[`params]!enlist enlist[`qty]!enlist 5f];
    times:(t0+0D00:00:01*til n)+0D00:00:00.500;
    r:.Q.ts[.qetl.variant.evaluate;(enlist `cv;s;0D00:00:01;n#`BTCUSDT;times;n#`venue)];
    -1 "consolidated_vwap, ",string[n]," instants x ",string[v]," venues x ",string[lv]," levels: ",string[r[0;0]]," ms, ",string[(r[0;1]) div n]," bytes allocated per instant";
    .qunit.assertTrue[(count ((r 1)`cv)`same)=n;"all instants priced"];
    .qunit.assertFalse[all null (((r 1)`cv)`same)`mid;"and they are priced, so the measure is not of an empty answer"];
    .qunit.assertTrue[(r[0;1])<n*20000;"allocation per instant stays under 20 KB"];
    .qt.qetl.consolidated.forget enlist `cv};
