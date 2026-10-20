"""Class average and median from an evaluation detail payload.

The library types ``details.centralTendencies`` as ``list[str]``. Live
payloads may instead be objects with a ``graphic``, or the numbers may sit
on another field (``classAverage``, ``gemiddelde``, ``mediaan``, …).
Schools and teachers can omit the numbers entirely; then both stay null.

``detail_diagnostic`` reports keys and score-like values only. It does not
copy names, cookies, or other free text.
"""

from __future__ import annotations

import re
from typing import Any

_AVERAGE_LABELS = {
    "average",
    "avg",
    "mean",
    "gemiddelde",
    "classaverage",
    "class_average",
    "class-average",
}
_MEDIAN_LABELS = {
    "median",
    "mediaan",
    "classmedian",
    "class_median",
    "class-median",
}
_ALT_FIELDS = (
    ("classAverage", "average"),
    ("class_average", "average"),
    ("average", "average"),
    ("mean", "average"),
    ("gemiddelde", "average"),
    ("classMedian", "median"),
    ("class_median", "median"),
    ("median", "median"),
    ("mediaan", "median"),
)
_SCORE_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?(?:/[0-9]+(?:[.,][0-9]+)?)?%?$")
_STAT_KEY_RE = re.compile(r"(?i)average|median|mediaan|gemiddelde|mean|tendenc")


def _scalar(value: object) -> str | int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _score_preview(value: object) -> dict[str, Any]:
    """A number or a short score string. Other text is only marked present."""
    scalar = _scalar(value)
    if isinstance(scalar, (int, float)):
        return {"number": scalar}
    if isinstance(scalar, str) and _SCORE_RE.fullmatch(scalar.replace(" ", "")):
        return {"text": scalar}
    if scalar is not None:
        return {"present": True}
    return {}


def _as_score(item: object) -> dict[str, Any] | None:
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {"description": text, "value": None}
    if isinstance(item, bool) or item is None:
        return None
    if isinstance(item, (int, float)):
        return {"description": str(item), "value": item}

    graphic: object
    if isinstance(item, dict):
        graphic = item.get("graphic")
        if not isinstance(graphic, dict):
            description = _scalar(item.get("description"))
            value = _scalar(item.get("value"))
            if description is None and value is None:
                return None
            return {
                "description": description if isinstance(description, str) else "N/A",
                "value": value,
            }
        description = _scalar(graphic.get("description"))
        value = _scalar(graphic.get("value"))
    else:
        graphic = getattr(item, "graphic", None)
        if isinstance(graphic, (str, int, float, bool, list)) or graphic is None:
            return None
        description = _scalar(getattr(graphic, "description", None))
        value = _scalar(getattr(graphic, "value", None))
    if description is None and value is None:
        return None
    return {
        "description": description if isinstance(description, str) else "N/A",
        "value": value,
    }


def _label(item: object) -> str:
    source: object = item
    keys: tuple[str, ...]
    if isinstance(item, dict):
        keys = ("type", "name", "label", "kind", "tendency", "id")
    else:
        keys = ("type", "name", "label", "kind")
    for key in keys:
        if isinstance(source, dict):
            value = source.get(key)
        else:
            value = getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace(" ", "")
    return ""


def _pair(items: list[object]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    average: dict[str, Any] | None = None
    median: dict[str, Any] | None = None
    unnamed: list[dict[str, Any]] = []
    for item in items:
        score = _as_score(item)
        if score is None:
            continue
        label = _label(item)
        if label in _AVERAGE_LABELS and average is None:
            average = score
        elif label in _MEDIAN_LABELS and median is None:
            median = score
        else:
            unnamed.append(score)
    if average is None and unnamed:
        average = unnamed.pop(0)
    if median is None and unnamed:
        median = unnamed.pop(0)
    return average, median


def _fill_alts(
    detail: object,
    average: dict[str, Any] | None,
    median: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(detail, dict):
        return average, median
    for key, side in _ALT_FIELDS:
        if key not in detail:
            continue
        score = _as_score(detail.get(key))
        if score is None:
            continue
        if side == "average" and average is None:
            average = score
        elif side == "median" and median is None:
            median = score
    return average, median


def _tendency_list(detail: object) -> list[object]:
    if isinstance(detail, dict):
        for key in ("centralTendencies", "central_tendencies"):
            value = detail.get(key)
            if isinstance(value, list):
                return value
        return []
    value = getattr(detail, "central_tendencies", None)
    if isinstance(value, list):
        return value
    return []


def statistics_from_detail(
    detail: object,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Average and median from a details object or the ``details`` dict."""
    average, median = _pair(_tendency_list(detail))
    return _fill_alts(detail, average, median)


def statistics_from_payload(
    payload: object,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Average and median from a full evaluation JSON object."""
    detail: object = payload
    if isinstance(payload, dict) and isinstance(payload.get("details"), dict):
        detail = payload["details"]
    average, median = statistics_from_detail(detail)
    if isinstance(payload, dict) and detail is not payload:
        average, median = _fill_alts(payload, average, median)
    return average, median


def _item_summary(item: object) -> dict[str, Any]:
    if isinstance(item, str):
        summary: dict[str, Any] = {"kind": "string"}
        preview = _score_preview(item)
        if preview:
            summary["score"] = preview
        return summary
    if isinstance(item, dict):
        summary = {
            "kind": "object",
            "keys": [key for key in item if isinstance(key, str)],
        }
        graphic = item.get("graphic")
        if isinstance(graphic, dict):
            summary["graphic_keys"] = [key for key in graphic if isinstance(key, str)]
            description = _score_preview(graphic.get("description"))
            value = _score_preview(graphic.get("value"))
            if description:
                summary["description"] = description
            if value:
                summary["value"] = value
        else:
            description = _score_preview(item.get("description"))
            value = _score_preview(item.get("value"))
            if description:
                summary["description"] = description
            if value:
                summary["value"] = value
        return summary
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return {"kind": "number", "score": _score_preview(item)}
    return {"kind": "other"}


def detail_diagnostic(payload: object) -> dict[str, Any]:
    """Secret-free view of one evaluation payload.

    Key names, and score-like numbers only. No names and no cookies.
    """
    if not isinstance(payload, dict):
        return {"present": False}
    details = payload.get("details")
    detail = details if isinstance(details, dict) else payload
    tendencies = _tendency_list(detail)
    other: list[dict[str, Any]] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            if not isinstance(key, str) or key in {
                "centralTendencies",
                "central_tendencies",
            }:
                continue
            if _STAT_KEY_RE.search(key) is None:
                continue
            entry: dict[str, Any] = {
                "key": key,
                "kind": type(value).__name__,
            }
            if isinstance(value, dict):
                entry["keys"] = [name for name in value if isinstance(name, str)]
            preview = _score_preview(value)
            if not preview and isinstance(value, dict):
                graphic = value.get("graphic")
                if isinstance(graphic, dict):
                    preview = _score_preview(
                        graphic.get("description", graphic.get("value"))
                    )
                    entry["graphic_keys"] = [
                        name for name in graphic if isinstance(name, str)
                    ]
            if preview:
                entry["score"] = preview
            other.append(entry)
    average, median = statistics_from_payload(payload)
    return {
        "present": True,
        "keys": [key for key in payload if isinstance(key, str)],
        "details_keys": (
            [key for key in detail if isinstance(key, str)]
            if isinstance(detail, dict)
            else []
        ),
        "central_tendencies": {
            "length": len(tendencies),
            "items": [_item_summary(item) for item in tendencies[:4]],
        },
        "other_fields": other,
        "numbers_found": average is not None or median is not None,
    }
