"""/control/*: the routes that change something (writes)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from uqf_frontend.instants import AwareInstant
from uqf_frontend.models.base import STRICT_REQUEST, ConfirmedRequest, refuse_unconfirmed


class ControlProcessOut(BaseModel):
    """One process a lifecycle selector may name."""

    process: str
    proctype: str
    start_with_all: bool = Field(
        description='whether torq.sh\'s "all" selector includes it - the effective value, '
        "with process_overrides.csv applied"
    )


class ControlStatusResponse(BaseModel):
    """Whether the control surface is live, and what it accepts.

    A UI needs this to decide whether to render controls at all. Discovering
    it by provoking a 403 on a real action is a poor way to find out, and on
    a lifecycle route the "real action" would have stopped the fleet.
    """

    writes_enabled: bool
    lifecycle_actions: list[str]
    settable_fields: list[str]
    processes: list[ControlProcessOut] = Field(
        default_factory=list,
        description="what a lifecycle selector may name, from the effective process.csv; "
        "empty while writes are off, like settable_fields",
    )
    poll_seconds: int


#: The lifecycle verbs `/control/lifecycle/{action}` takes (#2230). The path
#: takes a str and control.lifecycle refuses any other verb 404 (#2224), so
#: an unknown verb is a name that is not there. `clean` is left out on
#: purpose - see control.py's docstring.
LifecycleAction = Literal["start", "stop", "restart"]


class LifecycleRequest(BaseModel):
    """Start, stop or restart processes.

    `procs` is torq.sh's own selector - a name, several separated by spaces,
    or "all" - and is passed through rather than reinterpreted, so the HTTP
    surface and the CLI cannot disagree about what "all" means.

    Required, with no default (#1361): it defaulted to "all", so a body that
    lost the field - renamed on the browser's side - made Stop stop the whole
    fleet. A destructive action is never what silence means.
    """

    model_config = STRICT_REQUEST

    procs: str = Field(min_length=1)
    #: Required true for the verbs in CONFIRMED_LIFECYCLE (#2244), which the
    #: route holds to ConfirmedLifecycleRequest; `start` needs none, and one
    #: sent is held to the same rule.
    confirmed: bool | None = Field(default=None, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _confirmed_if_sent(cls, data: Any) -> Any:
        return refuse_unconfirmed(data, required=False)


#: The lifecycle verbs that take something down: the UI asks before each, so
#: the API requires `confirmed: true` for them (the confirmation rule).
CONFIRMED_LIFECYCLE: frozenset[str] = frozenset({"stop", "restart"})


class ConfirmedLifecycleRequest(ConfirmedRequest, LifecycleRequest):
    """A stop or a restart: LifecycleRequest, held to the confirmation rule."""

    model_config = STRICT_REQUEST


class CommandResponse(BaseModel):
    """What a subprocess-backed control action did.

    Carries the exit code rather than only a boolean: torq.sh distinguishes
    "nothing to do" from "failed", and collapsing that to ok/not-ok loses the
    distinction an operator acts on.
    """

    action: str
    #: torq.sh's selector, as LifecycleRequest sent it. Not `target`, which is
    #: a deployment target everywhere (#2234): control acts on `local` only.
    procs: str
    exit_code: int
    ok: bool
    output: str


class ProcessConfigRequest(BaseModel):
    """One process.csv field override.

    `field` because it names a COLUMN of process.csv, one of
    ControlStatusResponse.settable_fields; WorkerConfigRequest's `key` names a
    `.qetl.cfg` KEY in a live process. Two stores, two words (#2227).
    """

    model_config = STRICT_REQUEST

    field: str = Field(min_length=1, description="a process.csv column")
    value: str = Field(description='the new value; "" is legal and clears nothing')

    @model_validator(mode="before")
    @classmethod
    def _renamed(cls, data: Any) -> Any:
        """One wire name: the model once took its attribute name as well (#2227)."""
        if isinstance(data, dict) and "field_name" in data:
            raise ValueError("`field_name` is `field`: the process.csv column to set (#2227)")
        return data


class ProcessConfigResponse(BaseModel):
    """The effective row after the write.

    The row rather than an acknowledgement: what the process will start with
    is the interesting part, and a caller who must issue a second request to
    learn it will sometimes not bother.
    """

    process: str
    config: dict[str, str]
    settable_fields: list[str]


class WorkerConfigRequest(BaseModel):
    """One `.qetl.cfg` override, set in a live process. `key` is a
    `.qetl.cfg` key, not a process.csv column - ProcessConfigRequest's `field`."""

    model_config = STRICT_REQUEST

    key: str = Field(min_length=1)
    value: str


class WorkerConfigResponse(BaseModel):
    """Where the value now comes from.

    `explain` names the LAYER that answered, which is not always the override
    just written - a key also set in the environment reads from there. A
    caller told only "ok" would believe a value that is not in effect.
    """

    key: str
    value: str
    explain: Any
    #: The layer now answering for the key, from `explain`: `overrides` when
    #: the value just set is in effect, None when the answer had an unknown shape.
    effective_layer: str | None
    #: The raw value that layer holds - the one the process now reads.
    effective_value: str | None
    #: True when an environment variable outranks the override just set, so
    #: the override has no effect until that variable is unset (#634).
    shadowed: bool
    #: True when the override layer is what the process now reads. The server
    #: says so because the layer's name is q's (`.qetl.cfg.override_layer`),
    #: not something the browser should retype.
    saved: bool
    #: The variable that wins, e.g. UQF_DRY_RUN, when shadowed.
    env_var: str | None
    note: str = (
        "this override lives in the process's memory and is lost when it restarts; "
        "a process.csv override survives"
    )


class BackfillRequest(BaseModel):
    """Run a bounded worker over a range.

    Every field is required and none has a default. The explicit-range rule at the HTTP
    surface: a backfill that guessed a range would publish the wrong window
    and record it as covered.
    """

    model_config = STRICT_REQUEST

    worker: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    range_from: AwareInstant = Field(description="ISO-8601 with an explicit offset")
    range_to: AwareInstant = Field(description="ISO-8601 with an explicit offset, exclusive")


class BackfillStartedResponse(BaseModel):
    """Launched, not finished.

    The run is detached, so this says what was started and where to watch it
    rather than whether it worked - `/ops/backfill` reads the status file the
    worker writes on every transition.
    """

    worker: str
    source_version: str
    range_from: AwareInstant
    range_to: AwareInstant
    #: The TorQ process running it - what `uqs logs` takes.
    process: str
    status_path: str
