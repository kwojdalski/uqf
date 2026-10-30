#!/usr/bin/env python3
"""Generate this repository's derived artifacts, or check that they are current (#1804).

    uv run python scripts/generate/derive.py TARGET            # write
    uv run python scripts/generate/derive.py TARGET --check    # exit 1 if stale; write nothing
    uv run python scripts/generate/derive.py --help            # the targets and their options

A derived artifact is a committed file made from an authoritative input -
getdata's table list from the plant tables, the process table from the
pipeline registry. Each used to have a script of its own, with its own
arguments, its own way of comparing and its own way of writing. This is the
one command for all of them. The computations stay where they were: each
target here is a few lines that ask a domain module for its outputs and hand
them to the lifecycle below.

TARGETS IS A LIST WRITTEN BY HAND. No plugins, no discovery, no `all`: a
generator joins by being added to TARGETS, and a target runs only when it is
named. Each target imports what it needs inside its own function, so naming
one never imports, let alone runs, another's generator - `diagrams` runs
under a bare python3 with no workspace at all.

THE LIFECYCLE, where the targets genuinely share it:

  - every output is rendered before any is written, so a generator that fails
    half way writes nothing and exits non-zero;
  - `--check` compares and never writes - neither the outputs nor anything
    they are made from;
  - writing goes through a temporary file and an atomic rename, keeps the
    file's mode, and leaves a file whose bytes are already right untouched;
  - exit 0 when current or written, 1 when stale or the generator refused,
    2 for a usage error.

Two targets keep a check of their own, because their outputs are compared by
something other than their bytes: `contract-surface` reports the CONTRACT
difference ("is_covered gained an argument"), and `plant-schema` regenerates
only when the fingerprint it records says it is stale. Comparing two
arbitrary contract surfaces is an audit, not a generation step, and stays
`contract_surface.py diff A B`.

A repository build tool, not part of `uqs`: it runs over a checkout (or a
staged release tree) and needs no runtime configuration, connectivity or
credentials. Interpreter requirements are each target's own and unchanged -
q-facts and contract-surface need KDB-X, plant-schema KDB-X or PeachQ,
diagrams the pinned d2.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
#: How this script is named in every message: relative, as a contributor runs it.
PROG = "scripts/generate/derive.py"


# ------------------------------------------------------------------ loading


def load(name: str) -> ModuleType:
    """The domain module `name`.py beside this script, loaded by path.

    scripts/ is not a package, so a sibling is loaded from its file - and
    registered under its own name, so a test that monkeypatches the module
    patches the one a target uses.
    """
    path = HERE / f"{name}.py"
    loaded = sys.modules.get(name)
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve() == path:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _uqs() -> None:
    """Make `uqs` importable from this checkout, as each script did before:
    `uv run` installs it, a bare python3 finds it here."""
    src = str(REPO / "python" / "uqs" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


# ---------------------------------------------------------------- lifecycle


@dataclass(frozen=True)
class Output:
    """One file a target derives, rendered in full before anything is written."""

    path: Path
    content: bytes
    #: For a rendering whose line diff would be unreadable (an SVG): the
    #: source it was rendered from, which a stale report names instead.
    source: Path | None = None


def text(path: Path, content: str) -> Output:
    return Output(path, content.encode("utf-8"))


def rel(path: Path) -> Path:
    try:
        return path.relative_to(REPO)
    except ValueError:
        return path


def compare(outputs: Sequence[Output], hint: str) -> int:
    """Report each output as current, missing or stale; 1 if any is not current.

    Reads only. `hint` is what to run to bring a stale output up to date.
    """
    stale = 0
    for out in outputs:
        name = rel(out.path)
        if not out.path.is_file():
            print(f"{name} is missing - {hint}", file=sys.stderr)
            stale += 1
            continue
        committed = out.path.read_bytes()
        if committed == out.content:
            print(f"{name} is current")
            continue
        stale += 1
        if out.source is not None:
            print(
                f"{name} is stale - {rel(out.source)} has changed since it was rendered; {hint}",
                file=sys.stderr,
            )
            continue
        print(f"{name} is stale - {hint}:", file=sys.stderr)
        sys.stderr.writelines(
            difflib.unified_diff(
                committed.decode("utf-8", errors="replace").splitlines(keepends=True),
                out.content.decode("utf-8", errors="replace").splitlines(keepends=True),
                fromfile="committed",
                tofile="generated",
            )
        )
    return 1 if stale else 0


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def replace(path: Path, content: bytes) -> None:
    """Write `content` to `path` through a temporary file and an atomic rename.

    A reader sees the old file or the new one, never half of either. The mode
    is the old file's (mkstemp's 0600 would otherwise replace it), or what the
    umask gives a new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o666 & ~_umask()
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write(outputs: Sequence[Output]) -> int:
    """Write every output whose bytes differ from the file's; 0 when done.

    Called only once every output is rendered. A write that fails raises, so
    a partly written target never reports success.
    """
    for out in outputs:
        if out.path.is_file() and out.path.read_bytes() == out.content:
            print(f"{rel(out.path)} is unchanged")
            continue
        replace(out.path, out.content)
        print(f"wrote {rel(out.path)}")
    return 0


def settle(outputs: Sequence[Output], *, check: bool, hint: str) -> int:
    """`--check` compares; otherwise writes. Refuses an empty set, which could
    only mean the generator found nothing to derive from."""
    if not outputs:
        print("error: the generator produced no outputs", file=sys.stderr)
        return 1
    return compare(outputs, hint) if check else write(outputs)


# ------------------------------------------------------------------ targets


@dataclass(frozen=True)
class Target:
    name: str
    help: str
    run: Callable[[argparse.Namespace], int]
    #: Adds the target's own options to its subparser.
    options: Callable[[argparse.ArgumentParser], None] = field(default=lambda _parser: None)
    #: The interpreter a contributor runs it with: bare python3 when it needs
    #: nothing beyond the standard library.
    python: str = "uv run python"

    def command(self, *args: str) -> str:
        return " ".join((self.python, PROG, self.name, *args))


def _hint(name: str) -> str:
    return f"run `{TARGETS[name].command()}`"


def _table_properties(args: argparse.Namespace) -> int:
    """scripts/torqconfig/dataaccess/tableproperties.csv (#1363): a getdata row
    for every plant table and every bounded worker's dataset, so nothing the
    catalog offers is refused by TorQ's checktablename. Needs no q - the
    workers' datasets are in the committed q_facts.py."""
    _uqs()
    from uqs.stack.gateway_access import TABLE_PROPERTIES, table_properties_rows

    rows = text(REPO / "scripts" / TABLE_PROPERTIES, "\n".join(table_properties_rows()) + "\n")
    return settle([rows], check=args.check, hint=_hint("table-properties"))


def _diagrams_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--require",
        action="store_true",
        help="fail, rather than skip, when d2 is absent or is not D2_VERSION (CI)",
    )
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="print D2_VERSION and exit (the CI workflow installs that release)",
    )


def _diagrams(args: argparse.Namespace) -> int:
    """docs/diagrams/*.svg, rendered from their .d2 by the pinned d2 - and
    skipped, saying so, under any other d2 unless --require (render_diagrams)."""
    diagrams = load("render_diagrams")
    if args.print_version:
        print(diagrams.D2_VERSION)
        return 0
    sources = diagrams.sources()
    if not sources:
        # Refusing to report success over an empty set: a moved directory
        # must fail loudly rather than quietly gate nothing.
        print(f"error: no .d2 sources under {rel(diagrams.DIAGRAMS)}", file=sys.stderr)
        return 1
    stop = diagrams.verdict(len(sources), require=args.require)
    if stop is not None:
        return stop
    svgs = [Output(s.with_suffix(".svg"), diagrams.rendered(s), source=s) for s in sources]
    code = settle(svgs, check=args.check, hint=_hint("diagrams"))
    if args.check and code == 0:
        print(f"diagrams: {len(svgs)} diagram(s) match their sources (d2 {diagrams.D2_VERSION})")
    return code


def _operational_docs(args: argparse.Namespace) -> int:
    """The process table, the pipeline DAG and load plan q reads, the port
    lock and the uqs guide's profile table - all from the pipeline registry
    (generate_operational_docs). It refuses, naming them, when the declared
    dataflow edges disagree with the q scripts."""
    _uqs()
    docs = load("generate_operational_docs")
    files = [text(path, content) for path, content in docs.outputs()]
    return settle(files, check=args.check, hint=_hint("operational-docs"))


#: Every target, by name. Added to by hand: there is no discovery.
TARGETS: dict[str, Target] = {
    t.name: t
    for t in (
        Target(
            "table-properties",
            "getdata's table list, from the plant tables and the workers' datasets",
            _table_properties,
        ),
        Target(
            "diagrams",
            "docs/diagrams/*.svg, rendered from their .d2 sources by the pinned d2",
            _diagrams,
            _diagrams_options,
            python="python3",
        ),
        Target(
            "operational-docs",
            "processes.md, the pipeline DAG, the load plan, the port lock and the "
            "uqs guide's profile table, from the pipeline registry",
            _operational_docs,
        ),
    )
}


# --------------------------------------------------------------------- main


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="target", required=True, metavar="TARGET")
    for target in TARGETS.values():
        one = sub.add_parser(target.name, help=target.help, description=target.help)
        one.add_argument(
            "--check",
            action="store_true",
            help="exit 1 if an output is stale or missing; write nothing",
        )
        target.options(one)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return TARGETS[args.target].run(args)


if __name__ == "__main__":
    sys.exit(main())
