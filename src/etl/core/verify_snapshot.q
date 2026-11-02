/ verify_snapshot.q - the read-only proof, and the capture that is held to it
/ (.qetl.verify, #1851).
/ .
/ `snapshot` reads everything a publication, a cursor advance, a checkpoint or a
/ coverage record would change - the status directory's files by content, each
/ streaming producer's cursor, the coverage ledger, the dataset's tables - and
/ `capture` takes one before and one after the producers run, and reports whether
/ they are equal. "Read only" is therefore a measured property of every verification
/ run, in q, whatever calls it, and not a claim.

\d .

/ Private: every file under a path, as (path; bytes), a directory walked and a
/ missing one empty. Content, not size: a cursor is the same length before and after.
/ @private
.qetl.verify.tree:{[p]
    k:key p;
    $[0=count k; ();
      -11h=type k; enlist (p;read1 p);
      raze .qetl.verify.tree each ` sv/: p,/:k]}

/ Everything verification promises not to change, for `dataset`'s producers.
/ @param dataset the dataset, a symbol
/ @return a dict of files (the status directory's, by content), cursors (each
/   streaming producer's saved cursor), coverage (the ledger, or empty when none
/   is attached) and tables (the dataset's in-process and plant tables)
/ @eg key .qetl.verify.snapshot `demo_deals  ->  `files`cursors`coverage`tables
.qetl.verify.snapshot:{[dataset]
    p:.qetl.verify.producers dataset;
    jobs:(p`producer) where `stream=p`mode;
    cur:{[j] .qetl.job.stream.cursor_ops[j][`load] .qetl.job.stream.cursor_name j} each jobs;
    plant:` sv `.qetl.plant,dataset;
    `files`cursors`coverage`tables!(
        .qetl.verify.tree hsym `$.qetl.job.bounded.state.lock_dir[];
        $[count jobs; jobs!cur; ()!()];
        $[`etl_coverage in tables `.; .qetl.coverage.ledger[]; ()];
        (`root`plant)!($[dataset in tables `.; value dataset; ()]; $[dataset in .qetl.plant.names[]; get plant; ()]))}

/ Capture one producer, never throwing: a producer that cannot be captured is a result
/ that says why, so the report can call it UNVERIFIED.
/ @param prod one row of .qetl.verify.producers, as a dict
/ @param dataset the dataset
/ @param from_ts the window's first instant
/ @param to_ts the instant after its last
/ @return a capture result, with `error` set when the capture threw
/ @eg (.qetl.verify.capture_one[`producer`mode!(`demo_deals_feed;`stream);`demo_deals;2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000])`fetched  ->  1
.qetl.verify.capture_one:{[prod;dataset;from_ts;to_ts]
    if[`reaction=prod`mode;
        r:.qetl.verify.capture_result[prod`producer;`reaction];
        r[`error]:string[prod`producer]," is a reaction: it runs on a published window and its output is not captured here";
        :r];
    r:$[`stream=prod`mode;
        .[.qetl.verify.capture_stream;(prod`producer;dataset;from_ts;to_ts);{[e] (`capture_failed;e)}];
        .[.qetl.verify.capture_bounded;(prod`producer;from_ts;to_ts);{[e] (`capture_failed;e)}]];
    if[(0h=type r) and `capture_failed~first r;
        why:last r;
        r:.qetl.verify.capture_result[prod`producer;prod`mode];
        r[`error]:"capture of ",string[prod`producer]," failed: ",why];
    r}

/ Capture every producer of `dataset` over one closed window, and prove nothing changed.
/ @param dataset the dataset, a symbol
/ @param from_ts the window's first instant
/ @param to_ts the instant after its last, after from_ts
/ @return a dict of producers (the table), captures (producer -> (result; ::), a pair
/   because a list of same-keyed dicts is a table in q), readonly (1b when the
/   snapshot taken after equals the one before), before and after
/ @throws error when the interval is empty or reversed, or nothing produces the dataset
/ @eg .qetl.verify.capture[`demo_deals;2026.09.11D00:00:00.000000000;2026.09.14D00:00:00.000000000]`readonly  ->  1b
.qetl.verify.capture:{[dataset;from_ts;to_ts]
    .qetl.coverage.require_interval[from_ts;to_ts];
    p:.qetl.verify.producers dataset;
    if[0=count p; '"capture: nothing publishes or backfills ",string[dataset]];
    before:.qetl.verify.snapshot dataset;
    rows:{[p;i] `producer`mode!(p[`producer] i;p[`mode] i)}[p] each til count p;
    caps:{[d;f;t;r] (.qetl.verify.capture_one[r;d;f;t];::)}[dataset;from_ts;to_ts] each rows;
    after:.qetl.verify.snapshot dataset;
    `producers`captures`readonly`before`after!(p;(p`producer)!caps;before~after;before;after)}

\d .
