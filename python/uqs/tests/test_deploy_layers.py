"""Layered deploy configuration (#1557, step 3): [defaults], then the
[environments.NAME] a target names, then the target, then the flags - each
layer held to the target's key checks, and each value's layer in the plan."""

from __future__ import annotations

import pytest

from uqs.deploy import history, plan, targets
from uqs.deploy.config import DeployError, make_config

LAYERED = """
[defaults]
remote_user = "svc"
profile = "essential"
verify_timeout = 300
torq_home = "/opt/torq"

[environments.uat]
profile = "fx"
verify_timeout = 400
labels = { tier = "uat" }

[targets.alpha]
environment = "uat"
host = "deploy@alpha"
dest = "/srv/uqf"
verify_timeout = 500

[targets.beta]
host = "deploy@beta"
dest = "/srv/uqf"
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    (tmp_path / targets.DECLARATION).write_text(LAYERED)
    return tmp_path


def _write(root, text):
    (root / targets.DECLARATION).write_text(text)


def test_each_layer_overrides_the_one_beneath_it(root):
    (cfg,) = targets.configs("alpha", "x", {"keep": 3}, False, root)
    assert (cfg.remote_user, cfg.torq_home) == ("svc", "/opt/torq")  # defaults
    assert cfg.profile == "fx"  # the environment over the defaults
    assert cfg.verify_timeout == 500  # the target over both
    assert cfg.sources == {
        "remote_user": "defaults",
        "torq_home": "defaults",
        "profile": "environment uat",
        "verify_timeout": "target alpha",
        "host": "target alpha",
        "dest": "target alpha",
        "keep": "--keep",
    }


def test_a_flag_wins_over_every_layer(root):
    explicit = {"profile": "rates", "verify_timeout": 60, "remote_user": "ops"}
    (cfg,) = targets.configs("alpha", "x", explicit, False, root)
    assert (cfg.profile, cfg.verify_timeout, cfg.remote_user) == ("rates", 60, "ops")
    assert {cfg.sources[k] for k in explicit} == {"--profile", "--verify-timeout", "--remote-user"}


def test_a_target_without_an_environment_takes_the_defaults(root):
    (cfg,) = targets.configs("beta", "x", {}, False, root)
    assert (cfg.profile, cfg.verify_timeout) == ("essential", 300)
    assert cfg.sources["profile"] == "defaults"


def test_layered_values_are_checked_by_make_config(root):
    _write(root, LAYERED.replace('torq_home = "/opt/torq"', 'torq_home = "opt/torq"'))
    with pytest.raises(DeployError, match="must be an absolute path"):
        targets.configs("beta", "x", {}, False, root)


def test_the_plan_names_each_layer_lowest_first(root):
    (cfg,) = targets.configs("alpha", "x", {"keep": 3}, True, root)
    assert plan._source_lines(cfg) == [
        "settings: remote_user, torq_home from defaults",
        "settings: profile from environment uat",
        "settings: dest, host, verify_timeout from target alpha",
        "settings: keep from --keep",
    ]


@pytest.mark.parametrize(
    ("table", "layer"),
    [("[defaults]", "defaults"), ("[environments.uat]", "environment uat"),
     ("[targets.alpha]", "target alpha")],
)  # fmt: skip
def test_an_unknown_key_is_refused_naming_its_layer(root, table, layer):
    _write(root, LAYERED.replace(table, f"{table}\nhots = 1"))
    with pytest.raises(DeployError, match=f"^{layer}: unknown setting.*hots"):
        targets.configs("alpha", "x", {}, False, root)


def test_an_unused_environments_unknown_key_is_refused_too(root):
    _write(root, LAYERED + '\n[environments.prod]\nprofil = "p"\n')
    with pytest.raises(DeployError, match="environment prod: unknown setting.*profil"):
        targets.configs("beta", "x", {}, False, root)


@pytest.mark.parametrize("table", ["[defaults]", "[environments.uat]", "[targets.alpha]"])
@pytest.mark.parametrize("key", ["artifact", "dry_run", "accept_digest", "allow_unpinned"])
def test_a_per_run_key_is_refused_in_every_layer(root, table, key):
    assert key in targets.PER_RUN
    _write(root, LAYERED.replace(table, f'{table}\n{key} = "x"'))
    with pytest.raises(DeployError, match=key):
        targets.configs("alpha", "x", {}, False, root)


def test_an_unknown_environment_is_refused_naming_those_declared(root):
    _write(root, LAYERED.replace('environment = "uat"', 'environment = "uta"'))
    with pytest.raises(DeployError, match="target alpha: environment 'uta' is not declared.*: uat"):
        targets.configs("alpha", "x", {}, False, root)


@pytest.mark.parametrize(
    ("table", "layer"),
    [("[defaults]", "defaults"), ("[environments.uat]", "environment uat")],
)
def test_only_a_target_names_an_environment(root, table, layer):
    _write(root, LAYERED.replace(table, f'{table}\nenvironment = "uat"'))
    with pytest.raises(DeployError, match=f"{layer}: sets environment - only a target"):
        targets.configs("beta", "x", {}, False, root)


@pytest.mark.parametrize(
    "text",
    ['[default]\nprofile = "p"\n', 'defaults = "p"\n', '[environments]\nuat = "p"\n'],
)
def test_a_misshapen_or_misspelt_table_is_refused(root, text):
    _write(root, text + '[targets.beta]\nhost = "h"\ndest = "/d"\nprofile = "p"\n')
    with pytest.raises(DeployError, match=r"declare targets as \[targets.<name>\] tables"):
        targets.configs("beta", "x", {}, False, root)


def test_a_file_without_the_new_tables_behaves_as_before(root):
    """A plain [targets.*] file: the same Config, every source the target."""
    _write(root, '[targets.a]\nhost = "h"\ndest = "/d"\nprofile = "p"\nruntime = "crypto"\n')
    (cfg,) = targets.configs("a", "x", {"profile": "q"}, False, root)
    assert cfg.sources == {"host": "target a", "dest": "target a", "profile": "--profile"}
    assert cfg.runtime == "crypto"
    want = make_config(artifact="x", host="h", dest="/d", profile="q")
    assert (cfg.host, cfg.dest, cfg.profile) == (want.host, want.dest, want.profile)
    assert targets.read(root) == {
        "a": {"host": "h", "dest": "/d", "profile": "p", "runtime": "crypto"}
    }
    assert list(history.target_rows(root)[0]) == ["target", "ssh", "dest", "as", "labels",
                                                  "last deployed"]  # fmt: skip


def test_runtime_and_labels_layer_like_any_key(root):
    _write(root, '[defaults]\nruntime = "crypto"\n' + LAYERED.replace("[defaults]\n", ""))
    (cfg,) = targets.configs("alpha", "x", {}, False, root)
    assert cfg.runtime == "crypto"
    assert targets.read(root)["alpha"]["labels"] == {"tier": "uat"}


def test_list_targets_shows_the_merged_target_and_its_environment(root):
    rows = {r["target"]: r for r in history.target_rows(root)}
    assert rows["alpha"]["environment"] == "uat" and rows["beta"]["environment"] == ""
    assert rows["alpha"]["as"] == "svc", "the defaults' remote_user"
    assert rows["alpha"]["labels"] == "tier=uat", "the environment's labels"


def test_target_add_and_remove_keep_the_shared_layers(root):
    targets.append(root, "gamma", {"host": "deploy@gamma", "dest": "/srv/g"})
    (cfg,) = targets.configs("gamma", "x", {}, False, root)
    assert (cfg.profile, cfg.remote_user) == ("essential", "svc")
    targets.remove(root, "alpha")
    assert sorted(targets.read(root)) == ["beta", "gamma"]
    assert targets.configs("beta", "x", {}, False, root)[0].profile == "essential"


def test_a_file_with_only_shared_layers_takes_its_first_target(root):
    _write(root, '[defaults]\nprofile = "p"\n')
    assert targets.read(root) == {}
    targets.append(root, "a", {"host": "h", "dest": "/d"})
    assert targets.configs("a", "x", {}, False, root)[0].profile == "p"
