"""`uqs deploy push`'s policy options: the requirements a deployment holds an
artifact and a server to, and the named overrides that relax them (#1557,
deploy/overrides.py). Kept beside cli/deploy.py, which takes each as a
parameter, so the push command stays readable in one sitting."""

from __future__ import annotations

from typing import Annotated

import typer

AllowDirty = Annotated[
    bool,
    typer.Option(
        "--allow-dirty", help="Deploy an artifact built from uncommitted changes (refused)"
    ),
]
AcceptConvertedRelease = Annotated[
    str | None,
    typer.Option(
        "--accept-converted-release",
        metavar="REASON",
        help="Start a release converted for an older kdb+ (--q-target) on a q its "
        "version check cannot confirm, once its smoke test passes there. Its "
        "conversion record and integrity are still checked; recorded in the report",
    ),
]
ExpectedSha256 = Annotated[
    str | None,
    typer.Option(
        "--expected-sha256",
        metavar="SHA256",
        help="Refuse an artifact whose sha256 is anything else (usually a target's "
        "expected_sha256)",
    ),
]
QVersion = Annotated[
    str | None,
    typer.Option(
        "--q-version",
        metavar="REQUIREMENT",
        help="The server's q must be this version (4.1) or at least it (>=4.1), as "
        "preflight finds it; usually a target's q_version",
    ),
]
AcceptDigest = Annotated[
    str | None,
    typer.Option(
        "--accept-digest",
        metavar="REASON",
        help="Deploy an artifact whose sha256 is not expected_sha256. Its actual sha256 is "
        "recorded, and the server still checks the bytes it receives against it",
    ),
]
AcceptQVersion = Annotated[
    str | None,
    typer.Option(
        "--accept-q-version",
        metavar="REASON",
        help="Deploy on a q outside q_version; the version found is recorded",
    ),
]
AllowUnpinned = Annotated[
    str | None,
    typer.Option(
        "--allow-unpinned",
        metavar="REASON",
        help="Deploy a local artifact with no .sha256 beside it and no expected_sha256",
    ),
]
