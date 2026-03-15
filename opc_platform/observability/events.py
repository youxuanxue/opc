"""Runtime event helpers."""

from __future__ import annotations

from typing import Any

from ..shared.workspace import utc_now_iso


def node_event(node: str, status: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"at": utc_now_iso(), "node": node, "status": status}
    payload.update(extra)
    return payload

