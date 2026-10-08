/ test_twins.q - a stream job and its backfill twin compute one dataset one way (#884).
/ .
/ .qetl.uptime.twins names a bounded worker as the twin of the stream job
/ whose table it writes; `uqs gaps` offers the twin to refill an outage, and
/ a refill REPLACES the live rows under the shared key. So a twin must
/ re-derive what the stream job publishes, and that was nowhere checked: the
/ demo markout pair computed its rows two ways for as long as it existed.
/ .
/ The rule, over every twin pair: run the twin's transform on the STREAM
/ transform's own examples - with the input names mapped - and it must give
/ the stream's expected rows, on the stream's columns. A pair is listed in
/ `pairs` with that mapping; a new twin fails here until someone lists it.

\d .twintest

/ stream job -> (stream transform; twin transform; twin input name for each stream input)
pairs:(`demo_markout`eq_orderbook)!(
    (`demo_execution_quality;`hdb_demo_markouts_score;`trades`quotes!`trades`quote);
    (`eq_orderbook;`eq_orderbook;()!()))

test_every_twin_pair_is_listed:{[t]
    js:key .qetl.job.stream.jobs;
    have:js where 0<count each .qetl.uptime.twins each js;
    .qunit.assertEquals[asc have;asc key pairs;
        "a stream job with a twin is listed here, so its equivalence is checked"]};

/ The twin's transform reproduces each of the stream transform's examples.
agrees:{[job]
    p:pairs job;
    s:.qetl.transform.registry p 0;
    if[(p 0)~p 1; :1b];
    all {[p;s;ex]
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
        0=count .qetl.transform.differences[c#want;c#got]}[p;s] each s`examples}

test_the_demo_markout_twin_scores_as_the_live_job_does:{[t]
    .qunit.assertTrue[agrees `demo_markout;
        "hdb_demo_markouts_score gives the live transform's rows on its examples"]};

test_the_eq_orderbook_twin_applies_the_same_transform:{[t]
    .qunit.assertTrue[agrees `eq_orderbook;"one transform, shared"]};

\d .
