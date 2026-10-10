"""The worked example a scaffolded backfill gets: one run, no stack.

`uqs job new` used to stop before "it runs": seeing a new worker publish
anything meant writing a script like scripts/examples/hdb_transfer_example.q
by hand (#713). This writes that script with the job, so the first thing
after scaffolding can be

    q scripts/examples/<name>_example.q

which runs the worker once on its fixture into a throwaway HDB under build/,
prints what it published, and exits 1 if that is nothing. The q-scripts lane
runs every script in that directory, so the example is exercised from the
first commit.

ONE RUN, NOT TWO. Coverage is one table per process, so a second run over the
same window in the same process is idle by design - it would test the ledger,
not the job. A `local` source therefore takes ONE of two paths: the fixture
while its `query` is still the scaffold's stub, and a source HDB built from
that fixture, read through the live transport, once it is written.

NOT A PLACEHOLDER. The script carries no SCAFFOLDED marker: it runs as
written, and test_no_scaffold_left.py would otherwise demand it be rewritten
before it has been needed.
"""

from __future__ import annotations

from pathlib import Path

#: Where worked examples live; the q-scripts lane globs `*.q` here.
EXAMPLES_DIR = Path("scripts/examples")


#: The line every generated example carries. `uqs job remove` deletes an
#: example only while it does: a hand-written one (hdb_transfer's) is reported,
#: never deleted.
GENERATED_BY = "Written by `uqs job new"


def example_path(name: str) -> Path:
    """The example script for job `name` - also what `uqs job remove` deletes."""
    return EXAMPLES_DIR / f"{name}_example.q"


def _local_block(src: str, dataset: str, var: str) -> tuple[str, str]:
    """(definitions, run) for a `local` source: build a source HDB from the
    fixture, and read it live once the query is written."""
    defs = f"""
/ The source HDB: the fixture on disk, one date partition per day, symbols
/ enumerated against its own sym file - what a `local` source reads.
src_hdb:hsym `$dir,"/source_hdb"
partition:{{[d] ` sv src_hdb,(`$string d),`{dataset},`}}
write_day:{{[d] (partition d) set .Q.en[src_hdb;select from fixture where d=`date$time]}}
write_source:{{[] write_day each distinct `date$fixture`time}}

/ Still the scaffold's throwing stub? Then there is no live path to read yet.
query_is_stub:{{[] 0<count ss[string .qpipe.source.{src}.query;"not implemented"]}}
"""
    run = f"""
$[.{{ns}}.query_is_stub[];
    [-1 "  {src} is local, but its query is still the scaffold's stub, so this reads";
     -1 "  the fixture. Write .qpipe.source.{src}.query, and it reads a source HDB";
     -1 "  built from the fixture instead, through the live `local` path."];
    [.{{ns}}.write_source[];
     setenv[`{var};1_string .{{ns}}.src_hdb];
     -1 "  reading the source HDB ",(1_string .{{ns}}.src_hdb)," through the live `local` path"]];
"""
    return defs, run


def example_body(
    name: str, worker: str, src: str, dataset: str, transport: str, credential: str
) -> str:
    """The example script for a new bounded worker.

    `credential` is the variable a live run reads (`credential_var`), cleared
    here so the run is the fixture's whatever the operator's shell holds.
    """
    ns = f"{name}ex"
    local_defs, local_run = (
        _local_block(src, dataset, credential) if transport == "local" else ("", "")
    )
    return f"""// {name}_example.q - run the {worker} bounded worker once, on its
// own fixture, into a throwaway HDB, and show what it published.
//
//   q {example_path(name).as_posix()}          (from the repository root)
//   q {example_path(name).as_posix()} -keep    keep what it wrote, under build/
//
// No TorQ and no running stack: everything it writes is under one directory
// in build/, removed at the end unless -keep. The q-scripts lane runs it, and
// it exits 1 unless the run completes and publishes at least one row - so a
// quality check or a derive the scaffold left throwing fails it until written.
// Written by `uqs job new {name} --kind backfill`; grow it with the job.

\\l src/init.q
\\l src/etl/init.q

\\d .{ns}

dir:(first system"pwd"),"/build/{name}_example_",string .z.i
dst:hsym `$dir,"/destination_hdb"
fixture:.qpipe.source.{src}.fixture[]

/ Whole days around the fixture's rows, half-open, so every row is inside.
range_from:`timestamp$`date$min fixture`time
range_to:`timestamp$1+`date$max fixture`time
{local_defs}
/ Run the worker over [range_from;range_to), as the backfill process does.
run:{{[]
    ns:(.qetl.job.bounded.def `{worker})`ns;
    (` sv ns,`init)[`source_version`range_from`range_to!(`v1;range_from;range_to)];
    (` sv ns,`run)[]}}

/ What the run left in the destination HDB, read back from disk.
published:{{[] .qetl.source.local_read[dst;`{dataset};range_from;range_to]}}

/ Exit 1 with a message when a check fails, so the q-scripts lane fails too.
check:{{[ok;what] $[ok; -1 "  ok    ",what; [-1 "  FAIL  ",what; exit 1]]}}

\\d .

-1 "\\n== 1. {src}'s fixture: ",string[count .{ns}.fixture]," row(s)";
-1 "  run over [",string[.{ns}.range_from],";",string[.{ns}.range_to],")";
system "rm -rf ",.{ns}.dir;
system "mkdir -p ",.{ns}.dir,"/status";
setenv[`UQF_STATUS_DIR;.{ns}.dir,"/status"];
/ No credential: the worker reads the fixture, the demo path it warns about,
/ which a run must ask for before it may write it (#1082).
setenv[`{credential};""];
setenv[`UQF_FIXTURE_WRITES;enlist "1"];
{local_run.replace("{ns}", ns)}
-1 "\\n== 2. run {worker} into ",1_string .{ns}.dst;
/ Where rows go is the runner's choice: here, an HDB partitioned on `time`.
.qetl.io.default:.qetl.io.hdb[.{ns}.dst;`time];
r:.{ns}.run[];
-1 "  ",string[r`state],", ",string[r`rows_published]," row(s) published";
.{ns}.check[`completed~r`state;"the run completed"];

-1 "\\n== 3. read {dataset} back from disk";
out:.{ns}.published[];
show out;
.{ns}.check[0<count out;"at least one row was written"];
.{ns}.check[(count out)=r`rows_published;"every published row is on disk"];

$[`keep in key .Q.opt .z.x;
    -1 "\\nkept: ",.{ns}.dir;
    [system "rm -rf ",.{ns}.dir; -1 "\\nremoved ",.{ns}.dir," - pass -keep to look at it"]];
exit 0
"""
