"""Stage-by-stage results with explicit PASS / FAIL and bounded waits.

A verifier that prints "done" proves nothing. Every stage here is a named
question with a deadline: it prints PASS with what it found, or FAIL with what
it expected and what it saw - and after the first FAIL the stages that depend
on it print SKIP, so one broken stage is one line of cause, not twenty of effect.
Nothing printed here is a credential.
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from typing import Any, TextIO


class Report:
    """Runs named stages, prints each result, and remembers whether all passed."""

    def __init__(self, out: TextIO | None = None) -> None:
        self.out = out or sys.stdout
        self.results: list[tuple[str, str, str]] = []
        self.failed = False

    def _emit(self, status: str, name: str, detail: str) -> None:
        self.results.append((status, name, detail))
        print(f"{status:<5} {name:<24} {detail}", file=self.out, flush=True)

    def run(self, name: str, fn: Callable[[], Any], *, always: bool = False) -> Any:
        """Run `fn`; it returns a detail string (PASS) or (detail, value), or raises
        `Fail` / any exception (FAIL). Returns the value, or None on FAIL or SKIP."""
        if self.failed and not always:
            self._emit("SKIP", name, "an earlier stage failed")
            return None
        try:
            got = fn()
        except Fail as exc:
            self.failed = True
            self._emit("FAIL", name, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001 - a stage that crashed is a stage that failed
            self.failed = True
            self._emit(
                "FAIL",
                name,
                f"{type(exc).__name__}: {exc} ({traceback.format_exc().splitlines()[-3].strip()})",
            )
            return None
        detail, value = got if isinstance(got, tuple) else (got, None)
        self._emit("PASS", name, detail)
        return value if value is not None else True

    def expect(self, name: str, fn: Callable[[], Any]) -> Any:
        """Like `run`, for a stage whose PASS is a refusal that was diagnosed as expected.
        A failed earlier stage does not skip it: a denial is checked on its own broker."""
        failed, self.failed = self.failed, False
        try:
            return self.run(name, fn)
        finally:
            self.failed = failed or self.failed

    @property
    def ok(self) -> bool:
        return not any(s == "FAIL" for s, _, _ in self.results)

    def summary(self) -> str:
        n = {s: sum(1 for r in self.results if r[0] == s) for s in ("PASS", "FAIL", "SKIP")}
        verdict = "PASS" if self.ok else "FAIL"
        return f"{verdict}: {n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped"


class Fail(Exception):
    """A stage's own verdict: the message is what it expected and what it saw."""


def wait_until(
    check: Callable[[], Any], timeout: float, *, interval: float = 0.5, what: str = "the condition"
) -> Any:
    """The first truthy `check()` within `timeout` seconds; `Fail` naming `what` otherwise.

    `check` may return `(done, seen)`: `seen` is what the message reports on a
    timeout, so it says how far the condition got, and `done` (or `seen` for a
    plain value) is what is returned.
    """
    deadline = time.monotonic() + timeout
    while True:
        got = check()
        done, last = got if isinstance(got, tuple) else (got, got)
        if done:
            return done if done is not True else last
        if time.monotonic() >= deadline:
            raise Fail(f"{what} did not hold within {timeout:.0f}s (last seen: {last!r})")
        time.sleep(interval)
