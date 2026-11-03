"""`--skip-<id>` options generated from the guardrail registry (#1826).

Every test declares its guardrails by replacing `safety.registry`, the one
place both `--override` and the generated options read. The one shipped
guardrail with skip_flag is `job backfill`'s eod-fence (#1828), tested with its
runtime in test_attach_backfill_eod_override.py; elsewhere the real tree is
shown taking one only under such a declaration.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
import typer
import typer.main
from plain_cli import PlainRunner
from typer.core import TyperGroup

from uqs import cli
from uqs.cli import renames, safety, skip_options
from uqs.cli import target as target_cli
from uqs.cli.renames import renamed
from uqs.deploy import config, control, targets
from uqs.guardrails import Guardrail
from uqs.paths import UqsError

runner = PlainRunner()

GUARD = Guardrail(
    "test-guard",
    ("job run",),
    "skips the test guard",
    risk="rows written meanwhile may be lost",
    action="check the rows after",
    skip_flag=True,
)
CODE = Guardrail(
    "TEST_CODE",
    ("job run",),
    "accepts the test finding on one process",
    component_scoped=True,
    skip_flag=True,
)
#: overridable, but declared without a generated option
PLAIN = Guardrail("plain-guard", ("job run",), "skips the plain guard")
#: never overridable: no option, and --override refuses it
FENCED = Guardrail("FENCED", ("job run",), "a proven mismatch", overridable=False)
#: another command's
OTHER = Guardrail("other-guard", ("job other",), "skips the other guard", skip_flag=True)
DECLARED = (GUARD, CODE, PLAIN, FENCED, OTHER)


@pytest.fixture
def declared(monkeypatch):
    monkeypatch.setattr(safety, "registry", lambda: DECLARED)
    return DECLARED


def _app() -> typer.Typer:
    """A small tree on the real command classes: `job run` marked
    ChangesState, `job other` through renamed(...) with an old spelling. Each
    prints what safety.given returns, or its refusal."""
    app, job = typer.Typer(), typer.Typer()
    app.add_typer(job, name="job")

    def report(command: str, override: list[str] | None, reason: str | None) -> None:
        try:
            typer.echo(json.dumps(safety.given(command, override, reason)))
        except UqsError as exc:
            typer.echo(f"refused: {exc}")
            raise typer.Exit(1) from exc

    @job.command("run", cls=safety.ChangesState)
    def run(override: safety.OverrideOpt = None, reason: safety.ReasonOpt = None) -> None:
        report("job run", override, reason)

    @job.command("other", cls=renamed({"--old": "--new"}, safety.ChangesState))
    def other(
        new: str = typer.Option("", "--new"),
        override: safety.OverrideOpt = None,
        reason: safety.ReasonOpt = None,
    ) -> None:
        typer.echo(f"new={new}")
        report("job other", override, reason)

    return app


def _given(*args: str) -> dict[str, str]:
    result = runner.invoke(_app(), list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.output.splitlines()[-1])


def _refused(*args: str) -> str:
    result = runner.invoke(_app(), list(args))
    assert result.exit_code != 0, result.output
    return " ".join(result.output.split())


# ------------------------------------------------------------ the option


def test_a_declaration_gives_the_command_an_option_its_signature_never_names(declared):
    help_text = " ".join(runner.invoke(_app(), ["job", "run", "--help"]).output.split())
    assert "--skip-test-guard" in help_text and "--skip-test-code" in help_text
    assert "--skip-plain-guard" not in help_text and "--skip-fenced" not in help_text
    assert "--skip-other-guard" not in help_text, "another command's"


def test_without_the_declaration_there_is_no_option():
    """The same command, under the shipped registry: nothing is generated."""
    assert "--skip-" not in runner.invoke(_app(), ["job", "run", "--help"]).output
    # #1824: no skip flag before its runtime. The one shipped is `job backfill`'s
    # --skip-eod-fence, whose runtime #1828 implements (test_attach_backfill_eod_override.py).
    assert [(g.id, g.commands) for g in safety.registry() if g.skip_flag] == [
        ("eod-fence", ("job backfill",))
    ]


def _group(command: object) -> TyperGroup:
    """`command`, checked to be a group, so its subcommands may be read."""
    assert isinstance(command, TyperGroup)
    return command


def _generated_help() -> dict[str, str | None]:
    """{flag: help} of `job run`'s generated options, as Click builds both its
    --help and its completion from them (`get_params`)."""
    root = _group(typer.main.get_command(_app()))
    top = root.make_context("uqs", ["job"], resilient_parsing=True)
    job_group = _group(root.commands["job"])
    job = job_group.make_context("job", [], parent=top, resilient_parsing=True)
    run = job_group.commands["run"]
    ctx = run.make_context("run", [], parent=job, resilient_parsing=True)
    return {p.opts[-1]: p.help for p in run.get_params(ctx) if skip_options.is_generated(p)}


def test_help_is_the_declaration(declared):
    helps = _generated_help()
    assert helps == {
        "--skip-test-guard": "Skips the test guard. Risk: rows written meanwhile may be lost. "
        "Then: check the rows after. The same as --override test-guard. Needs --reason; "
        "warned and recorded",
        "--skip-test-code": "Accepts the test finding on one process. The same as --override "
        "TEST_CODE:COMPONENT; repeatable, one COMPONENT each. Needs --reason; warned and "
        "recorded",
    }
    shown = " ".join(runner.invoke(_app(), ["job", "run", "--help"]).output.split())
    assert "--skip-test-code COMPONENT" in shown and "Skips the test guard." in shown


def test_completion_offers_the_options_help_shows_with_the_same_help(declared):
    """Typer's own completion protocol, as the installed zsh script drives it."""
    result = runner.invoke(
        _app(),
        [],
        prog_name="uqs",
        env={"_UQS_COMPLETE": "complete_zsh", "_TYPER_COMPLETE_ARGS": "uqs job run --skip-"},
    )
    offered = " ".join(result.output.replace("\\\\:", ":").split())
    for name, text in _generated_help().items():
        assert f'"{name}":"{text}"' in offered, offered
    assert "--skip-other-guard" not in offered
    bash = runner.invoke(
        _app(),
        [],
        prog_name="uqs",
        env={
            "_UQS_COMPLETE": "complete_bash",
            "COMP_WORDS": "uqs job run --skip-",
            "COMP_CWORD": "3",
        },
    )
    assert bash.output.split() == ["--skip-test-guard", "--skip-test-code"]


# ------------------------------------------------------- normalisation


def test_a_skip_is_the_override_it_spells(declared):
    skipped = _given("job", "run", "--skip-test-guard", "--reason", " window ")
    assert skipped == _given("job", "run", "--override", "test-guard", "--reason", "window")
    assert skipped == {"test-guard": "window"}


def test_a_component_skip_is_one_waiver_per_component(declared):
    skipped = _given(
        "job", "run", "--skip-test-code", "stp1", "--skip-test-code=rdb1", "--reason", "r"
    )
    overridden = _given(
        "job", "run", "--override", "TEST_CODE:stp1", "--override", "TEST_CODE:rdb1",
        "--reason", "r",
    )  # fmt: skip
    assert skipped == overridden == {"TEST_CODE:stp1": "r", "TEST_CODE:rdb1": "r"}


def test_both_spellings_together_are_one_request(declared):
    given = _given(
        "job", "run", "--skip-test-guard", "--override", "test-guard",
        "--skip-test-code", "stp1", "--override", "TEST_CODE:rdb1",
        "--override", "plain-guard", "--reason", "r",
    )  # fmt: skip
    assert given == dict.fromkeys(
        ["test-guard", "plain-guard", "TEST_CODE:stp1", "TEST_CODE:rdb1"], "r"
    )


def test_the_old_spellings_and_the_skips_parse_together(declared, monkeypatch):
    """renamed(...) rewrites first, then the generated options parse."""
    warned = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: warned.append((old, new)))
    result = runner.invoke(
        _app(), ["job", "other", "--old", "x", "--skip-other-guard", "--reason", "r"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["new=x", '{"other-guard": "r"}']
    assert warned == [("--old", "--new")]


def test_nothing_given_is_nothing(declared):
    assert _given("job", "run") == {}


# ------------------------------------------------------------ refusals


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--skip-test-guard"], "refused: --skip-test-guard needs --reason: say why"),
        (["--skip-test-guard", "--reason", "  "], "refused: --skip-test-guard needs --reason"),
        (["--skip-test-code", "stp1"], "refused: --skip-test-code stp1 needs --reason"),
        (["--override", "test-guard"], "refused: --override test-guard needs --reason"),
        (["--reason", "r"], "refused: --reason says why an --override is acceptable"),
        (
            ["--override", "nope", "--reason", "r"],
            "refused: `uqs job run` has no override 'nope' - it takes: TEST_CODE:COMPONENT, "
            "plain-guard, test-guard",
        ),
        (
            ["--override", "other-guard", "--reason", "r"],
            "refused: `uqs job run` has no override 'other-guard' (it is `uqs job other`'s)",
        ),
        (
            ["--override", "FENCED:stp1", "--reason", "r"],
            "refused: FENCED is never overridden: a proven mismatch",
        ),
        (
            ["--override", "TEST_CODE", "--reason", "r"],
            "refused: --override TEST_CODE: TEST_CODE is waived for one component - give "
            "TEST_CODE:COMPONENT",
        ),
        (
            ["--skip-test-code", "", "--reason", "r"],
            "refused: --skip-test-code : TEST_CODE is waived for one component",
        ),
        (
            ["--override", "test-guard:rdb1", "--reason", "r"],
            "refused: --override test-guard:rdb1: test-guard is waived for the whole command, "
            "not one component - give --override test-guard",
        ),
        (
            ["--skip-test-guard", "--override", "test-guard:rdb1", "--reason", "r"],
            "refused: conflicting requests for test-guard: --skip-test-guard waives it for the "
            "whole command, --override test-guard:rdb1 for one component - give it once",
        ),
        (["--skip-nope", "--reason", "r"], "No such option: --skip-nope"),
        (["--skip-plain-guard", "--reason", "r"], "No such option: --skip-plain-guard"),
        (["--skip-fenced", "--reason", "r"], "No such option: --skip-fenced"),
    ],
)
def test_job_run_refuses(declared, args, message):
    assert message in _refused("job", "run", *args)


def test_a_skip_out_of_its_commands_scope_is_no_option(declared):
    said = _refused("job", "other", "--skip-test-guard", "--reason", "r")
    assert "No such option: --skip-test-guard" in said


# --------------------------------------------------- refused declarations


def _run_command():
    return _group(_group(typer.main.get_command(_app())).commands["job"]).commands["run"]


def test_a_skip_flag_on_a_guardrail_that_is_never_overridden_is_refused():
    with pytest.raises(ValueError, match="FENCED: one that is not overridable gets no --skip"):
        Guardrail("FENCED", ("job run",), "x", overridable=False, skip_flag=True)


def test_two_guardrails_spelling_one_flag_are_refused(monkeypatch):
    twin = Guardrail("TEST_GUARD", ("job run",), "the same flag", skip_flag=True)
    monkeypatch.setattr(safety, "registry", lambda: (GUARD, twin))
    with pytest.raises(
        ValueError, match="'test-guard' and 'TEST_GUARD' would both be --skip-test-guard"
    ):
        skip_options.options_for("job run")


def test_a_flag_the_command_already_spells_is_refused(monkeypatch):
    clash = Guardrail("override", ("job run",), "names --skip-override", skip_flag=True)
    monkeypatch.setattr(safety, "registry", lambda: (clash,))
    cmd = _run_command()
    cmd.params[0].opts.append("--skip-override")  # as a signature declaring it would
    with pytest.raises(ValueError, match="`uqs job run` already has --skip-override"):
        skip_options.generated(cmd, "job run")


def test_a_command_without_override_and_reason_takes_no_generated_option(monkeypatch):
    """Its body could not read the skip, so the skip would be accepted and ignored."""
    lone = Guardrail("lone-guard", ("job bare",), "skips it", skip_flag=True)
    monkeypatch.setattr(safety, "registry", lambda: (lone,))
    app, job = typer.Typer(), typer.Typer()
    app.add_typer(job, name="job")
    job.command("bare", cls=safety.ChangesState)(lambda: None)
    job.command("unused", cls=safety.ReadOnly)(lambda: None)
    result = runner.invoke(app, ["job", "bare", "--help"])
    assert isinstance(result.exception, ValueError)
    assert "takes no --override NAME --reason TEXT" in str(result.exception)


# ----------------------------------------------------------- the real tree


def _with(monkeypatch, ids: set[str], *extra: Guardrail) -> None:
    real = safety.registry()
    flagged = tuple(dataclasses.replace(g, skip_flag=True) if g.id in ids else g for g in real)
    monkeypatch.setattr(safety, "registry", lambda: (*flagged, *extra))


def test_the_real_tree_takes_a_declared_skip(monkeypatch):
    """`uqs start` under a declaration: in its help, nothing else changed."""
    _with(monkeypatch, set(), dataclasses.replace(GUARD, commands=("start", "restart")))
    for path in (["start"], ["restart"]):
        out = " ".join(runner.invoke(cli.app, [*path, "--help"]).output.split())
        assert "--skip-test-guard" in out and "Skips the test guard" in out
    assert "--skip-test-guard" not in runner.invoke(cli.app, ["stop", "--help"]).output


def test_an_old_skip_spelling_blocks_a_generated_one_of_the_same_name(monkeypatch):
    """start still parses `--skip-q-version-check REASON` (renamed, #1732): a
    generated flag of that name would be read as it, so it is refused."""
    _with(monkeypatch, {"q-version-check"})
    result = runner.invoke(cli.app, ["start", "--help"])
    assert isinstance(result.exception, ValueError)
    assert "`uqs start` already has --skip-q-version-check" in str(result.exception)


def test_deploy_push_receives_a_skip_as_its_override(monkeypatch):
    """`deploy push` reads its overrides through safety.given (deploy_policy):
    a generated skip reaches make_config as `--override accept-digest` does."""
    _with(monkeypatch, {"accept-digest"})
    made = []

    def make_config(**kw):
        made.append(kw)
        raise config.DeployError("arguments", "stop here")

    monkeypatch.setattr(config, "make_config", make_config)
    base = ["deploy", "push", "a.tar.gz", "--ssh", "h", "--dest", "/d", "--profile", "p"]
    for spelling in (["--skip-accept-digest"], ["--override", "accept-digest"]):
        result = runner.invoke(cli.app, [*base, *spelling, "--reason", "vendor build"])
        assert result.exit_code == 2, result.output  # "stop here" at the arguments stage
    assert len(made) == 2 and made[0] == made[1]
    # an override not asked for is left out, for make_config's own None (#1892)
    assert made[0]["accept_digest"] == "vendor build" and "allow_unpinned" not in made[0]
    assert "uqs_skip__accept_digest" not in made[0], "the body never sees the option"


def test_deploy_push_to_targets_records_a_skip_as_the_override(monkeypatch):
    """deploy/targets.py layers the run's overrides over a target's settings and
    says where each came from: a skip is `--override accept-digest` there too."""
    _with(monkeypatch, {"accept-digest"})
    seen = []

    def configs(names, artifact, explicit, dry_run, root):
        seen.append((names, explicit))
        raise config.DeployError("arguments", "stop here")

    monkeypatch.setattr(targets, "configs", configs)
    args = ["deploy", "push", "a.tar.gz", "uat", "--skip-accept-digest", "--reason", "why"]
    assert runner.invoke(cli.app, args).exit_code == 2  # "stop here" at the arguments stage
    names, explicit = seen[0]
    assert names == "uat" and explicit == {"accept_digest": "why"}
    assert targets._flag("accept_digest") == "--override accept-digest"


# --------------------------------------------------------- uqs --target


@pytest.fixture
def sent(monkeypatch):
    """What `uqs --target uat ...` would run on the server."""
    out: list[list[str]] = []
    monkeypatch.setattr(control, "remote_for", lambda name, root: control.Remote(name, "h", "/d"))
    monkeypatch.setattr(control, "run", lambda r, args, tty: out.append(list(args)) or 0)
    _with(
        monkeypatch,
        set(),
        dataclasses.replace(GUARD, commands=("start",)),
        dataclasses.replace(CODE, commands=("start",)),
    )
    return out


@pytest.mark.parametrize(
    ("args", "server"),
    [
        (
            ["start", "rdb1", "--skip-test-guard", "--reason", "r"],
            ["start", "rdb1", "--override", "test-guard", "--reason", "r"],
        ),
        (
            ["start", "--skip-test-code", "stp1", "--skip-test-code=rdb1", "--reason", "r"],
            ["start", "--override", "TEST_CODE:stp1", "--override", "TEST_CODE:rdb1",
             "--reason", "r"],
        ),
        (  # --reason takes the word: it is no skip
            ["start", "--override", "test-guard", "--reason", "--skip-test-guard"],
            ["start", "--override", "test-guard", "--reason", "--skip-test-guard"],
        ),
        (  # after --, nothing is an option
            ["start", "--skip-test-guard", "--reason", "r", "--", "--skip-test-guard"],
            ["start", "--override", "test-guard", "--reason", "r", "--", "--skip-test-guard"],
        ),
        (  # a command it is not generated for is sent as given, for the server to refuse
            ["stop", "--skip-test-guard"],
            ["stop", "--skip-test-guard"],
        ),
    ],
)  # fmt: skip
def test_a_target_receives_a_skip_as_override(sent, args, server):
    assert target_cli.forward("uat", True, args, interactive=False) == 0
    assert sent == [server]
