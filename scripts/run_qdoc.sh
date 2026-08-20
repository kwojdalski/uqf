#!/usr/bin/env bash
# Launches the vendored q-doc (lib/q-doc/, see README's Licensing section)
# as a live kdb+ process serving browsable API docs over HTTP - an
# alternative to gen-docs.sh's qStudio-jar static-HTML approach that needs
# no external download, at the cost of staying up as a server instead of
# writing files to docs/.
#
# Requires real kdb+/KDB-X, not the local PeachQ binary: q-doc uses
# .Q.opt/.h.ty/HTTP request handlers PeachQ doesn't support.
#
# Run from the repository root: ./scripts/run_qdoc.sh [port]
# Once it's up, at the q) prompt run (any subset of this repo's folders):
#   .qdoc.parser.init `:src`:python/uqf-client
# then browse http://localhost:<port>/index-kdb.html

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8090}"

if command -v q >/dev/null 2>&1; then
    QBIN=q
elif [ -x "$HOME/.kx/bin/q" ]; then
    QBIN="$HOME/.kx/bin/q"
    export QHOME="${QHOME:-$HOME/.kx}"
else
    echo "No real kdb+/KDB-X interpreter found (checked PATH and ~/.kx/bin/q)." >&2
    echo "Install KDB-X/kdb+ (https://kx.com/kdb-personal-edition-download/) - PeachQ isn't compatible with q-doc." >&2
    exit 1
fi

echo "Starting q-doc on port $PORT ..."
echo 'Once loaded, run: .qdoc.parser.init `:src'
echo "Then browse: http://localhost:$PORT/index-kdb.html"
echo ""
exec "$QBIN" lib/q-doc/q-doc.q -p "$PORT" -standalone
