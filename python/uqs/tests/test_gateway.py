"""stack/gateway.py: what `uqs query` sends the gateway, and its session loop."""

from __future__ import annotations

import pytest

from uqs.paths import UqsError
from uqs.stack import gateway


@pytest.mark.parametrize(
    ("expr", "sent"),
    [
        ("select from trade", '.gw.syncexec["select from trade";`rdb]'),
        ("count trade", '.gw.syncexec["count trade";`rdb]'),
        ("update px:0 from trade", '.gw.syncexec["update px:0 from trade";`rdb]'),
        (
            'select from t where s like "EUR*"',
            '.gw.syncexec["select from t where s like \\"EUR*\\"";`rdb]',
        ),
        ('.gw.syncexec["select from t";`hdb]', '.gw.syncexec["select from t";`hdb]'),
        ("\\t 1", "\\t 1"),
    ],
)
def test_everything_is_routed_but_a_gateway_call_or_a_system_command(expr, sent):
    assert gateway.expression(expr, "rdb") == sent


@pytest.mark.parametrize("expr", ["delete from `trade", "update px:0 from `trade where sym=`X"])
def test_an_in_place_change_is_refused(expr):
    """Routed, it would change the RDB's live data."""
    with pytest.raises(UqsError, match="--proc"):
        gateway.expression(expr)


def test_servers_must_be_process_types():
    with pytest.raises(UqsError, match="process types"):
        gateway.expression("count trade", "rdb;exit 0")


def _run(lines, send=lambda e: f"<{e}>"):
    shown, failed = [], []
    script = iter(lines)

    def read(prompt):
        try:
            item = next(script)
        except StopIteration:
            raise EOFError from None
        if isinstance(item, BaseException):
            raise item
        return item

    n = gateway.session(send, read, shown.append, failed.append, "rdb")
    return n, shown, failed


def test_each_line_is_routed_and_shown():
    n, shown, failed = _run(["count trade", "", "  "])
    assert (n, shown, failed) == (1, ['<.gw.syncexec["count trade";`rdb]>'], [])


@pytest.mark.parametrize("quit_line", ["\\\\", "exit", "quit"])
def test_the_session_ends_on_a_quit_line(quit_line):
    n, shown, _ = _run([quit_line, "count trade"])
    assert (n, shown) == (0, [])


def test_a_failing_line_is_reported_and_the_session_goes_on():
    def send(expr):
        if "boom" in expr:
            raise RuntimeError("type")
        return "ok"

    n, shown, failed = _run(["boom", "count trade"], send)
    assert (n, shown, failed) == (1, ["ok"], ["type"])


def test_a_refused_line_is_reported_not_sent():
    n, _, failed = _run(["delete from `trade"])
    assert n == 0 and "--proc" in failed[0]


def test_ctrl_c_abandons_the_line_not_the_session():
    n, shown, _ = _run([KeyboardInterrupt(), "count trade"])
    assert n == 1 and shown


def test_with_a_console_size_the_gateway_lays_out_the_joined_result():
    """The layout goes in .gw.syncexecj's join, which the gateway applies
    before its deferred reply - the only place a -30! reply still sees it."""
    q = gateway.expression("tables[]", "rdb hdb", (40, 120))
    assert q.startswith('.gw.syncexecj["tables[]";`rdb`hdb;{')
    assert ".Q.s raze x" in q and 'system"c 40 120"' in q


def test_an_as_typed_call_with_a_console_size_is_wrapped():
    q = gateway.expression("\\t 1", "rdb", (40, 120))
    assert ".Q.s value x" in q


def test_the_session_routes_each_line_with_the_current_size():
    sizes = iter([(40, 120), (30, 90)])
    shown: list = []
    lines = iter(["count trade", "count quote"])

    def read(prompt):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError from None

    gateway.session(
        lambda e: e, read, shown.append, list().append, "rdb", render=lambda: next(sizes)
    )
    assert 'system"c 40 120"' in shown[0] and 'system"c 30 90"' in shown[1]
