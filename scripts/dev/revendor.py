#!/usr/bin/env python3
"""Re-vendor a tree under lib/ from its upstream, and rewrite its record (#2157).

    python3 scripts/dev/revendor.py TREE                 # restore the recorded commit's bytes
    python3 scripts/dev/revendor.py TREE --commit SHA    # move to another upstream commit
    python3 scripts/dev/revendor.py TREE --verify        # compare the record with upstream only

The ONE way a vendored tree changes. It fetches the tree's `upstream` (from
lib/VENDORED.toml) at the commit, into a temporary repository, and:

  1. replaces every file under lib/TREE with upstream's bytes and modes -
     the paths the record `include`s, minus those it `omit`s, `renames`
     applied - keeping only the record's `local` files (a NOTICE.md) and
     anything git ignores (an operator's own config);
  2. rewrites lib/.vendored/TREE.tsv (every file's mode, blob and path) and
     the tree's `commit`, `upstream_tree`, `files` and `digest` in
     lib/VENDORED.toml;
  3. deletes lib/.vendored/TREE.drift.tsv and the table's `drift_digest`:
     the tree is upstream's again.

Commit the tree, the listing and the record together, in one reviewed
commit; `python3 scripts/gates/check.py vendored` then passes. To vendor a
NEW tree, add its `[tree.NAME]` table to lib/VENDORED.toml by hand (upstream,
commit, license, license_file, and include/omit/renames/local as needed, with
placeholder digest/upstream_tree/files) and run this.

`--verify` fetches upstream and confirms the listing is what upstream holds at
the recorded commit - the check the gate cannot make offline. Needs git and
network access to the upstream; nothing else beyond the standard library.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
RULE = REPO / "scripts" / "gates" / "policy" / "vendored.py"


def _rule() -> ModuleType:
    loaded = sys.modules.get("vendored")
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve() == RULE:
        return loaded
    spec = importlib.util.spec_from_file_location("vendored", RULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["vendored"] = module
    spec.loader.exec_module(module)
    return module


v = _rule()


def _git(*args: str, cwd: Path, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, input=data
    ).stdout


def fetch(url: str, commit: str, into: Path) -> str:
    """Fetch `commit` of `url` into a fresh repository at `into`; return the full sha."""
    _git("init", "-q", str(into), cwd=into.parent)
    try:
        _git("fetch", "-q", "--depth", "1", url, commit, cwd=into)
        sha = _git("rev-parse", "FETCH_HEAD^{commit}", cwd=into).decode().strip()
    except subprocess.CalledProcessError:
        # A short sha, or a server that refuses a sha in `want`: fetch everything.
        _git(
            "fetch",
            "-q",
            url,
            "+refs/heads/*:refs/remotes/upstream/*",
            "+refs/tags/*:refs/tags/*",
            cwd=into,
        )
        sha = _git("rev-parse", f"{commit}^{{commit}}", cwd=into).decode().strip()
    if not sha.startswith(commit.lower()):
        raise SystemExit(f"revendor: {url} resolved {commit} to {sha}")
    return sha


def ls_tree(repo: Path, sha: str) -> list[tuple[str, str, str, str]]:
    out = _git("ls-tree", "-r", "-z", "--full-tree", sha, cwd=repo).decode()
    entries = []
    for rec in out.split("\0"):
        if rec:
            meta, path = rec.split("\t", 1)
            mode, kind, blob = meta.split()
            entries.append((mode, kind, blob, path))
    return entries


def read_blobs(repo: Path, blobs: list[str]) -> dict[str, bytes]:
    """Every blob's raw bytes, through one `git cat-file --batch`: no checkout,
    so no line-ending or filter rewrites a checkout might apply."""
    out = _git("cat-file", "--batch", cwd=repo, data="".join(b + "\n" for b in blobs).encode())
    result: dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        sha, _, size = out[pos:nl].decode().split()
        start = nl + 1
        result[sha] = out[start : start + int(size)]
        pos = start + int(size) + 1
    return result


def set_fields(record: str, name: str, fields: dict[str, str | int]) -> str:
    """`record` with `fields` rewritten inside its [tree.NAME] table."""
    head = re.search(rf"^\[tree\.{re.escape(name)}\]\s*$", record, re.M)
    if not head:
        raise SystemExit(f"revendor: no [tree.{name}] table in {v.RECORD}")
    nxt = re.search(r"^\[", record[head.end() :], re.M)
    end = head.end() + (nxt.start() if nxt else len(record) - head.end())
    body = record[head.end() : end]
    for key, value in fields.items():
        text = f'"{value}"' if isinstance(value, str) else str(value)
        body, n = re.subn(rf"^{key}\s*=.*$", f"{key} = {text}", body, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"revendor: [tree.{name}] has no `{key}` line to rewrite")
    return record[: head.end()] + body + record[end:]


def drop_field(record: str, name: str, key: str) -> str:
    """`record` without `key`'s line in its [tree.NAME] table, if it has one."""
    head = re.search(rf"^\[tree\.{re.escape(name)}\]\s*$", record, re.M)
    if not head:
        raise SystemExit(f"revendor: no [tree.{name}] table in {v.RECORD}")
    nxt = re.search(r"^\[", record[head.end() :], re.M)
    end = head.end() + (nxt.start() if nxt else len(record) - head.end())
    body = re.sub(rf"^{key}\s*=.*\n?", "", record[head.end() : end], count=1, flags=re.M)
    return record[: head.end()] + body + record[end:]


def revendor(root: Path, name: str, commit: str | None, verify: bool) -> int:
    trees = {t.name: t for t in v.load_record(root)}
    if name not in trees:
        raise SystemExit(f"revendor: {name} has no [tree.{name}] table in {v.RECORD}")
    tree = trees[name]
    commit = commit or tree.commit
    with tempfile.TemporaryDirectory(prefix="revendor-") as tmp:
        repo = Path(tmp) / "upstream"
        repo.mkdir()
        sha = fetch(tree.upstream, commit, repo)
        upstream_tree = _git("rev-parse", f"{sha}^{{tree}}", cwd=repo).decode().strip()
        listing = v.upstream_listing(ls_tree(repo, sha), tree)
        text = v.format_listing(listing)
        if verify:
            recorded = (
                (root / tree.listing_file).read_text()
                if (root / tree.listing_file).exists()
                else ""
            )
            ok = sha == tree.commit and text == recorded and v.digest(text) == tree.digest
            ok = ok and upstream_tree == tree.upstream_tree
            verdict = "matches" if ok else "DOES NOT match"
            print(f"{name}: {tree.upstream} {sha[:12]} - record {verdict} upstream")
            if not ok:
                old = v.parse_listing(recorded)
                for p in sorted(old.keys() | listing.keys()):
                    if old.get(p) != listing.get(p):
                        print(
                            f"  {p}: recorded {old.get(p)}, upstream {listing.get(p)}",
                            file=sys.stderr,
                        )
            return 0 if ok else 1
        blobs = read_blobs(repo, sorted({b for _, b in listing.values()}))

    dest = root / tree.path
    keep = set(tree.local)
    if dest.exists():
        for p in v.git_files(root, tree.path):
            rel = p[len(tree.path) + 1 :]
            f = root / p
            if rel not in keep and (f.exists() or f.is_symlink()):
                f.unlink()
        for d in sorted((p for p in dest.rglob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
    for rel, (mode, blob) in listing.items():
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            f.symlink_to(blobs[blob].decode())
        else:
            f.write_bytes(blobs[blob])
            f.chmod(0o755 if mode == "100755" else 0o644)
        if v.file_entry(f) != (mode, blob):
            raise SystemExit(f"revendor: {f} did not round-trip to {mode} {blob}")

    listings = root / v.LISTINGS
    listings.mkdir(parents=True, exist_ok=True)
    (root / tree.listing_file).write_text(text)
    (root / tree.drift_file).unlink(missing_ok=True)
    record = root / v.RECORD
    record.write_text(
        set_fields(
            drop_field(record.read_text(), name, "drift_digest"),
            name,
            {
                "commit": sha,
                "upstream_tree": upstream_tree,
                "files": len(listing),
                "digest": v.digest(text),
            },
        )
    )
    print(f"{name}: {len(listing)} files from {tree.upstream} at {sha}; record rewritten")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scripts/dev/revendor.py", description=__doc__.split("\n\n")[0]
    )
    ap.add_argument("tree", help="the directory under lib/, as named in lib/VENDORED.toml")
    ap.add_argument("--commit", help="the upstream commit to vendor (default: the recorded one)")
    ap.add_argument(
        "--verify", action="store_true", help="compare the record with upstream; write nothing"
    )
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    return revendor(args.root.resolve(), args.tree, args.commit, args.verify)


if __name__ == "__main__":
    sys.exit(main())
