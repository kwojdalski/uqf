"""Kafka in a release (#1515): opt-in, shared by every job that needs it.

Two consumers read Kafka, and a release carries neither unless asked:

  native   `uqs deploy build --kafka-home DIR` packages KX's kfk (kfk.q,
           libkfk.so), the shared libraries libkfk.so resolves to on the
           builder - librdkafka and the rest, never the C library (GLIBC) -
           and the redistribution notices found in DIR. They go under
           KAFKA_DIR, and the release's deploy.env points UQF_KFK_HOME at
           that copy and puts its lib/ first on LD_LIBRARY_PATH, so every
           process the release starts - every job that loads kfk through
           .qpipe.job.kafka_feed.load_library - finds the same library. No
           job names it: it is one setting for the release.
  python   `--with-kafka-connector` exports the lock with the uqs `kafka`
           extra, so confluent-kafka's locked wheel for the target is among
           the release's wheels (uqs.deploy.payload); `uqs connector start
           kafka` refuses without it (uqs.stack.kafka_extra).

The decoders (kafka_flow1, crypto_book_decoder1, a sidecar's) read plant rows
and need neither. Brokers, groups and credentials stay runtime configuration
(UQF_KAFKA_*): nothing here reads or records one.

CHECKED TWICE, never against a broker. At build, every packaged library must
be an ELF object for the target's architecture (read from its header here, not
by running it). At deploy, before any process starts, load_check_lines loads
kfk.q in the server's own q from the release's copy: the library and every
dependency resolve there, or the deployment stops naming what did not.
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from uqs.deploy.artifact import RELEASE_DIR, ReleaseError
from uqs.deploy.payload import Runner
from uqs.deploy.remote import q
from uqs.logger import get_logger
from uqs.stack.kafka_extra import EXTRA

log = get_logger(__name__)

#: Where the native client goes in a release; UQF_KFK_HOME on the server.
KAFKA_DIR = f"{RELEASE_DIR}/kafka"
#: The resolved shared libraries, LD_LIBRARY_PATH's first entry on the server.
LIB_SUBDIR = "lib"
NOTICE_SUBDIR = "notices"
#: What --kafka-home must hold: the q half and the C half of KX's kfk v1.6.6.
REQUIRED = ("kfk.q", "libkfk.so")
#: `uqs deploy build`'s two options (uqs.cli.deploy).
HOME_HELP = (
    "Package KX's kfk from DIR - kfk.q, libkfk.so, the libraries it loads and the "
    "LICENSE/NOTICE files there - for every job using the native Kafka client"
)
CONNECTOR_HELP = (
    f"Include the locked confluent-kafka wheels (the uqs `{EXTRA}` extra) for "
    "`uqs connector start kafka`"
)

#: Printed by the load check when kfk.q loaded.
LOAD_MARKER = "KAFKA_LOAD_OK"

#: ELF e_machine, by the architecture names artifact.PLATFORMS uses.
ELF_MACHINE = {"x86_64": 62, "aarch64": 183}
_MACHINE_NAME = {v: k for k, v in ELF_MACHINE.items()} | {3: "i386", 40: "arm", 243: "riscv"}

#: The C library's own objects, which every Linux server has and a release must
#: never carry: a second libc beside the system's loader breaks the process.
GLIBC = frozenset(
    {
        "linux-vdso", "linux-gate", "ld-linux", "ld-linux-x86-64", "ld-linux-aarch64",
        "libc", "libm", "libdl", "libpthread", "librt", "libresolv", "libutil", "libnsl",
        "libanl", "libmvec", "libBrokenLocale", "libthread_db", "libc_malloc_debug",
    }
)  # fmt: skip
#: macOS's system libraries, by install-name prefix: never redistributed.
MACOS_SYSTEM = ("/usr/lib/", "/System/Library/")

#: The builder's platform, which picks the dependency tool; a test sets it.
BUILDER_PLATFORM = sys.platform

_NOTICE = re.compile(r"^(licen[cs]e|notice|copying)", re.IGNORECASE)
_LDD_LINE = re.compile(r"^\s*(\S+)\s+=>\s+(not found|\S+)")


def elf_arch(path: Path) -> str:
    """The architecture of the ELF object at `path`, by artifact.PLATFORMS'
    names; a ReleaseError when it is not a 64-bit ELF object."""
    with path.open("rb") as f:
        head = f.read(20)
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        raise ReleaseError(
            "kafka", f"{path.name} is a macOS (Mach-O) library - a release runs on Linux"
        )
    if len(head) < 20 or head[:4] != b"\x7fELF":
        raise ReleaseError("kafka", f"{path.name} is not an ELF shared library")
    if head[4] != 2:
        raise ReleaseError("kafka", f"{path.name} is a 32-bit ELF object")
    order = "<" if head[5] == 1 else ">"
    (machine,) = struct.unpack(f"{order}H", head[18:20])
    return _MACHINE_NAME.get(machine, f"machine {machine}")


def check_arch(path: Path, arch: str) -> None:
    got = elf_arch(path)
    if got != arch:
        raise ReleaseError(
            "kafka",
            f"{path.name} is built for {got}, the release targets linux/{arch} - "
            f"build kfk on (or for) {arch}, or pass --arch {got}",
        )


def _is_glibc(name: str) -> bool:
    return Path(name).name.split(".so")[0] in GLIBC


def dependencies(
    lib: Path, runner: Runner = subprocess.run, platform: str | None = None
) -> dict[str, Path]:
    """{name: file} of the shared libraries `lib` loads, the C library's and
    the operating system's left out: from `ldd` on Linux (which resolves
    them transitively, as the server's loader will), `otool -L` on macOS. A
    library the tool cannot find is refused - the server would not find it
    either."""
    platform = platform or BUILDER_PLATFORM
    tool = ["otool", "-L"] if platform == "darwin" else ["ldd"]
    try:
        r = runner([*tool, str(lib)], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ReleaseError("kafka", f"could not run {tool[0]}: {exc}") from None
    if r.returncode:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise ReleaseError(
            "kafka",
            f"{tool[0]} could not list {lib.name}'s libraries - resolve them on a Linux "
            f"builder of the target's architecture: {' '.join(tail)}",
        )
    found: dict[str, Path] = {}
    missing = []
    lines = (r.stdout or "").splitlines()
    if platform == "darwin":
        for line in lines[1:]:  # the first names the library itself
            path = line.strip().split(" (")[0]
            if path and not path.startswith(MACOS_SYSTEM) and Path(path).name != lib.name:
                found[Path(path).name] = Path(path)
        return found
    for line in lines:
        m = _LDD_LINE.match(line)
        if not m or _is_glibc(m.group(1)):
            continue
        if m.group(2) == "not found":
            missing.append(m.group(1))
        else:
            found[m.group(1)] = Path(m.group(2))
    if missing:
        raise ReleaseError(
            "kafka",
            f"{lib.name} needs {', '.join(missing)}, which {tool[0]} does not find - put them "
            "beside it, or on LD_LIBRARY_PATH, before building",
        )
    return found


def notices(home: Path) -> list[Path]:
    """The licence and notice files in `home` and its subdirectories."""
    return sorted(p for p in home.rglob("*") if p.is_file() and _NOTICE.match(p.name))


def package(
    home: Path,
    arch: str,
    into: Path,
    runner: Runner = subprocess.run,
    platform: str | None = None,
) -> dict:
    """Copy the native client from `home` into `into` (KAFKA_DIR's layout)
    and return the manifest's `kafka` record. Refuses, before copying
    anything, a missing file, a library for another architecture, an
    unresolved dependency or a DIR with no redistribution notice."""
    home = home.expanduser().resolve()
    if not home.is_dir():
        raise ReleaseError("kafka", f"--kafka-home {home} is not a directory")
    missing = [f for f in REQUIRED if not (home / f).is_file()]
    if missing:
        raise ReleaseError(
            "kafka",
            f"--kafka-home {home} lacks {', '.join(missing)} - it must hold kfk.q and libkfk.so "
            "built from KxSystems/kafka v1.6.6 (docs/services/kafka.md)",
        )
    so = home / "libkfk.so"
    check_arch(so, arch)
    libs = dependencies(so, runner, platform)
    for name, path in libs.items():
        if not path.is_file():
            raise ReleaseError("kafka", f"{so.name} resolves {name} to {path}, which is not a file")
        check_arch(path, arch)
    found = notices(home)
    if not found:
        raise ReleaseError(
            "kafka",
            f"--kafka-home {home} holds no LICENSE, NOTICE or COPYING file - kfk and "
            "librdkafka are redistributed with their licences: copy kfk's LICENSE and "
            "librdkafka's LICENSE* files into it",
        )
    into.mkdir(parents=True, exist_ok=True)
    for f in REQUIRED:
        shutil.copyfile(home / f, into / f)
    (into / LIB_SUBDIR).mkdir(exist_ok=True)
    for name, path in libs.items():
        shutil.copyfile(path, into / LIB_SUBDIR / name)  # the soname, the target's content
    for n in found:
        dest = into / NOTICE_SUBDIR / n.relative_to(home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(n, dest)
    return {
        "home": KAFKA_DIR,
        "arch": arch,
        "libraries": sorted(libs),
        "notices": [f"{NOTICE_SUBDIR}/{n.relative_to(home).as_posix()}" for n in found],
    }


@dataclass
class Selected:
    """What a build was asked to carry of Kafka: the uqs extras its
    requirements export, and the native client `package` staged."""

    extras: tuple[str, ...] = ()
    staged: Path | None = None
    record: dict | None = None

    def members(self) -> dict[str, Path]:
        """{archive name: file} for everything staged under KAFKA_DIR."""
        if self.staged is None:
            return {}
        return {
            f"{KAFKA_DIR}/{p.relative_to(self.staged).as_posix()}": p
            for p in sorted(self.staged.rglob("*"))
            if p.is_file()
        }

    def annotate(self, manifest: dict) -> None:
        """Record the selection in a release's manifest; a default build's
        manifest is left exactly as it was."""
        if self.extras:
            manifest["python"]["extras"] = list(self.extras)
        if self.record:
            manifest["kafka"] = self.record


def select(
    home: Path | str | None,
    connector: bool,
    arch: str,
    into: Path,
    runner: Runner = subprocess.run,
) -> Selected:
    """`--kafka-home` packaged into `into` (refused before anything else is
    built), and `--with-kafka-connector` as the `kafka` extra."""
    sel = Selected(extras=(EXTRA,) if connector else ())
    if home is not None:
        sel.staged, sel.record = into, package(Path(home), arch, into, runner)
        log.info("native Kafka client from {}: libkfk.so, {}", home,
                 ", ".join(sel.record["libraries"]) or "no other library")  # fmt: skip
    return sel


# ------------------------------------------------------------- on the server


def env_lines(release: str) -> list[str]:
    """deploy.env's lines for a release that carries the native client: its
    own copy, ahead of anything the server's environment holds. Written
    literally into the file and expanded when it is sourced."""
    home = f"{release}/{KAFKA_DIR}"
    rest = '"${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
    return [
        f"export UQF_KFK_HOME={q(home)}",
        f"export LD_LIBRARY_PATH={q(f'{home}/{LIB_SUBDIR}')}{rest}",
    ]


def plan_note(release: str, manifest: dict) -> str:
    """What --dry-run's prepare line adds for a release carrying kfk."""
    if not manifest.get("kafka"):
        return ""
    return (
        f"; UQF_KFK_HOME={release}/{KAFKA_DIR} with its lib/ first on LD_LIBRARY_PATH, "
        "then kfk.q loaded once in $QCMD (no broker) before anything starts"
    )


#: Loads kfk.q exactly as load_library does (from UQF_KFK_HOME, which kfk.q's
#: relative `2:` needs as the working directory) and prints librdkafka's
#: version. kfk.q's load opens no connection: it binds the C functions and
#: calls kfk's Init, which only sets up local state.
LOAD_CHECK_Q = (
    '@[{system "l kfk.q"; -1 "' + LOAD_MARKER + ' librdkafka ",-3!.kfk.Version; exit 0};'
    '::;{-2 "kfk.q did not load: ",x; exit 1}]'
)


def load_check_lines(timeout: int) -> list[str]:
    """Shell lines, run after `source ./deploy.env`, that load kfk in the
    release's own q ($QCMD), with no broker: exit 0 with LOAD_MARKER printed,
    or 1 with q's reason on stderr."""
    return [
        'test -n "${UQF_KFK_HOME:-}" ||',
        '  { echo "UQF_KFK_HOME is not set by deploy.env" >&2; exit 1; }',
        "probe=$(mktemp -d)",
        f"printf '%s\\n' {q(LOAD_CHECK_Q)} > \"$probe/kfk_check.q\"",
        f'out=$(cd "$UQF_KFK_HOME" && timeout {timeout} "$QCMD" "$probe/kfk_check.q" -q '
        "</dev/null 2>&1) || true",
        'rm -rf "$probe"',
        'case "$out" in',
        f"  *{LOAD_MARKER}*) printf '%s\\n' \"$out\" | grep {LOAD_MARKER};;",
        '  *) echo "kfk did not load from $UQF_KFK_HOME with $QCMD: '
        '$(printf \'%s\\n\' "$out" | tail -5)" >&2; exit 1;;',
        "esac",
    ]
