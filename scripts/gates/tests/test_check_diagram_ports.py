"""Tests for `scripts/gates/check_diagram_ports.py` (#1369).

The gate must fire on a perturbed port and on a procname that is not a
process, pass on the real diagrams, and refuse to pass over zero labels.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "check_diagram_ports", REPO / "scripts" / "gates" / "check_diagram_ports.py"
)
assert _SPEC and _SPEC.loader
cdp = importlib.util.module_from_spec(_SPEC)
sys.modules["check_diagram_ports"] = cdp
_SPEC.loader.exec_module(cdp)

PORTS = {"fxfeed1": 6069, "wdb1": 6055, "stp1": 6050}


def _diagrams(tmp_path: Path, body: str) -> Path:
    (tmp_path / "t.d2").write_text(body)
    return tmp_path


def test_the_real_diagrams_match_the_stack():
    assert cdp.problems(cdp.DIAGRAMS, cdp.authoritative_ports()) == []


def test_authority_covers_uqf_and_vendored_processes():
    ports = cdp.authoritative_ports()
    assert ports["fxfeed1"] == 6069  # process_ports.csv
    assert ports["stp1"] == 6050  # the vendored process.csv


def test_a_perturbed_port_fails_naming_both(tmp_path):
    found = cdp.problems(_diagrams(tmp_path, 'a: "fxfeed1 :6070\\nx"\n'), PORTS)
    assert len(found) == 1
    assert "fxfeed1 :6070" in found[0] and "6069" in found[0]


def test_an_unknown_procname_fails(tmp_path):
    found = cdp.problems(_diagrams(tmp_path, 'a: "ghost1 :6069"\n'), PORTS)
    assert len(found) == 1 and "does not exist" in found[0]


def test_a_label_after_a_line_break_is_still_read(tmp_path):
    body = 'a: "x\\nwdb1 :6056 → y"\n'
    found = cdp.problems(_diagrams(tmp_path, body), PORTS)
    assert len(found) == 1 and "wdb1 :6056" in found[0]


def test_comments_are_not_labels(tmp_path):
    body = '# reachable on :6075 ; ghost1 :1234\na: "fxfeed1 :6069"\nb: "no port here"\n'
    assert cdp.problems(_diagrams(tmp_path, body), PORTS) == []


def test_no_labels_at_all_is_an_error(tmp_path):
    found = cdp.problems(_diagrams(tmp_path, 'a: "nothing"\n'), PORTS)
    assert found and "no `procname :port` label" in found[0]


def test_no_sources_is_an_error(tmp_path):
    assert "no .d2 sources" in cdp.problems(tmp_path, PORTS)[0]
