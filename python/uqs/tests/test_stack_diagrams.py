"""The stack diagrams against the registry they draw (#890).

docs/diagrams/stack-topology.d2 and stack-dataflow.d2 are hand-written, and
the topology one names its own authority: docs/reference/processes.md,
GENERATED from the pipeline registry - "if this diagram and that table
disagree, the table is right". Until this test nothing compared them, and
they had drifted: a backfill under a name no process has, jobs added since
and never drawn. render_diagrams.py --check proves only that each .svg
renders from its .d2.

Two rules, for each diagram:

- every `<procname> :<port>` it prints names a real process at its real
  port: a pipeline's from PIPELINES, a vendored one's from the starter
  pack's own appconfig/process.csv (read, never edited);
- every pipeline is drawn, or is in that diagram's OMITTED with the reason
  it is left out. A new job therefore fails here until someone decides.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from uqs.model.registry import PIPELINES

ROOT = Path(__file__).resolve().parents[3]
DIAGRAMS = ROOT / "docs" / "diagrams"
VENDORED_CSV = ROOT / "lib" / "torq-finance-starter-pack" / "appconfig" / "process.csv"
BASE = 6050

#: Pipelines each diagram leaves out on purpose, and why. Everything else in
#: PIPELINES must appear in it.
OMITTED: dict[str, dict[str, str]] = {
    "stack-topology.d2": {},
    "stack-dataflow.d2": {
        # which process writes which TABLE through the tickerplant: a bounded
        # worker writes through .qetl.io into its own process instead, and
        # stack-topology.d2 draws the backfills for that reason
        **{
            p.procname: "a backfill - writes into its own process, never stp1"
            for p in PIPELINES
            if p.kind.value == "backfill"
        },
        "tap1": "subscribes and logs; it writes no table",
        "alert_sink1": "an outbound sink - reads fx_limit_breach, writes no table",
    },
}

#: `name :port`, as the diagrams print a process.
_LABEL = re.compile(r"\b([a-z][a-z0-9_]*) :(\d{4})\b")


def _vendored() -> dict[str, int]:
    """The starter pack's processes and their ports at the diagrams' base."""
    ports = {}
    with VENDORED_CSV.open() as f:
        for row in csv.DictReader(f):
            m = re.fullmatch(r"\{KDBBASEPORT\}(?:\+(\d+))?", row["port"].strip())
            if m:
                ports[row["procname"]] = BASE + int(m.group(1) or 0)
    return ports


def _text(diagram: str) -> str:
    """The diagram, with d2's escaped newlines inside labels made real, so
    `"fxfeed1 :6069\\nFX top of book"` reads as its lines."""
    lines = (DIAGRAMS / diagram).read_text().splitlines()
    body = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    return body.replace("\\n", "\n")


def _registry() -> dict[str, int]:
    return {p.procname: BASE + p.offset for p in PIPELINES if p.offset is not None}


@pytest.mark.parametrize("diagram", sorted(OMITTED))
def test_every_printed_port_is_the_process_s_real_one(diagram):
    known = {**_vendored(), **_registry()}
    text = _text(diagram)
    wrong = []
    for name, port in _LABEL.findall(text):
        if name not in known:
            wrong.append(f"{name} :{port} - no process is called {name}")
        elif known[name] != int(port):
            wrong.append(f"{name} :{port} - its port is {known[name]}")
    assert not wrong, f"{diagram}:\n  " + "\n  ".join(wrong)


@pytest.mark.parametrize("diagram", sorted(OMITTED))
def test_every_pipeline_is_drawn_or_left_out_with_a_reason(diagram):
    text = _text(diagram)
    drawn = {name for name, _ in _LABEL.findall(text)} | set(
        re.findall(r"^\s*([a-z][a-z0-9_]*\d+)\s*:", text, re.M)
    )
    omitted = OMITTED[diagram]
    missing = sorted(p.procname for p in PIPELINES if p.procname not in drawn | set(omitted))
    assert not missing, (
        f"{diagram} draws neither these pipelines nor says why it leaves them out "
        f"(OMITTED in this file): {', '.join(missing)}"
    )
    stale = sorted(set(omitted) - {p.procname for p in PIPELINES})
    assert not stale, f"OMITTED[{diagram!r}] names processes that no longer exist: {stale}"
