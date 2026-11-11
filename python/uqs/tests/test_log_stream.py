"""Log lines and refusals go to stderr; stdout is the command's data (#1881).

The console log sink wrote to stdout unless the command line asked for --json,
so in text mode every log line - an `ERROR | ...` refusal first of all - landed
in `uqs list processes > f` and `$(uqs config show ...)`, and `2>/dev/null` hid
nothing. Only `deploy` sent its log to stderr, by reconfiguring logging in its
group callback - which also reset the level, dropping the global `--debug` for
exactly the riskiest commands.

Now the sink is stderr for every command, and the root callback alone decides
the level. The tree-wide case runs every command in a subprocess, given a data
root that is not a directory - so each refuses as soon as it builds its paths,
before it can touch anything - and holds every refusal to an empty stdout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

import pytest
import typer.main
from loguru import logger
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import shared


def _env(root: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("LOG_REGEX", "LOG_LEVEL", "UQS_TARGET", "UQS_RUNTIME")
        and not k.startswith("UQF_PEACHQ")
    }
    (root / "home").mkdir(exist_ok=True)
    return env | {
        # not a directory: every command that builds the stack's paths refuses there
        "UQS_DATA_ROOT": str(root / "no-such-data-root"),
        "UQS_ATTACH_DIR": str(root / "attachments"),
        "HOME": str(root / "home"),
        "NO_COLOR": "1",
        "EDITOR": "true",
        "VISUAL": "true",
    }


def _uqs(root: Path, *argv: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "uqs", *argv],
        capture_output=True,
        text=True,
        env=_env(root),
        cwd=root,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )


# ------------------------------------------------------------ the reported cases


def test_a_refusal_in_text_mode_goes_to_stderr(tmp_path):
    run = _uqs(tmp_path, "target", "remove", "nosuch-1881", "--dry-run")
    assert run.returncode == 1
    assert run.stdout == ""
    assert "ERROR" in run.stderr and "nosuch-1881" in run.stderr


def _sink_levels() -> set[int]:
    core = getattr(logger, "_core")  # noqa: B009 - loguru keeps its sinks private
    return {h.levelno for h in core.handlers.values()}


@pytest.mark.parametrize("group", [["target", "list"], ["deploy", "history"]])
def test_the_global_debug_reaches_every_group(group, tmp_path, monkeypatch):
    """`uqs --debug deploy ...` logged at INFO: deploy's own callback
    reconfigured logging after the root had set DEBUG."""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli.app, ["--debug", *group])
    assert _sink_levels() == {10}


# --------------------------------------------------------------- every command


def _leaves() -> dict[str, object]:
    out: dict[str, object] = {}

    def walk(cmd, path: list[str]) -> None:
        if getattr(cmd, "hidden", False):
            return
        subs = getattr(cmd, "commands", None)
        if subs:
            for name, sub in subs.items():
                walk(sub, [*path, name])
        elif path:
            out[" ".join(path)] = cmd

    walk(typer.main.get_command(cli.app), [])
    return out


#: Commands not run here, each with why.
NOT_RUN = {
    "exec": "passes its words to torq.sh: it has no refusal of its own to hold",
    "repl": "an interactive q session",
    "status": "its --interactive form is a full-screen browser; run in its own cases",
}


def _argv(path: str, cmd) -> list[str]:
    """`path`, a placeholder for each required argument or option, and
    --dry-run where the command takes it - so nothing it reaches can act."""
    argv = path.split()
    for p in cmd.params:
        if not p.required:
            continue
        value = "1" if getattr(p.type, "name", "") in ("integer", "integer range") else "nosuch"
        if p.param_type_name == "argument":
            argv += [value] * (p.nargs if p.nargs > 0 else 1)
        else:
            argv += [p.opts[0], value]
    if any("--dry-run" in p.opts for p in cmd.params):
        argv.append("--dry-run")
    return argv


@cache
def _outcomes(root: Path) -> dict[str, subprocess.CompletedProcess[str] | None]:
    leaves = {p: c for p, c in _leaves().items() if p.split()[0] not in NOT_RUN}

    def run(item):
        path, cmd = item
        try:
            return path, _uqs(root, *_argv(path, cmd), timeout=60)
        except subprocess.TimeoutExpired:
            return path, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(run, sorted(leaves.items())))


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    return _outcomes(tmp_path_factory.mktemp("log-stream"))


def test_every_refusal_leaves_stdout_empty(outcomes):
    refused = {p: r for p, r in outcomes.items() if r is not None and r.returncode == 1}
    assert len(refused) >= len(outcomes) // 2, "most commands should reach a refusal here"
    loud = {p: r.stdout[-300:] for p, r in refused.items() if r.stdout}
    assert not loud, "\n\n".join(f"uqs {p}:\n{out}" for p, out in sorted(loud.items()))


def test_json_refusal_still_goes_to_stdout(monkeypatch, capsys):
    """The --json document is data, so it stays where data goes."""
    monkeypatch.setattr(sys, "argv", ["uqs", "list", "--json"])
    shared.json_refusal("nope")
    assert capsys.readouterr().out.strip().startswith("{")
