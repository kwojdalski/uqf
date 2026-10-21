"""A table's declared TorQ sort rule, through the attachment lifecycle (#1517).

A stack's sort.csv `default` rows fit the tables it was built for. A table
attached later may lack the columns they name - and then end of day cannot sort
it. A job that knows how its table should be stored DECLARES it beside the
table (`sorting[`t;`sym`time!`p`]`, schema.parse_sorting); nothing here infers
a layout from column names. This module takes that declaration to the stack:

  PLAN    Which processes hold sort rules for the table - every managed wdb, and
          the sort / sortworker processes those wdbs name (`.wdb.sorttypes`,
          `.wdb.sortworkertypes`) - which file each reads, and what the file and
          the live `.sort.params` say for the table: the declared rows already
          (`present`), none (`add`), or other ones (`conflict`, a blocker: an
          attachment never replaces an operator's sort policy).
  APPLY   Append exactly the declared rows to the file, nothing else: a copy is
          kept first, the write is an atomic replace with the file's mode kept,
          and it is refused if the file changed since it was planned. Then each
          process reloads its own file with TorQ's own loader
          (q/sort_reload.q -> `.sort.getsortcsv`) and must report the declared
          rows live. A file written but not live is NOT configured: apply fails
          and the publisher stays stopped.
  DETACH  Remove only rows this attachment added, only while they are exactly as
          written, only when no other attachment relies on them; reload; and
          verify the live rules before calling it reverted.

TorQ's loader reads sort.csv with `("SSSB";enlist",")0:` - no comment lines, so
no marked block can live in the file. Ownership is therefore kept OUTSIDE it, in
the manifest: the exact text added, the rows, and the file's hash before and
after (op `sort_rows`, one per file and table).

A reload re-reads the WHOLE file. So it is refused when a process's live rules
for other tables differ from its file - reloading would also apply someone
else's pending edit - and the operator is told to reconcile it first.
"""

from __future__ import annotations

from pathlib import Path

from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Host
from uqs.attach.manifest import (
    CREATED,
    DONE,
    EXISTING,
    FAILED,
    PENDING,
    RETAINED,
    REVERTED,
    Manifest,
    Op,
)
from uqs.attach.remote import Ipc
from uqs.attach.sort_file import (
    Key,
    add,
    as_lists,
    declared,
    describe,
    keys_of,
    others,
    own,
    read,
    removal_problems,
    remove,
    sha,
)
from uqs.attach.sort_plan import Change, Reader, readers, resolve_path, stopped
from uqs.attach.topology import Observation

#: The manifest operation that records rows added to (or reused in) one file for one table.
SORT_OPS = ("sort_rows",)


# --------------------------------------------------------------------- apply


def _reload(ipc: Ipc, r: Reader, table: str, want: list[Key]) -> str:
    """Reload `r`'s rules from its own file and confirm `want` is live for `table`.
    Returns "" on success, else why not."""
    answer = ipc.call(r.proc.endpoint, "sort_reload", {"table": table})
    if answer.get("status") != "reloaded":
        return f"{answer.get('status')}: {answer.get('detail')}"
    cwd = (r.proc.probe or {}).get("cwd", "")
    if resolve_path({"sortcsv": answer.get("path", ""), "cwd": cwd}) != r.path:
        return f"it reloaded {answer.get('path')}, not {r.path}"
    live = [(x.get("att", ""), x.get("col", ""), bool(x.get("sort"))) for x in answer["rows"]]
    if live != want:
        return f"after the reload it sorts {table} by {describe(live) or 'nothing'}"
    return ""


def apply_change(
    m: Manifest, ipc: Ipc, ch: Change, expected: str, backup_dir: Path
) -> list[tuple[str, str, str, str, str]]:
    """Make `ch` true on the stack: rows into the file (`add`), then every reload it
    needs, each confirmed live. Steps as (op, process, table, outcome, detail).

    `expected` is the file's hash this write may start from. Raises AttachmentError
    (the manifest saying how far it got) when anything fails."""
    steps: list[tuple[str, str, str, str, str]] = []
    prior = m.find("sort_rows", ch.path, ch.table)
    if ch.status == "add":
        text = read(Path(ch.path))
        after = text.text + text.separator() + text.run(ch.table, ch.declared)
        pre = {"sha": expected, "expected_sha": sha(after), "rows": as_lists(ch.declared)}
        op = m.begin("sort_rows", ch.path, ch.table, pre)
        op.pre = pre  # a retry's: the file a refused or lost earlier attempt saw is not this one
        m.save()
        try:
            post = add(Path(ch.path), expected, ch.table, ch.declared, backup_dir)
        except (OSError, AttachmentError) as exc:
            m.finish(op, FAILED, note=str(exc))
            raise AttachmentError(f"sort.csv {ch.path}: {exc}") from None
        post["readers"] = [r.procname for r in ch.readers]
        m.finish(op, DONE, CREATED, post, "rows added; not live until reloaded")
        steps.append(("sort_rows", ch.path, ch.table, "created", f"{len(ch.declared)} row(s)"))
    elif ch.status == "present":
        op = prior
        if prior is not None and prior.status == PENDING and not prior.ownership:
            # an earlier apply lost track mid-write: the hash says whose rows these are
            ours = read(Path(ch.path)).sha == prior.pre.get("expected_sha")
            m.finish(prior, DONE, CREATED if ours else EXISTING, prior.post,
                     "outcome probed: " + ("written by this attachment" if ours
                                           else "not this attachment's write"))  # fmt: skip
        elif prior is None or prior.status not in (DONE, RETAINED):
            op = m.begin("sort_rows", ch.path, ch.table, {"sha": ch.sha})
            post = {"path": ch.path, "rows": as_lists(ch.declared), "run": ""}
            m.finish(op, DONE, EXISTING, post, "pre-existing rows reused; never removed")
            steps.append(("sort_rows", ch.path, ch.table, "adopted", "pre-existing rows"))
        else:
            steps.append(("sort_rows", ch.path, ch.table, "already", ""))
    op = m.find("sort_rows", ch.path, ch.table)
    assert op is not None
    for r in ch.reload:
        why = _reload(ipc, r, ch.table, ch.declared)
        if why:
            op.note = f"in {ch.path}, not live on {r.procname}: {why}"
            m.save()
            raise AttachmentError(
                f"{r.procname} did not take the sort rules for {ch.table} ({why}). The rows are "
                f"in {ch.path} but a file alone is not configuration: the publisher stays "
                f"stopped. Reload it by hand (`.sort.getsortcsv[.wdb.sortcsv]` on "
                f"{r.procname}) or restart it, then attach again"
            )
        steps.append(("sort_reload", r.procname, ch.table, "reloaded", "live rules confirmed"))
    if ch.reload:
        op.note = "live on " + ", ".join(r.procname for r in ch.readers)
        m.save()
    return steps


def apply_all(
    m: Manifest, ipc: Ipc, changes: list[Change], backup_dir: Path
) -> list[tuple[str, str, str, str, str]]:
    """Every change, in order; a second table's rows in a file start from the hash
    the first one's write left."""
    steps: list[tuple[str, str, str, str, str]] = []
    start: dict[str, str] = {}
    for ch in changes:
        steps += apply_change(m, ipc, ch, start.get(ch.path, ch.sha), backup_dir)
        done = m.find("sort_rows", ch.path, ch.table)
        if done is not None and done.post.get("sha_after"):
            start[ch.path] = done.post["sha_after"]
    return steps


def refuse_moved(changes: list[Change], expected: dict[str, str] | None) -> None:
    """A sort.csv that is not as the plan the operator confirmed saw it is a plan
    nobody saw: refuse before anything is touched."""
    if expected is None:
        return
    moved = {c.path for c in changes if c.status == "add" and expected.get(c.path) != c.sha}
    if moved:
        raise AttachmentError(
            f"{', '.join(sorted(moved))} changed since the plan you confirmed: nothing was "
            "changed - plan again and review the sort rules it shows"
        )


# -------------------------------------------------------------------- verify


def checks(
    att: Attachment, obs: Observation, ipc: Ipc, host: Host | None = None
) -> list[tuple[str, bool, str]]:
    """Read-only: the declared rows are in each file and live on each process."""
    specs = [s for s in att.tables if s.sort]
    if not specs:
        return []
    out: list[tuple[str, bool, str]] = []
    host = host or Host()
    rs = [r for r in readers(att, obs, ipc) if not stopped(r, host)]
    for spec in specs:
        want = declared(spec)
        for r in rs:
            if not r.proc.up:
                out.append((f"{r.procname} answers (it holds sort rules)", False, r.proc.error))
                continue
            live = own(r.live, spec.name)
            out.append(
                (
                    f"{r.procname}: {spec.name} is sorted by the declared rule, live",
                    live == want,
                    describe(live) or "no rows: the default rule applies",
                )
            )
        for path in sorted({r.path for r in rs if r.path}):
            if not Path(path).is_file():
                continue
            try:
                rows = own(read(Path(path)).rules, spec.name)
            except AttachmentError as exc:
                out.append((f"{path} is readable", False, str(exc)))
                continue
            name = f"{path} holds the declared sort rows for {spec.name}"
            out.append((name, rows == want, describe(rows)))
    return out


# -------------------------------------------------------------------- detach


def resolve_pending(m: Manifest, *, save: bool) -> list[tuple[str, str, str]]:
    """Settle a `sort_rows` write whose outcome was never recorded, from the file's
    hash: the hash planned after it -> ours; the hash before it -> it never
    happened; anything else -> a blocker naming the manual step."""
    out: list[tuple[str, str, str]] = []
    for op in m.operations:
        if op.op not in SORT_OPS or op.status != PENDING or op.ownership:
            continue
        path = Path(op.process)
        now = read(path).sha if path.is_file() else ""
        if now and now == op.pre.get("expected_sha"):
            op.status, op.ownership = DONE, CREATED
            op.note = "outcome probed: the file is as this attachment's write left it"
            op.post = {
                "path": op.process,
                "run": read(path).run(op.table, keys_of(op.pre.get("rows", []))),
                "rows": op.pre.get("rows", []),
                "separator": "",
            }
        elif now and now == op.pre.get("sha"):
            op.status, op.note = REVERTED, "outcome probed: it never took effect"
        else:
            out.append(
                (
                    "sort.csv",
                    f"the sort.csv rows for {op.table} in {op.process} have an unknown outcome",
                    f"check {op.process} for rows this attachment added and remove them by hand",
                )
            )
    if save:
        m.save()
    return out


def _needed_elsewhere(name: str, path: str, table: str, state_dir: Path | None) -> list[str]:
    """Other attachments whose manifests rely on the rows for `table` in `path`."""
    from uqs.attach import config

    users = []
    for other in config.names(state_dir):
        if other == name or not Manifest.exists(other, state_dir):
            continue
        om = Manifest.load(other, state_dir)
        if om.state in ("rolled_back", "detached"):
            continue
        if any(
            o.op in SORT_OPS and o.process == path and o.table == table
            and o.status in (DONE, PENDING, RETAINED)
            for o in om.operations
        ):  # fmt: skip
            users.append(other)
    return users


def blockers(
    att: Attachment,
    obs: Observation,
    ipc: Ipc,
    m: Manifest,
    state_dir: Path | None,
    host: Host | None = None,
) -> list[tuple[str, str, str]]:
    """Everything that makes removing this attachment's sort rows unsafe right now:
    an operator edit to them, another attachment relying on them, a process that
    cannot be reloaded, or one whose reload would also apply someone else's edit."""
    ops = [
        o
        for o in m.operations
        if o.op in SORT_OPS and o.ownership == CREATED and o.status in (DONE, PENDING)
    ]
    if not ops:
        return []
    out: list[tuple[str, str, str]] = []
    rs = [r for r in readers(att, obs, ipc) if not stopped(r, host or Host())]
    for op in ops:
        removed = bool(op.post.get("removed"))
        if not removed:
            out += [
                (
                    "sort.csv",
                    p,
                    f"restore the rows in {op.process} as they were added, or remove them by hand",
                )
                for p in removal_problems(op.post, op.table)
            ]
            for user in _needed_elsewhere(att.name, op.process, op.table, state_dir):
                out.append(
                    (
                        "sort.csv",
                        f"the sort.csv rows for {op.table} in {op.process} are relied on by "
                        f"attachment {user}",
                        f"detach {user} first, or keep the rows",
                    )
                )
        if not Path(op.process).is_file():
            continue
        after = [r for r in read(Path(op.process)).rules if removed or r.table != op.table]
        for r in [r for r in rs if r.path == op.process or not r.proc.up]:
            if not r.proc.up or not r.reloadable:
                out.append(
                    (
                        r.procname,
                        "cannot be asked to reload its sort rules",
                        f"start {r.procname}; or remove the rows and restart it by hand",
                    )
                )
            elif r.live and others(r.live, {op.table}) != others(after, {op.table}):
                out.append(
                    (
                        r.procname,
                        f"its live sort rules differ from {op.process} for other tables: a "
                        "reload would also apply that edit",
                        f"reconcile the file with {r.procname} first",
                    )
                )
    return out


def revert(
    att: Attachment, obs: Observation, ipc: Ipc, m: Manifest, op: Op, backup_dir: Path
) -> list[tuple[str, str, str, str, str]]:
    """Remove `op`'s rows, reload every process reading the file, and confirm the
    live rules match the file - only then is `op` reverted."""
    steps: list[tuple[str, str, str, str, str]] = []
    if not op.post.get("removed"):
        done = remove(op.post, op.table, backup_dir)
        op.post = {**op.post, **done, "removed": True}
        op.note = "rows removed from the file; not yet reloaded"
        m.save()
        steps.append(("sort_rows", op.process, op.table, "removed", "rows removed"))
    want = own(read(Path(op.process)).rules, op.table)
    for r in [r for r in readers(att, obs, ipc) if r.path == op.process and r.proc.up]:
        why = _reload(ipc, r, op.table, want)
        if why:
            op.note = f"rows removed; {r.procname} not reloaded: {why}"
            m.save()
            raise AttachmentError(
                f"{r.procname} did not reload its sort rules ({why}); the rows are out of "
                f"{op.process} but may still be live there"
            )
        steps.append(("sort_reload", r.procname, op.table, "reloaded", "live rules confirmed"))
    m.finish(op, REVERTED, note="rows removed; live rules reloaded and verified")
    return steps
