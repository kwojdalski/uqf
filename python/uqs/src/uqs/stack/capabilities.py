"""What an interpreter can do, asked before a worker that needs it starts.

PeachQ runs this tree's flattened q (stack/qtree.py), but not everything a
worker can ask of q. A static PeachQ build cannot load a shared library (`2:`),
so no ODBC driver; and PeachQ has neither on-disk attributes (`p#`, `s#`) nor
`.Q.chk`, which finishing an HDB partition takes (src/etl/core/io_hdb.q). A
worker started anyway fails after startup - having opened its source, and
having recorded coverage for a window whose partition it never finished. So a
backfill on a PeachQ runtime asks the interpreter first, and is refused naming
each capability missing and why the worker needs it.

Asked, never assumed: PROBE runs on the binary the runtime starts, so a build
that gains a capability gains the workers that need it with no edit here.
A KDB-X runtime is never probed.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

from uqs.interpreter import PEACHQ, q_command
from uqs.model import transports
from uqs.model.declarations import Declaration, read_declarations
from uqs.paths import SOURCE_DIR, UqsError, UqsPaths
from uqs.stack.env import with_interpreter
from uqs.stack.source_settings import declared_transport

#: Each capability, as an operator reads it.
CAPABILITIES = {
    "native": "loading a shared library (`2:`)",
    "disk_attributes": "setting `p#` and `s#` on a partition on disk",
    "chk": "`.Q.chk`, which fills a partition's missing tables",
}
#: One `name=0|1` line per capability, then DONE: q exits 0 whatever it hit,
#: so a probe that stops early is told apart from one that answered.
PROBE = """\
ok:{[f] @[{x[]; 1b};f;{0b}]};
native:not (@[{`:./uqs_no_such_library 2:(`f;1)};::;{x}]) like "static-dlopen*";
-1 "native=",string native;
@[{`:hdb/2026.01.01/t/ set .Q.en[`:hdb] ([] sym:`a`b; time:2#2026.01.01D00:00; x:1 2)};::;{}];
-1 "disk_attributes=",string ok {@[`:hdb/2026.01.01/t/;`sym;`p#]};
-1 "chk=",string ok {.Q.chk `:hdb};
-1 "DONE";
exit 0
"""
PROBE_TIMEOUT = 60.0


@functools.cache
def probe(qcmd: str) -> dict[str, bool]:
    """What `qcmd` can do, by asking it, once per binary per process."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "probe.q").write_text(PROBE)
        try:
            r = subprocess.run(
                [qcmd, "probe.q", "-q"],
                cwd=tmp,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UqsError(f"cannot ask {qcmd} what it supports: {exc}") from None
    lines = r.stdout.splitlines()
    if "DONE" not in lines:
        said = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UqsError(f"{qcmd} did not finish its capability check: {' '.join(said)}")
    found = dict(line.split("=", 1) for line in lines if "=" in line)
    return {name: found.get(name) == "1" for name in CAPABILITIES}


def _transport(repo_root: Path, source: str) -> str:
    """The transport `source` declares. Its file is usually named after it,
    but a bundle's keeps the bundle's own name: then it is the file whose
    `source_name` is `source`."""
    default = transports.default(repo_root)
    if (repo_root / SOURCE_DIR / f"{source}.q").is_file():
        return declared_transport(repo_root, source, default)
    for path in sorted((repo_root / SOURCE_DIR).glob("*.q")):
        if f"source_name:`{source}\n" in path.read_text(encoding="utf-8"):
            return declared_transport(repo_root, path.stem, default)
    return default


def requirements(repo_root: Path, decl: Declaration, mode: str | None) -> list[tuple[str, str]]:
    """(capability, why) for each thing `decl` needs in `mode`.

    validate opens nothing and plan reads only the ledgers; dry-run fetches
    and transforms but writes nothing; a run does all of it."""
    needs: list[tuple[str, str]] = []
    if mode in ("validate", "plan"):
        return needs
    transport = _transport(repo_root, decl.source)
    if transport == "odbc":
        needs.append(("native", f"its source {decl.source} is read through an ODBC driver"))
    if mode in (None, "run") and not decl.io:
        why = f"it writes {decl.dataset} into the HDB, and finishing a partition needs it"
        needs += [("disk_attributes", why), ("chk", why)]
    return needs


def refuse_unsupported(paths: UqsPaths, worker: str, mode: str | None = None) -> None:
    """Refuse to start `worker` on a PeachQ runtime that cannot run it."""
    if paths.runtime_declaration.interpreter != PEACHQ:
        return
    decl = next((d for d in read_declarations(paths.repo_root) if d.worker == worker), None)
    if decl is None:
        return  # not ours to name: the start refuses an unknown worker itself
    needs = requirements(paths.repo_root, decl, mode)
    if not needs:
        return
    qcmd = q_command(with_interpreter(paths))
    have = probe(qcmd)
    missing = [(cap, why) for cap, why in needs if not have[cap]]
    if missing:
        lines = [f"  - {CAPABILITIES[cap]}: {why}" for cap, why in missing]
        raise UqsError(
            f"{worker} cannot run on the {paths.runtime} runtime: {qcmd} lacks\n"
            + "\n".join(lines)
            + "\nNothing was started. A worker that declares its own io manager writes no "
            "partition; `--mode validate`, `plan` and `dry-run` need less "
            "(docs/guides/sidecar-bundles.md#peachq)"
        )
