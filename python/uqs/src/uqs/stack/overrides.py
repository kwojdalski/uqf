"""Bundle-owned and host-owned process.csv override files."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from uqs.paths import PACKAGE_DIR, UqsPaths
from uqs.stack.bundle_blocks import BundleError

HOST_FILE = PACKAGE_DIR / "process_overrides.csv"
BUNDLE_FILE = PACKAGE_DIR / "bundle_overrides.csv"


def read_file(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            overrides.setdefault(row["procname"], {})[row["field"]] = row["value"]
    return overrides


def effective(paths: UqsPaths) -> dict[str, dict[str, str]]:
    """Bundle defaults followed by this host's process overrides."""
    overrides = read_file(paths.repo_root / BUNDLE_FILE)
    for procname, fields in read_file(paths.overrides_path).items():
        overrides.setdefault(procname, {}).update(fields)
    return overrides


def _rows(path: Path) -> dict[tuple[str, str], str]:
    return {
        (proc, fld): value
        for proc, fields in read_file(path).items()
        for fld, value in fields.items()
    }


def _write_rows(path: Path, rows: dict[tuple[str, str], str]) -> None:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["procname", "field", "value"])
    writer.writerows([proc, fld, value] for (proc, fld), value in rows.items())
    path.write_text(out.getvalue())


def merge_bundle(
    root: Path,
    new_rows: list[tuple[str, str, str]],
    previous: list[list[str]],
    name: str,
) -> None:
    """Replace this bundle's rows, preserving other bundles and host choices."""
    path = root / BUNDLE_FILE
    host_path = root / HOST_FILE
    rows = _rows(path)
    for proc, fld, _value in previous:
        rows.pop((proc, fld), None)
    host_rows = _rows(host_path)
    # Earlier installs placed bundle rows in the host file. Move rows only
    # when they still match the ledger; changed values belong to the operator.
    migrated = False
    if not path.is_file():
        for proc, fld, value in previous:
            if host_rows.get((proc, fld)) == value:
                host_rows.pop((proc, fld))
                migrated = True
    for proc, fld, value in new_rows:
        if (proc, fld) in host_rows and host_rows[(proc, fld)] != value:
            raise BundleError(
                f"{host_path}: {proc} {fld} is already {host_rows[(proc, fld)]!r}, "
                f"bundle {name} sets {value!r}"
            )
        if rows.get((proc, fld), value) != value:
            raise BundleError(
                f"{BUNDLE_FILE}: {proc} {fld} is already {rows[(proc, fld)]!r}, "
                f"bundle {name} sets {value!r}"
            )
        rows[(proc, fld)] = value
    if not rows and not path.is_file() and not migrated:
        return
    _write_rows(path, rows)
    if migrated:
        _write_rows(host_path, host_rows)
