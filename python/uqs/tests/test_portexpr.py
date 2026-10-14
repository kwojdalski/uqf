"""One grammar for a process.csv port cell, read the same by every consumer (#1367).

uqs.portexpr is the only reader. These tests feed one set of cells to the
orchestrator's resolver, the occupancy check and the frontend's, and hold the
module to being a leaf so the frontend's fleet-health poll stays light.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from uqs import portexpr
from uqs.stack import occupancy
from uqs.stack.procs import _resolve_value

CELLS = [
    ("7000", 7000),
    ("{KDBBASEPORT}", 6050),
    ("{KDBBASEPORT}+3", 6053),
    ("{KDBBASEPORT}-2", 6048),
    ("{OTHER}+1", None),
    ("{KDBBASEPORT}+1+2", None),
    ("{KDBBASEPORT}+x", None),
    ("", None),
    ("garbage", None),
]


@pytest.mark.parametrize(("raw", "expected"), CELLS)
def test_the_shared_resolver(raw, expected):
    assert portexpr.resolve(raw, {"KDBBASEPORT": "6050"}) == expected


def test_another_variable_resolves_from_the_environment_it_is_given():
    assert portexpr.resolve("{HDBPORT}-1", {"HDBPORT": "7001"}) == 7000


@pytest.mark.parametrize(("raw", "expected"), CELLS)
def test_the_orchestrators_resolver_agrees(raw, expected):
    want = raw if expected is None else str(expected)
    assert _resolve_value(raw, {"KDBBASEPORT": "6050"}) == want


@pytest.mark.parametrize(("raw", "expected"), CELLS)
def test_the_occupancy_check_agrees(raw, expected, tmp_path):
    procfile = tmp_path / "process.csv"
    procfile.write_text(f"procname,port\nrdb1,{raw}\n")
    assert occupancy._port_in(procfile, "rdb1", 6050) == expected


@pytest.mark.parametrize(("raw", "expected"), CELLS)
def test_the_frontends_resolver_agrees(raw, expected):
    from uqf_frontend.procfile import resolve_port

    assert resolve_port(raw, 6050) == expected


def test_importing_portexpr_loads_no_other_uqs_module():
    probe = (
        "import sys, uqs.portexpr; "
        "print(sorted(m for m in sys.modules if m.startswith('uqs.') and m != 'uqs.portexpr'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"
