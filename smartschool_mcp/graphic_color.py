"""Let result graphics accept colors the pinned library enum rejects.

``markminnoye/smartschool@517de70`` types ``PercentageGraphic.color`` and
``TextGraphic.color`` as ``GraphicColor`` (green, red, olive, yellow, steel,
grass). De Pass sends ``blue``, and one invalid row fails the whole
``Results`` page. This process-wide patch keeps known colors as that enum
and leaves any other color as a plain string.

Pushing the same change to the fork is not possible from this environment
(the GitHub token has no write access to ``markminnoye/smartschool``), so
the MCP pin stays at ``517de70``.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from pydantic import BeforeValidator
from pydantic.dataclasses import rebuild_dataclass
from smartschool._objects import GraphicColor, PercentageGraphic, Result, TextGraphic

_relaxed = False


def _coerce_graphic_color(value: object) -> object:
    if isinstance(value, GraphicColor):
        return value
    if isinstance(value, str):
        try:
            return GraphicColor(value)
        except ValueError:
            return value
    return value


_LenientColor = Annotated[
    GraphicColor | str,
    BeforeValidator(_coerce_graphic_color),
]


def relax_graphic_colors() -> None:
    """Rebuild result graphic models so unknown colors do not fail validation."""
    global _relaxed
    if _relaxed:
        return

    for cls in (PercentageGraphic, TextGraphic):
        cls.__annotations__["color"] = _LenientColor
        cls.__dataclass_fields__["color"].type = _LenientColor
        rebuild_dataclass(cast(Any, cls), force=True)
    rebuild_dataclass(cast(Any, Result), force=True)
    _relaxed = True
