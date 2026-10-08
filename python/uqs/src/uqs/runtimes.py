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
    #: its schema, and the commands that work on them (UQF_ONLY_COMMANDS
    #: below) run. Profiles are defined against the full set, so a
    #: runtime without it checks each profile's processes before a start.
    pipelines: bool
    #: This tree's layers over the vendored processes: the row overlays and
    #: stp1's composed schema (stack/procs.py), the service config and code
    #: layers (KDBSERVCONFIG/KDBSERVCODE, which carry the query policies),
    #: gateway1's access list, DQE's metatable queries and monitor1's
    #: connection budget. Without it the starter pack runs as it ships.
    overlays: bool
    #: KDBBASEPORT when `--port` is not given: every process listens on it
    #: plus its offset (scripts/processes/process_ports.csv). Each runtime's
    #: span of ports is clear of every other's, so two runtimes run side by
    #: side with no flags (#761); `--port` still moves one anywhere.
    base_port: int
    #: Which of this tree's pipelines a runtime with `pipelines` has: None
    #: for every one, or a profile's name for that profile's processes and
    #: everything they depend on in the job graph (model/runtime_members.py).
    #: The subset is derived, never listed, so a dependency added to a job
    #: joins every runtime that includes the job (#760).
    profile: str | None = None
    #: The profiles `--profile` takes, and `uqs list profiles` shows, on this
    #: runtime. Declared, not filtered from uqf's (#765): a profile listed
    #: here that names a process the runtime lacks fails test_profiles,
    #: rather than an operator's start.
    profiles: tuple[str, ...] = ()
    #: The q implementation the stack runs on: `kdbx`, the reference, or
    #: `peachq`. A PeachQ runtime gets its binary from scripts/peachq.py and
    #: UQF_Q_IMPL=peachq for its own stack only - nothing global, nothing on
    #: PATH - and no licence budget, PeachQ having no connection cap (#764).
    interpreter: str = "kdbx"
    #: Which q the stack's processes load. `source` is this tree as written.
    #: `flattened` is a copy bootstrap prepares under the runtime's data
    #: directory, converted by scripts/portable/flatten_contexts.py so it
    #: needs no nested `\d` - the one thing that keeps the ETL tree off
    #: PeachQ (stack/qtree.py). The checkout itself is never rewritten.
    q_tree: str = "source"
    #: Not yet supported: listed and runnable, said to be experimental
    #: wherever the runtime is shown, and refused nothing it can do.
    experimental: bool = False

    def __post_init__(self) -> None:
        if self.interpreter not in ("kdbx", "peachq"):
            raise ValueError(
                f"{self.name}: interpreter is kdbx or peachq, not {self.interpreter!r}"
            )
        if self.q_tree not in ("source", "flattened"):
            raise ValueError(f"{self.name}: q_tree is source or flattened, not {self.q_tree!r}")
        # PeachQ cannot load a nested `\d` namespace (peachq-org/peachq#80,
        # #511), and the ETL tree is built of them: a pipeline process would
        # die loading the source. Only the flattened tree can carry them, and
        # only as an experiment, until a full fleet is proven there.
        if self.interpreter == "peachq" and self.pipelines and self.q_tree != "flattened":
            raise ValueError(
                f"{self.name}: a PeachQ runtime cannot load this tree's pipelines as written - "
                "PeachQ cannot load the nested ETL namespaces (peachq-org/peachq#80); "
                "declare it with pipelines=False, or with q_tree='flattened'"
            )
        if self.interpreter == "peachq" and self.pipelines and not self.experimental:
            raise ValueError(
                f"{self.name}: pipelines on PeachQ are experimental - declare experimental=True"
            )

    def resolve_base_port(self, base_port: int | None) -> int:
        """KDBBASEPORT: `base_port` when given (`--port`), else this runtime's."""
        return self.base_port if base_port is None else base_port


#: Every runtime, by name. The first is the default.
#:
#: uqf - the starter pack plus everything this tree adds. What `uqs` has
#: always run.
#:
#: torq - the starter pack as it ships, and nothing of this tree's: its own
#: process.csv, its own database.q (trade, quote, packets), its own feed1.
#: For seeing TorQ itself, or telling whether a problem is TorQ's or uqf's.
#:
#: peachq - torq's stack on PeachQ, the MIT-licensed interpreter: the
#: starter pack as it ships, with no connection cap and no licence needed.
#:
#: crypto, fx - the starter pack with this tree's layers and ONE profile's
#: pipelines: their processes, the tables they read and write, and an HDB
#: of their own. A focused stack that fits the licence's connection cap
#: with room to spare and holds no table it never writes.
#:
#: peachq-etl - EXPERIMENTAL: peachq's capture stack with this tree's layers
#: and the sidecar bundles declared for it (stack/runtime_bundles.py), their
#: jobs and what they depend on, loaded from a flattened copy of the q tree.
#: No uqf pipeline of its own: a bundle's jobs are what it is for.
RUNTIMES: dict[str, Runtime] = {
    runtime.name: runtime
    for runtime in (
        Runtime(
            name="uqf",
            description="the starter pack plus this tree's pipelines, tables and config layers",
            data_dir="uqs",
            pipelines=True,
            overlays=True,
            base_port=6050,
            profiles=("all", "arbitrage", "crypto", "default", "depth", "essential", "fx"),
        ),
        Runtime(
            name="torq",
            description="the starter pack as it ships: its processes and tables, nothing of uqf's",
            data_dir="uqs-torq",
            pipelines=False,
            overlays=False,
            base_port=6150,
            profiles=("essential", "feed", "full"),
        ),
        Runtime(
            name="peachq",
            description="the starter pack as it ships, on PeachQ rather than KDB-X",
            data_dir="uqs-peachq",
            pipelines=False,
            overlays=False,
            base_port=6450,
            profiles=("capture",),
            interpreter="peachq",
        ),
        Runtime(
            name="crypto",
            description="the starter pack plus the crypto profile's pipelines and their tables",
            data_dir="uqs-crypto",
            pipelines=True,
            overlays=True,
            base_port=6250,
            profile="crypto",
            profiles=("crypto", "essential"),
        ),
        Runtime(
            name="fx",
            description="the starter pack plus the fx profile's pipelines and their tables",
            data_dir="uqs-fx",
            pipelines=True,
            overlays=True,
            base_port=6350,
            profile="fx",
            profiles=("essential", "fx"),
        ),
        Runtime(
            name="peachq-etl",
            description="EXPERIMENTAL: peachq's capture stack plus its declared sidecar "
            "bundles' jobs, on PeachQ, from a flattened copy of the q tree",
            data_dir="uqs-peachq-etl",
            pipelines=True,
            overlays=True,
            base_port=6550,
            profile="capture",
            profiles=("capture",),
            interpreter="peachq",
            q_tree="flattened",
            experimental=True,
        ),
    )
}
DEFAULT_RUNTIME = next(iter(RUNTIMES))


#: Commands that work only on this tree's own pipelines, and what each needs
#: that a runtime without them (`pipelines=False`, such as torq) does not
#: have. Refused there with the reason, rather than left to fail against
#: processes and tables that do not exist.
UQF_ONLY_COMMANDS: dict[str, str] = {
    "backfill": "runs this tree's bounded workers",
    "gaps": "reads this tree's streaming jobs' coverage",
    "graph": "draws this tree's pipeline declarations",
    "run": "reads this tree's run ledger",
    "stream": "previews this tree's streaming jobs",
    "feed": "publishes this tree's tables into the plant",
}
