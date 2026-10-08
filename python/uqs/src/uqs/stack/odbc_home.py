"""`uqs odbc`: a deployment-owned, versioned ODBC setup (#840).

q reaches an ODBC database through KX's client (odbc.k and a native odbc.so),
a driver manager (unixODBC) and the database's own driver. On a server none of
that may be installed system-wide by the deployment: no sudo, nothing in /etc,
and the managed QHOME is not ours to change. So everything lives in one
directory the deployment owns - the ODBC HOME - and a process opts in by
sourcing that directory's env.sh, which sets:

  QHOME          an OVERLAY of the managed QHOME: a directory of links to
                 every entry of it, with the package's odbc.k and odbc.so in
                 place. The managed QHOME itself is never written to.
  ODBCSYSINI     the version's own etc/, holding odbcinst.ini, which
  ODBCINSTINI    registers each driver by name -> its library's absolute path.
  LD_LIBRARY_PATH  the version's lib/ first, so the driver manager and driver
                 found are the package's.

THE PACKAGE is a .tar.gz the operator obtains - KX's client and most drivers
may not be redistributed, so this repository ships none of them:

  package.json  {"name", "version", "platform": "linux-x86_64",
                 "files": {path: sha256, ...},
                 "drivers": {"Driver Name": "lib/libdriver.so", ...}}
  q/...         overlaid onto the managed QHOME (odbc.k, l64/odbc.so)
  lib/...       native libraries: the driver manager and the drivers
  certs/...     optional: CA certificates a driver's TLS settings may name

THE HOME:

  approved_packages.csv   name,version,platform,sha256 - the deployment's own
                          list of packages it has approved. An archive whose
                          sha256 is not in it is refused before it is opened.
  versions/<name>-<version>/   one installed package, checked file by file
                          against its manifest, with etc/, qhome/ and env.sh
  current -> versions/...      what env.sh users load; moved in one rename
  previous -> versions/...     what `uqs odbc rollback` returns to

An upgrade installs the new version beside the old and moves `current`; a
rollback swaps `current` and `previous`. Nothing is deleted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from uqs.paths import UqsError

APPROVED = "approved_packages.csv"
MANIFEST = "package.json"
ENV_FILE = "env.sh"

#: What `platform` calls this machine, in a package's spelling.
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


def host_platform() -> str:
    """This machine as a package names its target: linux-x86_64, darwin-aarch64."""
    machine = platform.machine().lower()
    return f"{platform.system().lower()}-{_ARCH.get(machine, machine)}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def approved(home: Path) -> dict[str, dict[str, str]]:
    """The approved packages, by archive sha256."""
    path = home / APPROVED
    if not path.is_file():
        raise UqsError(
            f"no {APPROVED} in {home} - list each package the deployment approves "
            "as name,version,platform,sha256 before installing one"
        )
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    need = {"name", "version", "platform", "sha256"}
    if rows and not need <= set(rows[0]):
        raise UqsError(f"{path} must have the columns {', '.join(sorted(need))}")
    return {r["sha256"].strip().lower(): r for r in rows}


def _extract(archive: Path, into: Path) -> dict:
    """Unpack `archive` into `into`, refusing any member that is not a plain
    file or directory at a relative path; return its manifest."""
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise UqsError(f"{archive.name}: member {member.name!r} is not a relative path")
            if not (member.isfile() or member.isdir()):
                raise UqsError(f"{archive.name}: member {member.name!r} is not a plain file")
        tar.extractall(into, filter="data")
    try:
        return json.loads((into / MANIFEST).read_text())
    except OSError, ValueError:
        raise UqsError(f"{archive.name} holds no readable {MANIFEST}") from None


def _verify(root: Path, manifest: dict) -> None:
    """Every file the manifest lists, with its hash; nothing it does not list."""
    listed = manifest.get("files") or {}
    found = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST
    }
    for name in sorted(set(listed) | found):
        if name not in found:
            raise UqsError(f"the package's manifest lists {name}, which it does not hold")
        if name not in listed:
            raise UqsError(f"the package holds {name}, which its manifest does not list")
        if _sha256(root / name) != str(listed[name]).lower():
            raise UqsError(f"{name} does not match its manifest checksum")
    for driver, lib in (manifest.get("drivers") or {}).items():
        if lib not in listed:
            raise UqsError(f"driver {driver!r} names {lib}, which the package does not hold")


def _overlay(dst: Path, managed: Path, package_q: Path) -> None:
    """`dst` as the managed QHOME with the package's q/ files in place: links
    to everything, real directories only where the package adds a file."""
    dst.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in managed.iterdir()} if managed.is_dir() else set()
    names |= {p.name for p in package_q.iterdir()} if package_q.is_dir() else set()
    for name in sorted(names):
        ours, theirs = package_q / name, managed / name
        if ours.is_dir() and (theirs.is_dir() or not theirs.exists()):
            _overlay(dst / name, theirs, ours)
        elif ours.exists():
            # relative: the version directory is built under a staging name
            # and renamed into place, which an absolute link would not survive
            (dst / name).symlink_to(os.path.relpath(ours, dst))
        else:
            (dst / name).symlink_to(theirs)


def _write_config(into: Path, version: Path, manifest: dict) -> None:
    """etc/ and env.sh, written into `into` and naming `version`'s paths -
    where they will be once the staging directory is renamed into place."""
    etc = into / "etc"
    etc.mkdir(exist_ok=True)
    lines = []
    for driver, lib in sorted((manifest.get("drivers") or {}).items()):
        lines += [f"[{driver}]", f"Driver={version / lib}", ""]
    (etc / "odbcinst.ini").write_text("\n".join(lines))
    (etc / "odbc.ini").write_text("")
    exports = env_vars(version, inherit="")
    body = [f'export {k}="{v}"' for k, v in exports.items() if k != "LD_LIBRARY_PATH"]
    body.append(
        f'export LD_LIBRARY_PATH="{version / "lib"}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"'
    )
    (into / ENV_FILE).write_text(
        "# written by `uqs odbc install` - source it\n" + "\n".join(body) + "\n"
    )


def env_vars(version: Path, inherit: str | None = None) -> dict[str, str]:
    """The variables that load `version`'s setup; LD_LIBRARY_PATH keeps
    `inherit` (the caller's own, by default) after the package's lib/."""
    rest = os.environ.get("LD_LIBRARY_PATH", "") if inherit is None else inherit
    lib = str(version / "lib")
    return {
        "QHOME": str(version / "qhome"),
        "ODBCSYSINI": str(version / "etc"),
        "ODBCINSTINI": "odbcinst.ini",
        "LD_LIBRARY_PATH": f"{lib}:{rest}" if rest else lib,
    }


def _point(home: Path, name: str, target: Path) -> None:
    """Move the `name` link to `target` in one rename."""
    tmp = home / f".{name}.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target.relative_to(home))
    os.replace(tmp, home / name)


def current(home: Path) -> Path | None:
    """The installed version `env.sh` users load, or None."""
    link = home / "current"
    return link.resolve() if link.is_symlink() else None


def install(home: Path, archive: Path, managed_qhome: Path) -> Path:
    """Install `archive` into `home` and make it current; return its directory.

    Refused, before anything is written, when the archive's sha256 is not in
    the home's approved list, or its manifest disagrees with that list, with
    this machine's platform, or with its own files.
    """
    if not archive.is_file():
        raise UqsError(f"no package at {archive}")
    if not managed_qhome.is_dir():
        raise UqsError(f"the managed QHOME {managed_qhome} is not a directory - pass --qhome")
    digest = _sha256(archive)
    row = approved(home).get(digest)
    if row is None:
        raise UqsError(
            f"{archive.name} (sha256 {digest}) is not in {home / APPROVED} - "
            "only an approved package is installed"
        )
    if row["platform"] != host_platform():
        raise UqsError(
            f"{archive.name} is approved for {row['platform']}; this machine is {host_platform()}"
        )
    versions = home / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    staged = versions / f".staging-{digest[:12]}"
    shutil.rmtree(staged, ignore_errors=True)
    try:
        manifest = _extract(archive, staged)
        claimed = {k: str(manifest.get(k, "")) for k in ("name", "version", "platform")}
        expected = {k: row[k] for k in ("name", "version", "platform")}
        if claimed != expected:
            raise UqsError(
                f"{archive.name}'s manifest says {claimed}; the approved list says {expected}"
            )
        _verify(staged, manifest)
        _overlay(staged / "qhome", managed_qhome, staged / "q")
        target = versions / f"{row['name']}-{row['version']}"
        if target.exists():
            raise UqsError(f"{target.name} is already installed - `uqs odbc status` shows it")
        _write_config(staged, target, manifest)
        os.replace(staged, target)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    was = current(home)
    if was is not None:
        _point(home, "previous", was)
    _point(home, "current", target)
    return target


def rollback(home: Path) -> Path:
    """Make `previous` current again, and current previous; return it."""
    prev, now = home / "previous", current(home)
    if not prev.is_symlink() or now is None:
        raise UqsError(f"{home} has no previous version to return to")
    back = prev.resolve()
    _point(home, "previous", now)
    _point(home, "current", back)
    return back


def status(home: Path) -> dict:
    """What is installed, and which version is current and previous."""
    versions = home / "versions"
    names = sorted(p.name for p in versions.iterdir() if p.is_dir() and not p.name.startswith("."))
    prev, now = home / "previous", current(home)
    return {
        "home": str(home),
        "current": now.name if now else None,
        "previous": prev.resolve().name if prev.is_symlink() else None,
        "versions": names if versions.is_dir() else [],
    }
