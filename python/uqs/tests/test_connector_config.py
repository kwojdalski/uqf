"""Who owns a connector's configuration (#1786): the shared tickerplant
connection is written once (external/connection.py), each setting resolves in
one documented order, a credential is never printed, and a binding q and
Python must agree on is derived from q or refused when it does not match.

No real credential, broker, tickerplant or network: every start here records
the command line it would have run.
"""

from __future__ import annotations

import ast
import re
import types
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import external as cli_external
from uqs.cli import renames
from uqs.external import connection, crypto, feeds, kafka_feed
from uqs.paths import UqsError, UqsPaths
from uqs.scaffold import external as scaffold_external

REPO = Path(__file__).resolve().parents[3]
BUNDLE_CONNECTORS = (
    REPO / "sidecars" / "databento" / "databento_feed.py",
    REPO / "sidecars" / "flink" / "flink_vwap_feed.py",
)
#: Not a real secret: a value no output may contain.
SECRET = "ops:not-for-printing-7f3a"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        connection.TP_CREDENTIAL_ENV,
        kafka_feed.BROKERS_ENV,
        kafka_feed.TOPICS_ENV,
        "DATABENTO_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _paths(tmp_path: Path, repo_root: Path = REPO) -> UqsPaths:
    return UqsPaths(
        repo_root=repo_root,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "orch",
    )


def _bundle_feed(path: Path) -> feeds.ExternalFeed:
    """A sidecar connector, imported as discovery imports an installed one."""
    module = feeds._load(f"uqs_bundle_test_{path.stem}", path)
    return module.FEED


# ---------------------------------------------------------------- precedence


def test_the_credential_is_explicit_then_environment_then_the_demo_default():
    env = {connection.TP_CREDENTIAL_ENV: "env:pw"}
    assert connection.tp_credential("given:pw", env) == "given:pw", "explicit beats env"
    assert connection.tp_credential(None, env) == "env:pw", "env beats the default"
    assert connection.tp_credential(None, {}) == connection.DEMO_TP_CREDENTIAL
    assert connection.tp_credential(None, {connection.TP_CREDENTIAL_ENV: "  "}) == (
        connection.DEMO_TP_CREDENTIAL
    ), "a blank variable is unset"


def test_an_option_resolves_given_then_its_variable_then_its_default():
    option = feeds.FeedOption("depth", "Levels", type=int, default=5, env="UQF_TEST_DEPTH")
    assert option.value(None, {}) == 5 and option.origin(None, {}) == "default"
    assert option.value(None, {"UQF_TEST_DEPTH": "9"}) == 9
    assert option.origin(None, {"UQF_TEST_DEPTH": "9"}) == "$UQF_TEST_DEPTH"
    assert option.value(0, {"UQF_TEST_DEPTH": "9"}) == 0, "given - even 0 - beats the variable"
    assert option.origin(0, {}) == "--depth"
    with pytest.raises(UqsError, match=r"--depth: \$UQF_TEST_DEPTH is not an integer"):
        option.value(None, {"UQF_TEST_DEPTH": "deep"})
    many = feeds.FeedOption("names", "N", many=True, default=("a",), env="UQF_TEST_NAMES")
    assert many.value(None, {"UQF_TEST_NAMES": "x, y"}) == ("x", "y")


def test_one_cli_option_two_feeds_read_from_different_variables_is_refused():
    def feed(name: str, env: str) -> feeds.ExternalFeed:
        option = feeds.FeedOption("brokers", "B", env=env)
        return feeds.ExternalFeed(
            name, lambda p, o: 1, lambda p: None, lambda p: {}, options=(option,)
        )

    with pytest.raises(
        UqsError, match=r"--brokers is declared by feeds a and b with different var"
    ):
        feeds.check_options({"a": feed("a", "UQF_A"), "b": feed("b", "UQF_B")})


def test_a_malformed_credential_is_refused_naming_where_it_came_from_not_its_value():
    env = {connection.TP_CREDENTIAL_ENV: "no-colon-secret"}
    with pytest.raises(UqsError) as exc:
        connection.tp_credential(None, env)
    assert "$UQF_TP_CREDENTIAL" in str(exc.value) and "no-colon-secret" not in str(exc.value)
    with pytest.raises(UqsError, match="from --tp-credential") as exc:
        connection._check_credential(":pw-only", "--tp-credential")
    assert "pw-only" not in str(exc.value)


# ------------------------------------------- one home for the shared defaults


def _string_constants(path: Path) -> list[str]:
    """Every string the code uses - not its docstrings, which may name a file."""
    tree = ast.parse(path.read_text())
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
    ]


def _connector_sources() -> list[Path]:
    own = sorted((REPO / "python/uqs/src/uqs/external").rglob("*.py"))
    return [p for p in own if p.name != "connection.py"] + list(BUNDLE_CONNECTORS)


def test_the_demo_credential_is_written_once():
    """Not in each connector, each bundle and the scaffold template, as it
    was: one labelled default, which every one of them imports."""
    sources = [*_connector_sources(), Path(scaffold_external.__file__)]
    holders = [p for p in sources if connection.DEMO_TP_CREDENTIAL in _string_constants(p)]
    assert holders == [], f"repeats the demo credential: {holders}"


def test_no_connector_reads_a_vendored_password_file():
    """A sample password file is not a deployment's credential (#1786)."""
    pattern = re.compile(r"passwords[/\\]|feed\.txt|accesslist")
    sources = [*_connector_sources(), REPO / "python/uqs/src/uqs/external/connection.py"]
    for path in sources:
        for text in _string_constants(path):
            assert not pattern.search(text), f"{path.name} names a password file: {text!r}"


def test_every_connector_declares_the_shared_credential_option():
    found = {**feeds.discover(), **{f.name: f for f in map(_bundle_feed, BUNDLE_CONNECTORS)}}
    assert {"kafka", "crypto", "crypto-fills", "databento", "flink_vwap"} <= set(found)
    for name, feed in found.items():
        assert connection.TP_CREDENTIAL in feed.options, f"{name} has no --tp-credential"
    feeds.check_options(found)  # one option, one type, one variable


def test_a_scaffolded_connector_takes_the_shared_credential_too(tmp_path):
    module = types.ModuleType("uqs.external.zz_feed")
    source = scaffold_external._feed("zz", "zz_raw", "zz_out")
    exec(compile(source, "zz_feed.py", "exec"), module.__dict__)  # noqa: S102 - our own template
    assert connection.TP_CREDENTIAL in module.FEED.options
    assert connection.DEMO_TP_CREDENTIAL not in source


# --------------------------------------------- every start presents the result


@pytest.fixture
def spawned(monkeypatch):
    """The command lines DetachedProcess would have run, each with the
    environment it would have run in."""
    commands: list[tuple[list[str], dict]] = []

    def start(self, cmd, **kw):
        commands.append((list(cmd), dict(kw.get("env") or {})))
        return 4242

    from uqs.external import lifecycle

    monkeypatch.setattr(lifecycle.DetachedProcess, "start", start)
    monkeypatch.setattr(
        connection, "get_process_config", lambda p, n, base_port=None: {"port": "6051"}
    )
    return commands


def _credential_in(spawn: tuple[list[str], dict]) -> str:
    """The credential a start hands its process: in its environment, and never
    in its argv, which ps and the pid file show (#1853)."""
    cmd, env = spawn
    credential = env[connection.TP_CREDENTIAL_ENV]
    assert "--credential" not in cmd and not any(credential in arg for arg in cmd), cmd
    return credential


@pytest.mark.parametrize(
    ("given", "env", "expected"),
    [
        (None, None, connection.DEMO_TP_CREDENTIAL),
        (None, "env:pw", "env:pw"),
        ("given:pw", "env:pw", "given:pw"),
    ],
)
def test_the_kafka_feed_presents_the_resolved_credential(
    tmp_path, monkeypatch, spawned, given, env, expected
):
    if env:
        monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, env)
    monkeypatch.setattr(kafka_feed.kafka_extra, "require", lambda: None)
    kafka_feed.start_kafka_feed(_paths(tmp_path), credential=given)
    (spawn,) = spawned
    assert _credential_in(spawn) == expected
    cmd = spawn[0]
    assert cmd[cmd.index("--host") + 1] == connection.TP_HOST
    assert cmd[cmd.index("--port") + 1] == "6051"


def test_the_bundle_connectors_present_the_resolved_credential(tmp_path, monkeypatch, spawned):
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, "env:pw")
    flink = _bundle_feed(BUNDLE_CONNECTORS[1])
    flink.start(_paths(tmp_path), flink.start_options({}))
    databento = _bundle_feed(BUNDLE_CONNECTORS[0])
    options = databento.start_options({"api_key": "k", "tp_credential": "given:pw"})
    databento.start(_paths(tmp_path), options)
    assert [_credential_in(c) for c in spawned] == ["env:pw", "given:pw"]


@pytest.fixture
def stp1_ports(monkeypatch) -> list[int | None]:
    """The base port each tickerplant_port lookup was asked for."""
    asked: list[int | None] = []

    def config(paths, name, base_port=None):
        asked.append(base_port)
        return {"port": str((base_port or 6050) + 1)}

    monkeypatch.setattr(connection, "get_process_config", config)
    return asked


def test_every_connector_reaches_a_stack_on_another_base_port(
    tmp_path, monkeypatch, spawned, stp1_ports
):
    """#2631: only crypto took --base-port; kafka, databento, flink and every
    scaffolded connector published to the runtime's default stp1 port."""
    monkeypatch.setattr(kafka_feed.kafka_extra, "require", lambda: None)
    kafka_feed.FEED.start(_paths(tmp_path), kafka_feed.FEED.start_options({"base_port": 7000}))
    flink = _bundle_feed(BUNDLE_CONNECTORS[1])
    flink.start(_paths(tmp_path), flink.start_options({"base_port": 7000}))
    databento = _bundle_feed(BUNDLE_CONNECTORS[0])
    databento.start(_paths(tmp_path), databento.start_options({"api_key": "k", "base_port": 7000}))
    assert stp1_ports == [7000, 7000, 7000]
    assert all(cmd[cmd.index("--port") + 1] == "7001" for cmd, _ in spawned)


def test_every_connector_declares_the_shared_base_port_option():
    declared = [kafka_feed.FEED, *(_bundle_feed(p) for p in BUNDLE_CONNECTORS)]
    declared += [f for f in feeds.discover().values() if f.name.startswith("crypto")]
    assert all(connection.BASE_PORT in f.options for f in declared), [
        f.name for f in declared if connection.BASE_PORT not in f.options
    ]
    template = scaffold_external._feed("demo", "demo_raw", "demo")
    assert "connection.BASE_PORT" in template
    assert 'tickerplant_port(paths, options["base_port"])' in template


def test_connector_start_kafka_takes_base_port(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(kafka_feed, "start_kafka_feed", lambda paths, **kw: seen.update(kw) or 7)
    argv = ["connector", "start", "kafka", "--topics", "uqf.client.flow", "--base-port", "7000"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert seen["base_port"] == 7000


def test_the_crypto_recorder_config_holds_the_credential_owner_only(tmp_path, monkeypatch):
    root = tmp_path / "cryptorust"
    root.mkdir()
    (root / "Cargo.toml").write_text("[package]\n")
    monkeypatch.setenv(crypto.CRYPTORUST_ROOT_ENV, str(root))
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, "env:pw")
    monkeypatch.setattr(
        connection, "get_process_config", lambda p, n, base_port=None: {"port": "6051"}
    )
    monkeypatch.setattr(
        crypto.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="x")
    )
    paths = _paths(tmp_path, repo_root=tmp_path)
    with pytest.raises(UqsError, match="cargo build failed"):
        crypto.start_crypto_recorder(paths)  # stops after writing the config
    config = paths.crypto_recorder_config_path
    assert 'credential: "env:pw"' in config.read_text()
    assert config.stat().st_mode & 0o077 == 0, "only its owner may read the credential"


# ------------------------------------------------------- masked where printed


def test_the_dry_run_masks_the_credential_and_names_its_source(monkeypatch):
    monkeypatch.setattr(kafka_feed, "kafka_feed_status", lambda p: {"running": "False"})
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, SECRET)
    monkeypatch.setenv(kafka_feed.BROKERS_ENV, "env-broker:9092")
    result = runner.invoke(cli.app, ["connector", "start", "kafka", "-n"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.stdout.split())
    assert "brokers=env-broker:9092 ($UQF_KAFKA_BROKERS)" in out
    assert "tp_credential=*** ($UQF_TP_CREDENTIAL)" in out
    assert SECRET not in result.output
    monkeypatch.setenv("OPS_TP", SECRET)
    named = runner.invoke(
        cli.app, ["connector", "start", "crypto", "-n", "--tp-credential-env", "OPS_TP"]
    )
    assert named.exit_code == 0, named.output
    assert "tp_credential=*** ($OPS_TP)" in " ".join(named.stdout.split())
    assert SECRET not in named.output


# ------------------------------------------- never on a command line (#1853)


def test_the_credential_is_taken_by_the_name_of_its_variable(monkeypatch):
    """`--tp-credential-env NAME` in --help, the inline flag hidden."""
    import typer

    root: Any = typer.main.get_command(cli.app)
    start = root.commands["connector"].commands["start"]
    shown = {o: p.hidden for p in start.params for o in p.opts}
    assert shown["--tp-credential-env"] is False and shown["--tp-credential"] is True
    monkeypatch.setenv("OPS_TP", "ops:pw")
    given, origins = cli_external._secrets_from_env(
        {"tp_credential": None, "tp_credential_env": "OPS_TP", "group": "g"}
    )
    assert given == {"tp_credential": "ops:pw", "group": "g"}
    assert origins == {"tp_credential": "$OPS_TP"}


def _recorded_refusals(monkeypatch) -> list[str]:
    """`_die`'s messages, recorded; like `_die` it never returns."""
    errors: list[str] = []

    def record(exc: Exception) -> None:
        errors.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(cli_external, "_die", record)
    return errors


def test_a_credential_variable_that_is_unset_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("OPS_TP", raising=False)
    errors = _recorded_refusals(monkeypatch)
    with pytest.raises(SystemExit):
        cli_external._start("crypto", {"tp_credential_env": "OPS_TP"}, dry_run=True)
    assert errors == ["--tp-credential-env OPS_TP: $OPS_TP is not set"]


def test_a_bad_credential_in_the_named_variable_names_the_variable(monkeypatch):
    errors = _recorded_refusals(monkeypatch)
    monkeypatch.setenv("OPS_TP", "nocolon-secret")
    with pytest.raises(SystemExit):
        cli_external._start("crypto", {"tp_credential_env": "OPS_TP"}, dry_run=True)
    assert errors and "from $OPS_TP" in errors[0] and "nocolon-secret" not in errors[0]


def test_the_inline_credential_still_works_for_one_release_and_warns(monkeypatch):
    warned: list[tuple[str, str]] = []
    monkeypatch.setattr(renames, "warn_secret_argv", lambda old, new: warned.append((old, new)))
    given = runner.invoke(
        cli.app, ["connector", "start", "crypto", "-n", "--tp-credential", SECRET]
    )
    assert given.exit_code == 0, given.output
    assert "tp_credential=***" in given.stdout and SECRET not in given.output
    assert warned == [("--tp-credential", "--tp-credential-env NAME")]


def test_the_inline_and_named_credential_together_are_refused(monkeypatch):
    monkeypatch.setenv("OPS_TP", "ops:pw")
    errors = _recorded_refusals(monkeypatch)
    with pytest.raises(SystemExit):
        cli_external._start(
            "crypto", {"tp_credential": SECRET, "tp_credential_env": "OPS_TP"}, dry_run=True
        )
    assert errors == ["give --tp-credential-env or --tp-credential, not both"]


def test_credential_from_env_reads_what_credential_env_wrote():
    env = connection.credential_env("ops:pw", {"PATH": "/bin"})
    assert env == {"PATH": "/bin", connection.TP_CREDENTIAL_ENV: "ops:pw"}
    assert connection.credential_from_env(env) == ("ops", "pw")
    with pytest.raises(UqsError, match=r"\$UQF_TP_CREDENTIAL") as exc:
        connection.credential_from_env({connection.TP_CREDENTIAL_ENV: "secret-no-colon"})
    assert "secret-no-colon" not in str(exc.value)


def test_no_streamer_takes_the_credential_as_an_argument():
    """Every streamer reads it from its environment: the launcher's argv is
    in ps and in the pid file (lifecycle.process_identity)."""
    streamers = [
        REPO / "python/uqs/src/uqs/external/kafka_streamer.py",
        REPO / "sidecars/databento/databento_streamer.py",
        REPO / "sidecars/flink/flink_vwap_streamer.py",
    ]
    texts = [p.read_text() for p in streamers]
    texts.append(scaffold_external._streamer("zz", "zz_raw", ["a"]))
    for text in texts:
        assert '"--credential"' not in text and "credential_from_env" in text


def test_the_fills_recorder_reads_its_credential_from_an_owner_only_file(tmp_path, monkeypatch):
    root = tmp_path / "cryptorust"
    root.mkdir()
    (root / "Cargo.toml").write_text("[package]\n")
    monkeypatch.setenv(crypto.CRYPTORUST_ROOT_ENV, str(root))
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, SECRET)
    monkeypatch.setattr(
        connection, "get_process_config", lambda p, n, base_port=None: {"port": "6051"}
    )
    monkeypatch.setattr(
        crypto.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stderr="")
    )
    started: list[list[str]] = []
    from uqs.external import lifecycle

    monkeypatch.setattr(
        lifecycle.DetachedProcess, "start", lambda self, cmd, **kw: started.append(cmd) or 4242
    )
    paths = _paths(tmp_path, repo_root=tmp_path)
    crypto.start_crypto_fills_recorder(paths)
    (cmd,) = started
    assert not any(SECRET in arg for arg in cmd) and "--kdb-credential" not in cmd
    config = paths.crypto_fills_recorder_config_path
    assert cmd[cmd.index("--config") + 1] == str(config)
    assert f'credential: "{SECRET}"' in config.read_text()
    assert config.stat().st_mode & 0o077 == 0, "only its owner may read the credential"


def test_status_and_its_json_carry_no_credential(monkeypatch):
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, SECRET)
    for argv in (["connector", "status"], ["connector", "status", "--json"]):
        result = runner.invoke(cli.app, argv)
        assert result.exit_code == 0, result.output
        assert SECRET not in result.output
        assert connection.DEMO_TP_CREDENTIAL not in result.output


def test_a_bad_credential_refuses_the_dry_run_without_printing_it(monkeypatch):
    errors = _recorded_refusals(monkeypatch)
    monkeypatch.setenv(connection.TP_CREDENTIAL_ENV, "nocolon-secret")
    with pytest.raises(SystemExit):
        cli_external._start("crypto", {}, dry_run=True)
    assert errors and "$UQF_TP_CREDENTIAL" in errors[0] and "nocolon-secret" not in errors[0]


# ------------------------------------------- bindings q and Python both need


def test_kafka_topics_default_to_every_topic_a_decoder_declares(tmp_path):
    """kafka_feed1's default (.qetl.kafka.topic.known[]), from the same q."""
    declared = kafka_feed.declared_topics(_paths(tmp_path))
    assert declared.get("uqf.client.flow") == "kafka_flow"
    assert kafka_feed.check_topics(_paths(tmp_path), None) == tuple(sorted(declared))


def test_a_topic_no_decoder_declares_is_refused_naming_the_declared_ones(tmp_path):
    with pytest.raises(UqsError) as exc:
        kafka_feed.check_topics(_paths(tmp_path), "uqf.client.flow,uqf.clinet.flow")
    text = str(exc.value)
    assert "uqf.clinet.flow is declared by no decoder job" in text
    assert "The declared topics are uqf.client.flow" in text


def test_a_topic_mismatch_stops_start_and_dry_run_before_anything_runs(monkeypatch):
    errors = _recorded_refusals(monkeypatch)
    monkeypatch.setattr(kafka_feed, "start_kafka_feed", lambda *a, **k: pytest.fail("started"))
    monkeypatch.setenv(kafka_feed.TOPICS_ENV, "no.such.topic")
    with pytest.raises(SystemExit):
        cli_external._start("kafka", {}, dry_run=True)
    with pytest.raises(SystemExit):
        cli_external._start("kafka", {})
    assert len(errors) == 2 and all("no.such.topic is declared by no decoder" in e for e in errors)


def test_a_table_the_plant_does_not_define_is_refused_before_a_start(monkeypatch, tmp_path):
    feed = feeds.ExternalFeed(
        "ghost",
        lambda p, o: pytest.fail("started"),
        lambda p: None,
        lambda p: {"running": "False"},
        produces={"ghost_raw": "the ghost feed"},
    )
    with pytest.raises(UqsError) as exc:
        feed.check_bindings(_paths(tmp_path), {})
    assert "feed ghost publishes ghost_raw, which src/etl/plant_tables.q does not define" in str(
        exc.value
    )
    assert "uqs job install" in str(exc.value)


def test_every_tree_connector_publishes_only_plant_tables(tmp_path):
    for name, feed in feeds.discover().items():
        assert feeds.unplanted(feed, REPO) == [], name


def _q_default_group() -> str:
    text = (REPO / "src/etl/streaming/kafka_feed.q").read_text()
    match = re.search(
        r'\.qpipe\.job\.kafka_feed\.defaults:\(`GROUP`OFFSET_RESET\)!\("([^"]+)"', text
    )
    assert match, "kafka_feed.q's defaults line moved - update this reader"
    return match.group(1)


def test_the_two_kafka_consumers_keep_their_own_groups(monkeypatch, tmp_path, spawned):
    """An intentional difference: one group shared by the alternatives would
    split its partitions between them. The Python consumer does not read
    $UQF_KAFKA_GROUP, which is kafka_feed1's."""
    assert kafka_feed.DEFAULT_GROUP != _q_default_group()
    monkeypatch.setenv("UQF_KAFKA_GROUP", "the-q-feeds-group")
    monkeypatch.setattr(kafka_feed.kafka_extra, "require", lambda: None)
    options = kafka_feed.FEED.start_options({})
    assert options["group"] == kafka_feed.DEFAULT_GROUP
    kafka_feed.FEED.start(_paths(tmp_path), kafka_feed.FEED.start_options({"group": "mine"}))
    ((cmd, _env),) = spawned
    assert cmd[cmd.index("--group") + 1] == "mine"


def test_kafka_brokers_and_topics_come_from_the_variables_kafka_feed1_reads(monkeypatch):
    """One cluster, configured once: the q feed reads UQF_KAFKA_<SETTING>."""
    text = (REPO / "src/etl/streaming/kafka_feed.q").read_text()
    assert '"UQF_KAFKA_"' in text and "`BROKERS" in text and "`TOPICS" in text
    assert (kafka_feed.BROKERS_ENV, kafka_feed.TOPICS_ENV) == (
        "UQF_KAFKA_BROKERS",
        "UQF_KAFKA_TOPICS",
    )
    monkeypatch.setenv(kafka_feed.BROKERS_ENV, "b:1")
    monkeypatch.setenv(kafka_feed.TOPICS_ENV, "uqf.client.flow")
    options = kafka_feed.FEED.start_options({})
    assert (options["brokers"], options["topics"]) == ("b:1", "uqf.client.flow")
    assert kafka_feed.FEED.start_options({"brokers": "x:2"})["brokers"] == "x:2"
