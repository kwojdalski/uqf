#!/usr/bin/env bash
# Generates HTML API docs for src/**/*.q using qDoc (bundled inside qStudio's
# jar) into build/docs/ (gitignored - regenerate on demand, don't commit it).
#
# Output goes to build/docs/, NOT docs/. This used to `rm -rf docs` before
# regenerating, which would have deleted every hand-written document in
# docs/ - ROADMAP.md, the requirements files, migrations/, drift-reports/,
# torq/. Generated output and authored documentation must not share a
# directory when the generator clears its target.
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

rm -rf build/docs
mkdir -p build/docs
# UNVERIFIED since the src/ domain split: this passes `src` as the source
# folder, and whether qDoc recurses into subdirectories has not been tested
# here (it needs the ~120MB qstudio.jar plus a JDK). If the generated index
# comes back with fewer than the 14 modules, qDoc is not recursing and this
# needs one invocation per subdirectory, or a find|xargs over them.
java -cp "$QSTUDIO_JAR" com.timestored.qdoc.QDocMain build/docs src

echo ""
echo "Docs generated at build/docs/index.html"
