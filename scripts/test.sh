#!/usr/bin/env bash
# test.sh - the lane dispatcher ETL-21 names.
#
# ETL-21: "Use the suite that matches the changed layer: q-unit for q
# behaviour, q-backfill-process for bounded process behaviour, and the
# focused Python suites for orchestration."
#
# D5 deliberately did NOT build this when the only lane was q-unit: a
# dispatcher with one option is worse than no dispatcher. There are now three
# genuinely different lanes, so it earns its place.
#
# The lanes differ in what they PROVE, which is the whole point of choosing
# between them:
#
#   q-unit               deterministic q behaviour, in one process, no I/O
#                        beyond a temp status directory. Fast, hermetic, and
#                        the only lane the commit hook runs.
#   q-backfill-process   the bounded lifecycle end to end - locks, resumption
#                        and coverage - which needs a real filesystem and a
#                        real second process to be worth anything.
#   python               orchestration and the BFF.
#   smoke                ETL-20's live external check. Explicitly NOT part of
#                        any other lane: the deterministic suite proves local
#                        behaviour, not that a configured external service is
#                        reachable or compatible, and folding it in would
#                        make every local run depend on a remote host being
#                        up.
#
# Exits non-zero on the first failing lane.

set -euo pipefail
cd "$(dirname "$0")/.."

# KDB-X. This tree targets it alone, so there is no fallback interpreter -
# a suite that passed on something it is not verified against is worse than
# one that does not run.
Q="${Q:-$HOME/.kx/bin/q}"
export QHOME="${QHOME:-$HOME/.kx}"

usage() {
    cat <<'USAGE'
usage: scripts/test.sh <lane>

lanes:
  q-unit              deterministic qUnit suite (hermetic, fast)
  q-metatables-hdb    metatable queries against a temporary partitioned HDB
  q-backfill-process  bounded worker lifecycle against a real filesystem
  python              orchestrator and frontend suites
  smoke               ETL-20 live external metadata check (needs a live stack)
  all                 every lane except smoke - see ETL-20

ETL-21: run the lane matching the layer you changed. `all` is for a release,
not for an edit.
USAGE
}

lane_q_unit() {
    echo "== q-unit: deterministic qUnit suite =="
    "$Q" tests/run_tests.q
}

lane_q_backfill_process() {
    echo "== q-backfill-process: bounded lifecycle on a real filesystem =="
    # A separate status directory per run, so this lane never reads state a
    # previous run left behind - which would make a stale lock look like a
    # passing single-instance test.
    UQFSTATUSDIR="$(mktemp -d)" "$Q" tests/q/run_backfill_process.q
}

lane_q_metatables_hdb() (
    set -euo pipefail
    metadata_test_dir="$(mktemp -d)"
    trap 'rm -rf -- "$metadata_test_dir"' EXIT
    "$Q" tests/q/run_metatables_hdb.q "$metadata_test_dir"
)

lane_python() {
    echo "== python: orchestrator and frontend =="
    uv run pytest -q
}

lane_smoke() {
    echo "== smoke: ETL-20 live external metadata check =="
    "$Q" tests/q/smoke_external_metadata.q
}

case "${1:-}" in
    q-unit)             lane_q_unit ;;
    q-backfill-process) lane_q_backfill_process ;;
    q-metatables-hdb)    lane_q_metatables_hdb ;;
    python)             lane_python ;;
    smoke)              lane_smoke ;;
    all)                lane_q_unit; lane_q_backfill_process; lane_q_metatables_hdb; lane_python ;;
    ""|-h|--help)       usage; exit 0 ;;
    *)                  echo "unknown lane: $1" >&2; usage >&2; exit 2 ;;
esac
