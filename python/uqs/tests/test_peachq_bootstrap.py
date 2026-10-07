"""Tests for scripts/peachq.py, the resolver that provides PeachQ to the lanes
that must pass on it: override, then cache, then a build of the pinned commit.

Fetch, build and identify are replaced throughout, so these run with no
network, no compiler and no q. The real cold build is checked by hand and by
CI's cache-miss path, which goes through this same resolver.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "peachq.py"

_spec = importlib.util.spec_from_file_location("uqf_peachq_under_test", SCRIPT)
assert _spec and _spec.loader
peachq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peachq)

COMMIT = "a" * 40
PLAT = "Linux-x86_64"


def _pin(tmp_path: Path, commit: str = COMMIT, options: dict | None = None) -> Path:
    path = tmp_path / "pin.json"
    path.write_text(
        json.dumps(
            {
                "repository": "https://example.invalid/peachq",
                "commit": commit,
                "make_target": "q",
                "platforms": {PLAT: options or {"RAY_MARCH": "x86-64-v2"}},
            }
        )
    )
    return path


class Fake:
    """Stand-ins for git, make and the identify probe, counting their calls."""

    def __init__(self, *, head: str | None = None, build_fails: bool = False, slow: float = 0):
        self.fetches = 0
        self.builds = 0
        self.head = head
        self.build_fails = build_fails
        self.slow = slow
        self.messages: list[str] = []
        self.lock = threading.Lock()

    def fetch(self, src: Path, repository: str, commit: str, log: Path) -> str:
        with self.lock:
            self.fetches += 1
        src.mkdir(parents=True)
        return self.head or commit

    def build(self, src: Path, target: str, options, log: Path) -> Path:
        with self.lock:
            self.builds += 1
        time.sleep(self.slow)
        if self.build_fails:
            raise peachq.PeachQError("make exit 2")
        binary = src / target
        binary.write_text("#!/bin/sh\necho peachq\n")
        binary.chmod(0o755)
        return binary

    @staticmethod
    def identify(binary) -> str:
        text = Path(binary).read_text() if Path(binary).is_file() else ""
        return "peachq" if "peachq" in text else ("kdbx" if "kdbx" in text else "")

    def resolve(self, tmp_path: Path, env: dict | None = None, **kw):
        if "pin_path" not in kw:
            kw["pin_path"] = _pin(tmp_path)
        return peachq.resolve(
            {"XDG_CACHE_HOME": str(tmp_path / "cache"), **(env or {})},
            plat=kw.pop("plat", PLAT),
            fetch=self.fetch,
            build=self.build,
            identify=self.identify,
            prerequisites=kw.pop("prerequisites", lambda env: []),
            log=self.messages.append,
            **kw,
        )


def test_the_resolver_parses_on_the_oldest_python_it_runs_under():
    """scripts/ runs under a bare python3, not the workspace's 3.14: syntax
    only 3.14 accepts (an unparenthesised `except A, B:`) broke CI once."""
    import ast

    for script in (SCRIPT, ROOT / "scripts" / "test.py"):
        ast.parse(script.read_text(), feature_version=(3, 10))


def test_the_shipped_pin_is_a_full_commit_with_options_per_platform():
    pin = peachq.load_pin()
    assert re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
    assert pin["platforms"]["Linux-x86_64"] == {"RAY_MARCH": "x86-64-v2"}
    assert "Darwin-arm64" in pin["platforms"]


def test_ci_reads_the_pin_rather_than_restating_it():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert peachq.load_pin()["commit"] not in ci
    assert "scripts/peachq.py" in ci
    assert "RAY_MARCH" not in ci


def test_a_cold_cache_builds_and_a_warm_one_needs_no_network(tmp_path):
    fake = Fake()
    first = fake.resolve(tmp_path)
    assert first.is_absolute() and first.parent.parent == tmp_path / "cache" / "uqf" / "peachq"
    assert (fake.fetches, fake.builds) == (1, 1)
    assert any("fetching" in m for m in fake.messages), "the first-run build is announced"

    def offline(*_a):
        raise AssertionError("a warm cache must not fetch or build")

    warm = peachq.resolve(
        {"XDG_CACHE_HOME": str(tmp_path / "cache")},
        pin_path=tmp_path / "pin.json",
        plat=PLAT,
        fetch=offline,
        build=offline,
        identify=Fake.identify,
        prerequisites=offline,
        log=offline,
    )
    assert warm == first


def test_moving_the_pin_or_the_options_selects_a_new_entry(tmp_path):
    fake = Fake()
    one = fake.resolve(tmp_path)
    two = fake.resolve(tmp_path, pin_path=_pin(tmp_path, commit="b" * 40))
    three = fake.resolve(tmp_path, pin_path=_pin(tmp_path, options={"RAY_MARCH": "x86-64-v3"}))
    assert len({one, two, three}) == 3
    assert fake.builds == 3
    assert ("b" * 40) in two.parent.name


def test_the_override_wins_and_is_not_built(tmp_path):
    binary = tmp_path / "mine"
    binary.write_text("peachq")
    binary.chmod(0o755)
    fake = Fake()
    assert fake.resolve(tmp_path, {peachq.OVERRIDE_ENV: str(binary)}) == binary.resolve()
    assert fake.builds == 0


@pytest.mark.parametrize(
    ("content", "expected"),
    [(None, "not a runnable file"), ("kdbx", "is kdbx, not PeachQ"), ("", "did not say")],
)
def test_an_invalid_override_fails_with_no_fallback(tmp_path, content, expected):
    binary = tmp_path / "bad"
    if content is not None:
        binary.write_text(content)
        binary.chmod(0o755)
    fake = Fake()
    with pytest.raises(peachq.PeachQError, match=expected):
        fake.resolve(tmp_path, {peachq.OVERRIDE_ENV: str(binary)})
    assert fake.builds == 0, "an invalid override must not fall back to the pinned build"


def test_an_unsupported_platform_is_refused_by_name(tmp_path):
    with pytest.raises(peachq.PeachQError, match=r"Linux-riscv64.*supported: Linux-x86_64"):
        Fake().resolve(tmp_path, plat="Linux-riscv64")


def test_missing_build_tools_are_named(tmp_path):
    with pytest.raises(peachq.PeachQError, match=r"needs git, cc on PATH"):
        Fake().resolve(tmp_path, prerequisites=lambda env: ["git", "cc"])


def test_a_checkout_that_is_not_the_pin_is_refused(tmp_path):
    fake = Fake(head="c" * 40)
    with pytest.raises(peachq.PeachQError, match="not the pinned"):
        fake.resolve(tmp_path)
    assert fake.builds == 0


def test_a_failed_build_leaves_nothing_reusable_and_can_be_retried(tmp_path):
    with pytest.raises(peachq.PeachQError, match="make exit 2"):
        Fake(build_fails=True).resolve(tmp_path)
    root = tmp_path / "cache" / "uqf" / "peachq"
    assert not [p for p in root.iterdir() if p.is_dir()], "no entry and no build directory"
    retry = Fake()
    assert retry.resolve(tmp_path).is_file()
    assert retry.builds == 1


def test_an_interrupted_build_and_a_damaged_entry_are_rebuilt(tmp_path):
    good = Fake().resolve(tmp_path)
    root = good.parent.parent
    (root / f".build-{good.parent.name}-dead").mkdir()  # a build killed mid-way
    good.write_text("garbage")  # an entry that no longer identifies
    fake = Fake()
    assert fake.resolve(tmp_path) == good
    assert fake.builds == 1
    assert not list(root.glob(".build-*"))


def test_concurrent_runs_build_once(tmp_path):
    fake = Fake(slow=0.3)
    _pin(tmp_path)
    results: list[Path] = []
    errors: list[BaseException] = []

    def run():
        try:
            results.append(fake.resolve(tmp_path, pin_path=tmp_path / "pin.json"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert fake.builds == 1
    assert len(set(results)) == 1
    assert any("waiting" in m for m in fake.messages)


def test_the_cache_follows_xdg_and_defaults_under_home():
    assert peachq.cache_root({"XDG_CACHE_HOME": "/x"}) == Path("/x/uqf/peachq")
    assert peachq.cache_root({"HOME": "/h"}) == Path("/h/.cache/uqf/peachq")


def test_architecture_spellings_are_normalised():
    assert peachq.platform_key("Linux", "aarch64") == "Linux-arm64"
    assert peachq.platform_key("Linux", "AMD64") == "Linux-x86_64"


def test_unsafe_build_options_are_refused(tmp_path):
    with pytest.raises(peachq.PeachQError, match="NAME: VALUE"):
        peachq.load_pin(_pin(tmp_path, options={"RAY_MARCH": "x; rm -rf /"}))


def test_a_short_commit_is_refused(tmp_path):
    with pytest.raises(peachq.PeachQError, match="full 40-character"):
        peachq.load_pin(_pin(tmp_path, commit="1925d06"))


def test_nothing_here_touches_qcmd_or_path(tmp_path, monkeypatch):
    before = (os.environ.get("QCMD"), os.environ.get("PATH"))
    Fake().resolve(tmp_path)
    assert (os.environ.get("QCMD"), os.environ.get("PATH")) == before
