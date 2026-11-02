"""What starting an attachment's publisher prepares, and how it authenticates (#1847).

An attached publisher joins a TorQ stack uqf does not manage. Its start used
to run the whole-stack bootstrap, which writes gateway1's and sctp1's access
lists from `$TORQAPPHOME/appconfig/passwords/accesslist.txt`, the schema
stp1 loads, the DQE and reporter configuration, and fills the HDB - none of it
for the one process being started, and the access lists fail outright on a
site whose TorQ authenticates its own way: no starter-pack access list, so
FileNotFoundError before the publisher launched.

So a torq.sh command that names attachments' publishers and nothing else is
prepared by `runtime.bootstrap_publisher`: the data directory's logs, the
process.csv and setenv.sh torq.sh reads, and nothing else. Every refusal of
a start still applies first, unchanged (start_policy.refuse_start: the name,
the q version, the ports another stack holds, the attachment's verification
and its live table check). A start of anything else, `all` included, is the
whole-stack bootstrap as before.

AUTHENTICATION. Two integrations are supported, and anything else is refused
by name rather than guessed:

  starter-pack  `$TORQAPPHOME/appconfig/passwords/accesslist.txt` exists and
                KDBSECURE is not set: the publisher runs as it always has -
                its row's `-U` names that list, and uqf's credential loader
                (scripts/torqcode/handlers/loadpassword.q) reads TorQ's
                passwords/ files.
  site          KDBSECURE is set, to a directory: the site keeps its
                credentials there and its own TorQ code authenticates, so the
                publisher's row names no `-U` (who may connect is the site's
                .pm, loaded from its own KDBAPPCONFIG, as for its other
                processes) and SITE_AUTH_ENV tells uqf's handlers to leave the
                site's loader and .pm users alone. KDBSECURE is passed on as
                it is.

Neither is refused (UNSUPPORTED): falling back to TorQ's demo credentials
would start a publisher that either cannot log in or logs in as a demo user,
and say nothing.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import UqsError, UqsPaths
from uqs.stack import attach_state
from uqs.stack.start_policy import started_procs

#: Where a site keeps its encrypted credentials, for its own loader to read.
KDBSECURE_ENV = "KDBSECURE"
#: Set to "1" for a publisher whose site authenticates its own way: uqf's
#: handlers then leave the site's credential loader and .pm users as they are.
SITE_AUTH_ENV = "UQF_SITE_AUTH"
#: The starter pack's access list, under TORQAPPHOME.
STARTER_ACCESS_LIST = Path("appconfig") / "passwords" / "accesslist.txt"

STARTER_PACK, SITE = "starter-pack", "site"

UNSUPPORTED = (
    "{procs} cannot be started: the stack's TorQ application ({app}) has no "
    "{access} and KDBSECURE is not set, so how its processes authenticate is not "
    "known, and uqf will not fall back to TorQ's demo credentials. If the site keeps "
    "its credentials under KDBSECURE and authenticates with its own .pm code, export "
    "KDBSECURE as the site's processes see it and start the publisher again; the "
    "site's loader and .pm then stand. Otherwise point TORQAPPHOME at the stack's "
    "TorQ application, which has that access list. Nothing was started."
)


@dataclass(frozen=True)
class Auth:
    """How the publisher authenticates: STARTER_PACK or SITE."""

    kind: str
    #: the KDBSECURE directory, for SITE
    secure: str = ""

    def env(self) -> dict[str, str]:
        """What setenv.sh adds for the publisher: nothing for the starter pack."""
        if self.kind != SITE:
            return {}
        return {SITE_AUTH_ENV: "1", KDBSECURE_ENV: self.secure}


def publishers(args: Sequence[str]) -> tuple[str, ...]:
    """The attachments' publishers torq.sh `args` names, when it names nothing
    else; () for any other command - `all` and a bare verb included, which are
    the whole stack's."""
    named = set(started_procs(args).split())
    if not named or "all" in named:
        return ()
    attached = {pub.procname for pub in attach_state.publishers()}
    return tuple(sorted(named)) if named <= attached else ()


def auth(paths: UqsPaths, procs: Sequence[str]) -> Auth:
    """The integration the publishers `procs` start under, or UNSUPPORTED."""
    who = ", ".join(procs)
    how = auth_for(paths.torqapphome, f"{who} cannot be started", "Nothing was started.")
    if how is None:
        raise UqsError(
            UNSUPPORTED.format(procs=who, app=paths.torqapphome, access=STARTER_ACCESS_LIST)
        )
    return how


def auth_for(app: Path | None, refused: str, nothing: str) -> Auth | None:
    """SITE when KDBSECURE names a directory, STARTER_PACK when the TorQ
    application `app` has the starter pack's access list, None when neither -
    the caller says why, for what it was doing. `refused` and `nothing` word
    the refusal of a KDBSECURE that names no directory."""
    secure = os.environ.get(KDBSECURE_ENV, "").strip()
    if secure:
        if not Path(secure).is_dir():
            raise UqsError(
                f"{refused}: {KDBSECURE_ENV}={secure!r} is not a directory - it should be where "
                f"the site keeps the credentials its own TorQ code reads. {nothing}"
            )
        return Auth(SITE, secure)
    if app is not None and (app / STARTER_ACCESS_LIST).is_file():
        return Auth(STARTER_PACK)
    return None


def with_auth(rows: list[dict[str, str]], procs: Sequence[str], how: Auth) -> list[dict[str, str]]:
    """`rows`, with each of `procs` carrying no `-U` under the site's own
    authentication; unchanged for the starter pack."""
    if how.kind != SITE:
        return rows
    return [{**row, "U": ""} if row["procname"] in procs else row for row in rows]
