"""Which job an attachment is for, the process that runs it, and the tables it
needs the stack to have.

Two sources, one answer:

  * a sidecar BUNDLE folder (`--bundle`): the job's `.q` declaration and the
    bundle's `tables.q`, read as text. Nothing is installed in this checkout -
    the point of attaching is that the stack is somewhere else.
  * the checkout's own tree (no `--bundle`): the job as already installed or
    shipped here, its tables from `src/etl/plant_tables.q`.

The tables an attachment registers are the job's `publishes` that the job's
source DEFINES. A table the job publishes that nothing here defines (the
vendored `quote`, say) is not ours to create; the plan checks it already
exists on the stack instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from uqs.attach.config import AttachmentError
from uqs.attach.schema import SchemaError, TableSpec, parse_definition
from uqs.model.declarations import Declaration, read_file
from uqs.paths import UqsError
from uqs.stack import bundles

_TABLE_LINE = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]")


@dataclass(frozen=True)
class JobSpec:
    job: str
    procname: str
    #: tables the job publishes and its source defines, in publish order
    tables: tuple[TableSpec, ...]
    #: tables the job publishes that must already exist on the stack
    external: tuple[str, ...]
    #: {"bundle", "version", "folder"} for a bundle, {} for this checkout's tree
    origin: dict


def _definitions(text: str) -> dict[str, str]:
    return {m.group(1): line for line in text.splitlines() if (m := _TABLE_LINE.match(line))}


def _spec(table: str, line: str, where: str) -> TableSpec:
    try:
        return parse_definition(line)
    except SchemaError as exc:
        raise AttachmentError(f"{where}: table {table}: {exc}") from None


def _pick(declarations: list[Declaration], job: str, where: str) -> Declaration:
    found = [d for d in declarations if d.name == job]
    if not found:
        streaming = sorted(d.name for d in declarations if d.publishes)
        raise AttachmentError(
            f"{where} declares no streaming job {job!r}"
            + (f" - it has {', '.join(streaming)}" if streaming else "")
        )
    return found[0]


def from_bundle(job: str, folder: Path) -> JobSpec:
    """`job` out of the bundle at `folder`."""
    try:
        bundle = bundles.read_bundle(folder)
    except UqsError as exc:
        raise AttachmentError(str(exc)) from None
    declarations: list[Declaration] = []
    for path in sorted(folder.glob("*.q")):
        if path.name in bundles.ADDITIONS or path.name.startswith("test_"):
            continue
        declarations += read_file(path)
    decl = _pick(declarations, job, f"bundle {bundle.name}")
    if not decl.publishes:
        raise AttachmentError(f"{job} publishes nothing, so there is nothing to attach")
    tables_q = folder / bundles.TABLES
    defined = _definitions(tables_q.read_text()) if tables_q.is_file() else {}
    return _build(decl, defined, f"bundle {bundle.name}/{bundles.TABLES}", bundle)


def from_tree(job: str, root: Path) -> JobSpec:
    """`job` as this checkout's tree declares it."""
    from uqs.model.declarations import read_declarations
    from uqs.paths import TABLES_FILE

    decl = _pick(read_declarations(root), job, "this tree")
    if not decl.publishes:
        raise AttachmentError(f"{job} publishes nothing, so there is nothing to attach")
    defined = _definitions((root / TABLES_FILE).read_text())
    return _build(decl, defined, str(TABLES_FILE), None)


def _build(decl: Declaration, defined: dict[str, str], where: str, bundle) -> JobSpec:
    tables = tuple(_spec(t, defined[t], where) for t in decl.publishes if t in defined)
    external = tuple(t for t in decl.publishes if t not in defined)
    origin = (
        {"bundle": bundle.name, "version": bundle.version, "folder": str(bundle.root)}
        if bundle
        else {}
    )
    return JobSpec(decl.name, decl.procname, tables, external, origin)


def resolve(job: str, bundle: Path | None, root: Path) -> JobSpec:
    return from_bundle(job, bundle) if bundle is not None else from_tree(job, root)
