"""Python's producer map, held to q's job graph (#1862).

q has the whole map: `.qetl.dag.producers`, after `.qetl.dag.adopt_all[]`.
Python used to keep three partial copies of it. `jobs.bounded_producers` saw
only bounded workers, and `dependencies.producers_by_table` and
`plant_schema._publishers` saw only streaming publishers. None of them had a
reaction's output: `deal_positions`, written by `demo_deals~rebuild_positions`,
had no producer anywhere in Python.

Now there is one map, `model/producers.py`, and the narrower ones are its
projections. These tests hold it to q's map twice. The first compares it with
`q_facts.DAG_PRODUCERS`, which q exports and a hook `--check`s; that test
needs no q. The second compares it with a live q, where there is one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from uqs.generated import q_facts
from uqs.interpreter import q_interpreter
from uqs.model import dependencies, producers
from uqs.model.jobs import bounded_producers

UQF_ROOT = Path(__file__).resolve().parents[3]

#: q's producer map, built the way export_q_facts.q builds DAG_PRODUCERS.
_DUMP = """
\\l src/init.q
\\l src/etl/init.q
.qetl.dag.adopt_all[];
outs:asc distinct raze exec outputs from .qetl.dag.jobs;
-1 .j.j outs!{string asc .qetl.dag.producers x} each outs;
exit 0
"""


def _q_producers() -> dict[str, tuple[str, ...]]:
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    # A script FILE, not stdin: q continues a multi-line lambda only in a file.
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "dump.q"
        script.write_text(_DUMP)
        result = subprocess.run(
            [str(q), str(script), "-q"],
            cwd=UQF_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    assert lines, f"q did not load the ETL tree:\n{(result.stdout + result.stderr)[-2000:]}"
    return {t: tuple(jobs) for t, jobs in json.loads(lines[-1]).items()}


def differences(python: dict, q: dict) -> list[str]:
    """Every table where the two maps disagree, as readable lines."""
    return [
        f"{table}: python {python.get(table, ())!r}, q {q.get(table, ())!r}"
        for table in sorted(set(python) | set(q))
        if tuple(python.get(table, ())) != tuple(q.get(table, ()))
    ]


def test_the_producer_map_is_the_one_q_exports_reactions_included():
    q = q_facts.DAG_PRODUCERS
    assert q["deal_positions"] == ("demo_deals~rebuild_positions",), (
        "the exported map must carry a reaction's output, or this test proves nothing about one"
    )
    assert differences(producers.job_producers(UQF_ROOT), q) == []


def test_the_producer_map_is_what_a_running_q_says():
    """The committed export can be stale between a q change and the q-facts
    hook, so where q is available the map is compared with q itself."""
    q = _q_producers()
    assert q, "q's job graph has no outputs - the comparison would be vacuous"
    assert differences(producers.job_producers(UQF_ROOT), q) == []


def test_a_disagreement_is_reported_not_passed_over():
    python = {"a": ("x",), "b": ("y",)}
    q = {"a": ("x", "z"), "c": ("w",)}
    assert differences(python, q) == [
        "a: python ('x',), q ('x', 'z')",
        "b: python ('y',), q ()",
        "c: python (), q ('w',)",
    ]


def test_a_reaction_runs_in_the_processes_that_fill_what_it_watches():
    [job] = producers.producers(UQF_ROOT)["deal_positions"]
    assert job.kind == producers.REACTION
    assert job.processes == tuple(bounded_producers(UQF_ROOT)["demo_deals"])


def test_the_bounded_map_is_the_bounded_projection():
    by_job = producers.producers(UQF_ROOT)
    expected = {
        t: [p for j in jobs if j.kind == producers.BOUNDED for p in j.processes]
        for t, jobs in by_job.items()
    }
    assert bounded_producers(UQF_ROOT) == {t: ps for t, ps in expected.items() if ps}
    assert "deal_positions" not in bounded_producers(UQF_ROOT), (
        "a reaction's output fires no reaction, so it must not be offered as one to watch"
    )


def test_the_plant_map_is_the_streaming_projection():
    """`producers_by_table` is built from the process registry, the route the
    generated database.q takes; it must name the same publishers as the job
    graph's streaming jobs, in both directions."""
    assert dependencies.producers_by_table() == producers.plant_producers(UQF_ROOT)


def test_a_new_reaction_is_in_the_map_before_q_has_seen_it(tmp_path):
    """The reason the map is read from the tree rather than only from the
    export: a reaction `uqs job new` has just written is a producer at once."""
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    (tmp_path / "src/etl/reactions/rebuild_more.q").write_text(
        ".qetl.reaction.on_writing[`demo_deals;`rebuild_more;enlist `more_positions;{[d;f;t] }];\n"
    )
    [job] = producers.producers(tmp_path)["more_positions"]
    assert job.job == "demo_deals~rebuild_more"
    assert job.processes == tuple(bounded_producers(tmp_path)["demo_deals"])
