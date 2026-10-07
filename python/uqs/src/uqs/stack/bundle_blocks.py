"""The `/ BEGIN bundle <name>` ... `/ END bundle <name>` blocks a bundle's
additions live in, inside tree files that are otherwise hand-written
(src/etl/plant_tables.q, scripts/processes/uqs_catalog.q).

A block is the unit of idempotence: a reinstall replaces the bundle's block in
place, so installing twice never appends twice, and an upgrade that drops a
table drops its line. Comments, because both files are q: `/ ` followed by
text is a line comment, where a line of only `/` would open a block comment.
"""

from __future__ import annotations

from uqs.paths import UqsError


class BundleError(UqsError):
    """A bundle that cannot be installed as it stands; nothing was written."""


def _markers(name: str) -> tuple[str, str]:
    return f"/ BEGIN bundle {name}", f"/ END bundle {name}"


def _span(lines: list[str], name: str, what: str) -> tuple[int, int] | None:
    begin, end = _markers(name)
    starts = [i for i, ln in enumerate(lines) if ln.rstrip() == begin]
    ends = [i for i, ln in enumerate(lines) if ln.rstrip() == end]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise BundleError(f"{what}: bundle {name}'s BEGIN/END markers are damaged - fix by hand")
    return starts[0], ends[0]


def without_block(text: str, name: str, what: str) -> str:
    """`text` with bundle `name`'s block taken out."""
    lines = text.splitlines()
    span = _span(lines, name, what)
    if span is None:
        return text
    del lines[span[0] : span[1] + 1]
    return "\n".join(lines) + "\n"


def with_block(text: str, name: str, body: list[str], what: str) -> str:
    """`text` with bundle `name`'s block set to `body`: replaced where it is,
    appended at the end the first time, removed when `body` is empty."""
    lines = text.splitlines()
    span = _span(lines, name, what)
    block = [*_markers(name)[:1], *body, _markers(name)[1]] if body else []
    if span is not None:
        lines[span[0] : span[1] + 1] = block
    elif block:
        lines += ["", *block] if lines and lines[-1].strip() else block
    return "\n".join(lines) + "\n"
