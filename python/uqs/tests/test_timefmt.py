"""uqs.timefmt stays a leaf: importing it loads no other uqs module (#1326).

uqf_frontend's read path imports it on every timestamp filter. If it ever
imported a sibling, the frontend would load that module - and every
import-time failure in it - onto a read-only `/query`.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from uqs.timefmt import finer_than_a_microsecond


def test_importing_timefmt_loads_no_other_uqs_module():
    # A fresh interpreter: this process has already imported half of uqs.
    probe = (
        "import sys, uqs.timefmt; "
        "print(sorted(m for m in sys.modules if m.startswith('uqs.') and m != 'uqs.timefmt'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"


@pytest.mark.parametrize(
    ("text", "finer"),
    [
        ("2026-09-13T00:00:00.000000001Z", True),
        ("2026.09.13D00:00:00.000000001", True),
        ("2026-09-13 00:00:00,1234567", True),
        ("2026-09-13T00:00:00.123456000Z", False),
        ("2026-09-13T00:00:00.123456Z", False),
        ("2026-09-13T00:00:00Z", False),
    ],
)
def test_finer_than_a_microsecond(text, finer):
    assert finer_than_a_microsecond(text) is finer
