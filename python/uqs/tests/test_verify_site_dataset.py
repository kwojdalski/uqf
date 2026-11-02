"""The controlled verification of a real site's dataset (#1851): opt-in.

Piggybank's streaming and backfill producers are a site bundle, outside this tree,
so the in-tree end-to-end case is demo_deals (test_dataset_verify.py). This runs the
same verifier against the site's own dataset, from the site's environment. Nothing
here runs by default: it needs `-m external` AND the dataset named.

    UQF_SITE_VERIFY_DATASET=<dataset> \\
    UQF_SITE_VERIFY_FROM=2026-09-11 UQF_SITE_VERIFY_TO=2026-09-12 \\
    UQF_SITE_VERIFY_SCOPE=account=<id>[,col=value] \\
    UQF_SITE_VERIFY_GATEWAY=<host:port> \\
        uv run pytest -m external python/uqs/tests/test_verify_site_dataset.py

The scope and gateway are optional; without them completeness is UNVERIFIED, which
the check allows and reports. A FAIL anywhere, or a snapshot that changed, fails it.
"""

from __future__ import annotations

import os

import pytest

from uqs import timerange
from uqs.paths import default_paths
from uqs.stack import dataset_verify

pytestmark = pytest.mark.external


def test_the_sites_dataset_verifies_without_a_failure():
    dataset = os.environ.get("UQF_SITE_VERIFY_DATASET", "").strip()
    if not dataset:
        pytest.skip("UQF_SITE_VERIFY_DATASET names no dataset: the site check is opt-in")
    start, end = os.environ.get("UQF_SITE_VERIFY_FROM"), os.environ.get("UQF_SITE_VERIFY_TO")
    if not (start and end):
        pytest.fail("UQF_SITE_VERIFY_FROM and UQF_SITE_VERIFY_TO bound the closed window to verify")
    scope = dataset_verify.parse_scope(
        [s for s in os.environ.get("UQF_SITE_VERIFY_SCOPE", "").split(",") if s]
    )
    report = dataset_verify.verify(
        default_paths(),
        dataset,
        scope,
        timerange.resolve(start, end),
        gateway=os.environ.get("UQF_SITE_VERIFY_GATEWAY") or None,
    )
    assert report["readonly"], "verification changed a cursor, checkpoint, coverage record or table"
    failed = [
        (c["category"], c["reason"], c["findings"])
        for c in report["checks"]
        if c["status"] == "FAIL"
    ]
    assert not failed, failed
    for c in report["checks"]:
        print(c["category"], c["status"], c["reason"])
