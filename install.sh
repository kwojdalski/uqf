#!/usr/bin/env bash
# install.sh - set this repository up on a workstation (#850).
#
#   ./install.sh               the uqs CLI, after checking the fleet's prerequisites
#   ./install.sh --dev         also the Python workspace, pre-commit and its hooks
#   ./install.sh --web         also the browser application in web/, installed and built
#   ./install.sh --odbc        also ODBC for q: on macOS built in user space, on Linux checked
#   ./install.sh --check       check the selected components' prerequisites only
#
# Flags combine (`--dev --web`, `--check --odbc`). Every prerequisite is checked
# before anything changes, and a missing required one stops the run with the
# command that installs it. Re-running is safe.
#
# WHAT IT NEVER DOES. No sudo and no system packages - a missing system tool is
# reported, not installed. No change to a q or TorQ installation, no secret
# written, no process started. Deploying a server is `uqs deploy`'s job.
#
# ODBC (--odbc). q's ODBC client links a driver manager, unixODBC - a C
# library, so no Python package or uv can supply it. On macOS KX's only client
# is x86_64, so it runs under Rosetta: scripts/dev/odbc_rosetta.sh builds
# unixODBC, the client and the DuckDB driver into output/odbc-x86_64, in user
# space, and this runs it. On Linux unixODBC is a system package, so it is
# checked and its install command printed. A server takes an approved package
# instead (`uqs odbc install`, docs/guides/odbc.md). Database drivers are
# per-database and often not redistributable: never installed here.
#
# TLS VERIFICATION STAYS ON. Behind a proxy that re-signs TLS, give the tools
# your CA instead: SSL_CERT_FILE=/path/ca.pem (or UV_SYSTEM_CERTS=1) for uv,
# NODE_EXTRA_CA_CERTS=/path/ca.pem for npm.

set -euo pipefail

# Paths are relative to this file, not the caller's directory.
src="${BASH_SOURCE[0]}"
case "$src" in */*) here="${src%/*}" ;; *) here=. ;; esac
ROOT="$(cd "$here" && pwd)"
cd "$ROOT"

DEV=0 WEB=0 ODBC=0 CHECK=0
for arg in "$@"; do
    case "$arg" in
        --dev) DEV=1 ;;
        --web) WEB=1 ;;
        --odbc) ODBC=1 ;;
        --check) CHECK=1 ;;
        -h | --help)
            sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown option '$arg' (try --help)" >&2
            exit 2
            ;;
    esac
done

#: Distribution names this package has used. A tool still registered under one
#: owns the `uqs` executable, so it goes first: `uv tool install` adds a second
#: tool rather than replacing one under another name.
FORMER_NAMES=(uqf-stack torq-orchestrator torq-demo)
PACKAGE="python/uqs"
COMMAND="uqs"
PRE_COMMIT="pre-commit==4.6.2" # the version .github/workflows/ci.yml runs
QLINT_VERSION="v0.14.9"        # the qlinter CI pins; the q-traps rules match it
NODE_RANGE="^22.13 || ^24 || >=26" # web/package.json "engines"

INSTALLED=() MISSING=() OPTIONAL=() UNVERIFIED=()

if [[ "$(uname -s)" == Darwin ]]; then mac=1; else mac=0; fi
# How to install a system package here - printed, never run.
pkg() { if ((mac)); then echo "brew install $2"; else echo "sudo apt-get install $1"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }
need() { have "$1" || MISSING+=("$1 - $2"); }
want() { have "$1" || OPTIONAL+=("$1 - $2"); }

# ------------------------------------------------------------------ checks

need uv "curl -LsSf https://astral.sh/uv/install.sh | sh"

# q: QCMD or `q` on PATH, as uqs resolves it, and it must run a line - which
# is also what a missing licence (QHOME) breaks. Fed on stdin: nothing written.
qcmd="${QCMD:-q}"
if ! have "$qcmd"; then
    MISSING+=("q ($qcmd) - install KDB-X, or set QCMD (and QHOME) to your q")
else
    limit=""; if have timeout; then limit="timeout 20"; fi
    out="$(printf '%s\n' '-1 "UQF_Q_OK ",string .z.K;exit 0' | $limit "$qcmd" -q 2>/dev/null || true)"
    if [[ "$out" != *UQF_Q_OK* ]]; then
        MISSING+=("q ($qcmd) does not run a script - check QHOME and its licence")
    else
        # The tree as written uses nested working contexts, which kdb+ has
        # only from 5.0 (uqs.interpreter.NESTED_CONTEXTS_SINCE, which
        # test_interpreter.py holds this to): `uqs start` refuses an older q
        # unless the runtime loads a flattened tree (#882).
        qversion="${out##*UQF_Q_OK }"; qversion="${qversion%%[!0-9.]*}"
        if [[ "${qversion%%.*}" =~ ^[0-9]+$ ]] && (( ${qversion%%.*} < 5 )); then
            MISSING+=("q ($qcmd) is kdb+ $qversion, which has no nested contexts - use KDB-X 5.0 or later")
        fi
    fi
fi
# uqs refuses to start the fleet without these two (uqs.paths.check_prerequisites).
need envsubst "$(pkg gettext-base gettext)"
need rlwrap "$(pkg rlwrap rlwrap)"
if [[ ! -f "${TORQHOME:-$ROOT/lib/torq}/torq.q" ]]; then
    MISSING+=("TorQ - no torq.q in ${TORQHOME:-lib/torq}; set TORQHOME to a TorQ installation")
fi

want direnv "loads .envrc: $(pkg direnv direnv)"
want multitail "uqs logs --multitail: $(pkg multitail multitail)"
want qcon "uqs query with no expression; ships with some kdb+ distributions"
((ODBC)) || want odbcinst "ODBC sources: ./install.sh --odbc, or see docs/guides/odbc.md"
UNVERIFIED+=("live feeds: the databento / confluent-kafka packages and credentials (docs/services/)")
UNVERIFIED+=("source credentials and ODBC drivers: uqs config sources check")

if ((DEV)); then
    need git "$(pkg git git)"
    want cargo "qlinter $QLINT_VERSION for the q-traps hook: https://rustup.rs, then cargo install --git https://github.com/kwojdalski/q-lint --tag $QLINT_VERSION --locked"
    want make "building PeachQ for the q-docs-peachq hook: $(pkg build-essential 'xcode-select --install')"
    want d2 "rendering diagrams (render-diagrams hook): https://d2lang.com"
    want quarto "checking docs links (check-doc-links hook): https://quarto.org"
fi

if ((ODBC)); then
    if ((mac)); then
        # What scripts/dev/odbc_rosetta.sh setup needs (its own Requires line).
        if [[ "$(uname -m)" == arm64 ]] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
            MISSING+=("Rosetta 2 - softwareupdate --install-rosetta --agree-to-license")
        fi
        xcode-select -p >/dev/null 2>&1 || MISSING+=("Xcode command line tools - xcode-select --install")
        need curl "$(pkg curl curl)"
        if ! have gh; then
            MISSING+=("gh - $(pkg gh gh), then gh auth login")
        elif ! gh auth status >/dev/null 2>&1; then
            MISSING+=("gh is not logged in - gh auth login (it downloads unixODBC and the DuckDB driver)")
        fi
        [[ -d "$HOME/.kx" ]] || MISSING+=("KDB-X at ~/.kx - the ODBC overlay QHOME links to it")
    else
        need odbcinst "$(pkg unixodbc unixodbc) - root installs the driver manager; a server takes an approved package instead (uqs odbc install)"
        if have ldconfig && [[ "$(ldconfig -p 2>/dev/null)" != *libodbc.so.2* ]]; then
            MISSING+=("libodbc.so.2 - $(pkg unixodbc unixodbc)")
        fi
        # KX's client goes into QHOME, which this script never changes.
        if [[ ! -f "${QHOME:-$HOME/.kx}/l64/odbc.so" ]]; then
            OPTIONAL+=("KX's ODBC client - copy odbc.k and l64/odbc.so from https://github.com/KxSystems/kdb into QHOME (docs/guides/odbc.md)")
        fi
    fi
    UNVERIFIED+=("a database's own ODBC driver: per database - docs/guides/odbc.md")
fi

if ((WEB)); then
    if ! have node; then
        MISSING+=("node $NODE_RANGE - https://nodejs.org")
    else
        v="$(node --version)"
        v="${v#v}"
        major="${v%%.*}" minor="${v#*.}" minor="${minor%%.*}"
        if ! ((major == 22 && minor >= 13 || major == 24 || major >= 26)); then
            MISSING+=("node $NODE_RANGE - this is v$v")
        fi
    fi
    need npm "ships with node"
fi

summary() {
    echo
    if ((${#INSTALLED[@]})); then
        echo "Installed:"; printf '  %s\n' "${INSTALLED[@]}"
    fi
    if ((${#MISSING[@]})); then
        echo "Missing, required:"; printf '  %s\n' "${MISSING[@]}"
    fi
    if ((${#OPTIONAL[@]})); then
        echo "Missing, optional:"; printf '  %s\n' "${OPTIONAL[@]}"
    fi
    echo "Not verified here:"; printf '  %s\n' "${UNVERIFIED[@]}"
}

if ((${#MISSING[@]})); then
    summary
    echo
    echo "Nothing was changed: install what is required, then run this again." >&2
    exit 1
fi
if ((CHECK)); then
    summary
    echo
    echo "--check: nothing was changed."
    exit 0
fi

# Any step that fails ends the run with what was done so far.
run() {
    local what="$1"; shift
    echo "== $what"
    if ! "$@"; then
        MISSING+=("$what failed: $*")
        summary
        exit 1
    fi
}

# ---------------------------------------------------------------- uqs CLI

installed="$(uv tool list 2>/dev/null || true)"
for name in "${FORMER_NAMES[@]}"; do
    if [[ $'\n'"$installed" == *$'\n'"$name "* ]]; then
        run "removing '$name', an install from before a rename" uv tool uninstall "$name"
    fi
done
run "installing $COMMAND from $PACKAGE (editable)" uv tool install --force --editable "$PACKAGE"

# Verify rather than announce: a stale entry point earlier on PATH, or uv's
# tool directory missing from PATH, leaves the command unusable.
if ! resolved="$(command -v "$COMMAND")"; then
    MISSING+=("$COMMAND is installed but not on PATH - run 'uv tool update-shell' and open a new shell")
    summary
    exit 1
fi
if ! "$COMMAND" --help >/dev/null 2>&1; then
    MISSING+=("$COMMAND at $resolved fails to run - another copy earlier on PATH?")
    summary
    exit 1
fi
INSTALLED+=("$COMMAND -> $resolved (editable: edits under $PACKAGE/src are live)")

# -------------------------------------------------------------------- dev

if ((DEV)); then
    run "syncing the Python workspace" uv sync
    INSTALLED+=("Python workspace (.venv)")
    if ! have pre-commit; then
        run "installing $PRE_COMMIT" uv tool install "$PRE_COMMIT"
    fi
    run "installing the git hooks" pre-commit install
    INSTALLED+=("pre-commit and its git hooks")
    UNVERIFIED+=("the q hooks need KDB-X on PATH; see the list above for the rest")
fi

# ------------------------------------------------------------------- odbc

if ((ODBC)); then
    if ((mac)); then
        run "building ODBC for q under Rosetta (a few minutes the first time)" scripts/dev/odbc_rosetta.sh setup
        INSTALLED+=("ODBC for q under Rosetta, in output/odbc-x86_64 - run q with it: scripts/dev/odbc_rosetta.sh q")
    else
        INSTALLED+=("ODBC driver manager present - q loads it through KX's l64/odbc.so")
    fi
fi

# -------------------------------------------------------------------- web

if ((WEB)); then
    run "installing web/ from its lockfile" npm --prefix web ci
    run "building web/" npm --prefix web run build
    INSTALLED+=("browser application, built into web/dist")
fi

summary
