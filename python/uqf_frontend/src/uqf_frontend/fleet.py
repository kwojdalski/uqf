"""Fan-out to individual TorQ processes.

``.usage.usage`` lives in each process and there is **no fleet-wide rollup**
(F-04), so a single query log across the stack must be assembled here by
querying each process directly.

The design rule: **one unreachable process must not blank the whole view.**
:meth:`Fleet.per_process` therefore returns a result *or* an error per
process, and the caller reports both. An ops dashboard that goes blank
because one process is down is worse than one that shows nine processes and
names the tenth as unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from uqf_frontend.config import Process, Settings


@dataclass(frozen=True)
class ProcessResult:
    """What one process returned, or why it could not be reached."""

    process: str
    ok: bool
    value: Any = None
    error: str | None = None


@runtime_checkable
class Fleet(Protocol):
    """Reach individual processes, one at a time or all at once.

    ``one`` exists because per-process state - a capture watermark, say -
    means each process needs *different* arguments, which a single fan-out
    cannot express.
    """

    @property
    def processes(self) -> tuple[str, ...]: ...

    def one(self, process: str, program: str, *args: Any) -> ProcessResult: ...

    def per_process(self, program: str, *args: Any) -> list[ProcessResult]: ...


class KolaFleet:
    """A :class:`Fleet` over real IPC connections, one per process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._by_name: dict[str, Process] = {p.name: p for p in settings.processes}

    @property
    def processes(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    def one(self, process: str, program: str, *args: Any) -> ProcessResult:
        from uqf_frontend.gateway import KolaGateway

        proc = self._by_name.get(process)
        if proc is None:
            return ProcessResult(process=process, ok=False, error="not a configured process")
        per_proc = Settings(
            host=proc.host,
            port=proc.port,
            user=self._settings.user,
            passwd=self._settings.passwd,
            timeout=self._settings.timeout,
            max_rows=self._settings.max_rows,
        )
        try:
            return ProcessResult(
                process=process, ok=True, value=KolaGateway(per_proc).call(program, *args)
            )
        except Exception as exc:
            # Deliberately broad: any failure reaching one process is reported
            # against that process so the fan-out continues.
            return ProcessResult(process=process, ok=False, error=str(exc))

    def per_process(self, program: str, *args: Any) -> list[ProcessResult]:
        return [self.one(name, program, *args) for name in self.processes]


class FakeFleet:
    """An in-process :class:`Fleet` for tests."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        #: process name -> value, or process name -> Exception to simulate a
        #: process being down. A callable is invoked with the call args, so a
        #: test can vary the answer by watermark.
        self.responses: dict[str, Any] = responses or {}
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    @property
    def processes(self) -> tuple[str, ...]:
        return tuple(self.responses)

    def one(self, process: str, program: str, *args: Any) -> ProcessResult:
        self.calls.append((process, program, args))
        value = self.responses.get(process)
        if isinstance(value, Exception):
            return ProcessResult(process=process, ok=False, error=str(value))
        if callable(value):
            value = value(*args)
        return ProcessResult(process=process, ok=True, value=value)

    def per_process(self, program: str, *args: Any) -> list[ProcessResult]:
        return [self.one(name, program, *args) for name in self.processes]
