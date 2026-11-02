"""A CliRunner whose results read as plain text, on CI as on a laptop.

GitHub Actions sets GITHUB_ACTIONS, and Typer's rich help then forces a
terminal: help and usage errors come back in colour, with an escape code
inside an option name such as `--skip-eod-fence`. A test that asserts on CLI
text uses this runner and reads what a user's eyes read (the same pattern as
test_deploy_prune.py's regex).
"""

from __future__ import annotations

import re
from typing import Any

from typer.testing import CliRunner

_ANSI = re.compile(rb"\x1b\[[0-9;]*m")


class PlainRunner(CliRunner):
    """CliRunner with every ANSI style escape removed from its result."""

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        result = super().invoke(*args, **kwargs)
        for field in ("stdout_bytes", "stderr_bytes", "output_bytes"):
            setattr(result, field, _ANSI.sub(b"", getattr(result, field)))
        return result
