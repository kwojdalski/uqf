"""The variant-gather rule, `scripts/gates/policy/variant_gather.py`: the reference-variant
gather reads the deduped rids once, and no variant code reaches the market (#2289)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "variant_gather", REPO / "scripts" / "gates" / "policy" / "variant_gather.py"
)
assert _SPEC and _SPEC.loader
vg = importlib.util.module_from_spec(_SPEC)
sys.modules["variant_gather"] = vg
_SPEC.loader.exec_module(vg)


def _tree(tmp_path: Path) -> Path:
    """A copy of just the files the rule reads, to damage one at a time."""
    for rel in (*vg.NO_MARKET, vg.DEPTH_READER):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dst)
    for d in vg.DECLARATIONS:
        shutil.copytree(REPO / d, tmp_path / d)
    return tmp_path


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    f = root / rel
    text = f.read_text()
    assert old in text, (rel, old)
    f.write_text(text.replace(old, new, 1))


def test_the_real_tree_is_clean():
    assert vg.problems(REPO) == []


def test_the_command_runs_and_passes():
    r = subprocess.run(
        [sys.executable, "scripts/gates/check.py", "variant-gather"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "reads the deduped rids once" in r.stdout


def test_a_variant_that_selects_the_market_is_refused(tmp_path):
    root = _tree(tmp_path)
    (root / "src/etl/variants/greedy.q").write_text(
        "x:select bid_prices from market_data where date=d;\n"
    )
    out = vg.problems(root)
    assert len(out) == 1 and "greedy.q" in out[0] and "market_data" in out[0], out


def test_a_comment_naming_the_market_is_not_code(tmp_path):
    root = _tree(tmp_path)
    (root / "src/etl/variants/quiet.q").write_text(
        "/ never reads market_data\nx:1; / nor market_data\n"
    )
    assert vg.problems(root) == []


def test_a_second_ladder_reader_outside_the_gather_is_refused(tmp_path):
    root = _tree(tmp_path)
    (root / "src/etl/core/sneaky.q").write_text("x:.qetl.mapped.depth_at arg;\n")
    out = vg.problems(root)
    assert len(out) == 1 and "sneaky.q" in out[0] and "depth_at" in out[0], out


def test_a_gather_that_does_not_dedupe_is_refused(tmp_path):
    root = _tree(tmp_path)
    _edit(root, vg.DEPTH_READER, "books:distinct raze parts;", "books:raze parts;")
    out = vg.problems(root)
    assert any("DISTINCT books" in m for m in out), out


def test_a_gather_that_reads_twice_is_refused(tmp_path):
    root = _tree(tmp_path)
    _edit(
        root,
        vg.DEPTH_READER,
        "    reads:.qetl.mapped.depth_reads-before;",
        "    .qetl.mapped.depth_at`books`cols!(books;arg`cols);\n"
        "    reads:.qetl.mapped.depth_reads-before;",
    )
    out = vg.problems(root)
    assert any("exactly once" in m for m in out), out


def test_a_gather_that_stops_counting_what_it_reads_is_refused(tmp_path):
    root = _tree(tmp_path)
    _edit(root, vg.DEPTH_READER, "if[reads<>count books;", "if[0b;")
    out = vg.problems(root)
    assert any("depth_reads" in m for m in out), out
