#!/usr/bin/env bash
# Generates HTML API docs for src/*.q using qDoc (bundled inside qStudio's
# jar) into docs/ (gitignored - regenerate on demand, don't commit it).
#
# Requires:
#   - Java 8+ on PATH
#   - qstudio.jar next to this repo's root, or set QSTUDIO_JAR to its path.
#     Download: https://www.timestored.com/qstudio/download (~120MB)
#
# NOTE: TimeStored's own qDoc docs (timestored.com/qstudio/help/qdoc) state
# the CLI usage as `QDocMain <sourceFolder> <targetFolder>` - that is
# backwards. The actual, verified argument order is
# `QDocMain <targetFolder> <sourceFolder>` (confirmed by running it; see
# the kdb-q-conventions skill for the full writeup).

set -euo pipefail
cd "$(dirname "$0")/.."

QSTUDIO_JAR="${QSTUDIO_JAR:-qstudio.jar}"

if ! command -v java >/dev/null 2>&1; then
    echo "java not found on PATH - install a JDK (e.g. brew install openjdk) first." >&2
    exit 1
fi

if [ ! -f "$QSTUDIO_JAR" ]; then
    echo "qstudio.jar not found at '$QSTUDIO_JAR'." >&2
    echo "Download it from https://www.timestored.com/qstudio/download and either" >&2
    echo "place it at the repo root, or set QSTUDIO_JAR=/path/to/qstudio.jar." >&2
    exit 1
fi

rm -rf docs
mkdir -p docs
java -cp "$QSTUDIO_JAR" com.timestored.qdoc.QDocMain docs src

echo ""
echo "Docs generated at docs/index.html"
