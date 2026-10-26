"""The pins a push holds its artifacts to, and the overrides' reasons, checked
before any Config is made (#1557) - split from config.py, which calls
`checked` and turns its ValueError into the push's refusal.

Three artifacts can be pinned: the release (`expected_sha256`) and, when
bundled, the TorQ runtime (`torq_runtime_sha256`) and the q runtime
(`q_runtime_sha256`). An override with nothing to relax is refused, never
recorded as idle:

  --accept-digest   needs a pin on any of them;
  --allow-unpinned  needs an artifact no pin covers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from uqs.deploy.artifact import q_requirement


@dataclass(frozen=True)
class Pins:
    expected_sha256: str | None
    torq_runtime_sha256: str | None
    q_runtime_sha256: str | None
    q_version: str | None
    accept_digest: str | None
    accept_q_version: str | None
    allow_unpinned: str | None


def reason(flag: str, value: str | None) -> str | None:
    """An override's reason: an override nobody explained is refused."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{flag} needs a reason, recorded in the report")
    return value.strip()


def sha256(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
        raise ValueError(f"{name} {value!r} is not a sha256 (64 hex digits)")
    return value.strip().lower()


def q_version(value: str | None) -> str | None:
    if value is None:
        return None
    q_requirement(value)
    return value.strip()


def checked(
    *,
    expected_sha256: str | None,
    torq_runtime_sha256: str | None,
    q_runtime_sha256: str | None,
    q_version_: str | None,
    accept_digest: str | None,
    accept_q_version: str | None,
    allow_unpinned: str | None,
    torq_runtime: str | None,
    q_runtime: str | None,
) -> Pins:
    """Every pin and override, normalised, or the ValueError that refuses
    them. `torq_runtime` and `q_runtime` are the bundled runtimes' paths,
    when they ship."""
    pins = Pins(
        expected_sha256=sha256("expected_sha256", expected_sha256),
        torq_runtime_sha256=sha256("torq_runtime_sha256", torq_runtime_sha256),
        q_runtime_sha256=sha256("q_runtime_sha256", q_runtime_sha256),
        q_version=q_version(q_version_),
        accept_digest=reason("--accept-digest", accept_digest),
        accept_q_version=reason("--accept-q-version", accept_q_version),
        allow_unpinned=reason("--allow-unpinned", allow_unpinned),
    )
    if pins.accept_digest is not None and not (
        pins.expected_sha256 or pins.torq_runtime_sha256 or pins.q_runtime_sha256
    ):
        raise ValueError(
            "--accept-digest relaxes expected_sha256 (or torq_runtime_sha256, "
            "q_runtime_sha256), and this deployment sets none"
        )
    if pins.accept_q_version is not None and pins.q_version is None:
        raise ValueError("--accept-q-version relaxes q_version, and this deployment sets none")
    runtimes_pinned = (torq_runtime is None or pins.torq_runtime_sha256 is not None) and (
        q_runtime is None or pins.q_runtime_sha256 is not None
    )
    if pins.allow_unpinned is not None and pins.expected_sha256 is not None and runtimes_pinned:
        raise ValueError(
            "--allow-unpinned: expected_sha256 already pins the artifact - there is nothing "
            "for it to relax"
        )
    return pins
