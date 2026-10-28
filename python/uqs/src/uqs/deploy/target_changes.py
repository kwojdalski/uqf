"""Every change to deploy_targets.toml through `uqs` is one transaction (#1756).

`uqs target add/set/unset/rename/remove/edit/revert`, and the target a push
registers (deploy/history.py), all go through change():

  1. a lock on the file (`<file>.lock`): two sessions pushing at once must
     not interleave their registrations;
  2. the change is made to the file's TEXT (deploy/target_file.py), so the
     operator's comments, spacing and order survive; the result must parse
     to exactly the data the change meant, and pass targets.validate - an
     unknown key, a bad launch_mode, a source-only target missing a path are
     refused naming the line, before anything is written;
  3. optionally (`--check`) the changed target is reached over ssh first,
     and a target that cannot be reached is not saved;
  4. the new text goes to a temporary file beside it, flushed, which then
     replaces the original in one rename: an interrupted change leaves the
     old file, byte for byte, never a truncated one;
  5. one line is appended to the change log, `<file stem>.log.jsonl` beside
     it (deploy_targets.log.jsonl): when, who, from which machine, the
     command, and each changed table's block before and after, with the
     resulting file and its hash.

HAND EDITS ARE FIRST-CLASS. The file is meant to be edited directly too. The
log's last entry holds the file as `uqs` left it; when the next change finds
the file differs, it first records an "edited outside uqs" entry with the
diff, then proceeds. Nothing is blocked: the record is the point.

`revert` is a change like any other. It puts each table an entry changed back
to its block before the entry - exactly, comments included - and is refused
when a table has changed since, rather than overwriting the later change.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from uqs.deploy import target_changelog as changelog
from uqs.deploy import target_file, targets
from uqs.deploy.config import DeployError
from uqs.deploy.target_file import UNSET, Key

#: How long a change waits for another one holding the lock.
LOCK_WAIT = 10.0


def _refuse(message: str) -> DeployError:
    return DeployError("arguments", message)


class Conflict(DeployError):
    """The file changed under a change that read it earlier (`uqs target
    edit`): saving would lose the other change."""


@contextmanager
def locked(root: Path, wait: float = LOCK_WAIT) -> Iterator[None]:
    """Hold the file's lock, waiting up to `wait` seconds for another change."""
    path = targets.declaration_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with open(lock, "a") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise _refuse(
                        f"{path} is being changed by another uqs (it holds {lock}) - try again"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text` in one rename, keeping its permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class Edit:
    """What a change makes of the file: its new text, the target it is
    about, and how it changes the data - so a text change that disturbs
    anything else is caught."""

    text: str
    target: str | None
    expected: Callable[[dict], dict] | None = None


@dataclass
class Result:
    path: Path
    entry: dict | None  # None: the change changed nothing
    written: bool
    outside: dict | None = None  # the "edited outside uqs" entry recorded first


def change(
    root: Path,
    kind: str,
    command: str,
    edit: Callable[[str, dict], Edit],
    *,
    check: Callable[[dict, str | None], None] | None = None,
    dry_run: bool = False,
    then: Callable[[], None] | None = None,
) -> Result:
    """Make one change to the file as a transaction (the module's steps).
    `edit` gets the current text and its data and returns the Edit; `check`
    gets every target, merged, and the one changed, and raises to refuse;
    `then` runs after the write, still under the lock (rename's history)."""
    path = targets.declaration_path(root)
    # a dry run writes nothing, not even the lock file
    with nullcontext() if dry_run else locked(root):
        before = path.read_text() if path.is_file() else ""
        try:
            data = tomllib.loads(before)
        except tomllib.TOMLDecodeError as exc:
            if kind != "edit":  # the whole file replaced: the way to mend it
                raise _refuse(
                    f"{path}: not TOML ({exc}) - mend it with `uqs target edit`"
                ) from None
            data = {}
        outside = changelog.outside(changelog.read_log(root), before, path)
        planned = edit(before, data)
        after = planned.text
        try:
            got = tomllib.loads(after)
        except tomllib.TOMLDecodeError as exc:
            raise _refuse(f"the change would leave {path} unreadable ({exc})") from None
        if planned.expected is not None and got != planned.expected(data):
            raise _refuse(
                f"could not make this change to {path}'s text without disturbing the rest "
                "of it - make it with `uqs target edit`"
            )
        merged = targets.validate(after, path)
        if after == before:
            return Result(path, None, False, outside)
        if check is not None:
            check(merged, planned.target)
        entry = changelog.entry(kind, command, planned.target, before, after, path)
        if dry_run:
            return Result(path, entry, False, outside)
        write_atomically(path, after)
        changelog.append(root, [e for e in (outside, entry) if e is not None])
        if then is not None:
            then()
        return Result(path, entry, True, outside)


# ------------------------------------------------------------------ the changes


def _target(name: str) -> Key:
    return (targets.TARGETS, name)


def _declared(data: Mapping, name: str, path: Path) -> None:
    if name not in data.get(targets.TARGETS, {}):
        known = ", ".join(sorted(data.get(targets.TARGETS, {}))) or "none"
        raise _refuse(f"no target {name!r} in {path} - it declares: {known}")


def _new_name(data: Mapping, name: str, path: Path) -> None:
    if not targets.NAME.fullmatch(name):
        raise _refuse(
            f"target name {name!r}: lower-case letters, digits, - and _, starting with a letter"
        )
    if name in data.get(targets.TARGETS, {}):
        raise _refuse(f"target {name} is already declared in {path}")


def _with(data: dict, key: Key, value: dict | None) -> dict:
    """data with table `key` set to `value`, or removed when None."""
    out = _copy(data)
    node = out
    for part in key[:-1]:
        node = node.setdefault(part, {})
    if value is None:
        node.pop(key[-1], None)
    else:
        node[key[-1]] = value
    return out


def _copy(value):
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def add(root: Path, name: str, entry: dict, command: str, **kw) -> Result:
    """[targets.NAME] appended, its empty values left out."""
    path = targets.declaration_path(root)
    settings = {k: v for k, v in entry.items() if v not in (None, "", {}, [])}

    def edit(text: str, data: dict) -> Edit:
        _new_name(data, name, path)
        block = target_file.render(_target(name), settings)
        return Edit(
            target_file.append(text, block), name, lambda d: _with(d, _target(name), settings)
        )

    return change(root, kw.pop("kind", "add"), command, edit, **kw)


def set_keys(root: Path, name: str, changes: Mapping[str, object], command: str, **kw) -> Result:
    """Target NAME's keys set (a value) or removed (UNSET), in place."""
    path = targets.declaration_path(root)

    def edit(text: str, data: dict) -> Edit:
        _declared(data, name, path)
        table = _copy(data[targets.TARGETS][name])
        for dotted, value in changes.items():
            *outer, last = dotted.split(".")
            node = table
            for part in outer:
                if not isinstance(node.get(part, {}), dict):
                    raise _refuse(f"target {name}: {part} is not a table, so {dotted} is not a key")
                if part not in node and value is UNSET:
                    break
                node = node.setdefault(part, {})
            else:
                if value is UNSET:
                    if last not in node:
                        raise _refuse(f"target {name} sets no {dotted}")
                    del node[last]
                else:
                    node[last] = value
        new = target_file.set_values(text, _target(name), changes)
        return Edit(new, name, lambda d: _with(d, _target(name), table))

    return change(root, kw.pop("kind", "set"), command, edit, **kw)


def remove(root: Path, name: str, command: str, **kw) -> Result:
    path = targets.declaration_path(root)

    def edit(text: str, data: dict) -> Edit:
        _declared(data, name, path)
        return Edit(
            target_file.remove(text, _target(name)), name, lambda d: _with(d, _target(name), None)
        )

    return change(root, "remove", command, edit, **kw)


def rename(root: Path, old: str, new: str, command: str, **kw) -> Result:
    """[targets.OLD] renamed NEW in place; the deployments recorded under
    OLD are recorded under NEW too (deploy/history.py)."""
    from uqs.deploy import history  # history imports this module

    path = targets.declaration_path(root)

    def edit(text: str, data: dict) -> Edit:
        _declared(data, old, path)
        _new_name(data, new, path)
        value = data[targets.TARGETS][old]

        def expected(d: dict) -> dict:
            out = _with(d, _target(old), None)
            return _with(out, _target(new), value)

        return Edit(target_file.rename(text, _target(old), _target(new)), new, expected)

    return change(
        root, "rename", command, edit, then=lambda: history.rename_target(root, old, new), **kw
    )


def replace_file(root: Path, text: str, expected_before: str, command: str, **kw) -> Result:
    """The whole file replaced by `text` - `uqs target edit`'s save -
    refused when the file changed after the copy was taken."""
    path = targets.declaration_path(root)

    def edit(current: str, data: dict) -> Edit:
        if current != expected_before:
            raise Conflict(
                "arguments", f"{path} changed while it was being edited - nothing was saved"
            )
        return Edit(text, None)

    return change(root, "edit", command, edit, **kw)


def find(entries: list[dict], ref: str | None) -> tuple[int, dict]:
    """(number, entry) for `ref` - an entry's number from `uqs target log`
    - or, with None, the last entry."""
    if not entries:
        raise _refuse("the change log is empty - there is nothing to revert")
    if ref is None:
        return len(entries), entries[-1]
    if not ref.isdigit() or not 1 <= int(ref) <= len(entries):
        raise _refuse(
            f"no change {ref} in the log - `uqs target log` numbers them 1 to {len(entries)}"
        )
    return int(ref), entries[int(ref) - 1]


def revert(root: Path, ref: str | None, command: str, **kw) -> Result:
    """Each table entry `ref` changed back to its block before it."""
    number, entry = find(changelog.read_log(root), ref)
    tables = entry.get("tables") or []
    if not tables:
        raise _refuse(f"change {number} changed no table's settings - nothing to revert")

    def edit(text: str, data: dict) -> Edit:
        expected = _copy(data)
        new = text
        for t in tables:
            key = target_file.key_of(t["table"])
            now = target_file.lookup(data, key)
            after = target_file.lookup(tomllib.loads(t["after"]), key) if t["after"] else None
            if now != after:
                raise _refuse(
                    f"{t['table']} has changed since change {number} - revert the later "
                    "changes first, or set it by hand"
                )
            before = target_file.lookup(tomllib.loads(t["before"]), key) if t["before"] else None
            new = target_file.restore(new, key, t["before"])
            expected = _with(expected, key, before)
        names = [t["table"].split(".", 1)[1] for t in tables if t["table"].startswith("targets.")]
        return Edit(new, ",".join(names) or None, lambda d: expected)

    return change(root, "revert", f"{command} (change {number})", edit, **kw)
