"""The scheduled entries, one TOML file per machine (#2987).

    [deals-daily]
    cron = "10 6 * * 1-5"     # standard five-field cron
    tz   = "UTC"              # optional; UTC when absent
    run  = "job backfill demo_deals_backfill --last 3d --source-version v1"

`run` is exactly what one would type after `uqs`, split as a shell splits it.
The file is per machine, not in the repository, because entries name
attachments: it lives beside the attachments directory (uqs.stack.attach_state's
rules - `$UQS_ATTACH_DIR`'s parent, else `$UQS_DATA_ROOT` or the checkout's
`output/`), in `cron/`, with each entry's log and outcome records.

Imports only schedule.py and attach_state, both light, because guard.py reads
an entry here before anything slow loads.
"""

from __future__ import annotations

import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from uqs.cron import schedule
from uqs.paths import UqsError
from uqs.stack import attach_state

FILE = "cron.toml"
#: An entry's name: the attachments' rule, so it is safe in a file name and a crontab comment.
NAME = attach_state.NAME
_KEYS = {"cron", "tz", "run"}


@dataclass(frozen=True)
class Entry:
    name: str
    cron: str
    run: str
    tz: str = schedule.DEFAULT_TZ

    @property
    def args(self) -> list[str]:
        """`run` as the words uqs is given."""
        return shlex.split(self.run)

    @property
    def parsed(self) -> schedule.Cron:
        return schedule.parse(self.cron)


def directory() -> Path:
    """Where the entries, their logs and their outcomes live: beside the attachments."""
    return attach_state.state_root().parent / "cron"


def path(root: Path | None = None) -> Path:
    return (root or directory()) / FILE


def log_path(name: str, root: Path | None = None) -> Path:
    return (root or directory()) / f"{name}.log"


def outcomes_path(name: str, root: Path | None = None) -> Path:
    return (root or directory()) / f"{name}.outcomes.jsonl"


def check_name(name: str) -> str:
    if not NAME.match(name):
        raise UqsError(
            f"{name!r} is not an entry name - lowercase letters, digits and '-', starting "
            "with a letter"
        )
    return name


def entry(name: str, cron: str, run: str, tz: str | None = None) -> Entry:
    """An entry, checked: its name, its five fields and its zone; `run` must say something."""
    try:
        schedule.parse(cron)
        schedule.zone(tz or schedule.DEFAULT_TZ)
        words = shlex.split(run)
    except (schedule.CronError, ValueError) as exc:
        raise UqsError(f"entry {name}: {exc}") from None
    if not words:
        raise UqsError(f"entry {name}: `run` is empty - give the uqs command to run")
    return Entry(check_name(name), " ".join(cron.split()), shlex.join(words), tz or "UTC")


def load(root: Path | None = None) -> dict[str, Entry]:
    """Every entry by name; none when the file is not there."""
    file = path(root)
    try:
        data = tomllib.loads(file.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UqsError(f"{file} cannot be read: {exc}") from None
    out = {}
    for name, fields in data.items():
        if not isinstance(fields, dict) or not {"cron", "run"} <= set(fields) <= _KEYS:
            raise UqsError(
                f"{file}: [{name}] takes `cron` and `run`, and optionally `tz` - nothing else"
            )
        out[name] = entry(name, str(fields["cron"]), str(fields["run"]), fields.get("tz"))
    return out


def save(entries: dict[str, Entry], root: Path | None = None) -> Path:
    """Write every entry, in name order, through tomlkit; the zone is always written."""
    import tomlkit

    doc = tomlkit.document()
    doc.add(tomlkit.comment("uqs cron entries (managed by `uqs cron add`/`remove`)"))
    for name in sorted(entries):
        e = entries[name]
        table = tomlkit.table()
        table.add("cron", e.cron)
        table.add("tz", e.tz)
        table.add("run", e.run)
        doc.add(name, table)
    file = path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(tomlkit.dumps(doc))
    tmp.replace(file)
    return file
