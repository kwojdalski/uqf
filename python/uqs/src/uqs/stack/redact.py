"""Masking secrets in text that is about to be printed or logged.

Every front end that echoes an error from somewhere a credential travels -
ssh output in `uqs deploy`, a driver's login error in a live source check -
masks it here first, so there is one list of what counts as a secret.

The q side masks the same names: .qetl.source.secret_keys in
src/etl/core/source_credentials.q, the list sources.csv is validated with and
the live check redacts with (#2334). python/uqs/tests/test_live_check.py
holds the two lists equal, and source_settings validates with this one.
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
    "key",
)

#: A key is one of SECRET_KEYS, alone or as the last part of a snake_case
#: name (db_password, AWS_SECRET_ACCESS_KEY), not inside a longer word; a
#: quoted key's closing quote may sit before the = or : (JSON) (#2516). A
#: value runs to the next whitespace - or, quoted, to its closing quote; or,
#: ODBC {}-quoted, to its closing } (where ; and spaces are data and }} is an
#: escaped }) - or to the line's end when it is never closed.
_SECRETISH = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9_]*_)?(?:"
    + "|".join(sorted(SECRET_KEYS, key=len, reverse=True))
    + r"))([\"']?)\s*[=:]\s*"
    r"(?:\{(?:[^}\n]|\}\})*\}?|\"(?:[^\"\\\n]|\\.)*\"?|'[^'\n]*'?|\S+)"
)


def redact(text: str, *secrets: str) -> str:
    """`text` with every secret-shaped assignment masked, and each of
    `secrets` - a credential known to the caller - wherever it appears."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return _SECRETISH.sub(lambda m: f"{m.group(1)}{m.group(2)}=<redacted>", text)
