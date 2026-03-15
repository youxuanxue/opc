"""Commands for decision center operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shared.io import read_json
from ..shared.ids import ensure_safe_token
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def list_decisions(root: Path, opc_id: str | None = None) -> list[dict[str, Any]]:
    repo = WorkspaceRepo(root)
    items = repo.list_decisions()
    if opc_id:
        return [x for x in items if x.get("opc_id") == opc_id]
    return items


def approve_decision(root: Path, ticket_id: str, option: str) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    safe_ticket_id = ensure_safe_token(ticket_id, "ticket_id")
    path = repo.decision_path(safe_ticket_id)
    if not path.exists():
        raise ValueError(f"decision ticket not found: {ticket_id}")
    selected = read_json(path)
    if not selected:
        raise ValueError(f"decision ticket not found: {ticket_id}")
    selected["status"] = "approved"
    selected["approved_option"] = option
    selected["approved_at"] = utc_now_iso()
    repo.save_decision(safe_ticket_id, selected)
    return selected

