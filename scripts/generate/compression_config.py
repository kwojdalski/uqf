"""Render scripts/torqconfig/compressionconfig.csv from the compression policy (#2152).

TorQ's `.cmp` reads `compressionconfig.csv`; the vendored copy is a single
`default` row, and a hand-written file here would be a second copy of facts that
belong to the datasets. The policy is declared in q (src/etl/compression_policy.q,
validated by src/etl/core/compression.q), so this runs
scripts/generate/export_compression_config.q under KDB-X and returns the text q
renders. Nothing here knows an algorithm, a block size or a table: a policy the
vendored `.cmp` would refuse to load makes q fail, and so this.

Then it runs TorQ's own `.cmp.checkcsv` (the vendored lib/torq/code/common/
compress.q, read and never edited, with `.lg` stubbed) over the text, so the file
committed is one `.cmp` loads: nothing in src/ re-states what that function
accepts, and a policy it would reject at startup fails here instead.

It is derive.py's `compression-config` target:

    uv run python scripts/generate/derive.py compression-config           # write
    uv run python scripts/generate/derive.py compression-config --check   # fail if stale

KDB-X only, like q-facts: the ETL tree does not load on PeachQ.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))

EXPORTER = REPO / "scripts" / "generate" / "export_compression_config.q"
#: TorQ finds this before its own lib/torq/config copy: uqs points
#: KDBSERVCONFIG at scripts/torqconfig for every process it starts.
OUT = REPO / "scripts" / "torqconfig" / "compressionconfig.csv"


#: The vendored engine, read only: its loader and checks are the authority on
#: what a compressionconfig.csv may hold.
CMP = REPO / "lib" / "torq" / "code" / "common" / "compress.q"

#: Loads compress.q with just enough of TorQ stubbed, then loadcsv - which runs
#: checkcsv - over the file named by the file under test. `.lg.e` is what checkcsv reports
#: through; here it prints its message and exits non-zero.
_CHECK_Q = """
.lg.o:{[a;b] ::};
.lg.e:{[a;b] -2 b; exit 1};
.proc.getconfigfile:{enlist `:/nonexistent};
\\l %(cmp)s
.cmp.inputcsv:"%(csv)s";
.cmp.loadcsv[hsym `$"%(csv)s"];
-1 "rows ",string count .cmp.compressioncsv;
exit 0
"""


def _interpreter() -> tuple[Path, dict[str, str]]:
    from uqs.interpreter import q_interpreter

    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        raise SystemExit("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return q, env


def torq_check(text: str) -> int:
    """Run TorQ's `.cmp.checkcsv` over `text`; the number of rows it loaded.

    Exits with `.cmp`'s own message when it would refuse the file at startup.
    """
    q, env = _interpreter()
    with tempfile.TemporaryDirectory() as tmp:
        csv = Path(tmp) / "compressionconfig.csv"
        csv.write_text(text)
        result = subprocess.run(
            [str(q), "-q"],
            input=_CHECK_Q % {"cmp": CMP, "csv": csv},
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    if result.returncode != 0 or "rows " not in result.stdout:
        raise SystemExit(
            f"TorQ's .cmp would refuse this compressionconfig.csv:\n{result.stdout}{result.stderr}"
        )
    return int(result.stdout.split("rows ")[-1].split()[0])


def render() -> str:
    """The CSV text q renders from the declared policy, checked by TorQ's `.cmp`."""
    q, env = _interpreter()
    result = subprocess.run(
        [str(q), str(EXPORTER.relative_to(REPO)), "-q"],
        cwd=REPO,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(f"the q exporter failed:\n{result.stdout}\n{result.stderr}")
    text = result.stdout.strip() + "\n"
    torq_check(text)
    return text
