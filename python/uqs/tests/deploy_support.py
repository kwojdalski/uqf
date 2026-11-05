"""`driver.deploy` as a test reads it: the report written to `out` as the
JSON `uqs deploy push --json` carries for one target. The driver hands its
report to the command rather than printing it (#2125); a dry run's plan
still goes to `out` alone."""

from __future__ import annotations

import json

from uqs.cli import json_out
from uqs.deploy import driver
from uqs.deploy.report import Report


def deploy(cfg, remote, *, out, on_report=None) -> int:
    def shown(report: Report) -> None:
        if report.status != "planned":
            out.write(json.dumps(json_out.plain(report.as_dict()), indent=2))

    return driver.deploy(cfg, remote, out=out, on_report=on_report, on_result=shown)
