"""Replaying a tickerplant log into the HDB, aimed by what is RUNNING.

TorQ already does the replay: `tickerlogreplay.q` (the `tpreplay1` process,
`startwithall=0` because nothing should replay a log on a routine start) reads
a segmented log directory and writes down partitions, driven entirely by
`-.replay.*` switches on its start line. This module does not reimplement any
of that. It answers the question the switches leave to the operator - WHICH
log, into WHICH database, under WHICH schema - and it answers it from the
processes that are up rather than from the configuration that describes them.

WHY THE RUNNING PROCESS AND NOT THE CONFIG. The two disagree, and the
disagreement is silent. This tree's data directory moved from
`scripts/output/uqf-stack` to `output/uqs` (prerequisites._FORMER_DATA_DIRS), so a
plant started before the move still writes its log under the old path while
every config-derived answer names the new one. A replay aimed by config would
have found a log directory, replayed it without complaint, and written down a
day nobody asked for. The plant's own start line cannot drift from the plant:
`-tplogdir`, `-schemafile` and `-stackid` are on it, and `ps` reports them.

The stack's base port is the one exception. `--base-port` defaults to the
runtime's own, on this command as on every other (#1855) - one option, one
default - and the plant's `-stackid` is checked against it: a plant running
under another base port is refused, naming the `--base-port` that replays it,
rather than replayed under a port the operator never chose.

WHICH DAYS MAY BE REPLAYED (#2196). Only a day end of day has handed to the
HDB. The plant's live day is the rdb's and the wdb's to write: a replay of it
puts a partial partition in hdb/D, and the wdb's `movetohdb` then aborts the
move of the full day ("present in both location"). The rule is the backfill
writer's own (`.qetl.io.require_handed_over`, #2186), asked of q rather than
restated here: today and later, and any day the wdb's save directory still
holds, are refused. So the default is the newest log day that IS handed over,
not the newest log.

WHAT IS DELIBERATELY NOT DECIDED HERE. `emptytables`, `clean`, `sortafterreplay`
and the rest keep TorQ's defaults, which means the replay EMPTIES the tables it
is about to write in the partitions it touches. That is what a tickerplant
replay is, and changing it under a friendlier default would make `uqs data
replay` something other than the thing it wraps - so the flags are passed
through instead, and `--dry-run` exists to show what a run would do first.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import runtime
from uqs.stack.alive import _command_lines
from uqs.stack.replay_fence import replayable_days, require_handed_over
from uqs.stack.replay_flags import replay_flags

log = get_logger()

#: The process that performs the replay. Vendored, `startwithall=0`.
REPLAY_PROCNAME = "tpreplay1"

#: proctypes that own a tickerplant log. A chained plant (`sctp1`) is included
#: because it can be configured to write its own, but it usually is not - and
#: one that is not carries no `-tplogdir`, which is what `running_plants`
#: filters on. So the list is permissive and the command line decides.
PLANT_PROCTYPES = ("segmentedtickerplant", "tickerplant", "segmentedchainedtickerplant")

#: The process whose `-load` is the HDB directory to write into.
HDB_PROCTYPE = "hdb"

#: A segmented log directory: `<procname>_<YYYY.MM.DD>`, one per day, holding
#: a file per table per period plus the `stpmeta` table that names them.
_LOG_DIR = re.compile(r"^(?P<procname>.+)_(?P<date>\d{4}\.\d{2}\.\d{2})$")


def _switch(command: str, name: str) -> str | None:
    """The value of `-name` on a command line, or None when it is not there.

    Split on whitespace rather than regex-matched, so a value is only ever the
    single word that follows the switch - which is what q's own `.Q.opt` would
    take from the same line.
    """
    words = command.split()
    try:
        index = words.index(f"-{name}")
    except ValueError:
        return None
    value = words[index + 1 : index + 2]
    return value[0] if value and not value[0].startswith("-") else None


@dataclass(frozen=True)
class RunningPlant:
    """A tickerplant that is up, as its own start line describes it."""

    procname: str
    proctype: str
    pid: int
    #: `-stackid`: the base port the whole fleet was started under.
    base_port: int
    #: `-tplogdir`: the directory it writes its segmented log into.
    tplogdir: Path
    #: `-schemafile`: the schema the log's tables were declared with. The
    #: replay has to load the same one or it writes down a different shape.
    schemafile: Path | None


def running_plants(timeout: float | None = None) -> list[RunningPlant]:
    """Every tickerplant on this machine that is up AND writing a log.

    A plant with no `-tplogdir` is skipped rather than reported: there is
    nothing to replay from it, and offering it as a choice would only produce
    a refusal one step later.
    """
    plants = []
    for pid, command in _command_lines(timeout):
        proctype = _switch(command, "proctype")
        tplogdir = _switch(command, "tplogdir")
        procname = _switch(command, "procname")
        stackid = _switch(command, "stackid")
        if proctype not in PLANT_PROCTYPES or not (tplogdir and procname and stackid):
            continue
        schemafile = _switch(command, "schemafile")
        plants.append(
            RunningPlant(
                procname=procname,
                proctype=proctype,
                pid=pid,
                base_port=int(stackid),
                tplogdir=Path(tplogdir),
                schemafile=Path(schemafile) if schemafile else None,
            )
        )
    return sorted(plants, key=lambda p: p.procname)


def resolve_plant(procname: str | None = None, timeout: float | None = None) -> RunningPlant:
    """The plant to replay from: the one that is up, or the named one.

    Refuses rather than guesses when several are up and none was named. Two
    plants write two different logs, and picking one by sort order would
    replay the wrong day's trades into the HDB as readily as the right one.
    """
    plants = running_plants(timeout)
    if procname is not None:
        for plant in plants:
            if plant.procname == procname:
                return plant
        running = ", ".join(p.procname for p in plants) or "none"
        raise UqsError(
            f"no tickerplant named {procname!r} is running with a log directory "
            f"(running plants: {running}) - `uqs status` shows the fleet"
        )
    if not plants:
        raise UqsError(
            "no tickerplant is running, so there is nothing to take a log directory "
            "from - start one with `uqs start stp1`, or name the log yourself with "
            "`uqs data replay --tplogdir <path>`"
        )
    if len(plants) > 1:
        names = ", ".join(p.procname for p in plants)
        raise UqsError(f"several tickerplants are running ({names}) - `uqs data replay PROC`")
    return plants[0]


def running_hdb_dir(base_port: int, timeout: float | None = None) -> Path:
    """The database directory of the HDB running under `base_port`.

    An HDB process is started with the database as its `-load`, so the
    directory it HAS open is on its command line. The same reasoning as the
    plant: a config-derived path can name a database this fleet is not using.
    """
    for _pid, command in _command_lines(timeout):
        if _switch(command, "proctype") != HDB_PROCTYPE:
            continue
        if _switch(command, "stackid") != str(base_port):
            continue
        load = _switch(command, "load")
        if load:
            return Path(load)
    raise UqsError(
        f"no hdb process is running under stackid {base_port}, so there is no database "
        "to write into - start one with `uqs start hdb1`, or name it with --hdb"
    )


def log_dirs(plant: RunningPlant) -> list[Path]:
    """The plant's own log directories, oldest first.

    Only the ones named for THIS plant: one `tplogdir` holds every plant's
    logs side by side, and replaying `sctp1_...` because it sorted last would
    be the same wrong-log mistake as reading the configured path.
    """
    if not plant.tplogdir.is_dir():
        raise UqsError(
            f"{plant.procname} is running with -tplogdir {plant.tplogdir}, which does not "
            "exist - the plant has written no log yet, or it cannot be read from here"
        )
    found = []
    for entry in plant.tplogdir.iterdir():
        match = _LOG_DIR.match(entry.name)
        if entry.is_dir() and match and match["procname"] == plant.procname:
            found.append((match["date"], entry))
    return [entry for _date, entry in sorted(found)]


def _log_date(directory: Path) -> str | None:
    """The `YYYY.MM.DD` a log directory is named for, or None."""
    match = _LOG_DIR.match(directory.name)
    return match["date"] if match else None


def resolve_log_dir(
    plant: RunningPlant, date: str | None = None, replayable: frozenset[str] | None = None
) -> Path:
    """The log directory to replay: the day asked for, or the plant's newest
    REPLAYABLE one.

    The newest log is today's while the plant is writing, and today is not the
    HDB's to take (#2196), so the default is the newest day in `replayable` -
    the dates end of day has handed over. `replayable` None means "no
    restriction", which is only right when nothing can be judged.
    """
    dirs = log_dirs(plant)
    if not dirs:
        raise UqsError(
            f"{plant.tplogdir} holds no log directory for {plant.procname} - it is named "
            f"{plant.procname}_<date>, and nothing there matches"
        )
    if date is None:
        eligible = [d for d in dirs if replayable is None or _log_date(d) in replayable]
        if not eligible:
            days = ", ".join(str(_log_date(d)) for d in dirs)
            raise UqsError(
                f"{plant.procname} has no log for a day end of day has handed over to the "
                f"HDB (it has: {days}) - the live day is the rdb's and the wdb's to write. "
                "Pass --date for a handed-over day, or --tplogdir for a log of your own"
            )
        return eligible[-1]
    wanted = plant.tplogdir / f"{plant.procname}_{date}"
    if wanted not in dirs:
        # Split rather than re-matched: every name here came back from
        # log_dirs, which only returns what already matched.
        days = ", ".join(d.name.rsplit("_", 1)[-1] for d in dirs)
        raise UqsError(f"{plant.procname} has no log for {date} - it has: {days}")
    return wanted


@dataclass(frozen=True)
class ReplayPlan:
    """Everything a replay was aimed at, and where each part came from.

    Carried as one object so `--dry-run` prints exactly what a real run would
    use - a dry run that recomputes its own answer is not a dry run.
    """

    log_dir: Path
    hdb_dir: Path
    schema_file: Path
    base_port: int
    tables: tuple[str, ...]
    #: procname -> what it contributed, for the printed plan. None when the
    #: operator supplied every path and no plant was consulted.
    plant: RunningPlant | None

    def flags(self) -> list[str]:
        return replay_flags(self.log_dir, self.hdb_dir, self.schema_file, list(self.tables))

    def rows(self) -> list[tuple[str, str, str]]:
        """(what, value, where it came from), for the printed plan."""
        source = f"{self.plant.procname} (pid {self.plant.pid})" if self.plant else "--tplogdir"
        return [
            ("log", str(self.log_dir), source),
            ("hdb", str(self.hdb_dir), "hdb process" if self.plant else "--hdb"),
            ("schema", str(self.schema_file), source if self.plant else "--schema-file"),
            ("base port", str(self.base_port), "-stackid" if self.plant else "--base-port"),
            ("tables", ", ".join(self.tables) if self.tables else "all", "--table"),
        ]


def plan(
    procname: str | None = None,
    date: str | None = None,
    log_dir: Path | None = None,
    hdb_dir: Path | None = None,
    schema_file: Path | None = None,
    tables: list[str] | None = None,
    base_port: int | None = None,
    timeout: float | None = None,
    base_port_given: bool = True,
    wdb_dir: Path | None = None,
) -> ReplayPlan:
    """Resolve what to replay, preferring what was asked for over what is up.

    Every argument is an override of something discoverable, so the running
    fleet is only consulted for the parts left unsaid - and a call that
    supplies `log_dir`, `hdb_dir`, `schema_file` and `base_port` touches no
    process at all, which is what makes the command usable on a stack that
    has already been stopped. A `base_port` that is only the runtime's default
    (`base_port_given` False) must be the running plant's `-stackid`.
    """
    needs_plant = log_dir is None or schema_file is None or base_port is None or hdb_dir is None
    plant = resolve_plant(procname, timeout) if needs_plant else None

    if log_dir is not None and date is not None:
        raise UqsError(
            "--date picks a day out of a plant's log directory, so it cannot be used with "
            "--tplogdir"
        )

    if plant is not None and base_port is not None and not base_port_given:
        if plant.base_port != base_port:
            raise UqsError(
                f"{plant.procname} runs under base port {plant.base_port}, not this "
                f"runtime's {base_port}: give --base-port {plant.base_port} to replay its log"
            )
    resolved_port = base_port if base_port is not None else (plant.base_port if plant else None)
    assert resolved_port is not None

    if hdb_dir is None:
        hdb_dir = running_hdb_dir(resolved_port, timeout)

    # Only a day end of day has handed over may be replayed (#2196): the live
    # day is the rdb's and the wdb's, and a partial hdb/D makes movetohdb abort.
    if log_dir is None:
        assert plant is not None
        if date is None:
            days = [str(_log_date(d)) for d in log_dirs(plant)]
            ok = replayable_days(days, hdb_dir, wdb_dir)
            log_dir = resolve_log_dir(plant, None, ok)
        else:
            require_handed_over(date, hdb_dir, wdb_dir)
            log_dir = resolve_log_dir(plant, date)
    elif (named := _LOG_DIR.match(log_dir.name)) is not None:
        # A directory named outright is judged by its name when it has one
        # of a plant's; any other name cannot be dated, so it is the operator's.
        require_handed_over(named["date"], hdb_dir, wdb_dir)

    if schema_file is None:
        assert plant is not None
        if plant.schemafile is None:
            raise UqsError(
                f"{plant.procname} is running without -schemafile, so the schema the log "
                "was written under is not known - name it with --schema-file"
            )
        schema_file = plant.schemafile

    return ReplayPlan(
        log_dir=log_dir,
        hdb_dir=hdb_dir,
        schema_file=schema_file,
        base_port=resolved_port,
        tables=tuple(tables or ()),
        plant=plant,
    )


def start(
    paths: UqsPaths, replay: ReplayPlan, *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Start tpreplay1 on `replay`, through torq.sh's own `-extras`.

    The same channel `uqs job backfill` uses: the flags are appended to the one
    start line torq.sh builds from process.csv, so the process starts the way
    every other one does - registered with discovery, logged where `uqs logs`
    looks - and exits when the replay is done (`exitwhencomplete`).
    """
    flags = replay.flags()
    log.debug(
        "replaying {} into {} under stackid {}", replay.log_dir, replay.hdb_dir, replay.base_port
    )
    return runtime.run_torq_sh(
        paths,
        ["start", REPLAY_PROCNAME, "-extras", *flags],
        base_port=replay.base_port,
        capture=capture,
    )
