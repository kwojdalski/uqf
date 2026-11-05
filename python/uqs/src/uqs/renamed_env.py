"""The environment variables renamed rather than retired, each with what
replaces it (#1890, #2111).

A leaf, like `activity_log`: it imports nothing from `uqs` and nothing at
all, because the frontend reads it on its startup path
(uqf_frontend.config.refuse_renamed_env), where loading uqs.paths is not
wanted. `uqs` refuses every name here before any command, with the retired
ones (uqs.interpreter.removed_variables_problem); the frontend refuses its
own `UQF_FRONTEND_*` ones when it starts. Either way a renamed variable is
refused by name, never ignored: ignored, the default it was meant to replace
stays in force without a word.
"""

from __future__ import annotations

#: Old name -> what to set instead. #1890: one prefix for what q reads, UQF_,
#: and one spelling, PASSWORD.
RENAMED_ENV = {
    "UQS_REQUIRE_LIVE_SOURCES": "set UQF_LIVE_SOURCES_REQUIRED=1 (#1890)",
    "UQF_FRONTEND_GATEWAY_PASSWD": "set UQF_FRONTEND_GATEWAY_PASSWORD (#1890)",
    "UQF_FRONTEND_DATA_PASSWD": "set UQF_FRONTEND_DATA_PASSWORD (#1890)",
    # A duration names its unit, and the frontend's two retention-bounded
    # histories share one vocabulary: RETENTION_DAYS for how long, MAX_KEPT
    # for how many - MAX_ROWS is the per-request query cap alone (#2111).
    "UQF_FRONTEND_TIMEOUT": "set UQF_FRONTEND_TIMEOUT_SECONDS (#2111)",
    "UQF_FRONTEND_CAPTURE_INTERVAL": "set UQF_FRONTEND_CAPTURE_INTERVAL_SECONDS (#2111)",
    "UQF_FRONTEND_ACTIVITY_MAX_ROWS": "set UQF_FRONTEND_ACTIVITY_MAX_KEPT (#2111)",
    "UQF_FRONTEND_ACTIVITY_INTERACTIONS_MAX_ROWS": (
        "set UQF_FRONTEND_ACTIVITY_INTERACTIONS_MAX_KEPT (#2111)"
    ),
    "UQF_FRONTEND_ACTIVITY_DEBUG_MAX_ROWS": "set UQF_FRONTEND_ACTIVITY_DEBUG_MAX_KEPT (#2111)",
    "UQF_FRONTEND_CONFIG_HISTORY_KEEP": "set UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT (#2111)",
    "UQF_FRONTEND_CONFIG_HISTORY_DAYS": "set UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS (#2111)",
}
