#!/usr/bin/env bash
# ODBC from q on Apple silicon, by running q as x86_64 under Rosetta.
#
# KX's q client for ODBC is published for Linux and Windows. The only macOS
# build (KxSystems/kdb m64/odbc.so) is x86_64 and links
# /usr/lib/libodbc.2.dylib, which current macOS does not ship and SIP does not
# let anyone add. KDB-X's q is a universal binary, so this works around both:
#
#   1. build unixODBC as x86_64 into a local prefix - no second Homebrew,
#      nothing installed system-wide;
#   2. copy KX's odbc.so and rewrite its libodbc load path to that prefix,
#      then re-sign it ad hoc, since the rewrite invalidates the signature;
#   3. fetch DuckDB's universal ODBC driver and register it in a local
#      odbcinst.ini;
#   4. assemble an overlay QHOME - links to ~/.kx plus m64/odbc.so and
#      odbc.k - so ~/.kx itself is never modified.
#
# A workaround for a development machine, not a deployment path: on Linux the
# same source runs with the platform's own unixODBC and KX's l64/odbc.so.
#
# Everything lands in <main checkout>/output/odbc-x86_64 (gitignored).
#
# Usage:
#   scripts/dev/odbc_rosetta.sh setup          build/fetch everything (idempotent)
#   scripts/dev/odbc_rosetta.sh q <q args...>  run q under Rosetta with ODBC wired up
#   scripts/dev/odbc_rosetta.sh databento <q args...>
#       as `q`, with UQF_SOURCE_CRED_DATABENTO_MBP10 pointing at
#       output/duckdb/databento.duckdb (build it with
#       scripts/dev/dump_databento_duckdb.py)
#
# Requires: Rosetta 2, Xcode command line tools, gh (authenticated), curl,
# KDB-X at ~/.kx.

set -euo pipefail

UNIXODBC_TAG="v2.3.14"
DUCKDB_ODBC_TAG="v1.5.5.0"
KX_KDB_RAW="https://raw.githubusercontent.com/KxSystems/kdb/master"

# The MAIN checkout's root, so a worktree shares one build.
MAIN_ROOT="$(dirname "$(git -C "$(dirname "$0")" rev-parse --path-format=absolute --git-common-dir)")"
PREFIX="$MAIN_ROOT/output/odbc-x86_64"
QHOME_OVERLAY="$PREFIX/qhome"
KX_HOME="${HOME}/.kx"

setup_unixodbc() {
    if [ -f "$PREFIX/lib/libodbc.2.dylib" ]; then
        echo "unixODBC: already built"
        return
    fi
    echo "unixODBC: building ${UNIXODBC_TAG} for x86_64 (a few minutes)"
    mkdir -p "$PREFIX/src"
    gh release download "$UNIXODBC_TAG" -R lurcher/unixODBC -p "*.tar.gz" -D "$PREFIX/src" --clobber
    tar xzf "$PREFIX/src/unixODBC-${UNIXODBC_TAG#v}.tar.gz" -C "$PREFIX/src"
    (
        cd "$PREFIX/src/unixODBC-${UNIXODBC_TAG#v}"
        arch -x86_64 /bin/bash -c "
            CFLAGS='-arch x86_64 -O2' LDFLAGS='-arch x86_64' ./configure \
                --prefix='$PREFIX' --host=x86_64-apple-darwin --build=x86_64-apple-darwin \
                --disable-gui --enable-iconv > '$PREFIX/src/configure.log' 2>&1 &&
            make -j8 > '$PREFIX/src/make.log' 2>&1 &&
            make install > '$PREFIX/src/install.log' 2>&1"
    )
}

setup_duckdb_driver() {
    if [ -f "$PREFIX/duckdb/libduckdb_odbc.dylib" ]; then
        echo "DuckDB ODBC driver: already present"
    else
        echo "DuckDB ODBC driver: fetching ${DUCKDB_ODBC_TAG}"
        mkdir -p "$PREFIX/src" "$PREFIX/duckdb"
        gh release download "$DUCKDB_ODBC_TAG" -R duckdb/duckdb-odbc -p "duckdb_odbc-osx-universal.zip" -D "$PREFIX/src" --clobber
        unzip -o -q "$PREFIX/src/duckdb_odbc-osx-universal.zip" -d "$PREFIX/duckdb"
    fi
    printf '[DuckDB]\nDescription = DuckDB ODBC driver (universal, loaded x86_64 under Rosetta)\nDriver = %s\n' \
        "$PREFIX/duckdb/libduckdb_odbc.dylib" > "$PREFIX/odbcinst.ini"
}

setup_qhome() {
    mkdir -p "$QHOME_OVERLAY/m64"
    for entry in "$KX_HOME"/*; do
        ln -sfn "$entry" "$QHOME_OVERLAY/$(basename "$entry")"
    done
    if [ ! -f "$QHOME_OVERLAY/m64/odbc.so" ]; then
        echo "KX odbc.so: fetching and relinking to $PREFIX/lib"
        # Downloaded straight into the overlay, never into a working
        # directory: q's 2: searches the current directory first, and an
        # unpatched copy there is loaded instead of this one.
        curl -sSfL -o "$QHOME_OVERLAY/m64/odbc.so" "$KX_KDB_RAW/m64/odbc.so"
        install_name_tool -change /usr/lib/libodbc.2.dylib "$PREFIX/lib/libodbc.2.dylib" "$QHOME_OVERLAY/m64/odbc.so"
        codesign -f -s - "$QHOME_OVERLAY/m64/odbc.so"
    fi
    curl -sSfL -o "$QHOME_OVERLAY/odbc.k" "$KX_KDB_RAW/c/odbc.k"
}

require_setup() {
    if [ ! -f "$QHOME_OVERLAY/m64/odbc.so" ] || [ ! -f "$PREFIX/odbcinst.ini" ]; then
        echo "ODBC under Rosetta is not set up - run: scripts/dev/odbc_rosetta.sh setup" >&2
        exit 1
    fi
}

run_q() {
    require_setup
    QHOME="$QHOME_OVERLAY" ODBCSYSINI="$PREFIX" exec arch -x86_64 "$QHOME_OVERLAY/bin/q" "$@"
}

case "${1:-}" in
    setup)
        setup_unixodbc
        setup_duckdb_driver
        setup_qhome
        echo "ready: scripts/dev/odbc_rosetta.sh q <args>"
        ;;
    q)
        shift
        run_q "$@"
        ;;
    databento)
        shift
        db="$MAIN_ROOT/output/duckdb/databento.duckdb"
        if [ ! -f "$db" ]; then
            echo "no $db - build it with: uv run scripts/dev/dump_databento_duckdb.py" >&2
            exit 1
        fi
        export UQF_SOURCE_CRED_DATABENTO_MBP10="DRIVER=DuckDB;Database=$db;access_mode=READ_ONLY"
        run_q "$@"
        ;;
    *)
        sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
        exit 2
        ;;
esac
