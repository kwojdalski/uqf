"""/activity: the activity history and the browser's event batches (#1954)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from uqf_frontend.instants import AwareInstant, to_datetime
from uqf_frontend.models.base import STRICT_REQUEST

#: activity.SOURCES, BROWSER_KINDS + SERVER_KIND, OUTCOMES, TRIGGERS and
#: activity_log's LEVELS and DROP_REASONS, as words the browser's types are
#: held to (test_activity.py holds these to activity.py, test_api_types.py
#: holds api.ts to these).
ActivitySource = Literal["browser", "server"]
ActivityKind = Literal[
    "navigate",
    "select",
    "submit",
    "confirm",
    "cancel",
    "result",
    "interact",
    "debug",
    "request",
    "attempt",
]
BrowserKind = Literal[
    "navigate", "select", "submit", "confirm", "cancel", "result", "interact", "debug"
]
#: What a session records at; `off` records no browser history.
SessionLevel = Literal["off", "actions", "interactions", "debug"]
#: What an event is recorded under.
ActivityLevel = Literal["actions", "interactions", "debug"]
ActivityDropReason = Literal["sampled", "queue_overflow", "row_cap", "retention"]
#: The reasons the browser reports; the others are the server's own.
BrowserDropReason = Literal["sampled", "queue_overflow"]
ActivityExportFormat = Literal["jsonl", "csv", "text"]
ActivityOutcome = Literal["pending", "succeeded", "failed", "refused", "accepted", "unknown"]
ActivityTrigger = Literal["pointer", "keyboard", "programmatic", "unknown"]

_ACTIVITY_ID = r"^[A-Za-z0-9_-]{8,64}$"


#: The refusal of an activity event's or item's old `target` (#2234).
_SUBJECT_RENAMED = (
    "`target` is `subject`: what the event is about - a view, a control, a process "
    "selector. `target` names a deployment target (#2234)"
)


def _refuse_target(data: Any) -> Any:
    if isinstance(data, dict) and "target" in data:
        raise ValueError(_SUBJECT_RENAMED)
    return data


class ActivityItem(BaseModel):
    """One item's outcome, for an action over several things."""

    model_config = STRICT_REQUEST

    subject: str = Field(min_length=1, max_length=200)
    outcome: ActivityOutcome

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        return _refuse_target(data)


class BrowserEventIn(BaseModel):
    """One thing an operator did in the UI.

    Strict, and every field bounded: `detail` keys outside
    activity.DETAIL_KEYS are dropped before anything is stored, and there is
    no field for a typed value, a token or a file's contents.
    """

    model_config = STRICT_REQUEST

    event_id: str = Field(pattern=_ACTIVITY_ID)
    session_id: str = Field(pattern=_ACTIVITY_ID)
    #: When it happened, by the browser's clock; must carry a timezone. `time`,
    #: as `uqs activity list --json` and every log-line record say it (#2254).
    time: AwareInstant
    kind: BrowserKind
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    #: What the event is about: a view, a control, a process selector.
    subject: str | None = Field(default=None, max_length=200)
    trigger: ActivityTrigger
    request_id: str | None = Field(default=None, pattern=_ACTIVITY_ID)
    outcome: ActivityOutcome | None = None
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    items: list[ActivityItem] = Field(default_factory=list, max_length=256)
    detail: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        if isinstance(data, dict) and "ts" in data:
            raise ValueError("`ts` is `time`: when it happened, by the browser's clock (#2254)")
        return _refuse_target(data)

    @field_validator("time")
    @classmethod
    def _held_in_utc(cls, time: str) -> str:
        """An instant UTC can hold, to the microsecond rows are stored at -
        else the whole batch is a 422 here, before any of it is written (#2021)."""
        to_datetime(time)
        return time


class BrowserDropIn(BaseModel):
    """Events the browser did not send, counted: sampled by debug's rate
    limit, or pushed out of its queue while the API was unreachable."""

    model_config = STRICT_REQUEST

    level: ActivityLevel
    reason: BrowserDropReason
    count: int = Field(ge=1, le=1_000_000)


class ActivityBatchRequest(BaseModel):
    """What the browser sends: a batch, so navigation does not cost a request
    each - and what it could not send, so nothing is lost without a number."""

    model_config = STRICT_REQUEST

    events: list[BrowserEventIn] = Field(default_factory=list, max_length=100)
    #: Events this browser did not send, by level and reason - the same
    #: records GET /activity answers as `drops` (#2242).
    drops: list[BrowserDropIn] = Field(default_factory=list, max_length=16)
    #: The id of this batch's drop report, required with `drops`. A browser
    #: that sends a report again - its answer was lost - sends the same id,
    #: and its drops are counted once, as an event is stored once by its
    #: event_id (#2480).
    batch_id: str | None = Field(default=None, pattern=_ACTIVITY_ID)

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        if isinstance(data, dict) and "dropped" in data:
            raise ValueError("`dropped` is `drops`: the events this browser did not send (#2242)")
        return data

    @model_validator(mode="after")
    def _drops_have_an_id(self) -> ActivityBatchRequest:
        if self.drops and self.batch_id is None:
            raise ValueError("a batch with `drops` needs a batch_id, so a retry counts them once")
        return self


class ActivityBatchResponse(BaseModel):
    """What became of a batch."""

    #: Events written now.
    stored: int
    #: Events not written: a repeated event_id, or a failed write.
    not_stored: int
    #: `detail` keys dropped for being outside the allowlist or ill-formed.
    dropped_detail: int
    #: False while the history is in memory only.
    persistent: bool


class ActivityEventOut(BaseModel):
    """One stored event. `claimed_user` is the x-uqf-user header as sent: a
    label, never an authenticated identity."""

    event_id: str
    time: AwareInstant
    recorded_at: AwareInstant
    level: ActivityLevel
    source: ActivitySource
    kind: ActivityKind
    action: str
    #: The deployment target a server row acted on, `local` for this
    #: server's own stack; None for a browser row and an unrouted request.
    target: str | None
    #: What it acted on: a process selector, a cfg key, a worker, a route, a
    #: view or a control (#2234).
    subject: str | None
    outcome: ActivityOutcome | None
    session_id: str | None
    request_id: str | None
    claimed_user: str | None
    trigger: ActivityTrigger | None
    method: str | None
    status_code: int | None
    duration_ms: float | None
    items: list[ActivityItem]
    detail: dict[str, Any]
    #: `detail` keys dropped for being outside the allowlist or ill-formed,
    #: named as the batch response names them (#2242).
    dropped_detail: int


class ActivityPersistence(BaseModel):
    """Whether history is being kept, and whether writes have failed."""

    persistent: bool
    path: str | None
    failures: int
    last_error: str | None
    last_failure_at: AwareInstant | None


class ActivityBound(BaseModel):
    """How much of one level is kept: the tighter of the two wins."""

    level: ActivityLevel
    retention_days: int
    max_kept: int


class ActivityDrop(BaseModel):
    """Events not in the history, by level and why, since the file began."""

    level: ActivityLevel
    reason: ActivityDropReason
    count: int
    last_at: AwareInstant


class ActivityResponse(BaseModel):
    """The history, newest first, with how it is kept and what it lost."""

    events: list[ActivityEventOut]
    truncated: bool = Field(
        description="more events matched than were returned: `limit` cut them (#2220)"
    )
    persistence: ActivityPersistence
    #: The level a session records at unless its operator picks another - the
    #: `activity_level` setting, named as /health and /settings name it (#2240).
    activity_level: SessionLevel
    bounds: list[ActivityBound]
    drops: list[ActivityDrop]
    poll_seconds: int
