"""The API names a deployment target's fields as uqs does (#2107).

`GET /services` lists targets "as `uqs target list --json` reads them", and
every route that acts on a target sits beside it. They used to rename the
registry: `target` became `name`, the ssh destination `host` - the ambiguity
#1888 removed from the CLI, since `host` is a q host everywhere else - and the
deployment root `root`, next to `destination`, a directory under it. And the
writes switch had two names.

The names are not written down here. They are read from uqs itself - its
`Config` fields and `target_keys.RENAMED`, the keys it refuses - so the day
uqs renames one, this test names every model that still says the old word.
`api.ts` is held equal to the models by test_api_types.py, so the browser is
covered too.
"""

from __future__ import annotations

import dataclasses

from api_support import defined_models
from pydantic import BaseModel

from uqf_frontend import services
from uqf_frontend.config import Settings
from uqf_frontend.models.services import ServiceTargetOut
from uqs.deploy import config, history, inspection, target_keys

#: The Config fields that say which target, where it is, and where on it:
#: the three a model describing a target carries.
CONFIG_FIELDS = {f.name for f in dataclasses.fields(config.Config)}
IDENTITY = ("target", "ssh", "dest")

#: The frontend's own former spellings of a Config field, refused in a model
#: that describes a target: the target's name, and its deployment root.
#: uqs's own former spellings (`host` for `ssh`, ...) come from RENAMED.
FORMER = {"name": "target", "root": "dest", **target_keys.RENAMED}

#: The one name of the switch that lets the API reach a server, and every
#: name it has had: UQF_FRONTEND_ENABLE_WRITES, Settings.enable_writes.
WRITES = "writes_enabled"
WRITES_FORMER = ("remote_operations_enabled", "enable_writes", "remote_operations")


def _models() -> list[type[BaseModel]]:
    return [m for m in defined_models().values() if issubclass(m, BaseModel)]


def _describes_a_target(model: type[BaseModel]) -> bool:
    return any(name in model.model_fields for name in IDENTITY)


def test_the_identity_fields_are_uqs_config_fields():
    assert set(IDENTITY) <= CONFIG_FIELDS
    assert set(FORMER.values()) <= CONFIG_FIELDS


def test_every_model_describing_a_target_uses_uqs_names():
    wrong = {
        f"{m.__name__}.{name}": f"uqs calls it `{FORMER[name]}`"
        for m in _models()
        if _describes_a_target(m)
        for name in m.model_fields
        if name in FORMER
    }
    assert not wrong, wrong
    # the gate is not vacuous: these are the models it holds
    held = {m.__name__ for m in _models() if _describes_a_target(m)}
    assert {"ServiceTargetOut", "WorkspaceOut", "WorkspaceResponse", "RemoteFilesResponse",
            "ConfigPushResponse", "TunnelPlanResponse"} <= held  # fmt: skip


def test_no_field_beside_dest_reads_as_dest():
    """`destination` beside `dest` was the trap: one is the deployment root,
    the other a directory under it."""
    near = {
        f"{m.__name__}.{name}"
        for m in _models()
        for name in m.model_fields
        if name != "dest" and name.startswith("dest")
    }
    assert not near, near


def test_services_rows_are_target_list_rows_key_for_key(tmp_path, monkeypatch):
    """A /services row is a `uqs target list --json` row: the same keys, every
    optional column included, and the same value under each."""
    path = tmp_path / "deploy_targets.toml"
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    path.write_text(
        '[environments.uat]\nremote_user = "svc"\n\n'
        '[targets.prod-a]\nssh = "deploy@prod-a"\ndest = "/srv/uqf"\n'
        f"{inspection.KEY} = true\n\n"
        '[targets.uat-b]\nssh = "deploy@uat-b"\ndest = "/srv/uqf-uat"\nenvironment = "uat"\n'
    )
    uqs_rows = history.target_rows(tmp_path)
    ours = services.read(Settings(stack_root=tmp_path)).targets
    assert [set(r) for r in ours] == [set(r) for r in uqs_rows]
    assert {"environment", "inspection"} <= set(uqs_rows[0])  # every column is shown
    assert set(ServiceTargetOut.model_fields) == set(uqs_rows[0])
    for row, theirs in zip(ours, uqs_rows, strict=True):
        assert row["target"] == theirs["target"]
        assert (row["ssh"], row["dest"]) == (theirs["ssh"], theirs["dest"])


def test_the_writes_switch_has_one_name():
    named = {
        f"{m.__name__}.{name}"
        for m in _models()
        for name in m.model_fields
        if name == WRITES or name in WRITES_FORMER
    }
    assert {n.split(".")[1] for n in named} == {WRITES}, named
    assert {n.split(".")[0] for n in named} >= {
        "ControlStatusResponse", "ServicesResponse", "SettingsResponse",
    }  # fmt: skip
