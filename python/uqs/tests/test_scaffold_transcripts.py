"""The `uqs job new` transcripts in docs/scaffolding/, regenerated and compared.

Each transcript is marked `<!-- scaffold-transcript: ARGS -->` directly above
its fence. ARGS is run as `uqs job new ARGS --dry-run` against this tree, and
the plan it prints must be the block, line for line. The `(N lines)` after each
file is left out on both sides: it moves with every template edit, and the
pages say which files a scaffold touches, not how long they are.

The command a reader copies is a ```bash block on the same page, so ARGS must
be one of those too: a marker cannot drift from the command it documents.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import create, create_reaction
from uqs.scaffold.plan import ScaffoldPlan

UQF_ROOT = Path(__file__).resolve().parents[3]
PAGES = UQF_ROOT / "docs" / "scaffolding"
MARKER = re.compile(r"^<!-- scaffold-transcript: (?P<args>.+?) -->$")
COUNT = re.compile(r" \(\d+ lines?\)$")

#: The pages whose transcripts this holds - one per shape `uqs job new` writes.
#: A page losing its marker would otherwise drop out of the check silently.
SHAPES = {"feed.md", "etl.md", "normalizer.md", "backfill.md", "reaction.md"}


@dataclass(frozen=True)
class Transcript:
    page: Path
    line: int
    args: str
    body: str

    def __str__(self) -> str:
        return f"{self.page.relative_to(UQF_ROOT)}:{self.line}"


def _transcripts(page: Path) -> list[Transcript]:
    lines = page.read_text().splitlines()
    found = []
    for i, line in enumerate(lines):
        if not (m := MARKER.match(line)):
            continue
        assert lines[i + 1 : i + 2] == ["```"], (
            f"{page.name}:{i + 1}: a scaffold-transcript marker must sit directly above a ``` fence"
        )
        end = lines.index("```", i + 2)
        found.append(Transcript(page, i + 1, m["args"], "\n".join(lines[i + 2 : end])))
    return found


def _bash_commands(page: Path) -> set[str]:
    """Every command in the page's ```bash blocks, continuations joined."""
    text = page.read_text()
    blocks = re.findall(r"^```bash\n(.*?)^```$", text, flags=re.M | re.S)
    joined = "\n".join(b.replace("\\\n", " ") for b in blocks)
    return {" ".join(line.split()) for line in joined.splitlines() if line.strip()}


TRANSCRIPTS = [t for page in sorted(PAGES.glob("*.md")) for t in _transcripts(page)]


def test_every_shape_has_a_checked_transcript():
    assert {t.page.name for t in TRANSCRIPTS} >= SHAPES


@pytest.mark.parametrize("transcript", TRANSCRIPTS, ids=str)
def test_the_marked_command_is_the_one_the_page_shows(transcript: Transcript):
    command = " ".join(["uqs", "job", "new", *transcript.args.split()])
    assert command in _bash_commands(transcript.page), (
        f"{transcript}: `{command}` is not in a ```bash block on the page"
    )


@pytest.mark.parametrize("transcript", TRANSCRIPTS, ids=str)
def test_the_transcript_is_what_a_dry_run_prints(transcript: Transcript, monkeypatch):
    """Through the CLI, so the documented flags are parsed as a reader's are.
    The plan is captured where the command hands it to be printed: what the
    console then does to it is wrapping to the terminal, not content."""
    plans: list[ScaffoldPlan] = []

    def record(plan: ScaffoldPlan, repo_root: Path, *, dry_run: bool) -> None:
        assert dry_run, "the check must never write"
        plans.append(plan)

    def refuse(exc: Exception) -> None:
        raise AssertionError(f"{transcript}: uqs job new refused: {exc}")

    for module in (create, create_reaction):
        monkeypatch.setattr(module, "write_plan", record)
        monkeypatch.setattr(module, "_die", refuse)
    argv = ["job", "new", *shlex.split(transcript.args), "--dry-run"]
    result = CliRunner().invoke(cli.app, argv)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    (plan,) = plans
    printed = "\n".join(COUNT.sub("", line) for line in plan.render().splitlines())
    assert transcript.body == printed, f"{transcript} differs from what the scaffold prints"


#: Where else a reader copies a `uqs job new` from: the command's own --help and
#: the new-job skill (#2505). Each must scaffold as written, at least in a dry run.
SKILL = UQF_ROOT / ".claude" / "skills" / "new-job" / "SKILL.md"


def _commands(text: str) -> list[str]:
    """Every `uqs job new` command line in text, continuations joined."""
    joined = text.replace("\\\n", " ")
    return [
        " ".join(line.split())
        for line in joined.splitlines()
        if line.strip().startswith("uqs job new ")
    ]


def _skill_commands() -> list[str]:
    blocks = re.findall(r"^```bash\n(.*?)^```$", SKILL.read_text(), flags=re.M | re.S)
    return _commands("\n".join(blocks))


HELP_EXAMPLES = _commands(create.new_job.__doc__ or "")
SKILL_EXAMPLES = _skill_commands()


def test_the_help_and_the_skill_carry_examples():
    """A rewrite that dropped them would empty the check below silently."""
    assert HELP_EXAMPLES and SKILL_EXAMPLES
    assert any("--triggered-by" in c for c in HELP_EXAMPLES)
    assert any("--triggered-by" in c for c in SKILL_EXAMPLES)


@pytest.mark.parametrize("command", HELP_EXAMPLES + SKILL_EXAMPLES)
def test_a_documented_example_scaffolds_in_a_dry_run(command: str, monkeypatch):
    def record(plan: ScaffoldPlan, repo_root: Path, *, dry_run: bool) -> None:
        assert dry_run, "the check must never write"

    def refuse(exc: Exception) -> None:
        raise AssertionError(f"`{command}` is refused: {exc}")

    for module in (create, create_reaction):
        monkeypatch.setattr(module, "write_plan", record)
        monkeypatch.setattr(module, "_die", refuse)
    argv = shlex.split(command)[1:]
    if "--dry-run" not in argv:
        argv.append("--dry-run")
    result = CliRunner().invoke(cli.app, argv)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
