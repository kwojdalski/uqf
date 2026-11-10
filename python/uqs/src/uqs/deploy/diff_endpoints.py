"""`uqs data diff` and the frontend's /compare: one table on two endpoints - this
machine's stack or a deploy target - compared by schema, a bounded sample of rows
and published coverage (#2033).

This is the orchestration half: each side named, reached (a target through an ssh
tunnel, as `uqs query --tunnel` reaches one), and handed to q as an address. What
the comparison says is q's - src/etl/core/compare.q - and stack/data_diff.py runs
it. A side that cannot be reached is not a refusal here: it is handed to q with
its reason, and comes back as the `endpoint_unavailable` outcome.

It sits in deploy/, the lowest layer that may reach both this machine's stack and
a target, and outside uqs.cli so the frontend never loads the command line
(#2655): cli/data_diff.py keeps only the command's options and its text.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

from uqs import paths as stack_paths
from uqs.deploy import control, tunnel
from uqs.deploy import query_target as via_target
from uqs.paths import UqsError, UqsPaths, UqsUsageError, repo_root
from uqs.stack import alive, data_diff, listing
from uqs.stack import gateway as stack_gateway
from uqs.timefmt import timestamp
from uqs.timerange import TimeRange


def _local_port(paths: UqsPaths, proc: str) -> int:
    """The port `proc` listens on in the stack at `paths`, refusing one that is
    not declared there or not running - `uqs query`'s rule, for any stack."""
    ports = listing.configured_ports(paths)
    if proc not in ports:
        raise UqsError(f"{proc} is not declared in this stack")
    try:
        up = proc in alive.running(paths)
    except UqsError, OSError:  # ps could not answer: the connection will
        up = True
    if not up:
        raise UqsError(f"{proc} is not running - start it with `uqs start {proc}`")
    return int(ports[proc])


@contextmanager
def reach(
    name: str, user: str, passwd: str, paths: UqsPaths | None = None
) -> Iterator[data_diff.Endpoint]:
    """`name`'s gateway, for the block: this machine's (the stack at `paths`), or
    a target's through a tunnel.

    An undeclared target is refused - it is a mistake in the command - while one
    that is declared and cannot be reached is yielded with why, for q to report.
    """
    proc = stack_gateway.DEFAULT_PROC
    if name == data_diff.LOCAL:
        try:
            port = _local_port(paths or stack_paths.default_paths(), proc)
        except UqsError as exc:
            yield data_diff.Endpoint(name, error=f"{proc} on this machine: {exc}")
            return
        yield data_diff.Endpoint(name, address=f"localhost:{port}")
        return
    control.remote_for(
        name, paths.repo_root if paths else repo_root()
    )  # refuses a target nobody declared
    with ExitStack() as stack:
        try:
            # change=None: compare.q only reads (#2179)
            reached = via_target.connect(name, proc, None, None, user, passwd, change=None)
            via = stack.enter_context(reached)
        except Exception as exc:  # ssh, the release's port list or the tunnel: all one outcome
            said = tunnel.scrub(str(exc), [passwd])
            yield data_diff.Endpoint(name, error=f"{proc} on target {name}: {said}")
            return
        yield data_diff.Endpoint(name, address=f"{via_target.LOOPBACK}:{via.port}")


def _utc(value: Any) -> Any:
    """Every time in a report as ISO-8601 in UTC: q's `.j.j` writes a timestamp
    with no zone, and each one here is `.z.p`'s."""
    if isinstance(value, dict):
        times = ("range_from", "range_to")
        return {k: timestamp(v, naive="utc") if k in times else _utc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_utc(v) for v in value]
    return value


def run(
    paths: UqsPaths,
    left: str,
    right: str,
    tables: list[str],
    window: TimeRange,
    *,
    filters: list[str] | None = None,
    limit: int = data_diff.DEFAULT_LIMIT,
    source_version: str | None = None,
    partition: str | None = None,
    user: str = "admin",
    passwd: str = "admin",
    timeout: float = data_diff.DEFAULT_TIMEOUT,
) -> dict:
    """The comparison `uqs data diff --json` prints: both sides reached, q's
    report, its times in UTC and `ok` - what the frontend's /compare returns.

    @raise UqsUsageError for two sides that are one, or a --slice with no
    --source-version; UqsError for an undeclared target or a request q refuses
    (data_diff.DiffNotRun / DiffTimedOut when the q itself failed or ran past `timeout`
    seconds).
    """
    if left == right:
        raise UqsUsageError(f"--left and --right both name {left} - compare two endpoints")
    if partition and not source_version:
        raise UqsUsageError("--slice reads coverage, which needs --source-version")
    with reach(left, user, passwd, paths) as lside, reach(right, user, passwd, paths) as rside:
        report = data_diff.compare(
            paths, lside, rside, tables, window,
            filters=filters or [], limit=limit, source_version=source_version,
            partition=partition, login=f"{user}:{passwd}", timeout=timeout,
        )  # fmt: skip
    report = _utc(report)
    return {"ok": report["outcome"] == data_diff.EQUAL, **report}
