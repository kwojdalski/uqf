"""Who is calling: a trusted proxy's word, or nobody in particular (#2045).

The frontend does not authenticate anyone itself. A proxy in front of it -
oauth2-proxy, Authelia, Tailscale serve - does, and passes the user it
authenticated on in a header. That header is only as good as the guarantee
that the proxy set it, so it is believed under two conditions together, and
otherwise the caller is ANONYMOUS:

  configured  UQF_FRONTEND_TRUSTED_IDENTITY_HEADER names the header. Unset,
              no header is ever an identity - the deployment this tree has
              always run, where every caller is anonymous.
  from proxy  the request's DIRECT peer - the TCP connection's address, never
              X-Forwarded-For or anything else the request says about itself -
              is in UQF_FRONTEND_TRUSTED_PROXIES. A caller that reaches the
              port without the proxy can set any header it likes, so a header
              from anywhere else is ignored, whatever it says.

A header present more than once, empty, or not shaped like a user name is
ignored too (anonymous): a proxy that appended to a header a client sent,
rather than replacing it, must not let the client's value through.

The groups header (UQF_FRONTEND_TRUSTED_GROUPS_HEADER, optional) is believed
under the same conditions and only alongside a believed user.

`x-uqf-user` (authz.IDENTITY_HEADER) is not an identity and never reaches the
policy: it is still recorded in the activity history as a CLAIM, for a caller
with no verified identity, and labelled as one there.

Nothing here logs or returns a header value other than the user and group
names it accepted, and it reads no other header.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "ANONYMOUS",
    "EVERYONE",
    "FORBIDDEN_HEADERS",
    "GROUP_PREFIX",
    "USER_PREFIX",
    "Identity",
    "group_subject",
    "is_name",
    "is_user",
    "parse_proxies",
    "peer_is_trusted",
    "resolve",
    "user_subject",
]

#: The identity of every caller the frontend cannot vouch for.
ANONYMOUS = "anonymous"

#: The policy's word for every caller, anonymous ones included. Never a user.
EVERYONE = "*"

#: A user name, as accepted from a proxy: what activity.py stores as a user.
_USER = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
#: A group name: a user name's characters, plus the `:` and `/` an identity
#: provider's team names carry (`org:team`, `org/team`).
_GROUP = re.compile(r"^[A-Za-z0-9_.@:/-]{1,128}$")

#: Most groups taken from one request's groups header.
MAX_GROUPS = 32

#: Headers that must never be named as the identity or groups header: a
#: credential, a cookie or the Host would make the frontend read a secret, or
#: something every client controls, as a user name.
FORBIDDEN_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "host", "x-forwarded-for"}
)

#: The namespaces a policy subject is asked in (#2525). A user name and a
#: group name share one spelling in a policy file, so the enforcer is built
#: and asked with these prefixes: a user can only ever be the user.
USER_PREFIX = "user:"
GROUP_PREFIX = "group:"

ProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def is_user(value: str) -> bool:
    """Is *value* a user name - and not one of the reserved words?"""
    return bool(_USER.match(value)) and value not in (ANONYMOUS, EVERYONE)


def is_name(value: str) -> bool:
    """Is *value* a user or group name - and not one of the reserved words?"""
    return bool(_GROUP.match(value)) and value not in (ANONYMOUS, EVERYONE)


def user_subject(name: str) -> str:
    """*name* as a user, in the enforcer's subject space."""
    return USER_PREFIX + name


def group_subject(name: str) -> str:
    """*name* as a group, in the enforcer's subject space."""
    return GROUP_PREFIX + name


@dataclass(frozen=True)
class Identity:
    """The caller, as far as the frontend can vouch for it."""

    user: str = ANONYMOUS
    #: Groups the proxy says the user is in; the policy adds its own.
    groups: tuple[str, ...] = field(default_factory=tuple)
    #: True only when a trusted proxy named the user.
    authenticated: bool = False

    @property
    def subjects(self) -> tuple[str, ...]:
        """What the policy is asked about: the user, then each proxy group,
        each in its own namespace (#2525) - a user the proxy names `admins`
        is `user:admins`, never the group `group:admins`."""
        return (user_subject(self.user), *(group_subject(g) for g in self.groups))


ANONYMOUS_IDENTITY = Identity()


def parse_proxies(raw: Iterable[str]) -> tuple[ProxyNetwork, ...]:
    """The trusted proxy addresses, as networks. A bare address is a /32 or
    /128. A malformed entry is an error, never skipped: a typo must not
    quietly leave a proxy untrusted - or the wrong one trusted."""
    networks: list[ProxyNetwork] = []
    for entry in raw:
        text = entry.strip()
        if not text:
            continue
        try:
            networks.append(_unmapped(ipaddress.ip_network(text, strict=False)))
        except ValueError:
            raise ValueError(
                f"UQF_FRONTEND_TRUSTED_PROXIES must list IP addresses or CIDR ranges, got {text!r}"
            ) from None
    return tuple(networks)


#: Every IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) - an IPv4 address,
#: as a dual-stack `::` socket shows an IPv4 peer.
_MAPPED = ipaddress.IPv6Network("::ffff:0:0/96")


def _unmapped(network: ProxyNetwork) -> ProxyNetwork:
    """*network*, as IPv4 when it lies wholly in the IPv4-mapped range (#2073).

    `::ffff:10.0.0.5` and `::ffff:10.0.0.0/104` name the IPv4 proxies
    `10.0.0.5` and `10.0.0.0/8`, and a peer is compared in its IPv4 form, so
    a mapped entry stored as written would be accepted and never match. A
    wider IPv6 range is kept as written: peer_is_trusted tests a mapped
    peer as written too."""
    if isinstance(network, ipaddress.IPv6Network) and network.subnet_of(_MAPPED):
        mapped = network.network_address.ipv4_mapped
        assert mapped is not None  # network_address is inside _MAPPED
        return ipaddress.IPv4Network((mapped, network.prefixlen - 96))
    return network


def peer_is_trusted(peer: str | None, proxies: tuple[ProxyNetwork, ...]) -> bool:
    """Is the direct peer one of the trusted proxies? A peer that is not an
    IP address (a unix socket, a test client's name) never is."""
    if not peer or not proxies:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # A mapped peer is its IPv4 address (#2073); tested as written too, so
        # an IPv6 range wider than the mapped one still holds it.
        addresses.append(address.ipv4_mapped)
    return any(a in network for a in addresses for network in proxies)


class Headers(Protocol):
    """A request's headers, as starlette gives them: every value of a name."""

    def getlist(self, key: str) -> list[str]: ...


def _single(headers: Headers, name: str) -> str | None:
    """The header's one value, or None when it is absent or repeated."""
    values = headers.getlist(name)
    if len(values) != 1:
        return None
    return values[0].strip()


def resolve(
    headers: Headers,
    peer: str | None,
    *,
    identity_header: str,
    groups_header: str,
    proxies: tuple[ProxyNetwork, ...],
) -> Identity:
    """The caller's identity - see the module docstring for when it is not
    ANONYMOUS. *headers* is the request's (starlette) headers."""
    if not identity_header or not peer_is_trusted(peer, proxies):
        return ANONYMOUS_IDENTITY
    user = _single(headers, identity_header)
    if user is None or not is_user(user):
        return ANONYMOUS_IDENTITY
    groups: list[str] = []
    if groups_header:
        raw = _single(headers, groups_header) or ""
        for group in raw.split(","):
            group = group.strip()
            if is_name(group) and group not in groups:
                groups.append(group)
            if len(groups) == MAX_GROUPS:
                break
    return Identity(user=user, groups=tuple(groups), authenticated=True)
