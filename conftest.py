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

import os

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


# --- sharding across CI runners (#1696, scripts/dev/pytest_shards.py) ------------


def _shards():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("scripts") / "dev" / "pytest_shards.py"
    spec = importlib.util.spec_from_file_location("pytest_shards", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser):
    group = parser.getgroup("shards", "split the suite across CI runners by module")
    group.addoption("--shard", default=None, help="run shard I of N, as I/N")
    group.addoption(
        "--store-shard-durations",
        default=None,
        metavar="PATH",
        help="record seconds per test module, for balancing --shard",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "shard_apart: a shard unit of its own, not its module's (scripts/dev/pytest_shards.py)",
    )
    spec = config.getoption("--shard")
    if spec:
        import pytest

        try:
            config._uqf_shard = _shards().parse_shard(spec)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None
    path = config.getoption("--store-shard-durations")
    if path:
        from pathlib import Path

        config.pluginmanager.register(_shards().Recorder(Path(path)), "shard-durations")


def pytest_collection_modifyitems(config, items):
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
