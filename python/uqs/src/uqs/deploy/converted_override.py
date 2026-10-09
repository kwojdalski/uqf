"""--accept-converted-release (#936): the override a deployment writes into a
converted release once its smoke test has passed on the server's q.

`uqs start` checks it (stack/converted.py): it holds only for the attempt
whose token the start is given, and never excuses the release's conversion
record or the integrity of its q.
"""

from __future__ import annotations

import getpass
import json
import secrets
from datetime import UTC, datetime

from uqs.deploy.remote import q
from uqs.deploy.stages import Deployment
from uqs.stack.converted import OVERRIDE


def write(dep: Deployment, release: str, facts: dict) -> dict:
    """The override's record, written into `release`; the report keeps it."""
    record = {
        "attempt": secrets.token_hex(8),
        "release": dep.release,
        "reason": dep.cfg.accept_converted_release,
        "requested_by": getpass.getuser(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target_q": dep.target.get("q"),
        "server_q": facts.get("qversion", ""),
        "smoke": "ok",
    }
    body = json.dumps(record, indent=2)
    dep.run(
        "start",
        "recording the converted-release override",
        *dep.in_release(release, f"printf '%s\\n' {q(body)} > {q(OVERRIDE)}"),
    )
    return record
