"""Make `--install-completion` write a zsh script that completes on the FIRST tab.

Typer generates a `#compdef uqs` file whose body does not complete anything:

    _uqs_completion() { eval $(... uqs) }
    compdef _uqs_completion uqs

A `#compdef` file IS the completion function for the command - zsh autoloads
it and runs the body to get candidates. Typer's body only *defines* a helper
and re-registers it, so the first TAB in every new shell produces nothing and
rebinds `uqs`; the second TAB is the one that completes. Measured: one TAB
printed no candidates, two printed all 40 process names.

The fix is to have the body run the completion directly. Same protocol, same
environment variables, one less round trip:

    eval $(... uqs)

The value has to be replaced in `_completion_scripts`, the dict
`get_completion_script` actually looks the shell up in. Rebinding the
module-level `COMPLETION_SCRIPT_ZSH` alone does nothing: the dict captured
the original string when the module was imported, and every install and
`--show-completion` reads the dict.

This reaches into a private Typer name, which is why `test_zsh_completion.py`
asserts the template we replace still looks like the one we expect. If a
Typer upgrade changes it, that test fails rather than this silently
reinstating the wasted TAB.
"""

from __future__ import annotations

from typer import _completion_shared

#: What the body must do: complete, rather than register something that does.
COMPLETION_SCRIPT_ZSH = (
    "\n#compdef %(prog_name)s\n\n"
    # One emitted line, split here only to stay inside the line-length gate.
    'eval $(env _TYPER_COMPLETE_ARGS="${words[1,$CURRENT]}" '
    "%(autocomplete_var)s=complete_zsh %(prog_name)s)\n"
)


def patch_zsh_completion_script() -> None:
    """Point Typer's zsh installer at the first-TAB script.

    Called for its side effect at CLI start. Idempotent, and deliberately
    silent: a completion script that is one TAB slower is not worth failing
    `uqs start` over, so a Typer that no longer exposes the name is left
    alone and the test is what reports it.
    """
    scripts = getattr(_completion_shared, "_completion_scripts", None)
    if isinstance(scripts, dict) and "zsh" in scripts:
        scripts["zsh"] = COMPLETION_SCRIPT_ZSH
