#!/usr/bin/env bash
# Puts this repository's console commands on your PATH, so they work without a
# `uv run --project ...` prefix. Currently one: `uqs`, the stack orchestrator
# (python/uqs, [project.scripts]).
#
# Idempotent - re-run it after pulling, after adding an entry point, or any
# time `uqs` starts behaving like an older copy of itself.
#
# WHY A SCRIPT RATHER THAN A LINE IN THE README
#
# `uv tool install --editable` is the whole install, and it would fit on one
# line if the package had never been renamed. It has been renamed twice
# (torq-orchestrator -> uqf-stack -> uqs), and a DISTRIBUTION rename is the
# case the one-liner gets wrong:
#
#   * The console script is generated once, at install time, and names its
#     import literally. The one from two renames ago still reads
#     `from torq_orchestrator.cli import main`, so it raises ModuleNotFoundError.
#   * `uv tool install` will not replace a tool registered under a different
#     name. It adds a SECOND tool, and the old one goes on owning `uqs` on
#     your PATH - so the install appears to succeed and the command stays
#     broken.
#
# So the uninstall of the old names is the part worth automating, and the
# verification at the end is the part that proves it worked. Both are steps a
# human reading a one-liner skips.
#
# Requires: uv (https://docs.astral.sh/uv/ - `curl -LsSf https://astral.sh/uv/install.sh | sh`)

set -euo pipefail

# `git rev-parse`, not a count of `..` from this file: scripts/dev/ has been
# moved before (#241), and a hardcoded depth resolves to the wrong directory
# silently rather than failing.
REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$REPO"

#: Distribution names this package has used. A tool still registered under one
#: of these owns the `uqs` executable and must go first.
FORMER_NAMES=(uqf-stack torq-orchestrator torq-demo)

#: package directory -> the command it installs, for the verification below.
PACKAGE="python/uqs"
COMMAND="uqs"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not on PATH." >&2
    echo "       Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# Every uv call below goes through this, so it can reach PyPI from behind a
# proxy that re-signs TLS with a certificate uv does not trust. The cost is
# real: certificate checks are OFF for these two hosts, so anything that can
# intercept the connection can serve a different package. Defined AFTER the
# check above on purpose - once `uv` is a function, `command -v uv` finds the
# function and would pass even with no uv installed.
uv() {
    command uv \
        --allow-insecure-host pypi.org \
        --allow-insecure-host files.pythonhosted.org \
        "$@"
}

if [[ ! -f "$PACKAGE/pyproject.toml" ]]; then
    echo "error: $PACKAGE/pyproject.toml not found under $REPO." >&2
    echo "       If the package moved, update PACKAGE in this script." >&2
    exit 1
fi

installed="$(uv tool list 2>/dev/null || true)"
for name in "${FORMER_NAMES[@]}"; do
    if grep -qE "^${name} " <<<"$installed"; then
        echo "removing '$name', an install from before this package was renamed"
        uv tool uninstall "$name"
    fi
done

echo "installing $COMMAND from $PACKAGE (editable)"
uv tool install --force --editable "$PACKAGE"

# Verify rather than announce. `uv tool install` reporting success is not the
# same as the command working: a stale entry point earlier on PATH, or a
# missing tool bin directory, both leave it unusable.
resolved="$(command -v "$COMMAND" 2>/dev/null || true)"
if [[ -z "$resolved" ]]; then
    echo
    echo "warning: '$COMMAND' installed but is not on your PATH." >&2
    echo "         Run 'uv tool update-shell' and open a new shell, or add" >&2
    echo "         uv's tool bin directory (\$HOME/.local/bin on most setups)" >&2
    echo "         to PATH yourself." >&2
    echo
    echo "Until then: uv run --project $PACKAGE $COMMAND --help" >&2
    exit 1
fi

if ! "$COMMAND" --help >/dev/null 2>&1; then
    echo
    echo "error: '$COMMAND' is at $resolved but fails to run." >&2
    echo "       The likely cause is another copy earlier on PATH. This is what" >&2
    echo "       it imports:" >&2
    grep -m1 'import main' "$resolved" >&2 || true
    exit 1
fi

echo
echo "$COMMAND -> $resolved"
echo "Editable: source edits under $PACKAGE/src are live with no reinstall."
echo "Re-run this script only when an entry point is added or renamed."
