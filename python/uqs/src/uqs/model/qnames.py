"""Names q computes from a key, spelled once for Python (#1368).

q owns each rule (`.qetl.cfg.env_name`, `.qetl.source.credential_var`); the
prefixes they build on are generated into `uqs.generated.q_facts`, so a q
change to one fails the q-facts hook until Python follows. The `upper` that
completes each rule is held to q's own output by `test_qnames.py`.
"""

from __future__ import annotations

from uqs.generated.q_facts import CFG_ENV_PREFIX, SOURCE_CREDENTIAL_PREFIX


def credential_var(source: str) -> str:
    """The environment variable a live run reads the source's credential from -
    `.qetl.source.credential_var`."""
    return SOURCE_CREDENTIAL_PREFIX + source.upper()


def config_env_var(key: str) -> str:
    """The environment variable that outranks a worker-config key -
    `.qetl.cfg.env_name`."""
    return CFG_ENV_PREFIX + key.upper()
