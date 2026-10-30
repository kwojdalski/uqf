"""Generate src/etl/generated/plant_schema.json from what q evaluates (#1781).

The tree's plant tables, their ordered columns, q type characters, attributes,
nested element types and sort rules - exported by q from plant_tables.q
(scripts/generate/export_plant_schema.q) so Python never reads them out of the
q text. The work is uqs.model.plant_metadata_gen's, which a bundle install calls
too; this is the script form the hook and `uqs job new|remove|install` run.

Runs on KDB-X or PeachQ - the exporter loads plant_tables.q alone, which both
load - so CI's PeachQ lane regenerates and compares it as well.

    uv run python scripts/generate/plant_schema.py           # write
    uv run python scripts/generate/plant_schema.py --check   # fail if stale
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python" / "uqs" / "src"))

from uqs.model import plant_metadata_gen  # noqa: E402

if __name__ == "__main__":
    sys.exit(plant_metadata_gen.main(["--root", str(REPO), *sys.argv[1:]]))
