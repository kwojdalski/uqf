#!/bin/bash
# ship.sh PR WORKTREE - attest if needed, push, wait for CI, merge.
#
#   scripts/dev/ship.sh 1697 issue-1696      # .claude/worktrees/issue-1696
#   scripts/dev/ship.sh 1697 /abs/path/to/worktree
#
# Merges master into the branch ONLY when it must (#1687): GitHub reports a
# conflict, or the KDB-X attestation check refuses the head against the
# current master because master changed a path the gates judge. A branch that
# is merely behind merges as it is, and a branch whose own new commit is not
# attested yet is attested without merging master. Before merging, it asks the
# check again against the latest master.
#
# Green means "Repository checks" - the job that needs every CI job - has
# itself passed, not merely that nothing failed: before CI has created its
# jobs, the checks reported so far can all be green (#1697 merged that way).
# Repeats up to three times when master moves underneath. One line per step.
# After the merge it starts the whole-suite coverage run on master's new tip
# in the background (scripts/dev/master_coverage.py, #1719).
set -u
pr=$1
wt=$2
root=$(cd "$(dirname "$0")/../.." && git rev-parse --path-format=absolute --git-common-dir)/..
[ -d "$wt" ] || wt="$root/.claude/worktrees/$wt"
cd "$wt" || { echo "$pr: no worktree $wt"; exit 1; }
logs=$(mktemp -d "${TMPDIR:-/tmp}/ship-$pr.XXXXXX")
push() { timeout 120 git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push -q 2>&1 | tail -1; }
gate_ok() { python3 scripts/gates/check_kdbx_attestation.py origin/master HEAD > "$logs/gate.log" 2>&1; }
conflicted() { [ "$(gh pr view "$pr" --json mergeable --jq .mergeable 2>/dev/null)" = "CONFLICTING" ]; }
master_moved_judged() { grep -q "the base has since changed\|does not contain the base's tip, so" "$logs/gate.log"; }
ci_state() {
  # shellcheck disable=SC2016 # jq's own $variables
  gh pr checks "$pr" --json name,bucket --jq '
    [.[].bucket] as $b
    | (map(select(.name == "Repository checks")) | .[0].bucket // "missing") as $agg
    | if any($b[]; . == "fail") then "fail"
      elif $agg == "pass" and (any($b[]; . == "pending") | not) then "pass"
      else "pending" end' 2>/dev/null
}

# The whole-suite coverage run on master's new tip (#1719), in the background:
# an attestation judges coverage only in the modules its change reaches, and
# this is where the rest of the tree is judged. Started with python3, from the
# git directory, so it outlives this worktree; --if-new and its lock keep two
# merges from running it twice. attest_kdbx.py prints its last verdict.
master_coverage() {
  local state
  state="$(git rev-parse --path-format=absolute --git-common-dir)/uqf-master-coverage"
  mkdir -p "$state"
  nohup python3 "$wt/scripts/dev/master_coverage.py" --if-new >> "$state/log" 2>&1 < /dev/null &
  echo "$pr: whole-suite coverage of master started in the background (log: $state/log)"
}

for _ in 1 2 3; do
  git fetch -q origin
  changed=0
  if conflicted || ! gate_ok; then
    if [ "$(git rev-list --count HEAD..origin/master)" != "0" ] && { conflicted || master_moved_judged; }; then
      echo "$pr: must merge master: $(tail -1 "$logs/gate.log" | cut -c1-160)"
      env -u UQF_PEACHQ git merge --no-edit -q origin/master > "$logs/merge.log" 2>&1 \
        || { echo "$pr: merge with master failed (conflict?)"; tail -5 "$logs/merge.log"; exit 1; }
      echo "$pr: merged master"
      changed=1
    fi
    if ! gate_ok; then
      env -u UQF_PEACHQ uv run python scripts/dev/attest_kdbx.py > "$logs/attest.log" 2>&1 \
        || { echo "$pr: attestation failed"; tail -5 "$logs/attest.log"; exit 1; }
      echo "$pr: attested"
      changed=1
    fi
  fi
  upstream="origin/$(git branch --show-current)"
  if [ "$changed" = "1" ] || [ "$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo 1)" != "0" ]; then
    push
    echo "$pr: pushed"
    sleep 20
  fi
  st=pending
  for _ in $(seq 1 80); do
    st=$(ci_state)
    [ "$st" = "pending" ] || [ -z "$st" ] || break
    sleep 30
  done
  echo "$pr: checks $st"
  [ "$st" = "pass" ] || { echo "$pr: not green, not merging (logs: $logs)"; exit 1; }
  git fetch -q origin
  gate_ok || { echo "$pr: master moved a judged path meanwhile, again"; continue; }
  conflicted && { echo "$pr: conflicts with master now, again"; continue; }
  if gh pr merge "$pr" --merge > /dev/null 2>&1; then
    echo "$pr: MERGED"
    master_coverage
    exit 0
  fi
  echo "$pr: merge refused, retrying"
done
echo "$pr: gave up after 3 attempts (logs: $logs)"
exit 1
