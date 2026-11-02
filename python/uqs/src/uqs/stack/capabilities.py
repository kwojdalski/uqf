"""What an interpreter can do, asked before a worker that needs it starts.

PeachQ runs this tree's flattened q (stack/qtree.py), but not everything a
worker can ask of q. A static PeachQ build cannot load a shared library (`2:`),
so no ODBC driver: a worker reading through one would fail after startup,
having taken its lock and begun a run. So a backfill on a PeachQ runtime asks
the interpreter first, and is refused naming each capability missing and why
the worker needs it.

Writing the HDB is not one of them: src/etl/core/io_hdb.q writes, appends and
finishes partitions on PeachQ itself, without the on-disk attributes and
`.Q.chk` it lacks.

Asked, never assumed: PROBE runs on the binary the runtime starts, so a build
that gains a capability gains the workers that need it with no edit here.
A KDB-X runtime is never probed.

Which worker needs the capability comes from q: its source's transport is
`q_facts.SOURCE_TRANSPORTS`. PROBE's expression is the one
`.qetl.io.odbc.native_loadable` asks inside q (src/etl/core/singlestore_odbc.q),
which refuses an ODBC worker itself before it claims or locks anything; a bare
q cannot load that tree, so the expression is written here too, and
test_peachq_pipelines holds the two together.
"""

from __future__ import annotations

import functools
import subprocess
import tempfile
from pathlib import Path

from uqs.generated import q_facts
from uqs.interpreter import PEACHQ, q_command
from uqs.model import transports
from uqs.model.declarations import Declaration, read_declarations
from uqs.paths import UqsError, UqsPaths
from uqs.stack.env import with_interpreter
from uqs.stack.run_modes import READ_ONLY_MODES

#: Each capability, as an operator reads it.
CAPABILITIES = {"native": "loading a shared library (`2:`)"}
#: One `name=0|1` line per capability, then DONE: q exits 0 whatever it hit,
#: so a probe that stops early is told apart from one that answered.
PROBE = """\
native:not (@[{`:./uqs_no_such_library 2:(`f;1)};::;{x}]) like "static-dlopen*";
-1 "native=",string native;
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


def _transport(source: str, default: str) -> str:
    """The transport `source` is read through: what q resolved
    (`q_facts.SOURCE_TRANSPORTS`). A source q_facts does not list - one
    written since it was generated - reads as `default`; q itself refuses an
    ODBC one it cannot reach, before it claims or locks anything
    (`.qetl.source.preflight`)."""
    return q_facts.SOURCE_TRANSPORTS.get(source, default)


def requirements(repo_root: Path, decl: Declaration, mode: str | None) -> list[tuple[str, str]]:
    """(capability, why) for each thing `decl` needs in `mode`.

    validate opens nothing and plan reads only the ledgers; dry-run and a
    run open the source."""
    if mode in READ_ONLY_MODES:
        return []
    if _transport(decl.source, transports.default(repo_root)) == "odbc":
        return [("native", f"its source {decl.source} is read through an ODBC driver")]
    return []


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
            + "\nNothing was started. `--mode validate` and `plan` open no source; a "
            "PeachQ build that can load shared libraries (a -glibc download) runs it "
            "(docs/guides/sidecar-bundles.md#peachq)"
        )
