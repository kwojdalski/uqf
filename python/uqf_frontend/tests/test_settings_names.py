"""The frontend's settings say one thing one way (#2111).

The activity history and the config-push history are both bounded by age and
by count, and nothing about them differs in a way their names should express:
each says `retention_days` for how long and `max_kept` for how many - in its
environment variables, its runtime settings, its responses and its removal
reasons. `max_rows` is the per-request query cap and nothing else. A variable
holding a duration names its unit (`scripts/gates/check.py env-reference`
holds the rest of the tree to that).

A name renamed here is refused by name - as an environment variable and as a
settings-file key - never ignored: ignored, the default stays in force while
the operator believes their own value is.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

import pytest

from uqf_frontend import app as app_module
from uqf_frontend import config, models
from uqf_frontend.config import RENAMED_SETTINGS_KEYS, Settings
from uqf_frontend.models import (
    ActivityBound,
    ConfigPushOpenResponse,
    PrunedPushOut,
    RuntimeSettingsBody,
)
from uqs import activity_log
from uqs.renamed_env import RENAMED_ENV

#: Each retention-bounded history's settings prefix.
HISTORIES = ("activity", "activity_interactions", "activity_debug", "config_history")
#: The two bounds every history has, in every place it is named.
BOUNDS = ("retention_days", "max_kept")

RENAMED_FRONTEND_ENV = {k: v for k, v in RENAMED_ENV.items() if k.startswith("UQF_FRONTEND_")}


def test_every_history_is_bounded_by_the_same_two_runtime_settings():
    fields = set(RuntimeSettingsBody.model_fields)
    bounds = {f for f in fields if f != "activity_level"}
    assert bounds == {f"{h}_{b}" for h in HISTORIES for b in BOUNDS}


def test_every_history_bound_is_read_from_the_environment_by_its_own_name(monkeypatch):
    expected = {}
    for i, history in enumerate(HISTORIES):
        for bound in BOUNDS:
            value = 10 + i * 2 + BOUNDS.index(bound)
            monkeypatch.setenv(f"UQF_FRONTEND_{history.upper()}_{bound.upper()}", str(value))
            expected[f"{history}_{bound}"] = value
    s = Settings.from_env()
    assert {name: getattr(s, name) for name in expected} == expected


def test_both_histories_answer_their_bounds_in_the_same_words():
    for model in (ActivityBound, ConfigPushOpenResponse):
        assert set(BOUNDS) <= set(model.model_fields), model.__name__
        assert "max_rows" not in model.model_fields and "keep" not in model.model_fields


def test_a_pruned_push_gives_the_activity_historys_reasons():
    reasons = set(typing.get_args(PrunedPushOut.model_fields["why"].annotation))
    assert reasons == {"row_cap", "retention"}
    assert reasons <= set(activity_log.DROP_REASONS)


def test_the_duration_variables_name_their_unit(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("UQF_FRONTEND_CAPTURE_INTERVAL_SECONDS", "34")
    s = Settings.from_env()
    assert (s.timeout, s.capture_interval) == (12, 34)


@pytest.mark.parametrize("old", sorted(RENAMED_FRONTEND_ENV))
def test_a_renamed_variable_is_refused_naming_its_new_name(monkeypatch, old):
    monkeypatch.setenv(old, "1")
    with pytest.raises(ValueError) as raised:
        Settings.from_env()
    assert f"{old} is no longer read - {RENAMED_FRONTEND_ENV[old]}" in str(raised.value)


@pytest.mark.parametrize(
    "old", ["UQF_FRONTEND_CONFIG_HISTORY_KEEP", "UQF_FRONTEND_CONFIG_HISTORY_DAYS"]
)
def test_the_history_bound_uqs_restores_with_refuses_a_renamed_variable(monkeypatch, old):
    monkeypatch.setenv(old, "1")
    with pytest.raises(ValueError, match=f"{old} is no longer read"):
        config.history_bound()


def test_each_renamed_variable_names_one_the_frontend_reads():
    """The migration message must point somewhere real."""
    for old, hint in RENAMED_FRONTEND_ENV.items():
        new = hint.split()[1]
        assert new != old and new.startswith("UQF_FRONTEND_"), hint


def test_each_renamed_settings_key_names_a_runtime_setting():
    fields = set(RuntimeSettingsBody.model_fields)
    for old, new in RENAMED_SETTINGS_KEYS.items():
        assert old not in fields and new in fields, (old, new)


@pytest.mark.parametrize("old", sorted(RENAMED_SETTINGS_KEYS))
def test_a_settings_file_written_before_the_rename_is_refused_naming_the_new_key(tmp_path, old):
    """Never skipped and never the bare `extra_forbidden` a validator would
    give: the operator is told which key it is now."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"activity_level": "actions", old: 5}), encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        config.runtime_file_values(path)
    said = str(raised.value)
    assert f"`{old}` is now `{RENAMED_SETTINGS_KEYS[old]}`" in said
    assert str(path) in said


def test_the_api_refuses_to_start_on_a_settings_file_written_before_the_rename(tmp_path):
    from uqf_frontend.app import create_app
    from uqf_frontend.fleet import FakeFleet
    from uqf_frontend.gateway import FakeGateway

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"config_history_days": 30}), encoding="utf-8")
    with pytest.raises(ValueError, match="`config_history_days` is now"):
        create_app(
            gateway=FakeGateway({}),
            fleet=FakeFleet(),
            settings=Settings(settings_file=path, activity_db=tmp_path / "a.sqlite3"),
        )


def test_a_response_serving_the_default_activity_level_names_it_activity_level():
    """The setting is `activity_level` in Settings, its environment variable,
    /health and /settings; /activity once served the same value as
    `default_level` (#2240). Every response field set from
    `live().activity_level` must carry the setting's own name."""
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    named = re.findall(r"(\w+)=typing\.cast\(Any, live\(\)\.activity_level\)", source)
    assert named, "no response serves the activity level any more - update this gate"
    assert set(named) == {"activity_level"}
    for model in (models.ActivityResponse, models.HealthResponse):
        fields = model.model_fields
        assert "activity_level" in fields and "default_level" not in fields, model.__name__
