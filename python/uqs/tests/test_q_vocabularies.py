"""Python's copies of q's vocabularies, pinned to the q source (#609).

The copies stay: uqs must work without starting q. What these tests hold is
that they agree - changing a level or a trace field in q alone fails here,
naming the Python copy that would now mislabel or drop it. That happened once
already: `uqs logs` did not know DBG/TRC and showed them as INFO (#598).
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.stack import logs
from uqs.stack.trace_render import CODE_FIELDS

UQF_ROOT = Path(__file__).resolve().parents[3]
LOG_Q = UQF_ROOT / "src" / "etl" / "core" / "log.q"


def q_levels() -> list[str]:
    line = next(ln for ln in LOG_Q.read_text().splitlines() if ln.startswith("levels:"))
    return re.findall(r"`(\w+)", line)


def test_every_level_q_logs_is_one_uqs_logs_maps():
    """An unmapped level falls through to INFO: labelled INFO, and kept by
    `--level INFO` however quiet it was meant to be."""
    levels = q_levels()
    assert levels, "log.q's `levels:` is no longer spelled the way this test reads it"
    unmapped = [lv for lv in levels if lv not in logs._LOGURU_LEVEL]
    assert not unmapped, f"stack/logs.py _LOGURU_LEVEL has no entry for q's {unmapped}"


def test_every_mapped_level_has_a_place_in_the_order():
    """`--level` filters by _LEVEL_ORDER; a mapped level missing from it ranks
    0 and is shown under every filter."""
    missing = set(logs._LOGURU_LEVEL.values()) - set(logs._LEVEL_ORDER)
    assert not missing, f"_LEVEL_ORDER has no rank for {missing}"


def test_q_levels_keep_their_order_once_mapped():
    """q lists its levels least to most severe; mapped, they must still rank
    that way, or `--level DEBUG` would keep what q considers quieter."""
    ranks = [logs._LEVEL_ORDER[logs._LOGURU_LEVEL[lv]] for lv in q_levels()]
    assert ranks == sorted(ranks), f"q's {q_levels()} rank {ranks} once mapped"


def test_the_level_choices_are_derived_from_the_order():
    """The `--level` completion and help on `logs` and `up` read these, so no
    command restates the list."""
    assert logs.LEVEL_CHOICES == tuple(sorted(logs._LEVEL_ORDER, key=logs._LEVEL_ORDER.__getitem__))
    assert all(choice in logs.LEVEL_HELP for choice in logs.LEVEL_CHOICES)


def test_every_traced_code_field_is_one_q_writes():
    """trace_render shows a trace event's code as a block, by event text and
    field name. Renaming either in q alone would stop the block rendering, and
    nothing else would notice."""
    sources = [
        p.read_text() for p in (UQF_ROOT / "src").rglob("*.q") if ".qetl.log.trc" in p.read_text()
    ]
    for event, field in CODE_FIELDS.items():
        call = re.compile(rf'"{re.escape(event)}";\s*\(?\s*\(?enlist\[`{re.escape(field)}\]', re.S)
        assert any(call.search(src) for src in sources), (
            f'no q .qetl.log.trc call logs "{event}" with a `{field} field - '
            "trace_render.CODE_FIELDS would never render it"
        )
