"""Every `uqs deploy push` option reaches the Config it configures (#904).

An option lives in four places: the CLI parameter, the `Config` field,
`make_config`'s parameter, and the keyword `make_config` passes to
`Config(...)`. The first hand-off is checked - the CLI passes
`**dict(locals())`, so an unknown name is a TypeError - but the last is
not: leave `soak=soak` out of the `Config(...)` call and the field keeps its
default, so `--soak 45` is accepted, validated, and ignored. Every field
has a default, so no type checker sees it.

So each `make_config` parameter has a SAMPLE here - a valid value that is
not its default - and the test passes each one alone and requires the field
to change. A new option fails here until it has a sample, which is the
point: adding one means proving it arrives.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any

import pytest

from uqs.deploy.config import Config, make_config

REQUIRED: dict[str, Any] = {
    "artifact": "dist/uqf.tar.gz",
    "host": "uqf-server",
    "dest": "/opt/uqf",
    "profile": "fx",
}

#: A valid value for each parameter that differs from its default. The
#: required ones differ from REQUIRED's, so they are proven to arrive too.
SAMPLES: dict[str, Any] = {
    "artifact": "dist/other.tar.gz",
    "host": "other-server",
    "dest": "/srv/uqf",
    "profile": "essential",
    "remote_user": "uqf",
    "torq_home": "/opt/torq",
    "torq_app_home": "/opt/torqapp",
    "torq_launcher": "/opt/torq/torq.sh",
    "launcher_env": ["UQF_X=1"],
    "qcmd": "/opt/kx/bin/q",
    "qhome": "/opt/kx",
    "data_dir": "/data/uqf",
    "dry_run": True,
    "restart": True,
    "init_data": True,
    "jobs": "superbook",
    "live": True,
    "odbc_home": "/opt/odbc",
    "live_check": "databento",
    "live_check_timeout": 7,
    "connect_timeout": 7,
    "command_timeout": 7,
    "smoke_timeout": 7,
    "verify_timeout": 7,
    "allow_dirty": True,
    "release_repo": "owner/name",
    "fix_hdb": True,
    "keep": 3,
    "soak": 45,
    "break_lock": True,
}

#: Config fields `make_config` does not set, and who does.
SET_ELSEWHERE = {
    "target": "deploy/targets.py, from deploy_targets.toml (#873)",
    "sources": "deploy/targets.py: where each setting came from, for the plan",
    "runtime": "deploy/targets.py: the runtime a target expects",
}


def _params() -> list[str]:
    return list(inspect.signature(make_config).parameters)


def _fields() -> set[str]:
    return {f.name for f in dataclasses.fields(Config)}


def test_every_option_has_a_sample_and_every_sample_an_option():
    params = set(_params())
    assert not params - set(SAMPLES), (
        "make_config parameters with no SAMPLE - add one, which proves the option "
        f"reaches its Config field: {sorted(params - set(SAMPLES))}"
    )
    assert not set(SAMPLES) - params, f"SAMPLES for no parameter: {sorted(set(SAMPLES) - params)}"


def test_every_option_is_a_field_and_every_field_is_set_somewhere():
    params, fields = set(_params()), _fields()
    assert not params - fields, f"make_config parameters with no Config field: {params - fields}"
    unset = fields - params - set(SET_ELSEWHERE)
    assert not unset, f"Config fields nothing sets: {sorted(unset)}"
    assert not set(SET_ELSEWHERE) - fields, "SET_ELSEWHERE names a field that is gone"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_each_option_reaches_its_field(name):
    """make_config(..., name=sample) changes Config.name: the option was not
    dropped on the way from make_config's parameters to Config(...)."""
    baseline = make_config(**REQUIRED)
    changed = make_config(**{**REQUIRED, name: SAMPLES[name]})
    assert getattr(changed, name) != getattr(baseline, name), (
        f"{name}={SAMPLES[name]!r} was accepted but Config.{name} kept "
        f"{getattr(baseline, name)!r} - is it passed to Config(...)?"
    )
