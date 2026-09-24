"""Backend-for-frontend over the uqf TorQ gateway.

A thin server-side API layer in front of the gateway, reusing the kola IPC pattern already proven in
this repo, with validated and genuinely parameterised query construction.

The design property worth knowing: client input never reaches q as text. The
q programs in :mod:`uqf_frontend.queries` are constants written in this
package; a caller's table, columns and operator are checked against
:mod:`uqf_frontend.catalog` and then sent as IPC arguments, and a caller's
values are sent as typed IPC arguments only.
"""

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings

__all__ = ["Settings", "create_app"]
