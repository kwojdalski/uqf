"""Which attachment a `uqs job attachment add NAME` command line means (#1457):
the saved one reused, a new one, or a replacement of the saved one.

A saved attachment is reused when the options agree with it - repeating the
first command line is the same request - and refused when they conflict,
unless `--replace`. A replacement changes exactly the options it lists and
carries every other saved value over (`carried`); it may not move an
attachment whose manifest still records changes (`_refuse_moving`), and
`--repin-storage` re-pins only what the stack reports (`repins`). Executing
what is resolved here is workflow.py's.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from uqs.attach import apply as applying
from uqs.attach import config, jobs, native_config, plan
from uqs.attach.config import Attachment, AttachmentError, Storage
from uqs.attach.service import (
    create,
    discovery_offset,
    manifest_of,
    observed_storage,
    parse_discovery,
)


@dataclass(frozen=True)
class Given:
    """The configuration options a command line gave. None, empty or False
    means "not given": a saved attachment's own value stands."""

    job: str | None = None
    bundle: Path | None = None
    stack_id: int | None = None
    host: str | None = None
    discovery: str | None = None
    tickerplant: str | None = None
    consumers: tuple[str, ...] = ()
    user: str | None = None
    password_env: str | None = None
    runtime_only: bool = False
    #: `--schema-writer`: cli | tickerplant, None = the saved one (#1845)
    schema_writer: str | None = None
    tplogdir: str = ""
    schema_file: str = ""
    #: how to reach the stack's host over ssh (#1755): set on a saved attachment
    #: without --replace, since it changes how uqs reaches the stack, not which
    ssh: str | None = None
    #: native registration (#1845): None keeps the saved setting; no --replace needed
    process_file: Path | None = None
    start_with_all: bool | None = None
    #: `--repin-storage` (#2767): with --replace, drop every storage pin and pin
    #: what the stack reports now - the one way a pinned location follows a move
    repin_storage: bool = False


@dataclass
class Resolved:
    att: Attachment
    #: saved (reused as it is) | new | replaced
    source: str
    path: Path
    #: what a --replace changed, as "field: saved -> given"
    changed: list[str] = field(default_factory=list)
    #: under --repin-storage, the pins it replaces (`repins` says what with)
    repinned: Storage | None = None


def differences(saved: Attachment, given: Given, root: Path) -> list[str]:
    """Every option `given` sets to something other than `saved` holds."""
    st, out = saved.stack, []

    def differs(what: str, before: object, after: object) -> None:
        if after is not None and after != before:
            out.append(f"{what}: {before!r} -> {after!r}")

    differs("--base-port", st.id, given.stack_id)
    differs("--host", st.host, given.host)
    if given.discovery is not None:
        differs("--discovery", f"{st.discovery_host}:{st.discovery_port}",
                "{}:{}".format(*parse_discovery(given.discovery)))  # fmt: skip
    differs("--tickerplant", st.tickerplant, given.tickerplant)
    differs("--consumer", st.consumers, tuple(given.consumers) or None)
    differs("--user", st.user, given.user)
    differs("--password-env", st.password_env, given.password_env)
    differs("--transient", saved.persist, "runtime-only" if given.runtime_only else None)
    differs("--schema-writer", saved.schema_writer or saved.persist, given.schema_writer)
    differs("--tplogdir", saved.storage.tplogdir, given.tplogdir or None)
    differs("--schema-file", saved.storage.schema_file, given.schema_file or None)
    differs("--job", saved.publisher.job, given.job)
    if (given.job or given.bundle) and not out:
        # the job as it is declared NOW: a table that changed shape is a new attachment
        folder = given.bundle or (Path(saved.origin["folder"]) if saved.origin else None)
        spec = jobs.resolve(given.job or saved.publisher.job, folder, root)
        differs("job process", saved.publisher.procname, spec.procname)
        if spec.tables != saved.tables:
            out.append(
                f"the job's tables: {[t.name for t in saved.tables]} as saved, "
                f"{[t.name for t in spec.tables]} as declared now (or their columns changed)"
            )
    return out


def resolve(
    name: str,
    given: Given,
    root: Path,
    *,
    replace_saved: bool = False,
    runtime: str = "",
    state_dir: Path | None = None,
) -> Resolved:
    """The attachment a command line means.

    A saved attachment is reused when the options agree with it (repeating the
    first command line is the same request), and refused when they conflict,
    unless `replace_saved`. A replacement changes only the options given and
    keeps every other saved value, the overrides among them (`carried`). It
    never rewrites the manifest - the record of what was changed on the stack
    and the only thing detach can reverse from - and it may not move an
    attachment whose manifest still holds changes to another stack or other
    tables. Its saving takes `verified` back (workflow.save): only the apply and
    verify that follow can grant it to the new configuration.
    """
    path = config.directory(name, state_dir) / config.CONFIG_FILE
    if (m := manifest_of(name, state_dir)) is not None:
        applying.refuse_detaching(name, m.state)
    saved = config.load(name, state_dir) if path.is_file() else None
    changed: list[str] = []
    if given.repin_storage and (saved is None or not replace_saved):
        raise AttachmentError(
            f"--repin-storage replaces attachment {name}'s saved storage pins with what the "
            "stack reports now, so it needs "
            + ("--replace" if saved is not None else f"a saved one (no attachment {name} is)")
            + f": {config.repin(name)}"
        )
    if saved is not None:
        changed = differences(saved, given, root)
        if given.repin_storage:
            changed.append("--repin-storage: every storage pin re-read from the stack")
        if not changed:
            att, said = saved, []
            if given.ssh is not None and given.ssh != saved.stack.ssh:
                att = replace(saved, stack=replace(saved.stack, ssh=given.ssh))
                said = [f"--ssh: {saved.stack.ssh!r} -> {given.ssh!r}"]
            att, more = native_config.settle(
                att, saved.native, given.process_file, given.start_with_all
            )
            return Resolved(att, "saved", path, said + more)
        if not replace_saved:
            raise AttachmentError(
                f"attachment {name} is saved with other settings ({'; '.join(changed)}). "
                "Drop those options to use the saved ones, or add --replace to replace them"
            )
    if saved is None and (not given.job or given.stack_id is None):
        raise AttachmentError(
            f"a new attachment needs --job and --base-port (no attachment {name} is saved)"
        )
    att = (
        create(
            name, given.job or "", given.bundle, given.stack_id or 0, root,
            host=given.host or "localhost", discovery=given.discovery,
            tickerplant=given.tickerplant or "", consumers=tuple(given.consumers),
            user=given.user or config.DEFAULT_USER, password_env=given.password_env or "",
            runtime_only=given.runtime_only, schema_writer=given.schema_writer,
            storage=Storage(tplogdir=given.tplogdir, schema_file=given.schema_file),
            runtime=runtime, ssh=given.ssh or "",
        )
        if saved is None
        else carried(saved, given, root, runtime)
    )  # fmt: skip
    att, more = native_config.settle(
        att, saved.native if saved else None, given.process_file, given.start_with_all
    )
    if saved is None:
        return Resolved(att, "new", path)
    _refuse_moving(saved, att, state_dir)
    if given.ssh is not None and given.ssh != saved.stack.ssh:
        changed.append(f"--ssh: {saved.stack.ssh!r} -> {given.ssh!r}")
    repinned = saved.storage if given.repin_storage else None
    return Resolved(att, "replaced", path, changed + more, repinned)


def carried(saved: Attachment, given: Given, root: Path, runtime: str = "") -> Attachment:
    """`saved` with what `given` sets replaced, and nothing else (#2762).

    The one rule `differences` and a plain re-run already follow: an option not
    given keeps its saved value, under `--replace` too - the stack and how it is
    reached (user, password variable, ssh), the persist mode, the job, the
    native settings and the overrides. `--transient` is given only as itself and
    `--schema-writer` only with a value, so a durable attachment stays durable,
    with the writer it had, unless one of them says otherwise. A replacement
    changes exactly the options it lists.

    The storage pins stay too, except where an option that decides them changed:
    a stack moved (base port, host, discovery) re-observes every one, another
    tickerplant its log directory and schema file, other consumers the wdb's
    directories - each re-read from the stack at save (service.observed_storage),
    unless `--tplogdir`/`--schema-file` pin it here. `--repin-storage` drops
    every one, to be re-observed the same way (#2767).
    """
    st = saved.stack
    stack_id = st.id if given.stack_id is None else given.stack_id
    host = given.host or st.host
    derived = (st.host, st.id + discovery_offset(root))
    discovery = given.discovery or (
        None
        if (st.discovery_host, st.discovery_port) == derived
        else f"{st.discovery_host}:{st.discovery_port}"
    )
    if given.runtime_only or given.schema_writer is not None:
        runtime_only, writer = given.runtime_only, given.schema_writer
    else:
        runtime_only, writer = saved.persist == "runtime-only", saved.schema_writer or None
    folder = saved.origin.get("folder")
    att = create(
        saved.name, given.job or saved.publisher.job,
        given.bundle or (Path(folder) if folder else None), stack_id, root,
        host=host, discovery=discovery, tickerplant=given.tickerplant or st.tickerplant,
        consumers=tuple(given.consumers) or st.consumers, user=given.user or st.user,
        password_env=given.password_env or st.password_env, runtime_only=runtime_only,
        schema_writer=writer, runtime=runtime, ssh=st.ssh if given.ssh is None else given.ssh,
    )  # fmt: skip
    moved = (att.stack.id, att.stack.host, att.stack.discovery_host, att.stack.discovery_port) != (
        st.id, st.host, st.discovery_host, st.discovery_port
    )  # fmt: skip
    pins = saved.storage
    if moved or att.stack.tickerplant != st.tickerplant:
        pins = replace(pins, tplogdir="", schema_file="")
    if moved or att.stack.consumers != st.consumers:
        pins = replace(pins, wdb_savedir="", hdb_dir="")
    if given.repin_storage:
        pins = Storage()
    given_pins = {"tplogdir": given.tplogdir, "schema_file": given.schema_file}
    pins = replace(pins, **{k: v for k, v in given_pins.items() if v})
    return replace(att, storage=pins, overrides=saved.overrides)


def _refuse_moving(saved: Attachment, att: Attachment, state_dir: Path | None) -> None:
    m = manifest_of(saved.name, state_dir)
    if m is None or m.state == "rolled_back":
        return
    where = lambda a: (  # noqa: E731
        a.stack.id, a.stack.host, a.stack.discovery_host, a.stack.discovery_port,
        a.publisher.job, a.tables,
    )  # fmt: skip
    if where(saved) != where(att):
        raise AttachmentError(
            f"attachment {saved.name} is {m.state} on stack {saved.stack.id}, and its manifest "
            "records what it changed there - the record detach reverses from. A replacement "
            "may not point it at another stack, job or tables: detach it first "
            f"(`uqs job attachment remove {saved.name}`), or attach under another name"
        )


def repins(resolved: Resolved, p: plan.Plan) -> list[str]:
    """What `--repin-storage` re-pins, as "storage.KEY: old -> new", from the
    stack the plan saw (#2767). Empty without it.

    The re-pin is an operator's act, never a side effect: a moved HDB under a
    verified attachment is exactly when following the stack silently would be
    dangerous (a backfill's state directory and its ledgers are keyed on the
    destination, backfill.state_dir_of). So it is a replacement - refused while
    the publisher runs or a backfill owns the attachment, and taking `verified`
    back until apply and verify re-grant it. A location the stack reports no
    single value for (its wdbs disagree, or none is up) is refused rather than
    left unpinned: nothing could check it afterwards."""
    if resolved.repinned is None:
        return []
    old, new = resolved.repinned, observed_storage(resolved.att, p.observation)
    lost = [k for k, v in old.__dict__.items() if v and not getattr(new, k)]
    if lost:
        wdbs = ", ".join(
            f"{w.procname} {(w.probe or {}).get('hdbdir', '?')}" for w in p.observation.hdb_wdbs()
        )
        raise AttachmentError(
            f"--repin-storage: the stack reports no single {', '.join(lost)} now "
            f"(wdbs: {wdbs or 'none up'}), so re-pinning would leave it unpinned. Bring the "
            "stack to one location, then run the same command again. Nothing was saved"
        )
    return [
        f"storage.{k}: {v!r} -> {getattr(new, k)!r}"
        for k, v in old.__dict__.items()
        if v != getattr(new, k)
    ] or ["storage: every pin already matches what the stack reports"]
