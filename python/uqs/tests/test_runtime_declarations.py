"""Runtimes are declarations (#759): every site reads a field, none asks
which runtime it is, so a new runtime is one RUNTIMES entry."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli, runtimes
from uqs import paths as stack_paths
from uqs.cli import shared
from uqs.runtimes import Runtime
from uqs.stack import runtime_profiles
from uqs.stack.env import build_env

SRC = Path(stack_paths.__file__).resolve().parent
REPO = SRC.parents[3]

#: A runtime no site has heard of: if it works, nothing branches on names.
PROBE = Runtime(
    name="probe",
    description="a test runtime",
    data_dir="uqs-probe",
    pipelines=False,
    overlays=False,
    base_port=6250,
)


@pytest.fixture
def with_probe(monkeypatch):
    monkeypatch.setitem(runtimes.RUNTIMES, PROBE.name, PROBE)
    return PROBE


def test_the_default_is_the_first_declared_and_both_runtimes_are_declared():
    assert runtimes.DEFAULT_RUNTIME == "uqf"
    assert list(runtimes.RUNTIMES) == ["uqf", "torq", "crypto", "fx"]
    uqf, torq = runtimes.RUNTIMES["uqf"], runtimes.RUNTIMES["torq"]
    assert (uqf.pipelines, uqf.overlays, uqf.data_dir) == (True, True, "uqs")
    assert (torq.pipelines, torq.overlays, torq.data_dir) == (False, False, "uqs-torq")


def test_no_code_outside_the_declarations_tests_a_runtime_by_name():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "runtimes.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "pure_torq" in code or re.search(r"""runtime\s*[!=]=\s*["']""", code):
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not offenders, "read a Runtime field instead:\n" + "\n".join(offenders)


def test_a_new_runtime_gets_its_own_data_directory_with_no_other_edit(with_probe):
    paths = stack_paths.paths_for_root(Path("/repo"), "probe")
    assert paths.torqdata == Path("/repo/output/uqs-probe")
    assert paths.runtime_declaration is PROBE


def test_a_runtime_without_overlays_has_no_service_layer(with_probe, tmp_path):
    env = build_env(stack_paths.paths_for_root(tmp_path, "probe"))
    assert not {"KDBSERVCONFIG", "KDBSERVCODE"} & set(env)
    assert env["UQS_RUNTIME"] == "probe"


@pytest.mark.parametrize("command", sorted(runtimes.UQF_ONLY_COMMANDS))
def test_a_runtime_without_pipelines_refuses_their_commands_by_its_own_name(
    with_probe, monkeypatch, command
):
    monkeypatch.setenv("UQS_RUNTIME", "uqf")  # restored afterwards: --runtime sets it
    said: list[str] = []

    def die(exc):
        said.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(shared, "_die", die)
    result = CliRunner().invoke(cli.app, ["--runtime", "probe", command])
    assert result.exit_code == 1
    assert "the probe runtime has none of this tree's pipelines" in said[0]


def test_a_runtime_with_every_pipeline_can_start_every_profile():
    from uqs.model import profiles

    assert runtime_profiles.startable_profiles(None) == sorted(profiles.PROFILES)


def test_a_runtime_can_start_only_the_profiles_its_processes_cover():
    from uqs.model import profiles

    essential = set(profiles.resolve(["essential"]))
    assert runtime_profiles.startable_profiles(essential) == ["essential"]
    assert runtime_profiles.startable_profiles(set()) == []


def test_the_guide_s_runtime_table_matches_the_declarations():
    """docs/guides/uqs.md#runtimes compares the runtimes column by column:
    one column per declared runtime, in order, the default marked, and each
    one's data directory as declared."""
    text = (REPO / "docs" / "guides" / "uqs.md").read_text()
    section = text.split("### Runtimes", 1)[1].split("\n### ", 1)[0]
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.strip().startswith("|") and "---" not in line
    ]
    header, body = rows[0], {row[0]: row[1:] for row in rows[1:]}
    expected = [
        f"`{name}`" + (" (default)" if name == runtimes.DEFAULT_RUNTIME else "")
        for name in runtimes.RUNTIMES
    ]
    assert header[1:] == expected
    assert body["data directory"] == [f"`output/{r.data_dir}`" for r in runtimes.RUNTIMES.values()]
    assert body["base port"] == [f"`{r.base_port}`" for r in runtimes.RUNTIMES.values()]
