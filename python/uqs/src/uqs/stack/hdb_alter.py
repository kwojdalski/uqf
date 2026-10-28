"""One table's maintenance across an HDB: the front end of scripts/gates/hdb_alter.q (#1755).

`uqs data hdb-alter` and `uqs data hdb-check --table` are thin: every decision
about the database - which partitions hold the table, whether they agree, what
each would be written, the journal, the undo, the final check - is the q
script's, so the same script can be copied to a host with no uqf on it and run
there with the server's own q (stack/hdb_remote.py). This module says what to
ask the script, runs it on this machine, and reads its one JSON document back.

Also here, for the runtime's OWN HDB (what `uqs --target NAME data ...` runs
on the release): its write lock, which uqf's own writers honour, and the reload
of its HDB processes once a change is written.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uqs.interpreter import q_command, q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_migrate
from uqs.stack.env import with_interpreter
from uqs.stack.listing import _list_processes
from uqs.stack.runtime import query

#: The script, under scripts/, and the vendored KX dbmaint it loads from
#: ../../lib/kx-dbmaint: the two files a host without uqf needs.
SCRIPT = Path("gates") / "hdb_alter.q"
DBMAINT = Path("lib") / "kx-dbmaint" / "dbmaint.q"

#: The operations that change a table; one per run.
CHANGES = ("reorder", "rename", "attr", "cast")
#: Every operation the script takes: the consistency check alone, the fill
#: behind `hdb-check --table`, the changes, and the undo of one.
OPERATIONS = ("check", "fill", *CHANGES, "undo")

#: Seconds a run may take: a cast rewrites a column in every partition.
TIMEOUT_S = 6 * 3600


@dataclass(frozen=True)
class Request:
    """What to ask the script: the HDB as the host that runs it names it."""

    hdb: str
    table: str | None
    operation: str
    value: str = ""
    #: a q file declaring the table, for a fill (copied with the script when remote)
    schema: Path | None = None
    template: str | None = None
    #: the journal a change writes: the path its dry run named
    journal: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise UqsError(f"unknown operation {self.operation!r}")


def args(req: Request, *, apply: bool, schema_path: str | None = None) -> list[str]:
    """The script's command line after its path: -json always, the one stdout
    document this reads. `schema_path` is the declaration's path where the
    script runs (a remote copy's), defaulting to `req.schema`."""
    out = ["-hdb", req.hdb]
    if req.operation == "undo":
        out += ["-undo", req.value]
    else:
        if not req.table:
            raise UqsError("name the table")
        out += ["-table", req.table]
        if req.operation != "check":
            out += [f"-{req.operation}", *([req.value] if req.value else [])]
    schema = schema_path or (str(req.schema) if req.schema else None)
    if schema:
        out += ["-schema", schema]
    if req.template:
        out += ["-template", req.template]
    if req.journal:
        out += ["-journal", req.journal]
    if apply:
        out.append("-apply")
    return [*out, "-json", "-q"]


def parse(result: subprocess.CompletedProcess[str], where: str) -> dict[str, Any]:
    """The script's JSON document, with its exit code as `exit` - or a refusal
    saying what it printed instead."""
    lines = [line for line in (result.stdout or "").splitlines() if line.startswith("{")]
    if not lines:
        said = " ".join(((result.stderr or "") + (result.stdout or "")).strip().splitlines()[-5:])
        raise UqsError(
            f"hdb_alter.q {where} printed no plan (exit {result.returncode}): {said or 'nothing'}"
        )
    try:
        doc = json.loads(lines[-1])
    except ValueError:
        raise UqsError(f"hdb_alter.q {where} printed something other than JSON") from None
    return {**doc, "exit": result.returncode}


def run_local(paths: UqsPaths, req: Request, *, apply: bool) -> dict[str, Any]:
    """Run the script on this machine, with this runtime's q."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError(f"no q interpreter ({q_command(env)!r} is not runnable) - set QCMD")
    try:
        result = subprocess.run(
            [str(q), str(paths.scripts_dir / SCRIPT), *args(req, apply=apply)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            env=env,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(f"hdb_alter.q did not finish within {TIMEOUT_S}s") from None
    return parse(result, "on this machine")


@contextlib.contextmanager
def declaration(schema_text: str, table: str | None, declared: set[str]) -> Iterator[Path | None]:
    """A temporary file holding this runtime's declarations when they declare
    `table`, else None: the fill then takes its schema from a partition."""
    if not table or table not in declared:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="uqf_hdb_alter_") as tmp:
        path = Path(tmp) / "database.q"
        path.write_text(schema_text)
        yield path


# ------------------------------------------------------------ this runtime's own HDB


@contextlib.contextmanager
def write_lock(paths: UqsPaths) -> Iterator[None]:
    """Hold the runtime's HDB write lock while a change is written: the lock
    uqf's own writers (a backfill, `uqs data migrate`) take, so one that is
    running refuses this - and this refuses while one runs. TorQ's own end of
    day does not take it."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("taking the HDB write lock needs q; set QCMD")
    hdb_migrate._write_lock(paths, q, env, "acquire")
    try:
        yield
    finally:
        hdb_migrate._write_lock(paths, q, env, "release")


#: What a reader is sent: TorQ's own reload (hdbstandard.q), else `\l .`.
RELOAD_Q = '@[{[d] $[`reload in key `.; reload d; system "l ."]; "reloaded"}; .z.d; {[e] e}]'


def reload_own_readers(paths: UqsPaths) -> list[dict[str, str]]:
    """Ask each HDB process this runtime declares to reload: one row per
    process, `ok` "yes", "no" or "down" (nothing listens: it maps the change
    when it starts)."""
    out = []
    for row in _list_processes(paths, None):
        if row["proctype"] != "hdb":
            continue
        try:
            said = query(RELOAD_Q, int(row["port"]), timeout=30)
        except Exception as exc:  # noqa: BLE001 - kola raises its own, untyped, errors
            down = "refused" in str(exc).lower() or "connect" in str(exc).lower()
            out.append({"procname": row["procname"], "ok": "down" if down else "no",
                        "detail": str(exc)})  # fmt: skip
            continue
        text = said.decode() if isinstance(said, bytes) else str(said)
        out.append({"procname": row["procname"], "ok": "yes" if text == "reloaded" else "no",
                    "detail": text})  # fmt: skip
    return out
