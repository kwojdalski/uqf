"""The OpenAPI document, held byte for byte to openapi.json.

Written from the app as it was before its routes were split into routes/
and its models into models/ (#2168). That was
a pure move, so the document the split app serves is the one the single
file served - down to the order of its paths, which FastAPI lists in the
order routes are registered: create_app installs the route modules in the
order app.py declared the routes.

Kept after the split, as the check that a route moved between modules,
renamed or reordered is meant: a change to the API regenerates the file, and
the diff shows what the API's callers will see.

    uv run python python/uqf_frontend/tests/test_openapi_snapshot.py
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

SNAPSHOT = Path(__file__).with_name("openapi.json")


def _document() -> str:
    app = create_app(gateway=FakeGateway(), settings=Settings(max_rows=5000))
    return json.dumps(app.openapi(), indent=2) + "\n"


def test_the_openapi_document_is_the_snapshot():
    expected, actual = SNAPSHOT.read_text(), _document()
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            "openapi.json",
            "the app's document",
            n=2,
        )
    )
    assert actual == expected, (
        "the OpenAPI document changed - if it is meant to, regenerate openapi.json "
        f"(see this file's docstring):\n{diff[:6000]}"
    )


if __name__ == "__main__":
    SNAPSHOT.write_text(_document())
