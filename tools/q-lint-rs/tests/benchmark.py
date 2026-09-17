"""Reproducible wall-clock comparison; build release binaries before running."""

import argparse
import cProfile
import hashlib
import json
import platform
import pstats
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
# The Python q-lint this crate was ported from, which is NOT vendored here -
# this script exists to compare the two implementations, so it only runs in a
# checkout that has both. `ty` cannot resolve it for that reason rather than
# because anything is wrong, and the import is deliberately left as-is so the
# comparison still works where the package is present.
sys.path.insert(0, str(ROOT / "tools/q-lint/src"))
from q_lint import lint  # noqa: E402  # ty: ignore[unresolved-import]
from q_lint.cli import discover  # noqa: E402  # ty: ignore[unresolved-import]

BINARY = ROOT / "tools/q-lint-rs/target/release/qlinter"
CORE = BINARY.parent / "examples/profile"


def stats(values):
    return {
        "median_ms": round(statistics.median(values) * 1000, 3),
        "min_ms": round(min(values) * 1000, 3),
        "max_ms": round(max(values) * 1000, 3),
        "samples_seconds": values,
    }


def identity(output):
    return sorted(
        (f["path"], f["line"], f["column"] or 0, f["rule"], f["code"], f["severity"])
        for f in json.loads(output)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--qls", action="store_true", help="Also time installed qls on one file")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.runs > 0
    datasets = {
        "single": ["src/foundation/stats.q"],
        "quant": [
            "src/foundation",
            "src/pricing",
            "src/portfolio",
            "src/execution",
            "src/market_data",
        ],
        "source-and-tests": ["src", "tests/q"],
    }
    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
        "runs": args.runs,
        "method": (
            "One untimed warm-up per command; alternating fresh processes; warm filesystem cache; "
            "JSON output captured; release build; no uv/cargo launch overhead"
        ),
        "results": {},
    }
    for name, paths in datasets.items():
        files = discover([str(ROOT / p) for p in paths])
        sources = [(str(p), p.read_text()) for p in files]
        row: dict[str, Any] = {
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "hashes": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
            },
        }
        for backend in ["builtin", "qls"] if args.qls and name == "single" else ["builtin"]:
            commands = {"python": [sys.executable, "-m", "q_lint"], "rust": [str(BINARY)]}
            samples = {impl: [] for impl in commands}
            outputs = {}
            for i in range(args.runs + 1):
                order = list(commands) if i % 2 else list(reversed(commands))
                for impl in order:
                    start = time.perf_counter()
                    run = subprocess.run(
                        [*commands[impl], *paths, "--format", "json", "--backend", backend],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    elapsed = time.perf_counter() - start
                    assert run.returncode in (0, 1), run.stderr
                    outputs[impl] = identity(run.stdout)
                    if i:
                        samples[impl].append(elapsed)
                assert outputs["python"] == outputs["rust"], f"{name}/{backend}: finding mismatch"
            row[backend] = {impl: stats(times) for impl, times in samples.items()}
            row[backend]["speedup"] = round(
                statistics.median(samples["python"]) / statistics.median(samples["rust"]), 2
            )
            print(
                name,
                backend,
                {k: v["median_ms"] for k, v in row[backend].items() if isinstance(v, dict)},
                "speedup",
                row[backend]["speedup"],
                flush=True,
            )
        # Same source text loaded once; isolate the pure analysis API.
        for p, source in sources:
            lint(source, p)
        samples = []
        for _ in range(10):
            start = time.perf_counter()
            for p, source in sources:
                lint(source, p)
            samples.append(time.perf_counter() - start)
        rust = json.loads(subprocess.check_output([str(CORE), *map(str, files)], text=True))
        row["core"] = {"python": stats(samples), "rust": stats(rust["seconds"])}
        report["results"][name] = row
    profiler = cProfile.Profile()
    profiler.enable()
    for p in discover([str(ROOT / "src"), str(ROOT / "tests/q")]):
        lint(p.read_text(), str(p))
    profiler.disable()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    profiler.dump_stats(str(args.output.with_suffix(".prof")))
    with args.output.with_suffix(".profile.txt").open("w") as stream:
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(20)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("Saved", args.output)


if __name__ == "__main__":
    main()
