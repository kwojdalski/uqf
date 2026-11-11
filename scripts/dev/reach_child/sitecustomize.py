"""What a Python child of a recorded test reads, lists and starts (#1879).

scripts/dev/python_test_reach.py records what each test does in its own process. A
test that runs a gate as a script, or `uv run uqs ...`, does the rest in a
child, which that record cannot see. While a run records, this directory is
put first on PYTHONPATH and UQF_REACH_CHILD names where to write and which
pytest process asked: every Python started under it - however deep - then
imports this module at startup, and appends what it sees to a file the
recording process reads back at the end, charging it to the test that was
running when the child started.

Does nothing unless UQF_REACH_CHILD is set. Never raises: an exception here
would be printed into the output of every program the tests run. Standard
library only, and old syntax, for whichever python3 a test starts.
"""

import os
import sys


def _install():
    spec = os.environ.get("UQF_REACH_CHILD", "")
    if "|" not in spec:
        return
    directory, worker = spec.split("|", 1)
    try:
        with open(os.path.join(directory, worker + ".owner")) as f:
            owner = f.read().strip()
    except OSError:
        return
    if not owner:
        return
    out_path = os.path.join(directory, f"{worker}.{os.getpid()}.child")
    seen = set()
    state = {"busy": False}
    out = []  # the file, once something is worth writing

    def emit(kind, value):
        key = (kind, value)
        if key in seen:
            return
        seen.add(key)
        if not out:
            out.append(open(out_path, "a", buffering=1))
            out[0].write(owner + "\n")
        out[0].write("{}\t{}\n".format(kind, value.replace("\n", " ")))

    def hook(event, args):
        if state["busy"]:
            return
        state["busy"] = True
        try:
            if event == "open":
                path, mode, flags = args
                if isinstance(mode, str) and not ("r" in mode or "+" in mode):
                    return
                if not isinstance(mode, str) and isinstance(flags, int):
                    if flags & (os.O_WRONLY | os.O_RDWR) == os.O_WRONLY:
                        return
                if isinstance(path, (str, bytes)) or hasattr(path, "__fspath__"):
                    path = os.fsdecode(os.fspath(path))
                    if not path.endswith((".py", ".pyc")):
                        emit("read", os.path.abspath(path))
            elif event in ("os.listdir", "os.scandir"):
                path = args[0] if args and args[0] is not None else "."
                if isinstance(path, (str, bytes)) or hasattr(path, "__fspath__"):
                    emit("list", os.path.abspath(os.fsdecode(os.fspath(path))))
            elif event == "subprocess.Popen":
                executable, argv, cwd, env = args
                if isinstance(argv, (str, bytes)):
                    argv = os.fsdecode(argv).split()
                argv = [os.fsdecode(a) if isinstance(a, bytes) else str(a) for a in argv or []]
                if executable is not None and argv:
                    argv = [os.fsdecode(executable)] + argv[1:]
                inherits = env is None or "UQF_REACH_CHILD" in env
                where = os.path.abspath(os.fsdecode(cwd)) if cwd is not None else os.getcwd()
                emit("start", "{}\t{}\t{}".format(int(inherits), where, "\x1f".join(argv)))
        except Exception:
            pass
        finally:
            state["busy"] = False

    sys.addaudithook(hook)


try:
    _install()
except Exception:
    pass
