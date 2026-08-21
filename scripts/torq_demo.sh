#!/bin/bash
# torq_demo.sh - bridges lib/torq (the vendored TorQ framework) and
# lib/torq-finance-starter-pack (the vendored app built on top of it) into
# one runnable demo, without touching either vendored tree.
#
# TorQ's own convention (see lib/torq/installtorqapp.sh) is to point a set
# of env vars - TORQHOME at the framework, TORQAPPHOME at the app, plus a
# writable data root - at whichever directories you like; appconfig/
# process.csv already references KDBCODE/KDBAPPCODE/KDBHDB/etc as
# ${VAR}-style placeholders (see torq.sh's use of envsubst), so no physical
# merge of the two lib/ trees is needed. This script just sets those vars
# to point at uqf's two lib/ dirs, points every writable path (logs,
# tickerplant logs, wdb, and a copy of the sample hdb/dqe data) at a
# gitignored scripts/output/torq-demo/ directory instead of into lib/, and
# execs lib/torq/torq.sh with whatever arguments you passed in.
#
# Usage (from anywhere - resolves its own location):
#   scripts/torq_demo.sh start all      # start every startwithall=1 process
#   scripts/torq_demo.sh summary        # one-line-per-process status table
#   scripts/torq_demo.sh stop all       # stop everything
#   scripts/torq_demo.sh clean          # wipe scripts/output/torq-demo/
#
# See docs/torq-demo.md for the full writeup (ports, monitor UI, qcon,
# troubleshooting, the community-edition process limits this pack already
# works around).

set -euo pipefail

if [ "-bash" = "$0" ]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  script_dir="$(cd "$(dirname "$0")" && pwd)"
fi
repo_root="$(cd "$script_dir/.." && pwd)"

export TORQHOME="$repo_root/lib/torq"
export TORQAPPHOME="$repo_root/lib/torq-finance-starter-pack"
torq_data="$repo_root/scripts/output/torq-demo"

if [ ! -d "$TORQHOME" ] || [ ! -f "$TORQHOME/torq.q" ]; then
  echo "ERROR: $TORQHOME not found or missing torq.q - is lib/torq vendored?" >&2
  exit 1
fi
if [ ! -d "$TORQAPPHOME" ] || [ ! -f "$TORQAPPHOME/database.q" ]; then
  echo "ERROR: $TORQAPPHOME not found or missing database.q - is lib/torq-finance-starter-pack vendored?" >&2
  exit 1
fi

for tool in envsubst rlwrap; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: '$tool' not found on PATH - torq.sh needs it (macOS: brew install gettext rlwrap)" >&2
    exit 1
  fi
done

if [ "${1:-}" = "clean" ]; then
  echo "Removing $torq_data"
  rm -rf "$torq_data"
  exit 0
fi

# Bootstrap the writable data dir on first run: copy (never symlink - these
# processes write into hdb/wdb/tplogs) the app's sample hdb/dqe data once,
# so nothing ever gets written into the vendored lib/ tree.
if [ ! -d "$torq_data/hdb" ]; then
  echo "Bootstrapping $torq_data (first run) - copying sample hdb/dqe data..."
  mkdir -p "$torq_data"
  cp -R "$TORQAPPHOME/hdb" "$torq_data/hdb"
  cp -R "$TORQAPPHOME/dqe" "$torq_data/dqe"
fi
mkdir -p "$torq_data/logs" "$torq_data/tplogs" "$torq_data/wdbhdb"

kdb_base_port="${TORQ_DEMO_PORT:-6010}"

# torq.sh unconditionally sources $SETENV (defaulting to lib/torq/setenv.sh,
# which would overwrite TORQAPPHOME/TORQPROCESSES/etc back to lib/torq's own
# defaults) - so generate our own setenv.sh into the gitignored data dir,
# pointing everything at uqf's two lib/ trees plus this data dir, and point
# SETENV at it instead.
generated_setenv="$torq_data/setenv.sh"
cat >"$generated_setenv" <<EOF
export TORQHOME="$TORQHOME"
export TORQAPPHOME="$TORQAPPHOME"
export TORQDATA="$torq_data"
export KDBCONFIG="$TORQHOME/config"
export KDBCODE="$TORQHOME/code"
export KDBAPPCONFIG="$TORQAPPHOME/appconfig"
export KDBAPPCODE="$TORQAPPHOME/code"
export KDBLIB="$TORQHOME/lib"
export KDBTESTS="$TORQHOME/tests"
export KDBLOG="$torq_data/logs"
export KDBHDB="$torq_data/hdb"
export KDBWDB="$torq_data/wdbhdb"
export KDBTPLOG="$torq_data/tplogs"
export KDBDQCDB="$torq_data/dqe/dqcdb/database"
export KDBDQEDB="$torq_data/dqe/dqedb/database"
export KDBBASEPORT="$kdb_base_port"
export KDBSTACKID="-stackid \${KDBBASEPORT}"
export TORQPROCESSES="$TORQAPPHOME/appconfig/process.csv"
export RLWRAP="rlwrap"
export QCON="qcon"
export QCMD="q"
EOF

export SETENV="$generated_setenv"

if [ $# -eq 0 ]; then
  echo "torq_demo.sh - bridges lib/torq + lib/torq-finance-starter-pack (see docs/torq-demo.md)"
  echo
fi

exec "$TORQHOME/torq.sh" "$@"
