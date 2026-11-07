/ test_mapped_heap.q - a markout's heap grows with its executions, never with the market (#2288).
/ .
/ The requirement behind the whole change: market data can run to tens of terabytes, and a markout run must never
/ copy it into a process heap. This measures it. tests/q/run_mapped_heap.q builds a synthetic HDB on disk - a sorted
/ market_data partition with p# on sym, the flat top and a row number, and NO depth columns at all, so a markout
/ that selected a ladder would fail outright - then, in a fresh child process that memory-maps it, runs the
/ hdb_fx_markouts source's query and the worker's transform and reports what .Q.w[] says the run added to the heap.
/ .
/ Three claims, each against numbers and not against a diagram:
/   * the heap delta - and the heap's high-water mark, which a transient copy of the rows would raise - is flat as the
/     partition grows tenfold, with the executions held fixed
/   * it grows with the executions
/   * the old way - the same books selected with a where beyond the date - grows with the partition, so the
/     measurement is one that could have failed
/ and the same two claims for a book's DEPTH read through its rid.
/ .
/ Needs a q that can map a partitioned HDB: KDB-X. PeachQ cannot (it has no .Q.pv/.Q.pd), so there the suite only
/ says so; the claims are KDB-X's, as the merge queue's attestation runs them.
/ .
/ UQF_HEAP_ROWS sets the smaller partition's rows per day (default 200,000; the larger is ten times it). A deployment-scale run is
/   UQF_HEAP_ROWS=10000000 scripts/test.py q-unit ...   (100 million rows in the larger partition)


/ The smaller partition's rows per day.
.qt.qetl.mapped.heap.rows:{[] $[count e:getenv`UQF_HEAP_ROWS; "J"$e; 200000]}

/ Run the child; its last RESULT line, as a dictionary.
/ @param mode build, measure, build_deep, measure_depth or measure_copy
/ @param dir the HDB directory
/ @param rows rows per day
/ @param n executions (or books)
.qt.qetl.mapped.heap.child:{[mode;dir;rows;n]
    m:string mode;
    out:system "env ",.qt.support.q_interpreter[]," tests/q/run_mapped_heap.q ",m," ",dir," ",string[rows]," ",string[n]," -q < /dev/null 2>&1";
    line:last out where out like "RESULT|*";
    if[(m like "measure*") and 0=count line; '"run_mapped_heap ",m,": no result - ",last out];
    if[0=count line; :()!()];
    f:"|" vs line;
    `rows`execs`used`peak`mapped`out!"J"$1_f}

/ A scratch HDB directory, removed after.
.qt.qetl.mapped.heap.scratch:{[] first system "mktemp -d"}

/ The heap a run added, with the partition rebuilt at each size.
.qt.qetl.mapped.heap.run:{[build;measure;rows;n]
    dir:.qt.qetl.mapped.heap.scratch[];
    .qt.qetl.mapped.heap.child[build;dir;rows;n];
    r:.qt.qetl.mapped.heap.child[measure;dir;rows;n];
    system "rm -rf ",dir;
    r}

.qt.qetl.mapped.heap.test_the_twins_heap_is_flat_as_the_market_grows_and_grows_with_the_executions:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    n:.qt.qetl.mapped.heap.rows[];
    small:.qt.qetl.mapped.heap.run[`build;`measure;n;1000];
    big:.qt.qetl.mapped.heap.run[`build;`measure;10*n;1000];
    more:.qt.qetl.mapped.heap.run[`build;`measure;n;2000];
    .qunit.assertTrue[(small`out)>0;"the run scored executions"];
    .qunit.assertTrue[(big`used)<(1.25*small`used)+1000000;"ten times the market, the same heap: ",string[small`used]," -> ",string big`used];
    .qunit.assertTrue[(big`peak)<=(small`peak)+16777216;"nor does its high-water mark grow with the market: a transient copy of a sym's rows would show here: ",string[small`peak]," -> ",string big`peak];
    .qunit.assertTrue[(more`used)>1.4*small`used;"twice the executions, more heap: ",string[small`used]," -> ",string more`used]};

.qt.qetl.mapped.heap.test_selecting_the_books_with_a_where_beyond_the_date_grows_with_the_market:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    n:.qt.qetl.mapped.heap.rows[] div 10;
    small:.qt.qetl.mapped.heap.run[`build_deep;`measure_copy;n;0];
    big:.qt.qetl.mapped.heap.run[`build_deep;`measure_copy;10*n;0];
    .qunit.assertTrue[(big`used)>5*small`used;"the control: the copy grows with the partition (",string[small`used]," -> ",string[big`used],"), which is what the aj avoids"]};

.qt.qetl.mapped.heap.test_a_books_depth_read_through_its_rid_is_flat_as_the_market_grows:{[t]
    if[.qetl.io.on_peachq; :.qunit.assertTrue[1b;"PeachQ cannot map a partitioned HDB: measured on KDB-X"]];
    n:.qt.qetl.mapped.heap.rows[] div 10;
    small:.qt.qetl.mapped.heap.run[`build_deep;`measure_depth;n;500];
    big:.qt.qetl.mapped.heap.run[`build_deep;`measure_depth;10*n;500];
    .qunit.assertTrue[(small`out)>0;"matched books' ladders were read"];
    .qunit.assertTrue[(big`used)<(1.25*small`used)+500000;"ten times the market, the same heap: ",string[small`used]," -> ",string big`used]};
