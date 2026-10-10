"""Offline, lossless migration of one HDB table partition (#1096).

The q stage rebuilds the table from an explicit rename/cast declaration and
checks the rows it wrote. The final exchange is one filesystem operation; the
old table remains beside the HDB as a rollback copy. Backfill writers share
the root's write lock, and the operator must stop the stack before applying a
schema migration because TorQ end of day does not take that lock.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter
from uqs.stack.hdb_types import declared_schema

STAGE_SCRIPT = Path("gates") / "stage_hdb_migration.q"
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


def migrate(
    paths: UqsPaths,
    table: str,
    partition: date,
    renames: dict[str, str],
    casts: set[str],
    *,
    apply: bool = False,
) -> str:
    """Migrate one date/table to this release's schema; dry-run by default.

    `renames` maps old to new column names. Any type change must also be
    listed in `casts`, and q accepts it only when casting back reproduces
    every original value. `apply` stages, validates and atomically exchanges
    the table; the previous table is retained at the reported backup path.
    """
    _name(table)
    for old, new in renames.items():
        _name(old)
        _name(new)
    for column in casts:
        _name(column)
    if len(set(renames.values())) != len(renames):
        raise UqsError("two source columns cannot rename to the same target")
    if not renames and not casts:
        raise UqsError("name at least one --rename or --cast")

    if paths.hdb_dir.is_symlink():
        raise UqsError("symlinked HDB roots need a migration that shares the writer's lock path")
    root = paths.hdb_dir.resolve()
    if not root.is_dir():
        raise UqsError(f"no HDB at {root}")
    if (root / "par.txt").exists():
        raise UqsError("segmented HDBs need a segment-aware migration; no files changed")
    live = root / partition.strftime("%Y.%m.%d") / table
    if not (live.is_dir() and (live / ".d").is_file()):
        raise UqsError(f"no splayed table at {live}")
    description = f"{partition:%Y.%m.%d}/{table}: rename {renames or '{}'}, cast {sorted(casts)}"
    if not apply:
        return f"would migrate {description}; pass --apply after stopping the stack"

    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("HDB migration needs q; set QCMD")
    lock = root.with_name(root.name + ".write.lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise UqsError(f"HDB write lock already held at {lock}; stop writers first") from exc

    stage_root: Path | None = None
    swapped = False
    try:
        (lock / "owner").write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname()}))
        stage_root = Path(tempfile.mkdtemp(prefix=root.name + ".migration-", dir=root.parent))
        stage = stage_root / "new" / partition.strftime("%Y.%m.%d") / table
        stage.mkdir(parents=True)
        schema = declared_schema(paths)
        (stage_root / "database.q").write_text(schema)
        (stage_root / "migration.json").write_text(
            json.dumps(
                {
                    "hdb": str(root),
                    "partition": partition.isoformat(),
                    "table": table,
                    "renames": renames,
                    "casts": sorted(casts),
                },
                indent=2,
            )
        )
        # q's system "l <path>" cannot load a path with spaces. Keep the
        # schema used for this migration beside the backup, but load a copy
        # from the OS temp directory whose generated name has no spaces.
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
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=paths.repo_root,
                env=env,
                stdin=subprocess.DEVNULL,
                timeout=600,
            )
        lines = [line for line in result.stdout.splitlines() if line.startswith("STAGED|")]
        if result.returncode != 0 or len(lines) != 1:
            said = (result.stderr or result.stdout).strip().splitlines()[-3:]
            raise UqsError(f"HDB migration stage failed: {' '.join(said)}")
        _, count, source = lines[0].split("|", 2)
        if Path(source).resolve() != live.resolve() or not count.isdecimal():
            raise UqsError("HDB migration stage reported an unexpected source path or row count")
        _exchange(live, stage)
        swapped = True
        return f"migrated {description}; {count} row(s); previous table retained at {stage}"
    finally:
        if stage_root is not None and not swapped:
            shutil.rmtree(stage_root)
        (lock / "owner").unlink(missing_ok=True)
        lock.rmdir()
