"""Tests for `uqs install-jobs` (stack/install.py, cli/install.py).

Every test builds a fake repository root and a sidecar under tmp_path, so
nothing here writes into this tree; the CLI tests patch `_paths` to point at
the fake root and the derived-file regeneration to a no-op, because that runs
the real generators over the whole tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import install as cli_install
from uqs.paths import SOURCE_DIR, STREAM_DIR, TABLES_FILE, WORKER_DIR
from uqs.stack import install
from uqs.stack.install import Kind, Mode, Status

SOURCE = "source_name:`acme\n.qsrc.define[source_name;`fields!enlist `time];\n"
WORKER = ".qbw.define[`acme_backfill;`source`dataset`width!(`acme;`acme_tape;1D)];\n"
STREAM = (
    ".qstream.define[`acme_spread;"
    "`procname`subscribes`publishes!(`acme_spread1;enlist `quote;enlist `acme_spread)];\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for directory in (SOURCE_DIR, WORKER_DIR, STREAM_DIR):
        (root / directory).mkdir(parents=True)
    (root / STREAM_DIR / "quotes.q").write_text(
        ".qstream.define[`quotes;`procname`publishes!(`quotes1;enlist `quote)];\n"
    )
    (root / TABLES_FILE).parent.mkdir(parents=True)
    (root / TABLES_FILE).write_text("quote:([]time:`timestamp$();sym:`symbol$())\n")
    return root


@pytest.fixture
def sidecar(tmp_path: Path) -> Path:
    side = tmp_path / "sidecars"
    (side / "feeds").mkdir(parents=True)
    (side / "feeds" / "acme.q").write_text(SOURCE)
    (side / "acme_backfill.q").write_text(WORKER)
    (side / "acme_spread.q").write_text(STREAM)
    (side / "test_acme.q").write_text("/ a test\n")
    (side / "util.q").write_text("/ helpers, no declaration\n")
    return side


def _by_name(items: list[install.Item]) -> dict[str, install.Item]:
    return {i.source.name: i for i in items}


def test_classify_reads_the_declaration_not_the_filename() -> None:
    assert install.classify(SOURCE) == ({Kind.SOURCE}, ())
    assert install.classify(WORKER) == ({Kind.WORKER}, ("acme_backfill",))
    assert install.classify(STREAM) == ({Kind.STREAMING}, ("acme_spread",))
    # A commented-out call declares nothing.
    assert install.classify("/ .qsrc.define[x;y]\n") == (set(), ())


def test_plan_places_each_file_by_kind(sidecar: Path, repo: Path) -> None:
    items = _by_name(install.plan(sidecar, repo))
    assert items["acme.q"].destination == repo / SOURCE_DIR / "acme.q"
    assert items["acme_backfill.q"].destination == repo / WORKER_DIR / "acme_backfill.q"
    assert items["acme_spread.q"].destination == repo / STREAM_DIR / "acme_spread.q"
    assert {items[n].status for n in ("acme.q", "acme_backfill.q", "acme_spread.q")} == {Status.NEW}
    assert items["test_acme.q"].status is Status.SKIPPED
    assert "q test" in items["test_acme.q"].reason
    assert items["util.q"].status is Status.SKIPPED
    assert "declares no" in items["util.q"].reason


def test_plan_refuses_a_file_of_two_kinds(sidecar: Path, repo: Path) -> None:
    (sidecar / "both.q").write_text(SOURCE + WORKER.replace("acme_backfill", "both_backfill"))
    item = _by_name(install.plan(sidecar, repo))["both.q"]
    assert item.status is Status.SKIPPED
    assert "one kind per file" in item.reason


def test_plan_refuses_a_name_the_tree_already_declares(sidecar: Path, repo: Path) -> None:
    (sidecar / "quotes_again.q").write_text(STREAM.replace("acme_spread", "quotes"))
    item = _by_name(install.plan(sidecar, repo))["quotes_again.q"]
    assert item.status is Status.SKIPPED
    assert "already declared by" in item.reason


def test_plan_refuses_two_sidecar_files_with_one_name(sidecar: Path, repo: Path) -> None:
    (sidecar / "more").mkdir()
    (sidecar / "more" / "acme_spread.q").write_text(STREAM.replace("acme_spread;", "other;"))
    items = [i for i in install.plan(sidecar, repo) if i.source.name == "acme_spread.q"]
    assert [i.status for i in items] == [Status.NEW, Status.SKIPPED]
    assert "same filename" in items[1].reason


def test_copy_then_same_then_conflict(sidecar: Path, repo: Path) -> None:
    item = _by_name(install.plan(sidecar, repo))["acme_spread.q"]
    install.install(item, Mode.COPY)
    dest = repo / STREAM_DIR / "acme_spread.q"
    assert dest.read_text() == STREAM and not dest.is_symlink()
    assert _by_name(install.plan(sidecar, repo))["acme_spread.q"].status is Status.SAME

    (sidecar / "acme_spread.q").write_text(STREAM + "/ changed\n")
    conflict = _by_name(install.plan(sidecar, repo))["acme_spread.q"]
    assert conflict.status is Status.CONFLICT
    with pytest.raises(FileExistsError):
        install.install(conflict, Mode.COPY)
    install.install(conflict, Mode.COPY, overwrite=True)
    assert dest.read_text().endswith("/ changed\n")


def test_symlink_points_at_the_sidecar(sidecar: Path, repo: Path) -> None:
    item = _by_name(install.plan(sidecar, repo))["acme.q"]
    install.install(item, Mode.SYMLINK)
    dest = repo / SOURCE_DIR / "acme.q"
    assert dest.is_symlink() and dest.resolve() == (sidecar / "feeds" / "acme.q").resolve()
    assert _by_name(install.plan(sidecar, repo))["acme.q"].status is Status.SAME


def test_skipped_is_not_installable(sidecar: Path, repo: Path) -> None:
    with pytest.raises(ValueError, match="not installable"):
        install.install(_by_name(install.plan(sidecar, repo))["util.q"], Mode.COPY)


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def cli_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli_install, "_paths", lambda: stack_paths.paths_for_root(repo))
    monkeypatch.setattr(
        cli_install,
        "_regenerate_derived",
        lambda _root: [subprocess.CompletedProcess([], 0, "", "")] * 2,
    )
    return repo


def _run(*args: str, stdin: str = "") -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, ["install-jobs", *args], input=stdin)
    return result.exit_code, result.output


def test_cli_dry_run_writes_nothing(sidecar: Path, cli_repo: Path) -> None:
    code, out = _run(str(sidecar), "--dry-run")
    assert code == 0, out
    assert "nothing installed" in out
    assert not (cli_repo / STREAM_DIR / "acme_spread.q").exists()


def test_cli_yes_needs_a_mode(sidecar: Path, cli_repo: Path) -> None:
    code, _out = _run(str(sidecar), "--yes")
    assert code == 1
    assert not (cli_repo / STREAM_DIR / "acme_spread.q").exists()


def test_cli_wizard_asks_the_mode(sidecar: Path, cli_repo: Path) -> None:
    code, out = _run(str(sidecar), stdin="symlink\ny\n")
    assert code == 0, out
    assert (cli_repo / WORKER_DIR / "acme_backfill.q").is_symlink()
    assert "Next steps" in out
    assert "uqs start acme_spread1" in out
    assert "uqs backfill acme_backfill" in out
    # Neither table is defined in the fake plant, and both are reported.
    assert "acme_spread publishes acme_spread" in out
    assert "acme_backfill fills acme_tape" in out


def test_cli_declining_installs_nothing(sidecar: Path, cli_repo: Path) -> None:
    code, _out = _run(str(sidecar), stdin="copy\nn\n")
    assert code == 1
    assert not (cli_repo / SOURCE_DIR / "acme.q").exists()


def test_cli_keeps_conflicts_unless_told(sidecar: Path, cli_repo: Path) -> None:
    dest = cli_repo / STREAM_DIR / "acme_spread.q"
    dest.write_text("/ the tree's own version\n")
    code, out = _run(str(sidecar), "--mode", "copy", "--yes")
    assert code == 0, out
    assert dest.read_text() == "/ the tree's own version\n"
    assert (cli_repo / SOURCE_DIR / "acme.q").is_file()

    code, out = _run(str(sidecar), "--mode", "copy", "--yes", "--overwrite")
    assert code == 0, out
    assert dest.read_text() == STREAM


def test_cli_asks_before_replacing(sidecar: Path, cli_repo: Path) -> None:
    dest = cli_repo / STREAM_DIR / "acme_spread.q"
    dest.write_text("/ the tree's own version\n")
    code, out = _run(str(sidecar), "--mode", "copy", stdin="y\ny\n")
    assert code == 0, out
    assert dest.read_text() == STREAM


def test_cli_second_run_has_nothing_to_do(sidecar: Path, cli_repo: Path) -> None:
    assert _run(str(sidecar), "--mode", "copy", "--yes")[0] == 0
    code, out = _run(str(sidecar), "--mode", "copy", "--yes")
    assert code == 0
    assert "Nothing to install" in out
