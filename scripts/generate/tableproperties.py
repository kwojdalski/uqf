"""Generate scripts/torqconfig/dataaccess/tableproperties.csv (#1363).

What a desk may browse is the plant tables plus the datasets bounded workers
write, and both are already declared - the first in src/etl/plant_tables.q, the
second in the workers' own declarations, reached through generated/q_facts.py.
The file TorQ's getdata reads used to be five hand-written rows; a browsable
table without a row is refused by TorQ's checktablename, so the catalog could
offer what getdata would not serve.

The rows come from uqs.stack.gateway_access.table_properties_rows; the one
plant table whose instrument column is not `sym` (order_quality) is named there,
with the check that the column exists. `--check` fails when the committed file
differs, so a table added to either source fails the commit until this runs.

    uv run python scripts/generate/tableproperties.py           # write
    uv run python scripts/generate/tableproperties.py --check   # fail if stale
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))

from uqs.stack.gateway_access import TABLE_PROPERTIES, table_properties_rows  # noqa: E402

OUT = REPO / "scripts" / TABLE_PROPERTIES


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = ap.parse_args()
    generated = "\n".join(table_properties_rows()) + "\n"
    if not args.check:
        OUT.write_text(generated)
        print(f"wrote {OUT.relative_to(REPO)}")
        return 0
    committed = OUT.read_text() if OUT.is_file() else ""
    if committed == generated:
        print(f"{OUT.relative_to(REPO)} matches the plant tables and the workers' datasets")
        return 0
    print(
        f"{OUT.relative_to(REPO)} is stale - rerun scripts/generate/tableproperties.py:",
        file=sys.stderr,
    )
    sys.stderr.writelines(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile="committed",
            tofile="generated",
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
