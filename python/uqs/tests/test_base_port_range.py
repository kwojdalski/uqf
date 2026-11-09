"""A base port is a port, and so is every port it puts a process on (#2609).

`--base-port` took any integer: 65500 listed ports up to 65574, -100 listed
negative ports as strings, and the MCP server's `base_port` had no check at
all. The option is range-checked, Runtime.resolve_base_port refuses what the
option cannot see, and resolving a row refuses an offset pushed past 65535.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.paths import UqsError
from uqs.runtimes import DEFAULT_RUNTIME, RUNTIMES
from uqs.stack.placeholders import resolve_process_config


@pytest.mark.parametrize("base", [0, -100, 65536, 10**20])
def test_the_runtime_refuses_a_base_that_is_not_a_port(base):
    with pytest.raises(ValueError, match="not a port"):
        RUNTIMES[DEFAULT_RUNTIME].resolve_base_port(base)


@pytest.mark.parametrize("base", [1, 7000, 65535])
def test_the_runtime_takes_a_base_in_range(base):
    assert RUNTIMES[DEFAULT_RUNTIME].resolve_base_port(base) == base


def test_an_offset_pushed_past_65535_is_refused_not_left_unresolved():
    row = {"procname": "rdb1", "port": "{KDBBASEPORT}+74"}
    with pytest.raises(UqsError, match=r"rdb1 would listen on port 65574"):
        resolve_process_config(row, {"KDBBASEPORT": "65500"})


def test_an_offset_within_range_resolves():
    row = {"procname": "rdb1", "port": "{KDBBASEPORT}+35"}
    assert resolve_process_config(row, {"KDBBASEPORT": "65500"})["port"] == "65535"


@pytest.mark.parametrize("base", ["-100", "0", "65536", "99999999999999999999"])
def test_the_option_refuses_a_base_that_is_not_a_port(base):
    result = CliRunner().invoke(cli.app, ["process", "list", f"--base-port={base}", "--json"])
    assert result.exit_code == 2, result.output
    assert "1<=x<=65535" in result.output


def test_process_list_refuses_a_base_that_pushes_a_process_past_65535():
    # The refusal itself is asserted above; this holds that the listing
    # reaches it rather than printing the unresolved cells.
    result = CliRunner().invoke(cli.app, ["process", "list", "--base-port=65500", "--json"])
    assert result.exit_code == 1
