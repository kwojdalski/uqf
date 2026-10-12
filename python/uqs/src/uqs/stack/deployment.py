"""The deployment set: which process.csv rows this stack's TorQ discovers (#1207).

process.csv lists every process uqs knows, started or not - torq.sh starts
from it, so `uqs start <name>` must find any of them there. TorQ's discovery
service registers every row it reads and hands them all out, so under the
`essential` profile sort1 is told about sortworker2, which nothing started,
and retries its port every few seconds for as long as the stack runs.

So the file this module writes names the processes this deployment runs, and
scripts/torqcode/handlers/deployment.q - loaded into every TorQ process -
drops the other process.csv rows before they reach .servers.SERVERS. A row
that does get in is retried and reconnected exactly as TorQ does it.

WHAT IS IN IT. A profile start REPLACES it with what the profile resolved to,
named extras included (start_policy.resolve_profiles). Every later start ADDS
to it - the names started and, for this tree's jobs, what they read
(profiles.closure) - so `uqs start sortworker2` makes sortworker2 discoverable
from then on, to processes already running as well as new ones: the q side
reads the file on every registration. `all` adds every startwithall=1 row.

A stack never started from a profile has no file, and the q side then filters
nothing: TorQ discovers exactly as it always has. Stopping a process leaves it
in the set - a selected process that goes down is a failure to retry and
report, not a deselection.
"""

from __future__ import annotations

from collections.abc import Iterable

from uqs.model import profiles
from uqs.paths import UqsPaths
from uqs.stack.procs import effective_process_rows


def members(paths: UqsPaths) -> tuple[str, ...] | None:
    """The deployment set as written, or None when no profile start wrote one."""
    try:
        text = paths.generated_deployment.read_text()
    except FileNotFoundError:
        return None
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _write(paths: UqsPaths, procnames: Iterable[str]) -> None:
    paths.generated_deployment.parent.mkdir(parents=True, exist_ok=True)
    names = list(dict.fromkeys(procnames))
    paths.generated_deployment.write_text("".join(f"{name}\n" for name in names))


def select(paths: UqsPaths, procnames: Iterable[str]) -> None:
    """A profile start: the set is exactly `procnames`, what it resolved to."""
    _write(paths, procnames)


def admit(paths: UqsPaths, procs: str) -> None:
    """Any other start: add what `procs` starts - and what it reads - to a set
    a profile start wrote. No set, nothing to add to: no filter either."""
    current = members(paths)
    if current is None:
        return
    names = procs.split()
    if "all" in names:
        rows = effective_process_rows(paths)
        names = [row["procname"] for row in rows if row.get("startwithall") == "1"]
    added = sorted(profiles.closure(names) - set(current))
    if added:
        _write(paths, (*current, *added))


def record(paths: UqsPaths, procs: str, selected: Iterable[str] | None = None) -> None:
    """What a start does to the set: `selected` replaces it (a profile start),
    otherwise `procs` joins it."""
    if selected is not None:
        select(paths, selected)
    else:
        admit(paths, procs)
