"""`uqs job new NAME --kind backfill`: planning a bounded worker.

Apart from cli/create.py, which reached the module budget: a backfill is the
one `job new` shape that must read the tree it writes into - which workers
already fill a dataset, which tables the plant already defines, whether its
source exists - before it can plan anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.model.declarations import (
    declaration_calls,
    read_declarations,
    strip_q_comments,
    symbols,
)
from uqs.model.pipeline import PipelineKind
from uqs.model.schemas import _DEFINITION
from uqs.paths import ETL_DIR, SOURCE_DIR, TABLES_FILE, WORKER_DIR, UqsError
from uqs.scaffold import worker
from uqs.scaffold.columns import (
    PHYSICAL_NAME,
    Columns,
    as_columns,
    definition_columns,
    parse_raw_columns,
    type_char,
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
    `.qetl.uptime.twins` looks for, and so what `uqs job gaps` refills from. The
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
            f"--twin-of takes the columns from the table {twin_of} publishes - drop --schema"
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


#: The declaration directories `src/etl/init.q` loads before workers/: a
#: transform a worker names must be registered in one of them, or
#: `.qetl.job.bounded.define` refuses it as not registered (#1579).
BEFORE_WORKERS = (SOURCE_DIR, ETL_DIR / "transforms")


def require_loaded_before_workers(repo_root: Path, twin_of: str, transform: str) -> None:
    """Refuse a twin whose shared transform is registered too late to load.

    The tree loads sources, transforms, workers, then streaming. A transform
    registered inside a streaming job's file - where the job defines it beside
    its handler - is not registered yet when the twin's worker is defined, so
    the twin would stop the whole tree loading. Extracting it to
    src/etl/transforms/ is the author's move, not the scaffold's to guess.
    """
    define = re.compile(rf"\.qetl\.transform\.define\[`{re.escape(transform)}\s*;")
    for directory in BEFORE_WORKERS:
        for path in sorted((repo_root / directory).glob("*.q")):
            if define.search(strip_q_comments(path.read_text())):
                return
    raise UqsError(
        f"--twin-of {twin_of} applies its transform {transform}, which is not registered "
        f"under {' or '.join(d.as_posix() for d in BEFORE_WORKERS)} - the tree loads "
        "workers before streaming jobs, so the twin's worker could not find it. Move "
        f"{transform}'s .qetl.transform.define into src/etl/transforms/ first, as "
        "src/etl/transforms/book_liquidity.q does for book_liquidity_snapshot"
    )


def _meta(cols: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """[(column, meta type char)]: what .qetl.transform compares, attributes aside."""
    out = []
    for name, literal in cols:
        try:
            out.append((name, type_char(literal)))
        except UqsError:
            out.append((name, literal))
    return out


def _shown(cols: list[tuple[str, str]]) -> str:
    return ", ".join(f"{c}:{t}" for c, t in cols)


def check_passthrough(dataset: str, source: str, columns: Columns, definition: str) -> None:
    """Refuse a passthrough from a new source shaped by `columns` into an
    existing `dataset` of another shape (#1593).

    The passthrough declares its input and output as the dataset's shape and
    its example from the source's fixture, which `columns` shape: two shapes,
    which .qetl.transform.define refuses only when the tree next loads."""
    have = _meta(as_columns(columns))
    want = _meta(definition_columns(definition))
    if have != want:
        raise UqsError(
            f"the new source {source}'s columns ({_shown(have)}) are not {dataset}'s "
            f"({_shown(want)}), so a passthrough cannot publish them - pass --transform derive "
            f"and map one to the other, or give --schema {dataset}'s shape"
        )


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
    partition_column: str | None = None,
    transform: str | None,
    start_with_all: bool,
    twin_of: str | None = None,
    definitions: dict[str, str] | None = None,
    raw_table: str | None = None,
    raw_columns: str | None = None,
    known_tables: set[str] | None = None,
) -> ScaffoldPlan:
    """The bounded worker's plan, or an error naming why there is none.

    `twin_of` names a streaming job whose published table this worker refills:
    its dataset and columns then come from there - see twin_target.
    `known_tables` is every table the plant carries, the vendored starter
    pack's included (cli/create_plant.py plant_tables): a dataset among them
    is not defined a second time (#1602). Without it, only the tree's own
    plant_tables.q is read.
    """
    if (raw_table is None) != (raw_columns is None):
        raise UqsError(
            "--raw-table and --raw-columns go together: the physical table the new source "
            "reads, and the columns it reads from it"
        )
    raw = None
    if raw_table is not None:
        if not PHYSICAL_NAME.fullmatch(raw_table):
            raise UqsError(
                f"--raw-table {raw_table!r} must start with a letter and hold only letters, "
                "digits and underscores"
            )
        raw = (raw_table, parse_raw_columns(raw_columns or ""))
    shared = None
    if twin_of is not None:
        dataset, shape = twin_target(repo_root, twin_of, dataset, shape, definitions or {})
        shared = shared_transform(repo_root, twin_of, transform)
        if shared is not None:
            require_loaded_before_workers(repo_root, *shared)
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
    reuse_source = (repo_root / SOURCE_DIR / f"{source or name}.q").is_file()
    define_table = dataset not in (
        defined_tables(repo_root) if known_tables is None else known_tables
    )
    if twin_of is not None and reuse_source and not define_table:
        # A twin's columns were read from its job's table, not passed: with
        # its source and table both written already they shape nothing, and
        # are no reason to refuse the plan as --schema would be (#1582).
        shape = None
    if (
        not reuse_source
        and shape
        and shared is None
        and (transform or "passthrough") == "passthrough"
        and dataset in (definitions or {})
    ):
        check_passthrough(dataset, source or name, shape, (definitions or {})[dataset])
    return worker.bounded_worker(
        name,
        dataset,
        shape,
        width="1D" if width is None else width,
        source=source,
        procname=procname,
        transport=transport,
        partition=partition,
        partition_column=partition_column,
        check=check,
        transform=transform or "passthrough",
        shared=shared,
        raw=raw,
        reuse_source=reuse_source,
        define_table=define_table,
        target_columns=(
            definition_columns(definitions[dataset])
            if definitions and dataset in definitions
            else None
        ),
    )
