"""The preflight checks a push runs on the server before it changes anything
(deploy/server.py, Server.preflight): read-only, and safe under --dry-run.

They are the minimum host contract docs/guides/deploy.md writes down: what a
server must provide even when every bundleable component is bundled - the
tools the push's own steps and TorQ's torq.sh run, a python3 for the push's
checks, and a glibc at or above the release's wheels' floor, reported here
and judged by artifact.compatible. A bundled q or Python skips the part that
would find the server's own (`body`).
"""

from __future__ import annotations

import shlex

from uqs.interpreter import REMOVED_ENV


def _removed_checks() -> str:
    """A refusal for each variable that chose an interpreter before #1753,
    set in the server account's environment (its profile, say): the release's
    own uqs would refuse it at its first command, so the push refuses it
    before it changes anything, naming what replaces it."""
    return "".join(
        f'[ -z "${{{name}+set}}" ] || fail '
        + shlex.quote(f"{name} is set on the server, and is no longer read - {hint}; unset it")
        + "\n"
        for name, hint in REMOVED_ENV.items()
    )


#: The preflight checks, run on the server. Read-only - safe under --dry-run.
#: Each failure says what is missing on stderr and exits 1; the facts the
#: caller decides on (data present, a current release, a held lock) come
#: back on stdout as name=value lines.
PREFLIGHT = (
    r"""
fail() { echo "$*" >&2; exit 1; }
"""
    + _removed_checks()
    + r"""echo "user=$(id -un)"
if [ -n "$expected_user" ] && [ "$(id -un)" != "$expected_user" ]; then
  fail "running as $(id -un), not $expected_user"
fi
if [ -e "$dest" ]; then
  test -w "$dest" || fail "destination $dest is not writable"
else
  test -w "$(dirname "$dest")" || fail "cannot create $dest: its parent is not writable"
fi
command -v uv >/dev/null || fail "uv is not on PATH"
py=$(uv python find "$python" 2>/dev/null) ||
  fail "uv finds no Python $python - the release's wheels need it"
echo "python=$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "os=$(uname -s)"
echo "arch=$(uname -m)"
# The host contract (docs/guides/deploy.md): the push's own steps (tar and
# the gzip it unpacks with, timeout, find, sed, python3) and what torq.sh runs -
# its start (awk, grep, pgrep, hostname, nohup, envsubst) and stop (lsof) -
# and rlwrap, which uqs checks for before torq.sh is run.
for tool in bash envsubst rlwrap tar gzip timeout python3 awk sed grep pgrep lsof hostname nohup \
  find; do
  command -v "$tool" >/dev/null || fail "$tool is not on PATH - the release needs it"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' ||
  fail "python3 is older than 3.8 - the push's checks on the server need 3.8 or later"
hostname -I >/dev/null 2>&1 ||
  fail "hostname -I fails - torq.sh matches a process's host against this host's addresses with it"
echo "glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null | sed -n 's/^glibc //p')"
me=$(id -un)
# QHOME: --qhome, else this account's own $QHOME - never the caller's.
if [ -n "$qhome_flag" ]; then qhome_eff=$qhome_flag; qhome_from="--qhome"
elif [ -n "${QHOME:-}" ]; then qhome_eff=$QHOME; qhome_from="$me's QHOME"
else fail "QHOME is not set for $me and --qhome was not given -" \
  "pass --qhome <the directory q finds its licence in>"
fi
test -d "$qhome_eff" || fail "QHOME $qhome_eff ($qhome_from) is not a directory"
# QCMD: --qcmd, else $QCMD, else q on this account's PATH. A name or a path,
# never a command line: it is run as one word, quoted.
if [ -n "$qcmd_flag" ]; then qcmd_want=$qcmd_flag; qcmd_from="--qcmd"
elif [ -n "${QCMD:-}" ]; then qcmd_want=$QCMD; qcmd_from="$me's QCMD"
else qcmd_want=q; qcmd_from="q on $me's PATH"
fi
case "$qcmd_want" in
  /*) test -f "$qcmd_want" && test -x "$qcmd_want" ||
        fail "QCMD $qcmd_want ($qcmd_from) is not an executable file"
      qcmd_eff=$qcmd_want;;
  */*) fail "QCMD $qcmd_want ($qcmd_from) is a relative path - give an absolute one";;
  *[[:space:]]*) fail "QCMD '$qcmd_want' ($qcmd_from) is not an executable name -" \
                   "a command with arguments is not supported; pass --qcmd";;
  *) qcmd_eff=$(command -v -- "$qcmd_want") ||
       fail "QCMD $qcmd_want ($qcmd_from) is not on $me's PATH - pass --qcmd"
     case "$qcmd_eff" in
       /*) ;;
       *) fail "QCMD $qcmd_want ($qcmd_from) is a shell alias or function, not an executable";;
     esac;;
esac
case "$qhome_eff$qcmd_eff" in *$'\n'*) fail "QHOME or QCMD holds a newline";; esac
export QHOME="$qhome_eff"
echo "qhome=$qhome_eff"
echo "qhome_from=$qhome_from"
echo "qcmd=$qcmd_eff"
echo "qcmd_from=$qcmd_from"
probe=$(mktemp -d)
printf '%s\n' '-1 "DEPLOY_Q_OK ",string .z.K; exit 0' > "$probe/probe.q"
out=$(timeout "$probe_timeout" "$qcmd_eff" "$probe/probe.q" -q 2>&1 || true)
rm -rf "$probe"
case "$out" in
  *DEPLOY_Q_OK*)
    qversion=$(printf '%s\n' "$out" | sed -n 's/.*DEPLOY_Q_OK \([0-9.]*\).*/\1/p' | head -1)
    echo "qversion=$qversion";;
  *) fail "$qcmd_eff did not run a script with QHOME=$qhome_eff - is it licensed?" \
          "$(echo "$out" | tail -3)";;
esac
if [ -n "$odbc_home" ]; then
  test -f "$odbc_home/current/env.sh" ||
    fail "no ODBC setup at $odbc_home - run \`uqs setup odbc install\` there first"
fi
if [ -n "$torq_launcher" ]; then
  test -f "$torq_launcher" || fail "no TorQ launcher $torq_launcher"
  test -x "$torq_launcher" || fail "the TorQ launcher $torq_launcher is not executable"
fi
if [ -n "$torq_home" ]; then
  test -f "$torq_home/torq.q" || fail "no torq.q in $torq_home"
  if [ -z "$torq_launcher" ]; then
    test -f "$torq_home/torq.sh" || fail "no torq.sh in $torq_home - pass --torq-launcher" \
      "if this site keeps its launcher elsewhere"
  fi
fi
if [ -n "$torq_app_home" ]; then
  for f in database.q appconfig/process.csv; do
    test -f "$torq_app_home/$f" || fail "no $f in $torq_app_home"
  done
fi
if [ -d "$data" ]; then echo "data=present"; else echo "data=absent"; fi
if [ -L "$current" ]; then echo "current=$(basename "$(readlink "$current")")"; fi
if [ -n "$release_dir" ] && [ -e "$release_dir" ]; then echo "release_exists=yes"; fi
if [ -d "$lock" ]; then
  hb=$(cat "$lock/heartbeat" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || true)
  case "$hb" in ''|*[!0-9]*) age=unknown;; *) age=$(( $(date +%s) - hb ));; esac
  echo "locked=$(head -n 1 "$lock/owner" 2>/dev/null || echo unknown), last beat ${age}s ago"
fi
"""
)

#: Where PREFLIGHT resolves and runs the server's q: from the first line to
#: the second, exclusive. A push with a bundled q leaves it out.
Q_RESOLUTION = ("# QHOME: --qhome", 'if [ -n "$odbc_home" ]')
#: Where it finds the server's Python with uv. A push with a bundled Python
#: leaves it out: the release's .venv is made from the runtime's interpreter.
PY_RESOLUTION = ("command -v uv", 'echo "os=')


def body(q_bundled: bool, python_bundled: bool) -> str:
    """PREFLIGHT for a push, without the resolution of each bundled component."""
    text = PREFLIGHT
    for bundled, (first, end) in ((q_bundled, Q_RESOLUTION), (python_bundled, PY_RESOLUTION)):
        if bundled:
            text = text[: text.index(first)] + text[text.index(end) :]
    return text
