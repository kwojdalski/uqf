"""The runtimes uqs can build and run, and how one is chosen.

A runtime is which stack EXISTS: which processes and tables are defined at
all, and which of this tree's layers sit on the starter pack. (A profile is
which part of that stack to start - see model/profiles.py.)

Each runtime is a declaration, and every place that composes a stack reads
a field of it rather than asking which runtime it is: a new runtime is one
entry in RUNTIMES, not a branch at every site (#759).

Apart from paths.py, which builds each runtime's paths, because the CLI's
global --runtime option offers them before any path is built.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The environment variable naming the runtime uqs builds and runs: what
#: `uqs --runtime` sets, so every command and every process it starts sees
#: the same choice.
RUNTIME_ENV = "UQS_RUNTIME"


@dataclass(frozen=True)
class Runtime:
    """What one runtime composes from the starter pack and this tree."""

    name: str
    #: One line for --help and listings.
    description: str
    #: The data directory under output/. Each runtime has its own: an HDB
    #: one runtime wrote holds tables another does not declare.
    data_dir: str
    #: This tree's pipelines are processes in the stack, their tables are in
    #: its schema, and the commands that work on them (UQF_ONLY_COMMANDS in
    #: cli/shared.py) run. Profiles are defined against the full set, so a
    #: runtime without it checks each profile's processes before a start.
    pipelines: bool
    #: This tree's layers over the vendored processes: the row overlays and
    #: stp1's composed schema (stack/procs.py), the service config and code
    #: layers (KDBSERVCONFIG/KDBSERVCODE, which carry the query policies),
    #: gateway1's access list, DQE's metatable queries and monitor1's
    #: connection budget. Without it the starter pack runs as it ships.
    overlays: bool


#: Every runtime, by name. The first is the default.
#:
#: uqf - the starter pack plus everything this tree adds. What `uqs` has
#: always run.
#:
#: torq - the starter pack as it ships, and nothing of this tree's: its own
#: process.csv, its own database.q (trade, quote, packets), its own feed1.
#: For seeing TorQ itself, or telling whether a problem is TorQ's or uqf's.
RUNTIMES: dict[str, Runtime] = {
    runtime.name: runtime
    for runtime in (
        Runtime(
            name="uqf",
            description="the starter pack plus this tree's pipelines, tables and config layers",
            data_dir="uqs",
            pipelines=True,
            overlays=True,
        ),
        Runtime(
            name="torq",
            description="the starter pack as it ships: its processes and tables, nothing of uqf's",
            data_dir="uqs-torq",
            pipelines=False,
            overlays=False,
        ),
    )
}
DEFAULT_RUNTIME = next(iter(RUNTIMES))
