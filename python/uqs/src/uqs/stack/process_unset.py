"""`uqs config unset`: an override taken out of process_overrides.csv (#1858),
so process.csv's own value applies again - beside stack/procs.py's
set_process_config, which writes one."""

from __future__ import annotations

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import overrides as override_files
from uqs.stack.procs import _write_overrides

log = get_logger()


def unset_process_config(
    paths: UqsPaths, procname: str, fields: list[str], dry_run: bool = False
) -> None:
    """Remove *procname*'s overrides of `fields` from process_overrides.csv,
    so process.csv's own values apply again (`uqs config unset`, #1858).
    Refused, naming them, when a field has no override to remove."""
    overrides = override_files.read_file(paths.overrides_path)
    key = override_files.row_key(override_files.effective(paths), procname)
    have = overrides.get(key, {})
    missing = [f for f in fields if f not in have]
    if missing:
        set_now = ", ".join(sorted(have)) or "none"
        raise UqsError(
            f"{procname} has no override of {', '.join(missing)} - it overrides: {set_now}"
        )
    if dry_run:
        return
    for field in fields:
        del have[field]
    if not have:
        overrides.pop(key, None)
    _write_overrides(paths, overrides)
    log.info("unset {}.{}", procname, ",".join(fields))
