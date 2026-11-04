"""A test may not write into the checkout's output/ (#2082).

`output/<runtime>/` is where a stack's bootstrap writes process.csv, setenv.sh
and the rest, and what the next `uqs` command, the frontend and the next test
run read. A test that ran a lifecycle action without a scratch stack root left
a setenv.sh there naming its own, long-deleted UQF_STATUS_DIR (#2038), and the
following run's test_authz failed on it until someone deleted the file.

The guard compares output/ before and after each test and fails the test when
anything there was created, removed or rewritten. It looks two levels deep:
output/'s entries and each of their entries - output/uqs/setenv.sh,
output/uqs/hdb. A file at either level is compared by mtime and size; a
directory below the first level only by whether it exists, because what is
inside one - hdb/, logs/, status/ - is what a stack the developer has running
changes on its own while the suite runs. A lifecycle command the developer
runs during a test run rewrites the generated files too, and will be reported:
nothing tells it apart from a test doing the same.

Loaded by the root conftest.py, for the tests under python/.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

#: relative path -> (is a directory, mtime_ns, size); a directory below the
#: first level carries no mtime or size (see the module docstring).
Snapshot = dict[str, tuple[bool, int, int]]


def snapshot(output: Path) -> Snapshot:
    """output/'s first two levels, as compared before and after a test."""
    seen: Snapshot = {}

    def entries(directory: Path) -> list[os.DirEntry[str]]:
        try:
            with os.scandir(directory) as it:
                return list(it)
        except FileNotFoundError, NotADirectoryError:
            return []

    def record(entry: os.DirEntry[str], rel: str, deep: bool) -> bool:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            st = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        seen[rel] = (True, 0, 0) if is_dir and deep else (is_dir, st.st_mtime_ns, st.st_size)
        return is_dir

    for top in entries(output):
        if record(top, top.name, deep=False):
            for inner in entries(Path(top.path)):
                record(inner, f"{top.name}/{inner.name}", deep=True)
    return seen


def changes(before: Snapshot, after: Snapshot) -> list[str]:
    """What differs between two snapshots, one line per path."""
    out = []
    for rel in sorted(before.keys() | after.keys()):
        if rel not in before:
            out.append(f"created {rel}")
        elif rel not in after:
            out.append(f"removed {rel}")
        elif before[rel] != after[rel]:
            out.append(f"changed {rel}")
    return out


def make_guard(output: Path, applies: Callable[[Path], bool]):
    """An autouse fixture failing each test *applies* accepts (by its file)
    that changed *output*. A conftest assigns it to a module-level name."""

    @pytest.fixture(autouse=True)
    def checkout_output_untouched(request) -> Iterator[None]:
        if not applies(Path(request.node.path)):
            yield
            return
        before = snapshot(output)
        yield
        changed = changes(before, snapshot(output))
        if changed:
            pytest.fail(
                f"{output} changed while this test ran - a test must give the stack it runs "
                "a scratch root (tmp_path), never the checkout's (#2082). Under pytest-xdist "
                "it may be a test running beside it on another worker; a lifecycle command "
                "run by hand during the run trips this too:\n  " + "\n  ".join(changed),
                pytrace=False,
            )

    return checkout_output_untouched
