#!/usr/bin/env python3
"""Start the stack, watch it, and say whether it is actually working.

Run through `python3 scripts/test.py stack-smoke`.

NOT PART OF `all`, and not part of the GitHub workflow: it needs a kdb+
licence, free ports and a couple of minutes. That is not a reason to skip
it - the bugs it catches are precisely the ones CI cannot see, because
every one of them passed CI on the day it shipped (#298).

WHAT IT DOES NOT DO: it never calls `uqf-stack clean`. A developer's tplogs
and sample data are not this gate's to delete.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "torq_orchestrator" / "src"))

from torq_orchestrator import core  # noqa: E402
from torq_orchestrator.checks import hdb_shape, stack_smoke  # noqa: E402

#: How long to let the stack run before looking. The feeds publish on
#: sub-second timers and the slowest consumer chain is three deep, so this
#: is generous - but a smoke test that reports a false failure because it
#: was impatient is worse than one that takes another half minute.
SETTLE_SECONDS = 30


def _stack(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "uqf-stack", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def _running(paths, base_port: int) -> set[str]:
    """The processes torq.sh reports as up, by the same parse `summary` uses."""
    result = core.summary(paths, base_port=base_port)
    return {
        row["Process"]
        for row in core.summary_rows(result.stdout, {}, None)
        if row["Status"] == "up"
    }


def _row_counts(tables: set[str], port: int) -> dict[str, int]:
    """Row count per table in rdb1, a missing table counting as zero.

    One query for all of them rather than one each: a per-table query would
    take a plant-adjacent connection slot for every table, and slots are
    the scarce resource here (#285).
    """
    if not tables:
        return {}
    names = "`" + "`".join(sorted(tables))
    expr = f"{{[t] t!{{@[{{count value x}};x;0]}} each t}}[{names}]"
    try:
        answer = core.query(expr, port=port)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        print(f"  could not read row counts from rdb1: {exc}")
        return dict.fromkeys(tables, 0)
    return {str(k): int(v) for k, v in dict(answer).items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=core.DEFAULT_BASE_PORT)
    parser.add_argument("--settle", type=int, default=SETTLE_SECONDS, help="seconds to watch")
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="watch the stack that is already running instead of restarting it",
    )
    args = parser.parse_args()

    paths = core.default_paths()
    # Same place stack/logs.py reads them from, rather than a second guess at it.
    logs_dir = paths.torqdata / "logs"

    if not args.no_restart:
        print("stopping the stack ...")
        _stack("stop")
        print("starting the stack ...")
        _stack("start")
        # The plant has to be up before anything else can subscribe, and
        # torq.sh returns before that is true.
        time.sleep(5)

    running = _running(paths, args.port)
    if not running:
        print("FAILED: no process is running - nothing to smoke test")
        return 1
    print(f"{len(running)} process(es) up; watching for {args.settle}s ...")

    # The baseline is taken AFTER the start, so a start-up error the stack
    # has always had does not fail this run - what is being asserted is
    # that nothing goes wrong while it runs. A start-up error is real and
    # worth its own check; conflating the two would make this one
    # unrunnable on any machine with a history.
    before = stack_smoke.error_log_sizes(logs_dir, running)
    time.sleep(args.settle)

    expected = stack_smoke.expected_tables(running)
    counts = _row_counts(set(expected), args.port + 2)
    fresh = stack_smoke.new_error_lines(logs_dir, before, running)
    problems = stack_smoke.findings(counts, expected, fresh)

    # A third thing the running stack can be asked, because it is the one
    # that fails EVERY cross-table HDB query rather than one of them, and
    # the kdb+ error names an arbitrary table instead of the short
    # partition (#348). Cheap: a directory listing per partition.
    hdb_root = paths.torqdata / "hdb"
    short = hdb_shape.gaps(hdb_root, hdb_shape.declared_tables(paths.generated_schema.read_text()))
    if short:
        problems.append(
            stack_smoke.SmokeFinding(
                stack_smoke.FindingKind.HDB_NOT_RECTANGULAR,
                f"{len(short)} partition(s)",
                hdb_shape.describe(short).replace("\n", " "),
            )
        )

    print()
    print("================= stack smoke =================")
    for table in sorted(expected):
        print(f"  {counts.get(table, 0):>9,} rows  {table}")
    if not problems:
        print(f"\n{len(expected)} published table(s) flowing, {len(running)} process(es) quiet")
        return 0
    print(f"\n{len(problems)} problem(s):")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
