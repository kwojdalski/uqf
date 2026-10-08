"""Real sources, checked live (#840) - skipped unless asked for.

    UQF_LIVE_SOURCE_TEST=deals_db,quotes_db \
    UQS_ODBC_HOME=/srv/uqf/odbc \
    uv run pytest python/uqs/tests/test_live_sources_integration.py

UQF_LIVE_SOURCE_TEST names the sources; each needs its credential set the way
a worker reads it (UQF_SOURCE_CRED_<SOURCE>, or a row in sources.csv). With
UQS_ODBC_HOME, the private ODBC setup there is loaded. Ordinary runs and CI
skip this: it needs drivers, credentials and the network, which the offline
suites must never depend on.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uqs.paths import default_paths
from uqs.stack import live_check, odbc_home

SOURCES = [s for s in os.environ.get("UQF_LIVE_SOURCE_TEST", "").split(",") if s]

pytestmark = pytest.mark.skipif(not SOURCES, reason="UQF_LIVE_SOURCE_TEST names no source")


def test_every_named_source_answers_live():
    env = None
    if home := os.environ.get("UQS_ODBC_HOME"):
        version = odbc_home.current(Path(home))
        assert version is not None, f"{home} has no current ODBC version"
        env = odbc_home.env_vars(version)
    results = live_check.check(default_paths(), SOURCES, timeout=300, odbc_env=env)
    failed = [r for r in results if r["status"] == "failed"]
    assert not failed, "\n".join(f"{r['source']} {r['stage']}: {r['diagnostic']}" for r in failed)
