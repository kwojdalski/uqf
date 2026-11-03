"""A copy of the repository to run `uqs ... --dry-run` against (#1880).

`--dry-run` promises that nothing is changed, and the promise is only worth
what a test can see. So the command runs in a SUBPROCESS whose `uqs` package
is the copy's own (PYTHONPATH puts it ahead of the editable install), whose
repository root is therefore the copy, whose data root (UQS_DATA_ROOT) is an
empty directory and whose HOME is another - and every file under all three is
compared before and after. A write anywhere the command can reach from its
paths shows up; one into the real checkout cannot happen, because the
real checkout is not where the command thinks it lives.

Bytecode is not written (PYTHONDONTWRITEBYTECODE), or the copy's own
__pycache__ directories would be the first "change" every run found.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

UQF_ROOT = Path(__file__).resolve().parents[3]

#: The workspace packages whose sources the subprocess takes from the copy.
_PACKAGES = ("uqs", "uqf_frontend", "uqf_airflow_provider")


def copy_repo(dest: Path) -> Path:
    """The tracked tree of this checkout, copied (never linked: a hard link
    would let a write through the copy change the checkout) to `dest`."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=UQF_ROOT,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for raw in listed:
        if not raw:
            continue
        rel = raw.decode()
        src = UQF_ROOT / rel
        if not src.is_file():
            continue  # deleted in the working tree, not yet staged
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target, follow_symlinks=False)
    return dest


Snapshot = dict[str, tuple[int, int] | None]


def snapshot(*roots: Path) -> Snapshot:
    """Every file and directory under `roots`: a file by (size, mtime_ns), a
    directory by None - so a created empty directory counts as a change too."""
    out: Snapshot = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames:
                out[os.path.join(dirpath, name)] = None
            for name in filenames:
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                out[path] = (st.st_size, st.st_mtime_ns)
    return out


def changes(before: Snapshot, after: Snapshot) -> list[str]:
    """What differs: each path created, removed or modified, labelled."""
    created = [f"created  {p}" for p in sorted(after.keys() - before.keys())]
    removed = [f"removed  {p}" for p in sorted(before.keys() - after.keys())]
    modified = [
        f"modified {p}" for p in sorted(before.keys() & after.keys()) if before[p] != after[p]
    ]
    return created + removed + modified


@dataclass(frozen=True)
class Sandbox:
    repo: Path
    data: Path
    home: Path

    @property
    def roots(self) -> tuple[Path, Path, Path]:
        return self.repo, self.data, self.home

    def env(self) -> dict[str, str]:
        drop = ("UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON", "UQS_RUNTIME", "UQS_TARGET")
        env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("TORQ")}
        sources = [str(self.repo / "python" / p / "src") for p in _PACKAGES]
        env.update(
            PYTHONPATH=os.pathsep.join(sources),
            PYTHONDONTWRITEBYTECODE="1",
            UQS_DATA_ROOT=str(self.data),
            HOME=str(self.home),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_STATE_HOME=str(self.home / ".local" / "state"),
            NO_COLOR="1",
            EDITOR="true",
            VISUAL="true",
            COLUMNS="200",
        )
        return env

    def uqs(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        """`uqs args...`, run from the copy, against the sandbox."""
        return subprocess.run(
            [sys.executable, "-m", "uqs", *args],
            cwd=self.repo,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
