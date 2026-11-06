"""What every area's models share: the strict request config, the confirmation rule (#2244)
and the field descriptions more than one area uses.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Every REQUEST model's config (#1361). Pydantic's default ignores a key it
#: does not know and fills a default for one it does not get, so a field
#: renamed on the caller's side returned 200 with the field silently gone -
#: an unfiltered query, a dropped coverage check. A stale key is a 422 instead.
#: test_api_types.py holds every request model to it.
STRICT_REQUEST = ConfigDict(extra="forbid")

#: THE CONFIRMATION RULE (#2244). Every request the UI puts behind a
#: confirmation dialog - a lifecycle stop or restart, opening a config push,
#: a restore, a delete - carries `confirmed`, strictly a boolean, and is
#: refused unless it is `true`. A script or a second client gets the same
#: guard the browser's dialog stands for, and a body that lost the field (the
#: #1361 case) is refused rather than read as consent. The check is
#: `refuse_unconfirmed`, the one place it is made.
CONFIRMATION_REQUIRED = "explicit confirmation is required - send confirmed: true"

#: ONE NAME FOR THE COVERAGE SLICE (#2261). The slice of a dataset a coverage
#: claim is recorded under - a symbol, say - is `slice` on every route, as it
#: is `--slice` on `uqs data diff` and `uqs job new` (#2239). "Partition" in
#: this API means only a date partition of the HDB. The ledger's q column is
#: still `partition`: it is mapped at the q boundary (app._coverage and
#: compare.run), not migrated on disk. A request still sending `partition` is
#: refused with this text rather than read as a missing slice.
PARTITION_IS_SLICE = (
    '`partition` is now `slice`: the coverage slice, "" for a dataset with no slice '
    "dimension - a partition in this API is only a date partition (#2261)"
)


def refuse_partition(data: Any) -> Any:
    """A request body naming the coverage slice by its old name, refused."""
    if isinstance(data, dict) and "partition" in data:
        raise ValueError(PARTITION_IS_SLICE)
    return data


def refuse_unconfirmed(data: Any, *, required: bool = True) -> Any:
    """The confirmation rule, on a request body before it is parsed: refused
    unless `confirmed` is the JSON `true` - missing (when `required`), false,
    1 and "true" alike, with the one text."""
    if isinstance(data, dict) and (required or "confirmed" in data):
        if data.get("confirmed") is not True:
            raise ValueError(CONFIRMATION_REQUIRED)
    return data


class ConfirmedRequest(BaseModel):
    """A request for something the operator must confirm (the rule above)."""

    model_config = STRICT_REQUEST

    confirmed: bool = Field(strict=True, description="must be true: the operator confirmed it")

    @model_validator(mode="before")
    @classmethod
    def _confirmed(cls, data: Any) -> Any:
        return refuse_unconfirmed(data)


#: A target's deployment root, named as uqs names it (#2107): `Config.dest`,
#: which uqs normalises as this is, without a trailing `/`.
DEST = "the target's deployment root: its registry `dest`, without a trailing /"
