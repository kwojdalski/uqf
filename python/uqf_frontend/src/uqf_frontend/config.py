"""Settings, read from the environment once at startup.

Credentials live here and nowhere else in the request path: the
browser never receives or sends q credentials, so they are read from the
process environment on the server and held only in this object.
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from uqf_frontend import identity, remote_helper, stack
from uqf_frontend.stack import Process
from uqs.activity_log import DEFAULT_LEVEL, LEVELS
from uqs.renamed_env import RENAMED_ENV

#: Seconds between usage-capture sweeps when nothing overrides it. Well
#: under `.usage.flushtime`, which is one day in a standard TorQ stack - see
#: capture.py on why three hours is the figure usually quoted and why it is
#: the wrong one. The interval is the whole guarantee: a capture that runs
#: less often than the flush window loses history silently, so this is sized
#: for the shorter of the two readings rather than the longer.
#:
#: Here rather than in capture.py so that config imports nothing: capture
#: imports fleet, and fleet imports config.
DEFAULT_CAPTURE_INTERVAL = 900

DEFAULT_MAX_ROWS = 10_000

#: The runtime settings renamed by #2111, each with its new name. A settings
#: file (UQF_FRONTEND_SETTINGS_FILE) that still holds one is refused, naming
#: the new key: both retention-bounded histories now say `retention_days` for
#: how long and `max_kept` for how many, and `max_rows` is the query cap only.
#: The environment variables renamed with them are in uqs.interpreter.
#: RENAMED_ENV.
RENAMED_SETTINGS_KEYS = {
    "activity_max_rows": "activity_max_kept",
    "activity_interactions_max_rows": "activity_interactions_max_kept",
    "activity_debug_max_rows": "activity_debug_max_kept",
    "config_history_keep": "config_history_max_kept",
    "config_history_days": "config_history_retention_days",
}

#: Days of activity history kept, and the most rows kept whatever their age
#: (activity.py), at each level. Both bounds apply; the tighter one wins. The
#: `actions` bound holds the server's audit too. A level that records more
#: keeps less: a day of clicking at `interactions` is thousands of rows, and
#: `debug` is for the session being troubleshot, not for the record.
DEFAULT_ACTIVITY_RETENTION_DAYS = 30
DEFAULT_ACTIVITY_MAX_KEPT = 100_000
DEFAULT_ACTIVITY_INTERACTIONS_RETENTION_DAYS = 7
DEFAULT_ACTIVITY_INTERACTIONS_MAX_KEPT = 50_000
DEFAULT_ACTIVITY_DEBUG_RETENTION_DAYS = 1
DEFAULT_ACTIVITY_DEBUG_MAX_KEPT = 10_000

#: Seconds a gateway query may take before kola gives up (UQF_FRONTEND_TIMEOUT_SECONDS).
DEFAULT_TIMEOUT = 30

#: Seconds the browser waits BEYOND the gateway timeout before it abandons a
#: request: the API's own round trip, and a lifecycle route's torq.sh. The
#: browser used to hard-code 45 s (web/src/api/), so raising
#: UQF_FRONTEND_TIMEOUT_SECONDS changed nothing it saw; now it is served as
#: `request_timeout_seconds` on /health and this is the one place the sum is
#: made. DEFAULT_TIMEOUT + this = 45 s, what the browser always used.
REQUEST_HEADROOM = 15

#: How much sooner than the browser gives up a lifecycle subprocess is killed,
#: so the operator sees the server's refusal, not an anonymous browser timeout
#: while torq.sh is still running (#1370).
LIFECYCLE_MARGIN = 5


class UqsLogin(TypedDict, total=False):
    """uqs's `user=` / `passwd=` keywords; empty, uqs's own default."""

    user: str
    passwd: str


@dataclass(frozen=True)
class Settings:
    """Where the gateway is, and the caps this layer enforces."""

    #: The gateway, from the stack's process.csv (stack.derive): never a
    #: setting of its own, so it cannot disagree with where the stack put it.
    #: With a stack root these are the facts at startup; what acts on them
    #: reads ``facts()``, which follows the stack's files (#2027).
    host: str = stack.DEFAULT_HOST
    port: int = stack.DEFAULT_BASE_PORT + stack.GATEWAY_PORT_OFFSET
    #: Empty, and it stays empty: credentials belong in the server
    #: environment and never in a default baked into the package, which
    #: test_config.py asserts. The local demo stack does want a credential
    #: (`admin:admin`, what `uqs query` uses) - that belongs in the
    #: README's run instructions, not here. A default credential in source
    #: is how a real one ends up committed next to it.
    user: str = ""
    passwd: str = ""
    #: The login /query reads tables as (#889): a NON-trusted role
    #: (`browser` in scripts/torqconfig/permissions/gateway.q), so every read
    #: is held to the gateway's query policy, as any other user's is. Empty
    #: for the same reason as `user`; on the demo stack it is browser/browser.
    data_user: str = ""
    data_passwd: str = ""
    #: Seconds. Passed to kola, which enforces it per query. Historical
    #: HDB queries are legitimately slower than current-session RDB ones.
    timeout: int = DEFAULT_TIMEOUT
    #: Hard cap on rows returned, whatever the caller asks for. A browser
    #: cannot usefully render more, and an unbounded select against an HDB
    #: is how a demo process runs out of memory.
    max_rows: int = DEFAULT_MAX_ROWS
    #: The rest of what stack.derive reads from UQF_FRONTEND_STACK_ROOT.
    #: process.csv is the declared process set for fleet health; None (no
    #: stack root) means fleet health reports itself unconfigured rather than
    #: returning an empty fleet. base_port is what its {KDBBASEPORT}
    #: placeholders resolve against. processes is the per-process query
    #: log's fan-out; empty, the view says it has nothing configured. web_dist
    #: is the built React app served under /ui/, when there is one.
    process_csv: Path | None = None
    base_port: int = stack.DEFAULT_BASE_PORT
    processes: tuple[Process, ...] = ()
    web_dist: Path | None = None
    #: Whether the /control/* routes do anything. OFF by default, and that
    #: default is the security posture rather than caution: this deployment
    #: has one shared credential and an identity CLAIMED through a header
    #: anyone can set, which is defensible while every route is a read. The
    #: moment a route can stop the fleet or rewrite process.csv, "anyone who
    #: can reach the port" is the whole access control - so turning that on
    #: is a deliberate act with a name, not a thing that happens by default.
    enable_writes: bool = False
    #: The secret every control action must carry while writes are on - see
    #: write_guard.py. Empty by default, and the server refuses to start with
    #: writes on and this empty, for the same reason `user` has no default.
    write_token: str = ""
    #: Hosts a request's Host header may name while writes are on - the
    #: DNS-rebinding check in write_guard.py. The loopback names by default:
    #: the single-host deployment this tree runs.
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    #: Where captured usage rows are written. None means capture does
    #: not run - and that is a real choice, not a safe one: `.usage.flushtime`
    #: is one day in a standard stack, so with this unset the usage view can
    #: only ever show the last day and history before that is gone. It is
    #: off by default because a capture pipeline writes files and fans out
    #: across the fleet on a timer, which a process should not start doing
    #: because someone imported it.
    capture_dir: Path | None = None
    #: Seconds between capture sweeps. Must stay well under the flush window:
    #: the interval IS the guarantee, and one longer than the window loses
    #: rows silently.
    capture_interval: int = DEFAULT_CAPTURE_INTERVAL
    #: The stack this frontend serves: where torq.sh, process.csv and the
    #: status files live, and what every derived fact above is read from.
    stack_root: Path | None = None
    #: The SQLite file activity history and the action audit are kept in
    #: (activity.py). from_env derives it when UQF_FRONTEND_ACTIVITY_DB is
    #: unset: `$TORQDATA/frontend/activity.sqlite3`, beside the workers'
    #: status files, for the stack UQF_FRONTEND_STACK_ROOT names. None - no
    #: stack root and no file - keeps history in memory, and GET /activity
    #: says it is not persistent. `uqs clean` removes $TORQDATA and this file
    #: with it; name a file elsewhere to keep history past a clean.
    activity_db: Path | None = None
    #: Retention: rows older than this many days are pruned, and only the
    #: newest `activity_max_kept` browser rows and, separately, the newest
    #: `activity_max_kept` server audit rows are kept - neither can evict the
    #: other (#2019). Refused attempts have a smaller cap of their own
    #: (activity.MAX_ATTEMPT_ROWS).
    activity_retention_days: int = DEFAULT_ACTIVITY_RETENTION_DAYS
    activity_max_kept: int = DEFAULT_ACTIVITY_MAX_KEPT
    #: The same two bounds for the `interactions` and `debug` levels (#2000).
    activity_interactions_retention_days: int = DEFAULT_ACTIVITY_INTERACTIONS_RETENTION_DAYS
    activity_interactions_max_kept: int = DEFAULT_ACTIVITY_INTERACTIONS_MAX_KEPT
    activity_debug_retention_days: int = DEFAULT_ACTIVITY_DEBUG_RETENTION_DAYS
    activity_debug_max_kept: int = DEFAULT_ACTIVITY_DEBUG_MAX_KEPT
    #: The level a browser session records at unless its operator picks
    #: another for the tab (activity_log.LEVELS). `off` stops browser history
    #: only: the server's audit of every write is kept whatever this says.
    activity_level: str = DEFAULT_LEVEL
    #: The config push history each target keeps (remote_helper's PUSH
    #: HISTORY, #2002): the newest `config_history_max_kept` pushes, none
    #: older than `config_history_retention_days` - the tighter bound wins.
    #: Applied, and its pruning audited, whenever a push opens. The same two
    #: words as the activity history's bounds (#2111): `retention_days` for
    #: how long, `max_kept` for how many - never `max_rows`, which is the
    #: per-request query cap above.
    config_history_max_kept: int = remote_helper.DEFAULT_KEEP
    config_history_retention_days: int = remote_helper.DEFAULT_KEEP_DAYS
    #: Who is calling (identity.py, #2045). The header an authenticating
    #: proxy puts the user it authenticated in - believed only from a peer in
    #: `trusted_proxies`. Empty: no header is an identity, every caller is
    #: `anonymous`, as it always was here.
    trusted_identity_header: str = ""
    #: The header the same proxy puts the user's groups in, comma-separated.
    #: Optional; believed only alongside a believed user.
    trusted_groups_header: str = ""
    #: The proxy addresses (IPs or CIDR ranges) those headers are believed
    #: from: the request's direct peer, never what X-Forwarded-For says.
    trusted_proxies: tuple[str, ...] = ()
    #: The deployment's access policy (access.py): Casbin CSV of grants and
    #: memberships. None serves the packaged default - everyone may do
    #: everything - which cannot be changed from the frontend.
    policy_file: Path | None = None
    #: Where runtime settings changed in the Settings view are kept
    #: (settings_admin.RuntimeStore), JSON, applied over the environment at
    #: startup. None: those settings can be read there but not changed.
    settings_file: Path | None = None
    #: Inspection mode for the whole frontend (inspection.py, #2032): every
    #: write route refused, whoever asks. Off by default; a target is put in
    #: inspection mode on its own in deploy_targets.toml.
    inspection_mode: bool = False
    #: The restricted q login the frontend's own gateway and fleet reads use
    #: while inspection mode is on, in place of `user` - a role granted reads
    #: only (README, "Inspection mode"). Empty keeps `user`, and
    #: /me/capabilities says so. Set without inspection mode, it is refused:
    #: it would be used by nothing, and its operator would think otherwise.
    inspection_user: str = ""
    inspection_passwd: str = ""

    def __post_init__(self) -> None:
        if self.activity_level not in LEVELS:
            raise ValueError(
                f"UQF_FRONTEND_ACTIVITY_LEVEL must be one of {', '.join(LEVELS)}, "
                f"got {self.activity_level!r}"
            )
        check_history_bound(self.config_history_max_kept, self.config_history_retention_days)
        check_identity(self)
        check_inspection(self)

    @property
    def uses_inspection_login(self) -> bool:
        """Do the frontend's own q connections use the inspection login?"""
        return self.inspection_mode and bool(self.inspection_user)

    @property
    def service_login(self) -> tuple[str, str]:
        """(user, password) the gateway and fleet connections log in as: the
        inspection login while inspection mode uses one, else the service
        login. Never logged, never served."""
        if self.uses_inspection_login:
            return self.inspection_user, self.inspection_passwd
        return self.user, self.passwd

    @property
    def uqs_login(self) -> UqsLogin:
        """The login uqs reaches a gateway as for /compare and the tunnel
        check, as keyword arguments: `service_login` - the inspection login
        while inspection mode uses one (#2524) - or none, for uqs's default.

        Not `data_user`: its `browser` role may call `.uqf.browse` and
        nothing else, and a comparison reads `meta` and rows through
        `.gw.syncexec`. What the query policy bounds for /query, the /compare
        route bounds itself: catalog tables only, `max_rows` a side."""
        user, passwd = self.service_login
        return UqsLogin(user=user, passwd=passwd) if user and passwd else UqsLogin()

    @functools.cached_property
    def proxy_networks(self) -> tuple[identity.ProxyNetwork, ...]:
        """`trusted_proxies`, parsed (and checked at construction)."""
        return identity.parse_proxies(self.trusted_proxies)

    def activity_bounds(self) -> dict[str, tuple[int, int]]:
        """Each event level's (retention days, max kept)."""
        return {
            "actions": (self.activity_retention_days, self.activity_max_kept),
            "interactions": (
                self.activity_interactions_retention_days,
                self.activity_interactions_max_kept,
            ),
            "debug": (self.activity_debug_retention_days, self.activity_debug_max_kept),
        }

    def facts(self) -> stack.StackFacts:
        """The stack facts as they are NOW (#2027): with a stack root, read
        from the stack's files (``stack.current``), which every start, stop
        and restart rewrites; without one, the fields above.

        The gateway address, the fan-out list and the base port a lifecycle
        action passes torq.sh all come from here, per request. The fields of
        the same names are only what was true when this object was built.
        """
        if self.stack_root is None:
            return stack.StackFacts(
                host=self.host,
                port=self.port,
                base_port=self.base_port,
                process_csv=self.process_csv,
                processes=self.processes,
                web_dist=self.web_dist,
                activity_db=self.activity_db,
            )
        return stack.current(self.stack_root)

    @property
    def request_timeout(self) -> int:
        """Seconds the browser should wait for any one request: the gateway
        timeout plus headroom (a timeout of 0 means kola waits forever, so
        the default stands in for it)."""
        return (self.timeout or DEFAULT_TIMEOUT) + REQUEST_HEADROOM

    @property
    def lifecycle_timeout(self) -> int:
        """Seconds a start/stop/restart may run before torq.sh is killed:
        just inside the browser's own wait."""
        return self.request_timeout - LIFECYCLE_MARGIN

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from UQF_FRONTEND_* environment variables, and the
        stack facts from the stack UQF_FRONTEND_STACK_ROOT names (stack.py).

        Fails loudly on a malformed numeric value rather than silently
        falling back to a default, matching the q side's config posture
        (refuse to start rather than start misconfigured). A variable renamed
        since (uqs.renamed_env.RENAMED_ENV) is refused too, naming its new name:
        read by nobody, it would leave the default in force without a word.
        """
        refuse_renamed_env()
        stack_root = _path_env("UQF_FRONTEND_STACK_ROOT")
        facts = stack.derive(stack_root)
        return cls(
            host=facts.host,
            port=facts.port,
            user=os.environ.get("UQF_FRONTEND_GATEWAY_USER", cls.user),
            passwd=os.environ.get("UQF_FRONTEND_GATEWAY_PASSWORD", cls.passwd),
            data_user=os.environ.get("UQF_FRONTEND_DATA_USER", cls.data_user),
            data_passwd=os.environ.get("UQF_FRONTEND_DATA_PASSWORD", cls.data_passwd),
            timeout=_int_env("UQF_FRONTEND_TIMEOUT_SECONDS", cls.timeout),
            max_rows=_int_env("UQF_FRONTEND_MAX_ROWS", cls.max_rows),
            processes=facts.processes,
            process_csv=facts.process_csv,
            web_dist=facts.web_dist,
            base_port=facts.base_port,
            enable_writes=_flag_env("UQF_FRONTEND_ENABLE_WRITES"),
            write_token=os.environ.get("UQF_FRONTEND_WRITE_TOKEN", cls.write_token),
            allowed_hosts=_hosts_env("UQF_FRONTEND_ALLOWED_HOSTS", cls.allowed_hosts),
            stack_root=stack_root,
            capture_dir=_path_env("UQF_FRONTEND_CAPTURE_DIR"),
            capture_interval=_int_env(
                "UQF_FRONTEND_CAPTURE_INTERVAL_SECONDS", cls.capture_interval
            ),
            activity_db=_path_env("UQF_FRONTEND_ACTIVITY_DB") or facts.activity_db,
            activity_retention_days=_int_env(
                "UQF_FRONTEND_ACTIVITY_RETENTION_DAYS", cls.activity_retention_days
            ),
            activity_max_kept=_int_env("UQF_FRONTEND_ACTIVITY_MAX_KEPT", cls.activity_max_kept),
            activity_interactions_retention_days=_int_env(
                "UQF_FRONTEND_ACTIVITY_INTERACTIONS_RETENTION_DAYS",
                cls.activity_interactions_retention_days,
            ),
            activity_interactions_max_kept=_int_env(
                "UQF_FRONTEND_ACTIVITY_INTERACTIONS_MAX_KEPT", cls.activity_interactions_max_kept
            ),
            activity_debug_retention_days=_int_env(
                "UQF_FRONTEND_ACTIVITY_DEBUG_RETENTION_DAYS", cls.activity_debug_retention_days
            ),
            activity_debug_max_kept=_int_env(
                "UQF_FRONTEND_ACTIVITY_DEBUG_MAX_KEPT", cls.activity_debug_max_kept
            ),
            activity_level=(
                os.environ.get("UQF_FRONTEND_ACTIVITY_LEVEL", "").strip().lower()
                or cls.activity_level
            ),
            config_history_max_kept=_history_bound_env()[0],
            config_history_retention_days=_history_bound_env()[1],
            trusted_identity_header=_header_env("UQF_FRONTEND_TRUSTED_IDENTITY_HEADER"),
            trusted_groups_header=_header_env("UQF_FRONTEND_TRUSTED_GROUPS_HEADER"),
            trusted_proxies=_list_env("UQF_FRONTEND_TRUSTED_PROXIES"),
            policy_file=_path_env("UQF_FRONTEND_POLICY_FILE"),
            settings_file=_path_env("UQF_FRONTEND_SETTINGS_FILE"),
            inspection_mode=_flag_env("UQF_FRONTEND_INSPECTION_MODE"),
            inspection_user=os.environ.get("UQF_FRONTEND_INSPECTION_USER", cls.inspection_user),
            inspection_passwd=os.environ.get(
                "UQF_FRONTEND_INSPECTION_PASSWORD", cls.inspection_passwd
            ),
        )


def check_identity(settings: Settings) -> None:
    """Refuse a trusted-identity configuration that could not mean what its
    operator thinks: a header with no proxy to believe it from (nobody would
    ever be identified), proxies with no header, a header that carries a
    credential or that every client sets, or a malformed address."""
    header = settings.trusted_identity_header
    groups = settings.trusted_groups_header
    for name, value in (
        ("UQF_FRONTEND_TRUSTED_IDENTITY_HEADER", header),
        ("UQF_FRONTEND_TRUSTED_GROUPS_HEADER", groups),
    ):
        if value != value.strip().lower() or " " in value:
            raise ValueError(f"{name} must be a lower-case header name, got {value!r}")
        if value in identity.FORBIDDEN_HEADERS:
            raise ValueError(f"{name} cannot be {value!r}: it is not an identity a proxy sets")
    if groups and not header:
        raise ValueError(
            "UQF_FRONTEND_TRUSTED_GROUPS_HEADER is set but UQF_FRONTEND_TRUSTED_IDENTITY_HEADER "
            "is not - groups are only believed alongside a user"
        )
    if groups and groups == header:
        raise ValueError("the trusted identity and groups headers must differ")
    proxies = identity.parse_proxies(settings.trusted_proxies)
    if header and not proxies:
        raise ValueError(
            "UQF_FRONTEND_TRUSTED_IDENTITY_HEADER is set but UQF_FRONTEND_TRUSTED_PROXIES is "
            "not - name the address the authenticating proxy connects from, or the header "
            "would be believed from nobody"
        )
    if proxies and not header:
        raise ValueError(
            "UQF_FRONTEND_TRUSTED_PROXIES is set but UQF_FRONTEND_TRUSTED_IDENTITY_HEADER is "
            "not - name the header the proxy puts the authenticated user in"
        )


def check_inspection(settings: Settings) -> None:
    """Refuse an inspection login that nothing would use, or a password with
    no login - either says the operator believes something that is not so."""
    if settings.inspection_passwd and not settings.inspection_user:
        raise ValueError(
            "UQF_FRONTEND_INSPECTION_PASSWORD is set but UQF_FRONTEND_INSPECTION_USER is not"
        )
    if settings.inspection_user and not settings.inspection_mode:
        raise ValueError(
            "UQF_FRONTEND_INSPECTION_USER is set but UQF_FRONTEND_INSPECTION_MODE is not - the "
            "restricted login is used only in inspection mode, so it would be used by nothing"
        )


def refuse_renamed_env(env: Mapping[str, str] | None = None) -> None:
    """Refuse a frontend variable renamed since (#1890, #2111), naming what
    replaces it. `uqs` refuses the same names before any command
    (uqs.renamed_env.RENAMED_ENV, which holds them); this is the frontend's
    own check, for a server started by uvicorn rather than by uqs."""
    source = os.environ if env is None else env
    found = [name for name in RENAMED_ENV if name.startswith("UQF_FRONTEND_") and name in source]
    if found:
        raise ValueError(
            "; ".join(f"{name} is no longer read - {RENAMED_ENV[name]}; unset it" for name in found)
        )


def check_history_bound(keep: int, days: int) -> None:
    """Refuse a push-history bound the helper would refuse."""
    if not 1 <= keep <= remote_helper.MAX_KEEP:
        raise ValueError(
            f"UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT must be 1 to {remote_helper.MAX_KEEP}, "
            f"got {keep}"
        )
    if not 1 <= days <= remote_helper.MAX_KEEP_DAYS:
        raise ValueError(
            "UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS must be 1 to "
            f"{remote_helper.MAX_KEEP_DAYS}, got {days}"
        )


def _history_bound_env() -> tuple[int, int]:
    """(pushes kept, days kept) from UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT and
    _RETENTION_DAYS alone - the API's starting point, before the Settings
    view's file."""
    refuse_renamed_env()
    keep = _int_env("UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT", remote_helper.DEFAULT_KEEP)
    days = _int_env("UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS", remote_helper.DEFAULT_KEEP_DAYS)
    check_history_bound(keep, days)
    return keep, days


def history_bound() -> tuple[int, int]:
    """(pushes kept, days kept) as the frontend enforces them: the
    environment, then what the Settings view kept in
    UQF_FRONTEND_SETTINGS_FILE over it - the same order the API applies at
    startup (settings_admin.RuntimeStore). `uqs target config restore` reads
    this, so a restore prunes to the retention the Settings view shows, not
    to the environment's defaults (#2072)."""
    keep, days = _history_bound_env()
    kept = runtime_file_values(_path_env("UQF_FRONTEND_SETTINGS_FILE"))
    keep = kept.get("config_history_max_kept", keep)
    days = kept.get("config_history_retention_days", days)
    check_history_bound(keep, days)
    return keep, days


def runtime_file_values(path: Path | None) -> dict[str, Any]:
    """The runtime settings the Settings view kept in *path*
    (UQF_FRONTEND_SETTINGS_FILE), checked by their model; empty when there
    is no file. A file that does not parse is an error, never skipped - and
    so is one written before a key was renamed (RENAMED_SETTINGS_KEYS): its
    value would otherwise be refused as an unknown key, without a word on
    what the key is called now."""
    if path is None or not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if isinstance(raw, dict):
        old = [key for key in RENAMED_SETTINGS_KEYS if key in raw]
        if old:
            raise ValueError(
                f"UQF_FRONTEND_SETTINGS_FILE {path} was written before #2111: "
                + "; ".join(f"`{key}` is now `{RENAMED_SETTINGS_KEYS[key]}`" for key in old)
                + " - rename the key in the file"
            )
    # Here, not at the top: models imports access, which imports casbin -
    # more than a CLI reading two numbers should load before it needs to.
    from uqf_frontend.models.settings import RuntimeSettingsBody

    try:
        body = RuntimeSettingsBody.model_validate_json(text)
    except ValueError as exc:
        raise ValueError(
            f"UQF_FRONTEND_SETTINGS_FILE {path} is not valid runtime settings: "
            f"{type(exc).__name__} - fix or remove it"
        ) from None
    return body.model_dump()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _flag_env(name: str) -> bool:
    """A boolean environment variable, strictly.

    Only "1", "true", "yes" and "on" enable it, case-insensitively. An
    unrecognised value is an ERROR rather than a silent false: `ENABLE=fasle`
    typed at 2am must not read as "writes are off, all is well" - it must
    stop the server with the value quoted back.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return False
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean (true/false/1/0/yes/no/on/off), got {raw!r}")


def _hosts_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated host list, lower-cased; unset or empty keeps the default."""
    raw = os.environ.get(name, "")
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts or default


def _header_env(name: str) -> str:
    """A header name, lower-cased as HTTP compares them; unset is empty."""
    return os.environ.get(name, "").strip().lower()


def _list_env(name: str) -> tuple[str, ...]:
    """A comma-separated list; unset or empty is empty."""
    return tuple(v.strip() for v in os.environ.get(name, "").split(",") if v.strip())


def _path_env(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None
