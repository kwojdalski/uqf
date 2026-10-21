"""A TorQ sort.csv as its loader reads it, and the only two edits an attachment
makes to one: append a table's declared rows, and take exactly those rows out
again (#1517). The lifecycle around them is in sorting.py.

`.sort.getsortcsv` reads the file with `("SSSB";enlist",")0:` - a header naming
the columns, fields trimmed, `sort` a boolean, an empty line a row of nulls, and
no comment lines. So nothing can mark the rows an attachment added: what was
added is recorded in the manifest instead, as the exact text, and a removal
proves the text is still there, once, and alone for its table.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from uqs.attach.config import AttachmentError, atomic_write
from uqs.attach.schema import TableSpec

#: sort.csv's columns as TorQ's loader requires them: `sort` last, as the `B` it parses.
HEADER = ("tabname", "att", "column", "sort")
#: The manifest operation that records rows added to (or reused in) one file for one table.
#: How q's `B` parse reads a true boolean.
_TRUE = "1tTyY"
_CODEC = ("utf-8", "surrogateescape")

#: (att, column, sort) - one sort.csv row without its table
Key = tuple[str, str, bool]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode(*_CODEC)).hexdigest()


def declared(spec: TableSpec) -> list[Key]:
    """The rows `spec`'s declaration stands for: each column sorted, in order."""
    return [(att, column, True) for column, att in spec.sort]


def keys_of(rows: list[list]) -> list[Key]:
    return [(str(a), str(c), bool(s)) for a, c, s in rows]


def as_lists(keys: list[Key]) -> list[list]:
    return [[a, c, s] for a, c, s in keys]


@dataclass(frozen=True)
class Rule:
    """One sort.csv row, as `.sort.params` holds it."""

    table: str
    att: str
    column: str
    sort: bool

    @property
    def key(self) -> Key:
        return (self.att, self.column, self.sort)


def own(rules: list[Rule] | tuple[Rule, ...], table: str) -> list[Key]:
    return [r.key for r in rules if r.table == table]


def others(rules: list[Rule] | tuple[Rule, ...], tables: set[str]) -> list[Rule]:
    return [r for r in rules if r.table not in tables]


# ---------------------------------------------------------------------- the file


def _lines(text: str) -> list[str]:
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _bare(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


@dataclass(frozen=True)
class SortFile:
    """A sort.csv as TorQ's loader reads it, and its exact text."""

    path: Path
    text: str
    header: tuple[str, ...]
    rules: tuple[Rule, ...]
    newline: str

    @property
    def sha(self) -> str:
        return sha(self.text)

    def run(self, table: str, keys: list[Key]) -> str:
        """The lines that add `keys` for `table`, in this file's column order and line ending."""
        out = []
        for att, column, sorted_ in keys:
            cell = {"tabname": table, "att": att, "column": column, "sort": "1" if sorted_ else "0"}
            out.append(",".join(cell[h] for h in self.header) + self.newline)
        return "".join(out)

    def separator(self) -> str:
        """What goes before a run so it starts on a line of its own."""
        return self.newline if self.text and not self.text.endswith("\n") else ""


def parse(path: Path, text: str) -> SortFile:
    """`text` read as `.sort.getsortcsv` reads it - the header names the columns, `sort`
    is parsed as a boolean, fields are trimmed, and an empty line is a row of nulls.
    Raises AttachmentError for a file it cannot model exactly: never guess at a
    file this workflow is about to change."""
    lines = _lines(text)
    if not lines:
        raise AttachmentError(f"{path} is empty: TorQ's loader needs the header {','.join(HEADER)}")
    header = tuple(f.strip() for f in _bare(lines[0]).split(","))
    if sorted(header) != sorted(HEADER) or header[-1] != "sort":
        raise AttachmentError(
            f"{path} has the header {','.join(header)!r}; TorQ's loader parses "
            f"{','.join(HEADER)} with `sort` last"
        )
    rules: list[Rule] = []
    for n, line in enumerate(lines[1:], 2):
        cells = [c.strip() for c in _bare(line).split(",")]
        if cells == [""]:
            rules.append(Rule("", "", "", False))
            continue
        if len(cells) != len(HEADER):
            raise AttachmentError(f"{path}:{n} has {len(cells)} fields, not {len(HEADER)}")
        d = dict(zip(header, cells, strict=True))
        rules.append(
            Rule(d["tabname"], d["att"], d["column"], d["sort"][:1] in _TRUE and d["sort"] != "")
        )
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    return SortFile(path, text, header, tuple(rules), newline)


def read(path: Path) -> SortFile:
    return parse(path, path.read_bytes().decode(*_CODEC))


def _replace(target: Path, text: str, backup_dir: Path) -> str:
    """Back `target` up into `backup_dir`, then replace it whole, keeping its mode.
    Returns the backup's path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    before = target.read_bytes()
    backup = backup_dir / f"{target.name}.{hashlib.sha256(before).hexdigest()[:12]}.bak"
    if not backup.exists():
        shutil.copy2(target, backup)
    mode = target.stat().st_mode
    atomic_write(target, text.encode(*_CODEC))
    os.chmod(target, mode)
    return str(backup)


def add(path: Path, expected: str, table: str, keys: list[Key], backup_dir: Path) -> dict:
    """Append `keys` for `table` to the file at `path` - which must still hash to
    `expected` - and say exactly what was added. Raises AttachmentError otherwise."""
    current = read(path)
    if current.sha != expected:
        raise AttachmentError(
            f"{path} changed since it was planned (another edit is in progress, or was made): "
            "nothing was written - plan again"
        )
    sep, run = current.separator(), current.run(table, keys)
    after = current.text + sep + run
    backup = _replace(path, after, backup_dir)
    return {
        "path": str(path),
        "run": run,
        "rows": as_lists(keys),
        "separator": sep,
        "sha_before": expected,
        "sha_after": sha(after),
        "backup": backup,
    }


def removal_problems(post: dict, table: str) -> list[str]:
    """Why the rows recorded in `post` cannot be taken out of the file: gone, edited,
    or joined by other rows for the table. Empty when they are exactly as written."""
    path = Path(post["path"])
    if not path.is_file():
        return [f"{path} no longer exists"]
    try:
        current = read(path)
    except AttachmentError as exc:
        return [str(exc)]
    run = post["run"]
    found = current.text.count(run)
    if found != 1:
        return [
            f"the sort.csv rows this attachment added for {table} are "
            + ("no longer in" if not found else "more than once in")
            + f" {path} as written (an operator changed them)"
        ]
    if own(current.rules, table) != keys_of(post["rows"]):
        return [f"{path} has sort.csv rows for {table} besides the ones this attachment added"]
    return []


def remove(post: dict, table: str, backup_dir: Path) -> dict:
    """Take out the rows recorded in `post` - only if removal_problems is empty."""
    if found := removal_problems(post, table):
        raise AttachmentError("; ".join(found))
    path, run, sep = Path(post["path"]), post["run"], post.get("separator", "")
    text = read(path).text
    at = text.index(run)
    rest = text[:at] + text[at + len(run) :]
    if sep and at == len(rest) and rest.endswith(sep):
        rest = rest[: -len(sep)]  # the line ending this attachment added before its run
    backup = _replace(path, rest, backup_dir)
    return {"sha_removed": sha(rest), "removal_backup": backup}


def describe(keys: list[Key]) -> str:
    return "; ".join(f"{c}{'' if s else ' (unsorted)'}{f' {a}#' if a else ''}" for a, c, s in keys)
