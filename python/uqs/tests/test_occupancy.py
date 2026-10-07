"""Two runtimes side by side (#761), and a start refused by name when
another stack holds its ports (#762)."""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs import runtimes
from uqs.paths import UqsError
from uqs.stack import alive, occupancy
from uqs.stack.env import build_env

REPO = Path(stack_paths.__file__).resolve().parents[4]


def _ports(runtime: str, base_port: int | None = None) -> set[int]:
    paths = stack_paths.paths_for_root(REPO, runtime)
    return {
        int(row["port"])
        for row in alive._process_rows(paths, base_port)
        if str(row.get("port", "")).isdigit()
    }


# ------------------------------------------------------------------ #761


def test_each_runtime_defaults_to_its_own_base_port(tmp_path):
    for runtime in runtimes.RUNTIMES.values():
        env = build_env(stack_paths.paths_for_root(tmp_path, runtime.name))
        assert env["KDBBASEPORT"] == str(runtime.base_port)


def test_port_still_wins_over_the_runtime_default(tmp_path):
    env = build_env(stack_paths.paths_for_root(tmp_path, "torq"), base_port=7000)
    assert env["KDBBASEPORT"] == "7000"


def test_the_runtimes_default_port_spans_do_not_overlap():
    """What lets two runtimes run at once with no flags: every port each
    one's processes listen on, resolved from its own process.csv."""
    spans = {name: _ports(name) for name in runtimes.RUNTIMES}
    names = list(spans)
    for i, a in enumerate(names):
        assert spans[a], f"{a} resolved no ports"
        for b in names[i + 1 :]:
            assert not spans[a] & spans[b], f"{a} and {b} share {sorted(spans[a] & spans[b])}"


# ------------------------------------------------------------------ #762


def _procfile(data_dir: Path, rows: dict[str, str]) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "process.csv"
    lines = ["host,port,proctype,procname"]
    lines += [f"localhost,{port},t,{name}" for name, port in rows.items()]
    path.write_text("\n".join(lines) + "\n")
    return path


def _line(stackid: int, procname: str, procfile: Path) -> str:
    return f"q torq.q -stackid {stackid} -proctype t -procname {procname} -procfile {procfile}"


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """This runtime (torq) wants ports 6150 (a1) and 6152 (a2); `ps` is
    whatever the test puts in `lines`."""
    paths = stack_paths.paths_for_root(tmp_path, "torq")
    lines: list[tuple[int, str]] = []
    monkeypatch.setattr(alive, "_command_lines", lambda timeout=None: lines)

    def rows(_paths, base):
        return [{"procname": "a1", "port": str(base)}, {"procname": "a2", "port": str(base + 2)}]

    monkeypatch.setattr(alive, "_process_rows", rows)
    return paths, lines


def test_another_runtime_on_our_ports_is_refused_by_name(stack):
    paths, lines = stack
    uqf = _procfile(paths.repo_root / "output" / "uqs", {"rdb1": "{KDBBASEPORT}+2"})
    lines.append((4242, _line(6150, "rdb1", uqf)))
    with pytest.raises(UqsError) as exc:
        occupancy.refuse_if_taken(paths, None)
    message = str(exc.value)
    assert "the uqf runtime's stack (base port 6150)" in message
    assert "rdb1 :6152 (pid 4242)" in message
    assert "`uqs stop --port 6150`" in message


def test_a_stack_on_other_ports_is_no_conflict(stack):
    paths, lines = stack
    uqf = _procfile(paths.repo_root / "output" / "uqs", {"rdb1": "{KDBBASEPORT}+2"})
    lines.append((4242, _line(6050, "rdb1", uqf)))  # the uqf default: 6052
    occupancy.refuse_if_taken(paths, None)


def test_this_stack_s_own_processes_are_not_a_conflict(stack):
    paths, lines = stack
    ours = _procfile(paths.torqdata, {"a1": "{KDBBASEPORT}"})
    lines.append((1, _line(6150, "a1", ours)))
    occupancy.refuse_if_taken(paths, None)


def test_the_same_runtime_on_another_port_is_named_with_its_port(stack):
    """This runtime, started earlier with --port 6148: its a2 holds 6152."""
    paths, lines = stack
    ours = _procfile(paths.torqdata, {"a2": "{KDBBASEPORT}+4"})
    lines.append((7, _line(6148, "a2", ours)))
    with pytest.raises(UqsError) as exc:
        occupancy.refuse_if_taken(paths, None)
    assert "the torq runtime's stack (base port 6148)" in str(exc.value)
    assert "`uqs --runtime torq stop --port 6148`" in str(exc.value)


def test_only_the_processes_being_started_are_checked(stack):
    paths, lines = stack
    uqf = _procfile(paths.repo_root / "output" / "uqs", {"rdb1": "{KDBBASEPORT}+2"})
    lines.append((4242, _line(6150, "rdb1", uqf)))
    occupancy.refuse_if_taken(paths, None, "a1")  # a1 is 6150, free
    with pytest.raises(UqsError):
        occupancy.refuse_if_taken(paths, None, "a2")


def test_a_stack_from_another_checkout_is_named_by_its_directory(stack, tmp_path):
    paths, lines = stack
    elsewhere = _procfile(tmp_path / "other" / "output" / "uqs", {"x1": "6152"})
    lines.append((9, _line(9999, "x1", elsewhere)))
    with pytest.raises(
        UqsError,
        match=r"data is in .*other/output/uqs.*Stop it from the checkout it belongs to \(.*other\)",
    ):
        occupancy.refuse_if_taken(paths, None)


def test_a_failing_ps_does_not_block_a_start(monkeypatch, tmp_path):
    def broken(timeout=None):
        raise UqsError("ps failed (1)")

    monkeypatch.setattr(alive, "_command_lines", broken)
    monkeypatch.setattr(alive, "_process_rows", lambda _p, _b: [])
    occupancy.refuse_if_taken(stack_paths.paths_for_root(tmp_path, "torq"), None)
