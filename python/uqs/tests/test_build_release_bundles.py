"""`uqs deploy build --bundle` (#800): sidecar bundles installed into a
staged copy of the tree and packaged from it.

The fake-tool tests check the plumbing; the last test runs the real uqs
installer and generator over a staged copy of this checkout, with only the
network steps (the dependency wheels) faked.
"""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from typer.main import get_group

from uqs.cli.deploy import deploy_app
from uqs.deploy import artifact, payload
from uqs.deploy import build as release_build
from uqs.stack import bundles

ROOT = Path(__file__).resolve().parents[3]


_CLI = get_group(deploy_app)


def _built(argv: list[str], **kwargs):
    """`uqs deploy build` on `argv`: parsed by the command's own options."""
    p = _CLI.commands["build"].make_context("build", list(argv)).params
    return release_build.build(
        p["output"],
        arch=p["arch"],
        python=p["python"],
        allow_dirty=p["allow_dirty"],
        bundles=p["bundle"] or (),
        **kwargs,
    )


REV = "0123456789abcdef0123456789abcdef01234567"
JOB = "src/etl/streaming/piggy_spread.q"


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def _python_tools(argv: list[str]) -> subprocess.CompletedProcess | None:
    """uv export/build and pip download, faked; None for anything else."""
    if "export" in argv:
        Path(argv[argv.index("-o") + 1]).write_text("rich==15.0.0 --hash=sha256:00\n")
        return _done()
    if "build" in argv:
        (Path(argv[argv.index("--out-dir") + 1]) / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"u")
        return _done()
    if "download" in argv:
        return _done()
    return None


def _tools(files: list[str], fail_install: bool = False):
    calls: list[tuple[list[str], dict]] = []

    def runner(argv, **kw):
        calls.append((list(argv), kw))
        if (faked := _python_tools(argv)) is not None:
            return faked
        if "ls-files" in argv:
            return _done("\0".join(files) + "\0")
        if "rev-parse" in argv:
            return _done(REV + "\n")
        if "status" in argv:
            return _done("")
        staged = Path(kw["cwd"])
        if "install" in argv:
            if fail_install:
                return _done(rc=1, stderr="bundle_build: table quote is already defined")
            (staged / JOB).parent.mkdir(parents=True, exist_ok=True)
            (staged / JOB).write_text("/ the job\n")
            (staged / bundles.LEDGER.as_posix()).write_text("{}\n")
            jobs = [{"kind": "streaming", "name": "piggy_spread", "procname": "piggy_spread1"}]
            return _done(json.dumps({"piggybank": {"version": "1.0.0", "jobs": jobs}}))
        if "needs" in argv:
            return _done(json.dumps({"piggy_spread1": ["piggy_spread1", "fxfeed1"]}))
        if argv[-1].endswith(release_build.GENERATOR):
            return _done()
        raise AssertionError(argv)

    return runner, calls


def _tree(root: Path) -> list[str]:
    files = ["pyproject.toml", "src/init.q", "python/uqs/pyproject.toml"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    (root / "python/uqs/pyproject.toml").write_text('requires-python = ">=3.14"\n')
    return files


def _bundle(tmp_path: Path) -> Path:
    b = tmp_path / "piggybank"
    b.mkdir()
    (b / "bundle.json").write_text('{"name": "piggybank", "version": "1.0.0"}')
    return b


def _build(tmp_path: Path, *extra: str, fail_install: bool = False):
    root = tmp_path / "tree"
    tools, calls = _tools(_tree(root), fail_install)
    art = _built(["--output", str(tmp_path / "dist"), *extra], root=root, runner=tools)
    return art, calls, root


def test_without_a_bundle_nothing_is_staged_or_recorded(tmp_path):
    art, calls, _ = _build(tmp_path)
    assert "bundles" not in art.manifest
    assert not [argv for argv, _ in calls if "run" in argv]


def test_a_bundle_is_installed_into_a_staged_tree_and_recorded(tmp_path):
    art, calls, root = _build(tmp_path, "--bundle", str(_bundle(tmp_path)))
    with tarfile.open(art.path) as tar:
        names = set(tar.getnames())
    assert {JOB, bundles.LEDGER.as_posix(), "src/init.q"} <= names
    assert JOB in art.manifest["files"]
    job = art.manifest["bundles"]["piggybank"]["jobs"][0]
    assert job["needs"] == ["piggy_spread1", "fxfeed1"]
    # the checkout itself is never written
    assert not (root / JOB).exists()
    # every uqs step imports uqs from the staged tree, not the checkout
    runs = [(argv, kw) for argv, kw in calls if argv[:2] == ["uv", "run"]]
    assert len(runs) == 3
    for argv, kw in runs:
        staged = Path(kw["cwd"])
        assert staged != root
        assert kw["env"]["PYTHONPATH"] == str(staged / "python" / "uqs" / "src")
        assert argv[argv.index("--project") + 1] == str(root)


def test_a_folder_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "loose").mkdir()
    with pytest.raises(artifact.ReleaseError, match="holds no bundle.json"):
        _build(tmp_path, "--bundle", str(tmp_path / "loose"))


def test_an_installer_refusal_fails_the_build_with_its_reason(tmp_path):
    with pytest.raises(artifact.ReleaseError, match="quote is already defined"):
        _build(tmp_path, "--bundle", str(_bundle(tmp_path)), fail_install=True)
    assert not (tmp_path / "dist").exists() or not list((tmp_path / "dist").iterdir())


STREAM = """\
/ piggy_spread.q - a sidecar bundle's streaming job, for the build test.
.qetl.job.stream.define[`piggy_spread;
    `procname`subscribe_to`publishes`note!(`piggy_spread1;enlist `quote;enlist `piggy_quote;
        "copies quotes into piggy_quote")];
\\d .qpipe.job.piggy_spread
upd:{[t;x] .qpipe.job.piggy_spread.publish[`piggy_quote;x]}
\\d .
"""


def test_the_real_installer_and_generator_over_a_staged_checkout(tmp_path):
    """No fakes but the network: the real uqs.stack.bundle_build and the
    real generator, in a staged copy of this checkout."""
    b = _bundle(tmp_path)
    (b / "piggy_spread.q").write_text(STREAM)
    (b / "tables.q").write_text("piggy_quote:([]time:`timestamp$();sym:`symbol$())\n")
    (b / "catalog.q").write_text('.qcat.describe[`piggy_quote]:"quotes, for the piggybank";\n')
    files = payload.tracked_files(ROOT)

    def runner(argv, **kw):
        if (faked := _python_tools(argv)) is not None:
            return faked
        kw.pop("check", None)
        return subprocess.run(argv, check=False, **kw)

    staged = tmp_path / "staged"
    out, record = release_build.stage_bundles(ROOT, files, [str(b)], staged, runner)
    assert JOB in out and bundles.LEDGER.as_posix() in out
    entry = record["piggybank"]
    assert entry["tables"] == ["piggy_quote"]
    (job,) = entry["jobs"]
    assert job["procname"] == "piggy_spread1" and "piggy_spread1" in job["needs"]
    assert "piggy_quote:([]" in (staged / "src/etl/plant_tables.q").read_text()
    assert "piggy_spread1" in (staged / "scripts/processes/process_ports.csv").read_text()
    assert not (ROOT / JOB).exists()
