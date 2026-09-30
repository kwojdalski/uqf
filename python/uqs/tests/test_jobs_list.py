"""`uqs list jobs`: every declared job, read from its declaration.

Held against the reader the process registry is built from, so the list can
neither miss a job the registry knows nor invent one; and against scaffolds
written into a scratch tree, so `state` tells a scaffold from written work.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model.declarations import read_declarations
from uqs.model.jobs import job_rows
from uqs.stack import listing

UQF_ROOT = Path(__file__).resolve().parents[3]


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for d in ("src/etl/streaming", "src/etl/workers"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def test_every_job_the_registry_reads_is_listed_once():
    """Reactions are listed too, but have no process, so the registry - which
    is a list of processes - never reads them: they are held to it apart."""
    rows = job_rows(UQF_ROOT)
    listed = [row["job"] for row in rows if row["kind"] != "reaction"]
    assert sorted(listed) == sorted(d.name for d in read_declarations(UQF_ROOT))
    assert len(rows) == len({row["job"] for row in rows})


def test_every_kind_is_represented_in_the_real_tree():
    """`reaction` since #529, when rebuild_positions became the first one the
    tree runs rather than only tests."""
    assert {row["kind"] for row in job_rows(UQF_ROOT)} == {
        "feed",
        "etl",
        "normalizer",
        "backfill",
        "reaction",
    }


def test_it_is_a_list_kind():
    assert listing.LISTABLE_KINDS["jobs"] is listing._list_jobs


def test_a_backfill_reads_its_source_and_writes_its_dataset_and_partition(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/etl/workers/w.q": ".qetl.job.bounded.define[`w;`source`dataset`width`transform"
            "`partition`procname!(`s;`fx;1D;`s_passthrough;`EURUSD;`wbf1)];\n"
        },
    )
    (row,) = job_rows(root)
    assert (row["kind"], row["procname"], row["reads"], row["writes"], row["starts"]) == (
        "backfill",
        "wbf1",
        "source s",
        "fx [EURUSD]",
        "triggered",
    )


def test_state_tells_a_scaffold_from_written_work(tmp_path):
    decl = (
        ".qetl.job.stream.define[`{n};`procname`subscribe_to`publishes`period`on_timer!"
        "(`{n}1;`symbol$();enlist `t;0D00:00:01;{{}})];\n"
    )
    root = _tree(
        tmp_path,
        {
            "src/etl/streaming/a.q": "/ SCAFFOLDED. throws until written\n" + decl.format(n="a"),
            "src/etl/streaming/b.q": decl.format(n="b"),
        },
    )
    states = {row["job"]: (row["state"], row["starts"], row["kind"]) for row in job_rows(root)}
    assert states == {
        "a": ("scaffolded", "on demand", "feed"),
        "b": ("written", "on demand", "feed"),
    }
