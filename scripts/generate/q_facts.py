"""Generate python/uqs/src/uqs/generated/q_facts.py from the q tree (#818).

uqs runs without starting q, so the q facts it needs - the conflict
strategies, the run modes, the log levels, the run ledgers' columns - used to
be hand-copied into Python, each pinned to q's SOURCE TEXT by a regex of its
own in test_q_vocabularies.py. That pinned only what someone remembered to
pin, and broke on a reformat as readily as on a real change.

This runs scripts/generate/export_q_facts.q under KDB-X, which loads the tree
and prints the values themselves, and writes them as a Python module. The
module is committed, so uqs imports it without q. `--check` regenerates it in
memory and fails when the committed file differs - the same contract as every
other derived artefact here (docs/man.q, the contract surface).

KDB-X only, like the contract surface: the ETL tree does not load on PeachQ,
and a hosted runner has no KDB-X, so the hook runs this locally.

    uv run python scripts/generate/q_facts.py           # write
    uv run python scripts/generate/q_facts.py --check   # fail if stale
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))

EXPORTER = REPO / "scripts" / "generate" / "export_q_facts.q"
OUT = REPO / "python" / "uqs" / "src" / "uqs" / "generated" / "q_facts.py"

#: What each fact is, for the module's comments - and the set the exporter
#: must print, so a fact added on one side only fails here.
FACTS = {
    "IO_STRATEGIES": "what a write may do with a row whose key exists - .qetl.io.strategies",
    "RUN_MODES": "what a bounded run may do - .qetl.job.bounded.runtime.modes",
    "LOG_LEVELS": "the levels .qetl.log writes, least to most severe - .qetl.log.levels",
    "ETL_RUNS_COLUMNS": "the run ledger's columns - cols .qetl.run.init_runs[]",
    "ETL_RUN_META_COLUMNS": "the run facts ledger's columns - cols .qetl.run.init_meta[]",
}

HEADER = '''"""q facts uqs reads, generated from the q tree - DO NOT EDIT.

Written by scripts/generate/q_facts.py, which loads src/ under KDB-X and
reads each value (#818). A hook runs it with --check, so a change to any of
these in q fails the commit until this file is regenerated.
"""
'''


def export() -> dict[str, list[str]]:
    """Run the q exporter and return its facts."""
    from uqs.interpreter import q_interpreter

    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        raise SystemExit("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
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
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        raise SystemExit(f"the q exporter failed:\n{result.stdout}\n{result.stderr}")
    facts = json.loads(lines[-1])
    if set(facts) != set(FACTS):
        raise SystemExit(f"the exporter printed {sorted(facts)}, expected {sorted(FACTS)}")
    return facts


def render(facts: dict[str, list[str]]) -> str:
    """The module, already ruff-formatted - the format hook would otherwise
    rewrite it, and --check would then call every commit's copy stale."""
    out = [HEADER]
    for name, what in FACTS.items():
        words = ", ".join(f'"{w}"' for w in facts[name])
        out.append(f"\n#: {what}\n{name}: tuple[str, ...] = ({words},)\n")
    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", str(OUT), "-"],
        input="".join(out),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return formatted.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed module is stale")
    args = ap.parse_args()
    generated = render(export())
    rel = OUT.relative_to(REPO)
    if not args.check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(generated)
        print(f"wrote {rel}")
        return 0
    committed = OUT.read_text() if OUT.is_file() else ""
    if committed == generated:
        print(f"{rel} matches the q tree")
        return 0
    print(f"{rel} is stale - rerun scripts/generate/q_facts.py:", file=sys.stderr)
    sys.stderr.writelines(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile="committed",
            tofile="generated",
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
