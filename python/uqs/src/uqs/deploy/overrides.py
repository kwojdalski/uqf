"""Named policy overrides for `uqs deploy push`, each with a reason and each
recorded (#1557).

A target can require an artifact digest (`expected_sha256`) and a q version
(`q_version`: `>=4.1` a minimum, `4.1` exactly), and any local artifact must
have its `.sha256` beside it. Each requirement has one override that relaxes
it and nothing else:

    --accept-digest REASON      an artifact whose sha256 is not expected_sha256
    --accept-q-version REASON   a server q outside q_version
    --allow-unpinned REASON     an artifact with no .sha256 and no expected_sha256

None of them bypasses anything else. The archive's members are still checked
against its manifest, the platform still has to match, and smoke and verify
still run. Hashing is never switched off either: the artifact's ACTUAL digest is
recorded, and the server checks the bytes it receives against that digest.
Accepting a digest does not mean trusting the artifact.

Each override requested lands in one list, `exceptions`. The plan shows it at
the top, the release's report keeps it, and so does this machine's deploy
history. `applied` says whether the override was needed: false when the
requirement was met anyway. --accept-converted-release (#936) is recorded in
the same list.
"""

from __future__ import annotations

from uqs.deploy.artifact import Artifact, meets
from uqs.deploy.config import Config, DeployError

ACCEPT_DIGEST = "accept-digest"
ACCEPT_Q_VERSION = "accept-q-version"
ALLOW_UNPINNED = "allow-unpinned"
ACCEPT_CONVERTED = "accept-converted-release"


def _entry(override: str, reason: str, requirement: str, observed: str, applied: bool) -> dict:
    return {
        "override": override,
        "reason": reason,
        "requirement": requirement,
        "observed": observed,
        "applied": applied,
    }


def for_artifact(cfg: Config, pkg: Artifact) -> list[dict]:
    """The digest and pin policy for `pkg`, which read_artifact has already
    checked whole: refused unless an override relaxes it, else the exceptions
    requested."""
    return _pinned(cfg, pkg.path.name, pkg.sha256, pkg.pinned, cfg.expected_sha256, "")


def for_runtime(cfg: Config, name: str, sha256: str, pinned: bool) -> list[dict]:
    """The same policy for a bundled TorQ runtime (deploy/torq_runtime.py),
    pinned by torq_runtime_sha256: its exceptions name it."""
    return _pinned(cfg, name, sha256, pinned, cfg.torq_runtime_sha256, "the torq runtime's ")


def _pinned(
    cfg: Config, name: str, sha256: str, pinned: bool, expected: str | None, whose: str
) -> list[dict]:
    key = "torq_runtime_sha256" if whose else "expected_sha256"
    out = []
    if expected is not None:
        matched = sha256 == expected
        if not matched and cfg.accept_digest is None:
            raise DeployError(
                "artifact",
                f"{name} is {sha256}, not the {key} {expected} - pass --accept-digest REASON "
                "to deploy it anyway",
            )
        if cfg.accept_digest is not None:
            out.append(
                _entry(
                    ACCEPT_DIGEST,
                    cfg.accept_digest,
                    f"{whose}sha256 {expected}",
                    f"{whose}sha256 {sha256}",
                    not matched,
                )
            )
    else:
        if not pinned and cfg.allow_unpinned is None:
            raise DeployError(
                "artifact",
                f"{name} has no {name}.sha256 beside it and no {key} pins it - pass "
                "--allow-unpinned REASON to deploy it anyway",
            )
        if cfg.allow_unpinned is not None:
            observed = f"{whose}sha256 {sha256}, " + ("pinned" if pinned else "no .sha256")
            out.append(
                _entry(
                    ALLOW_UNPINNED,
                    cfg.allow_unpinned,
                    f"a .sha256 beside {'the torq runtime' if whose else 'the artifact'}",
                    observed,
                    not pinned,
                )
            )
    return out


def for_q(cfg: Config, observed: str) -> list[dict]:
    """The q_version policy for the q preflight ran (`observed`, its .z.K):
    refused unless --accept-q-version relaxes it, else the exceptions requested.
    A version preflight could not read does not meet any requirement."""
    if cfg.q_version is None:
        return []
    ok = meets(cfg.q_version, observed)
    seen = observed or "unknown"
    if not ok and cfg.accept_q_version is None:
        raise DeployError(
            "preflight",
            f"the server's q is {seen}, and q_version requires {cfg.q_version} - pass "
            "--accept-q-version REASON to deploy on it anyway",
        )
    if cfg.accept_q_version is None:
        return []
    return [_entry(ACCEPT_Q_VERSION, cfg.accept_q_version, cfg.q_version, seen, not ok)]


def converted(record: dict) -> dict:
    """--accept-converted-release's record (deploy/converted_override.py), as one
    exception."""
    return {
        "override": ACCEPT_CONVERTED,
        "reason": record["reason"],
        "requirement": f"kdb+ {record.get('target_q')}",
        "observed": record.get("server_q") or "unknown",
        "applied": True,
        "record": record,
    }


def plan_lines(cfg: Config, exceptions: list[dict]) -> list[str]:
    """The EXCEPTIONS block at the top of the plan; nothing when none was asked."""
    lines = []
    for e in exceptions:
        what = (
            f"{e['requirement']} required, {e['observed']} found"
            if e["applied"]
            else f"not needed, {e['observed']} found"
        )
        lines.append(f"  --{e['override']}: {what}; reason: {e['reason']}")
        if e["override"] == ACCEPT_DIGEST and e["applied"]:
            lines.append("    the server still checks the bytes it receives against that sha256")
    if cfg.accept_converted_release is not None:
        lines.append(
            f"  --{ACCEPT_CONVERTED}: recorded in the release once its smoke test passes; "
            f"reason: {cfg.accept_converted_release}"
        )
    if not lines:
        return []
    return ["EXCEPTIONS - policy relaxed for this deployment:", *lines, ""]
