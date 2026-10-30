#!/usr/bin/env python3
"""Run one of this repository's policy checks over a checkout (#1806).

    python3 scripts/gates/check.py CHECK                # over this checkout
    python3 scripts/gates/check.py CHECK --root DIR     # over another tree
    python3 scripts/gates/check.py --help               # the checks

A policy check reads the repository and reports where it breaks a rule the
tree has set itself - a q module over its line budget, a `\\d` two levels
deep. Each used to be a script of its own, with its own argument handling,
its own idea of where the repository is and its own exit handling. This is
the one command for them; the rules stay where they were written, one module
each under scripts/gates/policy/, with their own scope, parsing, exemptions
and messages. A check here names its rule module and is a few lines that ask
it for findings and hand them to the reporting below.

CHECKS IS A LIST WRITTEN BY HAND. No plugins, no discovery, no `all`: a rule
joins by being added to CHECKS, and runs only when it is named. A check's
rule module is loaded only once that check is chosen, so naming one never
imports, let alone runs, another.

WHAT THE COMMAND OWNS, and every check shares:

  - the repository root: this checkout, or `--root`, which must hold a src/
    (a wrong root otherwise reads as a tree with nothing in it to break a
    rule);
  - the report: each finding on stderr, with the file and line the rule
    gives it; a one-line summary on stdout when there are none;
  - the exit status: 0 when the rule holds, 1 when it does not or the check
    could not run, 2 for a usage error. A check that crashes is reported as
    one that could not run - never as one that found nothing.

Every check is read-only and needs only the standard library: no q, no
configuration, no credentials. Not every gate under scripts/gates/ is one -
the HDB utilities there operate on data, and the rest have not moved here.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
#: Where each check's rule lives, one module per check.
RULES = HERE / "policy"
#: How this command is named in every message: relative, as a contributor runs it.
PROG = "scripts/gates/check.py"


def _repo_command() -> ModuleType:
    """scripts/repo_command.py: the exit statuses, the loader and the crash
    guard this command shares with the next repository command (#1804)."""
    path = HERE.parent / "repo_command.py"
    loaded = sys.modules.get("repo_command")
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve() == path:
        return loaded
    spec = importlib.util.spec_from_file_location("repo_command", path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules["repo_command"] = module
    spec.loader.exec_module(module)
    return module


kit = _repo_command()


def rule(name: str) -> ModuleType:
    """The rule module scripts/gates/policy/`name`.py, loaded when a check runs."""
    return kit.load(RULES / f"{name}.py", name)


def report(check: str, findings: Sequence[str], ok: str, footer: str = "") -> int:
    """Each finding on stderr and FAILED, or `ok` on stdout and OK."""
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        if footer:
            print(footer, file=sys.stderr)
        return kit.FAILED
    print(f"{check}: {ok}")
    return kit.OK


# ------------------------------------------------------------------- checks


def _q_module_size(size: ModuleType, root: Path) -> int:
    """Every q module under src/ within its line budget (#970)."""
    return report(
        "q-module-size",
        size.problems(root),
        f"every q module under {size.LIMIT} lines, {len(size.ALLOWED)} exempt",
    )


@dataclass(frozen=True)
class Check:
    name: str
    help: str
    #: The rule's module, scripts/gates/policy/<rule>.py - loaded only when
    #: this check runs.
    rule: str
    #: Runs the check: given its rule module and the repository root, returns
    #: the exit status.
    run: Callable[[ModuleType, Path], int]


#: Every check, by name. Added to by hand: there is no discovery.
CHECKS: dict[str, Check] = {
    c.name: c
    for c in (
        Check(
            "q-module-size",
            "every q module under src/ stays within its line budget, or its listed cap",
            "q_module_size",
            _q_module_size,
        ),
    )
}


# --------------------------------------------------------------------- main


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="check", required=True, metavar="CHECK")
    for check in CHECKS.values():
        one = sub.add_parser(check.name, help=check.help, description=check.help)
        one.add_argument(
            "--root",
            type=Path,
            default=REPO,
            metavar="DIR",
            help="the repository tree to check (default: this checkout)",
        )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    root = args.root.resolve()
    if not (root / "src").is_dir():
        ap.error(f"--root {args.root}: not a checkout of this repository (it has no src/)")
    check = CHECKS[args.check]
    return kit.guarded(check.name, lambda: check.run(rule(check.rule), root))


if __name__ == "__main__":
    sys.exit(main())
