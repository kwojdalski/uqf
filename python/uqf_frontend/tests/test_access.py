"""Trusted identity, the Casbin access policy and /me/capabilities (#2045).

The properties that carry the security weight are tested as an attacker
would try them: a spoofed identity header from an address that is not the
proxy, the same header twice, X-Forwarded-For claiming to be the proxy, a
route with no feature, a refused feature called directly - and that the
default policy changes nothing for a deployment that configures nothing.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from uqf_frontend import access, authz, identity, queries
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway

PROXY = "10.0.0.5"
ELSEWHERE = "10.0.0.99"
HEADER = "x-forwarded-user"
GROUPS = "x-forwarded-groups"
TOKEN = "t" * 64

#: Two groups with different views, and one admin.
POLICY = """\
# viewers read the desk; operators run the stack; admins do everything
p, viewers, desk, read
p, viewers, coverage, read
p, operators, control, read
p, operators, control, write
p, operators, fleet, read
p, admins, *, *
g, alice, admins
g, bob, viewers
g, carol, operators
"""


def _policy_file(tmp_path: Path, text: str = POLICY) -> Path:
    path = tmp_path / "policy.csv"
    path.write_text(text)
    return path


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "max_rows": 5000,
        "trusted_identity_header": HEADER,
        "trusted_proxies": (PROXY,),
        "policy_file": _policy_file(tmp_path),
        "activity_db": tmp_path / "activity.sqlite3",
    }
    base.update(kw)
    return Settings(**base)


def _client(settings: Settings, peer: str = PROXY) -> TestClient:
    app = create_app(
        gateway=FakeGateway({queries.BROWSE: []}), settings=settings, fleet=FakeFleet()
    )
    return TestClient(app, client=(peer, 40000))


def _rows(db: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute("SELECT * FROM activity ORDER BY seq"))


# --- who is calling ----------------------------------------------------------


def test_the_proxy_header_from_the_proxy_names_the_user(tmp_path):
    c = _client(_settings(tmp_path))
    me = c.get("/me/capabilities", headers={HEADER: "bob"}).json()
    assert (me["identity"], me["authenticated"]) == ("bob", True)
    assert me["groups"] == ["viewers"]


def test_a_spoofed_header_from_another_address_is_anonymous(tmp_path):
    """The acceptance criterion: a caller that reaches the port without the
    proxy can send any header, so from anywhere else it is ignored."""
    c = _client(_settings(tmp_path), peer=ELSEWHERE)
    me = c.get("/me/capabilities", headers={HEADER: "alice"}).json()
    assert (me["identity"], me["authenticated"]) == ("anonymous", False)
    assert me["capabilities"] == []
    resp = c.get("/control", headers={HEADER: "alice"})
    assert resp.status_code == 403
    assert "not granted to anonymous" in resp.json()["detail"]


def test_x_forwarded_for_naming_the_proxy_changes_nothing(tmp_path):
    """Only the TCP peer counts - never what the request says about itself."""
    c = _client(_settings(tmp_path), peer=ELSEWHERE)
    me = c.get("/me/capabilities", headers={HEADER: "alice", "x-forwarded-for": PROXY}).json()
    assert me["identity"] == "anonymous"


def test_a_repeated_identity_header_is_anonymous(tmp_path):
    """A proxy that appends to a client's header rather than replacing it
    must not let the client's value through."""
    c = _client(_settings(tmp_path))
    me = c.get("/me/capabilities", headers=[(HEADER, "bob"), (HEADER, "alice")]).json()
    assert me["identity"] == "anonymous"


@pytest.mark.parametrize("value", ["", "anonymous", "*", "bob smith", "a" * 65, "x,y", "eve!"])
def test_a_header_that_is_not_a_user_name_is_anonymous(tmp_path, value):
    c = _client(_settings(tmp_path))
    assert c.get("/me/capabilities", headers={HEADER: value}).json()["identity"] == "anonymous"


def test_without_a_configured_header_no_header_is_an_identity(tmp_path):
    """The deployment this tree always ran: every caller anonymous - the
    claimed x-uqf-user included."""
    c = _client(
        _settings(tmp_path, trusted_identity_header="", trusted_proxies=(), policy_file=None)
    )
    me = c.get("/me/capabilities", headers={HEADER: "alice", authz.IDENTITY_HEADER: "alice"}).json()
    assert me["identity"] == "anonymous"


def test_the_proxy_groups_header_adds_groups(tmp_path):
    c = _client(_settings(tmp_path, trusted_groups_header=GROUPS))
    me = c.get(
        "/me/capabilities", headers={HEADER: "dave", GROUPS: "operators, bad group,org:team"}
    ).json()
    assert me["identity"] == "dave"
    assert me["groups"] == ["operators", "org:team"]
    assert {"feature": "control", "action": "write"} in me["capabilities"]


def test_a_groups_header_from_another_address_is_ignored(tmp_path):
    c = _client(_settings(tmp_path, trusted_groups_header=GROUPS), peer=ELSEWHERE)
    me = c.get("/me/capabilities", headers={HEADER: "dave", GROUPS: "admins"}).json()
    assert (me["identity"], me["groups"], me["capabilities"]) == ("anonymous", [], [])


def test_an_ipv4_mapped_peer_matches_its_ipv4_proxy():
    nets = identity.parse_proxies(["10.0.0.0/24"])
    assert identity.peer_is_trusted("::ffff:10.0.0.7", nets)
    assert not identity.peer_is_trusted("testclient", nets)
    assert not identity.peer_is_trusted(None, nets)


# --- configuration that could not mean what it says ---------------------------


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"trusted_identity_header": HEADER}, "TRUSTED_PROXIES is not"),
        ({"trusted_proxies": (PROXY,)}, "TRUSTED_IDENTITY_HEADER is not"),
        (
            {"trusted_identity_header": "authorization", "trusted_proxies": (PROXY,)},
            "not an identity",
        ),
        (
            {"trusted_identity_header": "cookie", "trusted_proxies": (PROXY,)},
            "not an identity",
        ),
        ({"trusted_identity_header": HEADER, "trusted_proxies": ("10.0.0.300",)}, "CIDR"),
        ({"trusted_groups_header": GROUPS}, "only believed alongside a user"),
        (
            {
                "trusted_identity_header": HEADER,
                "trusted_groups_header": HEADER,
                "trusted_proxies": (PROXY,),
            },
            "must differ",
        ),  # fmt: skip
        ({"trusted_identity_header": "X-User", "trusted_proxies": (PROXY,)}, "lower-case"),
    ],
)
def test_a_trust_configuration_that_cannot_work_refuses_to_start(kw, message):
    with pytest.raises(ValueError, match=message):
        Settings(**kw)


def test_the_environment_names_the_trust_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("UQF_FRONTEND_TRUSTED_IDENTITY_HEADER", "X-Forwarded-User")
    monkeypatch.setenv("UQF_FRONTEND_TRUSTED_GROUPS_HEADER", "X-Forwarded-Groups")
    monkeypatch.setenv("UQF_FRONTEND_TRUSTED_PROXIES", "127.0.0.1, 10.1.0.0/16")
    monkeypatch.setenv("UQF_FRONTEND_POLICY_FILE", str(tmp_path / "p.csv"))
    s = Settings.from_env()
    assert s.trusted_identity_header == "x-forwarded-user"
    assert s.trusted_groups_header == "x-forwarded-groups"
    assert s.trusted_proxies == ("127.0.0.1", "10.1.0.0/16")
    assert s.policy_file == tmp_path / "p.csv"


# --- the default reproduces today --------------------------------------------


def test_the_default_policy_lets_everyone_do_everything():
    store = access.PolicyStore(None)
    every = [(f, a) for f, actions in access.FEATURES.items() for a in actions]
    assert store.capabilities(identity.Identity()) == every
    assert not store.editable


def test_the_packaged_default_is_exactly_everyone_everything():
    assert access.default_policy() == access.AccessPolicy(
        grants=(access.Grant("*", "*", "*"),), memberships=()
    )


# --- every route has one feature ------------------------------------------------


def _api_routes() -> list[APIRoute]:
    app = create_app(gateway=FakeGateway({}), settings=Settings(), fleet=FakeFleet())
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_every_guarded_route_maps_to_a_feature_and_no_stale_entry_remains():
    """A route added without a mapping is refused to everyone at runtime;
    this names it in CI first. A mapping left behind for a removed route
    fails too."""
    guarded = {
        (method, r.path)
        for r in _api_routes()
        if authz.unguarded_reason(r.endpoint) is None
        for method in r.methods or ()
    }
    assert guarded - set(access.ROUTE_PERMISSIONS) == set(), "routes with no feature"
    assert set(access.ROUTE_PERMISSIONS) - guarded == set(), "mappings with no route"


def test_every_mapping_names_a_real_feature_and_action():
    assert all(set(actions) <= set(access.ACTIONS) for actions in access.FEATURES.values())
    for feature, action in access.ROUTE_PERMISSIONS.values():
        assert action in access.FEATURES[feature]


def test_a_route_with_no_mapping_is_refused_and_audited(tmp_path):
    """Unmapped is closed, never open - even under the allow-everything
    default policy."""
    settings = Settings(activity_db=tmp_path / "a.sqlite3")
    app = create_app(gateway=FakeGateway({}), settings=settings, fleet=FakeFleet())
    app.add_api_route("/unmapped", lambda: {"ok": True}, methods=["GET"])
    c = TestClient(app)
    resp = c.get("/unmapped")
    assert resp.status_code == 403
    assert "no feature" in resp.json()["detail"]
    (row,) = _rows(tmp_path / "a.sqlite3")
    assert (row["action"], row["outcome"], row["target"]) == (
        "authz.refused",
        "refused",
        "/unmapped",
    )


# --- refusals: 403 on a direct call, and audited --------------------------------


def test_a_refused_read_is_403_and_audited_with_the_verified_user(tmp_path):
    c = _client(_settings(tmp_path))
    resp = c.get("/ops/queue", headers={HEADER: "bob"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "Forbidden"
    (row,) = _rows(tmp_path / "activity.sqlite3")
    assert row["action"] == "authz.refused"
    assert row["claimed_user"] == "bob"
    assert row["status_code"] == 403
    assert '"permission": "queue:read"' in row["detail"]
    assert '"identity": "verified"' in row["detail"]


def test_a_granted_read_is_allowed_and_not_audited(tmp_path):
    c = _client(_settings(tmp_path))
    assert (
        c.post(
            "/query", json={"table": "trades", "filters": []}, headers={HEADER: "bob"}
        ).status_code
        == 200
    )
    assert _rows(tmp_path / "activity.sqlite3") == []


def test_a_refused_write_is_one_row_naming_the_permission(tmp_path):
    """A write already has its own audit row; the refusal settles it rather
    than adding a second one."""
    c = _client(
        _settings(
            tmp_path, enable_writes=True, write_token=TOKEN,
            allowed_hosts=("testserver",),
        )
    )  # fmt: skip
    resp = c.post(
        "/control/process/stop",
        json={"procs": "rdb1"},
        headers={HEADER: "bob", "authorization": f"Bearer {TOKEN}"},
    )
    assert resp.status_code == 403
    (row,) = _rows(tmp_path / "activity.sqlite3")
    assert (row["action"], row["outcome"], row["claimed_user"]) == (
        "process.stop",
        "refused",
        "bob",
    )
    assert '"permission": "control:write"' in row["detail"]
    assert TOKEN not in repr(tuple(row))


def test_the_write_token_still_stands_in_front_of_a_granted_write(tmp_path):
    """The policy is a layer on top of write_guard, never instead of it:
    an admin without the token is still 401."""
    c = _client(
        _settings(
            tmp_path, enable_writes=True, write_token=TOKEN,
            allowed_hosts=("testserver",),
        )
    )  # fmt: skip
    resp = c.post("/control/process/stop", json={"procs": "rdb1"}, headers={HEADER: "alice"})
    assert resp.status_code == 401


def test_the_host_check_still_stands_in_front_of_the_policy(tmp_path):
    c = _client(_settings(tmp_path, enable_writes=True, write_token=TOKEN))
    resp = c.get("/me/capabilities", headers={HEADER: "alice", "host": "evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "HostNotAllowed"


# --- two groups, two views -------------------------------------------------------


def _granted(c: TestClient, user: str) -> set[tuple[str, str]]:
    me = c.get("/me/capabilities", headers={HEADER: user}).json()
    return {(cap["feature"], cap["action"]) for cap in me["capabilities"]}


def test_two_groups_see_different_capabilities_and_the_api_agrees(tmp_path):
    """What /me/capabilities says is what the routes do - checked against
    the routes themselves, not against the policy text."""
    c = _client(_settings(tmp_path))
    assert _granted(c, "bob") == {("desk", "read"), ("coverage", "read")}
    assert _granted(c, "carol") == {("control", "read"), ("control", "write"), ("fleet", "read")}
    assert c.get("/control", headers={HEADER: "bob"}).status_code == 403
    assert c.get("/control", headers={HEADER: "carol"}).status_code == 200
    query = {"table": "trades", "filters": []}
    assert c.post("/query", json=query, headers={HEADER: "bob"}).status_code == 200
    assert c.post("/query", json=query, headers={HEADER: "carol"}).status_code == 403


def test_capabilities_never_carry_a_secret(tmp_path):
    c = _client(
        _settings(
            tmp_path, enable_writes=True, write_token=TOKEN, allowed_hosts=("testserver",),
            passwd="gw-secret", data_passwd="data-secret",
        )
    )  # fmt: skip
    body = c.get("/me/capabilities", headers={HEADER: "alice"}).text
    for secret in (TOKEN, "gw-secret", "data-secret"):
        assert secret not in body
    assert set(c.get("/me/capabilities").json()) == {
        "identity",
        "authenticated",
        "groups",
        "capabilities",
        "features",
        "poll_seconds",
    }


# --- the policy file -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("p, bob, nosuch, read\n", "unknown feature"),
        ("p, bob, desk, write\n", "no action 'write'"),
        ("p, bob, desk\n", "expected"),
        ("x, bob, desk, read\n", "expected"),
        ("g, bob\n", "expected"),
        ("p, bob smith, desk, read\n", "not a user or group name"),
        ("g, bob, *\n", "not a user or group name"),
        ("g, bob, bob\n", "member of itself"),
        ("g, bob, anonymous\n", "a caller, not a group"),
    ],
)
def test_a_policy_line_that_does_not_parse_refuses_to_start(tmp_path, text, message):
    """Casbin would skip a line it does not understand; in an access policy
    a skipped line is a rule nobody knows is missing."""
    with pytest.raises(ValueError, match=message):
        access.PolicyStore(_policy_file(tmp_path, text))


def test_a_named_policy_file_that_is_missing_refuses_to_start(tmp_path):
    """Never a silent fall back to everyone-may-do-everything."""
    with pytest.raises(ValueError, match="does not exist"):
        create_app(
            gateway=FakeGateway({}),
            settings=Settings(policy_file=tmp_path / "gone.csv"),
            fleet=FakeFleet(),
        )


def _touch_later(path: Path, text: str) -> None:
    before = path.stat().st_mtime_ns
    path.write_text(text)
    while path.stat().st_mtime_ns == before:  # a coarse clock
        time.sleep(0.01)
        path.write_text(text)


def test_an_edit_on_disk_takes_effect_without_a_restart(tmp_path):
    c = _client(_settings(tmp_path))
    assert _granted(c, "bob") == {("desk", "read"), ("coverage", "read")}
    _touch_later(tmp_path / "policy.csv", POLICY + "p, viewers, usage, read\n")
    assert ("usage", "read") in _granted(c, "bob")


def test_a_broken_edit_keeps_the_last_good_policy(tmp_path, caplog):
    c = _client(_settings(tmp_path))
    _touch_later(tmp_path / "policy.csv", POLICY + "p, viewers, nosuch, read\n")
    assert _granted(c, "bob") == {("desk", "read"), ("coverage", "read")}
    assert "not reloaded" in caplog.text


def test_parse_and_render_round_trip():
    policy = access.parse(POLICY)
    assert access.parse(access.render(policy)) == policy


# --- who counts as an admin --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "identity_header", "groups_header", "expected"),
    [
        ("p, *, *, *\n", False, False, {"*"}),
        ("p, *, settings, write\n", True, False, {"*"}),
        ("p, anonymous, settings, write\n", False, False, {"anonymous"}),
        # a named admin is unreachable while every caller is anonymous
        ("p, alice, settings, write\n", False, False, set()),
        ("p, alice, settings, write\n", True, False, {"alice"}),
        (POLICY, True, False, {"alice"}),
        # a name only in a grant may be a user, so it counts
        ("p, admins, *, *\n", True, False, {"admins"}),
        # a group counts through its members - or itself, when the proxy
        # supplies groups and so could put a caller in it
        ("p, root, *, *\ng, ops, root\n", True, False, {"ops"}),
        ("p, root, *, *\ng, ops, root\n", True, True, {"ops", "root"}),
        ("p, viewers, desk, read\ng, bob, viewers\n", True, True, set()),
    ],
)
def test_admins(text, identity_header, groups_header, expected):
    assert (
        access.admins(
            access.parse(text), identity_header=identity_header, groups_header=groups_header
        )
        == expected
    )
