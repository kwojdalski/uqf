"""Runtime-declared sidecar bundles (#852): stack/runtime_bundles.py, the
membership it feeds (model/runtime_members.py), `uqs runtime prepare` and the
composition `uqs deploy build` records.

Installs go into a fake repository under tmp_path. Membership is read against
this tree's real registry, with the bundle ledger faked, so "which runtime has
which process" is asserted on real processes and their real dependencies.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import runtime_prepare
from uqs.cli.regenerate import _DERIVED
from uqs.deploy import build as release_build
from uqs.model import runtime_members
from uqs.paths import CATALOG_FILE, SOURCE_DIR, STREAM_DIR, TABLES_FILE, WORKER_DIR, UqsError
from uqs.runtimes import RUNTIMES
from uqs.stack import bundles, procs, runtime_bundles, runtime_profiles
from uqs.stack.bundle_blocks import BundleError
from uqs.stack.bundles import LEDGER, OVERRIDES_FILE

ROOT = Path(__file__).resolve().parents[3]


def stream(name: str, table: str) -> str:
    return (
        f".qetl.job.stream.define[`{name};"
        f"`procname`subscribe_to`publishes!(`{name}1;enlist `quote;enlist `{table})];\n"
    )


def make_bundle(where: Path, name: str, job: str | None = None, table: str | None = None) -> Path:
    job, table = job or f"{name}_job", table or f"{name}_tape"
    where.mkdir(parents=True)
    (where / "bundle.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
    (where / f"{job}.q").write_text(stream(job, table))
    (where / "tables.q").write_text(f"{table}:([]time:`timestamp$();sym:`symbol$())\n")
    (where / "catalog.q").write_text(f'.qcat.describe[`{table}]:"{name}";\n')
    return where


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    for directory in (SOURCE_DIR, WORKER_DIR, STREAM_DIR):
        (root / directory).mkdir(parents=True)
    (root / TABLES_FILE).write_text("\\d .qetl.plant\nquote:([]time:`timestamp$())\n")
    (root / CATALOG_FILE).parent.mkdir(parents=True)
    (root / CATALOG_FILE).write_text('\\d .qcat\ndescribe[`quote]:"q";\n\\d .\n')
    (root / OVERRIDES_FILE).parent.mkdir(parents=True)
    monkeypatch.delenv(runtime_bundles.DECLARATION_ENV, raising=False)
    return root


def declare(root: Path, mapping: dict[str, list[str]]) -> None:
    (root / runtime_bundles.DECLARATION).write_text(json.dumps(mapping))


# ------------------------------------------------------------ declaration


def test_no_declaration_is_no_bundles(repo: Path) -> None:
    assert runtime_bundles.resolve("uqf", repo) == []


def test_a_runtime_declares_bundles_relative_to_the_file(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "pb", "pb")
    declare(repo, {"crypto": ["../pb"]})
    (member,) = runtime_bundles.resolve("crypto", repo)
    assert (member.bundle.name, member.source) == ("pb", "runtime")
    assert runtime_bundles.resolve("fx", repo) == []  # unrelated runtime: none


@pytest.mark.parametrize(
    ("mapping", "why"),
    [
        ({"nope": []}, "not a runtime"),
        ({"torq": ["../pb"]}, "none of this tree's pipelines"),
        ({"uqf": "../pb"}, "as strings"),
    ],
)
def test_a_bad_declaration_is_refused(repo: Path, mapping: dict, why: str) -> None:
    declare(repo, mapping)
    with pytest.raises(BundleError, match=why):
        runtime_bundles.resolve("uqf", repo)


def test_the_declaration_can_live_outside_the_tree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_bundle(tmp_path / "site" / "pb", "pb")
    elsewhere = tmp_path / "site" / "bundles.json"
    elsewhere.write_text('{"uqf": ["pb"]}')
    monkeypatch.setenv(runtime_bundles.DECLARATION_ENV, str(elsewhere))
    assert [m.bundle.name for m in runtime_bundles.resolve("uqf", repo)] == ["pb"]


def test_explicit_bundles_add_to_the_declared_ones(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "pb", "pb")
    make_bundle(tmp_path / "mw", "mw")
    declare(repo, {"uqf": ["../pb"]})
    got = runtime_bundles.resolve("uqf", repo, [tmp_path / "mw", tmp_path / "pb"])
    assert [(m.bundle.name, m.source) for m in got] == [("pb", "runtime"), ("mw", "explicit")]


def test_two_folders_claiming_one_name_are_refused(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "a", "pb")
    make_bundle(tmp_path / "b", "pb")
    declare(repo, {"uqf": ["../a"]})
    with pytest.raises(BundleError, match="named by both"):
        runtime_bundles.resolve("uqf", repo, [tmp_path / "b"])


# ---------------------------------------------------------------- prepare


def test_prepare_installs_and_records_the_runtime(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "pb", "pb")
    declare(repo, {"crypto": ["../pb"]})
    runtime_bundles.prepare("crypto", runtime_bundles.resolve("crypto", repo), repo)
    assert (repo / STREAM_DIR / "pb_job.q").is_file()
    assert bundles.read_ledger(repo)["pb"]["runtimes"] == ["crypto"]
    # a second runtime declaring it joins, rather than replacing
    declare(repo, {"crypto": ["../pb"], "fx": ["../pb"]})
    runtime_bundles.prepare("fx", runtime_bundles.resolve("fx", repo), repo)
    assert bundles.read_ledger(repo)["pb"]["runtimes"] == ["crypto", "fx"]
    # and a rerun changes nothing
    before = (repo / TABLES_FILE).read_text()
    runtime_bundles.prepare("fx", runtime_bundles.resolve("fx", repo), repo)
    assert (repo / TABLES_FILE).read_text() == before


def test_an_undeclared_bundle_leaves_the_runtime_and_stays_installed(
    repo: Path, tmp_path: Path
) -> None:
    make_bundle(tmp_path / "pb", "pb")
    declare(repo, {"crypto": ["../pb"]})
    runtime_bundles.prepare("crypto", runtime_bundles.resolve("crypto", repo), repo)
    declare(repo, {})
    runtime_bundles.prepare("crypto", runtime_bundles.resolve("crypto", repo), repo)
    assert bundles.read_ledger(repo)["pb"]["runtimes"] == []
    assert (repo / STREAM_DIR / "pb_job.q").is_file()


def test_a_conflict_between_bundles_is_refused_before_any_write(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "a", "a", job="shared_job")
    make_bundle(tmp_path / "b", "b", job="shared_job")
    declare(repo, {"uqf": ["../a", "../b"]})
    with pytest.raises(BundleError, match="both claim"):
        runtime_bundles.prepare("uqf", runtime_bundles.resolve("uqf", repo), repo)
    assert not (repo / LEDGER).exists()
    assert not list((repo / STREAM_DIR).iterdir())


def test_an_invalid_bundle_is_refused_before_any_write(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "ok", "ok")
    bad = make_bundle(tmp_path / "bad", "bad")
    (bad / "tables.q").write_text("/\n")  # a block comment, refused
    declare(repo, {"uqf": ["../ok", "../bad"]})
    with pytest.raises(BundleError, match="block comment"):
        runtime_bundles.prepare("uqf", runtime_bundles.resolve("uqf", repo), repo)
    assert not (repo / STREAM_DIR / "ok_job.q").exists()


# ------------------------------------------------------------- the CLI


def _cli(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str, runtime: str = "uqf"):
    monkeypatch.setattr(
        runtime_prepare, "_paths", lambda: stack_paths.paths_for_root(repo, runtime)
    )
    monkeypatch.setattr(
        runtime_prepare,
        "_regenerate_derived",
        lambda _root: [subprocess.CompletedProcess([], 0, "", "")] * len(_DERIVED),
    )
    return CliRunner().invoke(cli.app, ["runtime", "prepare", *args])


def test_dry_run_reports_the_composition_and_writes_nothing(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_bundle(tmp_path / "pb", "pb")
    declare(repo, {"crypto": ["../pb"]})
    result = _cli(repo, monkeypatch, "--dry-run", runtime="crypto")
    assert result.exit_code == 0, result.output
    assert (
        "pb" in result.output and "runtime" in result.output and "nothing written" in result.output
    )
    assert not (repo / LEDGER).exists()


def test_prepare_says_nothing_was_started(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_bundle(tmp_path / "pb", "pb")
    result = _cli(repo, monkeypatch, "--bundle", str(tmp_path / "pb"))
    assert result.exit_code == 0, result.output
    assert "Nothing was started" in result.output
    assert bundles.read_ledger(repo)["pb"]["runtimes"] == ["uqf"]


# ------------------------------------------------------------ membership


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch):
    """Fake installed bundles over REAL processes: `pb` owns arbitrage1
    (which needs superbook1 and marketdata1), `other` owns kafka_flow1."""
    entries: dict[str, dict] = {}

    def install(name: str, procname: str, table: str, runtimes: list[str] | None) -> None:
        entry = {"jobs": [{"kind": "streaming", "name": procname[:-1], "procname": procname}],
                 "tables": [table]}  # fmt: skip
        if runtimes is not None:
            entry["runtimes"] = runtimes
        entries[name] = entry

    monkeypatch.setattr(runtime_members, "_ledger", lambda: entries)
    return install


def test_a_bundle_and_its_dependencies_join_a_profile_runtime(ledger) -> None:
    ledger("pb", "arbitrage1", "arbitrage", ["fx"])
    ledger("other", "kafka_flow1", "client_flow", ["uqf"])
    fx = runtime_members.pipeline_procnames(RUNTIMES["fx"])
    assert fx is not None
    assert {"arbitrage1", "superbook1", "marketdata1", "fxfeed1"} <= fx
    assert "kafka_flow1" not in fx  # an unrelated bundle
    tables = runtime_members.plant_tables(RUNTIMES["fx"])
    assert tables is not None and {"arbitrage", "superbook"} <= tables
    assert "client_flow" not in tables
    # crypto does not declare pb: it does not have it
    assert "arbitrage1" not in (runtime_members.pipeline_procnames(RUNTIMES["crypto"]) or set())


def test_an_undeclared_bundle_leaves_the_full_runtime(ledger) -> None:
    ledger("pb", "arbitrage1", "arbitrage", ["fx"])
    uqf = runtime_members.pipeline_procnames(RUNTIMES["uqf"])
    assert uqf is not None and "arbitrage1" not in uqf and "kafka_flow1" in uqf
    tables = runtime_members.plant_tables(RUNTIMES["uqf"])
    assert tables is not None and "arbitrage" not in tables and "superbook" in tables


def test_a_bundle_from_before_is_where_it_was(ledger) -> None:
    ledger("pb", "arbitrage1", "arbitrage", None)
    assert runtime_members.pipeline_procnames(RUNTIMES["uqf"]) is None  # every pipeline
    assert "arbitrage1" not in (runtime_members.pipeline_procnames(RUNTIMES["fx"]) or set())


def test_no_bundles_leaves_every_runtime_unchanged(ledger) -> None:
    assert runtime_members.pipeline_procnames(RUNTIMES["uqf"]) is None
    assert runtime_members.plant_tables(RUNTIMES["uqf"]) is None


def test_a_profile_cannot_start_a_job_its_runtime_lacks(ledger) -> None:
    ledger("pb", "arbitrage1", "arbitrage", ["fx"])
    paths = stack_paths.paths_for_root(ROOT, "uqf")
    assert "arbitrage1" not in procs.list_process_names(paths)
    with pytest.raises(UqsError, match="arbitrage1"):
        runtime_profiles.refuse_what_the_runtime_lacks(paths, ("arbitrage1",), "the start")


# ---------------------------------------------------- one composition


def test_prepare_and_build_resolve_the_same_composition(repo: Path, tmp_path: Path) -> None:
    make_bundle(tmp_path / "pb", "pb")
    make_bundle(tmp_path / "mw", "mw")
    declare(repo, {"crypto": ["../pb"]})
    local = runtime_bundles.resolve("crypto", repo, [tmp_path / "mw"])
    built = release_build.composition_members("crypto", repo, [str(tmp_path / "mw")])
    assert runtime_bundles.composition("crypto", local) == runtime_bundles.composition(
        "crypto", built
    )


def test_the_build_installs_for_its_runtime_and_records_it(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_bundle(tmp_path / "pb", "pb")
    declare(repo, {"crypto": ["../pb"]})
    seen: list[list[str]] = []

    def fake_stage(root, files, folders, staged, runner, runtime):
        seen.append([runtime, *folders])
        return files, {"pb": {"version": "1.0.0", "jobs": []}}

    monkeypatch.setattr(release_build, "stage_bundles", fake_stage)
    monkeypatch.setattr(release_build, "revision", lambda root, runner: ("0" * 40, False))
    monkeypatch.setattr(release_build, "tracked_files", lambda root, runner: [])
    monkeypatch.setattr(release_build, "default_python", lambda root: "3.14")

    def fake_payload(root, into, target, runner):
        (into / "wheels").mkdir(parents=True)
        (into / "requirements.txt").write_text("")
        (into / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"u")

    monkeypatch.setattr(release_build, "python_payload", fake_payload)
    art = release_build.build(tmp_path / "dist", root=repo, runtime="crypto")
    assert seen == [["crypto", str((tmp_path / "pb").resolve())]]
    assert art.manifest["runtime"] == "crypto"
    assert art.manifest["bundles"]["pb"]["source"] == "runtime"


def test_a_default_runtime_build_records_its_runtime_too(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case #883 found: the default runtime with no bundles used to leave
    `runtime` out, so its meaning was whatever the deploying uqs assumed."""
    from uqs.runtimes import DEFAULT_RUNTIME

    monkeypatch.setattr(release_build, "revision", lambda root, runner: ("0" * 40, False))
    monkeypatch.setattr(release_build, "tracked_files", lambda root, runner: [])
    monkeypatch.setattr(release_build, "default_python", lambda root: "3.14")

    def fake_payload(root, into, target, runner):
        (into / "wheels").mkdir(parents=True)
        (into / "requirements.txt").write_text("")
        (into / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"u")

    monkeypatch.setattr(release_build, "python_payload", fake_payload)
    art = release_build.build(tmp_path / "dist", root=repo)
    assert art.manifest["runtime"] == DEFAULT_RUNTIME
