"""A row-limited rehearsal copy of HDB tables: `uqs data sample` (#2812).

A schema migration is rehearsed on a small HDB, not on production volumes.
scripts/hdb/sample.q does the reading and the writing: at most `limit` rows
per table per day, every symbol column decoded against the source's sym file
and enumerated against the destination's, the schema checked before anything
is written. This module resolves and refuses the paths, runs the q, and moves
each staged table into the destination.

Nothing partial is ever visible in the destination. The q stages each table
beside the destination root (`<root>.sample-<pid>-*`), outside it, and a
table is moved in only once complete - one rename, or an atomic exchange for
`--on-conflict replace`. A run that is interrupted leaves only its stage
directory, which the next applied run removes and reports. The destination
root's write lock, the one every uqs and q writer shares
(scripts/hdb/lock.q), is held while the run writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter
from uqs.stack.hdb_migrate import _exchange, _write_lock

#: The q that samples, relative to the scripts directory.
SAMPLE_SCRIPT = Path("hdb") / "sample.q"
#: What `--on-conflict` may say; None refuses a table the destination holds.
CONFLICTS = ("ignore", "replace")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
#: A stage directory's name after its root's: `<root>.sample-<pid>-<random>`.
_STAGE = re.compile(r"\.sample-(\d+)-[^/]+$")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def resolve_roots(source: Path, destination: Path) -> tuple[Path, Path]:
    """The source and destination roots, resolved, or refused: a source that
    is not a directory, a destination that is a file or has no parent, and two
    roots that are the same directory or one inside the other - a destination
    inside the source would write into the HDB being sampled."""
    src = source.expanduser().resolve()
    dst = destination.expanduser().resolve()
    if not src.is_dir():
        raise UqsError(f"--hdb {source}: no HDB directory there")
    if dst.exists() and not dst.is_dir():
        raise UqsError(f"--into-hdb {destination}: a file, not an HDB directory")
    if not dst.parent.is_dir():
        raise UqsError(f"--into-hdb {destination}: its parent {dst.parent} does not exist")
    if src == dst:
        raise UqsError(f"--hdb and --into-hdb are the same directory, {src}")
    if dst.is_relative_to(src):
        raise UqsError(f"--into-hdb {dst} is inside the source HDB {src}: it would write into it")
    if src.is_relative_to(dst):
        raise UqsError(f"the source HDB {src} is inside --into-hdb {dst}")
    for root, flag in ((src, "--hdb"), (dst, "--into-hdb")):
        if (root / "par.txt").exists():
            raise UqsError(f"{flag} {root} is a segmented HDB (par.txt): not sampled yet")
    return src, dst


def writer_lock(root: Path) -> Path:
    """The root's write lock file, as .qetl.io.hdb_lock names it: beside it."""
    return root.parent / f"{root.name}.write.lock"


def stale_stages(dst: Path) -> list[Path]:
    """The stage directories interrupted runs left beside `dst`: those whose
    process is gone. A live run's is not one."""
    found = []
    for path in sorted(dst.parent.glob(f"{dst.name}.sample-*")):
        m = _STAGE.search(path.name)
        if m and path.is_dir() and not _alive(int(m.group(1))):
            found.append(path)
    return found


def _run(
    paths: UqsPaths,
    q: Path,
    env: dict[str, str],
    argv: list[str],
    tag: str,
) -> dict[str, Any]:
    result = subprocess.run(
        [str(q), str(paths.scripts_dir / SAMPLE_SCRIPT), *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=paths.repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=3600,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith(tag)]
    if result.returncode != 0 or len(lines) != 1:
        said = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise UqsError(" ".join(said).removeprefix("hdb_sample: ") or "the sample failed")
    return json.loads(lines[0][len(tag) :])


def _move_in(dst: Path, stage: Path, row: dict[str, Any]) -> None:
    """Move one staged table into the destination: a rename, or for a table
    being replaced an atomic exchange, the old one then removed."""
    staged = stage / row["day"] / row["tbl"]
    final = dst / row["day"] / row["tbl"]
    final.parent.mkdir(exist_ok=True)
    if row["status"] == "replace":
        _exchange(staged, final)
        shutil.rmtree(staged)
        return
    if final.exists():
        raise UqsError(f"{final} appeared while the sample was being written; it was left as it is")
    staged.rename(final)


def sample(
    paths: UqsPaths,
    source: Path,
    destination: Path,
    tables: list[str],
    days: list[date],
    limit: int,
    on_conflict: str | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Sample `tables` on `days` from `source` into `destination`; a dry run
    unless `apply`.

    Returns the plan - every (day, table) with its status and the rows it
    takes - and, applied, what was moved in and which interrupted stages were
    removed. Raises, with nothing written, on a path, schema or conflict
    refusal. The source is only read; a dry run writes nothing anywhere.
    """
    if not tables:
        raise UqsError("name the tables to sample: --table TABLE, repeatable")
    for t in tables:
        if not _NAME.fullmatch(t):
            raise UqsError(f"{t!r} is not a q table name")
    if not days:
        raise UqsError("name the days to sample: --date D, or --from D --to D")
    if limit < 1:
        raise UqsError("--rows must be at least 1")
    if on_conflict is not None and on_conflict not in CONFLICTS:
        raise UqsError(f"--on-conflict {on_conflict!r}: a sample can ignore or replace")
    src, dst = resolve_roots(source, destination)
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("sampling an HDB needs q, and no interpreter is runnable - set QCMD")
    argv = [
        str(src),
        str(dst),
        ",".join(dict.fromkeys(tables)),
        ",".join(f"{d:%Y.%m.%d}" for d in days),
        str(limit),
        on_conflict or "refuse",
    ]
    plan = _run(paths, q, env, [*argv, "plan", "-"], "PLAN|")
    doc: dict[str, Any] = {**plan, "on_conflict": on_conflict, "applied": False}
    doc["skipped_conflicts"] = sum(r["status"] == "ignore" for r in plan["partitions"])
    if not apply:
        doc["stale_stages"] = [str(p) for p in stale_stages(dst)]
        return doc

    dst.mkdir(exist_ok=True)
    _write_lock(paths, q, env, "acquire", dst)
    stage: Path | None = None
    moved: list[dict[str, Any]] = []
    try:
        recovered = [str(p) for p in stale_stages(dst)]
        for path in recovered:
            shutil.rmtree(path)
        stage = Path(tempfile.mkdtemp(prefix=f"{dst.name}.sample-{os.getpid()}-", dir=dst.parent))
        staged = _run(paths, q, env, [*argv, "stage", str(stage)], "STAGED|")
        for row in staged["staged"]:
            _move_in(dst, stage, row)
            moved.append(row)
    except UqsError as exc:
        if moved:
            done = ", ".join(f"{r['tbl']} on {r['day']}" for r in moved)
            raise UqsError(f"{exc} - already moved in, each complete: {done}") from exc
        raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        with contextlib.suppress(UqsError):
            _write_lock(paths, q, env, "release", dst)
    doc.update(
        applied=True,
        moved=moved,
        copied_rows=sum(r["rows"] for r in moved),
        recovered=recovered,
    )
    return doc


def render(doc: dict[str, Any]) -> str:
    """The plan or result, for a person to read."""
    head = "sampled" if doc["applied"] else "would sample"
    out = [
        f"{head} {', '.join(doc['tables'])} from {doc['source']} into {doc['destination']}, "
        f"at most {doc['limit']:,} row(s) per table per day"
    ]
    notes = {
        "copy": "{rows:,} of {source_rows:,} row(s)",
        "replace": "{rows:,} of {source_rows:,} row(s), replacing the table there",
        "fill": "not in the source that day: an empty table, so the day stays queryable",
        "ignore": "already there: kept (--on-conflict ignore)",
        "absent": "not in the source that day, already there: kept",
        "none": "the source has no partition that day: nothing written",
    }
    for row in doc["partitions"]:
        out.append(f"  {row['day']}  {row['tbl']:<24} " + notes[row["status"]].format(**row))
    if doc["applied"]:
        out.append(f"moved in {len(doc['moved'])} table(s), {doc['copied_rows']:,} row(s)")
        for path in doc["recovered"]:
            out.append(f"removed an interrupted run's stage: {path}")
    else:
        for path in doc.get("stale_stages", []):
            out.append(f"an interrupted run's stage, removed by --apply: {path}")
    return "\n".join(out)
