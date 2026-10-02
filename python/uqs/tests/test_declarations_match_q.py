"""The process registry's text parser, held to what q itself loads (#533).

`model/declarations.py` reads the q job declarations as TEXT, never running q,
so that knowing which processes exist needs no interpreter. Its other tests
check it against synthetic strings and against another static read of the same
files; none compared it with what q resolves when it loads them. So a
declaration in a shape the parser reads differently - a key it does not model,
a symbol list spelled a new way - would put a wrong process, port or edge into
the registry and docs, and nothing would notice.

This loads the ETL tree in q, the way a process does, and diffs q's own
registries against the parser, field by field, for every job:

    streaming job, normalizer   procname, subscribe_to, publishes
    bounded worker              procname, dataset, source_version

Not `start_with_all` or `note`: those are deployment facts only the registry
reads, and q's declaration functions validate and then drop them, so there is
nothing in q to compare them with.

Needs q, so it skips without one - like test_scaffold_loads.py - and runs
wherever the q lanes do.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.model.declarations import declaration_calls, read_declarations, symbols
from uqs.model.pipeline import PipelineKind
from uqs.paths import WORKER_DIR

UQF_ROOT = Path(__file__).resolve().parents[3]

#: q's view of every job, as JSON: {"stream": {job: {...}}, "bounded": {job: {...}}}.
_DUMP = """
\\l src/init.q
\\l src/etl/init.q
s:{[j] d:.qetl.job.stream.def j;
    `procname`subscribe_to`publishes!(d`procname;(),d`subscribe_to;(),d`publishes)
  } each key .qetl.job.stream.jobs;
b:{[w] c:.qetl.job.bounded.worker_cfg w;
    `procname`dataset`source_version!(c`procname;c`dataset;c`source_version)
  } each key .qetl.job.bounded.worker_cfg;
-1 .j.j `stream`bounded!((key .qetl.job.stream.jobs)!s;(key .qetl.job.bounded.worker_cfg)!b);
exit 0
"""


def _parser_view(root: Path) -> dict[str, dict[str, dict]]:
    """The same shape as `_DUMP`, from the text parser."""
    stream: dict[str, dict] = {}
    bounded: dict[str, dict] = {}
    for d in read_declarations(root):
        if d.kind is PipelineKind.BACKFILL:
            bounded[d.name] = {"procname": d.procname, "source_version": d.source_version}
        else:
            stream[d.name] = {
                "procname": d.procname,
                "subscribe_to": sorted(d.subscribe_to),
                "publishes": sorted(d.publishes),
            }
    for path in sorted((root / WORKER_DIR).glob("*.q")):
        for fn, name, fields in declaration_calls(path.read_text()):
            if fn == "qetl.job.bounded.define" and name in bounded:
                bounded[name]["dataset"] = (symbols(fields.get("dataset", "")) or ("",))[0]
    return {"stream": stream, "bounded": bounded}


def _normalised(q_view: dict) -> dict[str, dict[str, dict]]:
    """q's JSON with list fields sorted, so order is not a difference."""
    stream = {
        job: {
            **fields,
            "subscribe_to": sorted(fields["subscribe_to"]),
            "publishes": sorted(fields["publishes"]),
        }
        for job, fields in q_view["stream"].items()
    }
    return {"stream": stream, "bounded": dict(q_view["bounded"])}


def differences(parser: dict, q: dict) -> list[str]:
    """Every job and field where the parser and q disagree, as readable lines."""
    out = []
    for family in ("stream", "bounded"):
        ours, theirs = parser[family], q[family]
        out += [
            f"{family} job {j}: q loads it, the parser does not see it"
            for j in sorted(set(theirs) - set(ours))
        ]
        out += [
            f"{family} job {j}: the parser sees it, q does not load it"
            for j in sorted(set(ours) - set(theirs))
        ]
        for job in sorted(set(ours) & set(theirs)):
            for key in sorted(set(ours[job]) | set(theirs[job])):
                if ours[job].get(key) != theirs[job].get(key):
                    mine, q_side = ours[job].get(key), theirs[job].get(key)
                    out.append(f"{family} job {job}.{key}: parser {mine!r}, q {q_side!r}")
    return out


def _q_view() -> dict:
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
    return _normalised(json.loads(lines[-1]))


def test_the_parser_reads_every_job_the_way_q_loads_it():
    q = _q_view()
    assert q["stream"] and q["bounded"], "q loaded no jobs - the comparison would be vacuous"
    assert differences(_parser_view(UQF_ROOT), q) == []


def test_a_disagreement_is_reported_not_passed_over():
    """The comparison itself can fail: a job only one side sees, and a field
    the two read differently, are both named."""
    parser = {
        "stream": {"a": {"procname": "a1", "subscribe_to": [], "publishes": ["t"]}},
        "bounded": {},
    }
    q = {
        "stream": {
            "a": {"procname": "a1", "subscribe_to": [], "publishes": ["u"]},
            "b": {"procname": "b1", "subscribe_to": [], "publishes": []},
        },
        "bounded": {},
    }
    assert differences(parser, q) == [
        "stream job b: q loads it, the parser does not see it",
        "stream job a.publishes: parser ['t'], q ['u']",
    ]
