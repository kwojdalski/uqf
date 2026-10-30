"""The tickerplant connection every external connector shares (#1786).

Each connector outside the plant - kafka, crypto, crypto-fills, a sidecar
bundle's, one `uqs job new --kind external` scaffolds - opens its own kdb+ IPC
handle to stp1 and calls `.u.upd`. Where that handle goes and what it presents
are the same for all of them, so they are written once, here, and every
connector and the scaffold template import them:

  TP_HOST            the host stp1 is reached on
  tickerplant_port   stp1's port, from its process.csv row
  TP_CREDENTIAL      the `--tp-credential` option, declared once and listed
                     in every connector's options
  tp_credential      the credential a start presents, resolved

What stays each connector's own is everything else: Kafka's brokers, topics
and consumer group, cryptorust's venues, Databento's dataset and key.

## Precedence

A setting a FeedOption gives an `env` resolves in one order
(`feeds.FeedOption.value`), and the credential follows it:

  1. given explicitly: the option to `uqs connector start` (`--tp-credential`),
     or the argument a Python caller passes - a deployment's own credential;
  2. the environment: `$UQF_TP_CREDENTIAL`, which a deployed server can export
     from its shared/config/secrets.env (deploy/config.SECRETS), sourced by
     deploy.env for every process uqs starts;
  3. the declared default - for the credential, the DEMO default below.

## The demo credential

`feed:pass` is a sample login: the starter pack's
appconfig/passwords/accesslist.txt accepts it, which is the only reason it
works against an unchanged stack. It is NOT read from that file, or from
passwords/feed.txt beside it - nothing here opens a vendored password file,
because a sample is not a deployment's credential. It is written once, as
DEMO_TP_CREDENTIAL, and a start that falls back to it says so in its log.

A credential is a secret wherever it appears: the option is `secret`, so a
dry run prints `***`, and every refusal here names where the value came from,
never the value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from uqs.external.feeds import FeedOption
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack.procs import get_process_config

log = get_logger()

#: Where stp1 is reached: every connector runs on the stack's own host.
TP_HOST = "localhost"

#: The environment variable holding the credential (rule 2 above).
TP_CREDENTIAL_ENV = "UQF_TP_CREDENTIAL"

#: The DEMO credential (rule 3): the starter pack's sample `feed` login, which
#: its sample accesslist.txt accepts. Written here, never read from that file.
DEMO_TP_CREDENTIAL = "feed:pass"


def _check_credential(value: str, origin: str) -> None:
    """Refuse a credential that is not `user:password`, naming where it came
    from and never what it was."""
    user, colon, _password = value.partition(":")
    if not colon or not user.strip():
        raise UqsError(
            f"the tickerplant credential from {origin} is not user:password "
            "(its value is not shown) - pass --tp-credential USER:PASSWORD or set "
            f"${TP_CREDENTIAL_ENV}"
        )


#: `--tp-credential`: one option, every connector lists it in its options=.
TP_CREDENTIAL = FeedOption(
    "tp_credential",
    "user:password stp1 accepts; default $" + TP_CREDENTIAL_ENV + ", else the starter "
    "pack's DEMO login",
    default=DEMO_TP_CREDENTIAL,
    secret=True,
    env=TP_CREDENTIAL_ENV,
    validate=_check_credential,
)


def tp_credential(given: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """The credential a connector presents to stp1: `given`, else
    $UQF_TP_CREDENTIAL, else the demo default - checked, and logged (by its
    origin, never its value) when it is the demo default."""
    environ = os.environ if environ is None else environ
    origin = TP_CREDENTIAL.origin(given, environ)
    value = TP_CREDENTIAL.value(given, environ)
    _check_credential(value, origin)
    if value == DEMO_TP_CREDENTIAL:
        log.info(
            "connecting to stp1 with the DEMO credential (the starter pack's sample "
            f"`feed` login) - pass --tp-credential or set ${TP_CREDENTIAL_ENV} to use "
            "a deployment's own"
        )
    return value


def tickerplant_port(paths: UqsPaths, base_port: int | None = None) -> int:
    """stp1's port from its process.csv row, or a refusal naming the row."""
    port = get_process_config(paths, "stp1", base_port=base_port).get("port", "")
    if not str(port).strip().isdigit():
        raise UqsError(
            f"stp1's process.csv row gives no port ({port!r}) - every external "
            "connector publishes to stp1; check `uqs config show stp1 port`"
        )
    return int(port)
