"""CORE_INFRA and the plant-client list are hand-kept copies of the starter pack.

model/infra.py and model/pipeline_edges.py spell which vendored processes a
profile starts and which of them hold a tickerplant slot. They cannot be
derived at import time (the starter pack is found through UqsPaths, which
TORQAPPHOME can repoint, and a model module must not read the filesystem), so
they stay as copies - and these tests are the gate that fails the day the
authority moves (#1367). The authority is, per fact:

  CORE_INFRA / FULL_INFRA   lib/torq-finance-starter-pack/appconfig/process.csv's
                            startwithall=1 rows, with VENDORED_STARTWITHALL_OVERLAY
                            (stack/procs.py) applied
  VENDORED_PLANT_CLIENTS    which of those subscribe to the segmented tickerplant:
                            a `tickerplanttypes:`segmentedtickerplant` in the
                            process type's settings file, or a
                            `-parentproctype segmentedtickerplant` extra
"""

from __future__ import annotations

import csv
import re

import pytest

from uqs import paths as stack_paths
from uqs.model import infra, pipeline_edges
from uqs.stack.procs import VENDORED_STARTWITHALL_OVERLAY

_TICKERPLANT_TYPES = re.compile(r"^tickerplanttypes:\s*`segmentedtickerplant\b", re.MULTILINE)


@pytest.fixture(scope="module")
def vendored() -> list[dict[str, str]]:
    path = stack_paths.default_paths().torqapphome / "appconfig" / "process.csv"
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _started_by_default(rows: list[dict[str, str]], overlay: bool) -> set[str]:
    def flag(row: dict[str, str]) -> str:
        if overlay:
            return VENDORED_STARTWITHALL_OVERLAY.get(row["procname"], row["startwithall"])
        return row["startwithall"]

    return {row["procname"] for row in rows if flag(row) == "1"}


def test_core_infra_is_what_the_starter_pack_starts_by_default(vendored):
    assert set(infra.CORE_INFRA) == _started_by_default(vendored, overlay=True), (
        "model/infra.py CORE_INFRA no longer matches the starter pack's startwithall=1 "
        "rows plus VENDORED_STARTWITHALL_OVERLAY: `start all` and a profile start "
        "would bring up different infrastructure"
    )


def test_full_infra_is_the_starter_packs_own_default_fleet(vendored):
    assert set(infra.FULL_INFRA) == _started_by_default(vendored, overlay=False) | {
        proc for proc, flag in VENDORED_STARTWITHALL_OVERLAY.items() if flag == "1"
    }


def test_every_named_infra_process_exists_in_the_starter_pack(vendored):
    names = {row["procname"] for row in vendored}
    named = {p for procs in infra.PROFILE_INFRA.values() for p in procs} | set(infra.ONE_SHOT)
    assert named <= names, f"not in the starter pack's process.csv: {sorted(named - names)}"


def test_the_plant_clients_are_the_default_processes_that_subscribe(vendored):
    real = stack_paths.default_paths()
    settings_dirs = [
        real.torqhome / "config" / "settings",
        real.torqapphome / "appconfig" / "settings",
    ]

    def subscribes(row: dict[str, str]) -> bool:
        if "-parentproctype segmentedtickerplant" in row["extras"]:
            return True
        return any(
            _TICKERPLANT_TYPES.search(file.read_text())
            for directory in settings_dirs
            for file in [directory / f"{row['proctype']}.q"]
            if file.is_file()
        )

    by_name = {row["procname"]: row for row in vendored}
    derived = {p for p in infra.CORE_INFRA if subscribes(by_name[p])}
    assert pipeline_edges.VENDORED_PLANT_CLIENTS == derived, (
        "VENDORED_PLANT_CLIENTS (model/pipeline_edges.py) no longer matches the "
        "default processes whose settings subscribe to the tickerplant: the "
        "connection budget miscounts plant slots"
    )
