#!/bin/bash
# ship.sh PR WORKTREE - attest if needed, push, wait for CI, merge.
#
#   scripts/dev/ship.sh 1697 issue-1696      # .claude/worktrees/issue-1696
#   scripts/dev/ship.sh 1697 /abs/path/to/worktree
#
# Merges master into the branch ONLY when it must (#1687): the branch conflicts
# with master, or the KDB-X attestation check refuses the head against the
# current master because master changed a path the gates judge. A branch that
# is merely behind merges as it is, and a branch whose own new commit is not
# attested yet is attested without merging master. Before merging, it asks the
# check again against the latest master.
#
# Green means "Repository checks" - the job that needs every CI job - has
# itself passed, not merely that nothing failed: before CI has created its
# jobs, the checks reported so far can all be green (#1697 merged that way).
# Repeats up to three times when master moves underneath. One line per step.
#
# A conflict is judged locally, with `git merge-tree` against origin/master,
# not by GitHub's `mergeable`: that reads UNKNOWN for minutes after master
# moves, and a conflicting PR runs no CI, so a script trusting it waited 40
# minutes for checks that never start. The wait for CI stops the same way the
# moment master moves into a conflict.
# After the merge it starts the whole-suite coverage run on master's new tip
# in the background (scripts/dev/master_coverage.py, #1719).
set -u
pr=$1
wt=$2
root=$(cd "$(dirname "$0")/../.." && git rev-parse --path-format=absolute --git-common-dir)/..
[ -d "$wt" ] || wt="$root/.claude/worktrees/$wt"
cd "$wt" || { echo "$pr: no worktree $wt"; exit 1; }
logs=$(mktemp -d "${TMPDIR:-/tmp}/ship-$pr.XXXXXX")
# An explicit refspec, because agent branches are --no-track (#2102), and a
# timeout longer than the pre-push hooks, which take 8-25 minutes. A refused or
# timed-out push fails the ship, and so does a remote head that is not HEAD
# afterwards: CI on the old head would only fail on its missing attestation
# (#2217).
push() {
  local branch rc=0
  branch=$(git branch --show-current)
  timeout 3600 git -c credential.helper= -c 'credential.helper=!gh auth git-credential' \
    push -q origin "HEAD:refs/heads/$branch" > "$logs/push.log" 2>&1 || rc=$?
  if [ "$rc" != "0" ]; then
    echo "$pr: push failed (exit $rc, log $logs/push.log):"; grep -E "FAILED|error|rejected" "$logs/push.log" | head -5
    return 1
  fi
  [ "$(git ls-remote origin "refs/heads/$branch" | cut -f1)" = "$(git rev-parse HEAD)" ] \
    || { echo "$pr: push exited 0 but origin/$branch is not HEAD"; return 1; }
}
gate_ok() { uv run python scripts/dev/kdbx_attestation.py check origin/master HEAD > "$logs/gate.log" 2>&1; }
# merge-tree exits 1 for a conflict, 0 for a clean merge, >1 for an error
conflicted() {
  local rc=0
  git merge-tree --write-tree HEAD origin/master > /dev/null 2>&1 || rc=$?
  [ "$rc" = "1" ]
}
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
# this is where the rest of the tree is judged. Started on the project's
# interpreter, not PATH's python3, which may be too old to parse it (#2540);
# its state lives in the git directory, so it outlives this worktree; --if-new
# and its lock keep two merges from running it twice. kdbx_attestation.py prints
# its last verdict.
master_coverage() {
  local state
  state="$(git rev-parse --path-format=absolute --git-common-dir)/uqf-master-coverage"
  mkdir -p "$state"
  nohup uv run --project "$wt" python "$wt/scripts/dev/master_coverage.py" --if-new >> "$state/log" 2>&1 < /dev/null &
  echo "$pr: whole-suite coverage of master started in the background (log: $state/log)"
}

for _ in 1 2 3; do
  git fetch -q origin
  changed=0
  if conflicted || ! gate_ok; then
    if [ "$(git rev-list --count HEAD..origin/master)" != "0" ] && { conflicted || master_moved_judged; }; then
      echo "$pr: must merge master: $(tail -1 "$logs/gate.log" | cut -c1-160)"
      git merge --no-edit -q origin/master > "$logs/merge.log" 2>&1 \
        || { echo "$pr: merge with master failed (conflict?)"; tail -5 "$logs/merge.log"; exit 1; }
      echo "$pr: merged master"
      changed=1
    fi
    if ! gate_ok; then
      uv run python scripts/dev/kdbx_attestation.py attest > "$logs/attest.log" 2>&1 \
        || { echo "$pr: attestation failed"; tail -5 "$logs/attest.log"; exit 1; }
      echo "$pr: attested"
      changed=1
    fi
  fi
  upstream="origin/$(git branch --show-current)"
  if [ "$changed" = "1" ] || [ "$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo 1)" != "0" ]; then
    push || exit 1
    echo "$pr: pushed"
    sleep 20
  fi
  st=pending
  for i in $(seq 1 80); do
    st=$(ci_state)
    [ "$st" = "pending" ] || [ -z "$st" ] || break
    if [ $((i % 4)) = 0 ]; then
      git fetch -q origin
      conflicted && { st=conflict; break; }
    fi
    sleep 30
  done
  [ "$st" = "conflict" ] && { echo "$pr: master moved into a conflict while waiting for CI, again"; continue; }
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
