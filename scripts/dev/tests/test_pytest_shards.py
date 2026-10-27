"""scripts/dev/pytest_shards.py: splitting the suite across CI runners (#1696).

What matters is that no test is lost or run twice: every collected module is
in exactly one shard, whatever the recorded durations say.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "pytest_shards", Path(__file__).resolve().parents[1] / "pytest_shards.py"
)
assert _SPEC and _SPEC.loader
shards = importlib.util.module_from_spec(_SPEC)
sys.modules["pytest_shards"] = shards
_SPEC.loader.exec_module(shards)


@pytest.mark.parametrize("count", [1, 2, 3, 7])
def test_every_module_lands_in_exactly_one_shard(count):
    modules = [f"m{i}.py" for i in range(20)]
    weights = {f"m{i}.py": float(i % 5 + 1) for i in range(12)}  # some unknown
    split = shards.assign(modules, weights, count)
    assert len(split) == count
    flat = [m for shard in split for m in shard]
    assert sorted(flat) == sorted(modules), "each module once, none lost"


def test_heavy_modules_are_spread_and_the_split_is_balanced():
    weights = {"a.py": 40.0, "b.py": 30.0, "c.py": 20.0, "d.py": 10.0, "e.py": 10.0}
    split = shards.assign(list(weights), weights, 2)
    loads = sorted(sum(weights[m] for m in shard) for shard in split)
    assert loads == [50.0, 60.0]
    assert {"a.py", "b.py"} not in [set(s) for s in split], "the two heaviest apart"


def test_the_split_is_deterministic_and_order_free():
    modules = ["x.py", "y.py", "z.py", "w.py"]
    weights = {"x.py": 1.0, "y.py": 1.0}
    one = shards.assign(modules, weights, 2)
    assert shards.assign(list(reversed(modules)), weights, 2) == one


def test_an_unknown_module_weighs_the_median():
    weights = {"a.py": 1.0, "b.py": 3.0, "c.py": 100.0}
    split = shards.assign(["a.py", "b.py", "c.py", "new.py"], weights, 2)
    assert ["c.py"] in split, "new.py (3s, the median) joins the light shard"


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b", "1/0"])
def test_a_malformed_shard_is_refused(spec):
    with pytest.raises(ValueError, match="--shard"):
        shards.parse_shard(spec)


def test_the_shards_of_this_suite_collect_it_exactly_once():
    """Through pytest itself, over the real testpaths: the union of three
    shards' node ids is the whole suite's, with no id in two shards."""

    def collected(*extra: str) -> list[str]:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", *extra],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        return [line for line in out.splitlines() if "::" in line]

    whole = collected()
    parts = [collected("--shard", f"{i}/3") for i in (1, 2, 3)]
    joined = [nodeid for part in parts for nodeid in part]
    assert len(joined) == len(set(joined)), "a test in two shards"
    assert sorted(joined) == sorted(whole)


def test_the_recorder_sums_seconds_per_module(tmp_path):
    out = tmp_path / "d.json"
    rec = shards.Recorder(out)

    class Report:
        def __init__(self, nodeid, duration):
            self.nodeid, self.duration = nodeid, duration

    class Config:
        pass

    class Session:
        config = Config()

    for nodeid, d in [("a.py::t1", 1.5), ("a.py::t2", 0.5), ("b.py::t", 2.0)]:
        rec.pytest_runtest_logreport(Report(nodeid, d))
    rec.pytest_sessionfinish(Session())
    assert json.loads(out.read_text()) == {"a.py": 2.0, "b.py": 2.0}
