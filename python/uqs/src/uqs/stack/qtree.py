"""The flattened q tree a `q_tree="flattened"` runtime's processes load.

PeachQ cannot load a nested `\\d` context (peachq-org/peachq#80), and the ETL
tree is built of them. scripts/portable/flatten_contexts.py (#856) rewrites q
so it needs none, and deploy/portable.py already runs it over a release's
staged copy. This runs the same converter for a local runtime: over a COPY of
`src/` and `scripts/` - installed bundle jobs included, since `uqs runtime
prepare` puts them under `src/etl/` - written to `<data dir>/qtree`, which
UQF_ROOT, UQF_SCRIPTS and the service layers then name (stack/env.py). The
checkout and the vendored TorQ trees are never written.

A tree is published only once it is proven:

  - every q file converts with certainty, or nothing is written and the
    refusals are named, file and line;
  - the copy loads `src/init.q` and `src/etl/init.q` on the runtime's own
    interpreter and prints SENTINEL. q exits 0 whatever happened while it
    loaded, so the sentinel is the proof, never the exit code;
  - it is built beside the published tree and swapped in by rename, so a
    failed preparation leaves the previous tree - and the processes that
    loaded it - exactly as they were.

What was converted is in REPORT beside the tree; a refusal is in FAILED. An
unchanged source (FINGERPRINT over every copied file and the converter) reuses
the published tree, so a `stop` or `summary` does not convert again.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths

log = get_logger(__name__)

CONVERTER = Path("scripts") / "portable" / "flatten_contexts.py"
#: What the tree copies from the checkout; every other top-level entry is
#: linked, so a path a process builds from UQF_ROOT still finds it.
COPIED = ("src", "scripts")
#: Never copied, and never linked: Python tests, caches, and the runtimes'
#: own output, which holds this tree.
SKIPPED_DIRS = frozenset({"__pycache__", ".pytest_cache", "tests"})
UNLINKED = frozenset({*COPIED, "output", ".git", ".venv", "node_modules", ".claude"})
#: What kdb+ version the converter flattens for: 4.0's rule, no nested
#: contexts, is PeachQ's.
Q_TARGET = "4.0"
TREE = "qtree"
REPORT = "qtree.json"
FAILED = "qtree-failed.json"
SENTINEL = "UQS_QTREE_LOADED"
#: How many refusals an error lists before saying how many more.
SHOWN = 10
#: Loads what every pipeline process loads, then says so. Unprotected, so a
#: failure stops it before the sentinel with the interpreter's own report of
#: where - the file and line - which is what an operator needs to see.
PROBE = f'system"l src/init.q";\nsystem"l src/etl/init.q";\n-1 "{SENTINEL}";\nexit 0\n'
PROBE_TIMEOUT = 300.0
#: The schema the probe loads against (see probe).
VENDORED_SCHEMA = Path("lib") / "torq-finance-starter-pack" / "database.q"


def is_flattened(paths: UqsPaths) -> bool:
    return paths.runtime_declaration.q_tree == "flattened"


def tree_dir(paths: UqsPaths) -> Path:
    return paths.torqdata / TREE


def code_root(paths: UqsPaths) -> Path:
    """What UQF_ROOT names for this runtime: the checkout, or its tree."""
    return tree_dir(paths) if is_flattened(paths) else paths.repo_root


def converter(root: Path) -> ModuleType:
    """The converter of the tree being prepared, loaded by path."""
    path = root / CONVERTER
    if not path.is_file():
        raise UqsError(f"no {CONVERTER} in {root} - it converts the q tree for PeachQ")
    spec = importlib.util.spec_from_file_location("flatten_contexts", path)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout
        raise UqsError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("flatten_contexts", module)
    # No __pycache__ beside it: preparing a tree never writes the checkout.
    written, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = written
    return module


def sources(root: Path) -> dict[str, Path]:
    """Every file the tree copies, by its path relative to `root`."""
    found: dict[str, Path] = {}
    for top in COPIED:
        for path in sorted((root / top).rglob("*")):
            rel = path.relative_to(root)
            if path.is_file() and not SKIPPED_DIRS & set(rel.parts) and path.suffix != ".pyc":
                found[rel.as_posix()] = path
    return found


def fingerprint(root: Path, files: Mapping[str, Path]) -> str:
    digest = hashlib.sha256()
    for rel in sorted({*files, CONVERTER.as_posix()}):
        digest.update(rel.encode() + b"\0" + (root / rel).read_bytes() + b"\0")
    return digest.hexdigest()


def convert(root: Path, texts: Mapping[str, str]) -> dict[str, str]:
    """Each q file of `texts` (relative path -> source) as the tree holds it.

    Every file is read before any is converted, so a definition in one informs
    the others. Refused - writing nothing - when any place cannot be converted
    with certainty, naming each."""
    fc = converter(root)
    conv = fc.Converter(Q_TARGET, False)
    qfiles = sorted(f for f in texts if f.endswith(".q"))
    for f in qfiles:
        conv.collect(f, texts[f])
    conv.propagate()
    results = [conv.convert(f, texts[f]) for f in qfiles]
    refusals = [x for r in results for x in r.refusals]
    if refusals:
        lines = [f"  {x.path}:{x.line}:{x.col + 1} {x.code}: {x.reason}" for x in refusals]
        more = f"\n  ... and {len(lines) - SHOWN} more" if len(lines) > SHOWN else ""
        raise UqsError(
            f"{len(refusals)} place(s) in the q tree cannot be converted for PeachQ with "
            "certainty, so no tree was prepared:\n" + "\n".join(lines[:SHOWN]) + more
        )
    return {r.path: (r.output if r.action == "transformed" else texts[r.path]) for r in results}


def check(root: Path, extra: Mapping[str, str] | None = None) -> dict[str, int]:
    """Convert the tree, plus `extra` (relative path -> q) placed in it, in
    memory - for a dry run, which writes nothing. Raises as convert does."""
    texts = {
        rel: p.read_text(encoding="utf-8") for rel, p in sources(root).items() if rel.endswith(".q")
    }
    texts.update(extra or {})
    out = convert(root, texts)
    return {"q_files": len(out), "transformed": sum(out[f] != texts[f] for f in out)}


def probe(tree: Path, qcmd: str, env: Mapping[str, str], timeout: float = PROBE_TIMEOUT) -> None:
    """Load the tree as a pipeline process does, on `qcmd`; refuse unless it
    prints SENTINEL. Run from the tree, as every loader here is.

    Against the vendored starter pack's schema when the tree has it, not
    $TORQAPPHOME's: the probe proves the CONVERSION, which loads every
    declaration, and a managed schema without the demo's tables cannot load
    them all by design. Each process checks its own jobs against the
    deployment's schema when it starts (src/etl/core/declaration_load.q, #902)."""
    script = tree / ".uqs_qtree_probe.q"
    script.write_text(PROBE)
    if (tree / VENDORED_SCHEMA).is_file():
        env = {k: v for k, v in env.items() if k != "TORQAPPHOME"}
    try:
        r = subprocess.run(
            [qcmd, script.name, "-q"],
            cwd=tree,
            stdin=subprocess.DEVNULL,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(f"the converted q tree did not load on {qcmd} within {timeout:g}s") from None
    except OSError as exc:
        raise UqsError(f"cannot run {qcmd} to check the converted q tree: {exc}") from None
    finally:
        script.unlink(missing_ok=True)
    if SENTINEL not in r.stdout.splitlines():
        said = "\n".join((r.stderr or r.stdout).strip().splitlines()[-8:]) or "(nothing)"
        raise UqsError(
            f"the converted q tree does not load on {qcmd} (exit {r.returncode}, and no "
            f"{SENTINEL}):\n{said}"
        )


def _write(
    staging: Path, root: Path, files: Mapping[str, Path], converted: Mapping[str, str]
) -> None:
    for rel, src in files.items():
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel in converted:
            dest.write_text(converted[rel], encoding="utf-8")
        else:
            shutil.copy2(src, dest)
    for entry in sorted(root.iterdir()):
        if entry.name not in UNLINKED:
            (staging / entry.name).symlink_to(entry)


def _published(paths: UqsPaths, fp: str) -> bool:
    report = paths.torqdata / REPORT
    if not tree_dir(paths).is_dir() or not report.is_file():
        return False
    try:
        return json.loads(report.read_text()).get("fingerprint") == fp
    except ValueError:
        return False


def prepare(paths: UqsPaths, qcmd: str, env: Mapping[str, str]) -> Path:
    """The runtime's tree, converted, proven on `qcmd` and published - or the
    published one, when nothing it is built from has changed."""
    root = paths.repo_root
    files = sources(root)
    fp = fingerprint(root, files)
    tree = tree_dir(paths)
    if _published(paths, fp):
        return tree
    paths.torqdata.mkdir(parents=True, exist_ok=True)
    failed = paths.torqdata / FAILED
    staging = paths.torqdata / f"{TREE}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    log.info("converting the q tree for {} into {}", paths.runtime, tree)
    try:
        texts = {
            rel: p.read_text(encoding="utf-8") for rel, p in files.items() if rel.endswith(".q")
        }
        converted = convert(root, texts)
        _write(staging, root, files, converted)
        probe(staging, qcmd, env)
    except UqsError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        failed.write_text(json.dumps({"fingerprint": fp, "error": str(exc)}, indent=2) + "\n")
        raise
    old = paths.torqdata / f"{TREE}.old"
    shutil.rmtree(old, ignore_errors=True)
    if tree.exists():
        tree.rename(old)
    staging.rename(tree)
    shutil.rmtree(old, ignore_errors=True)
    report = {
        "fingerprint": fp,
        "q_target": Q_TARGET,
        "interpreter": qcmd,
        "files": len(files),
        "q_files": len(converted),
        "transformed": sorted(f for f in converted if converted[f] != texts[f]),
        "loaded": SENTINEL,
    }
    (paths.torqdata / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    failed.unlink(missing_ok=True)
    log.info("q tree ready: {} of {} q files converted", len(report["transformed"]), len(converted))
    return tree
