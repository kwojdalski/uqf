#!/usr/bin/env python3
"""Fail if a pre-commit scope is dead, or if tracked Python is ungated.

Two distinct failures, and only the first is obvious:

1. **A dead scope** - a hook scoped to a path that no longer exists matches
   nothing, exits zero, and reports green. Worse than no hook, because the
   commit looks gated when it is not.
2. **Ungated code** - a scope that is still *valid* but too *narrow*. Python
   added outside it is simply never linted, and nothing anywhere complains.

The second is what actually happened here - twice, because the first version
of this script checked only the LINT hooks. While it reported 43/43 lint
coverage and exited zero, 229 of the repository's 231 Python tests were not
run by any hook, and no CI existed to catch them either. A gate-checker blind
to half the gates is its own version of the bug it exists to prevent, so it
now checks both.

A hook scoped with ``files:`` to a path that no longer exists does not fail -
it matches nothing, exits zero, and reports green. That is strictly worse
than having no hook: the commit looks gated when it is not.

The ruff hooks were scoped ``^python/uqf-client/`` while Python was being
added under ``python/torq_orchestrator/`` and ``python/uqf_frontend/``. That
scope was never dead - the directory still exists - so a dead-scope check
alone would have passed it, which is why the coverage check exists too. Four
consecutive pull requests of Python went through a lint gate pointed at a
different directory. One bug that slipped past - a module shadowed by a
same-named endpoint function - was caught by ruff within seconds of it
finally being pointed at the code.

Run standalone to audit, or as a pre-commit hook to enforce:

    python3 scripts/check_hook_scopes.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / ".pre-commit-config.yaml"

#: Hooks whose scope is deliberately allowed to match nothing, with a reason.
#: Keep this empty unless there is a real one - the point of the check is to
#: refuse dead scopes, not to accumulate exemptions.
ALLOWED_EMPTY: dict[str, str] = {}

#: Hook ids that constitute the Python lint gate. Every tracked .py file must
#: be matched by at least one of them.
LINT_HOOKS = ("ruff", "ruff-format")

#: Hook ids that constitute the Python test gate. Same requirement: tracked
#: Python outside these is never exercised on commit.
TEST_HOOKS = ("python-tests",)

#: Hook ids that constitute the Python TYPE gate. Same requirement again.
#:
#: Checked here rather than trusted because the lint gate already drifted
#: once, from a directory scope that stayed valid while the code moved out
#: from under it - 43 of 47 files ungated, and nothing complained. A type
#: gate can drift exactly the same way, and the symptom is identical:
#: everything passes because almost nothing is checked.
TYPE_HOOKS = ("ty",)

#: Path prefixes exempt from needing lint coverage, each with its reason.
#: Vendored trees only: this repository's standing rule is never to edit a
#: vendored tree, so linting one would produce findings nobody may fix.
LINT_EXEMPT: dict[str, str] = {
    "lib/": "vendored third-party trees are never edited here, so linting them is noise",
}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        cwd=CONFIG.parent,
    )
    return out.stdout.splitlines()


def scoped_hooks(config_text: str) -> list[tuple[str, str]]:
    """Every (hook id, files pattern) pair, in file order.

    Parsed with a regex rather than a YAML library so this script has no
    dependencies and can run as a plain system hook. The config's shape is
    stable and this only needs two fields.
    """
    hooks: list[tuple[str, str]] = []
    current: str | None = None
    for line in config_text.splitlines():
        id_match = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if id_match:
            current = id_match.group(1)
            continue
        files_match = re.match(r"""\s*files:\s*['"]?(.+?)['"]?\s*$""", line)
        if files_match and current is not None:
            hooks.append((current, files_match.group(1)))
    return hooks


def main() -> int:
    if not CONFIG.is_file():
        print(f"no pre-commit config at {CONFIG}", file=sys.stderr)
        return 1

    files = tracked_files()
    hooks = scoped_hooks(CONFIG.read_text())
    if not hooks:
        # A regex that matches nothing would make this check pass vacuously -
        # the exact failure mode it exists to prevent.
        print(
            "check_hook_scopes: parsed no scoped hooks, which cannot be right",
            file=sys.stderr,
        )
        return 1

    dead: list[tuple[str, str]] = []
    print(f"check_hook_scopes: {len(hooks)} path-scoped hook(s) in {CONFIG.name}")
    for hook_id, pattern in hooks:
        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            print(f"  {hook_id:28} INVALID REGEX {pattern!r}: {exc}", file=sys.stderr)
            dead.append((hook_id, pattern))
            continue
        count = sum(1 for f in files if matcher.search(f))
        status = "ok" if count else "MATCHES NOTHING"
        print(f"  {hook_id:28} {pattern:34} {count:>5} file(s)  {status}")
        if not count and hook_id not in ALLOWED_EMPTY:
            dead.append((hook_id, pattern))

    # --- coverage: is any tracked Python outside either gate? -----------
    py_files = [
        f
        for f in files
        if f.endswith(".py")
        and "/.venv/" not in f
        and not any(f.startswith(prefix) for prefix in LINT_EXEMPT)
    ]

    ungated: list[str] = []
    print()
    for gate_name, gate_hooks in (
        ("lint", LINT_HOOKS),
        ("test", TEST_HOOKS),
        ("type", TYPE_HOOKS),
    ):
        patterns = [re.compile(p) for hook_id, p in hooks if hook_id in gate_hooks]
        if not patterns:
            print(
                f"  NO {gate_name.upper()} GATE  none of {gate_hooks} is configured",
                file=sys.stderr,
            )
            ungated.extend(py_files)
            continue
        missed = [f for f in py_files if not any(p.search(f) for p in patterns)]
        print(
            f"check_hook_scopes: {len(py_files) - len(missed)}/{len(py_files)} "
            f"tracked .py file(s) covered by the {gate_name} gate {gate_hooks}"
        )
        for f in missed:
            if f not in ungated:
                ungated.append(f)
        if missed:
            by_dir: dict[str, int] = {}
            for f in missed:
                top = "/".join(f.split("/")[:2])
                by_dir[top] = by_dir.get(top, 0) + 1
            for directory, count in sorted(by_dir.items()):
                print(
                    f"  UNGATED ({gate_name})  {directory:30} {count:>5} file(s)",
                    file=sys.stderr,
                )

    if dead:
        print(file=sys.stderr)
        print(
            "These hooks are scoped to paths that match no tracked file:",
            file=sys.stderr,
        )
        for hook_id, pattern in dead:
            print(f"  - {hook_id}: {pattern}", file=sys.stderr)
        print(
            "\nA hook that matches nothing exits zero and reports green, so the commit "
            "looks gated when it is not. Fix the scope, or add the hook to ALLOWED_EMPTY "
            "with a reason.",
            file=sys.stderr,
        )
        return 1

    if ungated:
        print(file=sys.stderr)
        print(
            f"{len(ungated)} tracked Python file(s) fall outside the lint gate "
            f"{LINT_HOOKS}, the test gate {TEST_HOOKS} or the type gate "
            f"{TYPE_HOOKS}, so they are not fully "
            f"checked on commit. Widen the relevant scopes, or add a path to "
            f"LINT_EXEMPT with a reason.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
