"""Client for a running uqf q session, over kdb+ IPC via kola."""

from __future__ import annotations

from typing import Any

import kola


class UqfClient:
    """Thin wrapper around kola.Q for querying a running uqf q session.

    Expects a q process with `src/init.q` loaded and listening on a port,
    e.g. `q src/init.q -p 5000` from the uqf repo root, so `.uqf.*`
    functions are callable by name.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        user: str = "",
        passwd: str = "",
        enable_tls: bool = False,
        retries: int = 0,
        timeout: int = 0,
    ) -> None:
        self._q = kola.Q(
            host,
            port,
            user=user,
            passwd=passwd,
            enable_tls=enable_tls,
            retries=retries,
            timeout=timeout,
        )

    def __enter__(self) -> UqfClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.disconnect()

    def connect(self) -> None:
        self._q.connect()

    def disconnect(self) -> None:
        self._q.disconnect()

    def sync(self, expr: str, *args: Any) -> Any:
        """Send a synchronous q expression/query and return the result."""
        return self._q.sync(expr, *args)

    def call(self, fn: str, *args: Any) -> Any:
        """Call a `.uqf.<fn>` function, e.g. `call("gkCall", 1.10, 1.12, 0.045, 0.02, 0.10, 0.75)`.

        Equivalent to `sync(".uqf." + fn, *args)`.
        """
        return self._q.sync(f".uqf.{fn}", *args)
