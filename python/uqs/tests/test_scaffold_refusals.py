"""`uqs job new` refuses the same things whichever way it is run (#2824, #2827,
#2836).

The refusals that read the tree - a job name or procname already taken, a file
that already exists - lived only in `write.apply_plan`: after `--dry-run`
returned, and on the tree paths a `--bundle` plan no longer has. Every case
here is refused before anything is written, so each runs against the real tree
with `--dry-run` or into a bundle under tmp_path, and asserts nothing changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli.job import create, create_bundle, create_reaction, regenerate

UQF_ROOT = Path(__file__).resolve().parents[3]


def _job_new(argv: list[str], monkeypatch) -> tuple[str, list[str]]:
    """Run `uqs job new`; return its output and any refusal it raised."""
    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    for module in (create, create_bundle, create_reaction, regenerate):
        monkeypatch.setattr(module, "_die", record)
    result = CliRunner().invoke(cli.app, ["job", "new", *argv])
    assert refused or result.exception is None, result.exception
    return result.stdout, refused


_ETL = ["--subscribe-to", "trades", "--schema", "sym:symbol, px:float"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["crypto_markout", "--outputs", "zz_cm2", *_ETL], "'crypto_markout' already exists"),
        (["zz_proc", "--outputs", "zz_proc", *_ETL, "--proc", "cross1"], "cross already runs"),
        (["zz_pf", "--outputs", "zz_pf", *_ETL, "--profile", "all"], "all"),
    ],
)
def test_a_dry_run_refuses_what_the_real_run_refuses(monkeypatch, argv, message):
    """#2827: the dry run printed the plan and "nothing was changed. Run it
    again without --dry-run" for a scaffold the real run then refused."""
    out, refused = _job_new([*argv, "--dry-run"], monkeypatch)
    assert refused and message in refused[0], (refused, out)
    assert "Run it again" not in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        # #2824: a procname a tree job runs
        (["zb1", "--outputs", "zb1_out", *_ETL, "--proc", "cross1"], "cross already runs"),
        # #2824: a bounded worker's name, for a streaming job
        (["demo_deals_backfill", "--outputs", "zb2_out", *_ETL], "'demo_deals_backfill' already"),
        # #2836: a streaming job the tree already has
        (["fx_feed", "--outputs", "zz_x1", *_ETL], "'fx_feed' already exists"),
    ],
)
@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
def test_a_bundle_job_is_refused_what_a_tree_job_is(tmp_path, monkeypatch, argv, message, dry_run):
    """#2824, #2836: the refusals selected new job files by their src/etl/
    path, which a plan redirected into a bundle no longer has - so the bundle
    was written, and could never be installed."""
    folder = tmp_path / "zbundle"
    _out, refused = _job_new([*argv, "--bundle", str(folder), *dry_run], monkeypatch)
    assert refused and message in refused[0], refused
    assert not folder.exists()


def test_a_second_bundle_job_is_refused_the_first_ones_procname(tmp_path, monkeypatch):
    """#2824: the bundle's own jobs take names and procnames too."""
    folder = tmp_path / "zbundle"
    first = ["zb4", "--outputs", "zb4_out", *_ETL, "--bundle", str(folder)]
    assert _job_new(first, monkeypatch)[1] == []
    second = ["zb5", "--outputs", "zb5_out", *_ETL, "--proc", "zb41", "--bundle", str(folder)]
    _out, refused = _job_new(second, monkeypatch)
    assert refused and "zb41, which zb4 already runs" in refused[0], refused
    assert not (folder / "zb5.q").exists()
