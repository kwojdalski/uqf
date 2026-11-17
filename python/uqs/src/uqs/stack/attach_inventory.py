"""`uqs job attachment list`: one row per saved attachment, and whether its
publisher is running now (#2768).

The lifecycle state is attach_state.py's (the manifest's, read as the start
gate reads it); this adds what a TCP connect to the publisher's port says.
Reads the two JSON files and connects to one port; changes nothing, and takes
no lock.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from uqs.logger import get_logger
from uqs.paths import repo_root
from uqs.stack.attach_state import CONFIG_FILE, PUBLISHER_HOST, UNREADABLE, scan, state_root

log = get_logger()

#: What a TCP connect to the publisher's port says, as `job attachment list` shows it.
RUNNING, STOPPED, UNREACHABLE, UNKNOWN = "running", "stopped", "unreachable", "unknown"


def _port_state(host: str, port: int, timeout: float = 0.5) -> tuple[str, str]:
    """(state, detail) of host:port. A refused connection is the host answering
    that nothing listens - stopped; anything else (no route, no name, a
    timeout) says nothing about the publisher - unreachable, never stopped."""
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


def inventory(root: Path | None = None, repo: Path | None = None) -> list[dict[str, Any]]:
    """One row per attachment: what it is, where, its lifecycle state, and -
    separately - whether its publisher is running now. Reads the two JSON
    files and connects to one port - the publisher's, on PUBLISHER_HOST, not
    on the stack's host; changes nothing, and takes no lock."""
    rows: list[dict[str, Any]] = []
    base = root or state_root()
    published, broken = scan(base)
    for pub in published:
        try:
            cfg = json.loads((base / pub.attachment / CONFIG_FILE).read_text())
            job, host = str(cfg["publisher"]["job"]), str(cfg["stack"]["host"])
            persist = str(cfg.get("persist", ""))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            job, host, persist = "", "", ""
            log.warning("attachment {}: {}", pub.attachment, exc)
        port = _publisher_port(pub.procname, pub.stack_id, repo or repo_root())
        if port is None:
            running, detail = UNKNOWN, "the publisher's port cannot be worked out"
        else:
            running, detail = _port_state(PUBLISHER_HOST, port)
        rows.append(
            {
                "attachment": pub.attachment,
                "job": job,
                "publisher": pub.procname,
                "stack": pub.stack_id,
                "host": host,
                "state": pub.state,
                "persist": persist,
                "publisher_state": running,
                "publisher_port": port,
                "detail": pub.problem or detail,
            }
        )
    for line in broken:
        blank = {**dict.fromkeys(("job", "publisher", "host", "persist"), ""), "stack": None}
        name = Path(line.split(" cannot be read")[0]).parent.name
        rows.append(
            {"attachment": name, **blank, "state": UNREADABLE,
             "publisher_state": UNKNOWN, "publisher_port": None, "detail": line}
        )  # fmt: skip
    return rows
