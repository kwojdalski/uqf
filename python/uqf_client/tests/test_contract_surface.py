"""Guards on the contract-surface exporter and differ.

`scripts/contract_surface.py` produces the half of WP2 (#139) that does not
need the authority checkout: a machine-readable statement of what this tree's
public contract IS, so that locking contracts against the authority becomes a
diff rather than a person reading 29 q namespaces side by side.

Two properties make it useful, and both are asserted here rather than assumed:

* The export is **deterministic**. A surface that differs between two runs of
  the same tree makes every diff against it meaningless - the noise would
  drown the signal on the first comparison.
* The differ **finds** differences, and ranks a breaking one above a cosmetic
  one. A differ nobody has watched report anything might report nothing.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = UQF_ROOT / "scripts" / "contract_surface.py"
BASELINE = UQF_ROOT / "docs" / "migrations" / "surfaces" / "uqf-local.json"

# Loaded by path rather than imported, because `scripts/` is not a package on
# any search root - the same approach test_generated_docs.py takes to its own
# generator. A `sys.path` insert works at runtime but leaves `ty` unable to
# resolve the import statically, which is a gate failure rather than a
# cosmetic one.
_spec = importlib.util.spec_from_file_location("contract_surface", SCRIPT)
assert _spec and _spec.loader
contract_surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract_surface)


def _diff(a: dict, b: dict) -> list[str]:
    return contract_surface.diff_surfaces(a, b, "a", "b")


@pytest.fixture(scope="module")
def baseline() -> dict:
    if not BASELINE.is_file():
        pytest.skip(f"{BASELINE} not present")
    return json.loads(BASELINE.read_text())


def test_the_committed_baseline_is_well_formed(baseline: dict) -> None:
    """It has the four sections a comparison needs.

    A surface missing one section would diff clean on it, which reads as
    agreement rather than as an absent question.
    """
    for section in ("functions", "tables", "processes", "variables"):
        assert section in baseline, f"baseline has no {section!r} section"
    assert baseline["functions"], "no namespaces exported"
    assert baseline["processes"], "no processes exported"


def test_the_baseline_carries_the_etl_coverage_schema(baseline: dict) -> None:
    """The table whose shape is #139's sharp conflict.

    `etl_coverage` is created lazily by `.qcov.attach`, so the first version of
    the exporter reported ZERO tables - the most misleading possible answer,
    since a surface claiming no tables reads as "this tree defines none"
    rather than "none had been created yet". This is the regression guard for
    that.
    """
    assert "etl_coverage" in baseline["tables"], (
        "etl_coverage is absent - the exporter is not materialising lazily "
        "created tables, so the coverage model is missing from the surface"
    )
    cols = baseline["tables"]["etl_coverage"]["columns"]
    assert "dataset" in cols and "source_version" in cols


def test_a_surface_diffed_against_itself_is_clean(baseline: dict) -> None:
    assert _diff(baseline, baseline) == []


def test_a_rank_change_is_reported_first(baseline: dict) -> None:
    """Ordering is the point, not decoration.

    A function whose rank changed WILL break its callers; a renamed parameter
    compiles either way. Burying the first under fifty of the second is how a
    reviewer misses it.
    """
    other = copy.deepcopy(baseline)
    ns, entry = next(
        (ns, e)
        for ns, entries in other["functions"].items()
        for e in entries
        if e["kind"] == "function" and e["rank"]
    )
    entry["rank"] = entry["rank"] + 1
    # and a cosmetic change that must sort below it
    for entries in other["functions"].values():
        for e in entries:
            if e["kind"] == "function" and e["params"] and e is not entry:
                e["params"] = ["renamed", *e["params"][1:]]
                break
        break

    lines = _diff(baseline, other)
    assert lines, "a changed rank produced no output"
    assert "Rank changed" in lines[0], f"rank change was not reported first: {lines[0]}"


def test_a_table_schema_change_is_reported(baseline: dict) -> None:
    """The coverage-partition conflict, simulated.

    This is the difference #139 exists to resolve, so the differ must not be
    able to miss it.
    """
    other = copy.deepcopy(baseline)
    other["tables"]["etl_coverage"]["columns"].insert(2, "partition")
    other["tables"]["etl_coverage"]["types"].insert(2, "d")
    text = "\n".join(_diff(baseline, other))
    assert "etl_coverage" in text
    assert "partition" in text


def test_a_missing_function_is_reported(baseline: dict) -> None:
    other = copy.deepcopy(baseline)
    ns = next(iter(other["functions"]))
    removed = other["functions"][ns].pop()
    text = "\n".join(_diff(baseline, other))
    assert removed["name"] in text


def test_a_process_offset_change_is_reported(baseline: dict) -> None:
    """Ports are a contract: two processes on one port is a startup failure."""
    other = copy.deepcopy(baseline)
    other["processes"][0]["offset"] = 9999
    text = "\n".join(_diff(baseline, other))
    assert other["processes"][0]["procname"] in text


def test_the_export_is_deterministic() -> None:
    """Two runs of the same tree produce byte-identical output.

    Without this the first real comparison would be unreadable. It is also why
    the exporter records no timestamp: a timestamp makes every export differ
    from every other and destroys the diff the file exists to enable.
    """
    if not (Path.home() / ".kx" / "bin" / "q").is_file():
        pytest.skip("no KDB-X interpreter")
    runs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "export"],
            cwd=UQF_ROOT,
            capture_output=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        runs.append(result.stdout)
    assert runs[0] == runs[1], "the exporter is not deterministic"
