"""/health: whether the gateway is up, and what the browser polls by."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from uqf_frontend.models.activity import SessionLevel


class HealthResponse(BaseModel):
    poll_seconds: int
    #: How long the browser waits for any request - Settings.request_timeout,
    #: served so the client does not keep its own copy (#1370).
    request_timeout_seconds: int
    ok: bool
    gateway: Literal["up", "reloading", "unreachable", "wrong_process"]
    detail: str | None = None
    #: The level a browser session records its activity at until its operator
    #: picks another (Settings.activity_level, #2000) - served here, beside
    #: the timeout, so the browser keeps no copy of the server's default.
    activity_level: SessionLevel
