"""Diagnostic record helpers."""

from __future__ import annotations

from typing import Any

from ..shared.workspace import utc_now_iso


def build_diagnostic(
    *,
    node: str,
    day: int | None,
    reason_code: str,
    human_message: str,
    fix_hint: str | None = None,
    sample: str = "",
) -> dict[str, Any]:
    return {
        "at": utc_now_iso(),
        "node": node,
        "day": day,
        "reason_code": reason_code,
        "human_message": human_message,
        "fix_hint": fix_hint or "",
        "sample": (sample or "")[:240],
    }


def append_diagnostic(diags: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    diags.append(payload)

