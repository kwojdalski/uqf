"""Split the Python suite across CI runners by test module (#1696).

The PeachQ Python job is CPU-bound on its 4-vCPU runner: more xdist workers
measured no faster (#1695). So the suite is split across several runners, a
whole module to one shard. A module's fixtures are then built once, in one
place, and its xdist groups stay together.

Modules are balanced by their recorded duration (MODULE_DURATIONS, written by
`--store-shard-durations`), largest first onto the least loaded shard. A
module with no record weighs the median, so a new or renamed module only
unbalances the shards: every collected module lands in exactly one shard,
whatever the record says.

    uv run pytest -n auto --dist loadgroup --shard 2/3
    # refresh the record, on a CI-like run (PeachQ, the stack tests on):
    uv run pytest -n auto --dist loadgroup \
        --store-shard-durations scripts/dev/test_module_durations.json
    # or from CI's own times: each shard uploads durations-N.json
    gh run download <run-id> --pattern 'durations-*' --dir /tmp/d
    python3 scripts/dev/pytest_shards.py --merge /tmp/d/*/durations-*.json
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

MODULE_DURATIONS = Path(__file__).with_name("test_module_durations.json")


def parse_shard(spec: str) -> tuple[int, int]:
    """`I/N` as (I, N), 1 <= I <= N; anything else is refused."""
    try:
        index, count = (int(part) for part in spec.split("/"))
    except ValueError:
        raise ValueError(f"--shard {spec!r} must be I/N, e.g. 2/3") from None
    if not 1 <= index <= count:
        raise ValueError(f"--shard {spec!r}: I must be between 1 and N")
    return index, count


def assign(modules: list[str], weights: dict[str, float], count: int) -> list[list[str]]:
    """`modules` split into `count` shards of near-equal recorded weight.

    Deterministic: the heaviest module first, ties broken by name, each onto
    the least loaded shard (the lowest-numbered on a tie). A module with no
    weight gets the median of the known ones (1.0 when none is known)."""
    known = [weights[m] for m in modules if m in weights]
    default = statistics.median(known) if known else 1.0
    load = [0.0] * count
    shards: list[list[str]] = [[] for _ in range(count)]
    for module in sorted(set(modules), key=lambda m: (-weights.get(m, default), m)):
        target = min(range(count), key=lambda i: (load[i], i))
        shards[target].append(module)
        load[target] += weights.get(module, default)
    return [sorted(shard) for shard in shards]


def module_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def load_weights(path: Path = MODULE_DURATIONS) -> dict[str, float]:
    return json.loads(path.read_text()) if path.is_file() else {}


class Recorder:
    """Seconds per module, summed over each test's setup, call and teardown."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seconds: dict[str, float] = defaultdict(float)

    def pytest_runtest_logreport(self, report) -> None:
        self.seconds[module_of(report.nodeid)] += report.duration

    def pytest_sessionfinish(self, session) -> None:
        if hasattr(session.config, "workerinput"):
            return  # an xdist worker: the controller sees every report
        data = {m: round(s, 2) for m, s in sorted(self.seconds.items())}
        self.path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def merge(paths: list[Path], out: Path = MODULE_DURATIONS) -> dict[str, float]:
    """One record from the shards' own: each holds only its modules, so a
    module appears in exactly one of them."""
    data: dict[str, float] = {}
    for path in paths:
        data.update(json.loads(path.read_text()))
    out.write_text(json.dumps(dict(sorted(data.items())), indent=1) + "\n")
    return data


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3 or sys.argv[1] != "--merge":
        raise SystemExit("usage: pytest_shards.py --merge durations-1.json ...")
    merged = merge([Path(a) for a in sys.argv[2:]])
    print(f"{MODULE_DURATIONS.name}: {len(merged)} modules, {round(sum(merged.values()))}s")
