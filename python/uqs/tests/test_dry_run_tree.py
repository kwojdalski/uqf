"""Every `--dry-run` changes nothing, and says so the same way (#1880).

`start --dry-run` used to bootstrap the data directory - 30 MB of process.csv,
database.q, access lists and HDB columns - because the start lines came from
`torq.sh print`, which ran after `bootstrap()`. `uqs --target prod start
--dry-run` then ran that on a server without asking, because --target treats
any dry run as harmless (cli/target_confirm.py). Each dry run had its own last
line too, or none.

So this walks every command that may change state and takes `--dry-run`, runs
it in a copy of the repository against an empty data root and HOME
(dry_run_support), and holds it to both promises: no file under any of the
three is created, removed or modified, and a dry run that succeeds ends with
`safety.dry_run_note()`. A refusal is fine - it changes nothing either - but
the arguments are chosen to get as far into each dry run as a fixture can.

A new command that changes state and takes --dry-run fails
`test_every_dry_run_is_walked` until it has a case here.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from dry_run_support import Sandbox, changes, copy_repo, snapshot
from rich.text import Text
from test_cli_conventions import LEAVES

from uqs.cli.safety import Effect, dry_run_note
from uqs.cli.target_confirm import changes_the_server, effect_of, trait_of

pytestmark = pytest.mark.xdist_group("dry_run_tree")

NOTE = Text.from_markup(dry_run_note()).plain
#: The note of a command whose dry run is its default: "... Run it again with --apply".
APPLY_NOTE = Text.from_markup(dry_run_note("with --apply")).plain.rstrip(".")

#: The commands whose dry run starts a process - the worker, to fetch and
#: transform every window (#1732) - so `uqs --target` asks before it:
#: those marked `dry_run_starts` (cli/safety.py, #1893). It no
#: longer bootstraps the data directory to do so (#1902): here, on an empty
#: data root, it refuses and changes nothing, like every other case.
STARTS_A_PROCESS = {path for path, cmd in LEAVES.items() if trait_of(cmd.command, "dry_run_starts")}

#: Whose dry run prints its report as the JSON document a program reads: deploy
#: keeps stdout for that, so the note is stderr's last line instead (#1902).
JSON_REPORT = {"deploy prune", "deploy rollback"}


def _uqs(*args: str) -> Callable[[Sandbox], None]:
    def run(sb: Sandbox) -> None:
        done = sb.uqs(*args)
        assert done.returncode == 0, (args, done.stdout, done.stderr)

    return run


def _hdb(runtime_dir: str) -> Callable[[Sandbox], None]:
    """A data directory holding the starter pack's sample HDB."""

    def run(sb: Sandbox) -> None:
        shutil.copytree(
            sb.repo / "lib" / "torq-finance-starter-pack" / "hdb", sb.data / runtime_dir / "hdb"
        )

    return run


def _status(sb: Sandbox) -> None:
    (sb.data / "uqs" / "status").mkdir(parents=True)


def _targets(sb: Sandbox) -> None:
    """Two targets: the file refuses a removal that would leave it empty."""
    _uqs("target", "add", "t0", "--ssh", "u@h", "--dest", "/opt/w")(sb)
    _uqs("target", "add", "t1", "--ssh", "u@h", "--dest", "/opt/x", "--label", "tier=uat")(sb)


@dataclass(frozen=True)
class Case:
    command: str
    args: tuple[str, ...]
    #: what the sandbox holds first; run before the "before" snapshot
    setup: Callable[[Sandbox], None] | None = None
    #: the setup writes into the repository, so it gets a copy of its own
    own_copy: bool = False
    #: root options, before the command
    root: tuple[str, ...] = ()

    @property
    def dry_by_default(self) -> bool:
        """Writes only with --apply (#1854): its dry run is the command alone."""
        return self.command in _dry_by_default_commands()

    @property
    def argv(self) -> list[str]:
        dry = [] if self.dry_by_default else ["--dry-run"]
        return [*self.root, *self.command.split(), *self.args, *dry]


NOWHERE = "nosuchhost.invalid"

CASES = [
    Case("start", ("rdb1",)),
    Case("start", ()),
    Case("restart", ("rdb1",)),
    Case("stop", ("rdb1",)),
    Case("clean", ()),
    Case("clean", (), _hdb("uqs")),
    Case("config set", ("rdb1", "localtime=0")),
    Case("config unset", ("rdb1", "localtime")),
    Case("config sources stub", ("demo_deals",)),
    Case("connector start", ("kafka",)),
    Case("connector stop", ("kafka",)),
    Case("connector restart", ("kafka",)),
    Case("data migrate", ("quote", "--date", "2015-01-07", "--rename", "bid:bid2"), _hdb("uqs")),
    # the --apply commands' defaults (#2235): each a dry run
    Case("data check", (), _hdb("uqs")),
    Case("data alter", ("quote", "--rename", "bid:bid2"), _hdb("uqs")),
    Case("data compress", (), _hdb("uqs")),
    Case("data replay", ()),
    Case("data seed", ("--from-runtime", "torq", "--date", "2015-01-07"), _hdb("uqs-torq")),
    Case("deploy build", ()),
    Case("deploy component build", ("torq",)),
    Case("deploy prune", ("--keep", "1", "--ssh", NOWHERE, "--dest", "/opt/x")),
    Case("deploy push", ("nosuch.tar.gz", "--ssh", NOWHERE, "--dest", "/opt/x")),
    Case("deploy rollback", ("--ssh", NOWHERE, "--dest", "/opt/x")),
    Case("job backfill", ("demo_deals_backfill", "--source-version", "v1", "--date", "2026-09-13")),
    Case("job attachment add", ("nosuch",)),
    Case("job attachment remove", ("nosuch",)),
    Case("job attachment contract approve", ("nosuch.json", "--attachment", "nosuch")),
    Case("job checkpoint remove", ("demo_deals_backfill",)),
    Case("job install", ("sidecars/kafka_example",)),
    Case("job new", ("probe_job",)),
    Case(
        "job receiver start",
        ("--attachment", "nosuch", "--listen-port", "7999", "--contract", "c1"),
    ),
    Case("job receiver stop", ("--attachment", "nosuch")),
    Case("job remove", ("probe_job",), _uqs("job", "new", "probe_job"), own_copy=True),
    Case("job runs upgrade", (), _status),
    Case("job runs stop", ("demo_deals_backfill", "--attachment", "nosuch")),
    Case("runtime prepare", ()),
    Case("runtime prepare", (), root=("--runtime", "crypto")),
    Case("setup odbc install", ("nosuch", "--odbc-home", "/nonexistent/odbc")),
    Case("setup odbc rollback", ("--odbc-home", "/nonexistent/odbc")),
    Case("target add", ("t1", "--ssh", "u@h", "--dest", "/opt/x")),
    Case("target adopt", ("t1", "--ssh", NOWHERE, "--dest", "/opt/x")),
    Case("target config restore", ("t1", "20261104T000000000Z-00000000")),
    Case("target edit", ()),
    Case("target remove", ("t1",), _targets, own_copy=True),
    Case("target rename", ("t1", "t2"), _targets, own_copy=True),
    Case("target revert", ("last",), _targets, own_copy=True),
    Case("target set", ("t1", "dest=/opt/y"), _targets, own_copy=True),
    Case("target unset", ("t1", "labels.tier"), _targets, own_copy=True),
]


def _id(case: Case) -> str:
    return " ".join([*case.root, case.command, *case.args])


@cache
def _dry_by_default_commands() -> frozenset[str]:
    """The commands that write only with --apply (#1854): changes-state, and
    confirms-on-server - `data replay` and `data seed` wrote by default until
    #2235, so each --apply command's default is walked here, whatever its mark."""
    return frozenset(
        path
        for path, c in LEAVES.items()
        if c.option("--apply") is not None and effect_of(c.command) is not Effect.READ_ONLY
    )


def _dry_run_commands() -> set[str]:
    return {
        path
        for path, c in LEAVES.items()
        if c.option("--dry-run") is not None and effect_of(c.command) is Effect.CHANGES_STATE
    } | _dry_by_default_commands()


def test_every_dry_run_is_walked():
    walked = {c.command for c in CASES}
    missing = _dry_run_commands() - walked
    stale = walked - _dry_run_commands()
    assert not missing, f"give each a Case: {sorted(missing)}"
    assert not stale, f"no longer a changes-state command with --dry-run: {sorted(stale)}"


@pytest.fixture(scope="session")
def shared_repo(tmp_path_factory) -> Path:
    return copy_repo(tmp_path_factory.mktemp("dry-run-repo") / "repo")


@cache
def _outcome(case: Case, shared: Path) -> tuple[int, str, str, list[str]]:
    """Run `case` once (both tests read it): its exit code, stdout, stderr
    and every change it made."""
    root = Path(tempfile.mkdtemp(prefix="dry-run-"))
    repo = copy_repo(root / "repo") if case.own_copy else shared
    (root / "data").mkdir()
    (root / "home").mkdir()
    sb = Sandbox(repo, root / "data", root / "home")
    if case.setup is not None:
        case.setup(sb)
    before = snapshot(*sb.roots)
    done = sb.uqs(*case.argv)
    found = changes(before, snapshot(*sb.roots))
    shutil.rmtree(root, ignore_errors=True)
    return done.returncode, done.stdout, done.stderr, found


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_a_dry_run_changes_nothing(case, shared_repo):
    code, out, err, found = _outcome(case, shared_repo)
    assert not found, (
        f"`uqs {' '.join(case.argv)}` (exit {code}) changed {len(found)} path(s):\n"
        + "\n".join(found[:20])
        + f"\n--- stdout\n{out[-2000:]}\n--- stderr\n{err[-2000:]}"
    )


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_a_dry_run_ends_with_the_note(case, shared_repo):
    code, out, err, _ = _outcome(case, shared_repo)
    if code != 0:
        pytest.skip(f"refused (exit {code}): the note is a successful dry run's last line")
    if case.command in JSON_REPORT:
        lines = [line.rstrip() for line in err.splitlines() if line.strip()]
        assert lines and lines[-1] == NOTE, f"stderr:\n{err}"
        return
    lines = [line.rstrip() for line in out.splitlines() if line.strip()]
    if case.dry_by_default:  # its note names --apply, how to act on it (#1854)
        assert lines and lines[-1].startswith(APPLY_NOTE), f"stdout:\n{out}\n--- stderr\n{err}"
        return
    assert lines and lines[-1] == NOTE, f"stdout:\n{out}\n--- stderr\n{err[-2000:]}"


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_target_sends_a_dry_run_unasked_only_once_it_is_walked_here(case):
    """`uqs --target` sends a dry run without asking (cli/target_confirm.py):
    sound only for a dry run this file shows writes nothing. A backfill's
    still starts a process, so it asks."""
    assert changes_the_server(case.argv) is (case.command in STARTS_A_PROCESS)
