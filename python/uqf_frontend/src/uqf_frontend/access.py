"""Features, the routes that reach them, and the policy that grants them (#2045).

Three things, kept in one module so that "what may this caller do" has one
answer for the server and for the browser alike.

FEATURES. What the frontend offers, in the words a policy is written in: one
per view (`desk`, `coverage`, `control`, ...) with the actions on it (`read`,
`write`). The browser shows or hides its views from the same names, through
GET /me/capabilities - and never mistakes that for enforcement: hiding a
button refuses nothing, this module refuses.

ROUTES. ROUTE_PERMISSIONS maps every guarded route - (method, template) - to
the one (feature, action) it needs. It is the ONLY place a route is tied to a
feature. A route that is not in it is refused with 403 by `app.guard`, never
allowed by default, and test_access.py fails on any route the app builds that
is missing here - so a route added later is closed until someone decides who
may use it.

POLICY. A Casbin RBAC policy (policy/model.conf, pycasbin) of grants and group
memberships, in Casbin's CSV:

    p, <subject>, <feature>, <action>     a grant; subject is a user, a group,
                                          `anonymous`, or `*` for everyone
    g, <user or group>, <group>           a membership

The packaged default (policy/default_policy.csv) is `p, *, *, *`: everyone may
do everything, which is what the frontend allowed before - so a deployment
that sets nothing behaves exactly as it did. UQF_FRONTEND_POLICY_FILE names a
deployment's own file. It is read strictly - an unknown feature, action, line
type or malformed name refuses to start, never skipped - and re-read when it
changes on disk, without a restart; a change that does not parse is logged
(never its content) and the last good policy stays in force.

This layer is ADDITIONAL to write_guard.py, never instead of it: the write
token and the Host check still stand in front of every write, whatever the
policy grants.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import casbin
from casbin.model import Model

from uqf_frontend.identity import (
    ANONYMOUS,
    EVERYONE,
    GROUP_PREFIX,
    Identity,
    group_subject,
    is_name,
    is_user,
    user_subject,
)

log = logging.getLogger(__name__)

__all__ = [
    "ACTIONS",
    "ADMIN",
    "FEATURES",
    "ROUTE_PERMISSIONS",
    "AccessPolicy",
    "Grant",
    "Membership",
    "PolicyStore",
    "admins",
    "parse",
    "permission_for",
    "render",
]

#: Every action a policy may grant.
ACTIONS = ("read", "write")

#: Every feature, and the actions it has. The browser's views are named after
#: these (web/src/capabilities.tsx).
FEATURES: dict[str, tuple[str, ...]] = {
    "coverage": ("read",),
    "backfills": ("read",),
    "desk": ("read",),
    "charts": ("read",),
    "compare": ("read",),
    "fleet": ("read",),
    "queue": ("read",),
    "connections": ("read",),
    "usage": ("read",),
    "services": ("read", "write"),
    "control": ("read", "write"),
    "activity": ("read", "write"),
    "settings": ("read", "write"),
}

#: The permission that makes a caller an administrator: whoever holds it may
#: change the policy itself, so a policy with nobody holding it cannot be
#: changed from the frontend again (see `admins`).
ADMIN = ("settings", "write")

#: Every guarded route, as (method, route template) -> (feature, action).
#: A HEAD is asked as its GET. See the module docstring.
ROUTE_PERMISSIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/coverage"): ("coverage", "read"),
    ("GET", "/ops/backfill"): ("backfills", "read"),
    ("POST", "/query"): ("desk", "read"),
    # prepared charts (#2051): the POST runs a chart over the caller's
    # parameters and changes nothing - a read, as /query is
    ("GET", "/charts"): ("charts", "read"),
    ("POST", "/charts/{name}"): ("charts", "read"),
    # a table on two endpoints (#2033): reads both sides and changes nothing
    ("POST", "/compare"): ("compare", "read"),
    ("GET", "/ops/processes"): ("fleet", "read"),
    ("GET", "/ops/queue"): ("queue", "read"),
    ("GET", "/ops/connections"): ("connections", "read"),
    ("GET", "/ops/clients"): ("connections", "read"),
    ("GET", "/ops/usage"): ("usage", "read"),
    # services: reads list targets and their files and push history; writes
    # change files on a server
    ("GET", "/services"): ("services", "read"),
    # workspaces (#2029): the same registry /services lists, and the probe of
    # the one a request names - so a caller refused the registry there is
    # refused it here too
    ("GET", "/workspaces"): ("services", "read"),
    ("GET", "/workspace"): ("services", "read"),
    ("GET", "/services/{target}/files"): ("services", "read"),
    # the ssh tunnel plan and its check (#2042): reads, like the file list
    ("GET", "/services/{target}/tunnel"): ("services", "read"),
    ("GET", "/services/{target}/tunnel/check"): ("services", "read"),
    ("GET", "/services/{target}/config/pushes"): ("services", "read"),
    ("GET", "/services/{target}/config/pushes/{push_id}"): ("services", "read"),
    ("GET", "/services/{target}/config/pushes/{push_id}/diff"): ("services", "read"),
    ("GET", "/services/{target}/config/pushes/{push_id}/restore"): ("services", "read"),
    ("DELETE", "/services/{target}/files"): ("services", "write"),
    ("PUT", "/services/{target}/files"): ("services", "write"),
    ("POST", "/services/{target}/config/pushes"): ("services", "write"),
    ("POST", "/services/{target}/config/pushes/{push_id}/close"): ("services", "write"),
    ("POST", "/services/{target}/config/pushes/{push_id}/restore"): ("services", "write"),
    # control: the status read, and every change to the stack
    ("GET", "/control"): ("control", "read"),
    ("POST", "/control/lifecycle/{action}"): ("control", "write"),
    ("PUT", "/control/processes/{process}/config"): ("control", "write"),
    ("PUT", "/control/worker/config"): ("control", "write"),
    ("POST", "/control/backfill"): ("control", "write"),
    # activity: reading the history, and a browser recording its own
    ("GET", "/activity"): ("activity", "read"),
    ("GET", "/activity/export"): ("activity", "read"),
    ("POST", "/activity/events"): ("activity", "write"),
    # settings (#2046): reading them, and changing the policy or the runtime
    # settings - ADMIN
    ("GET", "/settings"): ("settings", "read"),
    ("PUT", "/settings/access"): ("settings", "write"),
    ("PUT", "/settings/runtime"): ("settings", "write"),
}


def permission_for(method: str, template: str | None) -> tuple[str, str] | None:
    """The (feature, action) a route needs, or None when it has no mapping -
    which the guard refuses."""
    if template is None:
        return None
    verb = "GET" if method.upper() == "HEAD" else method.upper()
    return ROUTE_PERMISSIONS.get((verb, template))


# ------------------------------------------------------------------ the policy


@dataclass(frozen=True, order=True)
class Grant:
    """`p, subject, feature, action`."""

    subject: str
    feature: str
    action: str


@dataclass(frozen=True, order=True)
class Membership:
    """`g, member, group`."""

    member: str
    group: str


@dataclass(frozen=True)
class AccessPolicy:
    """A whole policy, validated: grants and memberships, each once."""

    grants: tuple[Grant, ...]
    memberships: tuple[Membership, ...]


_SPLIT = re.compile(r"\s*,\s*")


def _check_subject(subject: str, where: str) -> None:
    if subject not in (EVERYONE, ANONYMOUS) and not is_name(subject):
        raise ValueError(f"{where}: {subject!r} is not a user or group name")


def _check_grant(grant: Grant, where: str) -> None:
    _check_subject(grant.subject, where)
    if grant.feature != "*" and grant.feature not in FEATURES:
        raise ValueError(
            f"{where}: unknown feature {grant.feature!r} - one of {', '.join(FEATURES)} or *"
        )
    allowed = ACTIONS if grant.feature == "*" else FEATURES[grant.feature]
    if grant.action != "*" and grant.action not in allowed:
        raise ValueError(
            f"{where}: {grant.feature!r} has no action {grant.action!r} - "
            f"one of {', '.join(allowed)} or *"
        )


def _check_membership(m: Membership, where: str) -> None:
    for name in (m.member, m.group):
        if name == EVERYONE or not is_name(name) and name != ANONYMOUS:
            raise ValueError(f"{where}: {name!r} is not a user or group name")
    if m.group == ANONYMOUS:
        raise ValueError(f"{where}: `anonymous` is a caller, not a group")
    if m.member == m.group:
        raise ValueError(f"{where}: {m.member!r} cannot be a member of itself")


def validate(policy: AccessPolicy) -> AccessPolicy:
    """*policy* with each rule checked, duplicates removed and rules sorted -
    or ValueError naming the first bad rule."""
    for i, grant in enumerate(policy.grants):
        _check_grant(grant, f"grant {i + 1}")
    for i, member in enumerate(policy.memberships):
        _check_membership(member, f"membership {i + 1}")
    return AccessPolicy(
        grants=tuple(sorted(set(policy.grants))),
        memberships=tuple(sorted(set(policy.memberships))),
    )


def parse(text: str, source: str = "policy") -> AccessPolicy:
    """Read Casbin CSV strictly. Every line is a comment, blank, a grant or a
    membership; anything else is an error naming its line - Casbin itself
    would skip it silently, and a skipped line in an access policy is a rule
    nobody knows is missing."""
    grants: list[Grant] = []
    memberships: list[Membership] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{source} line {number}"
        fields = _SPLIT.split(line)
        if fields[0] == "p" and len(fields) == 4:
            grant = Grant(*fields[1:])
            _check_grant(grant, where)
            grants.append(grant)
        elif fields[0] == "g" and len(fields) == 3:
            member = Membership(*fields[1:])
            _check_membership(member, where)
            memberships.append(member)
        else:
            raise ValueError(
                f"{where}: expected `p, <subject>, <feature>, <action>` or `g, <member>, <group>`"
            )
    return validate(AccessPolicy(tuple(grants), tuple(memberships)))


#: The header every rendered policy file starts with.
_RENDERED_HEADER = """\
# uqf frontend access policy (#2045), written by the frontend (access.render);
# hand edits are read too, and a change takes effect without a restart.
#   p, <subject>, <feature>, <action>   grant (subject: user, group, anonymous, *)
#   g, <user or group>, <group>         membership
"""


def render(policy: AccessPolicy) -> str:
    """*policy* as the Casbin CSV `parse` reads back."""
    lines = [f"p, {g.subject}, {g.feature}, {g.action}" for g in policy.grants]
    lines += [f"g, {m.member}, {m.group}" for m in policy.memberships]
    return _RENDERED_HEADER + "\n".join(lines) + "\n"


def _resource(name: str) -> str:
    return resources.files("uqf_frontend").joinpath("policy", name).read_text(encoding="utf-8")


def model_text() -> str:
    """The packaged Casbin model."""
    return _resource("model.conf")


def default_policy() -> AccessPolicy:
    """The packaged default: today's behaviour, everyone may do everything."""
    return parse(_resource("default_policy.csv"), "default_policy.csv")


def _namespaced(name: str, groups: set[str]) -> tuple[str, ...]:
    """*name*, as written in a policy, in the enforcer's subject spaces (#2525).

    A policy spells users and groups alike. A name some membership makes a
    group is only that group, so a user the proxy names after it matches
    nothing of the group's; any other name may be a user or a group the
    proxy supplies, so it is both. `*` stays the matcher's own word."""
    if name == EVERYONE:
        return (name,)
    if name in groups:
        return (group_subject(name),)
    return (user_subject(name), group_subject(name))


def enforcer(policy: AccessPolicy) -> casbin.Enforcer:
    """A Casbin enforcer over *policy*, holding no file and logging nothing.
    Its subjects are namespaced - see `_namespaced`; ask it with
    Identity.subjects."""
    model = Model()
    model.load_model_from_text(model_text())
    e = casbin.Enforcer(model, enable_log=False)
    groups = {m.group for m in policy.memberships}
    for grant in policy.grants:
        for subject in _namespaced(grant.subject, groups):
            e.add_policy(subject, grant.feature, grant.action)
    for member in policy.memberships:
        for subject in _namespaced(member.member, groups):
            e.add_grouping_policy(subject, group_subject(member.group))
    return e


def _allowed(e: casbin.Enforcer, subjects: Iterable[str], feature: str, action: str) -> bool:
    return any(e.enforce(subject, feature, action) for subject in subjects)


#: A subject no policy can name (`*` is not a name character), so only a grant
#: to everyone reaches it.
_NOBODY = user_subject("*nobody*")


def admins(
    policy: AccessPolicy,
    *,
    identity_header: bool,
    groups_header: bool,
    known_groups: Iterable[str] = (),
) -> set[str]:
    """Who could change the policy under *policy*: the callers that could
    reach ADMIN on this deployment. `*` in the answer means everyone.

    `anonymous` counts whenever it reaches ADMIN: some caller is always
    anonymous. Without a trusted identity header EVERY caller is, so nothing
    else counts. With one, a name counts only as something the identity
    layer can actually produce (#2071):

      a user   a name that is not a group and that identity.is_user accepts -
               a proxy never names a user `ops/admins`, `org:alice` or
               anything over 64 characters, so those are nobody;
      a group  only when the proxy supplies groups (the groups header), since
               it could then put a caller in it - and then any name the
               policy accepts may be such a group.

    A group's members count in their own right, as users. *known_groups*
    names groups beyond the policy's own - the ones a change is removing the
    last member of, which would otherwise read as users."""
    e = enforcer(policy)
    feature, action = ADMIN
    if e.enforce(_NOBODY, feature, action):
        return {EVERYONE}
    found = {ANONYMOUS} if e.enforce(user_subject(ANONYMOUS), feature, action) else set()
    if not identity_header:
        return found
    groups = {m.group for m in policy.memberships} | set(known_groups)
    names = {g.subject for g in policy.grants} | {m.member for m in policy.memberships}
    names -= {EVERYONE, ANONYMOUS}

    def subjects(name: str) -> list[str]:
        """The subjects a caller could reach *name* as: the user, when the
        identity layer can produce that user, and the group, when the proxy
        supplies groups."""
        reach = [user_subject(name)] if name not in groups and is_user(name) else []
        if groups_header and is_name(name):
            reach.append(group_subject(name))
        return reach

    return found | {
        name for name in names if any(e.enforce(s, feature, action) for s in subjects(name))
    }


# ------------------------------------------------------------------ the store


@dataclass(frozen=True)
class Loaded:
    """One policy in force, and its enforcer."""

    policy: AccessPolicy
    enforcer: casbin.Enforcer
    #: The file's (mtime_ns, size) when read; None for the packaged default.
    stamp: tuple[int, int] | None


class PolicyStore:
    """The policy in force. Thread-safe: sync routes run in a pool.

    *path* None serves the packaged default, which cannot be changed from the
    frontend. A *path* that does not exist refuses to start: a deployment
    that named its own policy must not fall back to everyone-may-do-anything
    because the file went missing.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        #: Held by an editor across read-check-write (settings_admin), so two
        #: changes cannot both be checked against the same version.
        self.lock = threading.Lock()
        #: The last reload's failure, as its type name and line - never the
        #: file's content - or None.
        self.last_error: str | None = None
        if path is None:
            policy = default_policy()
            self._loaded = Loaded(policy, enforcer(policy), None)
            return
        if not path.is_file():
            raise ValueError(
                f"UQF_FRONTEND_POLICY_FILE names {path}, which does not exist - copy the "
                f"packaged default ({resources.files('uqf_frontend') / 'policy'}"
                "/default_policy.csv) there and edit it, or unset the variable"
            )
        self._loaded = self._read(path)

    @property
    def editable(self) -> bool:
        """Can the policy be changed from the frontend?"""
        return self.path is not None

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int]:
        st = path.stat()
        return st.st_mtime_ns, st.st_size

    @classmethod
    def _read(cls, path: Path) -> Loaded:
        stamp = cls._stamp(path)
        policy = parse(path.read_text(encoding="utf-8"), path.name)
        return Loaded(policy, enforcer(policy), stamp)

    def current(self) -> Loaded:
        """The policy in force, re-read first when the file changed."""
        loaded = self._loaded
        if self.path is None:
            return loaded
        try:
            stamp = self._stamp(self.path)
        except OSError as exc:
            self._reload_failed(exc)
            return loaded
        if stamp == loaded.stamp:
            return loaded
        with self._lock:
            if self._loaded.stamp != stamp:
                try:
                    self._loaded = self._read(self.path)
                    self.last_error = None
                    log.info("access policy reloaded from %s", self.path)
                except (OSError, ValueError) as exc:
                    self._reload_failed(exc)
            return self._loaded

    def _reload_failed(self, exc: Exception) -> None:
        # The message names a line and a reason, never a line's content
        # beyond the one name it refused.
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        if reason != self.last_error:
            log.warning("access policy not reloaded, the last good one stays: %s", reason)
        self.last_error = reason

    def allowed(self, who: Identity, feature: str, action: str) -> bool:
        return _allowed(self.current().enforcer, who.subjects, feature, action)

    def capabilities(self, who: Identity) -> list[tuple[str, str]]:
        """Every (feature, action) *who* is granted."""
        e = self.current().enforcer
        return [
            (feature, action)
            for feature, actions in FEATURES.items()
            for action in actions
            if _allowed(e, who.subjects, feature, action)
        ]

    def groups(self, who: Identity) -> list[str]:
        """*who*'s groups: the proxy's, then the policy's, each once."""
        e = self.current().enforcer
        found = list(who.groups)
        for subject in who.subjects:
            for role in e.get_implicit_roles_for_user(subject):
                role = role.removeprefix(GROUP_PREFIX)
                if role not in found:
                    found.append(role)
        return found

    def replace(self, policy: AccessPolicy) -> tuple[AccessPolicy, AccessPolicy]:
        """Write *policy* to the file atomically and put it in force at once;
        returns (before, after). Validation is the caller's - this writes
        what it is given, after `validate`."""
        if self.path is None:
            raise ValueError(
                "the packaged default policy cannot be changed - set "
                "UQF_FRONTEND_POLICY_FILE to a policy file of this deployment's own"
            )
        after = validate(policy)
        with self._lock:
            before = self._loaded.policy
            write_atomically(self.path, render(after))
            self._loaded = Loaded(after, enforcer(after), self._stamp(self.path))
            self.last_error = None
        return before, after


def write_atomically(path: Path, text: str) -> None:
    """Replace *path* with *text* so a reader sees the old file or the new
    one, never half of either: a temporary file in the same directory,
    flushed to disk, then renamed over it. Keeps the file's permissions."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
