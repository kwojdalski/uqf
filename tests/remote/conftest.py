"""The remote-stack lane's machines, started once for the session and removed after.

Opt-in: these tests run only under `python3 scripts/test.py remote-stack`, which
builds the image and sets UQF_REMOTE_STACK_TEST and UQF_REMOTE_IMAGE. They are
not under any of pyproject.toml's testpaths, so a plain `uv run pytest` never
collects them and never needs Docker.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from remote_world import ENABLED, IMAGE, World, build, teardown


def pytest_collection_modifyitems(config, items):
    if ENABLED and IMAGE:
        return
    skip = pytest.mark.skip(
        reason="starts Docker containers: run `python3 scripts/test.py remote-stack`"
    )
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def world() -> Iterator[World]:
    built: World | None = None
    try:
        built = build()
        yield built
    finally:
        teardown(built)
