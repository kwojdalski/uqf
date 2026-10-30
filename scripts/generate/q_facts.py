"""Generate python/uqs/src/uqs/generated/q_facts.py from the q tree (#818).

uqs runs without starting q, so the q facts it needs - the conflict
strategies, the run modes, the log levels, the run ledgers' columns - used to
be hand-copied into Python, each pinned to q's SOURCE TEXT by a regex of its
own in test_q_vocabularies.py. That pinned only what someone remembered to
pin, and broke on a reformat as readily as on a real change.

This runs scripts/generate/export_q_facts.q under KDB-X, which loads the tree
and prints the values themselves, and writes them as a Python module. The
module is committed, so uqs imports it without q.

The status file's facts (its states, its keys and its name, #1362) go to a second
module too, inside uqf_airflow_provider: that package runs in Airflow's own
environment and does not depend on uqs, so it cannot import the first. `--check` regenerates it in
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
PROVIDER_OUT = (
    REPO / "python" / "uqf_airflow_provider" / "src" / "uqf_airflow_provider" / "q_facts.py"
)

#: What each fact is, for the module's comments - and the set the exporter
#: must print, so a fact added on one side only fails here.
FACTS = {
    "IO_STRATEGIES": "what a write may do with a row whose key exists - .qetl.io.strategies",
    "RUN_MODES": "what a bounded run may do - .qetl.job.bounded.runtime.modes",
    "LOG_LEVELS": "the levels .qetl.log writes, least to most severe - .qetl.log.levels",
    "ETL_RUNS_COLUMNS": "the run ledger's columns - cols .qetl.run.init_runs[]",
    "ETL_RUN_META_COLUMNS": "the run facts ledger's columns - cols .qetl.run.init_meta[]",
    "STATUS_STATES": "every state a worker's status file may record - .qetl.status.status_states",
    "STATUS_TERMINAL_STATES": "the states that end a run - .qetl.status.terminal_states",
    "STATUS_FIELDS": "every key of a worker's status file, in order - .qetl.status.fields",
    "STATUS_SUCCESS_STATES": (
        "the states a worker's process exits 0 for - .qetl.job.bounded.exit_code"
    ),
    "STATUS_FILE_PREFIX": "a status file's name before the instance id - .qetl.status.file_prefix",
    "STATUS_FILE_SUFFIX": "a status file's name after the instance id - .qetl.status.file_suffix",
    "BOUNDED_DATASET_TIME_COLUMNS": "dataset -> time column getdata reads it by (#1363)",
    "BOUNDED_DATASET_INSTRUMENT_COLUMNS": "dataset -> its instrument column, empty if none (#1363)",
    "CFG_SOURCES": "the worker-config layers, most significant first - .qetl.cfg.sources",
    "CFG_ENV_LAYER": "the layer that outranks a worker-config override - .qetl.cfg.env_layer",
    "CFG_OVERRIDE_LAYER": "the layer a worker-config override sits in - .qetl.cfg.override_layer",
    "CFG_ENV_PREFIX": "a worker-config key's environment variable prefix - .qetl.cfg.env_prefix",
    "SOURCE_CREDENTIAL_PREFIX": (
        "what a source's credential variable starts with - .qetl.source.credential_prefix"
    ),
    "FIXTURE_TAG": "what a fixture run's release ends with - .qetl.source.fixture_tag",
    "REVISION_TAG": (
        "what precedes a worker's output revision in a release - "
        ".qetl.job.bounded.runtime.revision_tag"
    ),
    "Q_TYPE_NAMES": "q's type characters (`meta`'s `t`) -> the names a cast takes (#1785)",
    "Q_TYPE_CHARS": "the names a q cast takes -> q's type characters (#1785)",
    "Q_ATTRIBUTES": "q's column attributes, as `attr` and `meta`'s `a` report them (#1785)",
}

#: Each generated module: the package that reads it, and the facts it carries.
MODULES = {
    OUT: ("uqs", list(FACTS)),
    PROVIDER_OUT: (
        "uqf_airflow_provider",
        [n for n in FACTS if n.startswith("STATUS_") or n in ("FIXTURE_TAG", "REVISION_TAG")],
    ),
}

HEADER = '''"""q facts {who} reads, generated from the q tree - DO NOT EDIT.

Written by scripts/generate/q_facts.py, which loads src/ under KDB-X and
reads each value (#818). A hook runs it with --check, so a change to any of
these in q fails the commit until this file is regenerated.
"""
'''


def export() -> dict[str, list[str] | str | dict[str, str]]:
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


def render(facts: dict[str, list[str] | str | dict[str, str]], out_path: Path) -> str:
    """The module, already ruff-formatted - the format hook would otherwise
    rewrite it, and --check would then call every commit's copy stale."""
    who, names = MODULES[out_path]
    out = [HEADER.format(who=who)]
    for name in names:
        value = facts[name]
        if isinstance(value, str):
            out.append(f"\n#: {FACTS[name]}\n{name}: str = {json.dumps(value)}\n")
            continue
        if isinstance(value, dict):
            items = ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in value.items())
            out.append(f"\n#: {FACTS[name]}\n{name}: dict[str, str] = {{{items}}}\n")
            continue
        words = ", ".join(f'"{w}"' for w in value)
        out.append(f"\n#: {FACTS[name]}\n{name}: tuple[str, ...] = ({words},)\n")
    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", str(out_path), "-"],
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
    facts = export()
    stale = 0
    for out_path in MODULES:
        generated = render(facts, out_path)
        rel = out_path.relative_to(REPO)
        if not args.check:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(generated)
            print(f"wrote {rel}")
            continue
        committed = out_path.read_text() if out_path.is_file() else ""
        if committed == generated:
            print(f"{rel} matches the q tree")
            continue
        stale = 1
        print(f"{rel} is stale - rerun scripts/generate/q_facts.py:", file=sys.stderr)
        sys.stderr.writelines(
            difflib.unified_diff(
                committed.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile="committed",
                tofile="generated",
            )
        )
    return stale


if __name__ == "__main__":
    sys.exit(main())
