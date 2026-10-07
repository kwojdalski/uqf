"""`uqs job new NAME --kind backfill`: planning a bounded worker.

Apart from cli/create.py, which reached the module budget: a backfill is the
one `job new` shape that must read the tree it writes into - which workers
already fill a dataset, which tables the plant already defines, whether its
source exists - before it can plan anything.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model.declarations import declaration_calls, symbols
from uqs.model.schemas import _DEFINITION
from uqs.paths import SOURCE_DIR, TABLES_FILE, WORKER_DIR, UqsError
from uqs.scaffold import worker
from uqs.scaffold.columns import Columns
from uqs.scaffold.plan import ScaffoldPlan


def workers_filling(repo_root: Path, dataset: str, partition: str | None = None) -> list[str]:
    """Workers that already fill `dataset` in `partition` (None: no partition).

    `.qetl.job.bounded.define` refuses two workers on one dataset AND partition (#60,
    #185), so a second one on a claimed pair is a tree that no longer LOADS.
    Caught here, before anything is written, rather than as a bare error from
    inside a declaration.
    """
    found = []
    for path in sorted((repo_root / WORKER_DIR).glob("*.q")):
        for fn, name, fields in declaration_calls(path.read_text()):
            if fn != "qetl.job.bounded.define":
                continue
            claimed = symbols(fields["partition"])[:1] if "partition" in fields else ()
            if symbols(fields.get("dataset", "")) == (dataset,) and claimed == (
                (partition,) if partition else ()
            ):
                found.append(name)
    return found


def defined_tables(repo_root: Path) -> set[str]:
    """The plant tables the q file defines, read from the tree being written
    into rather than the one this package was imported from."""
    return {m.group(1) for m in _DEFINITION.finditer((repo_root / TABLES_FILE).read_text())}


def backfill_plan(
    repo_root: Path,
    name: str,
    dataset: str | None,
    shape: Columns | None,
    *,
    width: str | None,
    source: str | None,
    procname: str | None,
    transport: str | None,
    partition: str | None,
    check: bool,
    transform: str | None,
    start_with_all: bool,
) -> ScaffoldPlan:
    """The bounded worker's plan, or an error naming why there is none."""
    if start_with_all:
        # A backfill runs a window and exits; `uqs start all` starts
        # standing processes, and .qetl.job.bounded.define has no such key.
        raise UqsError("--start-with-all is for standing jobs, not a backfill - drop it")
    if not dataset:
        raise UqsError("--kind backfill needs --dataset: the table it fills")
    claimed = workers_filling(repo_root, dataset, partition)
    if claimed:
        where = f"partition {partition!r}" if partition else "no partition"
        raise UqsError(
            f"dataset {dataset!r} is already filled by {', '.join(claimed)} "
            f"in {where}, "
            "and .qetl.job.bounded.define refuses two workers on one dataset and "
            "partition - pick another --dataset, or another --partition"
        )
    # An existing source is reused, not rewritten, and an existing
    # table is not defined twice: a second worker over rows someone
    # already declared is the common case after the first.
    return worker.bounded_worker(
        name,
        dataset,
        shape,
        width="1D" if width is None else width,
        source=source,
        procname=procname,
        transport=transport,
        partition=partition,
        check=check,
        transform=transform or "passthrough",
        reuse_source=(repo_root / SOURCE_DIR / f"{source or name}.q").is_file(),
        define_table=dataset not in defined_tables(repo_root),
    )
