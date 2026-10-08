"""`uqs deploy status`: what a server is running, read without ssh-ing in (#868).

Read-only, and it never takes the deploy lock - it reports whether one is
held instead, so it is safe to run while a push is in flight. Everything it
says comes from what the server itself recorded:

  releases     `current` and the release its report says it replaced, each
               with its manifest's revision, build time, dirty flag, runtime
               and bundles; and how many other releases are kept;
  last         the newest release's deploy-report.json. A failed push keeps
               its release directory, so the newest is not always `current`:
               this is where a failed stage and its rollback show up;
  health       `current`'s own verifier, with a short deadline: which
               processes answer. Plus the streaming jobs whose batches are
               failing (stream_health, #832), read by the release's own code;
  lock         held or not, and the owner line the push wrote.

Release ids begin with their UTC build time, so their names sort in the order
they were built. --json prints the dict `status` returns; its "format" key
changes when a key is renamed or removed, not when one is added.
"""

from __future__ import annotations

import json

from uqs.deploy.artifact import MANIFEST, release_runtime
from uqs.deploy.config import REPORT, Config, DeployError
from uqs.deploy.remote import Transport, q
from uqs.deploy.selection import Selection
from uqs.deploy.stages import Deployment

#: Bumped when a key of status()'s result is renamed or removed.
FORMAT = 1

#: The server's facts, one `key=value` per line; read-only.
_FACTS = """\
if [ -L {current} ]; then echo "current=$(basename "$(readlink {current})")"; fi
if [ -d {releases} ]; then
  for r in {releases}/*/; do
    if [ -d "$r" ]; then echo "release=$(basename "$r")"; fi
  done
fi
if [ -d {lock} ]; then
  echo "lock=held"
  echo "lock_owner=$(head -n 1 {lock}/owner 2>/dev/null || true)"
fi
"""

#: Run from a release with its deploy.env: every streaming job's latest
#: stream_health record, by the release's own reader. A release from before
#: #832 has no reader, and says so rather than failing the whole status.
_STREAM_HEALTH = (
    "import json\n"
    "try:\n"
    "    from uqs.paths import default_paths\n"
    "    from uqs.stack import stream_health\n"
    "except ImportError:\n"
    "    print(json.dumps(None))\n"
    "else:\n"
    "    print(json.dumps(stream_health.read(default_paths())))\n"
)


def _json_file(dep: Deployment, release: str, name: str) -> dict | None:
    """A release's JSON file, or None when it is missing or unreadable."""
    out = dep.run(
        "status",
        f"reading release {release}'s {name}",
        f"cat {q(dep.releases)}/{q(release)}/{q(name)} 2>/dev/null || true",
    )
    try:
        found = json.loads(out)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _release(dep: Deployment, release: str | None, kept: list[str]) -> dict | None:
    """What a release's manifest says it is, or None for no release."""
    if not release:
        return None
    if release not in kept:
        return {"release": release, "kept": False}
    m = _json_file(dep, release, MANIFEST) or {}
    return {
        "release": release,
        "kept": True,
        "revision": m.get("revision"),
        "dirty": m.get("dirty"),
        "built_at": m.get("created_at"),
        "runtime": release_runtime(m) if m else None,
        "q": (m.get("target") or {}).get("q"),
        "bundles": m.get("bundles") or {},
    }


def _last(dep: Deployment, newest: str) -> dict:
    report = _json_file(dep, newest, REPORT)
    if report is None:
        # a push that failed before it could write the report
        return {"release": newest, "report": False}
    keys = ("status", "stage", "error", "rollback", "profile", "previous_release", "live")
    return {"release": newest, "report": True, **{k: report.get(k) for k in keys}}


def _health(dep: Deployment, current: str, report: dict | None) -> dict:
    if not report or not report.get("profile"):
        return {"checked": False, "reason": f"release {current} has no report naming its profile"}
    release = f"{dep.releases}/{current}"
    sel = Selection(processes=list(report.get("extra_processes", [])))
    try:
        result = dep.verify(release, report["profile"], "status", sel)
    except DeployError as exc:
        result = {"passed": False, "reason": str(exc), "processes": []}
    try:
        out = dep.run(
            "status",
            "reading the streaming jobs' health",
            *dep.in_release(release, f".venv/bin/python -c {q(_STREAM_HEALTH)}"),
        )
        records = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
    except DeployError, ValueError:
        records = None
    failing = (
        None
        if records is None
        else sorted(p for p, r in records.items() if isinstance(r, dict) and r.get("failing"))
    )
    return {
        "checked": True,
        "passed": bool(result.get("passed")),
        "reason": result.get("reason") or "",
        "processes": result.get("processes", []),
        # None: this release cannot say (from before #832, or unreadable)
        "failing_jobs": failing,
    }


def status(cfg: Config, remote: Transport, *, health: bool = True) -> dict:
    """What cfg.host:cfg.dest runs. Takes no lock and changes nothing."""
    dep = Deployment(cfg, remote)
    dep.check_sudo()
    out = dep.run(
        "status",
        "reading the destination",
        _FACTS.format(current=q(dep.current), releases=q(dep.releases), lock=q(dep.lock)),
    )
    facts = [line.split("=", 1) for line in out.splitlines() if "=" in line]
    one = {k: v for k, v in facts if k != "release"}
    kept = sorted(v for k, v in facts if k == "release")
    current = one.get("current")
    report = _json_file(dep, current, REPORT) if current in kept else None
    previous = (report or {}).get("previous_release")
    named = {current, previous} - {None}
    result = {
        "format": FORMAT,
        "host": cfg.host,
        "dest": cfg.dest,
        "current": _release(dep, current, kept),
        "previous": _release(dep, previous, kept),
        "releases": {"kept": len(kept), "others": len(set(kept) - named), "names": kept},
        "last_deployment": _last(dep, kept[-1]) if kept else None,
        "health": (
            {"checked": False, "reason": "--no-health"}
            if not health
            else _health(dep, current, report)
            if current
            else {"checked": False, "reason": "no current release"}
        ),
        "lock": {"held": one.get("lock") == "held", "owner": one.get("lock_owner") or None},
    }
    return result


def render(s: dict) -> str:
    """The status for a terminal: one fact per line, worst news first."""
    lines = [f"{s['host']}:{s['dest']}"]
    if s["lock"]["held"]:
        lines.append(f"  LOCK      held: {s['lock']['owner'] or 'owner unknown'}")
    cur = s["current"]
    if cur is None:
        lines.append("  current   none")
    else:
        lines.append(f"  current   {_describe(cur)}")
    if s["previous"]:
        lines.append(f"  previous  {_describe(s['previous'])}")
    r = s["releases"]
    lines.append(f"  releases  {r['kept']} kept, {r['others']} besides current and previous")
    last = s["last_deployment"]
    if last:
        if not last["report"]:
            lines.append(f"  last      {last['release']}: no report (failed before writing one)")
        else:
            where = f" at {last['stage']}" if last.get("stage") else ""
            lines.append(f"  last      {last['release']}: {last['status']}{where}")
            if last.get("status") != "deployed":
                if last.get("error"):
                    lines.append(f"            error: {last['error']}")
                lines.append(f"            rollback: {last.get('rollback')}")
    h = s["health"]
    if not h["checked"]:
        lines.append(f"  health    not checked: {h['reason']}")
    else:
        verdict = "ok" if h["passed"] else f"FAILING: {h['reason'] or 'verification failed'}"
        lines.append(f"  health    {verdict}")
        down = [p.get("process") for p in h["processes"] if not p.get("ok")]
        if down:
            lines.append(f"            not answering: {', '.join(map(str, down))}")
        if h["failing_jobs"]:
            lines.append(f"            batches failing: {', '.join(h['failing_jobs'])}")
        elif h["failing_jobs"] is None:
            lines.append("            streaming jobs: this release cannot report them")
    return "\n".join(lines)


def _describe(rel: dict) -> str:
    if not rel.get("kept"):
        return f"{rel['release']} (no longer on the server)"
    bits = [rel["release"]]
    if rel.get("revision"):
        bits.append(f"rev {rel['revision'][:12]}" + (" (dirty)" if rel.get("dirty") else ""))
    if rel.get("runtime"):
        bits.append(f"runtime {rel['runtime']}")
    if rel.get("q"):
        bits.append(f"for kdb+ {rel['q']}")
    if rel.get("bundles"):
        bits.append("bundles " + ", ".join(sorted(rel["bundles"])))
    return ", ".join(bits)
