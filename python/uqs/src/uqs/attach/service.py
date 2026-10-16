"""The attach workflow as functions - what the CLI calls, and what tests call.

The CLI is a thin renderer over these. Each takes the `Ipc` it should talk
through, so tests pass a scripted one and failure injection wraps the real one.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from uqs.attach import config, jobs, plan
from uqs.attach.config import Attachment, AttachmentError, Publisher, Stack, Storage
from uqs.attach.facts import Facts
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Ipc, KolaIpc
from uqs.attach.topology import Observation


def ipc_for(att: Attachment) -> Ipc:
    """The real connection, with the credential the attachment names."""
    return KolaIpc(att.stack.user, att.stack.password())


def parse_discovery(text: str) -> tuple[str, int]:
    host, sep, port = text.rpartition(":")
    if not sep or not port.isdigit() or not host:
        raise AttachmentError(f"--discovery must be HOST:PORT, not {text!r}")
    return host, int(port)


def create(
    name: str,
    job: str,
    bundle: Path | None,
    stack_id: int,
    root: Path,
    *,
    host: str = "localhost",
    discovery: str | None = None,
    tickerplant: str = "",
    consumers: tuple[str, ...] = (),
    user: str = config.DEFAULT_USER,
    password_env: str = "",
    runtime_only: bool = False,
    storage: Storage | None = None,
    runtime: str = "",
) -> Attachment:
    """An attachment from explicit identity. `stack_id` has no default: it is
    the answer to "which stack", and the incident that produced this workflow
    was a default answering it wrongly.

    The discovery address defaults to `stack_id + 1` - TorQ's own rule for the
    starter pack's process.csv - but only HERE, once; the result is stored and
    never re-derived.
    """
    if stack_id < 1024:
        raise AttachmentError(f"--stack-id {stack_id} is not a base port")
    spec = jobs.resolve(job, bundle.resolve() if bundle else None, root)
    d_host, d_port = parse_discovery(discovery) if discovery else (host, stack_id + 1)
    return Attachment(
        name=name,
        stack=Stack(
            stack_id, host, d_host, d_port, tickerplant, tuple(consumers), user, password_env
        ),
        publisher=Publisher(spec.job, spec.procname, runtime),
        tables=spec.tables,
        storage=storage or Storage(),
        persist="runtime-only" if runtime_only else "schema-file",
        origin=spec.origin,
    )


def observed_storage(att: Attachment, obs: Observation) -> Storage:
    """`att.storage` with every location it left unpinned filled from the
    stack as it is now."""
    tp = (obs.tickerplant.probe or {}) if obs.tickerplant else {}
    wdb = next((c.probe or {} for c in obs.consumers if c.proctype == "wdb" and c.up), {})
    seen = {
        "tplogdir": tp.get("tplogdir", ""),
        "schema_file": tp.get("schemafile", ""),
        "wdb_savedir": wdb.get("savedir", "").lstrip(":"),
        "hdb_dir": wdb.get("hdbdir", "").lstrip(":"),
    }
    return Storage(**{k: getattr(att.storage, k) or v for k, v in seen.items()})


def make_plan(att: Attachment, ipc: Ipc, root: Path) -> plan.Plan:
    """The plan, read with the manifest's token when there is one, so a table
    this attachment already registered reads as its own."""
    facts = Facts()
    if Manifest.exists(att.name):
        facts.token = Manifest.load(att.name).token
    return plan.build(att, ipc, root, facts)


def manifest_of(name: str) -> Manifest | None:
    return Manifest.load(name) if Manifest.exists(name) else None


def save_planned(att: Attachment, p: plan.Plan, state_dir: Path | None = None) -> Path:
    """Write the config, with the storage the plan saw pinned into it."""
    pinned = replace(att, storage=observed_storage(att, p.observation))
    return config.save(pinned, state_dir)
