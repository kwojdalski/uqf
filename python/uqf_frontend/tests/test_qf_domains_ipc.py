"""The seven `.qf` domains (#1411) resolve by name over real IPC, not only in-process.

Everything else that touches the quant library does it by direct call, inside
the process that loaded it. A renamed namespace can still fail in the ways
that never show there: a function addressed by a SYMBOL (what kola sends when
a client names a function), a program shipped as a STRING for the server to
`value`, a name looked up with `get`/`value`, and the namespace discovery a
tool runs on the far side. A string is not type-checked, so a stale name in one
is a runtime error on the other process - which is the failure this pins.

The q process is plain q with `src/init.q` loaded, the library as a gateway
or an analytics process would hold it.
"""

from __future__ import annotations

from typing import Any

import pytest

from uqf_frontend.config import Settings
from uqf_frontend.errors import QueryRejected
from uqf_frontend.gateway import KolaGateway
from uqs.interpreter import PEACHQ, q_impl

#: Every retired namespace; none of them may exist in a process that loaded src/init.q.
RETIRED = (
    "qccy qcal qdcf qrates qfwd qopt qbook qcross qmicro qdqc qasof qexec qalloc qpos "
    "qdesk qrisk qlimit qstats qmeta qns qrender qschema qexdef qsynth"
).split()

DOMAINS = ["demo", "exec", "math", "mkt", "port", "px", "util"]


@pytest.fixture(scope="module")
def gateway(start_q, tmp_path_factory) -> Any:
    if q_impl() == PEACHQ:
        pytest.skip("PeachQ cannot load nested `\\d .qf.<domain>` namespaces unflattened")
    script = tmp_path_factory.mktemp("qf") / "library.q"
    script.write_text("\\l src/init.q\n")
    with start_q(str(script)) as port:
        yield KolaGateway(Settings(port=port))


def test_a_function_named_by_symbol_is_called_over_ipc(gateway):
    """`.qf.px.fwd_simple` addressed the way a client names a function."""
    assert gateway.call(".qf.px.fwd_simple", 1.1, 0.05, 0.02, 1.0) == pytest.approx(
        1.1323529411764708
    )
    assert gateway.call(".qf.exec.markout", 1, 1.1000, 1.1010, 10000) == pytest.approx(10.0)
    assert gateway.call(".qf.port.pip_value", 1000000, 10000) == pytest.approx(100.0)
    assert gateway.call(".qf.math.ncdf", 0.0) == pytest.approx(0.5)


def test_a_query_string_is_evaluated_by_the_far_side(gateway):
    """A program sent as text names each domain in full."""
    out = gateway.call(
        "{[x] (.qf.px.gk_call[1.1;1.1;0.05;0.02;0.1;1f]; .qf.math.ncdf 0f; "
        ".qf.px.pip_factor `USDJPY; .qf.exec.markout[1;1.1000;1.1010;10000])}",
        0,
    )
    assert out == pytest.approx([0.06018482163918004, 0.5, 100.0, 10.0])


def test_a_name_looked_up_by_value_and_by_get_resolves(gateway):
    """`value` of a symbol and of a string, and `get` - the lookups a tool uses
    to resolve a name it built at run time."""
    assert gateway.call("{[x] 100h=type value `.qf.mkt.sweep_price}", 0) is True
    assert gateway.call("{[x] 100h=type get `.qf.util.owned}", 0) is True
    assert gateway.call('{[x] value ".qf.px.pip_factor `EURUSD"}', 0) == pytest.approx(10000.0)


def test_the_seven_domains_exist_under_the_one_root(gateway):
    assert sorted(str(n) for n in gateway.call("{[x] (key `.qf) except `}", 0)) == DOMAINS


def test_no_retired_namespace_is_defined(gateway):
    program = (
        "{[x] ("
        + "".join(f"`{name}" for name in RETIRED)
        + ") where ("
        + "".join(f"`{name}" for name in RETIRED)
        + ") in key `}"
    )
    assert list(gateway.call(program, 0)) == []


def test_namespace_discovery_reaches_the_domains_and_still_finds_the_framework(gateway):
    owned = [str(n) for n in gateway.call("{[x] .qf.util.owned[]}", 0)]
    functional = [str(n) for n in gateway.call("{[x] .qf.util.functional[]}", 0)]
    for domain in DOMAINS:
        assert f".qf.{domain}" in owned
        if domain != "demo":
            assert f".qf.{domain}" in functional
    # src/init.q loads only .qf.demo's constants (the ETL adds its functions),
    # so here it is owned but holds no function, as .qexdef was.
    assert ".qf.demo" not in functional
    assert ".qf" in owned
    assert ".qf" not in functional, "the root holds namespaces, not functions"


def test_a_retired_name_is_refused_over_ipc(gateway):
    with pytest.raises(QueryRejected):
        gateway.call(".qfwd.fwd_simple", 1.1, 0.05, 0.02, 1.0)
