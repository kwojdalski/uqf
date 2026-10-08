"""The q a release carries, converted for the server's kdb+ at build time (#861).

kdb+ 4.0 has no nested working contexts, and this tree uses them. Rather than
change the source, `uqs deploy build --q-target 4.0` runs
scripts/portable/flatten_contexts.py (#856) over the build's STAGED copy:
the checkout is never written, and only the artifact carries the converted q.

The converter is loaded from the tree being built, so a release is converted
by the converter of its own revision. A refusal fails the build, naming each
file and line; `--q-exclude` leaves a file or folder out (it ships as written,
and is still read, so what it defines still informs the rest). What was
converted is recorded in the manifest, and push's preflight refuses to put an
unconverted release on a server older than 5.0 (artifact.compatible).

Converting is not proof that the release runs on 4.0. The deployment's own
smoke test and verification, run on the server's q, are that proof.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from uqs.deploy.artifact import ReleaseError

CONVERTER = Path("scripts") / "portable" / "flatten_contexts.py"
#: The kdb+ versions a release can be converted for. 5.0 needs no conversion.
Q_TARGETS = ("4.0",)
#: How many refusals the build failure lists before saying how many more.
SHOWN = 10


def _converter(tree: Path) -> ModuleType:
    path = tree / CONVERTER
    if not path.is_file():
        raise ReleaseError("portable", f"no {CONVERTER} in the tree being built")
    spec = importlib.util.spec_from_file_location("flatten_contexts", path)
    if spec is None or spec.loader is None:
        raise ReleaseError("portable", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("flatten_contexts", module)
    spec.loader.exec_module(module)
    return module


def stage_copy(root: Path, files: Sequence[str], staged: Path) -> None:
    """Copy the files a release ships into `staged`, so converting them
    never touches the checkout."""
    for rel in files:
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, staged / rel)


def convert(
    tree: Path,
    files: Sequence[str],
    q_target: str,
    exclude: Sequence[str] = (),
    allow_computed: bool = False,
) -> dict:
    """Convert the q among `files`, in place, in the STAGED `tree`; return
    the manifest's record of it. Refuses - writing nothing - when any file
    cannot be converted with certainty."""
    if q_target not in Q_TARGETS:
        raise ReleaseError(
            "arguments", f"--q-target {q_target!r} must be one of {', '.join(Q_TARGETS)}"
        )
    fc = _converter(tree)
    qfiles = sorted(f for f in files if f.endswith(".q"))
    held = [f for f in qfiles if fc._excluded(f, exclude)]
    texts = {f: (tree / f).read_text(encoding="utf-8") for f in qfiles}
    conv = fc.Converter(q_target, allow_computed)
    for f in qfiles:
        conv.collect(f, texts[f])
    conv.propagate()
    results = [conv.convert(f, texts[f]) for f in qfiles if f not in held]
    refusals = [x for r in results for x in r.refusals]
    if refusals:
        lines = [f"  {x.path}:{x.line}:{x.col + 1} {x.code}: {x.reason}" for x in refusals]
        more = f"\n  ... and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
        raise ReleaseError(
            "portable",
            f"{len(refusals)} place(s) cannot be converted for kdb+ {q_target} with certainty:\n"
            + "\n".join(lines[:SHOWN])
            + more
            + f"\nFix them, or leave the files out with --q-exclude PATTERN. "
            f"python3 {CONVERTER} <paths> --out <dir> --dry-run --debug shows every decision.",
        )
    transformed = [r for r in results if r.action == "transformed"]
    for r in transformed:
        (tree / r.path).write_text(r.output, encoding="utf-8")
    return {
        "q": q_target,
        "transformed": [r.path for r in transformed],
        "excluded": held,
        "warnings": [w.as_dict() for r in results for w in r.warnings],
    }
