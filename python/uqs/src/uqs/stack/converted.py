"""A release whose q was converted for an older kdb+ at build time, recognised
where it starts (#936).

`uqs deploy build --q-target 4.0` flattens the release's q (deploy/portable.py)
and records it in RELEASE_MANIFEST.json: `target.q`, and `portable` - which
files were transformed - beside a sha256 for every file. The startup guard
(qtree.refuse_unloadable) asked only the runtime's declared `q_tree`, so a
converted release on kdb+ 4.0 was refused as though it were the tree as
written, after its smoke test and HDB checks had passed on that same q.

This reads that evidence instead, and believes it only when it holds:

  - the manifest's `target.q` and `portable.q` agree, and name a kdb+ the
    converter targets;
  - every q file the manifest lists is on disk with the sha256 it records -
    a converted file edited, or swapped for the source as written, is not
    the release that was smoke-tested;
  - the server's q is at least the version the release was converted for.

Evidence that is present but does not hold is refused, by name: it is not
read as "unconverted", which would hide a damaged release behind the older
message. A release with no conversion recorded is the tree as written, and
the caller refuses it on a q older than 5.0 exactly as before.

THE OVERRIDE is for the one case recognition cannot settle: a server q older
than the release's target, or one whose version cannot be compared. It means
"start this verified, already-converted release", never "skip the checks":
the conversion record and every file's integrity are still required, and it
holds only for the deployment attempt that wrote it - see OVERRIDE.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import UqsError

#: The release's manifest, at its root (deploy.artifact.MANIFEST - held equal
#: by test_converted.py, since stack/ does not reach into deploy/).
MANIFEST = "RELEASE_MANIFEST.json"
#: What `uqs deploy push --accept-converted-release` writes into the release
#: once the smoke test has passed on the server's q.
OVERRIDE = "CONVERTED_RELEASE_OVERRIDE.json"
#: The deployment attempt's token; `uqs start` honours OVERRIDE only when its
#: token is this variable's value, so a later start by hand does not inherit it.
ATTEMPT_VAR = "UQS_CONVERTED_RELEASE_ATTEMPT"
#: The kdb+ versions the converter targets (deploy/portable.Q_TARGETS).
TARGETS = ("4.0",)
#: How many integrity failures a refusal names before counting the rest.
SHOWN = 5


@dataclass(frozen=True)
class Evidence:
    """A converted release, checked: what it was converted for, how many q
    files were verified, and the override it started under, if any."""

    release: str
    q: str
    checked: int
    override: dict | None = None


def recorded_target(root: Path) -> str | None:
    """The kdb+ `root`'s q was converted for, as its manifest records it; None
    for a checkout, or a release built as written. Unchecked - for deciding
    which code a runtime loads, never whether to start it."""
    manifest = _manifest(root)
    return ((manifest or {}).get("portable") or {}).get("q")


def _manifest(root: Path) -> dict | None:
    path = root / MANIFEST
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise UqsError(f"{path} is not JSON - this release's record of itself is damaged") from None


def _version(text: str) -> tuple[int, ...] | None:
    parts = text.strip().split(".")[:2]
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _refuse(root: Path, why: str) -> UqsError:
    return UqsError(
        f"{root / MANIFEST} records a q conversion, but {why}. The release cannot be "
        "trusted as converted, so nothing was started - redeploy the artifact"
    )


def _checked(root: Path, manifest: dict) -> tuple[str, int]:
    """(target, q files verified), or the reason the record does not hold."""
    target = (manifest.get("target") or {}).get("q")
    portable = manifest.get("portable") or {}
    if target != portable.get("q"):
        raise _refuse(
            root,
            f"its target says kdb+ {target or 'as written'} and its conversion "
            f"says {portable.get('q') or 'none'}",
        )
    if target not in TARGETS:
        raise _refuse(root, f"kdb+ {target} is not a version the converter targets")
    files = manifest.get("files") or {}
    unlisted = [f for f in portable.get("transformed", []) if f not in files]
    if unlisted:
        raise _refuse(root, f"it converted {', '.join(unlisted[:SHOWN])}, which it does not ship")
    qfiles = sorted(f for f in files if f.endswith(".q"))
    bad = []
    for rel in qfiles:
        path = root / rel
        if not path.is_file():
            bad.append(f"{rel} is missing")
        elif _sha256(path) != files[rel]:
            bad.append(f"{rel} differs from the release")
    if bad:
        more = f", and {len(bad) - SHOWN} more" if len(bad) > SHOWN else ""
        raise _refuse(root, "its q is not what was built: " + "; ".join(bad[:SHOWN]) + more)
    return target, len(qfiles)


def _override(root: Path, release: str, env: Mapping[str, str]) -> dict | None:
    """The override this deployment attempt wrote, checked; None when there is
    none for this attempt. Refused, by name, when it is present but not this
    release's, or claims no passing smoke test."""
    token = env.get(ATTEMPT_VAR, "")
    path = root / OVERRIDE
    if not token or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise UqsError(f"{path} is not JSON - nothing was started") from None
    if record.get("attempt") != token:
        return None  # another attempt's: it does not carry over
    problems = []
    if record.get("release") != release:
        problems.append(f"it is for release {record.get('release')}, not {release}")
    if record.get("smoke") != "ok":
        problems.append("it records no passing smoke test on this server's q")
    if not str(record.get("reason") or "").strip():
        problems.append("it gives no reason")
    if problems:
        raise UqsError(f"{path} cannot authorise this start: {'; '.join(problems)}")
    return record


def recognise(root: Path, server_version: str, env: Mapping[str, str]) -> Evidence | None:
    """The checked evidence that `root` is a release converted for a q like
    the server's; None when it records no conversion (the tree as written).

    Raises when a conversion is recorded but does not hold, or when the
    server's q is older than - or not comparable with - the release's target
    and no valid override for this deployment attempt says to start it."""
    manifest = _manifest(root)
    if manifest is None or not (
        manifest.get("portable") or (manifest.get("target") or {}).get("q")
    ):
        return None
    release = str(manifest.get("release", ""))
    target, checked = _checked(root, manifest)
    have, want = _version(server_version), _version(target)
    if have is not None and want is not None and have >= want:
        return Evidence(release, target, checked)
    override = _override(root, release, env)
    if override is None:
        raise UqsError(
            f"release {release} was converted for kdb+ {target}, and this q reports "
            f"{server_version or 'no version'}. Deploy it with --accept-converted-release "
            "REASON once its smoke test passes on this q, or use a q of that version. "
            "Nothing was started"
        )
    return Evidence(release, target, checked, override)
