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

log = get_logger(__name__)

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
    """One attachment's publisher, as the start path needs to know it."""

    attachment: str
    procname: str
    stack_id: int
    state: str


def publishers(root: Path | None = None) -> list[Published]:
    """Every saved attachment's publisher and manifest state ("planned" when
    nothing has been applied). An attachment whose files cannot be read is
    skipped, with a debug line: one broken folder must not stop an unrelated start."""
    base = root or state_root()
    found: list[Published] = []
    if not base.is_dir():
        return found
    for folder in sorted(base.iterdir()):
        try:
            cfg = json.loads((folder / CONFIG_FILE).read_text())
            manifest = folder / MANIFEST_FILE
            state = json.loads(manifest.read_text())["state"] if manifest.is_file() else "planned"
            found.append(
                Published(folder.name, cfg["publisher"]["procname"], int(cfg["stack"]["id"]), state)
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.debug("attachment {} skipped: {}", folder.name, exc)
    return found


def refuse_unverified_start(paths: UqsPaths, procs: str, base_port: int | None) -> None:
    """Refuse a start of an attachment's publisher unless it was verified AND
    is started under its attachment's stack.

    Two refusals, both about the same publisher: held stopped until `verify`
    has passed (the table it publishes may not be there yet - rows published
    onto a table a tickerplant lacks are discarded without a word), and never
    started with uqf's default base port, which looks for discovery at the
    wrong address. `procs` is `start_policy.started_procs`'s words; `all`
    starts only startwithall rows, which a publisher is not.
    """
    wanted = set(procs.split())
    if not wanted or "all" in wanted:
        return
    effective = paths.runtime_declaration.resolve_base_port(base_port)
    for pub in publishers():
        if pub.procname not in wanted:
            continue
        if pub.state != VERIFIED:
            raise UqsError(
                f"{pub.procname} is the publisher of attachment {pub.attachment} (stack "
                f"{pub.stack_id}), which is {pub.state}: it may start once "
                f"`uqs job attach verify {pub.attachment}` has passed"
            )
        if effective != pub.stack_id:
            raise UqsError(
                f"{pub.procname} belongs to attachment {pub.attachment} on stack {pub.stack_id}; "
                f"started under base port {effective} it would look for discovery at "
                f"{effective + 1}, not {pub.stack_id + 1}. Start it with "
                f"`uqs job attach start {pub.attachment}`, or --port {pub.stack_id}"
            )
