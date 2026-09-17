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
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

UQF_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = UQF_ROOT / "scripts" / "contract_surface.py"
BASELINE = UQF_ROOT / "docs" / "migrations" / "surfaces" / "uqf-local"

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
    if not BASELINE.is_dir():
        pytest.skip(f"{BASELINE} not present")
    return contract_surface.read_surface(BASELINE)


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


def test_the_committed_baseline_matches_this_tree() -> None:
    """The gate itself, run in the test lane as well as CI.

    A committed baseline that nothing verifies goes stale the first time
    anyone changes a signature, and then every diff against it is measuring
    against a tree that no longer exists. This repository has shipped that
    exact shape before - `.qcov.require_schema` was defined, tested, and
    called from no live path.

    Here rather than only in CI because the q suite and the Python suite run
    on a developer's machine, where the staleness is introduced.
    """
    if not (Path.home() / ".kx" / "bin" / "q").is_file():
        pytest.skip("no KDB-X interpreter")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check"],
        cwd=UQF_ROOT,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "the committed contract surface is stale:\n" + result.stderr.decode(errors="replace")
    )


def test_the_csv_round_trip_is_exact(baseline: dict) -> None:
    """Write the surface out and read it back unchanged.

    `check` compares the committed surface against a freshly built one, so
    any asymmetry in the serialisation reads as a contract change that never
    happened - and the fix would look like "re-export", which would make it
    disappear until the next time.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "surface"
        contract_surface.write_surface(out, baseline)
        assert contract_surface.read_surface(out) == baseline


def test_a_niladic_function_survives_the_round_trip(baseline: dict) -> None:
    """`params` of `[""]` must not come back as `[]`.

    q spells a niladic function's parameter list as one empty string, and a
    non-function's as an empty list. Space-joining both yields an empty cell,
    so the CSV distinguishes them by whether `rank` is blank. Getting this
    wrong would silently rewrite 72 entries.
    """
    niladic = [
        entry
        for entries in baseline["functions"].values()
        for entry in entries
        if entry["params"] == [""]
    ]
    assert niladic, "no niladic functions in the surface - this test is checking nothing"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "surface"
        contract_surface.write_surface(out, baseline)
        back = contract_surface.read_surface(out)
    restored = [
        entry
        for entries in back["functions"].values()
        for entry in entries
        if entry["params"] == [""]
    ]
    assert len(restored) == len(niladic)


def test_no_list_value_needs_csv_quoting(baseline: dict) -> None:
    """Lists share a cell, space-separated, which is only safe while no value
    in one contains a space.

    Asserted rather than assumed: the day a parameter or table name contains
    one, the surface would mis-round-trip silently rather than fail.

    Scoped to the LIST-valued fields on purpose. An earlier version of this
    test claimed to cover "no value" and checked only these, which is how the
    space-typed column below got past it.
    """
    offenders = []
    for ns, entries in baseline["functions"].items():
        for entry in entries:
            for param in entry["params"]:
                if " " in param or "," in param or '"' in param:
                    offenders.append(f".{ns}.{entry['name']}({param})")
    for process in baseline["processes"]:
        for table in process["subscribes"] + process["publishes"]:
            if " " in table or "," in table:
                offenders.append(f"{process['procname']} -> {table}")
    assert not offenders, f"values that break the space-separated encoding: {offenders}"


def test_a_general_column_type_survives_the_round_trip(baseline: dict) -> None:
    """q reports a GENERAL column's type as a literal space.

    `crypto_book.bid_prices` is one. Written unquoted it ends a CSV line in
    whitespace that is invisible in review and stripped by this repository's
    own trailing-whitespace hook - which would turn the type into an empty
    string and leave `check` failing against a file nothing could regenerate.
    That is exactly what happened on the first export.
    """
    general = [
        (table, column)
        for table, spec in baseline["tables"].items()
        for column, qtype in zip(spec["columns"], spec["types"], strict=True)
        if qtype == " "
    ]
    assert general, "no general-typed columns - this test is checking nothing"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "surface"
        contract_surface.write_surface(out, baseline)
        back = contract_surface.read_surface(out)
    for table, column in general:
        spec = back["tables"][table]
        assert spec["types"][spec["columns"].index(column)] == " ", (
            f"{table}.{column} lost its general type in the round trip"
        )


def test_no_surface_line_ends_in_whitespace() -> None:
    """The committed files must survive the repo's own pre-commit hooks.

    A hook that rewrites a generated file leaves `check` comparing against
    something `export` cannot reproduce, and the failure appears nowhere near
    its cause.
    """
    if not BASELINE.is_dir():
        pytest.skip(f"{BASELINE} not present")
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(BASELINE.glob("*.csv"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if line != line.rstrip()
    ]
    assert not offenders, f"lines ending in whitespace: {offenders}"


def test_rank_and_params_agree_about_emptiness(baseline: dict) -> None:
    """The invariant the encoding leans on: `rank is None` exactly when
    `params` is empty.

    If those ever diverge, the blank-`rank` cell stops being enough to tell a
    value from a niladic function, and the round trip breaks for whichever
    entry broke the rule.
    """
    broken = [
        f".{ns}.{entry['name']}"
        for ns, entries in baseline["functions"].items()
        for entry in entries
        if (entry["rank"] is None) != (entry["params"] == [])
    ]
    assert not broken, f"rank/params disagree for: {broken}"
