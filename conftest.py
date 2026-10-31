"""Every test in this repository runs outside the git repository git is
committing to (#996).

git runs pre-commit hooks with GIT_DIR and GIT_INDEX_FILE exported, and the
`python-tests` hook runs this suite. Several tests build a scratch repository
with `git init <tmp>` - and with GIT_DIR set, `git init <dir>` re-initialises
$GIT_DIR instead, writing `core.bare = true` into it. From a worktree that is
the shared config of the main checkout, which then refuses every command
("this operation must be run in a work tree").

Here, at the repository root, so it covers every testpath - the gates' and
the portable converter's tests as well as python/'s - and runs before any
test module is imported. Dropping the variables changes nothing a test means:
git then finds the repository it is pointed at (`git -C <path>`) or the one
it is run in, as it does outside a hook.

The same goes for the variables that say where status and data live (see
STATE_LOCATION_VARS): a test must never write where another test, another
pytest-xdist worker or a running stack reads.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import pytest

#: The variables with which git tells a child process which repository,
#: index and work tree to use.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
)

for _name in GIT_LOCATION_VARS:
    os.environ.pop(_name, None)


#: Where q code and uqs put status files and generated state:
#: `.qetl.status.status_dir` reads $UQF_STATUS_DIR, else $TORQDATA/status, and
#: uqs.paths puts the stack's data under $UQS_DATA_ROOT. Inherited from a
#: developer's shell, they would send every test that starts q or reads the
#: ledger without setting its own to that developer's real directory - shared
#: by every pytest-xdist worker at once, and by a running stack - and they
#: override the per-test roots tests do set (test_deploy_soak.py). Dropped, so
#: each test supplies its own under tmp_path, which xdist makes per worker.
STATE_LOCATION_VARS = ("UQF_STATUS_DIR", "TORQDATA", "UQS_DATA_ROOT")

for _name in STATE_LOCATION_VARS:
    os.environ.pop(_name, None)


#: The markers the q-running tests are selected by (#1808). `-m kdbx` and
#: `-m peachq` pick an interpreter; `--require-q IMPL` makes a missing one a
#: failure. q_backend is how the python-tests hook leaves out what the q hooks
#: run (scripts/dev/python_tests_hook.py), so a commit runs each test once.
MARKERS = (
    "kdbx: runs on KDB-X, the q $QCMD names (the `kdbx` fixture); skips when $QCMD is "
    "not KDB-X, fails instead with --require-q kdbx",
    "peachq: runs on PeachQ (the `peachq` fixture); skips when no PeachQ can be had, "
    "fails instead with --require-q peachq",
    "q_backend: runs a q script as its test - a worked example, a doc's q blocks; a q "
    "pre-commit hook runs it at commit, so the python-tests hook leaves it out",
    "q_suite: runs the whole qUnit suite (minutes): the q-tests hook and CI's PeachQ q job "
    "run it, and CI's sharded Python job leaves it to that job",
)

#: Markers a test runs under only when asked for: such a test skips, saying
#: how to select it, unless the `-m` expression names its marker. Each with
#: why it is not part of an ordinary run.
OPT_IN = {
    "release": "a release check, minutes long: the q suite again in reverse order, and "
    "each sidecar bundle installed into a copy with the whole suite run there",
    "live_stack": "starts isolated TorQ stacks on KDB-X (the attach workflow), about a minute each",
    "docker": "starts Docker containers standing in for other machines (tests/remote)",
    "external": "checks live external sources, named with --smoke-targets",
}

#: The interpreters --require-q takes.
REQUIRABLE_Q = ("kdbx", "peachq")


# --- sharding across CI runners (#1696, scripts/dev/pytest_shards.py) ------------


def _shards():
    import importlib.util

    path = Path(__file__).with_name("scripts") / "dev" / "pytest_shards.py"
    spec = importlib.util.spec_from_file_location("pytest_shards", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser):
    parser.addoption(
        "--peachq",
        default=None,
        metavar="PATH",
        help=(
            "the PeachQ binary for the tests that run on PeachQ (the `peachq` fixture); "
            "default: $QCMD if it is PeachQ, else the pinned build (scripts/peachq.py)"
        ),
    )
    parser.addoption(
        "--require-q",
        action="append",
        choices=REQUIRABLE_Q,
        default=[],
        metavar="IMPL",
        help=(
            "fail, rather than skip, a test that needs this interpreter (kdbx: the q $QCMD "
            "names, answering as KDB-X; peachq: the `peachq` fixture's) when it cannot be "
            "had - what a gate that selects q tests passes. Repeatable"
        ),
    )
    parser.addoption(
        "--q-files",
        default=None,
        metavar="A.q,B.q",
        help=(
            "run only the qUnit suites these tests/q files define (tests/run_tests.q -files): "
            "the q-tests hook passes scripts/dev/test_impact.py's selection"
        ),
    )
    smoke = parser.getgroup("smoke", "the external smoke check (-m external)")
    smoke.addoption("--smoke-targets", nargs="+", default=[], metavar="HOST:PORT",
                    help="the sources to check")  # fmt: skip
    smoke.addoption("--smoke-tables", nargs="+", default=[], metavar="TABLE:COL,COL",
                    help="each table and the columns it must still have")  # fmt: skip
    smoke.addoption("--smoke-timeout-ms", type=int, default=None,
                    help="per-connection timeout (default 5000)")  # fmt: skip
    group = parser.getgroup("shards", "split the suite across CI runners by module")
    group.addoption("--shard", default=None, help="run shard I of N, as I/N")
    group.addoption(
        "--store-shard-durations",
        default=None,
        metavar="PATH",
        help="record seconds per test module, for balancing --shard",
    )


def pytest_configure(config):
    # The variables that chose an interpreter before #1753 are refused here,
    # once, rather than by every test that reaches uqs.interpreter.
    from uqs.interpreter import removed_variables_problem

    removed = removed_variables_problem()
    if removed:
        raise pytest.UsageError(removed)
    config.addinivalue_line(
        "markers",
        "shard_apart: a shard unit of its own, not its module's (scripts/dev/pytest_shards.py)",
    )
    for marker in MARKERS:
        config.addinivalue_line("markers", marker)
    for marker, why in OPT_IN.items():
        config.addinivalue_line("markers", f"{marker}: opt-in, select with -m {marker}: {why}")
    spec = config.getoption("--shard")
    if spec:
        try:
            config._uqf_shard = _shards().parse_shard(spec)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None
    path = config.getoption("--store-shard-durations")
    if path:
        config.pluginmanager.register(_shards().Recorder(Path(path)), "shard-durations")


def pytest_collection_modifyitems(config, items):
    _skip_unasked_opt_ins(config, items)
    spec = config.getoption("--shard")
    if not spec:
        return
    shards = _shards()
    index, count = config._uqf_shard
    unit = {item.nodeid: shards.unit_of(item.nodeid, item.keywords) for item in items}
    mine = set(shards.assign(list(unit.values()), shards.load_weights(), count)[index - 1])
    keep = [item for item in items if unit[item.nodeid] in mine]
    dropped = [item for item in items if unit[item.nodeid] not in mine]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep


def _skip_unasked_opt_ins(config, items) -> None:
    """Skip each test with an OPT_IN marker its run's `-m` does not name."""
    import re

    asked = set(re.findall(r"[A-Za-z_]\w*", config.getoption("markexpr") or ""))
    for item in items:
        for marker in OPT_IN.keys() - asked:
            if item.get_closest_marker(marker):
                item.add_marker(pytest.mark.skip(reason=f"opt-in: select it with -m {marker}"))


# --- the tests PeachQ is known to fail (python/peachq_known_gaps.txt) ---------

#: Tests that cannot pass with PeachQ as the q, each with why: strict xfails
#: when $QCMD answers as PeachQ, so a listed test that passes fails the run too.
#: Here, at the root, so it reaches every testpath - tests/qrun/ as well as
#: python/.
PEACHQ_GAPS = Path(__file__).with_name("python") / "peachq_known_gaps.txt"


@functools.cache
def peachq_gaps() -> dict[str, str]:
    """{nodeid: reason} from PEACHQ_GAPS."""
    gaps: dict[str, str] = {}
    for line in PEACHQ_GAPS.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            nodeid, _, reason = line.partition("  # ")
            gaps[nodeid.strip()] = reason.strip()
    return gaps


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """On PeachQ, hold each listed test to failing: CI's PeachQ job runs every
    test with PeachQ as the q, and this list is what keeps it blocking. Which q
    that is, the binary says (#1753) - asked here, as a listed test is about to
    run, and not at collection, which must start no q (#1808)."""
    reason = peachq_gaps().get(item.nodeid)
    if reason is None:
        return
    from uqs.interpreter import PEACHQ, q_impl

    if q_impl() == PEACHQ:
        item.add_marker(pytest.mark.xfail(reason=f"PeachQ: {reason}", strict=True))


# --- the PeachQ the PeachQ tests run on (#1753) ------------------------------


@pytest.fixture(scope="session")
def peachq(request):
    """THE PeachQ for a test that must run on PeachQ, whatever $QCMD is:
    uqs.interpreter.peachq_binary - --peachq PATH, else $QCMD when it is
    PeachQ, else the pinned build, built on first use. Skips, saying why, only
    when none of those can be had; a --peachq that is not PeachQ fails."""
    from uqs.interpreter import peachq_binary
    from uqs.paths import UqsError

    override = request.config.getoption("--peachq")
    try:
        return peachq_binary(override=override)
    except UqsError as exc:
        if override:
            pytest.fail(str(exc))
        _unavailable(request, "peachq", str(exc))


def _unavailable(request, impl: str, why: str):
    """Skip the test - or fail it when the run requires `impl` (--require-q):
    a gate that selected tests on an interpreter it cannot have must fail, not
    pass with everything skipped."""
    if impl in request.config.getoption("--require-q"):
        pytest.fail(f"{why} (this run requires {impl}: --require-q {impl})", pytrace=False)
    pytest.skip(why)


# --- KDB-X, the reference (#1808) ---------------------------------------------


@pytest.fixture(scope="session")
def qcmd(request):
    """The q $QCMD names (else q on PATH), whichever implementation it is.
    Skips without one - fails instead with --require-q kdbx."""
    from uqs.interpreter import q_interpreter

    q = q_interpreter()
    if q is None:
        _unavailable(
            request, "kdbx", "no q interpreter - set $QCMD, or put q on PATH (README#requirements)"
        )
    return q


@pytest.fixture(scope="session")
def kdbx(request, qcmd):
    """The q $QCMD names, when it answers as KDB-X - the reference this tree is
    verified on. Skips otherwise, saying what $QCMD is; fails instead with
    --require-q kdbx."""
    from uqs.interpreter import KDBX, identified
    from uqs.paths import UqsError

    try:
        impl = identified(qcmd)
    except UqsError as exc:
        _unavailable(request, "kdbx", str(exc))
    if impl != KDBX:
        _unavailable(request, "kdbx", f"$QCMD ({qcmd}) is {impl}, not KDB-X")
    return qcmd


@pytest.fixture
def plant_tree():
    """Write a stand-in tree's src/etl/plant_tables.q that q can load - this
    tree's plant runtime (the API before its first table) plus `tables`, each
    `name:([]...)` line qualified under .qetl.plant - and have q evaluate it
    into the tree's schema metadata (#1781). Every reader of a tree's plant
    reads that metadata, never the text, so a stand-in needs it too. Skips
    the test when there is no q interpreter."""
    import re

    from uqs.interpreter import q_interpreter
    from uqs.model import plant_metadata_gen
    from uqs.paths import TABLES_FILE, repo_root

    if q_interpreter() is None:
        pytest.skip("no q interpreter - a stand-in plant tree is evaluated by q")
    real = (repo_root() / TABLES_FILE).read_text().splitlines(keepends=True)
    first = next(i for i, ln in enumerate(real) if re.match(r"^\.qetl\.plant\.\w+:\(\[\]", ln))
    runtime = "".join(real[:first])

    def write(root: Path, tables: str = "") -> Path:
        lines = [
            (".qetl.plant." + ln if re.match(r"^[a-z_]\w*:\(\[\]", ln) else ln)
            for ln in tables.splitlines(keepends=True)
            if ln.strip() != "\\d .qetl.plant"
        ]
        (root / TABLES_FILE).parent.mkdir(parents=True, exist_ok=True)
        (root / TABLES_FILE).write_text(runtime + "".join(lines))
        plant_metadata_gen.write(root)
        return root

    return write
