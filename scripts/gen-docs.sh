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

# qDoc is given a FLAT staging directory rather than src/ itself.
#
# Before the src/ domain split, src/ was flat and qDoc saw every module
# directly. Now the modules live in seven subdirectories, and whether qDoc
# recurses is not something this repository can test - it needs the ~120MB
# qstudio.jar plus a JDK. Rather than depend on an untested behaviour and
# silently generate a documentation set missing 14 of 15 modules, stage a
# flat copy and pass that. This is correct whether or not qDoc recurses.
#
# Safe because every .q basename under src/ is unique (15 files, 15 distinct
# names) - the check below fails loudly if that ever stops being true.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

EXPECTED=0
while IFS= read -r f; do
    base="$(basename "$f")"
    if [ -e "$STAGE/$base" ]; then
        echo "gen-docs: two src files share the basename '$base'; a flat staging" >&2
        echo "directory cannot hold both. Give qDoc one invocation per" >&2
        echo "subdirectory instead, or rename one of them." >&2
        exit 1
    fi
    cp "$f" "$STAGE/$base"
    EXPECTED=$((EXPECTED + 1))
done < <(find src -name '*.q' | sort)

echo "Documenting $EXPECTED module(s) from src/**/*.q"
java -cp "$QSTUDIO_JAR" com.timestored.qdoc.QDocMain build/docs "$STAGE"

# Self-verifying: qDoc emits one page per source file, so a count mismatch
# means it silently skipped something. This is what turns the src/ split from
# an unverified risk into a build-time assertion.
GENERATED=$(find build/docs -name '*.q.html' | wc -l | tr -d ' ')
if [ "$GENERATED" -ne "$EXPECTED" ]; then
    echo "" >&2
    echo "gen-docs: expected $EXPECTED module pages, qDoc produced $GENERATED." >&2
    echo "Documentation is incomplete - do not publish it. Inspect build/docs/." >&2
    exit 1
fi
echo "Verified: $GENERATED module page(s) generated, one per source file."

echo "Docs generated at build/docs/index.html"
