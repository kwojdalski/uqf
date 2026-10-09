"""`uqs job new NAME --kind backfill`: planning a bounded worker.

Apart from cli/create.py, which reached the module budget: a backfill is the
one `job new` shape that must read the tree it writes into - which workers
already fill a dataset, which tables the plant already defines, whether its
source exists - before it can plan anything.
"""

from __future__ import annotations

from pathlib import Path

from uqs.model.declarations import declaration_calls, read_declarations, symbols
from uqs.model.pipeline import PipelineKind
from uqs.model.schemas import _DEFINITION
from uqs.paths import SOURCE_DIR, TABLES_FILE, WORKER_DIR, UqsError
from uqs.scaffold import worker
from uqs.scaffold.columns import (
    PHYSICAL_NAME,
    Columns,
    definition_columns,
    parse_raw_columns,
)
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


def twin_target(
    repo_root: Path,
    twin_of: str,
    dataset: str | None,
    shape: Columns | None,
    definitions: dict[str, str],
) -> tuple[str, list[tuple[str, str]]]:
    """The dataset and columns a twin of streaming job `twin_of` writes (#716).

    A twin is a worker whose dataset is a table the job publishes - what
    `.qetl.uptime.twins` looks for, and so what `uqs gaps` refills from. The
    dataset and its columns are taken from the job's declaration and the
    plant's definition, so the twin writes the right table by construction
    rather than by matching it up by hand.
    """
    jobs = {d.name: d for d in read_declarations(repo_root) if d.kind is not PipelineKind.BACKFILL}
    if twin_of not in jobs:
        raise UqsError(
            f"--twin-of {twin_of!r} is not a streaming job in this tree - "
            f"the streaming jobs are {', '.join(sorted(jobs))}"
        )
    if shape:
        raise UqsError(
            f"--twin-of takes the columns from the table {twin_of} publishes - drop --columns"
        )
    published = list(jobs[twin_of].publishes)
    if not published:
        raise UqsError(
            f"--twin-of {twin_of}: it publishes nothing, so there is no table for a twin to refill"
        )
    if dataset is None:
        if len(published) > 1:
            raise UqsError(
                f"--twin-of {twin_of} publishes {', '.join(published)} - name the one this "
                "twin refills with --dataset"
            )
        dataset = published[0]
    elif dataset not in published:
        raise UqsError(
            f"--dataset {dataset} is not a table {twin_of} publishes ({', '.join(published)}) - "
            "a twin refills what the job publishes, or it is not a twin"
        )
    if dataset not in definitions:
        raise UqsError(f"{dataset} has no definition in the plant to take its columns from")
    return dataset, definition_columns(definitions[dataset])


def shared_transform(
    repo_root: Path, twin_of: str, transform: str | None
) -> tuple[str, str] | None:
    """(twin_of, its declared transform) for a twin to apply, or None when the
    job declares none - the twin then gets `transform`'s scaffold (#884).

    A job that declares its transform decides the twin's, so --transform
    beside it is refused rather than silently overridden."""
    job = next(d for d in read_declarations(repo_root) if d.name == twin_of)
    if not job.transform:
        return None
    if transform is not None:
        raise UqsError(
            f"--twin-of {twin_of} applies the transform it declares ({job.transform}), "
            "so a refill re-derives what it publishes - drop --transform"
        )
    return twin_of, job.transform


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
    twin_of: str | None = None,
    definitions: dict[str, str] | None = None,
    raw_table: str | None = None,
    raw_columns: str | None = None,
) -> ScaffoldPlan:
    """The bounded worker's plan, or an error naming why there is none.

    `twin_of` names a streaming job whose published table this worker refills:
    its dataset and columns then come from there - see twin_target.
    """
    if (raw_table is None) != (raw_columns is None):
        raise UqsError(
            "--raw-table and --raw-columns go together: the physical table the new source "
            "reads, and the columns it reads from it"
        )
    raw = None
    if raw_table is not None:
        if not PHYSICAL_NAME.match(raw_table):
            raise UqsError(
                f"--raw-table {raw_table!r} must start with a letter and hold only letters, "
                "digits and underscores"
            )
        raw = (raw_table, parse_raw_columns(raw_columns or ""))
    shared = None
    if twin_of is not None:
        dataset, shape = twin_target(repo_root, twin_of, dataset, shape, definitions or {})
        shared = shared_transform(repo_root, twin_of, transform)
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
        shared=shared,
        raw=raw,
        reuse_source=(repo_root / SOURCE_DIR / f"{source or name}.q").is_file(),
        define_table=dataset not in defined_tables(repo_root),
    )
