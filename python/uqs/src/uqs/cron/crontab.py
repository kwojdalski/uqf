"""The entries in the user's crontab: one managed, commented block (#2987).

    # >>> uqs cron (managed by `uqs cron install`; edit with `uqs cron`, not by hand) >>>
    # uqs-cron-block: sha=<hash of the lines below, as installed>
    # uqs-cron: entry=deals-daily managed-by=uqs schedules=/abs/cron.toml repo=/abs/repo \
    #   tz=UTC sha=<entry>   (one line in the crontab)
    # deals-daily: job backfill demo_deals_backfill --last 3d --source-version v1
    #   10 6 * * 1-5 UTC (06:10, Mon-Fri, UTC); log: /abs/cron/deals-daily.log
    10 6 * * 1-5 cd /abs/repo && UQS_CRON_ENTRY=deals-daily /abs/uqs cron run deals-daily
    # <<< uqs cron <<<

Read with `crontab -l` and written with `crontab -`; every line outside the
block is written back exactly as read. cron has no trailing comments - all
after the command is the command - so what traces a line back to uqs is on
comment lines: the `uqs-cron:` annotation above each schedule line, in
shell-quoted `key=value` words, names the entry, the schedules file, the
checkout and a hash of the entry as installed. The command also carries
`UQS_CRON_ENTRY=<name>`, set after the `cd` so that the uqs process - and
whatever it starts - inherits it (an assignment before `cd` would reach only
the `cd`). Paths are absolute: cron's PATH and working directory are minimal.

The block's own hash is of what was installed; a block whose lines no longer
hash to it was edited by hand, and `install` refuses it rather than lose the
edit. `findings` reports a block line no entry owns or whose entry has changed,
and any line OUTSIDE the block that calls `uqs cron run` - one uqs owns but
does not manage. None of them is ever changed here.

`COMMAND` is the crontab program; the tests replace it, and must: this machine's
real crontab holds lines that are not uqs's.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from uqs.cron import schedule
from uqs.cron.entries import Entry
from uqs.paths import UqsError

#: The crontab program, as argv. Replaced by tests: never this machine's real one there.
COMMAND: Sequence[str] = ("crontab",)
BEGIN = "# >>> uqs cron (managed by `uqs cron install`; edit with `uqs cron`, not by hand) >>>"
END = "# <<< uqs cron <<<"
BLOCK_SHA = "# uqs-cron-block: sha="
ANNOTATION = "# uqs-cron: "
ENTRY_ENV = "UQS_CRON_ENTRY"
_CALLS_RUN = re.compile(r"\bcron\s+run\b")
_DAYS = "Sun Mon Tue Wed Thu Fri Sat".split()


class CrontabError(UqsError):
    """The crontab cannot be read, written or changed as asked."""


def entry_sha(e: Entry) -> str:
    """A short hash of the entry as written: changes when its schedule, zone or command does."""
    return hashlib.sha256(f"{e.name}\0{e.cron}\0{e.tz}\0{e.run}".encode()).hexdigest()[:12]


def _lines_sha(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


def in_words(e: Entry) -> str:
    """The schedule said plainly where it is simple - `06:10, Mon-Fri, UTC` - else the fields."""
    m, h, dom, mon, dow = e.cron.split()
    if not (m.isdigit() and h.isdigit()):
        return f"{e.cron}, {e.tz}"
    days = []
    if dow != "*":
        days.append(re.sub(r"\d", lambda d: _DAYS[int(d.group()) % 7], dow))
    if dom != "*":
        days.append(f"day {dom}")
    if mon != "*":
        days.append(f"month {mon}")
    return ", ".join([f"{int(h):02d}:{int(m):02d}", *(days or ["every day"]), e.tz])


@dataclass(frozen=True)
class Context:
    """What every line names: the checkout, the uqs that runs, the files and the host's zone."""

    repo: Path
    uqs: str
    schedules: Path
    log_dir: Path
    host_tz: str | None
    now: datetime


def entry_lines(e: Entry, ctx: Context) -> list[str]:
    """One entry's annotation, its comments and its schedule line."""
    fields = schedule.line_schedule(e.parsed, e.tz, ctx.host_tz, ctx.now)
    tag = {
        "entry": e.name,
        "managed-by": "uqs",
        "schedules": str(ctx.schedules),
        "repo": str(ctx.repo),
        "tz": e.tz,
        "sha": entry_sha(e),
    }
    annotation = ANNOTATION + " ".join(f"{k}={shlex.quote(v)}" for k, v in tag.items())
    command = f"cd {shlex.quote(str(ctx.repo))} && {ENTRY_ENV}={e.name} {ctx.uqs} cron run {e.name}"
    guard = "" if fields == e.cron else f"; fires {fields} on this host, run checks the zone"
    return [
        annotation,
        f"# {e.name}: {e.run}",
        f"#   {e.cron} {e.tz} ({in_words(e)}){guard}; log: {ctx.log_dir / (e.name + '.log')}",
        f"{fields} {command}",
    ]


def render(entries: dict[str, Entry], ctx: Context) -> list[str]:
    """The whole block for `entries`, markers and hash included; none for no entries."""
    if not entries:
        return []
    body = [line for name in sorted(entries) for line in entry_lines(entries[name], ctx)]
    return [BEGIN, BLOCK_SHA + _lines_sha(body), *body, END]


@dataclass(frozen=True)
class Split:
    before: list[str]
    block: list[str] | None
    after: list[str]


def split(text: str) -> Split:
    """The crontab around the block, and the block (markers included), or None."""
    lines = text.splitlines()
    if BEGIN not in lines:
        return Split(lines, None, [])
    i = lines.index(BEGIN)
    try:
        j = lines.index(END, i)
    except ValueError:
        raise CrontabError(
            "the crontab has the start of the uqs cron block but not its end - fix it with "
            "`crontab -e` (delete the block from its first line), then `uqs cron install`"
        ) from None
    return Split(lines[:i], lines[i : j + 1], lines[j + 1 :])


@dataclass(frozen=True)
class Annotation:
    """One `uqs-cron:` comment and the schedule line beneath it."""

    fields: dict[str, str]
    line: str

    @property
    def entry(self) -> str:
        return self.fields.get("entry", "")


def annotations_of(block: Sequence[str]) -> list[Annotation]:
    """Every annotation in `block` with the first non-comment line after it."""
    out = []
    for i, line in enumerate(block):
        if not line.startswith(ANNOTATION):
            continue
        try:
            words = shlex.split(line[len(ANNOTATION) :])
        except ValueError:
            words = []
        fields = dict(w.split("=", 1) for w in words if "=" in w)
        command = next((x for x in block[i + 1 :] if not x.startswith("#")), "")
        out.append(Annotation(fields, command))
    return out


def edited(block: Sequence[str]) -> bool:
    """Whether the block's lines no longer hash to what was installed."""
    body = block[1:-1]
    if not body or not body[0].startswith(BLOCK_SHA):
        return True
    return body[0][len(BLOCK_SHA) :] != _lines_sha(body[1:])


@dataclass(frozen=True)
class Finding:
    kind: str  # unknown | stale | orphan | edited
    entry: str
    line: str
    message: str


def findings(text: str, entries: dict[str, Entry]) -> list[Finding]:
    """What in the crontab does not match the entries; reported, never changed."""
    parts = split(text)
    out = []
    if parts.block is not None:
        if edited(parts.block):
            out.append(Finding("edited", "", "", "the uqs cron block was edited by hand"))
        for a in annotations_of(parts.block):
            e = entries.get(a.entry)
            if e is None:
                out.append(Finding("unknown", a.entry, a.line,
                                   f"the crontab runs {a.entry or '(no entry)'}, which is no "
                                   "entry: `uqs cron install` removes it"))  # fmt: skip
            elif a.fields.get("sha") != entry_sha(e):
                out.append(Finding("stale", a.entry, a.line,
                                   f"{a.entry} changed since it was installed: `uqs cron "
                                   "install` writes it again"))  # fmt: skip
        named = {a.entry for a in annotations_of(parts.block)}
        out += [
            Finding("missing", n, "", f"{n} is not in the crontab: `uqs cron install` adds it")
            for n in sorted(set(entries) - named)
        ]
    else:
        out += [
            Finding("missing", n, "", f"{n} is not in the crontab: `uqs cron install` adds it")
            for n in sorted(entries)
        ]
    for line in [*parts.before, *parts.after]:
        if not line.lstrip().startswith("#") and _CALLS_RUN.search(line) and "uqs" in line:
            out.append(Finding("orphan", "", line,
                               "a line outside the uqs cron block calls `uqs cron run`: uqs "
                               "does not manage it, and never changes it"))  # fmt: skip
    return out


def read() -> str:
    """`crontab -l`, or "" when the user has no crontab yet."""
    try:
        done = subprocess.run([*COMMAND, "-l"], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CrontabError(f"{COMMAND[0]} cannot be run: {exc}") from None
    if done.returncode:
        if "no crontab" in (done.stderr or "").lower():
            return ""
        raise CrontabError(f"`crontab -l` failed: {(done.stderr or '').strip()}")
    return done.stdout


def write(text: str) -> None:
    """`crontab -`, with `text` on its stdin."""
    done = subprocess.run([*COMMAND, "-"], input=text, capture_output=True, text=True, check=False)
    if done.returncode:
        raise CrontabError(f"`crontab -` failed: {(done.stderr or '').strip()}")


@dataclass(frozen=True)
class Install:
    before: str
    after: str

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                "crontab (now)",
                "crontab (installed)",
            )
        )


def plan(current: str, entries: dict[str, Entry], ctx: Context) -> Install:
    """The crontab with the block replaced by the entries', every other line as it was.

    Refuses a block edited by hand, showing what the edit was against what was installed."""
    parts = split(current)
    if parts.block is not None and edited(parts.block):
        raise CrontabError(
            "the uqs cron block in the crontab was edited by hand since `uqs cron install` "
            "wrote it, so installing would lose the edit:\n"
            + "\n".join(parts.block)
            + "\nTo keep the change, make it with `uqs cron add`/`remove`; then delete the "
            "block (from its `>>> uqs cron` line to its `<<< uqs cron` line) with "
            "`crontab -e` and run `uqs cron install` again"
        )
    block = render(entries, ctx)
    lines = [*parts.before, *block, *parts.after]
    after = "\n".join(lines) + "\n" if lines else ""
    if parts.block is None and not block:
        after = current  # nothing of ours, before or after: not even a newline changed
    return Install(current, after)
