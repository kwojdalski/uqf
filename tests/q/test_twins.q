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
/ the stream's expected rows, on the stream's columns. Which transform each
/ side applies is read from its own declaration - the stream job's
/ `transform` and the twin's - so it cannot drift from what runs; only the
/ input renaming is listed, in `renames`, and a new twin fails here until
/ someone lists it.

\d .twintest

/ stream job -> (stream transform; twin transform; twin input name for each stream input)
/ Stream transform input -> twin transform input, where they differ.
renames:(`demo_markout`eq_orderbook)!(`trades`quotes!`trades`quote;()!())

/ (the stream job's transform; its twin's; the input renaming), each
/ transform as the declaration names it.
pair:{[job]
    twin:first .qetl.uptime.twins job;
    ((.qetl.job.stream.def job)`transform;(.qetl.job.bounded.def twin)`transform;renames job)}

with_twins:{[] js:key .qetl.job.stream.jobs; js where 0<count each .qetl.uptime.twins each js}

test_every_twin_pair_is_listed:{[t]
    .qunit.assertEquals[asc with_twins[];asc key renames;
        "a stream job with a twin is listed here, so its equivalence is checked"]};

/ The twin's transform reproduces each of the stream transform's examples.
test_a_stream_job_with_a_twin_declares_its_transform:{[t]
    / What --twin-of scaffolds into the twin, and what this suite compares.
    .qunit.assertEquals[{null (.qetl.job.stream.def x)`transform} each with_twins[];(count with_twins[])#0b;
        "every stream job a twin refills names the transform it applies"]};

agrees:{[job]
    p:pair job;
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
    .qunit.assertTrue[agrees[`demo_markout];
        "hdb_demo_markouts_score gives the live transform's rows on its examples"]};

test_the_eq_orderbook_twin_applies_the_same_transform:{[t]
    .qunit.assertTrue[agrees[`eq_orderbook];"one transform, shared"]};

\d .
