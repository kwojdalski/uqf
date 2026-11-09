"""Render housekeeping1's rules and schedule into scripts/torqconfig (#2150).

The rules are declared in python/uqs/src/uqs/stack/housekeeping.py, and
bootstrap writes them into TORQDATA, where housekeeping1 reads them. This
renders the same text into the tree - housekeeping.csv and
housekeeping_schedule.csv - so a change to what housekeeping removes, zips or
compresses is reviewed as the files housekeeping1 will read. Nothing here
knows a rule: it asks the module that declares them.

It is derive.py's `housekeeping-config` target:

    uv run python scripts/generate/derive.py housekeeping-config           # write
    uv run python scripts/generate/derive.py housekeeping-config --check   # fail if stale

Needs no q.
"""

from __future__ import annotations

from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "torqconfig"


def render() -> dict[Path, str]:
    """Each committed file, and the text bootstrap writes for it."""
    from uqs.stack import housekeeping

    return {
        OUT_DIR / "housekeeping.csv": housekeeping.render_housekeeping_csv(),
        OUT_DIR / "housekeeping_schedule.csv": housekeeping.render_housekeeping_schedule(),
    }
