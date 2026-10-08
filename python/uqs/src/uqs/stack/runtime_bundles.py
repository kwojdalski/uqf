"""Which sidecar bundles a runtime declares, resolved once for every caller (#852).

A runtime decides which stack exists (runtimes.py); a bundle supplies jobs,
tables and catalog entries (stack/bundles.py); a profile decides what starts.
Which bundles belong to which runtime is the operator's, not this tree's, so it
is declared outside the source, in `runtime_bundles.json` at the repository
root - or wherever $UQS_RUNTIME_BUNDLES names:

    {"uqf": ["../piggybank"], "crypto": ["../piggybank", "../marketwarehouse"]}

Folders are relative to that file. `resolve` turns a runtime's declaration,
plus any explicit `--bundle` folders (added to it, never replacing it), into
the composition - and `uqs runtime prepare` and `uqs deploy build` both call
it, so the stack prepared here and the release built for a server cannot
disagree. Installing records the runtime in the bundle ledger, which is what
runtime membership reads (model/runtime_members.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from uqs.runtimes import RUNTIMES
from uqs.stack import bundles
from uqs.stack.bundle_blocks import BundleError

DECLARATION = "runtime_bundles.json"
DECLARATION_ENV = "UQS_RUNTIME_BUNDLES"


@dataclass(frozen=True)
class Member:
    """One bundle in a runtime's composition, and why it is there."""

    bundle: bundles.Bundle
    #: "runtime" - the runtime declares it; "explicit" - a --bundle argument
    source: str


def declaration_path(root: Path) -> Path:
    configured = os.environ.get(DECLARATION_ENV, "").strip()
    return Path(configured).expanduser() if configured else root / DECLARATION


def read_declaration(root: Path) -> dict[str, list[Path]]:
    """{runtime: [bundle folder, ...]}; {} when there is no declaration."""
    path = declaration_path(root)
    if not path.is_file():
        if os.environ.get(DECLARATION_ENV, "").strip():
            raise BundleError(f"{DECLARATION_ENV}={path} does not exist")
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise BundleError(f"{path}: not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise BundleError(f'{path}: must map a runtime to its bundles, e.g. {{"uqf": ["../x"]}}')
    declared: dict[str, list[Path]] = {}
    for runtime, folders in data.items():
        if runtime not in RUNTIMES:
            raise BundleError(f"{path}: {runtime!r} is not a runtime - {', '.join(RUNTIMES)}")
        if not RUNTIMES[runtime].pipelines:
            raise BundleError(
                f"{path}: the {runtime} runtime has none of this tree's pipelines, "
                "so it cannot have a bundle's jobs"
            )
        if not isinstance(folders, list) or not all(isinstance(f, str) for f in folders):
            raise BundleError(f"{path}: {runtime} must list bundle folders as strings")
        declared[runtime] = [(path.parent / f).resolve() for f in folders]
    return declared


def resolve(runtime: str, root: Path, explicit: list[Path] | tuple[Path, ...] = ()) -> list[Member]:
    """`runtime`'s composition: its declared bundles, then `explicit` ones.

    Every manifest is read and checked here. One bundle named twice from the
    same folder counts once; two folders claiming one bundle name is refused.
    """
    if runtime not in RUNTIMES:
        raise BundleError(f"{runtime!r} is not a runtime - {', '.join(RUNTIMES)}")
    wanted = [(f, "runtime") for f in read_declaration(root).get(runtime, [])]
    wanted += [(Path(f).resolve(), "explicit") for f in explicit]
    members: dict[str, Member] = {}
    for folder, source in wanted:
        if not bundles.is_bundle(folder):
            raise BundleError(f"{folder} holds no {bundles.MANIFEST} - it is not a bundle")
        bundle = bundles.read_bundle(folder)
        seen = members.get(bundle.name)
        if seen is None:
            members[bundle.name] = Member(bundle, source)
        elif seen.bundle.root != bundle.root:
            raise BundleError(
                f"bundle {bundle.name} is named by both {seen.bundle.root} and {bundle.root}"
            )
    if members and not RUNTIMES[runtime].pipelines:
        raise BundleError(f"the {runtime} runtime has no pipelines, so it cannot have bundles")
    return list(members.values())


def plan_all(members: list[Member], root: Path) -> list[bundles.Plan]:
    """Every member planned against the tree, and checked against each
    other - two bundles placing one file or defining one table - so a
    conflict is refused before the first one is installed."""
    plans = [bundles.plan(m.bundle, root) for m in members]
    owner: dict[str, str] = {}
    for p in plans:
        claims = [str(i.destination) for i in p.jobs] + [f"table {t}" for t in p.tables]
        for claim in claims:
            if claim in owner and owner[claim] != p.bundle.name:
                raise BundleError(f"bundles {owner[claim]} and {p.bundle.name} both claim {claim}")
            owner[claim] = p.bundle.name
    return plans


def planned_q(plans: list[bundles.Plan], root: Path) -> dict[str, str]:
    """Each q file the plans would install, by its path in the tree: what a
    dry run converts beside the tree, before anything is written."""
    return {
        str(i.destination.relative_to(root)): i.source.read_text(encoding="utf-8")
        for p in plans
        for i in p.jobs
        if i.destination is not None and i.destination.suffix == ".q"
    }


def prepare(runtime: str, members: list[Member], root: Path) -> dict[str, dict]:
    """Install `members` into the tree for `runtime`, and take `runtime` off
    every installed bundle it no longer declares. Nothing is uninstalled -
    a bundle no runtime has is in no stack - and nothing is started."""
    plan_all(members, root)
    entries = {
        m.bundle.name: bundles.install(bundles.plan(m.bundle, root), root, runtime) for m in members
    }
    ledger = bundles.read_ledger(root)
    changed = False
    for name, entry in ledger.items():
        if name not in entries and runtime in entry.get("runtimes", []):
            entry["runtimes"] = [r for r in entry["runtimes"] if r != runtime]
            changed = True
    if changed:
        (root / bundles.LEDGER).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return entries


def composition(runtime: str, members: list[Member]) -> dict:
    """The manifest's and the dry run's record of what was resolved."""
    return {
        "runtime": runtime,
        "bundles": [
            {
                "name": m.bundle.name,
                "version": m.bundle.version,
                "source": m.source,
                "folder": str(m.bundle.root),
            }
            for m in members
        ],
    }
