"""Tests for `scripts/generate/derive.py`, the one command for derived artifacts (#1804).

What it promises, each held below: a target runs only when named, and naming
it imports no other target's generator; `--check` compares and writes
nothing; a write is all-or-refused and atomic; the exit status is 0, 1 or 2
for current, stale and misuse. The per-target cases prove each target reaches
its generator and that the committed output is what it derives.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "generate" / "derive.py"

# scripts/ is not a package, so the script is loaded by path - the idiom
# test_render_diagrams.py uses, and for the same reason.
_SPEC = importlib.util.spec_from_file_location("derive", SCRIPT)
assert _SPEC and _SPEC.loader
derive = importlib.util.module_from_spec(_SPEC)
sys.modules["derive"] = derive
_SPEC.loader.exec_module(derive)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


# ------------------------------------------------------------ the registry


def test_the_targets_are_registered_by_hand_and_there_is_no_all():
    assert "all" not in derive.TARGETS
    for name, target in derive.TARGETS.items():
        assert target.name == name
        assert target.command().endswith(f"scripts/generate/derive.py {name}")


def test_help_lists_every_target():
    result = _run("--help")
    assert result.returncode == 0
    for name in derive.TARGETS:
        assert name in result.stdout


@pytest.mark.parametrize("argv", [[], ["no-such-target"], ["all"]])
def test_no_or_an_unknown_target_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as exited:
        derive.main(argv)
    assert exited.value.code == 2


def test_a_target_runs_alone(monkeypatch):
    """Dispatch reaches the named target and no other."""
    ran = []
    for name, target in derive.TARGETS.items():
        monkeypatch.setitem(
            derive.TARGETS,
            name,
            dataclasses.replace(target, run=lambda args, name=name: ran.append(name) or 0),
        )
    assert derive.main(["table-properties", "--check"]) == 0
    assert ran == ["table-properties"]


def test_an_option_of_another_target_is_refused(capsys):
    with pytest.raises(SystemExit) as exited:
        derive.main(["table-properties", "--require"])
    assert exited.value.code == 2


# ----------------------------------------------------------- the lifecycle


def _outputs(tmp_path: Path) -> list:
    return [
        derive.text(tmp_path / "a.txt", "one\n"),
        derive.text(tmp_path / "sub" / "b.txt", "two\n"),
    ]


def _state(tmp_path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(tmp_path.rglob("*"))
        if p.is_file()
    }


def test_write_then_check_is_current(tmp_path, capsys):
    outputs = _outputs(tmp_path)
    assert derive.settle(outputs, check=False, hint="h") == 0
    assert (tmp_path / "sub" / "b.txt").read_text() == "two\n"
    assert derive.settle(outputs, check=True, hint="h") == 0
    assert "is current" in capsys.readouterr().out


def test_check_reports_a_stale_output_with_a_diff_and_writes_nothing(tmp_path, capsys):
    outputs = _outputs(tmp_path)
    derive.settle(outputs, check=False, hint="h")
    (tmp_path / "a.txt").write_text("edited by hand\n")
    before = _state(tmp_path)
    assert derive.settle(outputs, check=True, hint="run the generator") == 1
    err = capsys.readouterr().err
    assert "a.txt is stale - run the generator" in err
    assert "-edited by hand" in err and "+one" in err
    assert _state(tmp_path) == before


def test_check_reports_a_missing_output_and_creates_nothing(tmp_path, capsys):
    assert derive.settle(_outputs(tmp_path), check=True, hint="h") == 1
    assert "is missing" in capsys.readouterr().err
    assert _state(tmp_path) == {}


def test_a_rendered_output_names_its_source_instead_of_a_diff(tmp_path, capsys):
    target = tmp_path / "d.svg"
    target.write_bytes(b"<svg old/>")
    source = tmp_path / "d.d2"
    assert derive.compare([derive.Output(target, b"<svg new/>", source=source)], "h") == 1
    err = capsys.readouterr().err
    assert "d.d2 has changed since it was rendered" in err
    assert "<svg" not in err


def test_write_leaves_a_current_file_untouched(tmp_path, capsys):
    outputs = _outputs(tmp_path)
    derive.settle(outputs, check=False, hint="h")
    before = _state(tmp_path)
    assert derive.settle(outputs, check=False, hint="h") == 0
    assert _state(tmp_path) == before
    assert "is unchanged" in capsys.readouterr().out


def test_write_keeps_the_mode_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("old\n")
    path.chmod(0o640)
    derive.write([derive.text(path, "new\n")])
    assert path.read_text() == "new\n"
    assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_failed_write_raises_rather_than_reporting_success(tmp_path):
    """A target whose second file cannot be written must not exit 0."""
    (tmp_path / "blocked").mkdir()
    outputs = [
        derive.text(tmp_path / "a.txt", "one\n"),
        derive.text(tmp_path / "blocked", "a directory is in the way\n"),
    ]
    with pytest.raises(OSError):
        derive.settle(outputs, check=False, hint="h")
    assert [p.name for p in sorted(tmp_path.iterdir())] == ["a.txt", "blocked"]


def test_an_empty_output_set_is_refused(capsys):
    assert derive.settle([], check=True, hint="h") == 1
    assert "no outputs" in capsys.readouterr().err


# --------------------------------------------------------------- isolation


def _imports_after(*argv: str) -> set[str]:
    """The modules a fresh interpreter holds after running `derive.py ARGV`.

    Starting a process is refused in the probe, so a target that needs q or
    d2 stops at its first subprocess - after importing what it imports, which
    is what is being measured, and without needing the interpreter.
    """
    probe = (
        "import contextlib, io, runpy, subprocess, sys\n"
        "def refuse(*args, **kwargs):\n"
        "    raise RuntimeError('no processes in this probe')\n"
        "subprocess.run = subprocess.Popen = refuse\n"
        f"sys.argv = ['derive.py', *{list(argv)!r}]\n"
        "try:\n"
        "    with contextlib.redirect_stdout(io.StringIO()):\n"
        f"        runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
        "except (SystemExit, RuntimeError):\n"
        "    pass\n"
        "print('\\n'.join(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return set(result.stdout.split())


#: Each target's generator: the domain module it asks for its outputs.
GENERATORS = {
    "table-properties": "uqs.stack.gateway_access",
    "diagrams": "render_diagrams",
    "operational-docs": "generate_operational_docs",
    "contract-surface": "contract_surface",
    "q-facts": "q_facts",
    "plant-schema": "uqs.model.plant_metadata_gen",
}


def test_every_target_names_its_generator_here():
    assert set(GENERATORS) == set(derive.TARGETS)


@pytest.mark.parametrize("name", sorted(GENERATORS))
def test_running_a_target_loads_its_generator_and_no_other(name):
    loaded = _imports_after(name, "--check")
    assert GENERATORS[name] in loaded
    assert not loaded & (set(GENERATORS.values()) - {GENERATORS[name]})


def test_diagrams_needs_no_workspace():
    """It runs under CI's and the hook's bare python3: nothing from uqs."""
    assert not {m for m in _imports_after("diagrams", "--check") if m.split(".")[0] == "uqs"}


def test_help_imports_no_generator():
    loaded = _imports_after("--help")
    assert not loaded & set(GENERATORS.values())
    assert "uqs" not in loaded


# --------------------------------------------------------- table-properties


def test_table_properties_is_current_and_its_check_writes_nothing():
    path = REPO / "scripts" / "torqconfig" / "dataaccess" / "tableproperties.csv"
    before = path.stat().st_mtime_ns
    result = _run("table-properties", "--check")
    assert result.returncode == 0, result.stderr
    assert path.stat().st_mtime_ns == before


def test_table_properties_check_fails_when_the_rows_change(monkeypatch, capsys):
    from uqs.stack import gateway_access

    rows = gateway_access.table_properties_rows()
    monkeypatch.setattr(gateway_access, "table_properties_rows", lambda: [*rows, ",extra,,,,,,,"])
    path = REPO / "scripts" / "torqconfig" / "dataaccess" / "tableproperties.csv"
    committed = path.read_bytes()
    assert derive.main(["table-properties", "--check"]) == 1
    err = capsys.readouterr().err
    assert "tableproperties.csv is stale" in err and "+,extra" in err
    assert "derive.py table-properties" in err
    assert path.read_bytes() == committed


def test_table_properties_does_not_run_q(monkeypatch):
    """It reads the committed q_facts.py; it must never start an interpreter."""

    def no_q(*args, **kwargs):
        raise AssertionError(f"started a process: {args}")

    monkeypatch.setattr(subprocess, "run", no_q)
    monkeypatch.setattr(subprocess, "Popen", no_q)
    assert derive.main(["table-properties", "--check"]) == 0


def test_writing_is_atomic_per_file(tmp_path, monkeypatch):
    """os.replace is the only thing that touches the destination."""
    path = tmp_path / "a.txt"
    path.write_text("old\n")
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: seen.append(Path(b).name) or real(a, b))
    derive.write([derive.text(path, "new\n")])
    assert seen == ["a.txt"]


# --------------------------------------------------------- contract-surface


def test_contract_surface_refuses_out_with_check(capsys):
    assert derive.main(["contract-surface", "--check", "--out", "/nowhere"]) == 2
    assert "--check writes nothing" in capsys.readouterr().err


def test_contract_surface_check_reports_the_contract_difference(monkeypatch, capsys):
    """A semantic diff, not a byte diff: a gained parameter is named as one."""
    contract = derive.load("contract_surface")
    committed = contract.read_surface(contract.BASELINE)
    current = {**committed, "functions": {k: list(v) for k, v in committed["functions"].items()}}
    ns = sorted(current["functions"])[0]
    entry = dict(current["functions"][ns][0])
    entry["name"] = "a_function_no_tree_has"
    current["functions"][ns] = [*current["functions"][ns], entry]
    monkeypatch.setattr(contract, "build_surface", lambda: current)
    before = {p.name: p.read_bytes() for p in contract.BASELINE.iterdir()}
    assert derive.main(["contract-surface", "--check"]) == 1
    err = capsys.readouterr().err
    assert "## Only in current (1)" in err and "a_function_no_tree_has" in err
    assert "derive.py contract-surface" in err
    assert {p.name: p.read_bytes() for p in contract.BASELINE.iterdir()} == before


def test_contract_surface_check_names_a_missing_file(tmp_path, monkeypatch, capsys):
    contract = derive.load("contract_surface")
    monkeypatch.setattr(contract, "BASELINE", tmp_path)
    assert derive.main(["contract-surface", "--check"]) == 1
    assert "is missing functions.csv" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ q-facts


def test_q_facts_check_reports_a_changed_fact_and_writes_nothing(monkeypatch, capsys):
    """Without q: the export is replaced by the committed values, one changed."""
    q_facts = derive.load("q_facts")
    from uqs.generated import q_facts as committed

    facts = {name: getattr(committed, name, None) for name in q_facts.FACTS}
    facts = {k: (list(v) if isinstance(v, tuple) else v) for k, v in facts.items()}
    facts["LOG_LEVELS"] = [*committed.LOG_LEVELS, "CHATTY"]
    monkeypatch.setattr(q_facts, "export", lambda: facts)
    path = next(iter(q_facts.MODULES))
    before = path.read_bytes()
    assert derive.main(["q-facts", "--check"]) == 1
    err = capsys.readouterr().err
    assert "generated/q_facts.py is stale" in err and '"CHATTY"' in err
    assert path.read_bytes() == before


def test_q_facts_without_q_refuses(monkeypatch):
    q_facts = derive.load("q_facts")
    import uqs.interpreter

    monkeypatch.setattr(uqs.interpreter, "q_interpreter", lambda env=None: None)
    with pytest.raises(SystemExit, match="no q interpreter"):
        q_facts.export()


# ------------------------------------------------------------- plant-schema


def test_plant_schema_refuses_force_with_check(capsys):
    assert derive.main(["plant-schema", "--check", "--force"]) == 2
    assert "--check writes nothing" in capsys.readouterr().err


def test_plant_schema_write_needs_no_q_when_current(monkeypatch, capsys):
    """The fingerprint says current, so q is never asked and nothing is written."""
    from uqs.model import plant_metadata_gen

    monkeypatch.setattr(plant_metadata_gen, "export", lambda *a, **k: pytest.fail("ran q"))
    assert derive.main(["plant-schema"]) == 0
    assert "plant_schema.json is unchanged" in capsys.readouterr().out


def test_plant_schema_check_reports_a_difference_and_writes_nothing(monkeypatch, capsys):
    from uqs.model import plant_metadata_gen
    from uqs.model.plant_metadata import PLANT_SCHEMA_FILE

    path = REPO / PLANT_SCHEMA_FILE
    committed = path.read_text()
    monkeypatch.setattr(
        plant_metadata_gen, "generate", lambda root: committed.replace('"tables"', '"tablez"')
    )
    assert derive.main(["plant-schema", "--check"]) == 1
    err = capsys.readouterr().err
    assert "plant_schema.json is stale" in err and "derive.py plant-schema" in err
    assert path.read_text() == committed


def test_plant_schema_reports_a_refusal_without_a_traceback(monkeypatch, capsys):
    from uqs.model import plant_metadata_gen
    from uqs.model.plant_metadata import SchemaMetadataError

    def no_q(root):
        raise SchemaMetadataError("no q interpreter - set $QCMD, or put q on PATH")

    monkeypatch.setattr(plant_metadata_gen, "generate", no_q)
    assert derive.main(["plant-schema", "--check"]) == 1
    assert "plant-schema: no q interpreter" in capsys.readouterr().err
