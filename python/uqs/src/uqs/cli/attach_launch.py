"""`uqs job attachment add`'s launch options: what it does with the publisher it starts (#2687).

After verifying, `add` starts the attached publisher, so it takes `uqs start`'s
launch options with `start`'s meanings (cli/launch_options.py): `--trace`
starts the publisher with `-trace` on its start line (its process script loads
uqf through `.qtorq.load_uqf`, which reads it), `--follow` streams its log with
`start --follow`'s follower (cli/start_follow.py), `--level` filters that, and
`--stop-on-exit` stops it on Ctrl-C through `uqs stop`'s path. None applies
with `--no-start`, which starts nothing. Split from cli/attach.py, which holds
its modules to 400 lines.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from uqs.attach.config import Attachment
from uqs.attach.publisher import Publisher
from uqs.cli import launch_options, start_follow, start_warnings
from uqs.paths import UqsUsageError


def refusal(
    *,
    start: bool,
    follow: bool,
    level: str | None,
    stop_on_exit: bool,
    trace: bool,
    as_json: bool,
    dry_run: bool,
) -> UqsUsageError | None:
    """The first launch option given where it cannot apply, or None."""
    if not start:
        given = [
            flag
            for flag, on in (
                ("--follow", follow),
                ("--level", level is not None),
                ("--stop-on-exit", stop_on_exit),
                ("--trace", trace),
            )
            if on
        ]
        if given:
            return UqsUsageError(
                f"{given[0]} does not apply with --no-start: it acts on the publisher this "
                "starts, and --no-start leaves it stopped"
            )
    refused = launch_options.refusal(
        follow=follow, level=level, stop_on_exit=stop_on_exit, dry_run=dry_run
    )
    if refused is None and follow and as_json:
        refused = UqsUsageError(f"{launch_options.FOLLOW_NOT_WITH_JSON}: use --json alone")
    return refused


def run(
    att: Attachment,
    publisher: Publisher,
    stages: Callable[[], None],
    *,
    follow: bool,
    level: str | None,
    stop_on_exit: bool,
    trace: bool,
    as_json: bool,
) -> None:
    """Run `stages` - save, apply, verify, start and report, which always exits -
    and with `follow` stream the publisher's log from before it starts until
    Ctrl-C, as `uqs start -f` does. A publisher already running is not
    restarted, so `--trace` does not reach it: said, as `start` says it."""
    proc, port = att.publisher.procname, att.stack.id
    up = publisher.running(att)
    if trace and up and not as_json:
        start_warnings.trace_unreached([proc], port)
    if not follow:
        stages()
        return

    def before() -> None:
        try:
            stages()
        except typer.Exit as exc:  # the report ends with an exit; only a failure ends the stream
            if exc.exit_code:
                raise

    start_follow.follow([proc], {proc} if up else set(), before, port, level, stop=stop_on_exit)
