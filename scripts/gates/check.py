#!/usr/bin/env python3
"""Run one of this repository's policy checks over a checkout (#1806).

    python3 scripts/gates/check.py CHECK                # over this checkout
    python3 scripts/gates/check.py CHECK --root DIR     # over another tree
    python3 scripts/gates/check.py CHECK --help         # its options
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
    gives it; a one-line summary on stdout when there are none. A rule whose
    findings are grouped with advice per group (env-reference) prints its
    own, to the same streams;
  - the exit status: 0 when the rule holds, 1 when it does not or the check
    could not run, 2 for a usage error. A check that crashes is reported as
    one that could not run - never as one that found nothing.

A check takes `--root` and nothing else, unless it declares `options`: then
its subcommand gains them and its run is handed the parsed arguments
(hook-scopes' `--kdbx-only`, which prints the KDB-X-only hook ids instead).
No check takes a file list - each reads the whole tree, so a rule added today
flags a file touched last month - and none needs pre-commit's.

Every check is read-only and needs no q, no configuration and no
credentials. All but three need only the standard library: diagram-ports
reads the stack's ports through uqs, so its hook runs it under `uv run`;
q-traps and module-deps run the qlinter `.qlinter-version` pins, and fail,
naming the install, without it. bundle-blocks reads the root's git index,
not its working tree; hook-scopes and q-traps read what git tracks there.
Not every gate under scripts/gates/ is one - the HDB utilities there operate
on data. The doc-links gate (check_doc_links.py) is not one either: it renders
the docs site with Quarto, which writes into docs/ and leaves the site CI
publishes, so it is a build that also checks links.
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
    """scripts/gates/repo_command.py: the exit statuses, the loader and the
    crash guard, kept apart from the checks themselves (#1804)."""
    path = HERE / "repo_command.py"
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


def _nested_contexts(nested: ModuleType, root: Path) -> int:
    """No `\\d` names more than one level, in the tree's own q (#1411)."""
    return report(
        "nested-contexts",
        nested.problems(root),
        f"no `\\d` names more than one level in {', '.join(nested.SCOPE)}",
    )


def _registry_enlist(enlist: ModuleType, root: Path) -> int:
    """Every write that adds to a q registry under src/ stores it enlisted (#1030)."""
    return report(
        "registry-enlist",
        enlist.problems(root),
        f"every registry write enlists, {len(enlist.NOT_DICTS)} hold no dicts",
    )


def _env_reference(env: ModuleType, root: Path) -> int:
    """docs/reference/environment.md and .env.example agree with the code that
    reads the environment, both ways. The rule reports its own findings,
    grouped by direction with what to do about each."""
    return env.run(root)


def _declaration_reference(decl: ModuleType, root: Path) -> int:
    """docs/reference/pipeline-declarations.md has a row for every key the
    declaring q files list, and no row for a key they never mention (#1331)."""
    findings = decl.check((root / decl.PAGE).read_text(), root / decl.CORE)
    return report(
        "declaration-reference",
        findings,
        f"every declared key has a row in {decl.PAGE}, every row a key",
        footer=f"\n{len(findings)} problem(s) in {decl.PAGE}",
    )


def _markout_mapped(mapped: ModuleType, root: Path) -> int:
    """A markout's path selects market_data by its date alone and reads no depth (#2288)."""
    return report(
        "markout-mapped",
        mapped.problems(root),
        f"{len(mapped.PATH)} markout files select market_data by date alone and read no depth",
    )


def _variant_gather(gather: ModuleType, root: Path) -> int:
    """The reference-variant gather reads the deduped rids once and nothing else (#2289)."""
    return report(
        "variant-gather",
        gather.problems(root),
        "the variant gather reads the deduped rids once; no variant code touches the market",
    )


def _tool_pins(pins: ModuleType, root: Path) -> int:
    """Every copy of a tool's version pin agrees with its authority (#1838)."""
    return report(
        "tool-pins",
        pins.problems(root),
        f"every copy of {len(pins.PINS)} tool pins agrees with its authority",
    )


def _vendored(vendored: ModuleType, root: Path) -> int:
    """Every tree under lib/ holds the bytes its record says, upstream's (#2157)."""
    findings = vendored.problems(root)
    return report(
        "vendored",
        findings,
        vendored.summary(root) if not findings else "",
        footer=(
            f"\n{len(findings)} problem(s) under lib/. Vendored trees are never edited here:"
            " restore upstream's bytes with `python3 scripts/dev/revendor.py TREE`,"
            " or move to another upstream commit with `--commit SHA`."
        ),
    )


def _etl_layering(layering: ModuleType, root: Path) -> int:
    """src/etl/core/ never depends on a declaration; src/ reads TorQ only
    through its owner files; HTTP only in the webhook transport. Each rule's
    findings carry their own advice, so the rule reports them itself."""
    return layering.run(root)


def _diagram_ports(ports: ModuleType, root: Path) -> int:
    """Every `procname :port` label in docs/diagrams matches the stack (#1369)."""
    return ports.run(root)


def _bundle_blocks(blocks: ModuleType, root: Path) -> int:
    """Nothing in the index makes an operator's installed bundle tree content (#881)."""
    return blocks.run(root)


def _module_deps(deps: ModuleType, root: Path) -> int:
    """The q library's module graph is the reviewed, acyclic edge list (#626)."""
    return report(
        "module-deps",
        deps.problems(root),
        f"{len(deps.ALLOWED)} reviewed edges (qlinter analyze graph), acyclic, "
        "no name defined twice",
    )


def _q_traps(traps: ModuleType, root: Path) -> int:
    """No q file, nor q embedded in Python, sets one of the silent traps (#965).
    Each finding carries its own reason, so the rule reports them itself."""
    return traps.run(root)


def _doc_references(refs: ModuleType, root: Path) -> int:
    """Every q function the prose cites exists, and takes the arguments shown."""
    return refs.run(root)


def _hook_scopes(scopes: ModuleType, root: Path, args: argparse.Namespace) -> int:
    """No pre-commit scope is dead and no tracked Python is ungated - or, with
    `--kdbx-only`, the ids of the hooks that need a q."""
    return scopes.run(root, kdbx_only=args.kdbx_only)


def _hook_scopes_options(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--kdbx-only",
        action="store_true",
        help="print the ids of the hooks tagged `# kdbx-only`, comma-joined, and check nothing",
    )


@dataclass(frozen=True)
class Check:
    name: str
    help: str
    #: The rule's module, scripts/gates/policy/<rule>.py - loaded only when
    #: this check runs.
    rule: str
    #: Runs the check: given its rule module and the repository root - and,
    #: when it has `options`, the parsed arguments - returns the exit status.
    run: Callable[..., int]
    #: Adds the check's own options to its subcommand, beside `--root`.
    options: Callable[[argparse.ArgumentParser], None] | None = None


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
        Check(
            "nested-contexts",
            "no `\\d` in this tree's own q names more than one level",
            "nested_contexts",
            _nested_contexts,
        ),
        Check(
            "registry-enlist",
            "every write that adds to a q registry under src/ stores its value enlisted",
            "registry_enlist",
            _registry_enlist,
        ),
        Check(
            "markout-mapped",
            "a markout's path selects market_data by its date alone and reads no depth (#2288)",
            "markout_mapped",
            _markout_mapped,
        ),
        Check(
            "variant-gather",
            "the reference-variant gather reads the deduped rids once and nothing else (#2289)",
            "variant_gather",
            _variant_gather,
        ),
        Check(
            "env-reference",
            "docs/reference/environment.md and .env.example match the variables the code reads",
            "env_reference",
            _env_reference,
        ),
        Check(
            "declaration-reference",
            "docs/reference/pipeline-declarations.md matches the keys the declaring q files list",
            "declaration_reference",
            _declaration_reference,
        ),
        Check(
            "tool-pins",
            "every copy of a tool's version (uv, pre-commit, Python...) matches its authority",
            "tool_pins",
            _tool_pins,
        ),
        Check(
            "etl-layering",
            "src/etl/core/ depends on no declaration, and src/ reads TorQ only through its owners",
            "etl_layering",
            _etl_layering,
        ),
        Check(
            "diagram-ports",
            "every `procname :port` label in docs/diagrams matches the stack's ports",
            "diagram_ports",
            _diagram_ports,
        ),
        Check(
            "bundle-blocks",
            "the index stages no installed bundle's blocks, files, ledger or host overrides",
            "bundle_blocks",
            _bundle_blocks,
        ),
        Check(
            "vendored",
            "every tree under lib/ is byte-identical to the upstream commit it records",
            "vendored",
            _vendored,
        ),
        Check(
            "module-deps",
            "the q library's module graph is reviewed and acyclic, and each name is defined once",
            "module_deps",
            _module_deps,
        ),
        Check(
            "q-traps",
            "no q file, nor q built in Python, sets a silent trap the pinned qlinter knows",
            "q_traps",
            _q_traps,
        ),
        Check(
            "doc-references",
            "every q function the docs cite exists and takes the arguments the call shows",
            "doc_references",
            _doc_references,
        ),
        Check(
            "hook-scopes",
            "no pre-commit hook scope matches nothing, and every tracked .py file is gated",
            "hook_scopes",
            _hook_scopes,
            _hook_scopes_options,
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
        if check.options is not None:
            check.options(one)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    root = args.root.resolve()
    if not (root / "src").is_dir():
        ap.error(f"--root {args.root}: not a checkout of this repository (it has no src/)")
    check = CHECKS[args.check]
    extra = (args,) if check.options is not None else ()
    return kit.guarded(check.name, lambda: check.run(rule(check.rule), root, *extra))


if __name__ == "__main__":
    sys.exit(main())
