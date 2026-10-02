"""Which q interpreter runs this tree, and whether it is the one intended.

`$QCMD` names the binary, as TorQ spells it (#414). `$UQF_Q_IMPL` says which
implementation that binary is meant to be: `kdbx`, the default and the one
everything here is verified against, or `peachq`, the MIT-licensed PeachQ
interpreter.

PeachQ was once found by falling back to `./q` or `./peachq/q` when nothing
else was there - and a suite that passed on an interpreter nobody chose meant
less than no pass, so that was removed. It comes back only as a choice made
out loud: `UQF_Q_IMPL=peachq` together with a `QCMD` naming the binary. A bare
`q` on PATH is never taken to be PeachQ, and `identify` lets a caller check
that the binary really is what was declared, in both directions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import UqsError

#: The variable that chooses the q binary, everywhere (#414). It is TorQ's
#: own: `torq.sh` starts every stack process with `$QCMD`, defaulting to `q`,
#: so one setting reaches the stack, its HDB filler, the backfill launcher and
#: every test lane.
Q_INTERPRETER_ENV = "QCMD"

#: TorQ's default when QCMD is unset (lib/torq/torq.sh, lib/torq/setenv.sh).
#: Deliberately no ~/.kx/bin/q fallback on top: TorQ has none.
Q_DEFAULT_COMMAND = "q"

#: The variable that says which implementation `$QCMD` is.
Q_IMPL_ENV = "UQF_Q_IMPL"
KDBX = "kdbx"
PEACHQ = "peachq"
Q_IMPLS = (KDBX, PEACHQ)

#: A q script printing which implementation runs it. `.pq.load_natives` is
#: PeachQ's native-library loader (its own qlib/*.q call it first thing);
#: KDB-X has no such name, so `value` throws and the handler answers null.
#: scripts/test.py restates it, being run by a python3 that cannot import uqs;
#: test_interpreter.py holds the two equal.
IDENTIFY_SCRIPT = '-1 $[-7h=type @[value;`.pq.load_natives;{0N}];"kdbx";"peachq"];\nexit 0\n'


def q_impl(env: Mapping[str, str] | None = None) -> str:
    """The implementation `$QCMD` is declared to be: `kdbx` unless
    `$UQF_Q_IMPL` says `peachq`. Refuses any other value, and `peachq`
    without a `$QCMD` naming the binary."""
    source = os.environ if env is None else env
    impl = (source.get(Q_IMPL_ENV) or KDBX).strip().lower()
    if impl not in Q_IMPLS:
        raise UqsError(f"{Q_IMPL_ENV}={impl!r} - it is one of {', '.join(sorted(Q_IMPLS))}")
    if impl == PEACHQ and not source.get(Q_INTERPRETER_ENV):
        raise UqsError(
            f"{Q_IMPL_ENV}=peachq needs {Q_INTERPRETER_ENV} set to the PeachQ binary - "
            "a bare `q` on PATH is never taken to be PeachQ"
        )
    return impl


def q_command(env: Mapping[str, str] | None = None) -> str:
    """The q command as TorQ spells it: ``$QCMD`` if set, otherwise ``q``.

    Checks `$UQF_Q_IMPL` first, so an unknown value, or `peachq` without a
    binary named, is refused wherever q is about to be started."""
    source = os.environ if env is None else env
    q_impl(source)
    return source.get(Q_INTERPRETER_ENV) or Q_DEFAULT_COMMAND


def q_interpreter(env: Mapping[str, str] | None = None) -> Path | None:
    """The q interpreter ``q_command`` names, resolved - or None.

    A bare name resolves on the PATH of ``env`` (the process's own when env
    is None); a path must be an executable file. None is the caller's to
    handle: a test skips, a script refuses, the stack reports.
    """
    source = os.environ if env is None else env
    found = shutil.which(q_command(source), path=source.get("PATH"))
    return Path(found) if found else None


def identify(q: Path, env: Mapping[str, str] | None = None, timeout: float = 30.0) -> str:
    """Which implementation the binary `q` is, by asking it: `kdbx` or `peachq`."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "identify.q"
        script.write_text(IDENTIFY_SCRIPT)
        result = subprocess.run(
            [str(q), str(script), "-q"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if env is None else dict(env),
            check=False,
        )
    answer = result.stdout.strip().splitlines()[-1:] or [""]
    if answer[0] not in Q_IMPLS:
        raise UqsError(
            f"could not tell which q {q} is (exit {result.returncode}): "
            f"{(result.stdout + result.stderr).strip()[:200]!r}"
        )
    return answer[0]


def mismatch_message(q: Path, actual: str, declared: str) -> str:
    """What `check_identity` refuses with when the binary is not what was declared."""
    hint = (
        f"set {Q_IMPL_ENV}=peachq to run it knowingly"
        if actual == PEACHQ
        else f"point {Q_INTERPRETER_ENV} at the PeachQ binary"
    )
    return f"{q} is {actual}, but {Q_IMPL_ENV} declares {declared} - {hint}"


def check_identity(q: Path, env: Mapping[str, str] | None = None) -> str:
    """`identify`, refusing a binary that is not the declared implementation."""
    declared = q_impl(env)
    actual = identify(q, env)
    if actual != declared:
        raise UqsError(mismatch_message(q, actual, declared))
    return actual


@dataclass(frozen=True)
class InterpreterStatus:
    """Which q the stack would run, for commands that report rather than run.

    `declared` is what $UQF_Q_IMPL says (or the refusal, when it says
    something invalid); `binary` the resolved $QCMD or None; `actual` what the
    binary answers when asked, None when there is no binary or no answer;
    `problem` what is wrong, in check_identity's words when the two disagree.
    """

    declared: str
    binary: Path | None
    actual: str | None
    problem: str | None


def interpreter_status(
    env: Mapping[str, str] | None = None, timeout: float = 10.0
) -> InterpreterStatus:
    """The interpreter, described - never refused.

    For `uqs list env` and `uqs summary`, which only read: an operator looking
    at a fleet needs to see which q runs it, and a reporting command that died
    on a mismatch would hide exactly the thing worth reporting.
    """
    source = os.environ if env is None else env
    try:
        declared = q_impl(source)
    except UqsError as exc:
        return InterpreterStatus(f"invalid ({exc})", None, None, str(exc))
    binary = q_interpreter(source)
    if binary is None:
        return InterpreterStatus(declared, None, None, None)
    try:
        actual = identify(binary, source, timeout=timeout)
    except (UqsError, OSError, subprocess.TimeoutExpired) as exc:
        return InterpreterStatus(declared, binary, None, f"could not ask {binary}: {exc}")
    problem = None if actual == declared else mismatch_message(binary, actual, declared)
    return InterpreterStatus(declared, binary, actual, problem)
