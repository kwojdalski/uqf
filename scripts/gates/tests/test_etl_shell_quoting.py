"""No `src/etl` code hands a path to the shell unquoted (#2187).

`system"rm -rf ",p` is one word only while `p` holds no space. The HDB writer ran
exactly that on a root with a space in it, and `rm -rf` deleted a sibling install.
A path goes through `.qetl.job.bounded.state.shell_word` (or `.qetl.io.sh`, which
applies it), or to q's own `hdel`.

The HDB-touching modules are held to zero. Every other module is listed with the
count it had when this gate was written: the count may fall, never rise, so a
module is taken off the list by fixing it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ETL = REPO / "src" / "etl"

# `system "cmd ",x` -- a literal followed by a concatenation.
_CONCAT = re.compile(r'system\s*"[^"]*"\s*,')
# q's own one-letter commands (`\P`, `\c`, `\l` as `system"P ",...`) run in q,
# not a shell, so a value spliced into them is no shell word.
_SAFE = re.compile(r'shell_word|\.qetl\.io\.sh\[|system\s*"[A-Za-z] ')

# Modules that run on an HDB path: no exceptions.
STRICT = {"core/io_hdb.q", "core/compression.q"}

# Existing offenders outside the HDB writer (paths from status/lock/state dirs),
# by the number of unquoted calls. Only ever lower these.
BASELINE = {
    "core/verify_behaviour.q": 1,
    "core/react.q": 1,
    "core/worker_runtime.q": 1,
    "core/tick.q": 1,
}


def _offenders() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(ETL.rglob("*.q")):
        rel = path.relative_to(ETL).as_posix()
        for no, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split(" /")[0]
            if code.lstrip().startswith("/"):
                continue
            if _CONCAT.search(code) and not _SAFE.search(code):
                found.setdefault(rel, []).append(f"{rel}:{no}: {line.strip()}")
    return found


def test_the_hdb_modules_quote_every_path():
    found = _offenders()
    bad = [hit for rel in STRICT for hit in found.get(rel, [])]
    assert not bad, "unquoted path in a shell command (use shell_word):\n" + "\n".join(bad)


def test_no_other_module_grows_its_unquoted_calls():
    found = _offenders()
    grown = {
        rel: hits
        for rel, hits in found.items()
        if rel not in STRICT and len(hits) > BASELINE.get(rel, 0)
    }
    assert not grown, "new unquoted path in a shell command (use shell_word):\n" + "\n".join(
        hit for hits in grown.values() for hit in hits
    )


def test_the_baseline_names_only_modules_that_still_offend():
    found = _offenders()
    stale = {rel: n for rel, n in BASELINE.items() if len(found.get(rel, [])) < n}
    assert not stale, f"lower or remove these baseline entries: {stale}"


def test_a_q_command_is_not_a_shell_command():
    # #2438 restores \P with system"P ",string p: q runs it, no shell does.
    flagged = [
        c
        for c in ('system"P ",string p', 'system"rm -rf ",p', 'system "mv ",a," ",b')
        if _CONCAT.search(c) and not _SAFE.search(c)
    ]
    assert flagged == ['system"rm -rf ",p', 'system "mv ",a," ",b']
