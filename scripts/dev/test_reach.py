"""What each Python test reads and starts, beside testmon's record (#1879).

testmon records the Python each test EXECUTES, and selects by it. What it
cannot see is everything else a test depends on: a file it reads as data (a
CSV, a document, a .q read as text), a directory it lists (a gate that globs
src/), and a process it starts (a q, a gate run as a script, a shell starting
a TorQ stack). That is what decides which tests a commit of anything but
Python reaches, and this records it the way testmon records its own: as the
tests run, per test, into a file at the repository root.

    uv run pytest --record-reach .testreach.json ...   # what the python-tests hook passes

`--record-reach PATH` merges into PATH, replacing what it held for each test
that ran. Under xdist each worker writes its part beside PATH and the
controller merges them.

WHO IS CHARGED WITH WHAT IS SEEN (an `owner`):

* the test running - its setup, call and teardown;
* the fixture being set up, while it is: a session fixture's q starts once,
  under whichever test needs it first, so it is the FIXTURE's, and every
  test whose fixture closure holds that fixture inherits it;
* the test module, while it is imported at collection.

A test reaches what it and every owner it inherits from saw (`reach`).

WHAT IS SEEN, through Python's audit hooks (sys.addaudithook):

* `reads` - files under the repository opened for reading. Not .py files:
  executing Python is testmon's record, and the import machinery opens every
  module it loads, whoever asked for it first;
* `lists` - directories under the repository listed (os.listdir, os.scandir,
  which os.walk and pathlib's glob use, and glob.glob's fixed prefix);
* `starts` - the kinds of process started (subprocess.Popen, os.posix_spawn,
  os.system, os.exec*), by the program run (`kind_of`):
    git     reads the repository's file list; only adding or removing a file
            can change what it sees, so it counts as listing the root when
            it runs inside the repository;
    python  a Python child - a gate run as a script, `uv run`, uqs. While
            a run records, every Python child records itself too
            (reach_child/sitecustomize.py, through PYTHONPATH) and what it
            reads, lists and starts is charged to the test that started it.
            One handed an environment without that is not seen, and is
            taken to reach any file;
    q       a q, qlinter, or a shell (torq.sh starts a q): reaches .q and
            shell files, qlinter's .qlinter* settings, and what a q file names
            by a path literal (test_impact.q_readers);
    args    a program that reads only the files its arguments name (d2,
            grep, cat, ...; #2660): reaches each repository file named -
            and its siblings, for an import or include beside it - and
            everything under each repository directory named (`trees`).
            One naming the repository inside a larger argument (a pattern,
            a script) is charged as `other`;
    other   anything else: reaches any file - but for a few programs that
            read none of it (ps, hostname, ...), and an unknown program
            asked only its version or usage (`d2 --version`), which are
            not recorded;
* `scripts` - the repository .py files a Python child was started on, or an
  `args` program named;
* `trees` - the repository directories an `args` program may read anything
  under.

What this cannot see, the python-tests hook does not pretend to: a file read
by a cached loader is charged to the first test that loaded it in each
worker, and a Python child started with an environment that does not carry
the recorder is not seen at all (hence such a `python` start reaching
everything). CI runs the whole suite on every pull request that can
reach a Python test (scripts/dev/ci_scope.py, which checks its rule against
this record where there is one).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: The record, beside testmon's .testmondata (both gitignored).
RECORD = REPO / ".testreach.json"
#: Put first on a recorded run's PYTHONPATH: its sitecustomize.py records each
#: Python child (scripts/dev/reach_child/sitecustomize.py).
CHILD_DIR = Path(__file__).resolve().parent / "reach_child"
#: `<directory>|<token>`: where children write, and whose owner file to read.
CHILD_ENV = "UQF_REACH_CHILD"
VERSION = 1

#: Not charged to anyone: tooling, caches and what the tests themselves write.
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "output",
    ".claude",
}
_IGNORED_SUFFIXES = (".py", ".pyc", ".pyi", ".so")
_IGNORED_NAMES = {".testmondata", ".testmondata-wal", ".testmondata-shm"}

#: Programs that run another program: classified by what they run.
_WRAPPERS = {"env", "timeout", "nice", "nohup", "stdbuf", "exec", "command", "time", "sandbox-exec"}
#: A wrapper's options that take the next word as their value.
_WRAPPER_VALUES = {"sandbox-exec": {"-p", "-f", "-n", "-D"}}
_PYTHON = {"uv", "uvx", "uqs", "pytest", "pre-commit", "pip"}
_SHELLS = {"bash", "sh", "zsh", "dash"}
_Q = {"q", "peachq", "qlinter"}
_TMP = (os.path.realpath(tempfile.gettempdir()), tempfile.gettempdir(), "/tmp/", "/private/tmp/")
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")
#: Programs that read no file of the repository: not recorded at all.
_INERT = {
    "ps", "pgrep", "pkill", "kill", "lsof", "hostname", "uname", "which", "sleep", "id",
    "whoami", "true", "false", "date", "getconf", "sysctl", "sw_vers", "nproc", "tty",
    "mount", "df", "ssh",
}  # fmt: skip

#: Programs that read no file but those their arguments name, and stdin
#: (#2660). Not awk, sed or node: a program text can name a file of its own.
_ARGUMENT_READERS = {
    "d2", "cat", "head", "tail", "wc", "diff", "cmp", "grep", "egrep", "fgrep", "jq",
    "shasum", "sha256sum", "sha1sum", "md5sum", "md5",
}  # fmt: skip
#: An unknown program run with only one of these reads nothing of the tree.
_ONLY_ASKS = {"--version", "-V", "--help", "-h", "version", "help"}

KINDS = ("git", "python", "q", "other")


def kind_of(argv: list[str], qcmd: str | None = None) -> str:
    """What kind of process `argv` starts (see the module docstring)."""
    words = [w for w in argv if w]
    while words:
        name = os.path.basename(words[0])
        if name in _WRAPPERS:
            # its options, VAR=value and a duration (timeout 30) before the program
            takes = _WRAPPER_VALUES.get(name, set())
            words = words[1:]
            while words and (
                words[0].startswith("-") or "=" in words[0] or _DURATION.match(words[0])
            ):
                words = words[2:] if words[0] in takes else words[1:]
            continue
        if name in _INERT:
            return "inert"
        if name == "git":
            return "git"
        if name.startswith("python") or name in _PYTHON or words[0] == sys.executable:
            return "python"
        if name in _Q or name in _SHELLS or name.endswith(".sh") or (qcmd and words[0] == qcmd):
            return "q"
        if words[0].startswith(_TMP):
            return "q"  # a stand-in a test wrote - most often a fake q
        if len(words) == 2 and words[1] in _ONLY_ASKS:
            return "inert"
        if name in _ARGUMENT_READERS:
            return "args"
        return "other"
    return "other"


def _rel(path: object, base: str | None = None) -> str | None:
    """`path` relative to the repository, or None when it is not in it or is
    not worth recording."""
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    elif isinstance(path, os.PathLike):
        path = os.fspath(path)
    if not isinstance(path, str):
        return None  # a file descriptor
    full = os.path.normpath(os.path.join(base or os.getcwd(), path))
    root = str(REPO)
    if full == root:
        return "."
    if not full.startswith(root + os.sep):
        return None
    rel = full[len(root) + 1 :]
    parts = rel.split(os.sep)
    if _IGNORED_DIRS.intersection(parts) or parts[-1] in _IGNORED_NAMES:
        return None
    return "/".join(parts)


def _glob_prefix(pattern: str) -> str:
    """The directory a glob pattern lists from: its components before the first wildcard."""
    parts = []
    for part in pattern.replace(os.sep, "/").split("/"):
        if any(c in part for c in "*?["):
            break
        parts.append(part)
    return "/".join(parts) or "."


class Recorder:
    """The pytest plugin: charges what each owner reads, lists and starts."""

    def __init__(self, path: Path, worker: str | None = None) -> None:
        self.path = path
        self.worker = worker
        self.stack: list[str] = []
        self.owners: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.tests: dict[str, list[str]] = {}
        self.qcmd = os.environ.get("QCMD")
        self._busy = False
        #: Where Python children write what they see (CHILD_DIR/sitecustomize.py),
        #: and under which name this process tells them the running owner.
        # Outside the tree: a test that asks git what is untracked must not see it.
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
        self.children = Path(tempfile.gettempdir()) / f"uqf-reach-{digest}"
        self.token = worker or "main"

    def watch_children(self) -> None:
        """Have every Python started from here record itself: CHILD_DIR first
        on PYTHONPATH, and CHILD_ENV saying where to write. Set once, in the
        process that runs the tests; a test that hands a child a fresh
        environment leaves that child unseen, and it is charged as one."""
        self.children.mkdir(parents=True, exist_ok=True)
        path = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(CHILD_DIR), path) if p)
        os.environ[CHILD_ENV] = f"{self.children}|{self.token}"
        self._tell_children()

    def _tell_children(self) -> None:
        if self.children.is_dir():
            (self.children / f"{self.token}.owner").write_text(self.stack[-1] if self.stack else "")

    # --- what is seen -----------------------------------------------------
    def _charge(self, field: str, value: str, owner: str | None = None) -> None:
        owner = owner or (self.stack[-1] if self.stack else None)
        if owner:
            self.owners[owner][field].add(value)

    def audit(self, event: str, args: tuple) -> None:
        if self._busy or not self.stack:
            return
        self._busy = True
        try:
            self._audit(event, args)
        except Exception:  # an audit hook that raises aborts what it audits
            pass
        finally:
            self._busy = False

    def _audit(self, event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = args
            if isinstance(mode, str):
                if not ("r" in mode or "+" in mode):
                    return
            elif isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR) == os.O_WRONLY:
                return
            rel = _rel(path)
            if rel and rel != "." and not rel.endswith(_IGNORED_SUFFIXES):
                self._charge("reads", rel)
        elif event in ("os.listdir", "os.scandir"):
            rel = _rel(args[0] if args and args[0] is not None else ".")
            if rel:
                self._charge("lists", rel)
        elif event in ("glob.glob", "glob.glob/2"):
            root = args[2] if event == "glob.glob/2" and len(args) > 2 else None
            rel = _rel(_glob_prefix(os.fspath(args[0])), os.fspath(root) if root else None)
            if rel:
                self._charge("lists", rel)
        elif event == "subprocess.Popen":
            executable, argv, cwd, env = args
            if isinstance(argv, (str, bytes)):
                argv = os.fsdecode(argv).split()
            argv = [os.fsdecode(a) if isinstance(a, bytes) else str(a) for a in argv or []]
            if executable is not None and argv:
                argv = [os.fsdecode(executable), *argv[1:]]
            seen = CHILD_ENV in (os.environ if env is None else env)
            self._start(argv, os.fsdecode(cwd) if cwd is not None else None, seen)
        elif event in ("os.posix_spawn", "os.exec", "os.spawn"):
            path, argv = args[1:3] if event == "os.spawn" else args[0:2]
            argv = list(argv or [])
            self._start([os.fsdecode(path), *[str(a) for a in argv[1:]]], None)
        elif event == "os.system":
            self._start(os.fsdecode(args[0]).split(), None)

    def _start(
        self, argv: list[str], cwd: str | None, seen: bool = False, owner: str | None = None
    ) -> None:
        """A process started. A Python child that records itself (`seen`)
        is not charged as a start: what it reads arrives from its own record."""
        kind = kind_of(argv, self.qcmd)
        if kind == "inert":
            return
        if kind == "args" and self._named(argv[1:], cwd, owner):
            return
        if kind == "args":
            kind = "other"
        if not (kind == "python" and seen):
            self._charge("starts", kind, owner)
        if kind == "git" and _rel(cwd or ".") is not None:
            self._charge("lists", ".", owner)
        if kind == "python":
            for word in argv[1:]:
                rel = _rel(word, cwd) if word.endswith(".py") else None
                if rel and os.path.isfile(REPO / rel):
                    self._charge("scripts", rel, owner)

    def _named(self, words: list[str], cwd: str | None, owner: str | None) -> bool:
        """Charge what an `args` program's arguments name: a repository file
        reaches its directory, a directory everything under it, a .py file is
        also a script. False, charging nothing, when an argument holds the
        repository but is not a path to it - it cannot be taken apart."""
        trees: set[str] = set()
        scripts: set[str] = set()
        root = str(REPO)
        for word in words:
            # --option=value: the value; a flag (-r, -f/x): nothing to resolve
            part = word.partition("=")[2] if word.startswith("-") and "=" in word else word
            if part.startswith("-") or not part:
                if root in part:
                    return False
                continue
            rel = _rel(part, cwd)
            if rel is not None and os.path.isdir(REPO / rel):
                trees.add(rel)
            elif rel is not None and os.path.isfile(REPO / rel):
                trees.add(rel.rpartition("/")[0] or ".")
                if rel.endswith(".py"):
                    scripts.add(rel)
            elif root in part:
                return False
        for tree in trees:
            self._charge("trees", tree, owner)
        for script in scripts:
            self._charge("scripts", script, owner)
        return True

    def _read_children(self) -> None:
        """Charge what this process's Python children wrote to the owner each
        was started under, and remove their files."""
        for f in sorted(self.children.glob(f"{self.token}.*.child")):
            lines = f.read_text(errors="replace").splitlines()
            f.unlink(missing_ok=True)
            if not lines:
                continue
            owner = lines[0]
            for line in lines[1:]:
                kind, _, value = line.partition("\t")
                if kind == "read":
                    rel = _rel(value)
                    if rel and rel != "." and not rel.endswith(_IGNORED_SUFFIXES):
                        self._charge("reads", rel, owner)
                elif kind == "list":
                    rel = _rel(value)
                    if rel:
                        self._charge("lists", rel, owner)
                elif kind == "start":
                    inherits, cwd, argv = (value.split("\t", 2) + ["", ""])[:3]
                    self._start(argv.split("\x1f"), cwd or None, inherits == "1", owner)

    # --- who is charged ---------------------------------------------------
    def _push(self, owner: str) -> None:
        self.stack.append(owner)
        self._tell_children()

    def _pop(self) -> None:
        self.stack.pop()
        self._tell_children()

    @pytest.hookimpl(wrapper=True)
    def pytest_make_collect_report(self, collector):
        if not isinstance(collector, pytest.Module):
            return (yield)
        self._push(module_key(collector.nodeid))
        try:
            return (yield)
        finally:
            self._pop()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        nodeid = test_id(item.nodeid, item.keywords)
        keys = [nodeid, module_key(item.nodeid)]
        info = getattr(item, "_fixtureinfo", None)
        for defs in getattr(info, "name2fixturedefs", {}).values():
            if defs:
                keys.append(fixture_key(defs[-1].baseid, defs[-1].argname))
        self.tests[nodeid] = keys
        self._push(nodeid)
        try:
            return (yield)
        finally:
            self._pop()

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        self._push(fixture_key(fixturedef.baseid, fixturedef.argname))
        try:
            return (yield)
        finally:
            self._pop()

    # --- the record -------------------------------------------------------
    def data(self) -> dict:
        owners = {
            key: {f: sorted(v) for f, v in sorted(fields.items()) if v}
            for key, fields in self.owners.items()
        }
        where = str(REPO.resolve())
        return {
            "owners": {k: v for k, v in owners.items() if v},
            "tests": self.tests,
            "where": dict.fromkeys(self.tests, where),
        }

    def pytest_sessionfinish(self, session) -> None:
        self._read_children()
        if self.worker:
            part = self.path.with_name(f"{self.path.name}.{self.worker}.part")
            part.write_text(json.dumps(self.data()))
            return
        parts = sorted(self.path.parent.glob(f"{self.path.name}.*.part"))
        merged = merge(load(self.path), [self.data(), *(json.loads(p.read_text()) for p in parts)])
        write_whole(self.path, json.dumps(merged, indent=0, sort_keys=True))
        for p in parts:
            p.unlink(missing_ok=True)


def scratch_dir(path: Path) -> Path:
    """Where a whole new copy of `path` is written before it replaces it: the
    git directory of the work tree `path` sits at the root of, so a write cut
    short leaves nothing in the work tree for `git status` to show (#2273) -
    an untracked file there made attest_kdbx.py refuse the tree. Beside `path`
    when it is not at a work tree's root, or the git directory is on another
    file system (os.replace cannot cross one)."""
    parent = path.parent
    dotgit = parent / ".git"
    gitdir: Path | None = None
    if dotgit.is_dir():
        gitdir = dotgit
    elif dotgit.is_file():
        line = dotgit.read_text(errors="replace").strip()
        if line.startswith("gitdir:"):
            gitdir = (parent / line.removeprefix("gitdir:").strip()).resolve()
    try:
        if gitdir and gitdir.is_dir() and gitdir.stat().st_dev == parent.stat().st_dev:
            return gitdir
    except OSError:
        pass
    return parent


def write_whole(path: Path, text: str) -> None:
    """Replace `path` with `text` at once: a reader sees the old record or the
    new one, never half of one. The copy is written in `scratch_dir` and
    removed if anything stops it reaching `path`."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=scratch_dir(path))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_ACTIVE: list[Recorder] = []
_INSTALLED = False


def _audit(event: str, args: tuple) -> None:
    if _ACTIVE:
        _ACTIVE[-1].audit(event, args)


def install(recorder: Recorder) -> None:
    """Make `recorder` the one the (once-installed) audit hook reports to."""
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_audit)
        _INSTALLED = True
    _ACTIVE[:] = [recorder]


def module_key(nodeid: str) -> str:
    return "module:" + nodeid.split("::", 1)[0]


def fixture_key(baseid: str, argname: str) -> str:
    return f"fixture:{baseid}::{argname}"


def test_id(nodeid: str, keywords: Iterable[str] = ()) -> str:
    """The id collection gives a test: xdist's loadgroup adds `@<group>` to a
    grouped test's, which is dropped (as scripts/dev/pytest_shards.py does)."""
    return re.sub(r"@[\w.-]+$", "", nodeid) if "xdist_group" in keywords else nodeid


# --- reading the record -----------------------------------------------------


def load(path: Path = RECORD) -> dict | None:
    """The record at `path`, or None when there is none this can read."""
    try:
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    return data


def merge(old: dict | None, runs: list[dict]) -> dict:
    """`old` with each run's tests replaced and its owners' sightings added.

    A test's own entry is replaced - it ran, and this is what it does now. A
    module's or fixture's is added to, never narrowed: a run of a few tests
    sets up only some of what a fixture can do."""
    owners: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    tests: dict[str, list[str]] = dict((old or {}).get("tests", {}))
    where: dict[str, str] = dict((old or {}).get("where", {}))
    ran = {t for run in runs for t in run.get("tests", {})}
    for key, fields in (old or {}).get("owners", {}).items():
        if key in ran:
            continue
        for f, values in fields.items():
            owners[key][f].update(values)
    for run in runs:
        tests.update(run.get("tests", {}))
        where.update(run.get("where", {}))
        for key, fields in run.get("owners", {}).items():
            for f, values in fields.items():
                owners[key][f].update(values)
    return {
        "version": VERSION,
        "owners": {k: {f: sorted(v) for f, v in sorted(fs.items())} for k, fs in owners.items()},
        "tests": tests,
        "where": where,
    }


def recorded_here(record: dict, root: Path = REPO) -> set[str]:
    """The tests whose entry a run in the work tree at `root` wrote (#2276).

    The record is shared between the worktrees of a clone (python_tests_hook.py
    seeds a new one from the last any of them wrote), so it also holds what
    tests did on other branches - reads of files this tree has not got, or
    has differently. Each run stamps the tests it ran with its root (`where`);
    what judges the record against this tree judges only these."""
    here = str(Path(root).resolve())
    return {t for t, w in record.get("where", {}).items() if w == here and t in record["tests"]}


def reach(record: dict, nodeid: str) -> dict[str, set[str]]:
    """Everything `nodeid` and the owners it inherits from saw."""
    out: dict[str, set[str]] = defaultdict(set)
    for key in record["tests"].get(nodeid, [nodeid]):
        for f, values in record["owners"].get(key, {}).items():
            out[f].update(values)
    return out


def _under(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(directory.rstrip("/") + "/")


def reached(
    record: dict,
    changes: list[tuple[str, str]],
    q_reads: dict[str, list[str]],
) -> dict[str, str]:
    """{nodeid: why} for the tests that `changes` - (git status letter, path)
    pairs - reach by the record. A .py path is testmon's, except as a script
    a test started (`scripts`). `q_reads` is test_impact.q_readers' answer
    for the non-Python paths: which q files name each."""
    out: dict[str, str] = {}
    for nodeid in record["tests"]:
        seen = reach(record, nodeid)
        for status, path in changes:
            why = _why(seen, status, path, q_reads)
            if why:
                out[nodeid] = why
                break
    return out


def _why(seen: dict[str, set[str]], status: str, path: str, q_reads: dict[str, list[str]]):
    starts = seen.get("starts", set())
    tree = next((d for d in sorted(seen.get("trees", ())) if _under(path, d)), None)
    if path.endswith(".py"):
        if path in seen.get("scripts", ()):
            return f"starts {path}"
        return f"starts a program reading {tree}" if tree else None
    if tree is not None:
        return f"starts a program reading {tree}"
    if path in seen.get("reads", ()):
        return f"reads {path}"
    if status in "AD" and any(_under(path, d) for d in seen.get("lists", ())):
        return f"lists the directory of {path}"
    if "other" in starts:
        return "starts a process this cannot see into"
    if "python" in starts:
        return "starts a Python child, whose reads are not recorded"
    if "q" in starts and (
        path.endswith((".q", ".sh")) or path.startswith(".qlinter") or q_reads.get(path)
    ):
        return f"starts a q, which {'loads' if path.endswith('.q') else 'reads'} {path}"
    return None


def starters(record: dict, kind: str) -> set[str]:
    """The tests that start a process of `kind`."""
    return {t for t in record["tests"] if kind in reach(record, t).get("starts", set())}


def scan_of(path: str, files: Iterable[str]) -> list[str]:
    """What a test reading `path` as part of SCANNING `files` reads: every file
    of its kind (its suffix), or every file when it is the only one of its kind."""
    files = list(files)
    kind = [f for f in files if Path(f).suffix == Path(path).suffix]
    return kind if len(kind) > 1 else files


def stale_scans(record: dict, files: Iterable[str], tests: Iterable[str] | None = None) -> set[str]:
    """The tests whose record shows a scan of `files` as they were before some
    were added (#2276): each read at least half of a scan but not all of it,
    and lists a directory every file it missed is under - so a run now would
    read them. A test that judges the record by `scan_of` has them run first.
    One reading a file or two by name reads too little of a scan to be taken
    for one, and is left for the judge to find; rerunning a test is never a
    reason for the judge to pass, only for its record to be current. `tests`
    narrows it to some (by default every test in the record)."""
    files = list(files)
    known = set(files)
    out = set()
    for nodeid in record["tests"] if tests is None else tests:
        seen = reach(record, nodeid)
        reads = seen.get("reads", set())
        lists = seen.get("lists", set())
        for path in sorted(reads & known):
            scan = set(scan_of(path, files))
            missing = scan - reads
            if (
                missing
                and 2 * len(scan & reads) >= len(scan)
                and all(any(_under(m, d) for d in lists) for m in missing)
            ):
                out.add(nodeid)
                break
    return out
