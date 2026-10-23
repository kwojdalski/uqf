"""A bundled TorQ runtime on the server: installed once, never rewritten
(#1557, step 5).

A runtime (deploy/torq_runtime.py) lives at <dest>/shared/runtimes/torq/<sha256>/,
named by the sha256 of the archive it came from, beside a marker file holding
that sha256 and the RUNTIME_MANIFEST.json it shipped with. A push with
`torq_mode = "bundled"`, under the deploy lock and before its release is
transferred:

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
$TORQHOME's. Each release keeps naming its own runtime, so a rollback
(deploy/rollback.py, which sources each release's own deploy.env) runs the
release it returns to on the TorQ it was deployed with; `uqs deploy prune`
keeps every runtime a remaining release's deploy.env names.
"""

from __future__ import annotations

from pathlib import Path

from uqs.deploy.artifact import ReleaseError
from uqs.deploy.config import REPLACE_PY, SHA256_PY, DeployError
from uqs.deploy.remote import q
from uqs.deploy.server import Server
from uqs.deploy.torq_runtime import RUNTIMES_DIR, Runtime, read_runtime
from uqs.logger import get_logger

log = get_logger(__name__)

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
extra, missing = sorted(set(found) - set(listed)), sorted(set(listed) - set(found))
if extra or missing:
    refuse(f"{len(extra)} file(s) not in its manifest, {len(missing)} missing")
changed = sorted(r for r in listed if found[r] != listed[r])
if changed:
    refuse(f"{changed[0]} and {len(changed) - 1} other file(s) differ from its manifest")
print("runtime=ok")
"""

#: What the plan and the report say of the runtime directory.
REUSE, INSTALL = "already on the server, reused", "not on the server yet, installed write-once"


def load(path: str | None) -> Runtime:
    """The bundled TorQ runtime a push was given, checked whole."""
    if not path:
        raise DeployError("arguments", "torq_mode is bundled, and needs torq_runtime")
    try:
        return read_runtime(Path(path))
    except ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None


def _check(dep: Server, rt: Runtime, stage: str) -> str:
    """REUSE or INSTALL for `rt`'s directory, read-only; refused when it holds
    anything but `rt`."""
    home = rt.directory(dep.cfg.dest)
    out = dep.run(
        stage,
        "reading the TorQ runtime's directory",
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
        log.info("TorQ runtime {} is already installed: reused", rt.sha256[:12])
        return "reused"
    c = dep.cfg
    home = rt.directory(c.dest)
    staging = f"{c.dest}/staging/{rid}"
    partial = f"{c.dest}/{RUNTIMES_DIR}/.{rt.sha256}.partial"
    dep.run(
        "runtime",
        "creating the runtime's directories",
        f"mkdir -p {q(staging)} {q(f'{c.dest}/{RUNTIMES_DIR}')}",
    )
    archive = dep.upload(rt.path, staging)
    out = dep.run(
        "runtime",
        "checking the TorQ runtime and installing it",
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
    log.info("TorQ runtime {} installed at {}", rt.sha256[:12], home)
    return "installed"
