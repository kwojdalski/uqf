/ test_variant_scale.q - a thousand reference variants over one batch cost one gather (#2289).
/ .
/ The claim, measured and not argued: against a memory-mapped HDB with three-level ladders, price the same batch of
/ executions under 1, 10, 100 and 1000 declared variants (tests/q/run_mapped_heap.q measure_variants, a fresh process
/ each). The books gathered, the ladder rows read through their rids and the heap after the gather are the SAME at every
/ count - only the evaluation, a transform per distinct quantity and a combine per variant, grows. A second set varies
/ the SELECTs (offsets): then the gather grows with the distinct SELECTs, never with the variants.
/ .
/ Needs a q that can map a partitioned HDB: KDB-X. On PeachQ the suite says so and holds nothing: the claims are KDB-X's.
/ UQF_HEAP_ROWS sets the market's rows per day, as test_mapped_heap.q's does.

.qt.qetl.variant.scale.rows:{[] $[count e:getenv`UQF_HEAP_ROWS; "J"$e; 200000]}

/ Build the HDB once, measure it at each variant count, remove it.
/ @param counts the variant counts
/ @param selects the number of distinct SELECT offsets they spread over
/ @return a table variants, groups, slice, reads, gather_heap, gather_ms, eval_ms, eval_heap
.qt.qetl.variant.scale.run:{[rows;counts;selects]
    dir:first system "mktemp -d";
    system "env ",.qt.support.q_interpreter[]," tests/q/run_mapped_heap.q build_deep ",dir," ",string[rows]," 0 -q < /dev/null 2>&1";
    one:{[dir;rows;selects;nv]
        out:system "env ",.qt.support.q_interpreter[]," tests/q/run_mapped_heap.q measure_variants ",dir," ",string[rows]," 500 ",string[nv]," ",string[selects]," -q < /dev/null 2>&1";
        line:last out where out like "RESULT|*";
        if[0=count line; '"run_mapped_heap measure_variants: no result - ",last out];
        f:"J"$1_"|" vs line;
        `variants`groups`slice`reads`gather_heap`gather_ms`eval_ms`eval_heap!f 7 8 5 6 2 9 10 11}[dir;rows;selects];
    r:one each counts;
    system "rm -rf ",dir;
    r}

.qt.qetl.variant.scale.test_variants_beyond_the_first_cost_no_gather_and_no_heap_before_the_evaluation:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    r:.qt.qetl.variant.scale.run[.qt.qetl.variant.scale.rows[];1 10 100 1000;1];
    .qunit.assertTrue[(first r`slice)>0;"books were gathered"];
    .qunit.assertEquals[count distinct r`slice;1;"the gathered slice is the same at 1, 10, 100 and 1000 variants: ",", " sv string r`slice];
    .qunit.assertEquals[r`reads;r`slice;"and the ladder rows read are exactly the slice, never more: ",", " sv string r`reads];
    .qunit.assertEquals[count distinct r`groups;1;"one SELECT group serves all of them"];
    .qunit.assertTrue[(max r`gather_heap)<=(min r`gather_heap)+1048576;"and the heap after the gather is flat: ",", " sv string r`gather_heap];
    .qunit.assertTrue[(last r`eval_heap)>(first r`eval_heap);"what grows is the evaluation: the prices of the variants (heap ",string[first r`eval_heap]," -> ",string[last r`eval_heap],")"]};

.qt.qetl.variant.scale.test_the_gather_is_flat_as_the_market_grows_tenfold:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    n:.qt.qetl.variant.scale.rows[];
    small:first .qt.qetl.variant.scale.run[n;enlist 100;1];
    big:first .qt.qetl.variant.scale.run[10*n;enlist 100;1];
    .qunit.assertTrue[(big`gather_heap)<(1.25*small`gather_heap)+500000;"ten times the market, the same heap: ",string[small`gather_heap]," -> ",string big`gather_heap];
    .qunit.assertTrue[(big`reads)<2*small`reads;"and about the same ladder rows: ",string[small`reads]," -> ",string big`reads]};

.qt.qetl.variant.scale.test_more_selects_gather_more_books_but_only_by_the_distinct_selects:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    r:.qt.qetl.variant.scale.run[.qt.qetl.variant.scale.rows[];100 1000;3];
    .qunit.assertEquals[r`groups;3 3;"three offsets are three SELECT groups at 100 and at 1000 variants"];
    .qunit.assertEquals[first r`slice;last r`slice;"and the same slice"];
    .qunit.assertEquals[r`reads;r`slice;"read once"]};
