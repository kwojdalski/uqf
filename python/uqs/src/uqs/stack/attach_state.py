"""Where attachments are kept, and the one question the start path asks of them.

`uqs job attach` (uqs.attach) keeps one folder per attachment: its saved
configuration and its operation manifest. The START PATH has to consult them -
a publisher whose attachment has not been verified, or one started under the
wrong base port, must be refused wherever the start comes from (`uqs start`,
the MCP server, the browser) - and `stack` may not import `attach`, which sits
above it. So the location, the file names and the gate live here, in the layer
both can reach, and uqs.attach.config re-exports them.

Reads the two JSON files by hand, with no knowledge of their other contents:
this module's only questions are "which process does this attachment publish
from", "which stack", and "what state is it in".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths, repo_root
from uqs.stack.procs import effective_process_rows

log = get_logger()

ATTACH_DIR_ENV = "UQS_ATTACH_DIR"
CONFIG_FILE = "attachment.json"
MANIFEST_FILE = "manifest.json"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
#: The only manifest state in which the publisher may start.
VERIFIED = "verified"


def state_root() -> Path:
    """`$UQS_ATTACH_DIR`, else `$UQS_DATA_ROOT` or the checkout's `output/`, plus `attachments/`."""
    configured = os.environ.get(ATTACH_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    data_root = os.environ.get("UQS_DATA_ROOT", "").strip()
    return (Path(data_root).expanduser() if data_root else repo_root() / "output") / "attachments"


def directory(name: str, root: Path | None = None) -> Path:
    if not NAME.match(name):
        raise UqsError(
            f"{name!r} is not an attachment name - lowercase letters, digits and '-', "
            "starting with a letter"
        )
    return (root or state_root()) / name


@dataclass(frozen=True)
class Published:
    """One attachment's publisher, as the start path needs to know it.

    `state` is the manifest's, "planned" when nothing has been applied, or
    UNREADABLE when the manifest is there and cannot be read - which the gate
    refuses like any other state but VERIFIED (#1442); `problem` says why.
    """

    attachment: str
    procname: str
    stack_id: int
    state: str
    problem: str = ""


#: The state of a publisher whose manifest exists but cannot be read (#1442).
UNREADABLE = "unreadable"


def _scan(root: Path | None = None) -> tuple[list[Published], list[str]]:
    """Every attachment's publisher, and a line for each attachment whose
    publisher cannot be known (its folder or attachment.json unreadable)."""
    base = root or state_root()
    found: list[Published] = []
    broken: list[str] = []
    try:
        if not base.is_dir():
            return found, broken
        folders = sorted(base.iterdir())
    except OSError as exc:
        return found, [f"{base} cannot be listed ({exc})"]
    for folder in folders:
        cfg_path = folder / CONFIG_FILE
        if not folder.is_dir():
            continue
        try:
            # read, not is_file first: is_file says False for a folder this
            # user may not enter, which would make that attachment invisible
            cfg = json.loads(cfg_path.read_text())
            procname, stack_id = cfg["publisher"]["procname"], int(cfg["stack"]["id"])
            if not isinstance(procname, str) or not procname:
                raise ValueError(f"publisher.procname is {procname!r}")
        except FileNotFoundError, NotADirectoryError:
            continue  # not an attachment: config.save writes attachment.json first
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("attachment {}: {} cannot be read: {}", folder.name, cfg_path, exc)
            broken.append(f"{cfg_path} cannot be read ({exc})")
            continue
        state, problem = _manifest_state(folder / MANIFEST_FILE)
        found.append(Published(folder.name, procname, stack_id, state, problem))
    return found, broken


def _manifest_state(manifest: Path) -> tuple[str, str]:
    """(state, problem): "planned" with no manifest, UNREADABLE with a reason
    when one is there but unreadable, truncated, or without a string `state`."""
    try:
        state = json.loads(manifest.read_text())["state"]
        if not isinstance(state, str) or not state:
            raise ValueError(f"its state is {state!r}")
        return state, ""
    except FileNotFoundError:
        return "planned", ""
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reason = f"no state key ({exc})" if isinstance(exc, KeyError) else str(exc)
        log.warning("attachment manifest {} cannot be read: {}", manifest, reason)
        return UNREADABLE, f"{manifest} cannot be read ({reason})"


def publishers(root: Path | None = None) -> list[Published]:
    """Every saved attachment's publisher and manifest state ("planned" when
    nothing has been applied, UNREADABLE when the manifest cannot be read).
    An attachment whose attachment.json cannot be read is left out, with a
    warning - refuse_unverified_start refuses every start over it instead."""
    return _scan(root)[0]


def _started_by(paths: UqsPaths, wanted: set[str]) -> set[str]:
    """The processes a start of `wanted` launches: `all` (or nothing named,
    which torq.sh reads as `all`) is every startwithall=1 row of the
    effective process.csv - the same expansion deployment.py records (#1441)."""
    if wanted and "all" not in wanted:
        return wanted
    every = {
        row["procname"] for row in effective_process_rows(paths) if row.get("startwithall") == "1"
    }
    return (wanted - {"all"}) | every


def refuse_unverified_start(paths: UqsPaths, procs: str, base_port: int | None) -> None:
    """Refuse a start of an attachment's publisher unless it was verified AND
    is started under its attachment's stack.

    Two refusals, both about the same publisher: held stopped until `verify`
    has passed (the table it publishes may not be there yet - rows published
    onto a table a tickerplant lacks are discarded without a word), and never
    started with uqf's default base port, which looks for discovery at the
    wrong address. `procs` is `start_policy.started_procs`'s words, and `all`
    is expanded to the startwithall=1 rows it launches before anything is
    checked: a streaming job declared `start_with_all 1b` is one of them, so
    `uqs start`, `start all` and `restart all` are gated too (#1441).

    It fails CLOSED (#1442): a publisher whose manifest cannot be read is
    refused, and an attachment whose attachment.json cannot be read - so whose
    publisher cannot be known - refuses every start until it is fixed.
    """
    found, broken = _scan()
    if broken:
        raise UqsError(
            "an attachment cannot be read, so whether this start launches its publisher "
            f"cannot be told: {'; '.join(broken)}. Make it readable to this user, or move "
            f"that folder out of {state_root()} if the attachment is gone. Nothing was started."
        )
    if not found:
        return
    wanted = _started_by(paths, set(procs.split()))
    effective = paths.runtime_declaration.resolve_base_port(base_port)
    for pub in found:
        if pub.procname not in wanted:
            continue
        if pub.state == UNREADABLE:
            raise UqsError(
                f"{pub.procname} is the publisher of attachment {pub.attachment} (stack "
                f"{pub.stack_id}), whose manifest is unreadable: {pub.problem}. It stays "
                "stopped until that file is readable again - it records what apply changed on "
                f"the stack, so restore it rather than delete it - and `uqs job attach "
                f"{pub.attachment}` has verified it. Nothing was started."
            )
        if pub.state != VERIFIED:
            raise UqsError(
                f"{pub.procname} is the publisher of attachment {pub.attachment} (stack "
                f"{pub.stack_id}), which is {pub.state}: it may start once "
                f"`uqs job attach {pub.attachment}` has verified it"
            )
        if effective != pub.stack_id:
            raise UqsError(
                f"{pub.procname} belongs to attachment {pub.attachment} on stack {pub.stack_id}; "
                f"started under base port {effective} it would look for discovery at "
                f"{effective + 1}, not {pub.stack_id + 1}. Start it with "
                f"`uqs job attach {pub.attachment}`, or --port {pub.stack_id}"
            )


# ------------------------------------------------------- `uqs list attachments`

#: What a TCP connect to the publisher's port says, as `list attachments` shows it.
RUNNING, STOPPED, UNREACHABLE, UNKNOWN = "running", "stopped", "unreachable", "unknown"


def _port_state(host: str, port: int, timeout: float = 0.5) -> tuple[str, str]:
    """(state, detail) of host:port. A refused connection is the host answering
    that nothing listens - stopped; anything else (no route, no name, a
    timeout) says nothing about the publisher - unreachable, never stopped."""
    import socket

    address = "127.0.0.1" if host == "localhost" else host
    try:
        with socket.create_connection((address, port), timeout):
            return RUNNING, ""
    except ConnectionRefusedError:
        return STOPPED, ""
    except OSError as exc:
        return UNREACHABLE, f"{host}:{port}: {exc.strerror or type(exc).__name__}"


def _publisher_port(procname: str, stack_id: int, root: Path) -> int | None:
    from uqs.model.registry import allocate_offsets, read_port_lock

    try:
        return stack_id + allocate_offsets([procname], read_port_lock(root))[procname]
    except OSError, ValueError, KeyError:
        return None


def inventory(root: Path | None = None, repo: Path | None = None) -> list[dict[str, str]]:
    """One row per attachment: what it is, where, its lifecycle state, and -
    separately - whether its publisher is running now. Reads the two JSON
    files and connects to one port; changes nothing, and takes no lock."""
    rows: list[dict[str, str]] = []
    base = root or state_root()
    published, broken = _scan(base)
    for pub in published:
        try:
            cfg = json.loads((base / pub.attachment / CONFIG_FILE).read_text())
            job, host = str(cfg["publisher"]["job"]), str(cfg["stack"]["host"])
            persist = str(cfg.get("persist", ""))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            job, host, persist = "", "", ""
            log.warning("attachment {}: {}", pub.attachment, exc)
        port = _publisher_port(pub.procname, pub.stack_id, repo or repo_root())
        if port is None or not host:
            running, detail = UNKNOWN, "the publisher's port cannot be worked out"
        else:
            running, detail = _port_state(host, port)
        rows.append(
            {
                "attachment": pub.attachment,
                "job": job,
                "publisher": pub.procname,
                "stack": str(pub.stack_id),
                "host": host,
                "state": pub.state,
                "persist": persist,
                "publisher_state": running,
                "publisher_port": "" if port is None else str(port),
                "detail": pub.problem or detail,
            }
        )
    for line in broken:
        blank = dict.fromkeys(("job", "publisher", "stack", "host", "persist"), "")
        name = Path(line.split(" cannot be read")[0]).parent.name
        rows.append(
            {"attachment": name, **blank, "state": UNREADABLE,
             "publisher_state": UNKNOWN, "publisher_port": "", "detail": line}
        )  # fmt: skip
    return rows
