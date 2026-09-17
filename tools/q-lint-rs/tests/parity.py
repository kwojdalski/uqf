"""Compare native/Python diagnostic identities, positions and severity.

Run with the repo's Python test environment after cargo build --release.
Messages may differ; rule identities and diagnostic behavior must agree.
"""

import ast
import json
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# See benchmark.py: the Python q-lint is not vendored here, so this
# implementation-comparison script only runs in a checkout that has both.
sys.path.insert(0, str(ROOT / "tools/q-lint/src"))
from q_lint import lint  # noqa: E402  # ty: ignore[unresolved-import]
from q_lint.rules import RISKY_PARAM_NAMES  # noqa: E402  # ty: ignore[unresolved-import]
from q_lint.taxonomy import catalogue  # noqa: E402  # ty: ignore[unresolved-import]

BINARY = ROOT / "tools/q-lint-rs/target/release/qlinter"
FIELDS = (
    "path",
    "line",
    "column",
    "end_line",
    "end_column",
    "rule",
    "code",
    "category",
    "severity",
    "source",
)


def identity(findings):
    return Counter(tuple(f[k] for k in FIELDS) for f in findings)


def main():
    package = ROOT / "tools/q-lint"
    cases = [
        c["source"] for c in json.loads((package / "tests/fixtures/quant_cases.json").read_text())
    ]
    for c in json.loads((package / "tests/fixtures/mutations.json").read_text()):
        for s in (c["source"], c["source"].replace(c["old"], c["new"])):
            cases.extend((s, s.replace("{[", "{[\n "), 'note:"ratio / rate";\n' + s))
    # Include literal snippets from all Python boundary tests, including the
    # original hook suite. Harmless non-q strings exercise the same API too.
    for p in [
        *package.glob("tests/test_*.py"),
        ROOT / "python/uqf_frontend/tests/test_check_q_traps.py",
    ]:
        cases.extend(
            n.value
            for n in ast.walk(ast.parse(p.read_text()))
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and any(c in n.value for c in "{};")
        )
    cases.extend("f:{[" + n + "] 1}" for n in sorted(RISKY_PARAM_NAMES))
    cases.extend(
        [
            's:"😀";f:{]',
            "f:{[x] g:{[a] x+a};g[1]}",
            "f:{[] \n '\"" + "é" * 201 + '"}',
            's:"hi"; / bad {[desc]\n',
        ]
    )
    cases.extend(["select from t where a=b=b", 'f:{[xs] ( ", " sv string xs,"extra")}'])
    cases = sorted(set(cases))
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for i, source in enumerate(cases):
            p = Path(directory).resolve() / f"{i:04d}.q"
            p.write_text(source)
            paths.append(p)
        paths.extend(sorted((ROOT / "src").rglob("*.q")))
        paths.extend(sorted((ROOT / "tests/q").rglob("*.q")))
        paths.extend(
            ROOT / name
            for name in (
                "lib/kdb-parquet/Python/converter_new.q",
                "lib/kdb-parquet/Python/time_read.q",
                "lib/kdb-parquet/k4unit/embedread.q",
                "lib/kdb-parquet/k4unit/python_setup.q",
                "lib/q-doc/kdb-common/src/util.q",
            )
        )
        for profile in ("general", "uqf"):
            expected = [
                asdict(f) for p in paths for f in lint(p.read_text(), str(p), profile=profile)
            ]
            result = subprocess.run(
                [str(BINARY), *map(str, paths), "--format", "json", "--profile", profile],
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode in (0, 1), result.stderr
            actual = json.loads(result.stdout)
            missing, extra = (
                identity(expected) - identity(actual),
                identity(actual) - identity(expected),
            )
            for label, rows in (("Missing", missing), ("Extra", extra)):
                for key in rows:
                    print(label, key, repr(Path(key[0]).read_text()[:300]))
            assert not missing and not extra, (
                f"Parity failed: {len(missing)} missing, {len(extra)} extra"
            )
            print(f"{profile}: {len(paths)} sources, {len(actual)} findings match")
    rust_rules = json.loads(
        subprocess.check_output([str(BINARY), "--rules", "--format", "json"], text=True)
    )
    assert rust_rules == catalogue(), "Rust/Python rule catalogues drifted"
    print("Rule catalogues match")


if __name__ == "__main__":
    main()
