"""Offline, lossless migration of one HDB table partition (#1096).

The q stage rebuilds the table from an explicit rename/cast declaration and
checks the rows it wrote. The final exchange is one filesystem operation; the
old table remains beside the HDB as a rollback copy. Backfill writers share
the root's write lock, and the operator must stop the stack before applying a
schema migration because TorQ end of day does not take that lock.

The write lock is taken and given up by q (scripts/gates/hdb_lock.q, #1168), so
its path, owner record and staleness rule are the q writers' own. It names
this process as the holder: q writers wait while it runs and break the lock
once it is gone, however it ended.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from uqs.interpreter import KDBX, q_impl, q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import converted, qtree
from uqs.stack.env import with_interpreter
from uqs.stack.hdb_types import declared_schema

STAGE_SCRIPT = Path("gates") / "stage_hdb_migration.q"
LOCK_SCRIPT = Path("gates") / "hdb_lock.q"
#: What LOCK_SCRIPT loads, relative to its working directory: the lock
#: protocol and the HDB lock path, and nothing else.
LOCK_MODULES = ("src/etl/core/backfill_state.q", "src/etl/core/io_hdb.q")
_NAME = re.compile(r"^[a-z_][a-z_0-9]*$")


def _name(value: str) -> str:
    if not _NAME.fullmatch(value):
        raise UqsError(f"{value!r} is not a q table or column name")
    return value


def _exchange(left: Path, right: Path) -> None:
    """Exchange two populated directories atomically, or change neither."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        at_fdcwd = -2
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        at_fdcwd = -100
    else:
        function = None
        at_fdcwd = 0
    if function is None:
        raise UqsError("this platform has no atomic directory exchange")
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    # RENAME_SWAP on macOS, RENAME_EXCHANGE on Linux: both are flag 2.
    result = function(at_fdcwd, os.fsencode(left), at_fdcwd, os.fsencode(right), 2)
    if result != 0:
        err = ctypes.get_errno()
        raise UqsError(f"atomic HDB partition exchange failed: {os.strerror(err)}")


@contextlib.contextmanager
def _lock_code(paths: UqsPaths, env: dict[str, str]) -> Iterator[Path]:
    """The directory LOCK_SCRIPT loads LOCK_MODULES from.

    The checkout itself on KDB-X, or for a release whose q was converted when
    it was built. PeachQ cannot load the modules' nested `\\d` contexts
    (peachq-org/peachq#80), so for it they are flattened by the converter a
    flattened runtime's tree uses (stack/qtree.py) into a temporary directory:
    the same code, never a second copy of the protocol.
    """
    if q_impl(env) == KDBX or converted.recorded_target(paths.repo_root) is not None:
        yield paths.repo_root
        return
    texts = {rel: (paths.repo_root / rel).read_text() for rel in LOCK_MODULES}
    with tempfile.TemporaryDirectory(prefix="uqf_hdb_lock_") as tmp:
        for rel, text in qtree.convert(paths.repo_root, texts).items():
            (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / rel).write_text(text)
        yield Path(tmp)


def _run_lock_script(
    paths: UqsPaths, q: Path, env: dict[str, str], action: str, code: Path, hdb: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(q),
            str(paths.scripts_dir / LOCK_SCRIPT),
            action,
            str(hdb.absolute()),
            str(os.getpid()),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=code,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def _write_lock(
    paths: UqsPaths, q: Path, env: dict[str, str], action: str, hdb: Path | None = None
) -> str:
    """Acquire or release the HDB root's write lock through q, for this pid.

    The root is passed as the writers are given it (KDBHDB), made absolute but
    not resolved: .qetl.io.hdb_lock computes the path, so both sides agree.
    `hdb` is a root named by --hdb; None, this runtime's.
    """
    with _lock_code(paths, env) as code:
        result = _run_lock_script(paths, q, env, action, code, hdb or paths.hdb_dir)
    tag = "LOCKED|" if action == "acquire" else "RELEASED|"
    lines = [line for line in result.stdout.splitlines() if line.startswith(tag)]
    if result.returncode != 0 or len(lines) != 1:
        said = " ".join((result.stderr or result.stdout).strip().splitlines()[-3:])
        if action == "acquire" and "could not take the lock" in said:
            raise UqsError(f"HDB write lock already held; stop writers first: {said}")
        raise UqsError(f"HDB write lock {action} failed: {said}")
    return lines[0].split("|", 1)[1]


def _run_stage(
    paths: UqsPaths,
    q: Path,
    env: dict[str, str],
    root: Path,
    partition: date,
    table: str,
    stage: Path,
    mode: str,
    renames: dict[str, str] | None = None,
    casts: set[str] | None = None,
) -> str:
    """Run STAGE_SCRIPT; return the staged row count, for `check` the row count
    it would stage, or for `reset-check` the `stored|declared` column specs it
    printed. Raises with q's last words."""
    renames, casts = renames or {}, casts or set()
    schema = declared_schema(paths)
    # q's system "l <path>" cannot load a path with spaces. Load a copy from
    # the OS temp directory whose generated name has no spaces.
    with tempfile.TemporaryDirectory(prefix="uqf_migration_schema_") as schema_tmp:
        schema_file = Path(schema_tmp) / "database.q"
        schema_file.write_text(schema)
        result = subprocess.run(
            [
                str(q),
                str(paths.scripts_dir / STAGE_SCRIPT),
                str(root),
                str(schema_file),
                partition.strftime("%Y.%m.%d"),
                table,
                str(stage),
                ",".join(renames) or "-",
                ",".join(renames.values()) or "-",
                ",".join(sorted(casts)) or "-",
                mode,
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
    tag = {"reset-check": "PLAN|", "check": "CHECKED|"}.get(mode, "STAGED|")
    lines = [line for line in result.stdout.splitlines() if line.startswith(tag)]
    if result.returncode != 0 or len(lines) != 1:
        said = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise UqsError(f"HDB migration stage failed: {' '.join(said)}")
    if mode in ("reset-check", "check"):
        return lines[0].split("|", 1)[1]
    _, count, source = lines[0].split("|", 2)
    live = root / partition.strftime("%Y.%m.%d") / table
    if Path(source).resolve() != live.resolve() or not count.isdecimal():
        raise UqsError("HDB migration stage reported an unexpected source path or row count")
    return count


def migrate(
    paths: UqsPaths,
    table: str,
    partition: date,
    renames: dict[str, str],
    casts: set[str],
    *,
    apply: bool = False,
    reset_empty: bool = False,
    hdb: Path | None = None,
) -> str:
    """Migrate one date/table to this release's schema; dry-run by default.

    `hdb` is the HDB root, `--hdb DIR`; None is this runtime's (#2237).

    `renames` maps old to new column names. Any type change must also be
    listed in `casts`, and q accepts it only when casting back reproduces
    every original value. `apply` stages, validates and atomically exchanges
    the table; the previous table is retained at the reported backup path.

    `reset_empty` (#2251) instead rebuilds a table whose every stored column
    is empty - obsolete ones included - from its current declaration, through
    this same lock, staging, validation, exchange and backup. q checks the
    emptiness (and checks it again under the lock, just before staging); the
    dry run reports the schema change from that same check and writes nothing.
    It takes no renames or casts.

    A dry run of renames and casts is likewise q's own check - the stored
    columns against the declaration, each cast round-tripped - with nothing
    staged, so a migration `apply` would refuse is refused here with the same
    message (#2463). Either dry run needs q.
    """
    _name(table)
    for old, new in renames.items():
        _name(old)
        _name(new)
    for column in casts:
        _name(column)
    if len(set(renames.values())) != len(renames):
        raise UqsError("two source columns cannot rename to the same target")
    if reset_empty and (renames or casts):
        raise UqsError(
            "--reset-empty rebuilds an empty table from its declaration; "
            "it cannot be combined with --rename or --cast"
        )
    if not reset_empty and not renames and not casts:
        raise UqsError("name at least one --rename or --cast")

    given = hdb if hdb is not None else paths.hdb_dir
    if given.is_symlink():
        raise UqsError("symlinked HDB roots need a migration that shares the writer's lock path")
    root = given.resolve()
    if not root.is_dir():
        raise UqsError(f"no HDB at {root}")
    if (root / "par.txt").exists():
        raise UqsError("segmented HDBs need a segment-aware migration; no files changed")
    live = root / partition.strftime("%Y.%m.%d") / table
    if not (live.is_dir() and (live / ".d").is_file()):
        raise UqsError(f"no splayed table at {live}")
    if reset_empty:
        description = f"{partition:%Y.%m.%d}/{table}: reset empty table to its declaration"
    else:
        description = (
            f"{partition:%Y.%m.%d}/{table}: rename {renames or '{}'}, cast {sorted(casts)}"
        )
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("HDB migration needs q; set QCMD")
    if not apply:
        if reset_empty:
            plan = _run_stage(paths, q, env, root, partition, table, Path("-"), "reset-check")
            old, new = plan.split("|", 1)
            return (
                f"would migrate {description}; every stored column is empty\n"
                f"  stored:   {old.replace(',', ', ')}\n"
                f"  declared: {new.replace(',', ', ')}\n"
                "pass --apply after stopping the stack"
            )
        rows = _run_stage(paths, q, env, root, partition, table, Path("-"), "check", renames, casts)
        return f"would migrate {description}; {rows} row(s); pass --apply after stopping the stack"

    _write_lock(paths, q, env, "acquire", given)

    stage_root: Path | None = None
    swapped = False
    try:
        stage_root = Path(tempfile.mkdtemp(prefix=root.name + ".migration-", dir=root.parent))
        stage = stage_root / "new" / partition.strftime("%Y.%m.%d") / table
        stage.mkdir(parents=True)
        (stage_root / "database.q").write_text(declared_schema(paths))
        (stage_root / "migration.json").write_text(
            json.dumps(
                {
                    "hdb": str(root),
                    "partition": partition.isoformat(),
                    "table": table,
                    "renames": renames,
                    "casts": sorted(casts),
                    "reset_empty": reset_empty,
                },
                indent=2,
            )
        )
        mode = "reset" if reset_empty else "migrate"
        count = _run_stage(paths, q, env, root, partition, table, stage, mode, renames, casts)
        _exchange(live, stage)
        swapped = True
        return f"migrated {description}; {count} row(s); previous table retained at {stage}"
    finally:
        if stage_root is not None and not swapped:
            shutil.rmtree(stage_root)
        # A release that fails must not hide the migration's own outcome, and
        # cannot strand the root: the lock names this pid, so the next q
        # writer breaks it once uqs has exited.
        with contextlib.suppress(UqsError):
            _write_lock(paths, q, env, "release", given)
