"""The checks that read one component's configuration: the tickerplant, each
consumer, the storage the attachment pinned and the schema file it would
persist to. Split from preflight.py, which orchestrates them."""

from __future__ import annotations

import os
from pathlib import Path

from uqs.attach import persist
from uqs.attach.config import Attachment
from uqs.attach.facts import Facts
from uqs.attach.findings import Finding
from uqs.attach.schema import MIXED, TableSpec, differences
from uqs.attach.topology import Observation, Proc

SUPPORTED_LOGGING = ("tabperiod", "tabular")


def untyped_columns(specs: tuple[TableSpec, ...]) -> list[Finding]:
    """A blocker for each table with an untyped (`()`) column (#1451).

    A bundle cannot produce one any more - parsing refuses it - but an attachment
    saved before that can, and applying it again would register the table.
    Detach and verify do not come through here: taking such a table back stays possible."""
    return [
        Finding(
            "blocker",
            "UNTYPED_COLUMN",
            "schema",
            f"{spec.name}.{name} is an untyped `()` column: the wdb's write of a later batch of "
            "another shape fails and TorQ then aborts end of day for every table",
            "declare a concrete type for the column in the bundle and plan again",
            {"table": spec.name, "column": name},
        )
        for spec in specs
        for name, char in spec.columns
        if char == MIXED
    ]


def tickerplant(
    att: Attachment, tp: Proc, specs: tuple[TableSpec, ...], facts: Facts
) -> list[Finding]:
    out, p = [], tp.probe or {}
    if p.get("chained"):
        out.append(
            Finding("blocker", "CHAINED_TP", tp.procname, "this is a chained tickerplant", "")
        )
    if p.get("multilog") not in SUPPORTED_LOGGING:
        out.append(
            Finding(
                "blocker",
                "LOGGING_MODE",
                tp.procname,
                f"{tp.procname} logs in mode {p.get('multilog')!r}, which shares log files between "
                "tables, so one table's log cannot be attributed or taken back",
                f"supported: {', '.join(SUPPORTED_LOGGING)}; changing the mode needs a restart "
                "of a shared process, which this workflow never does",
            )
        )
    if not p.get("createlogs"):
        out.append(
            Finding(
                "blocker",
                "LOGS_DISABLED",
                tp.procname,
                f"{tp.procname} does not write tickerplant logs, so the table would not be "
                "replayable",
            )
        )
    for spec in specs:
        out += _table_on_tickerplant(tp, spec, facts)
    return out


def _table_on_tickerplant(tp: Proc, spec: TableSpec, facts: Facts) -> list[Finding]:
    p = tp.probe or {}
    seen = p.get("schemas", {}).get(spec.name, [])
    if not seen:
        return [
            Finding("info", "WILL_CREATE", tp.procname, f"{spec.name}: absent - will be registered")
        ]
    if diff := differences(spec, seen):
        return [
            Finding(
                "blocker",
                "SCHEMA_INCOMPATIBLE",
                tp.procname,
                f"{spec.name} exists on {tp.procname} with a different shape: {'; '.join(diff)}",
                "an attachment never alters an existing table (no automatic column conversion)",
            )
        ]
    if spec.name not in p.get("published", []):
        return [
            Finding(
                "blocker",
                "NOT_PUBLISHED",
                tp.procname,
                f"{spec.name} is defined on {tp.procname} but is not one of its pub/sub tables",
                "it was not created by a schema file; fix by hand",
            )
        ]
    ledger = p.get("owned", {}).get(spec.name)
    if ledger and facts.token and ledger.get("token") == facts.token:
        return [
            Finding(
                "info",
                "ALREADY_APPLIED",
                tp.procname,
                f"{spec.name}: registered by this attachment already - nothing to do",
            )
        ]
    if ledger:
        return [
            Finding(
                "info",
                "SHARED_TABLE",
                tp.procname,
                f"{spec.name}: created by attachment {ledger.get('attachment')} - this attachment "
                "will depend on it and never remove it",
            )
        ]
    return [
        Finding(
            "info",
            "WILL_ADOPT",
            tp.procname,
            f"{spec.name}: already registered with the same shape (pre-existing) - adopted, "
            "never removed by this attachment",
        )
    ]


def consumer(att: Attachment, c: Proc, specs: tuple[TableSpec, ...]) -> list[Finding]:
    out, p = [], c.probe or {}
    everything = (
        [p.get("subscribeto")] if c.proctype == "rdb" else [p.get("subtabs"), p.get("subsyms")]
    )
    unfiltered = all(v in ([], [""]) for v in everything)
    if p.get("subfiltered") or not unfiltered:
        out.append(
            Finding(
                "blocker",
                "CONSUMER_FILTERED",
                c.procname,
                f"{c.procname} subscribes with a filter or to a fixed table list, so it would not "
                "take the new table after a restart",
                "its subscription file is stack configuration this workflow does not edit",
            )
        )
    for spec in specs:
        if spec.name in p.get("ignorelist", []):
            out.append(
                Finding(
                    "blocker",
                    "CONSUMER_IGNORES",
                    c.procname,
                    f"{c.procname} ignores {spec.name} - it would never be persisted",
                    "its ignorelist is stack configuration",
                )
            )
        seen = p.get("schemas", {}).get(spec.name, [])
        if seen and (diff := differences(spec, seen)):
            out.append(
                Finding(
                    "blocker",
                    "SCHEMA_INCOMPATIBLE",
                    c.procname,
                    f"{spec.name} exists on {c.procname} with a different shape: {'; '.join(diff)}",
                )
            )
        if c.proctype == "wdb":
            out += _sorting(c, spec, p.get("sortrows", []))
    if c.proctype == "wdb" and p.get("mode") not in ("save", "saveandsort"):
        out.append(
            Finding("blocker", "WDB_MODE", c.procname, f"{c.procname} is in mode {p.get('mode')!r}")
        )
    return out


def _sorting(c: Proc, spec: TableSpec, rows: list[dict]) -> list[Finding]:
    """sort.csv (read from the wdb, never written) must be able to sort `spec`:
    its own rows if it has any, else the `default` rows."""
    own = [r for r in rows if r["tabname"] == spec.name]
    use = own or [r for r in rows if r["tabname"] == "default"]
    have = {n for n, _ in spec.columns}
    missing = sorted({r["col"] for r in use if r["col"] not in have})
    if not use:
        return [
            Finding(
                "blocker",
                "SORT_CONFIG",
                c.procname,
                f"{c.procname}'s sort.csv has no row for {spec.name} and no default row",
                "",
                {"table": spec.name, "rows": "none"},
            )
        ]
    if missing:
        return [
            Finding(
                "blocker",
                "SORT_CONFIG",
                c.procname,
                f"{c.procname} sorts {spec.name} by {'own' if own else 'default'} sort.csv rows "
                f"naming {', '.join(missing)}, which the table lacks - end of day would fail",
                "add the column to the table, or a row for the table to sort.csv by hand",
                {
                    "table": spec.name,
                    "rows": "own" if own else "default",
                    "missing": ",".join(missing),
                },
            )
        ]
    return []


def storage(att: Attachment, obs: Observation) -> list[Finding]:
    tp, pinned, out = obs.tickerplant, att.storage, []
    observed = {
        "tplogdir": (tp.probe or {}).get("tplogdir", "") if tp else "",
        "schema_file": (tp.probe or {}).get("schemafile", "") if tp else "",
    }
    for c in obs.consumers:
        probe = c.probe or {}
        if c.proctype == "wdb":
            observed["wdb_savedir"] = probe.get("savedir", "").lstrip(":")
            observed["hdb_dir"] = probe.get("hdbdir", "").lstrip(":")
    for key, want in pinned.__dict__.items():
        have = observed.get(key, "")
        if want and have and Path(want) != Path(have):
            out.append(
                Finding(
                    "blocker",
                    "STORAGE_MISMATCH",
                    key,
                    f"{key} was recorded as {want} but the stack reports {have}",
                    "the attachment never falls back to a default location; re-plan if the "
                    "stack moved",
                )
            )
    return out


def persistence(att: Attachment, obs: Observation, facts: Facts) -> list[Finding]:
    tp = obs.tickerplant
    if att.persist == "runtime-only":
        return [
            Finding(
                "warning",
                "RUNTIME_ONLY",
                "schema",
                "runtime-only: restarting the tickerplant DROPS the registration; the "
                "attachment then reports registration-lost until applied again",
            )
        ]
    probe = (tp.probe or {}) if tp else {}
    path = probe.get("schemafile", "")
    why = ""
    if not path:
        why = "the tickerplant was not started with -schemafile"
    elif not facts.host.is_local(att.stack.host):
        why = f"the schema file is on {att.stack.host}; it can only be edited from that machine"
    elif not Path(path).is_file():
        why = f"{path} does not exist on this machine"
    elif not _writable(Path(path)):
        why = f"{path} is not writable by this user"
    elif persist.locate(Path(path).read_text(errors="replace"), att.name) is not None:
        return [
            Finding(
                "info",
                "ALREADY_PERSISTED",
                "schema",
                f"{path} already holds this attachment's block",
            )
        ]
    elif "GENERATED" in Path(path).read_text(errors="replace")[:400]:
        why = (
            f"{path} is generated (uqf rebuilds it from plant_tables.q on every start); "
            "persist through `uqs job install` / `runtime prepare` instead"
        )
    if why:
        return [
            Finding(
                "blocker",
                "PERSIST_UNAVAILABLE",
                "schema",
                f"cannot persist the table across a tickerplant restart: {why}",
                "choose --runtime-only to accept losing it on restart, or fix the above",
            )
        ]
    return [Finding("info", "WILL_PERSIST", "schema", f"a marked block will be added to {path}")]


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)
