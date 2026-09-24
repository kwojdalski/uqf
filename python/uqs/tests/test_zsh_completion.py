"""The zsh completion script must complete on the first TAB.

`zsh_completion.py` replaces a private Typer constant. These tests are what
make that safe: if a Typer upgrade renames or reshapes the thing being
replaced, they fail here rather than quietly restoring the wasted TAB - a
regression nothing else in the suite can see, because it only shows up as a
keystroke that does nothing in an interactive shell.
"""

from __future__ import annotations

from typer import _completion_shared

from uqs.cli.zsh_completion import COMPLETION_SCRIPT_ZSH, patch_zsh_completion_script


def test_typer_still_exposes_the_template_we_replace() -> None:
    """The dict we patch still exists, keyed by shell, holding a zsh script.

    `get_completion_script` reads `_completion_scripts[shell]`, not the
    module-level constant - patching the constant alone is a no-op, which is
    how the first version of this fix silently did nothing.
    """
    scripts = _completion_shared._completion_scripts
    assert isinstance(scripts, dict)
    assert "#compdef" in scripts["zsh"]


def test_our_script_completes_rather_than_registers() -> None:
    """The body runs the completion; it does not define-and-register.

    This is the whole defect: a `#compdef` file's body IS the completion
    function, so a body whose only effect is `compdef ...` yields no
    candidates the first time it runs.
    """
    lines = COMPLETION_SCRIPT_ZSH.splitlines()
    # The `#compdef` TAG must stay - it is what makes zsh autoload this file
    # for `uqs`. What must not appear is a bare `compdef` REGISTRATION call,
    # which is the part that wasted the first TAB.
    assert not [ln for ln in lines if ln.startswith("compdef ")]
    assert "_completion()" not in COMPLETION_SCRIPT_ZSH
    assert COMPLETION_SCRIPT_ZSH.lstrip().startswith("#compdef %(prog_name)s")
    assert "eval $(env _TYPER_COMPLETE_ARGS=" in COMPLETION_SCRIPT_ZSH


def test_script_keeps_typer_s_protocol() -> None:
    """Same env vars Typer's own zsh class reads, or nothing completes.

    `_TYPER_COMPLETE_ARGS` rather than Click's `COMP_WORDS`, and
    `complete_zsh` rather than Click 8's `zsh_complete`: Typer keeps the
    Click 7 spelling on both, and getting either wrong returns the top-level
    command list for every input.
    """
    assert '_TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}"' in COMPLETION_SCRIPT_ZSH
    assert "%(autocomplete_var)s=complete_zsh" in COMPLETION_SCRIPT_ZSH


def test_rendered_script_is_one_eval_line() -> None:
    """The split string must still emit a single runnable line."""
    rendered = COMPLETION_SCRIPT_ZSH % {
        "prog_name": "uqs",
        "autocomplete_var": "_UQS_COMPLETE",
    }
    evals = [ln for ln in rendered.splitlines() if ln.startswith("eval ")]
    assert len(evals) == 1
    assert evals[0].endswith("_UQS_COMPLETE=complete_zsh uqs)")
    assert "#compdef uqs" in rendered


def test_patch_is_idempotent_and_takes_effect() -> None:
    original = _completion_shared._completion_scripts["zsh"]
    try:
        patch_zsh_completion_script()
        patch_zsh_completion_script()
        assert _completion_shared._completion_scripts["zsh"] == COMPLETION_SCRIPT_ZSH
    finally:
        _completion_shared._completion_scripts["zsh"] = original


def test_generated_script_goes_through_the_patch() -> None:
    """What Typer would WRITE, not just what we stored - the end of the path.

    Guards the actual defect: patching the wrong name left
    `get_completion_script` returning Typer's two-step script unchanged.
    """
    original = _completion_shared._completion_scripts["zsh"]
    try:
        patch_zsh_completion_script()
        rendered = _completion_shared.get_completion_script(
            prog_name="uqs", complete_var="_UQS_COMPLETE", shell="zsh"
        )
        assert "compdef _uqs_completion uqs" not in rendered
        assert rendered.splitlines()[0] == "#compdef uqs"
    finally:
        _completion_shared._completion_scripts["zsh"] = original
