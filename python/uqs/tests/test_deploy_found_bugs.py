"""Regressions for three deploy bugs found together (#1654, #1655, #1656)."""

from __future__ import annotations

import http.server
import threading
import urllib.request

from uqs.deploy import fetch, prune, verify

#: What kola hands Python for q's long null 0N: an ordinary int (measured on KDB-X).
Q_LONG_NULL = -(2**63)


def test_a_pipeline_without_its_etl_tree_fails_verify():
    """#1654: the trap's null used to read as `ok`."""

    def query(expr: str, port: int) -> object:
        if expr == verify.IDENTITY_EXPR:
            return "rdb1"
        if expr == verify.LIBRARY_EXPR:
            return verify.LIBRARY_EXPECTED
        return Q_LONG_NULL

    p = verify.check_process(verify.ProcessCheck("rdb1", 5002, True), query, live=True)
    assert p.etl == "not loaded"
    assert not p.ok and "ETL" in p.error and "live-sources" in p.error


def test_the_traps_answer_the_null_kola_reads_as_null():
    assert verify.ETL_EXPR.endswith(";0n]") and verify.LIVE_EXPR.endswith(";0n]")


def _rel(name: str) -> dict:
    return {"release": name, "bytes": 1, "status": "deployed", "previous_release": None}


A, B, C = (f"2026100{i}T000000Z-{c * 12}" for i, c in ((1, "a"), (2, "b"), (3, "c")))


def test_a_directory_not_named_like_a_release_takes_no_keep_slot():
    """#1655: `backup-before-upgrade` sorts after every id."""
    names = [A, B, C, "backup-before-upgrade"]
    result = prune.decide({"current": C, "releases": [_rel(n) for n in names]}, 2)
    assert result["removed"] == [A], "B is one of the newest 2 releases"
    why = {k["release"]: k["why"] for k in result["kept"]}
    assert why[B] == "one of the newest 2"
    assert why["backup-before-upgrade"] == "not named like a release, so never removed"


def test_a_redirect_to_another_host_carries_no_token():
    """#1656: urllib copies Authorization to whatever host a redirect names."""
    seen: dict[str, str | None] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            seen[self.path] = self.headers.get("Authorization")
            if self.path == "/same":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}/kept")
            elif self.path == "/away":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/dropped")
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the stdlib's name
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for path in ("/same", "/away"):
            req = urllib.request.Request(
                f"http://localhost:{port}{path}", headers={"Authorization": "Bearer T"}
            )
            fetch._OPENER.open(req, timeout=5).read()
    finally:
        server.shutdown()
    assert seen["/kept"] == "Bearer T", "the same host keeps it"
    assert seen["/dropped"] is None, "another host never sees it"
