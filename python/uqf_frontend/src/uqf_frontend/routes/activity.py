"""/activity: the activity history and the browser's event batches (#1954)."""

from __future__ import annotations

import datetime as dt
import typing
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

from uqf_frontend import activity, instants, limits, ops, write_guard
from uqf_frontend.config import Settings
from uqf_frontend.errors import TooManyRequests, ValidationFailed
from uqf_frontend.instants import AwareInstant
from uqf_frontend.models.activity import (
    ActivityBatchRequest,
    ActivityBatchResponse,
    ActivityBound,
    ActivityDrop,
    ActivityEventOut,
    ActivityExportFormat,
    ActivityLevel,
    ActivityOutcome,
    ActivityPersistence,
    ActivityResponse,
    ActivitySource,
)
from uqf_frontend.routes.common import audit_user
from uqs import activity_export, activity_log


def _carries_token(settings: Settings, request: Request) -> bool:
    """Does this request carry the write token? Never, while there is none."""
    return bool(settings.write_token) and (
        write_guard.token_refusal(settings, request.headers.get("authorization")) is None
    )


def _instant(name: str, raw: str | None) -> dt.datetime | None:
    """A filter bound - an AwareInstant the route has already checked - as
    the datetime the activity log compares, exactly or refused."""
    if raw is None:
        return None
    parsed = instants.bound(name, raw)
    try:
        activity.stamp(parsed)  # what the query will compare with (#2021)
    except ValueError:
        raise ValidationFailed(
            f"{name} is outside the range a UTC timestamp can hold, got {raw!r}"
        ) from None
    return parsed


def install(
    app: FastAPI,
    *,
    settings: Settings,
    live: Callable[[], Settings],
    history: activity.ActivityStore,
) -> None:
    """Add these routes to *app*. Called by create_app in the order its
    routes are declared: FastAPI lists the OpenAPI paths in registration
    order, and test_openapi_snapshot.py holds that order."""

    @app.post("/activity/events", response_model=ActivityBatchResponse)
    def record_activity(batch: ActivityBatchRequest, request: Request) -> ActivityBatchResponse:
        """What an operator did in the UI. Intent, as the browser reports it -
        the server's own rows are the authority on what happened. Written in
        one transaction, and with it the count of what the browser could not
        send. Without the write token, rate-limited (#2019) - its drops as
        well as its events, so a drop-only batch is not free (#2480)."""
        if not _carries_token(settings, request) and not request.app.state.activity_rate.take(
            len(batch.events) + len(batch.drops)
        ):
            raise TooManyRequests(
                "too many activity events without the write token - the batch is accepted "
                "again in a few seconds"
            )
        claimed = audit_user(request)
        dropped_detail = sum(activity.clean_detail(event.detail)[1] for event in batch.events)
        stored, not_stored = history.add_many(
            {
                "source": "browser",
                "kind": event.kind,
                "action": event.action,
                "time": instants.to_datetime(event.time),
                "event_id": event.event_id,
                "subject": event.subject,
                "outcome": event.outcome,
                "session_id": event.session_id,
                "request_id": event.request_id,
                "claimed_user": claimed,
                "trigger": event.trigger,
                "duration_ms": event.duration_ms,
                "items": [item.model_dump() for item in event.items],
                "detail": event.detail,
            }
            for event in batch.events
        )
        if batch.batch_id is not None and batch.drops:
            history.note_reported_drops(
                batch.batch_id, ((lost.level, lost.reason, lost.count) for lost in batch.drops)
            )
        return ActivityBatchResponse(
            stored=stored,
            not_stored=not_stored,
            dropped_detail=dropped_detail,
            persistent=history.persistence.persistent,
        )

    def _activity_query(
        filters: dict[str, Any], level: str | None, limit: int
    ) -> activity_log.Query:
        """The one reading of the filters, for the view and the export alike."""
        return activity_log.Query(
            since=_instant("since", filters["since"]),
            until=_instant("until", filters["until"]),
            session_id=filters["session_id"] or None,
            request_id=filters["request_id"] or None,
            action=filters["action"] or None,
            target=filters["target"] or None,
            subject=filters["subject"] or None,
            outcome=filters["outcome"],
            source=filters["source"],
            level=level,
            limit=limit,
        )

    @app.get("/activity", response_model=ActivityResponse)
    def list_activity(
        since: AwareInstant | None = None,
        until: AwareInstant | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        subject: str | None = None,
        outcome: ActivityOutcome | None = None,
        source: ActivitySource | None = None,
        level: ActivityLevel | None = None,
        limit: int = limits.row_limit(activity_log.DEFAULT_LIMIT, activity_log.MAX_LIMIT),
    ) -> ActivityResponse:
        """The history, newest first, filtered; whether it is being kept, how
        much of each level is, and what was not. `level` is the most
        detailed level shown: `interactions` shows actions too."""
        filters = dict(locals())
        # One event past the limit says whether more matched (#2220).
        rows = history.query(_activity_query(filters, level, limit + 1))
        state = history.persistence
        return ActivityResponse(
            events=[ActivityEventOut(**row) for row in rows[:limit]],
            truncated=len(rows) > limit,
            persistence=ActivityPersistence(
                persistent=state.persistent,
                path=state.path,
                failures=state.failures,
                last_error=state.last_error,
                last_failure_at=state.last_failure_at,
            ),
            activity_level=typing.cast(Any, live().activity_level),
            bounds=[
                ActivityBound(
                    level=typing.cast(Any, lvl),
                    retention_days=b.retention_days,
                    max_kept=b.max_kept,
                )
                for lvl, b in history.bounds.items()
            ],
            drops=[ActivityDrop(**row) for row in history.drops()],
            poll_seconds=ops.POLL_SECONDS["activity"],
        )

    @app.get("/activity/export")
    def export_activity(
        format: ActivityExportFormat = "text",  # noqa: A002 - the query parameter's name
        since: AwareInstant | None = None,
        until: AwareInstant | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        subject: str | None = None,
        outcome: ActivityOutcome | None = None,
        source: ActivitySource | None = None,
        level: ActivityLevel | None = None,
        limit: int = limits.row_limit(activity_log.EXPORT_DEFAULT, activity_log.EXPORT_MAX),
    ) -> Response:
        """The history as a file: JSON Lines, CSV, or a timeline a person
        reads (activity_export.render) - oldest first, under the same filters as
        GET /activity, at most activity_log.EXPORT_MAX events. Cut at the
        limit, it says so: a header line in text, and X-UQF-Truncated."""
        filters = dict(locals())
        # One event past the limit says whether older ones matched (#2527),
        # as on GET /activity.
        q = _activity_query(filters, level, limit + 1)
        rows = history.export(q)
        truncated = len(rows) > limit
        now = dt.datetime.now(dt.UTC)
        body = activity_export.render(
            rows[:limit],
            format,
            exported_at=now,
            filters=q.described(),
            drops=history.drops(),
            truncated=truncated,
        )
        name = f"uqf-activity-{now:%Y%m%dT%H%M%SZ}.{activity_log.EXTENSIONS[format]}"
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        if truncated:
            headers[activity_export.TRUNCATED_HEADER] = "true"
        return Response(content=body, media_type=activity_log.MEDIA_TYPES[format], headers=headers)
