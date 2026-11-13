"""Named policy overrides for `uqs deploy push`, each with a reason and each
recorded (#1557).

A target can require an artifact digest (`expected_sha256`) and a q version
(`q_version`: `>=4.1` a minimum, `4.1` exactly), and any local artifact must
have its `.sha256` beside it. Each requirement has one override that relaxes
it and nothing else:

    --override accept-digest      an artifact whose sha256 is not expected_sha256
    --override accept-q-version   a server q outside q_version
    --override allow-unpinned     an artifact with no .sha256 and no expected_sha256

each with `--reason TEXT` (#1732). So are the bypasses that were bare flags
until #1852, each now a named override with a recorded reason:

    --override allow-dirty           an artifact (or a build) from uncommitted changes
    --override fix-hdb               fill the shared HDB's missing tables and columns
    --override break-lock            remove a deploy lock whose holder stopped beating

and preflight's free-space floor (#2743) and a sidecar job's declared q
(#2747) have theirs:

    --override allow-low-disk        push onto a filesystem with less free than it needs
    --override accept-job-q          start a --jobs job on a q its `q` key does not name

and `deploy build --q-version`'s `--override allow-computed-names` is the
converter's own (deploy/portable.py).

None of them bypasses anything else. The archive's members are still checked
against its manifest, the platform still has to match, and smoke and verify
still run. Hashing is never switched off either: the artifact's ACTUAL digest is
recorded, and the server checks the bytes it receives against that digest.
Accepting a digest does not mean trusting the artifact.

Each override requested lands in one list, `exceptions`. The plan shows it at
the top, the release's report keeps it, and so does this machine's deploy
history. `applied` says whether the override was needed: false when the
requirement was met anyway. --override accept-converted-release (#936) is recorded in
the same list.
"""

from __future__ import annotations

from uqs.deploy import preflight
from uqs.deploy.artifact import Artifact, meets
from uqs.deploy.config import Config, DeployError
from uqs.deploy.selection import Selection
from uqs.guardrails import Guardrail
from uqs.interpreter import KDBX
from uqs.model import q_support

ACCEPT_DIGEST = "accept-digest"
ACCEPT_Q_VERSION = "accept-q-version"
ALLOW_UNPINNED = "allow-unpinned"
ACCEPT_CONVERTED = "accept-converted-release"
ALLOW_DIRTY = "allow-dirty"
FIX_HDB = "fix-hdb"
BREAK_LOCK = "break-lock"
ALLOW_LOW_DISK = "allow-low-disk"
ACCEPT_JOB_Q = "accept-job-q"

_PUSH = ("deploy push",)
#: The overrides that were option-per-override before #1732, each then taking
#: its reason as its value: their old spellings are value-taking (cli/deploy_policy.py).
POLICY = (ACCEPT_DIGEST, ACCEPT_Q_VERSION, ALLOW_UNPINNED, ACCEPT_CONVERTED)
#: Each override, as `--override NAME` gives it, and the requirement it relaxes (#1825).
GUARDRAILS = (
    Guardrail(ACCEPT_DIGEST, _PUSH, "deploy an artifact whose sha256 is not expected_sha256"),
    Guardrail(
        ACCEPT_Q_VERSION,
        _PUSH,
        "deploy on a server q outside q_version; the version found is recorded",
    ),
    Guardrail(
        ALLOW_UNPINNED,
        _PUSH,
        "deploy a local artifact with no .sha256 beside it and no expected_sha256",
    ),
    Guardrail(
        ACCEPT_CONVERTED,
        _PUSH,
        "start a release converted for an older kdb+ on a q its version check "
        "cannot confirm, once its smoke test passes there",
    ),
    # Bare flags until #1852: each a bypass with no reason recorded.
    Guardrail(
        ALLOW_DIRTY,
        ("deploy build", "deploy push"),
        "build, or deploy, an artifact from uncommitted changes (its manifest says dirty)",
        risk="what runs is no commit anyone can check out again",
        action="commit, and build from the commit - ideally in CI",
    ),
    Guardrail(
        FIX_HDB,
        _PUSH,
        "fill the shared HDB's partitions that lack a table or column this release declares, "
        "by running its `uqs data check --apply` (additive), instead of refusing them; a "
        "changed type is still refused",
        risk="every partition of the shared HDB is written to",
    ),
    Guardrail(
        BREAK_LOCK,
        ("deploy push", "deploy rollback"),
        "remove a deploy lock whose holder stopped beating; still refused while it beats",
        risk="a deployment that is only slow, not dead, loses its lock",
    ),
    Guardrail(
        ALLOW_LOW_DISK,
        _PUSH,
        "push onto a filesystem holding --dest or the data directory with less free than "
        f"{preflight.DISK_FACTOR} times the artifact plus a "
        f"{preflight.DISK_MARGIN >> 30} GiB margin",
        risk="the filesystem fills, taking down the processes - and anything else - writing to it",
        action="free space there, or `uqs deploy prune` the old releases",
    ),
    Guardrail(
        ACCEPT_JOB_Q,
        _PUSH,
        "start a --jobs job on a server q its `q` key does not name; the q found is recorded",
        risk="the job's process dies on what that q lacks, the failure only in its log",
        action="check the job came up and stayed up (`uqs status`, `uqs logs PROC`)",
    ),
)
#: {name: what it relaxes} for `deploy push`'s overrides: make_config's fields,
#: each the reason or None, and what a target file may not set (targets.OVERRIDES).
SKIPS = {g.id: g.description for g in GUARDRAILS if "deploy push" in g.commands}


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
    return _pinned(cfg, pkg.path.name, pkg.sha256, pkg.pinned, "")


def for_runtime(cfg: Config, component: str, name: str, sha256: str, pinned: bool) -> list[dict]:
    """The same policy for a bundled runtime - TorQ's or q's
    (deploy/torq_runtime.py, deploy/q_runtime.py) - pinned by
    <component>_component_sha256: its exceptions name it."""
    return _pinned(cfg, name, sha256, pinned, component)


def _pinned(cfg: Config, name: str, sha256: str, pinned: bool, component: str) -> list[dict]:
    key = f"{component}_component_sha256" if component else "expected_sha256"
    expected = getattr(cfg, key)
    whose = f"the {component} runtime's " if component else ""
    beside = f"the {component} runtime" if component else "the artifact"
    out = []
    if expected is not None:
        matched = sha256 == expected
        if not matched and cfg.accept_digest is None:
            raise DeployError(
                "artifact",
                f"{name} is {sha256}, not the {key} {expected} - pass "
                "--override accept-digest --reason REASON to deploy it anyway",
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
                "--override allow-unpinned --reason REASON to deploy it anyway",
            )
        if cfg.allow_unpinned is not None:
            observed = f"{whose}sha256 {sha256}, " + ("pinned" if pinned else "no .sha256")
            out.append(
                _entry(
                    ALLOW_UNPINNED,
                    cfg.allow_unpinned,
                    f"a .sha256 beside {beside}",
                    observed,
                    not pinned,
                )
            )
    return out


def for_q(cfg: Config, observed: str) -> list[dict]:
    """The q_version policy for the q preflight ran (`observed`, its .z.K):
    refused unless --override accept-q-version relaxes it, else the exceptions requested.
    A version preflight could not read does not meet any requirement."""
    if cfg.q_version is None:
        return []
    ok = meets(cfg.q_version, observed)
    seen = observed or "unknown"
    if not ok and cfg.accept_q_version is None:
        raise DeployError(
            "preflight",
            f"the server's q is {seen}, and q_version requires {cfg.q_version} - pass "
            "--override accept-q-version --reason REASON to deploy on it anyway",
        )
    if cfg.accept_q_version is None:
        return []
    return [_entry(ACCEPT_Q_VERSION, cfg.accept_q_version, cfg.q_version, seen, not ok)]


def for_job_q(cfg: Config, selection: Selection, facts: dict[str, str]) -> list[dict]:
    """Each --jobs job's declared q (its `q` key, #2747) against the q preflight
    found - its .z.K, and PeachQ when the release bundles one: refused unless
    --override accept-job-q relaxes it, else the exceptions requested. A
    version preflight could not read meets no version a job names."""
    if not selection.q:
        return []
    impl, version = facts.get("qimpl") or KDBX, facts.get("qversion", "")
    declared = {name: q_support.parse(text) for name, text in selection.q.items()}
    found = q_support.unsupported(declared, impl, version)
    if found and cfg.accept_job_q is None:
        raise DeployError(
            "preflight",
            q_support.problem(
                found, impl, version,
                "deploy without those jobs, or pass --override accept-job-q --reason REASON",
            ),
        )  # fmt: skip
    if cfg.accept_job_q is None:
        return []
    requirement = "; ".join(f'{n}: q "{t}"' for n, t in sorted(selection.q.items()))
    observed = q_support.described(impl, version)
    return [_entry(ACCEPT_JOB_Q, cfg.accept_job_q, requirement, observed, bool(found))]


def for_disk(cfg: Config, facts: dict[str, str], artifact_bytes: int) -> list[dict]:
    """The free-space floor for the filesystems preflight found holding --dest
    and the data directory (its disk_dest and disk_data: kB free, then the
    mount point), each judged once: refused unless --override allow-low-disk
    relaxes it, else the exceptions requested. The release lands on --dest's
    filesystem, which needs DISK_FACTOR times the artifact plus DISK_MARGIN; a
    data directory on a filesystem of its own needs only the margin."""
    release = preflight.DISK_FACTOR * artifact_bytes + preflight.DISK_MARGIN
    requirement = (
        f"{_size(release)} free where the release lands, {_size(preflight.DISK_MARGIN)} elsewhere"
    )
    why = (
        f"{preflight.DISK_FACTOR} times the artifact's {_size(artifact_bytes)}, "
        f"plus a {_size(preflight.DISK_MARGIN)} margin"
    )
    short, seen = [], set()
    for key, path, need in (
        ("disk_dest", cfg.dest, release),
        ("disk_data", cfg.data_root, preflight.DISK_MARGIN),
    ):
        if key not in facts:
            continue
        free_kb, _, mount = facts[key].partition(" ")
        if mount in seen:
            continue
        seen.add(mount)
        free = int(free_kb) * 1024
        if free < need:
            short.append(
                f"the filesystem {mount} (holding {path}) has {_size(free)} free "
                f"and needs {_size(need)}"
            )
    if short and cfg.allow_low_disk is None:
        raise DeployError(
            "preflight",
            f"{'; '.join(short)} - this push needs {requirement} ({why}). Free space there "
            "(`uqs deploy prune` removes old releases), or pass "
            "--override allow-low-disk --reason REASON to deploy anyway",
        )
    if cfg.allow_low_disk is None:
        return []
    observed = "; ".join(short) or "enough free"
    return [_entry(ALLOW_LOW_DISK, cfg.allow_low_disk, requirement, observed, bool(short))]


def _size(n: float) -> str:
    """`n` bytes, in the largest binary unit it is at least one of."""
    for unit in ("B", "KiB", "MiB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def for_flags(cfg: Config, dirty: bool) -> list[dict]:
    """The exceptions for the overrides that were bare flags (#1852), each with
    its reason: allow-dirty applied when the artifact is dirty, the others as
    requested - whether a lock is broken or a partition filled is decided
    later, on the server."""
    out = []
    if cfg.allow_dirty:
        state = "dirty: true" if dirty else "dirty: false"
        out.append(_entry(ALLOW_DIRTY, str(cfg.allow_dirty), "a clean artifact", state, dirty))
    if cfg.fix_hdb:
        out.append(_entry(FIX_HDB, str(cfg.fix_hdb), "no partition filled", "fill requested", True))
    if cfg.break_lock:
        out.append(_entry(BREAK_LOCK, str(cfg.break_lock), "a free lock", "break requested", True))
    return out


def converted(record: dict) -> dict:
    """--override accept-converted-release's record (deploy/converted_override.py), as one
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
        lines.append(f"  --override {e['override']}: {what}; reason: {e['reason']}")
        if e["override"] == ACCEPT_DIGEST and e["applied"]:
            lines.append("    the server still checks the bytes it receives against that sha256")
    if cfg.accept_converted_release is not None:
        lines.append(
            f"  --override {ACCEPT_CONVERTED}: recorded in the release once its smoke test passes; "
            f"reason: {cfg.accept_converted_release}"
        )
    if not lines:
        return []
    return ["EXCEPTIONS - policy relaxed for this deployment:", *lines, ""]
