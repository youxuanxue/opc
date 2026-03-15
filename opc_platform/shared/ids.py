"""Identifier/path safety validation helpers."""

from __future__ import annotations

import re

SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SAFE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def ensure_safe_id(value: str, field_name: str) -> str:
    """Validate external id/name used in path construction."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if ".." in text or "/" in text or "\\" in text:
        raise ValueError(f"{field_name} contains forbidden path characters")
    if not SAFE_ID_RE.match(text):
        raise ValueError(
            f"{field_name} must match pattern: {SAFE_ID_RE.pattern}"
        )
    return text


def ensure_safe_token(value: str, field_name: str) -> str:
    """Validate generic token used in path-like lookups."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if ".." in text or "/" in text or "\\" in text:
        raise ValueError(f"{field_name} contains forbidden path characters")
    if not SAFE_TOKEN_RE.match(text):
        raise ValueError(
            f"{field_name} must match pattern: {SAFE_TOKEN_RE.pattern}"
        )
    return text

