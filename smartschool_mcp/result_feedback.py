"""Let result feedback carry attachment objects.

``smartschool`` (0.13.0 and master) types ``FeedbackFull.attachments`` as
``list[str]``. De Pass sends ``[{"id": "<uuid>", "wopiUrl": null}, ...]``,
and one such feedback fails validation of the whole ``Results`` page, so
``get_results`` returned nothing. This process-wide patch turns each object
into its ``id`` and keeps plain strings; items without an id are dropped.

Remove this module once the library accepts the objects itself.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from pydantic import BeforeValidator
from pydantic.dataclasses import rebuild_dataclass
from smartschool._objects import FeedbackFull, ProjectGoal, Result, ResultDetails

_relaxed = False


def attachment_ids(value: object) -> object:
    """``[{"id": ...}, "id", ...]`` -> ``["id", ...]``; other input unchanged."""
    if not isinstance(value, list):
        return value
    ids: list[str] = []
    for item in value:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            ident = item.get("id")
            if ident is not None and str(ident):
                ids.append(str(ident))
    return ids


_LenientAttachments = Annotated[list[str], BeforeValidator(attachment_ids)]


def relax_feedback_attachments() -> None:
    """Rebuild the result models so attachment objects validate."""
    global _relaxed
    if _relaxed:
        return
    FeedbackFull.__annotations__["attachments"] = _LenientAttachments
    FeedbackFull.__dataclass_fields__["attachments"].type = _LenientAttachments
    rebuild_dataclass(cast(Any, FeedbackFull), force=True)
    # Rebuild every model that embeds FeedbackFull, innermost first.
    for cls in (ProjectGoal, ResultDetails, Result):
        rebuild_dataclass(cast(Any, cls), force=True)
    _relaxed = True
