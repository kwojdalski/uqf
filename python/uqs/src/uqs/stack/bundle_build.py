"""`python -m uqs.stack.bundle_build`: the two steps scripts/build_release.py
runs inside a STAGED copy of the tree when a release carries bundles (#800).

    install <bundle>...   install each bundle into the tree this package sits
                          in, in order; the first refusal fails the build,
                          whose staged tree is then thrown away. Prints the
                          ledger entries as JSON
    needs <procname>...   each streaming process's dependency closure - the
                          uqf processes it needs running - as JSON

Run with PYTHONPATH naming the staged tree's python/uqs/src, so `repo_root()`
finds the STAGED tree and never the checkout the build was started from. Two
invocations, not one: `needs` reads the registry, which must be imported
after `install` has written the bundles' jobs - and the derived files are
regenerated between them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from uqs.paths import UqsError, repo_root


def _install(folders: list[str]) -> dict:
    from uqs.stack import bundles

    root = repo_root()
    staged = [bundles.read_bundle(Path(f)) for f in folders]
    names = [b.name for b in staged]
    if dupes := sorted({n for n in names if names.count(n) > 1}):
        raise UqsError(f"bundle(s) {', '.join(dupes)} given twice")
    entries = {}
    for bundle in staged:
        # One at a time, each planned against the tree the previous ones
        # left: a second bundle declaring the first one's job or table is
        # then refused as the conflict it is.
        entries[bundle.name] = bundles.install(bundles.plan(bundle, root), root)
    return entries


def _needs(procnames: list[str]) -> dict[str, list[str]]:
    from uqs.model import profiles

    return {p: sorted(profiles.closure([p])) for p in procnames}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("install", "needs"):
        print("usage: bundle_build install <bundle>... | needs <procname>...", file=sys.stderr)
        return 2
    try:
        result = _install(args[1:]) if args[0] == "install" else _needs(args[1:])
    except UqsError as exc:
        print(f"bundle_build: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
