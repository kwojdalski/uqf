/ test_variant_gather.q - the mapped side of a list of variants: books deduped, ladders read once through their rids
/ (.qetl.mapped.gather, src/etl/core/mapped_depth.q, #2289).
/ .
/ The gate this holds: the gather reads the ladders of the DISTINCT books the variants' SELECTs matched, and no row beyond
/ them. `.qetl.mapped.depth_reads` counts the ladder rows depth_at indexes, so "reads nothing else" is a number that
/ must equal the distinct books - and a control shows the counter would see a read beyond them. The partitions are stood in
/ for by an in-memory table with a `date` column (what `select ... where date=d` reads); the real mapping, and what it
/ costs in heap, is tests/q/test_variant_scale.q's.

.qt.qetl.mapped.gather.market:{[n;seed]
    system "S ",string seed;
    t:2026.09.15D00:00:00.000000000+n?259200000000000;
    px:1+n?1f;
    rows:([] sym:n?`EURUSD`GBPUSD`USDJPY; source:n?`a`b`c; source_time:t; market:n#`fx; clock:n#`venue;
        bid:px; ask:px+0.0002;
        bid_prices:{x-0.0001*til 3}each px; bid_sizes:n#enlist 1e5 2e5 3e5; ask_prices:{x+0.0002+0.0001*til 3}each px; ask_sizes:n#enlist 1e5 2e5 3e5);
    .qt.support.stand_in_market_data rows}

/ Run f . args with `market_data` standing in for the mapped table, and put the root back.
.qt.qetl.mapped.gather.with_market:{[md;f;args]
    had:$[`market_data in key `.; (1b;get `market_data); (0b;::)];
    `market_data set md;
    r:.[f;args;{[e] `threw,e}];
    $[first had; `market_data set last had; ![`.;();0b;enlist `market_data]];
    if[`threw~first r; 'last r];
    r}

.qt.qetl.mapped.gather.arg:{[groups]
    p:([] sym:12#`EURUSD`GBPUSD`USDJPY; time:2026.09.16D00:00:00+12*0D01:00:00*til 12);
    `probes`read_from`skew`groups`cols!(p;2026.09.14D00:00:00;1;groups;`bid_prices`bid_sizes`ask_prices`ask_sizes)}

.qt.qetl.mapped.gather.test_the_gather_reads_the_ladders_of_the_distinct_books_across_every_select_and_nothing_else:{[t]
    md:.qt.qetl.mapped.gather.market[3000;31];
    groups:([] venues:(`all;enlist `a;`a`b;`all); offset:0D 0D 0D00:00:30 0D00:05:00);
    arg:.qt.qetl.mapped.gather.arg groups;
    r:.qt.qetl.mapped.gather.with_market[md;{[a]
        before:.qetl.mapped.depth_reads;
        g:.qetl.mapped.gather a;
        / what each SELECT matches on its own
        one:{[a;gr] p:update time:time-gr`offset from a`probes; .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;(a`read_from)-gr`offset;a`skew;gr`venues)}[a] each a`groups;
        `slice`reads`counted`each_alone`union!(g`slice;g`reads;.qetl.mapped.depth_reads-before;sum count each one;count select distinct date, rid from raze one)};enlist arg];
    .qunit.assertEquals[r`reads;r`union;"the ladder rows read are the distinct books the SELECTs matched"];
    .qunit.assertEquals[r`counted;r`reads;"and the process's own counter agrees"];
    .qunit.assertTrue[(r`union)<r`each_alone;"the selects overlap, so reading per select would read more: ",string[r`each_alone]," against ",string r`union];
    .qunit.assertEquals[count r`slice;r`union;"one slice row per distinct book"];
    / the ladder of each is the one at ITS rid
    s:r`slice;
    .qunit.assertEquals[(first each s`bid_prices);s`bid;"the ladder read at a book's rid starts at its own flat bid"]};

.qt.qetl.mapped.gather.test_asking_for_a_select_twice_reads_nothing_more:{[t]
    md:.qt.qetl.mapped.gather.market[3000;32];
    once:.qt.qetl.mapped.gather.arg ([] venues:enlist `all; offset:enlist 0D);
    many:.qt.qetl.mapped.gather.arg ([] venues:50#`all; offset:50#0D);
    a:.qt.qetl.mapped.gather.with_market[md;{[x] .qetl.mapped.gather x};enlist once];
    b:.qt.qetl.mapped.gather.with_market[md;{[x] .qetl.mapped.gather x};enlist many];
    .qunit.assertEquals[b`reads;a`reads;"fifty copies of the same SELECT read the same ladders as one"];
    .qunit.assertEquals[b`slice;a`slice;"and gather the same slice"]};

.qt.qetl.mapped.gather.test_the_counter_would_see_a_read_beyond_the_deduped_rids:{[t]
    md:.qt.qetl.mapped.gather.market[3000;33];
    r:.qt.qetl.mapped.gather.with_market[md;{[a]
        b:.qetl.mapped.tob_at`probes`read_from`skew`venues!(a`probes;a`read_from;a`skew;`all);
        before:.qetl.mapped.depth_reads;
        .qetl.mapped.depth_at`books`cols!(b,b;`bid_prices);
        `books`read!(count b;.qetl.mapped.depth_reads-before)};enlist .qt.qetl.mapped.gather.arg ([] venues:enlist `all; offset:enlist 0D)];
    .qunit.assertEquals[r`read;2*r`books;"the control: the same books twice are read twice, and the counter says so - the gather's equality check could fail"]};

.qt.qetl.mapped.gather.test_with_no_depth_variant_no_ladder_is_read:{[t]
    md:.qt.qetl.mapped.gather.market[3000;34];
    arg:.qt.qetl.mapped.gather.arg ([] venues:enlist `all; offset:enlist 0D);
    r:.qt.qetl.mapped.gather.with_market[md;{[a]
        before:.qetl.mapped.depth_reads;
        g:.qetl.mapped.gather a;
        `reads`counted`cols!(g`reads;.qetl.mapped.depth_reads-before;cols g`slice)};enlist arg,enlist[`cols]!enlist `symbol$()];
    .qunit.assertEquals[(r`reads;r`counted);(0;0);"variants of the flat top need no ladder, and none is touched"];
    .qunit.assertFalse[`bid_prices in r`cols;"and the slice carries none"]};
