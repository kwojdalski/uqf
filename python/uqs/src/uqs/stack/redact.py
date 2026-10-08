"""Masking secrets in text that is about to be printed or logged.

Every front end that echoes an error from somewhere a credential travels -
ssh output in `uqs deploy`, a driver's login error in a live source check -
masks it here first, so there is one list of what counts as a secret.

The q side masks the same names in src/etl/core/live_check.q
(.qetl.livecheck.secret_keys); python/uqs/tests/test_live_check.py holds the
two lists equal.
"""

from __future__ import annotations

import re

#: Setting names whose values are secrets, lower case.
SECRET_KEYS = (
    "pwd",
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "accesskey",
    "access_key",
)

_SECRETISH = re.compile(
    r"(?i)\b(" + "|".join(sorted(SECRET_KEYS, key=len, reverse=True)) + r")\s*[=:]\s*\S+"
)


def redact(text: str, *secrets: str) -> str:
    """`text` with every secret-shaped assignment masked, and each of
    `secrets` - a credential known to the caller - wherever it appears."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return _SECRETISH.sub(lambda m: f"{m.group(1)}=<redacted>", text)
