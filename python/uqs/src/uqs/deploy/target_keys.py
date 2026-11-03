"""How deploy_targets.toml and the command line spell make_config's options.

A target's file and push's flags share one name per setting; where that name
is not make_config's own field name, this is the one map between them (#1888),
split out of deploy/config.py so the field-to-key mapping has a home of its
own.
"""

from __future__ import annotations

#: make_config's options whose key in deploy_targets.toml - and so whose flag
#: - is not the option's own name (#1888): the file spells each as push's
#: option, `ssh` for `--ssh` (not `host`, which is a q host everywhere else,
#: #1768), `q_cmd` for `--q-cmd` (#1857), `torq_component` for
#: `--torq-component` (#1855). A file holding the field's own name is refused,
#: naming the key (deploy/targets.py).
FILE_KEYS = {
    "host": "ssh",
    "qcmd": "q_cmd",
    "qhome": "q_home",
    **{
        f"{c}_runtime{sha}": f"{c}_component{sha}"
        for c in ("q", "torq", "python")
        for sha in ("", "_sha256")
    },
}
#: The other way: a key in the file -> make_config's option.
FIELD_OF_KEY = {key: field for field, key in FILE_KEYS.items()}


def key(option: str) -> str:
    """make_config's `option` as deploy_targets.toml spells it: `ssh` for `host`."""
    return FILE_KEYS.get(option, option)


def flag(option: str) -> str:
    """How make_config's `option` is given on the command line: `--ssh` for
    `host`, `--torq-home` for `torq_home`."""
    return "--" + key(option).replace("_", "-")
