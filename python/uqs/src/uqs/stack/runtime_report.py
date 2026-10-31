"""What each runtime is, read off its declaration: `uqs runtime list` and
`uqs runtime diff` (#763).

Every fact here is computed from what a runtime composes - its process.csv
rows, its schema, its environment - rather than restated, so the listing and
the diff cannot drift from what `uqs --runtime X start` would start.
"""

from __future__ import annotations

from collections.abc import Collection
from functools import partial
from pathlib import Path

from uqs.paths import UqsError, UqsPaths, paths_for_root, runtime_from_env
from uqs.runtimes import DEFAULT_RUNTIME, RUNTIMES
from uqs.stack import alive
from uqs.stack.env import build_env
from uqs.stack.hdb_shape import declared_tables
from uqs.stack.procs import effective_process_rows

#: The configuration layers TorQ loads, by the variable that adds each.
_LAYERS = {
    "KDBCONFIG": "TorQ",
    "KDBSERVCONFIG": "uqf service layer",
    "KDBAPPCONFIG": "starter pack",
}


def processes(paths: UqsPaths) -> list[str]:
    """The processes the runtime's process.csv declares."""
    return [row["procname"] for row in effective_process_rows(paths)]


def tables(paths: UqsPaths) -> list[str]:
    """The tables the runtime's schema defines: the one stp1 is given."""
    return sorted(declared_tables(paths))


def layers(paths: UqsPaths) -> list[str]:
    """The config layers its processes load, in TorQ's load order."""
    env = build_env(paths)
    return [label for var, label in _LAYERS.items() if env.get(var)]


def commands(paths: UqsPaths, pipelines_only: Collection[str] = ()) -> list[str]:
    """The commands only some runtimes run, that this one does: of
    `pipelines_only` - the commands marked as needing this tree's pipelines,
    which the CLI reads off its own tree (cli.pipelines.pipelines_only) - all,
    on a runtime with them, else none."""
    return sorted(pipelines_only) if paths.runtime_declaration.pipelines else []


def list_runtimes(paths: UqsPaths, base_port: int | None = None) -> list[dict[str, str]]:
    """One row per declared runtime: what it is, and whether it is here and up.

    `base_port` is ignored: each runtime is reported on its own declared
    ports, since that is where its stack runs unless moved by hand.
    """
    selected = runtime_from_env()
    rows = []
    for name, runtime in RUNTIMES.items():
        p = paths_for_root(paths.repo_root, name)
        procs = processes(p)
        up = alive.running(p) & set(procs)
        rows.append(
            {
                "runtime": name,
                "default": "yes" if name == DEFAULT_RUNTIME else "",
                "selected": "yes" if name == selected else "",
                "description": runtime.description,
                "processes": str(len(procs)),
                "tables": str(len(tables(p))),
                "layers": ", ".join(layers(p)),
                "data_dir": f"output/{runtime.data_dir}",
                "data": "yes" if p.torqdata.is_dir() else "no",
                "base_port": str(runtime.base_port),
                "running": f"{len(up)}/{len(procs)}",
            }
        )
    return rows


#: What `runtime diff` compares, by section, and how each is read.
DIFF_SECTIONS = {
    "processes": processes,
    "tables": tables,
    "layers": layers,
    "commands": commands,
}


def diff(
    root: Path, a: str, b: str, pipelines_only: Collection[str] = ()
) -> dict[str, dict[str, list[str]]]:
    """For each section, what `a` has that `b` does not, and the reverse.
    `pipelines_only` is the commands section's input, as `commands` takes it."""
    for name in (a, b):
        if name not in RUNTIMES:
            raise UqsError(f"{name!r} is not a runtime - choose from {', '.join(RUNTIMES)}")
    pa, pb = paths_for_root(root, a), paths_for_root(root, b)
    out: dict[str, dict[str, list[str]]] = {}
    readers = {**DIFF_SECTIONS, "commands": partial(commands, pipelines_only=pipelines_only)}
    for section, read in readers.items():
        left, right = read(pa), read(pb)
        out[section] = {
            f"only_{a}": [x for x in left if x not in right],
            f"only_{b}": [x for x in right if x not in left],
        }
    return out
