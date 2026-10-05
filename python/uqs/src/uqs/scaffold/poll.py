"""`uqs job new --poll`: a feed scaffolded as polling steps, not one timer (#668).

A feed scaffolded the ordinary way gets an `on_timer` that does everything -
fetch, normalize, publish, save the cursor - so nothing can run its steps
apart, and `uqs stream preview` refuses it. With `--poll` the feed gets the
steps instead, declared as `poll`: the framework builds the timer from them
(.qetl.job.stream.tick) and the preview runs the same fetch and normalize
with nothing published or saved. So no timer is written here at all.

`--cursor-fields a,b` makes the cursor the row's position rather than a
timestamp, for a source that pages through rows sharing one: the load, save
and advances trio is written whole, from the framework's stock helpers, and
next_cursor takes those fields of the page's last row.

The steps that read the source throw "not implemented" until written. A
scaffold that fetched a fixture instead would preview, and run, as if it
were live.
"""

from __future__ import annotations

import re

from uqs.paths import UqsError

#: A q column name: the source's own spelling, so `securityId` is fine.
_COLUMN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def cursor_fields(raw: str | None) -> list[str]:
    """`--cursor-fields`, as names; empty when not given."""
    fields = [f.strip() for f in (raw or "").split(",") if f.strip()]
    bad = [f for f in fields if not _COLUMN.match(f)]
    if bad:
        raise UqsError(f"--cursor-fields: {', '.join(bad)} is not a q column name")
    if len(set(fields)) != len(fields):
        raise UqsError("--cursor-fields names a field twice")
    return fields


def declaration(name: str, tick: str, fields: list[str]) -> tuple[str, str]:
    """The `period`poll keys and their values, in the scaffold's layout."""
    ns = f".qpipe.job.{name}"
    steps = ["fetch", "normalize", "next_cursor"]
    values = [f"{ns}.{step}" for step in steps]
    if fields:
        steps += ["load", "save", "advances"]
        values += [
            ".qetl.job.continuous.load_cursor_value",
            ".qetl.job.continuous.save_cursor_value",
            f".qetl.job.continuous.lexically_after[{_symbols(fields)}]",
        ]
    keys = "`period`poll"
    poll = "`" + "`".join(steps) + "!(\n        " + ";\n        ".join(values) + ")"
    return keys, f"\n    {tick};\n    {poll};"


def steps(name: str, publishes: str, fields: list[str]) -> str:
    """The step functions, inside the job's namespace."""
    cursor = (
        f"the page's position, its {', '.join(fields)} - a first run's is (::)"
        if fields
        else "a timestamp - null on a first run"
    )
    if fields:
        named = ", ".join(fields)
        next_cursor = f"""/ [page] -> the cursor that acknowledges the page: the {named} of its
/ last row, in the order the source sorts by. Keep every field: dropping a
/ tie-breaker loses the rows that share the rest.
next_cursor:{{[page] {_symbols(fields)}#last page}}"""
    else:
        next_cursor = f"""/ [page] -> the cursor that acknowledges the page, e.g. the time of its
/ last row - strictly later than the cursor it was fetched after.
next_cursor:{{[page]
    '"{name}.next_cursor: not implemented";
    }}"""
    return f"""/ SCAFFOLDED. A polling feed: these steps, not one on_timer. The framework's
/ timer fetches the page after the cursor, normalizes it, publishes it and
/ then saves the cursor; `uqs stream preview {name}` runs the same fetch and
/ normalize, publishing nothing and saving nothing. Each step throws until
/ written - a fetch that returned a fixture would preview as live data.

/ [cursor] -> the page after `cursor`, a table: {cursor}.
fetch:{{[cursor]
    '"{name}.fetch: not implemented";
    }}

/ [page] -> the rows published to {publishes}, in the plant table's columns,
/ without `time` (the tickerplant stamps it).
normalize:{{[page]
    '"{name}.normalize: not implemented";
    }}

{next_cursor}"""


def _symbols(names: list[str]) -> str:
    """A q symbol list literal: `a`b, or enlist `a for one."""
    return f"enlist `{names[0]}" if len(names) == 1 else "`" + "`".join(names)
