"""What a remote receiver is approved to write, as data (#1717).

A backfill extracted on one machine is written by a receiver on the stack's
(uqs.attach.backfill_remote). The receiver must know what the batches it takes
look like - the dataset, its columns and types, the timestamp column the HDB
partitions by, the row key and the conflict policies - and it must learn that
WITHOUT the job's code: the stack's machine need not have the source, transform
or worker that produced the rows. A RECEIVER CONTRACT carries those facts and
nothing else.

  export    on the machine with the worker: `uqs job attachment contract export W`. q loads
            the worker and derives the contract from its declarations
            (scripts/dev/receiver_contract.q, .qetl.io.remote.contract.of), so
            it is never a hand-kept copy; this adds its identity.
  approve   on the stack's machine: `uqs job attachment contract approve FILE --attach NAME`.
            Validates the contract, checks it against the attachment - which
            must be verified, publish the dataset in the contract's shape and pin
            an HDB here - and stores it beside the attachment, BOUND to that
            destination, its user and the limits the operator sets. The binding
            is the operator's, never the contract's: a contract names no path,
            no user and no row limit.
  receive   `uqs job receive --contract ID --attach NAME`: starts the receiver
            from the approved record alone.

THE IDENTITY is the first 16 hex digits of the SHA-256 of the contract's
canonical JSON (sorted keys, no whitespace), without the identity itself. Any
change to a fact the contract carries is a new identity, so a worker whose
output changed must be exported and approved again.

THE FORMAT is versioned. A contract whose format or version this uqs does not
read is refused before anything is stored, and the receiver checks it again
(.qetl.io.remote.contract.require) before it makes a grant.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.attach import config
from uqs.attach.backfill import Destination, destination, require_verified
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill

FORMAT = "uqf-receiver-contract"
#: The versions this uqs reads; .qetl.io.remote.contract.versions is the receiver's.
VERSIONS = (1,)
SCRIPT = Path("scripts") / "dev" / "receiver_contract.q"
#: Approved contracts, one file per identity, in the attachment's directory.
APPROVED_DIR = "receiver_contracts"
#: .qetl.io.strategies
STRATEGIES = ("append", "fail", "ignore", "replace", "upsert")
#: The most rows one message may carry, unless the operator approves another limit.
DEFAULT_MAX_ROWS = 100_000
_KEYS = (
    "format", "version", "dataset", "columns", "time_column", "window_column",
    "row_key", "partition_column", "partition", "conflict_policies", "origin",
)  # fmt: skip
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARTITION = re.compile(r"[A-Za-z0-9_.:-]*")
#: meta's characters for a typed column; upper case is a nested column of that type
_TYPES = frozenset("bghijefcspmdznuvt") | frozenset("BGHIJEFCSPMDZNUVT")
_IDENTITY = re.compile(r"[0-9a-f]{16}")


class ContractError(UqsError):
    """A contract that cannot be taken: malformed, unsupported, or altered."""


@dataclass(frozen=True)
class Contract:
    """A validated receiver contract; `body` is its JSON, identity included."""

    body: dict[str, Any]

    @property
    def identity(self) -> str:
        return str(self.body["identity"])

    @property
    def dataset(self) -> str:
        return str(self.body["dataset"])

    @property
    def columns(self) -> list[list[str]]:
        return [list(c) for c in self.body["columns"]]

    @property
    def stored_columns(self) -> list[list[str]]:
        """The columns the HDB table holds: the batch's, behind a `time` the HDB writer
        copies from time_column when the batch has none (.qetl.io.hdb_rows)."""
        cols = self.columns
        return cols if any(n == "time" for n, _ in cols) else [["time", "p"], *cols]

    @property
    def conflict_policies(self) -> list[str]:
        return list(self.body["conflict_policies"])

    def text(self) -> str:
        """The contract as a file holds it: indented, keys sorted."""
        return json.dumps(self.body, indent=2, sort_keys=True) + "\n"


def identity_of(body: dict[str, Any]) -> str:
    """The digest of `body`'s canonical JSON, without any identity it carries."""
    rest = {k: v for k, v in body.items() if k != "identity"}
    text = json.dumps(rest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _name(value: Any, what: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or not (_NAME.fullmatch(value) or (empty and value == "")):
        raise ContractError(f"contract: {what} must be a q name{' or empty' if empty else ''}")
    return value


def parse(doc: Any, *, require_identity: bool = True) -> Contract:
    """Validate a contract's JSON and return it, refusing anything malformed,
    unsupported or - when it names an identity - altered since it was exported."""
    if not isinstance(doc, dict):
        raise ContractError("contract: a contract is a JSON object")
    if doc.get("format") != FORMAT:
        raise ContractError(f"contract: not a receiver contract (format must be {FORMAT})")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ContractError("contract: version must be a whole number")
    if version not in VERSIONS:
        raise ContractError(
            f"contract: version {version} is not supported - this uqs reads version(s) "
            + ", ".join(map(str, VERSIONS))
        )
    missing = [k for k in _KEYS if k not in doc]
    if missing:
        raise ContractError(f"contract: missing {', '.join(missing)}")
    extra = sorted(set(doc) - {*_KEYS, "identity"})
    if extra:
        raise ContractError(f"contract: unknown key(s) {', '.join(extra)}")
    _name(doc["dataset"], "dataset")
    cols = doc["columns"]
    if not isinstance(cols, list) or not cols:
        raise ContractError("contract: columns must be a non-empty array of [name, type] pairs")
    names = []
    for pair in cols:
        if not (isinstance(pair, list) and len(pair) == 2):
            raise ContractError("contract: columns must be a non-empty array of [name, type] pairs")
        names.append(_name(pair[0], "a column's name"))
        if pair[1] not in _TYPES:
            raise ContractError(
                f"contract: column {pair[0]}'s type {pair[1]!r} is not a concrete type of meta's"
            )
    if len(set(names)) != len(names):
        raise ContractError("contract: a column is named twice")
    if _name(doc["time_column"], "time_column") not in names:
        raise ContractError(f"contract: time_column {doc['time_column']} is not a column")
    for key in ("window_column", "partition_column"):
        value = _name(doc[key], key, empty=True)
        if value and value not in names:
            raise ContractError(f"contract: {key} {value} is not a column")
    key = doc["row_key"]
    if not isinstance(key, list) or any(_name(k, "row_key") not in names for k in key):
        raise ContractError("contract: row_key must be an array of the contract's columns")
    if not isinstance(doc["partition"], str) or not _PARTITION.fullmatch(doc["partition"]):
        raise ContractError("contract: partition must be a partition name, or empty")
    policies = doc["conflict_policies"]
    if (
        not isinstance(policies, list)
        or not policies
        or any(p not in STRATEGIES for p in policies)
        or len(set(policies)) != len(policies)
    ):
        raise ContractError(
            f"contract: conflict_policies must name strategies among {', '.join(STRATEGIES)}"
        )
    origin = doc["origin"]
    if not isinstance(origin, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in origin.items()
    ):
        raise ContractError("contract: origin must be an object of strings")
    expected = identity_of(doc)
    if "identity" in doc:
        if doc["identity"] != expected:
            raise ContractError(
                f"contract: identity {doc['identity']} does not match its contents ({expected}): "
                "it was altered after it was exported - export it again"
            )
    elif require_identity:
        raise ContractError(
            "contract: no identity - export it with `uqs job attachment contract export`"
        )
    return Contract({**doc, "identity": expected})


def load_file(path: Path) -> Contract:
    """The contract a file holds, validated."""
    try:
        doc = json.loads(path.read_text())
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc.strerror}") from None
    except ValueError as exc:
        raise ContractError(f"{path} is not JSON ({exc})") from None
    return parse(doc)


def export(paths: UqsPaths, worker: str) -> Contract:
    """`worker`'s receiver contract, derived by q from its declarations."""
    stack_backfill.procname_for(worker)  # the refusal that names the known workers
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; the contract is q's")
    done = subprocess.run(
        [str(q), str(paths.repo_root / SCRIPT), "-worker", worker, "-q"],
        cwd=paths.repo_root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, check=False,
    )  # fmt: skip
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        said = (done.stderr.strip() or done.stdout.strip()).splitlines()
        why = said[-1] if said else "q said nothing"
        raise UqsError(f"cannot derive {worker}'s contract: {why}")
    try:
        doc = json.loads(lines[-1])
    except ValueError:
        raise UqsError(f"cannot derive {worker}'s contract: q printed {lines[-1][:80]!r}") from None
    return parse(doc, require_identity=False)


# ---------------------------------------------------------------- approval


def approved_dir(attachment: str, attach_root: Path | None = None) -> Path:
    return config.directory(attachment, attach_root) / APPROVED_DIR


def _contract_destination(
    paths: UqsPaths, attachment: str, contract: Contract, attach_root: Path | None
) -> Destination:
    """The destination the attachment gives `contract`'s dataset on this machine."""
    require_verified(attachment, attach_root)
    att = config.load(attachment, attach_root)
    return destination(
        paths, att, contract.dataset, contract.stored_columns,
        writer=f"contract {contract.identity}", shape_of=f"contract {contract.identity}",
    )  # fmt: skip


def approve(
    paths: UqsPaths,
    contract: Contract,
    attachment: str,
    *,
    conflict_policies: list[str] | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    attach_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Store `contract` as approved for `attachment`, bound to its destination here,
    its stack user and the limits given. Returns the approved record - with
    `dry_run`, the record it would store, storing nothing."""
    policies = conflict_policies or contract.conflict_policies
    wider = sorted(set(policies) - set(contract.conflict_policies))
    if wider:
        raise UqsError(
            f"--conflict {', '.join(wider)}: contract {contract.identity} permits only "
            f"{', '.join(contract.conflict_policies)} - approval may narrow it, never widen it"
        )
    if not 0 < max_rows <= 10_000_000:
        raise UqsError(f"--max-rows {max_rows} must be between 1 and 10000000")
    dest = _contract_destination(paths, attachment, contract, attach_root)
    att = config.load(attachment, attach_root)
    record = {
        "contract": contract.body,
        "binding": {
            "attachment": att.name,
            "destination": dest.identity,
            "host": dest.host,
            "stack_id": dest.stack_id,
            "hdb_dir": str(dest.hdb_dir),
            "users": [att.stack.user],
            "conflict_policies": sorted(set(policies)),
            "max_rows": max_rows,
            "approved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "approved_by": getpass.getuser(),
        },
    }
    folder = approved_dir(attachment, attach_root)
    if dry_run:
        return {**record, "path": str(folder / f"{contract.identity}.json")}
    with config.locked(attachment, attach_root):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{contract.identity}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
    return {**record, "path": str(path)}


def records(attachment: str, attach_root: Path | None = None) -> list[dict[str, Any]]:
    """Every contract approved for `attachment`, oldest approval first."""
    folder = approved_dir(attachment, attach_root)
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            found.append({**json.loads(path.read_text()), "path": str(path)})
        except ValueError:
            continue
    return sorted(found, key=lambda r: str(r.get("binding", {}).get("approved_at", "")))


def approved(
    paths: UqsPaths, attachment: str, identity: str, attach_root: Path | None = None
) -> tuple[Contract, dict[str, Any], Destination, Path]:
    """The approved contract `identity` (or an unambiguous prefix of it) for
    `attachment`: the contract, re-validated; its binding; the destination the
    attachment gives it now, which must be the one approved; and the record's file."""
    if not re.fullmatch(r"[0-9a-f]{6,16}", identity):
        raise UqsError(f"--contract {identity!r} is not a contract identity (hex digits)")
    folder = approved_dir(attachment, attach_root)
    found = sorted(folder.glob(f"{identity}*.json")) if folder.is_dir() else []
    found = [p for p in found if _IDENTITY.fullmatch(p.stem)]
    if not found:
        raise UqsError(
            f"no contract {identity} is approved for attachment {attachment}: export it where "
            "the worker is (`uqs job attachment contract export WORKER > FILE`), then approve "
            f"it here (`uqs job attachment contract approve FILE --attach {attachment}`)"
        )
    if len(found) > 1:
        raise UqsError(
            f"--contract {identity} names {len(found)} approved contracts "
            f"({', '.join(p.stem for p in found)}): give more of its identity"
        )
    path = found[0]
    try:
        record = json.loads(path.read_text())
    except ValueError as exc:
        raise ContractError(f"{path} is not JSON ({exc})") from None
    if not isinstance(record, dict) or not isinstance(record.get("binding"), dict):
        raise ContractError(f"{path} is not an approved contract record")
    contract = parse(record.get("contract"))
    if contract.identity != path.stem:
        raise ContractError(f"{path} holds contract {contract.identity}, not {path.stem}")
    binding = record["binding"]
    dest = _contract_destination(paths, attachment, contract, attach_root)
    if binding.get("destination") != dest.identity:
        raise UqsError(
            f"contract {contract.identity} was approved for destination "
            f"{binding.get('destination')} ({binding.get('hdb_dir')}), but attachment "
            f"{attachment} now names {dest.identity} ({dest.hdb_dir}): approve it again"
        )
    return contract, binding, dest, path
