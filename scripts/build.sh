#!/usr/bin/env bash
# Builds a distributable release artifact for the uqf q/kdb+ library:
# runs the full test suite (the build fails if it doesn't pass), stages
# the runtime library (src/, the optional log4q/q-doc vendored deps,
# tests/, scripts/, docs/, README/LICENSE) into dist/uqf-<version>/, and
# packages it as both dist/uqf-<version>.tar.gz and dist/uqf-<version>.zip
# with sha256 checksums alongside.
#
# lib/kdb-parquet is deliberately excluded - per its own NOTICE.md and
# README.md's Layout section it's vendored for reference only, "NOT
# loaded by src/init.q or anything else in this repo", not part of the
# uqf library itself. python/uqf-client is its own separately-versioned
# package (own pyproject.toml/uv build) and is excluded too.
#
# Usage:
#   scripts/build.sh [version]
#     version defaults to `git describe --tags --always --dirty`, or
#     today's date (YYYY-MM-DD) if this isn't a git checkout.
#
# Requires a q/kdb+ interpreter on PATH (or ./q, PeachQ, at the repo
# root) to run the test suite - see README's Requirements section.

set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    VERSION="$(git describe --tags --always --dirty 2>/dev/null || true)"
    if [ -z "$VERSION" ]; then
        VERSION="$(date +%Y-%m-%d)"
    fi
fi
# git tags in this repo look like stable/2026-08-20 - "/" isn't safe in a
# package/file name (it'd nest dist/uqf-stable/2026-08-20... instead of a
# single flat uqf-stable-2026-08-20 package), so flatten it.
VERSION="${VERSION//\//-}"

echo "==> Building uqf $VERSION"

echo "==> Running test suite"
if [ -x ./q ]; then
    ./q tests/run_tests.q
elif command -v q >/dev/null 2>&1; then
    q tests/run_tests.q
else
    echo "error: no q/kdb+ interpreter found (no ./q, and 'q' not on PATH)." >&2
    echo "       see README's Requirements section to install one." >&2
    exit 1
fi

DIST_DIR="$REPO_ROOT/dist"
PKG_NAME="uqf-$VERSION"
STAGE_DIR="$DIST_DIR/$PKG_NAME"

echo "==> Staging $STAGE_DIR"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

cp -R src "$STAGE_DIR/src"
cp -R tests "$STAGE_DIR/tests"
cp -R scripts "$STAGE_DIR/scripts"
cp -R docs "$STAGE_DIR/docs"
mkdir -p "$STAGE_DIR/lib"
cp lib/log4q.q "$STAGE_DIR/lib/log4q.q"
cp lib/LICENSE-log4q "$STAGE_DIR/lib/LICENSE-log4q"
cp -R lib/q-doc "$STAGE_DIR/lib/q-doc"
cp README.md LICENSE "$STAGE_DIR/"
echo "$VERSION" > "$STAGE_DIR/VERSION"

# the packaged copy shouldn't carry its own dist/ or local interpreter
# binaries some contributors keep alongside the repo (see .gitignore)
rm -rf "$STAGE_DIR/scripts/../dist" 2>/dev/null || true
find "$STAGE_DIR" -name "*.log" -delete
find "$STAGE_DIR" -name ".DS_Store" -delete

echo "==> Packaging"
( cd "$DIST_DIR" && tar czf "$PKG_NAME.tar.gz" "$PKG_NAME" )
( cd "$DIST_DIR" && zip -rq "$PKG_NAME.zip" "$PKG_NAME" )
rm -rf "$STAGE_DIR"

echo "==> Checksums"
( cd "$DIST_DIR" && shasum -a 256 "$PKG_NAME.tar.gz" "$PKG_NAME.zip" | tee "$PKG_NAME.sha256" )

echo ""
echo "Built:"
echo "  $DIST_DIR/$PKG_NAME.tar.gz"
echo "  $DIST_DIR/$PKG_NAME.zip"
echo "  $DIST_DIR/$PKG_NAME.sha256"
