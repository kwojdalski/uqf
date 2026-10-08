#!/usr/bin/env bash
# Installs the tools this repository needs (direnv, envsubst, rlwrap,
# pre-commit; `--all` adds multitail and qlinter), then puts this repository's
# console commands on your PATH, so they work without a `uv run --project ...`
# prefix. Currently one: `uqs`, the stack orchestrator (python/uqs,
# [project.scripts]).
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

# ------------------------------------------------------------------- tools
#
# The tools this repository needs besides uv, each installed only when it is
# missing from PATH: system ones through brew (macOS) or apt (Debian, Ubuntu,
# WSL), pre-commit as a uv tool at the version CI pins. `--all` adds the
# optional ones. See the README's "Also needed, by component" for what each
# is for.

ALL=0
for arg in "$@"; do
    case "$arg" in
        --all) ALL=1 ;;
        -h|--help) echo "usage: $0 [--all]   (--all: also multitail and qlinter)"; exit 0 ;;
        *) echo "error: unknown argument '$arg'" >&2; exit 2 ;;
    esac
done

#: command  apt package  brew package
REQUIRED_TOOLS=(
    "direnv direnv direnv"         # loads .envrc
    "envsubst gettext-base gettext" # torq.sh
    "rlwrap rlwrap rlwrap"         # torq.sh, qcon
)
OPTIONAL_TOOLS=(
    "multitail multitail multitail" # uqs logs --multitail
)
PRE_COMMIT="pre-commit==4.6.2"     # .github/workflows/ci.yml runs this version
QLINT_VERSION="v0.2.0"             # and pins this qlinter: the q-traps rules match it

pkg_install() {
    local apt_pkg="$1" brew_pkg="$2"
    if command -v brew >/dev/null 2>&1; then
        brew install "$brew_pkg"
    elif command -v apt-get >/dev/null 2>&1; then
        local sudo=""
        if [[ "$(id -u)" -ne 0 ]]; then sudo="sudo"; fi
        if [[ -z "${APT_UPDATED:-}" ]]; then
            $sudo apt-get update -qq
            APT_UPDATED=1
        fi
        $sudo apt-get install -y -qq "$apt_pkg"
    else
        echo "error: no brew or apt-get - install '$apt_pkg' yourself" >&2
        return 1
    fi
}

tools=("${REQUIRED_TOOLS[@]}")
if [[ "$ALL" -eq 1 ]]; then tools+=("${OPTIONAL_TOOLS[@]}"); fi
for entry in "${tools[@]}"; do
    read -r cmd apt_pkg brew_pkg <<<"$entry"
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "ok: $cmd"
    else
        echo "installing $cmd"
        pkg_install "$apt_pkg" "$brew_pkg"
        if [[ "$cmd" == direnv ]]; then DIRENV_NEW=1; fi
    fi
done

if command -v pre-commit >/dev/null 2>&1; then
    echo "ok: pre-commit"
else
    echo "installing $PRE_COMMIT"
    uv tool install "$PRE_COMMIT"
fi

if [[ "$ALL" -eq 1 ]]; then
    if command -v qlinter >/dev/null 2>&1; then
        echo "ok: qlinter"
    elif command -v cargo >/dev/null 2>&1; then
        echo "installing qlinter"
        cargo install --git https://github.com/kwojdalski/q-lint --tag "$QLINT_VERSION" --locked
    else
        echo "skipped: qlinter needs cargo (https://rustup.rs)" >&2
    fi
fi

if [[ -n "${DIRENV_NEW:-}" ]]; then
    echo "direnv: add 'eval \"\$(direnv hook bash)\"' (or zsh) to your shell rc, then 'direnv allow' here"
fi

# ---------------------------------------------------------------------- uqs

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
