"""A bundled runtime on the server - TorQ's, q's or Python's - installed once,
never rewritten (#1557, steps 5 to 7).

A runtime (deploy/torq_runtime.py, deploy/q_runtime.py,
deploy/python_runtime.py) lives at
<dest>/shared/runtimes/<component>/<sha256>/, named by the sha256 of the
archive it came from, beside a marker file holding that sha256 and the
RUNTIME_MANIFEST.json it shipped with. A push with `torq_mode`, `q_mode` or
`python_mode` "bundled", under the deploy lock and before its release is transferred:

  - finds no such directory: uploads the archive, checks its sha256 ON THE
    SERVER, extracts it into a hidden .partial directory, writes the marker,
    checks every file against the manifest, makes the files read-only, and
    renames it into place - so a runtime directory is whole or absent;
  - finds one whose marker, manifest and files are exactly this runtime's:
    reuses it, writing nothing;
  - finds one that is anything else: refuses, and never overwrites it. A
    directory named by a sha256 that does not hold those bytes has been
    tampered with, and the processes of other releases may be running on it.

The release's deploy.env then names it: TORQHOME=<runtime>/torq and
TORQAPPHOME=<runtime>/torq-finance-starter-pack, and torq.sh is
$TORQHOME's; QCMD=<runtime>/q and QHOME=<runtime>; and the release's .venv
is made from a bundled Python's interpreter, which UQF_PYTHON_RUNTIME names.
Each release keeps naming
its own runtimes, so a rollback (deploy/rollback.py, which sources each
release's own deploy.env) runs the release it returns to on the TorQ and q
it was deployed with; `uqs deploy prune` keeps every runtime a remaining
release's deploy.env names.

A q runtime is run once installed (`probe`): it must answer that it is
PeachQ, and the .z.K it reports is the version the report records. A Python
runtime is run too (`probe_python`): it must start, load its own extension
modules and report the version it was built as.
"""

from __future__ import annotations

from pathlib import Path

from uqs.deploy import python_runtime, q_runtime, torq_runtime
from uqs.deploy.artifact import ReleaseError
from uqs.deploy.config import REPLACE_PY, SHA256_PY, DeployError
from uqs.deploy.remote import q
from uqs.deploy.server import Server
from uqs.interpreter import PEACHQ
from uqs.logger import get_logger

log = get_logger()

#: The file beside a runtime's trees that says which archive it came from.
MARKER = ".uqf-runtime"
#: Read-only: whether argv[1] is, file for file, the runtime whose archive is
#: argv[2] and whose shipped manifest is argv[3] (both sha256s). One line:
#: `runtime=ok`, or `runtime=refused <why>`.
VERIFY_PY = r"""
import hashlib, json, os, sys
d, want, want_manifest = sys.argv[1:4]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
def refuse(why):
    print("runtime=refused " + why)
    sys.exit(0)
try:
    with open(os.path.join(d, ".uqf-runtime")) as f:
        marker = f.read().strip()
except OSError:
    marker = ""
if marker != want:
    refuse("its marker names " + (marker or "no runtime"))
manifest = os.path.join(d, "RUNTIME_MANIFEST.json")
if not os.path.isfile(manifest) or sha(manifest) != want_manifest:
    refuse("its RUNTIME_MANIFEST.json is not the one this runtime shipped")
with open(manifest) as f:
    listed = json.load(f)["files"]
found = {}
for root, dirs, files in os.walk(d):
    for name in dirs + files:
        if os.path.islink(os.path.join(root, name)):
            refuse(os.path.relpath(os.path.join(root, name), d) + " is a link")
    for name in files:
        rel = os.path.relpath(os.path.join(root, name), d)
        if rel not in (".uqf-runtime", "RUNTIME_MANIFEST.json"):
            found[rel] = sha(os.path.join(root, name))
def cache_of_listed(rel):
    # A .pyc CPython wrote on first import, for a source the manifest lists:
    # it is checked against that (verified) source before use, so it is a
    # cache, not a change. A listed .pyc rewritten is still a change.
    head, tail = os.path.split(rel)
    parent, cache = os.path.split(head)
    if cache != "__pycache__" or not tail.endswith(".pyc"):
        return False
    return os.path.join(parent, tail.split(".")[0] + ".py") in listed
extra = sorted(r for r in set(found) - set(listed) if not cache_of_listed(r))
missing = sorted(set(listed) - set(found))
if extra or missing:
    refuse(f"{len(extra)} file(s) not in its manifest, {len(missing)} missing")
changed = sorted(r for r in listed if found[r] != listed[r])
if changed:
    refuse(f"{changed[0]} and {len(changed) - 1} other file(s) differ from its manifest")
print("runtime=ok")
"""

#: What the plan and the report say of the runtime directory.
REUSE, INSTALL = "already on the server, reused", "not on the server yet, installed write-once"


#: Each component's runtime reader.
READERS = {
    "torq": torq_runtime.read_runtime,
    "q": q_runtime.read_runtime,
    "python": python_runtime.read_runtime,
}
#: Any runtime: TorQ's, q's or Python's.
Runtime = torq_runtime.Runtime | q_runtime.QRuntime | python_runtime.PythonRuntime


def load(component: str, path: str | None) -> Runtime:
    """The bundled runtime of `component` a push was given, checked whole."""
    if not path:
        needs = f"{component}_mode is bundled, and needs {component}_runtime"
        raise DeployError("arguments", needs)
    try:
        return READERS[component](Path(path))
    except ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None


def _check(dep: Server, rt: Runtime, stage: str) -> str:
    """REUSE or INSTALL for `rt`'s directory, read-only; refused when it holds
    anything but `rt`."""
    home = rt.directory(dep.cfg.dest)
    out = dep.run(
        stage,
        f"reading the {rt.label} runtime's directory",
        f"if [ ! -e {q(home)} ]; then echo runtime=absent; else",
        f"  python3 -c {q(VERIFY_PY)} {q(home)} {q(rt.sha256)} {q(rt.manifest_sha256)}",
        "fi",
    )
    lines = [ln for ln in out.splitlines() if ln.startswith("runtime=")]
    verdict = lines[-1].removeprefix("runtime=") if lines else "refused it printed no verdict"
    if verdict == "absent":
        return INSTALL
    if verdict == "ok":
        return REUSE
    raise DeployError(
        stage,
        f"{home} is there and is not the runtime {rt.sha256}: "
        f"{verdict.removeprefix('refused ')} - an installed runtime is never overwritten; "
        "find what changed it, and remove it by hand once no release runs on it",
    )


def state(dep: Server, rt: Runtime) -> str:
    """What a push would do with the runtime: for preflight and the plan."""
    return _check(dep, rt, "preflight")


def install(dep, rt: Runtime, rid: str) -> str:
    """Install `rt` under the lock, or reuse it: "installed" or "reused".
    `dep` is a stages.Deployment, whose upload this uses."""
    if _check(dep, rt, "runtime") == REUSE:
        log.info("{} runtime {} is already installed: reused", rt.label, rt.sha256[:12])
        return "reused"
    c = dep.cfg
    home = rt.directory(c.dest)
    staging = f"{c.dest}/staging/{rid}"
    runtimes = f"{c.dest}/{rt.runtimes_dir}"
    partial = f"{runtimes}/.{rt.sha256}.partial"
    dep.run(
        "runtime",
        "creating the runtime's directories",
        f"mkdir -p {q(staging)} {q(runtimes)}",
    )
    archive = dep.upload(rt.path, staging)
    out = dep.run(
        "runtime",
        f"checking the {rt.label} runtime and installing it",
        f"got=$(python3 -c {q(SHA256_PY)} {q(archive)})",
        f'if [ "$got" != {q(rt.sha256)} ]; then',
        '  echo "checksum mismatch: the runtime arrived as $got" >&2',
        f"  rm -f {q(archive)}; exit 1",
        "fi",
        f"rm -rf {q(partial)}",
        f"mkdir {q(partial)}",
        f"tar -xzf {q(archive)} -C {q(partial)}",
        f"rm -f {q(archive)}",
        f"printf '%s\\n' {q(rt.sha256)} > {q(partial)}/{MARKER}",
        f"verdict=$(python3 -c {q(VERIFY_PY)} {q(partial)} {q(rt.sha256)} {q(rt.manifest_sha256)})",
        'if [ "$verdict" != runtime=ok ]; then',
        '  echo "the extracted runtime failed its check: $verdict" >&2',
        f"  rm -rf {q(partial)}; exit 1",
        "fi",
        f"find {q(partial)} -type f -exec chmod a-w {{}} +",
        f"if [ -e {q(home)} ]; then",
        f"  echo {q(f'{home} appeared during the install')} >&2; exit 1",
        "fi",
        f"python3 -c {q(REPLACE_PY)} {q(partial)} {q(home)}",
        'echo "$verdict"',
    )
    if "runtime=ok" not in out:
        raise DeployError("runtime", f"the runtime install printed no verdict: {out.strip()}")
    log.info("{} runtime {} installed at {}", rt.label, rt.sha256[:12], home)
    return "installed"


def probe(dep: Server, rt: q_runtime.QRuntime) -> str:
    """Run the installed q, as the release will: it must say it is PeachQ.
    Its .z.K, the version the report records."""
    home = rt.directory(dep.cfg.dest)
    out = dep.run(
        "runtime",
        "running the bundled q",
        "probe=$(mktemp -d)",
        f"printf '%s' {q(q_runtime.PROBE)} > \"$probe/probe.q\"",
        f"QHOME={q(home)} timeout {dep.cfg.smoke_timeout} {q(f'{home}/{q_runtime.BINARY}')} "
        '"$probe/probe.q" -q 2>&1 || true',
        'rm -rf "$probe"',
    )
    impl, version = q_runtime.parse_probe(out)
    if impl != PEACHQ:
        why = f"says it is {impl} - {q_runtime.KX_REFUSAL}" if impl else "did not run there"
        raise DeployError("runtime", f"the bundled q at {home} {why}: {out.strip()[-300:]}")
    return version


def probe_python(dep: Server, rt: python_runtime.PythonRuntime) -> str:
    """Run the installed interpreter, isolated from the account's environment:
    the X.Y.Z it reports, which must be the version it was built as."""
    interpreter = rt.interpreter(rt.directory(dep.cfg.dest))
    out = dep.run(
        "runtime",
        "running the bundled Python",
        f"timeout {dep.cfg.smoke_timeout} {q(interpreter)} -I -c {q(python_runtime.PROBE)} 2>&1 "
        "|| true",
    )
    version = python_runtime.parse_probe(out)
    if version != rt.version:
        said = f"says it is Python {version}" if version else "did not run there"
        raise DeployError(
            "runtime",
            f"the bundled Python {interpreter} {said}, not {rt.version}: {out.strip()[-300:]}",
        )
    return version
