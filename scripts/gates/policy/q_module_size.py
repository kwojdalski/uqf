"""Hold every q module under src/ to a line budget (#970).

Python has this in python/uqs/tests/test_module_split.py (400 lines); q had
nothing, so `source_contract.q` grew to 1,640 lines and eleven concerns with no
prompt to split it. A q module also cannot be reviewed in one read past a few
hundred lines, and a namespace may span files (`.qetl.source` now does), so
there is always somewhere to split to.

Two rules, each a failure on its own:

* **A file over LIMIT lines fails** unless it is in ALLOWED, with the reason.
* **ALLOWED only ratchets down.** An allowlisted file may not grow past the cap
  listed, and an entry whose file is now under LIMIT (or gone) fails, so a
  split has to remove its exemption rather than leave it to be reused.

Generated files are not held to it: they are rewritten, never edited.

    python3 scripts/gates/check.py q-module-size
"""

from __future__ import annotations

from pathlib import Path

#: Lines a q module may have without an entry in ALLOWED.
LIMIT = 800

#: path (from the repo root) -> (cap, why it is exempt). Today's size is the
#: cap, so the allowlist can only shrink.
ALLOWED: dict[str, tuple[int, str]] = {
    "src/etl/core/bounded_worker.q": (
        1614,
        "the bounded-worker lifecycle (define, plan, run, retry, cleanup) in one namespace; "
        "the next split after source_contract.q (#970), not yet done",
    ),
    "src/market_data/microstructure.q": (
        1227,
        "one .qf.mkt namespace of independent analytics (spread, depth, impact, flow); "
        "a split is possible along those lines but nothing is forcing it",
    ),
}

#: Directories whose files are written by a generator, not edited.
GENERATED = ("src/etl/generated/",)


def line_count(path: Path) -> int:
    return len(path.read_text().splitlines())


def problems(root: Path) -> list[str]:
    """Every breach of the budget under `root`/src, one message each."""
    out: list[str] = []
    seen: set[str] = set()
    for path in sorted((root / "src").rglob("*.q")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(GENERATED):
            continue
        n = line_count(path)
        seen.add(rel)
        if rel in ALLOWED:
            cap, _ = ALLOWED[rel]
            if n <= LIMIT:
                out.append(f"{rel}: {n} lines is under {LIMIT} - remove it from ALLOWED")
            elif n > cap:
                out.append(
                    f"{rel}: {n} lines is over its cap of {cap} - split it, do not raise the cap"
                )
            elif n < cap:
                out.append(f"{rel}: {n} lines is under its cap of {cap} - lower the cap to {n}")
        elif n > LIMIT:
            out.append(
                f"{rel}: {n} lines is over {LIMIT} - split it along its concerns "
                "(a namespace may span files), or add it to ALLOWED with the reason"
            )
    for rel in sorted(set(ALLOWED) - seen):
        out.append(f"ALLOWED lists {rel}, which does not exist - remove it")
    return out
