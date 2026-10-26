/ remote_writer_instance.q - the destination side of a remote backfill (#1650):
/ a q process that owns an HDB and takes batches for it through
/ .qetl.io.remote's receiver. tests/q/run_remote_writer.q starts it; the
/ extractor, in another process, never opens the HDB's files.
/ .
/   q tests/q/remote_writer_instance.q -p 5011 -root /path/to/hdb -journal /path/to/journal
/ .
/ What a deployment would wire, in the smallest form: a grant from the
/ operator's side, and a .z.pg that admits the receiver's entry points and
/ nothing else (.qetl.io.remote.permitted) - plus .rw.rows, this test's probe.

\l src/init.q
\l src/etl/init.q

args:.Q.opt .z.x;
root:hsym `$first args`root;
if[()~key root; [system"mkdir -p ",1_string root; (` sv root,`sym) set `symbol$()]];
.qetl.io.remote.open hsym `$first args`journal;

bounds:`max_date`deadline`destination!(.z.d-1;.z.p+1D;`remote_writer_test);
.qetl.io.remote.serve[`uat;`demo_deals;`mgr`target`schema`max_rows`users!(
    .qetl.io.hdb_scoped[root;`deal_time;bounds];`demo_deals;0#.qpipe.source.demo_deals.fixture[];10000;enlist .z.u)];

/ The probe: each partition date's demo_deals row count, read from disk here.
.rw.rows:{[] d:"D"$string key root; d:d where not null d;
    d!{[r;x] p:.Q.par[r;x;`demo_deals]; $[()~key p; 0; count get ` sv p,`sym]}[root] each d}

.z.pg:{[m]
    if[not any (.qetl.io.remote.permitted m;m~(`.rw.rows;::));
        '"remote_writer: not an entry point"];
    value m}

-1 "remote writer: serving demo_deals into ",(1_string root)," on port ",string system"p";
