"""Save names for portal downloads.

Prefer the filename from the response ``Content-Disposition`` header. When
that name, or the listing name, has no extension, append one derived from
Smartschool's short type (``pdf``, ``docx``) or from a real MIME type.
Never append an extension that is already there.
"""

from __future__ import annotations

import mimetypes
from email.message import Message
from pathlib import Path

# Short tokens Smartschool puts in ``mimeType`` / ``mime`` on listings.
_SHORT_EXTENSIONS = {
    "pdf": ".pdf",
    "doc": ".doc",
    "docx": ".docx",
    "xls": ".xls",
    "xlsx": ".xlsx",
    "ppt": ".ppt",
    "pptx": ".pptx",
    "png": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "gif": ".gif",
    "txt": ".txt",
    "csv": ".csv",
    "zip": ".zip",
    "odt": ".odt",
    "ods": ".ods",
    "odp": ".odp",
    "rtf": ".rtf",
    "mp3": ".mp3",
    "mp4": ".mp4",
    "webp": ".webp",
}

# Real MIME types whose ``mimetypes`` guess is missing or awkward (``.jpe``).
_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        ".pptx"
    ),
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/zip": ".zip",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}


def header_value(headers: object, name: str) -> str | None:
    """Read one response header when it is a real string."""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for candidate in (name, name.lower()):
        value = getter(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def content_disposition_filename(header: str | None) -> str | None:
    """Filename from ``Content-Disposition``, including ``filename*``."""
    if not isinstance(header, str) or not header.strip():
        return None
    message = Message()
    message["Content-Disposition"] = header
    filename = message.get_filename()
    if not isinstance(filename, str) or not filename.strip():
        return None
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        return None
    return name


def extension_for_mime(mime_type: str | None) -> str:
    """Map a short Smartschool type or a real MIME type to ``.ext``."""
    if not isinstance(mime_type, str):
        return ""
    text = mime_type.split(";", 1)[0].strip().lower()
    if not text or text in {"application/octet-stream", "application/force-download"}:
        return ""
    if text in _SHORT_EXTENSIONS:
        return _SHORT_EXTENSIONS[text]
    if text in _MIME_EXTENSIONS:
        return _MIME_EXTENSIONS[text]
    if "/" not in text:
        return ""
    guessed = mimetypes.guess_extension(text, strict=False) or ""
    if guessed in {".jpe", ".jpeg"}:
        return ".jpg"
    return guessed


def download_filename(
    listing_name: str,
    mime_type: str | None,
    content_disposition: str | None = None,
) -> str:
    """Choose the on-disk name. A name that already has an extension is kept."""
    header_name = content_disposition_filename(content_disposition)
    raw = header_name or listing_name or ""
    name = Path(raw).name.strip() or "download"
    if Path(name).suffix:
        return name
    extension = extension_for_mime(mime_type)
    if not extension or name.lower().endswith(extension.lower()):
        return name
    return f"{name}{extension}"


def filename_from_response(
    listing_name: str, mime_type: str | None, response: object
) -> str:
    """Listing name, unless the download response names the file."""
    headers = getattr(response, "headers", None)
    disposition = header_value(headers, "Content-Disposition")
    content_type = header_value(headers, "Content-Type")
    mime = mime_type
    if not isinstance(mime_type, str) or not mime_type.strip():
        mime = content_type
    return download_filename(listing_name, mime, disposition)
