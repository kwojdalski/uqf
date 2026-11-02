"""The remote-stack tests' machines, started once for the session and removed after.

Opt-in: `uv run pytest tests/remote -m docker` (conftest.py's OPT_IN at the
root); it was scripts/test.py's remote-stack lane until #1808. Selected, they
need a running Docker and fail without one; not selected, they skip and start
nothing. The image is built from this checkout (remote_world.build_image)
unless UQF_REMOTE_IMAGE names one, and every container and network this run
made is removed by its label afterwards, whatever happened.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from remote_world import PREBUILT, World, build, build_image, docker_problem, teardown


@pytest.fixture(scope="session")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    problem = docker_problem()
    if problem:
        pytest.fail(f"the remote-stack tests need Docker: {problem}", pytrace=False)
    if not PREBUILT:
        build_image(tmp_path_factory.mktemp("remote-image"))
    built: World | None = None
    try:
        built = build()
        yield built
    finally:
        teardown(built)
