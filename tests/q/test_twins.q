/ test_twins.q - a stream job and its backfill twin compute one dataset one way (#884).
/ .
/ .qetl.uptime.twins names a bounded worker as the twin of the stream job
/ whose table it writes; `uqs job gaps` offers the twin to refill an outage, and
/ a refill REPLACES the live rows under the shared key. So a twin must
/ re-derive what the stream job publishes, and that was nowhere checked: the
/ demo markout pair computed its rows two ways for as long as it existed.
/ .
/ The rule, over every twin pair: run the twin's transform on the STREAM
/ transform's own examples - with the input names mapped - and it must give
/ the stream's expected rows, on the stream's columns. Which transform each
/ side applies is read from its own declaration - the stream job's
/ `transform` and the twin's - so it cannot drift from what runs; only the
/ input renaming is listed, in `renames`, and a new twin fails here until
/ someone lists it.


/ stream job -> (stream transform; twin transform; twin input name for each stream input)
/ Stream transform input -> twin transform input, where they differ.
.qt.qpipe.twins.renames:(`demo_markout`exec_bars`crypto_book_1s`order_quality`book_liquidity_1m`fx_markout`fx_markout_wide)!(`trades`quotes!`trades`quote;enlist[`tape]!enlist `executions;enlist[`book]!enlist `book;()!();enlist[`books]!enlist `books;()!();()!())

/ A bundle's twin pair lists its own renaming, as .qt.qpipe.job.<job>.twin_renames in its test file
/ (the databento bundle's eq_orderbook is one): a name the job's suite owns has no load order, and the
/ tree's dictionary above cannot take an entry from a file that may not be installed. Or :: when it has none.
.qt.qpipe.twins.own_renames:{[j] @[get;` sv .qt.support.job_suite[j],`twin_renames;{[e] ::}]}

/ The renaming for a stream job with a twin: the tree's, else its own test file's.
.qt.qpipe.twins.renames_of:{[j] $[j in key .qt.qpipe.twins.renames; .qt.qpipe.twins.renames j; .qt.qpipe.twins.own_renames j]}

/ Twins whose equivalence is a live run, not an example: the twin reads the RAW orders and
/ applies the adapter itself, so its inputs are not the stream transform's and `agrees` cannot
/ feed it the stream's examples. Stream job -> where the equivalence is held.
.qt.qpipe.twins.own_test:enlist[`order_quality]!enlist "tests/q/test_hdb_order_quality_backfill.q: the live job and the worker over one hour, row for row"

/ (the stream job's transform; its twin's; the input renaming), each
/ transform as the declaration names it.
.qt.qpipe.twins.pair:{[job]
    twin:first .qetl.uptime.twins job;
    ((.qetl.job.stream.def job)`transform;(.qetl.job.bounded.def twin)`transform;.qt.qpipe.twins.renames_of job)}

.qt.qpipe.twins.with_twins:{[] js:key .qetl.job.stream.jobs; js where 0<count each .qetl.uptime.twins each js}

.qt.qpipe.twins.test_every_twin_pair_is_listed:{[t]
    listed:{[j] (j in key .qt.qpipe.twins.renames) or 99h=type .qt.qpipe.twins.own_renames j};
    .qunit.assertEquals[.qt.qpipe.twins.with_twins[] where not listed each .qt.qpipe.twins.with_twins[];`symbol$();
        "a stream job with a twin is listed here (or names its renames in its own test file), so its equivalence is checked"];
    .qunit.assertEquals[(key .qt.qpipe.twins.renames) except .qt.qpipe.twins.with_twins[];`symbol$();
        "and nothing listed here has lost its twin"]};

/ The twin's transform reproduces each of the stream transform's examples.
.qt.qpipe.twins.test_a_stream_job_with_a_twin_declares_its_transform:{[t]
    / What --twin-of scaffolds into the twin, and what this suite compares.
    .qunit.assertEquals[{null (.qetl.job.stream.def x)`transform} each .qt.qpipe.twins.with_twins[];(count .qt.qpipe.twins.with_twins[])#0b;
        "every stream job a twin refills names the transform it applies"]};

.qt.qpipe.twins.agrees:{[job]
    if[job in key .qt.qpipe.twins.own_test; :1b];
    p:.qt.qpipe.twins.pair[job];
    s:.qetl.transform.registry p 0;
    if[(p 0)~p 1; :1b];
    all {[p;ex]
        ins:ex`inputs;
        named:$[count p 2; (p[2] key ins)!value ins; ins];
        / each input cut to the columns the twin's transform declares: a
        / source reads only what it needs, and the transform refuses more
        want_cols:cols each .qetl.transform.registry[p 1]`inputs;
        named:(key named)!{[w;c;t] (w c)#t}[want_cols]'[key named;value named];
        got:.qetl.transform.apply[p 1;named];
        want:ex`expected;
        c:cols want;
        / the comparison the stream transform's own examples are held to:
        / float columns within .qetl.transform.float_tolerance
        0=count .qetl.transform.differences[c#want;c#got]}[p] each s`examples}

.qt.qpipe.twins.test_the_demo_markout_twin_scores_as_the_live_job_does:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`demo_markout];
        "hdb_demo_markouts_score gives the live transform's rows on its examples"]};

.qt.qpipe.twins.test_the_fx_markout_twins_score_as_the_live_jobs_do:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`fx_markout];"hdb_fx_markouts_long gives the live long layout's rows on its examples"];
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`fx_markout_wide];"hdb_fx_markouts_wide gives the live wide layout's rows on its examples"]};

.qt.qpipe.twins.test_the_exec_bars_twin_cuts_and_aggregates_as_the_live_job_does:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`exec_bars];"hdb_exec_bars gives the live bars on the live transform's example"]};

.qt.qpipe.twins.test_the_crypto_book_1s_twin_picks_the_snapshots_as_the_live_job_does:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`crypto_book_1s];"hdb_crypto_book_1s gives the live snapshots on the live transform's example"]};

.qt.qpipe.twins.test_the_book_liquidity_1m_twin_averages_the_books_as_the_live_job_does:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`book_liquidity_1m];"hdb_book_liquidity_1m gives the live minutes on the live transform's example"]};

/ A twin excused from `agrees` names the test that holds it, and the test exists.
.qt.qpipe.twins.test_a_twin_checked_by_its_own_test_names_it_and_it_exists:{[t]
    .qunit.assertEquals[key .qt.qpipe.twins.own_test;enlist `order_quality;"listed"];
    f:"tests/q/test_hdb_order_quality_backfill.q";
    .qunit.assertTrue[any read0[hsym `$f] like ".qt.qpipe.job.hdb_order_quality_backfill.test_the_twin_gives_the_live_series_over_an_hour_row_for_row:*";"the named test is in the named file"]};
