"""Hold docs/reference/pipeline-declarations.md to the declaring q code.

The page is hand-written, because "what this key means and when it is
refused" is prose no generator can produce. A hand-written reference drifts
the day a key is added or renamed, so this checks both directions for each
block's key table:

* every key the q file LISTS - its required keys, and the optional ones
  `.qetl.job.bounded` and the horizon, bars and rolling kinds list - has a row;
* every row names a key the q file mentions as a symbol (`` `key ``), so a
  renamed or removed key cannot live on in the page.

Optional keys that no list names (`procname`, `note`, `start_with_all`, `transport`,
`as_of`, the stream handlers) are held by the second direction only: the page
can describe them, and cannot describe one that no longer exists. A kind
that forwards `.qetl.job.stream.passthrough` accepts stream_job.q's
`passthrough_keys` without naming them, so those count as mentioned (#1232).

    python3 scripts/gates/check.py declaration-reference
"""

from __future__ import annotations

import re
from pathlib import Path

#: Both relative to the repository root, which scripts/gates/check.py
#: resolves and passes in.
PAGE = Path("docs/reference/pipeline-declarations.md")
CORE = Path("src/etl/core")

#: section heading prefix -> (declaring file, or a glob where the namespace
#: spans files (`.qetl.source`, #970), the key lists it defines, keys a list
#: names that are derived rather than declared)
BLOCKS: dict[str, tuple[str, tuple[str, ...], frozenset[str]]] = {
    "## Source": ("source_*.q", ("required_declarations", "optional_declarations"), frozenset()),
    "## Transform": ("transform.q", ("required_keys",), frozenset()),
    "## Bounded worker": ("bounded_worker.q", ("required_cfg", "optional_cfg"), frozenset()),
    # `ns` is in the list because register stamps it before checking; a job
    # never writes it, and the page says so in prose.
    "## Streaming job": ("stream_job.q", ("required_declarations",), frozenset({"ns"})),
    "## Normalizer": ("normalizer.q", ("required_keys",), frozenset()),
    # The three stream kinds (#1331). Each also lists its own optional keys;
    # the passthrough ones it forwards are counted as mentioned below.
    "## Horizon job": ("horizon.q", ("required_keys", "optional_keys"), frozenset()),
    "## Bars job": ("bars.q", ("required_keys", "optional_keys"), frozenset()),
    "## Rolling-window job": ("rolling.q", ("required_keys", "optional_keys"), frozenset()),
}

#: A key list in a declaring file. Every one under src/etl/core/ must be some
#: block's (`test_check_declaration_reference.py`), so a new kind cannot be
#: left out of BLOCKS the way Horizon, Bars and Rolling were (#1331).
#: A name is defined fully qualified from the root (`.qetl.job.stream.required_keys:`),
#: so the owning namespace may precede it (#1411).
_OWNER = r"(?:\.[A-Za-z_][\w.]*\.)?"
DECLARATION_LIST = re.compile(
    rf"^{_OWNER}((?:required|optional)_(?:keys|declarations|cfg)):", re.MULTILINE
)

_ROW_KEY = re.compile(r"^\s*\|\s*`(\w+)`\s*\|")


def q_list(source: str, name: str) -> list[str]:
    """The symbols of the q list `name:`, including its indented continuation
    lines (horizon.q's optional_keys runs onto a second line)."""
    m = re.search(rf"^{_OWNER}{name}:(.*(?:\n[ \t]+\S.*)*)", source, re.MULTILINE)
    if not m:
        raise SystemExit(f"{name} not found - has the declaring file changed shape?")
    return re.findall(r"`(\w+)", m.group(1))


def section_keys(page: str, heading: str) -> list[str]:
    """Keys in the first table under `heading`, up to the next heading."""
    start = page.index(heading)
    body = page[start + len(heading) :]
    end = re.search(r"^#{2,3} ", body, re.MULTILINE)
    body = body[: end.start()] if end else body
    return [m.group(1) for line in body.splitlines() if (m := _ROW_KEY.match(line))]


def check(page: str, core: Path) -> list[str]:
    """Every disagreement between `page` and the declaring files in `core`."""
    problems: list[str] = []
    # A kind that forwards `.qetl.job.stream.passthrough decl` accepts every
    # key stream_job.q lists in passthrough_keys without naming it (#1232).
    passthrough = set(q_list((core / "stream_job.q").read_text(), "passthrough_keys"))
    for heading, (file, lists, derived) in BLOCKS.items():
        if heading not in page:
            problems.append(f"{PAGE.name}: no section starting '{heading}'")
            continue
        source = "\n".join(f.read_text() for f in sorted(core.glob(file)))
        forwarded = passthrough if ".qetl.job.stream.passthrough" in source else set()
        rows = section_keys(page, heading)
        listed = {k for name in lists for k in q_list(source, name)} - derived
        for k in sorted(listed - set(rows)):
            problems.append(f"{heading[3:]}: {file} declares `{k}` but the page has no row for it")
        for k in rows:
            if k not in forwarded and not re.search(rf"`{k}\b", source):
                problems.append(
                    f"{heading[3:]}: the page describes `{k}`, which {file} never mentions"
                )
    return problems
