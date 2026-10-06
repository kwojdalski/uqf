"""The runtimes uqs can build and run, and how one is chosen.

Apart from paths.py, which builds each runtime's paths, because the CLI's
global --runtime option offers them before any path is built.
"""

from __future__ import annotations

#: The environment variable naming the runtime uqs builds and runs: what
#: `uqs --runtime` sets, so every command and every process it starts sees
#: the same choice.
RUNTIME_ENV = "UQS_RUNTIME"

#: What each runtime is, by name. The first is the default.
#:
#: uqf - the starter pack plus everything this tree adds: its pipelines and
#: their tables, the overlays on vendored processes, the service config and
#: code layers. What `uqs` has always run.
#:
#: torq - the starter pack as it ships, and nothing of this tree's: its own
#: process.csv, its own database.q (trade, quote, packets), its own feed1,
#: and no KDBSERVCONFIG or KDBSERVCODE. For seeing TorQ itself, or telling
#: whether a problem is TorQ's or this tree's.
RUNTIMES: dict[str, str] = {
    "uqf": "the starter pack plus this tree's pipelines, tables and config layers",
    "torq": "the starter pack as it ships: its processes and tables, nothing of uqf's",
}
DEFAULT_RUNTIME = "uqf"
