"""Which partitions an HDB has and which tables each holds - as q finds them (#1783).

`uqs data check` judges each partition against the declared schema
(stack/hdb_shape.py), so it has to know what the partitions are. It used to
list the root's date-named directories, which is a conventional database's
layout and not a segmented one's: there the root holds `par.txt`, the
partitions live in the segment directories it names, and the listing found
none - so the check reported a database whole without reading any of it.

The authority on which partitions a database has is q's load, so this asks it:
scripts/gates/hdb_layout.q loads the root and reads `.Q.pv` and `.Q.pd` back,
in ONE q process whatever the number of partitions, and prints them as JSON.
Nothing here restates q's segmented-database rules. A date held by two
segments is two partitions to q - every query reads both - so it is two here,
each named by the directory that holds it (`<segment>/<date>`); a conventional
database's partitions keep their bare date.

What q will not say, the script does: a segment par.txt names that does not
exist (q loads the others in silence), one it cannot read, partition
directories beside par.txt (q refuses the load). Each is a refusal naming it,
as is any run that does not finish - never an empty list of partitions, which
would read as a database with nothing wrong.

Without q, or with PeachQ (whose `\\l` maps no partitioned database), the
filesystem listing in stack/hdb_shape.py stands in, and it reads only a
conventional root: a segmented one is refused there rather than judged by its
root alone.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from uqs.interpreter import PEACHQ, identified, q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_shape
from uqs.stack.env import with_interpreter

SCRIPT = Path("gates") / "hdb_layout.q"
DONE = "DONE"
PREFIX = "layout|"


@dataclass(frozen=True)
class Layout:
    """An HDB as inspected: its partitions, keyed as `listing` (hdb_shape.Listing),
    and the directory each one is - what the type check reads."""

    listing: hdb_shape.Listing
    dirs: dict[str, Path]
    segmented: bool
    #: q's own table list once loaded - None when no q was asked
    tables: list[str] | None
    #: "q", or "filesystem" for a conventional root read without one
    source: str


def _key(part: dict, segmented: bool) -> str:
    return f"{part['dir']}/{part['partition']}" if segmented else part["partition"]


def parse(stdout: str, stderr: str, hdb_root: Path) -> Layout:
    """hdb_layout.q's report, as a Layout; a refusal for any problem it names,
    and for a run that did not finish."""
    lines = stdout.splitlines()
    found = [line for line in lines if line.startswith(PREFIX)]
    if DONE not in lines or not found:
        said = (stderr or stdout).strip().splitlines()[-3:]
        raise UqsError(f"the HDB layout inspection of {hdb_root} did not finish: {' '.join(said)}")
    doc = json.loads(found[-1][len(PREFIX) :])
    if doc["problems"]:
        raise UqsError(
            f"the HDB at {hdb_root} cannot be inspected whole: " + "; ".join(doc["problems"])
        )
    segmented = bool(doc["segmented"])
    listing: hdb_shape.Listing = {}
    dirs: dict[str, Path] = {}
    for part in doc["partitions"]:
        key = _key(part, segmented)
        listing[key] = {
            table: sorted(hdb_shape.column_names(files))
            for table, files in (part["tables"] or {}).items()
        }
        dirs[key] = Path(part["dir"]) / part["partition"]
    return Layout(listing, dirs, segmented, list(doc["tables"]), "q")


def inspect(paths: UqsPaths, hdb_root: Path) -> Layout:
    """The HDB at `hdb_root`, as q loads it: one q process."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None or identified(q) == PEACHQ:
        listing = hdb_shape.listing(hdb_root)
        dirs = {name: hdb_root / name for name in listing}
        return Layout(listing, dirs, False, None, "filesystem")
    r = subprocess.run(
        [str(q), str(paths.scripts_dir / SCRIPT), str(hdb_root), "-q"],
        capture_output=True,
        text=True,
        check=False,
        cwd=paths.repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )
    return parse(r.stdout, r.stderr, hdb_root)
