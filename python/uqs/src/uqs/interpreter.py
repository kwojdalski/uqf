"""Which q interpreter runs this tree, and which implementation it is.

`$QCMD` names the binary, as TorQ spells it (#414), and it is the ONLY
variable that chooses one (#1753). Which implementation that binary is -
`kdbx`, the default and the one everything here is verified against, or
`peachq`, the MIT-licensed PeachQ interpreter - is asked of the binary itself
(`identify`), never declared beside it: a declaration only restated what the
binary already says.

PeachQ was once found by falling back to `./q` or `./peachq/q` when nothing
else was there - and a suite that passed on an interpreter nobody chose meant
less than no pass, so that was removed and nothing falls back to it now.
`QCMD` naming a PeachQ binary is the choice, made out loud; a bare `q` on PATH
that answers `peachq` is PeachQ too, because someone put it there.

The PeachQ lanes need a PeachQ even when `QCMD` is KDB-X. `peachq_binary` is
the one place they get it: `QCMD` when it is PeachQ, else the pinned build
from scripts/peachq.py. A PeachQ that is not the pinned build is used, and
named in one warning.

Three other variables chose these before #1753 (`REMOVED_ENV`). They are
refused, not ignored: a variable that silently
stopped meaning anything would leave a run on an interpreter nobody chose.
"""

from __future__ import annotations

import functools
import importlib.util
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from uqs.logger import get_logger
from uqs.paths import UqsError, repo_root

log = get_logger()

#: The variable that chooses the q binary, everywhere (#414). It is TorQ's
#: own: `torq.sh` starts every stack process with `$QCMD`, defaulting to `q`,
#: so one setting reaches the stack, its HDB filler, the backfill launcher and
#: every test lane.
Q_INTERPRETER_ENV = "QCMD"

#: TorQ's default when QCMD is unset (lib/torq/torq.sh, lib/torq/setenv.sh).
#: Deliberately no ~/.kx/bin/q fallback on top: TorQ has none.
Q_DEFAULT_COMMAND = "q"

KDBX = "kdbx"
PEACHQ = "peachq"
Q_IMPLS = (KDBX, PEACHQ)

#: The variables that chose an interpreter before #1753, each with what
#: replaces it. Set at all, each is refused - by uqs, scripts/test.py (through
#: scripts/peachq.py, which spells the same list; test_interpreter.py holds
#: the two equal) and deploy's preflight on the server.
REMOVED_ENV = {
    "UQF_Q_IMPL": "the binary is asked which q it is - set QCMD=<binary> alone",
    "UQF_PEACHQ": (
        "set QCMD=<binary> to run everything on that PeachQ, or pass --peachq PATH "
        "(scripts/test.py, pytest) for the PeachQ lanes alone"
    ),
    "UQF_PEACHQ_REASON": (
        "a PeachQ in QCMD that is not the pinned build is named in a warning instead"
    ),
}

#: A q script printing which implementation runs it. `.pq.load_natives` is
#: PeachQ's native-library loader (its own qlib/*.q call it first thing);
#: KDB-X has no such name, so `value` throws and the handler answers null.
#: scripts/peachq.py restates it, being run by a python3 that cannot import
#: uqs; test_interpreter.py holds the two equal.
IDENTIFY_SCRIPT = '-1 $[-7h=type @[value;`.pq.load_natives;{0N}];"kdbx";"peachq"];\nexit 0\n'


#: The first kdb+ major whose q loads a nested working context (`\d .a.b`),
#: which this tree is written in (#882). An older kdb+ - 4.0 is a supported
#: deployment target since #861 - and PeachQ (peachq-org/peachq#80) load only
#: a tree scripts/portable/flatten_contexts.py has converted. THE one answer
#: to "can this q load the tree as written?": deploy's preflight
#: (deploy/artifact.compatible) and a local start (stack/qtree) both ask it.
NESTED_CONTEXTS_SINCE = 5

#: A q script printing its kdb+ version, .z.K.
VERSION_SCRIPT = "-1 string .z.K;\nexit 0\n"


def loads_nested_contexts(impl: str, version: str) -> bool | None:
    """Can a q of implementation `impl` reporting `.z.K` = `version` load this
    tree as written? None when `version` says nothing - an unparsable answer
    is not evidence either way, and is never a reason to refuse."""
    if impl == PEACHQ:
        return False
    major = version.strip().split(".")[0]
    if not major.isdigit():
        return None
    return int(major) >= NESTED_CONTEXTS_SINCE


def nested_contexts_problem(who: str, version: str, remedy: str) -> str:
    """Why `who`'s q cannot load the tree as written, and what to do."""
    return (
        f"{who} is kdb+ {version}, which has no nested contexts "
        f"(they arrived in {NESTED_CONTEXTS_SINCE}.0) - {remedy}"
    )


@functools.cache
def q_version(q: str, timeout: float = 30.0) -> str:
    """The binary `q`'s `.z.K`, by asking it; "" when it does not answer.
    Once per binary per process."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "version.q"
        script.write_text(VERSION_SCRIPT)
        try:
            r = subprocess.run(
                [q, str(script), "-q"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except OSError, subprocess.TimeoutExpired:
            return ""
    lines = r.stdout.strip().splitlines()
    return lines[-1].strip() if lines else ""


def removed_variables_problem(env: Mapping[str, str] | None = None) -> str | None:
    """Why the environment cannot be used as it is - a variable REMOVED_ENV
    names is set - or None. Each is named with what replaces it."""
    source = os.environ if env is None else env
    found = [name for name in REMOVED_ENV if name in source]
    if not found:
        return None
    return (
        "; ".join(f"{name} is no longer read - {REMOVED_ENV[name]}; unset it" for name in found)
        + " (#1753). A deploy.env written before then sets them: redeploy the release."
    )


def refuse_removed(env: Mapping[str, str] | None = None) -> None:
    """`removed_variables_problem`, as a refusal."""
    problem = removed_variables_problem(env)
    if problem:
        raise UqsError(problem)


def q_command(env: Mapping[str, str] | None = None) -> str:
    """The q command as TorQ spells it: ``$QCMD`` if set, otherwise ``q``.

    Refuses a variable REMOVED_ENV names first, wherever q is about to be
    started: it would otherwise be ignored, and the run would be on a q
    nobody chose."""
    source = os.environ if env is None else env
    refuse_removed(source)
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
    """Which implementation the binary `q` is, by asking it: `kdbx` or `peachq`.
    Refuses, with what the binary printed, when it answers neither."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "identify.q"
        script.write_text(IDENTIFY_SCRIPT)
        try:
            result = subprocess.run(
                [str(q), str(script), "-q"],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=None if env is None else dict(env),
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UqsError(f"could not tell which q {q} is: {exc}") from None
    answer = result.stdout.strip().splitlines()[-1:] or [""]
    if answer[0] not in Q_IMPLS:
        raise UqsError(
            f"could not tell which q {q} is (exit {result.returncode}): "
            f"{(result.stdout + result.stderr).strip()[:200]!r}"
        )
    return answer[0]


@functools.cache
def _identified(resolved: str, stamp: tuple[int, int]) -> str:
    """`identify`, once per binary per process: keyed on the resolved path and
    its (mtime, size), so a binary replaced in place is asked again."""
    return identify(Path(resolved))


def identified(q: Path) -> str:
    """Which implementation `q` is, asked once per binary per process."""
    resolved = Path(q).resolve()
    st = resolved.stat()
    return _identified(str(resolved), (st.st_mtime_ns, st.st_size))


def q_impl(env: Mapping[str, str] | None = None) -> str:
    """Which implementation `$QCMD` is: `identify(q_interpreter())`, asked once
    per resolved binary.

    `kdbx` when there is no runnable q at all: nothing can run, KDB-X is what
    this tree targets, and whatever starts q reports the missing binary in its
    own words. A binary that answers neither is refused, with its output. A
    PeachQ that is not the pinned build is named in one warning.
    """
    source = os.environ if env is None else env
    q = q_interpreter(source)
    if q is None:
        return KDBX
    impl = identified(q)
    if impl == PEACHQ:
        warn_if_unpinned(q)
    return impl


# ------------------------------------------------------------------ PeachQ


def peachq_module(scripts_dir: Path | None = None) -> ModuleType:
    """scripts/peachq.py, the pinned PeachQ's resolver, loaded once per
    path. Loaded by path rather than imported: it runs under a bare python3
    in CI, and scripts/ is no package. `scripts_dir` defaults to this
    checkout's (or release's)."""
    return _load_peachq((scripts_dir or repo_root() / "scripts").resolve() / "peachq.py")


@functools.cache
def _load_peachq(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("uqf_peachq", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise UqsError(f"cannot load {path}, the PeachQ resolver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The binaries already warned about, so each is named once per process.
_WARNED: set[Path] = set()


def warn_if_unpinned(q: Path, scripts_dir: Path | None = None) -> None:
    """One warning, naming the binary and the pin, when `q` is a PeachQ other
    than the pinned build (scripts/peachq.py `unpinned_warning`). Replaces the
    reason an override once had to give: the run goes ahead, and says on
    what."""
    resolved = Path(q).resolve()
    if resolved in _WARNED:
        return
    _WARNED.add(resolved)
    try:
        module = peachq_module(scripts_dir)
        message = module.unpinned_warning(resolved)
    except Exception as exc:  # noqa: BLE001 - a warning must not stop a run
        message = f"{resolved} is PeachQ, and could not be checked against the pin: {exc}"
    if message:
        log.warning("{}", message)


def peachq_binary(
    env: Mapping[str, str] | None = None,
    *,
    override: str | Path | None = None,
    scripts_dir: Path | None = None,
) -> Path:
    """THE PeachQ for whatever must run on PeachQ (#1753), in this order:

    1. `override` - `--peachq PATH`, from scripts/test.py or pytest - which
       must identify as PeachQ: an explicit choice that failed is refused,
       never replaced by a guess;
    2. `$QCMD`, when it answers as PeachQ;
    3. the pinned build, from scripts/peachq.py's cache, built on first use.

    A PeachQ from 1 or 2 that is not the pinned build is named in one
    warning. scripts/test.py, which cannot import uqs, keeps a copy of this
    rule; test_interpreter.py holds the two equal.
    """
    source = os.environ if env is None else env
    refuse_removed(source)
    if override:
        found = shutil.which(str(override), path=source.get("PATH"))
        if found is None:
            raise UqsError(f"--peachq {override} is not a runnable file")
        actual = identified(Path(found))
        if actual != PEACHQ:
            raise UqsError(f"--peachq {override} is {actual}, not PeachQ")
        warn_if_unpinned(Path(found), scripts_dir)
        return Path(found)
    q = q_interpreter(source)
    if q is not None and identified(q) == PEACHQ:
        warn_if_unpinned(q, scripts_dir)
        return q
    module = peachq_module(scripts_dir)

    def asked(binary: Path | str) -> str:
        try:
            return identified(Path(binary))
        except UqsError, OSError:
            return ""

    try:
        return module.resolve(dict(source), identify=asked)
    except module.PeachQError as exc:
        raise UqsError(f"no PeachQ: {exc}") from None


# ------------------------------------------------------- reporting it


@dataclass(frozen=True)
class InterpreterStatus:
    """Which q the stack would run, for commands that report rather than run.

    `binary` the resolved $QCMD or None; `actual` what the binary answers when
    asked, None when there is no binary or no answer; `problem` what is
    wrong - a removed variable set, or a binary that would not say.
    """

    binary: Path | None
    actual: str | None
    problem: str | None


def interpreter_status(
    env: Mapping[str, str] | None = None, timeout: float = 10.0
) -> InterpreterStatus:
    """The interpreter, described - never refused.

    For `uqs list env` and `uqs summary`, which only read: an operator looking
    at a fleet needs to see which q runs it, and a reporting command that died
    on a problem would hide exactly the thing worth reporting.
    """
    source = os.environ if env is None else env
    problem = removed_variables_problem(source)
    if problem:
        return InterpreterStatus(None, None, problem)
    binary = q_interpreter(source)
    if binary is None:
        return InterpreterStatus(None, None, None)
    try:
        actual = identify(binary, source, timeout=timeout)
    except UqsError as exc:
        return InterpreterStatus(binary, None, f"could not ask {binary}: {exc}")
    if actual == PEACHQ:
        warn_if_unpinned(binary)
    return InterpreterStatus(binary, actual, None)
