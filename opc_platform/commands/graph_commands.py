"""Commands for read-only graph visualization and review feedback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from org.agent_task_graph import topological_order

from ..specs.graph_edges import parse_edges
from ..shared.ids import ensure_safe_id
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def view_graph(root: Path, opc_id: str, scenario_id: str) -> dict[str, Any]:
    safe_opc_id = ensure_safe_id(opc_id, "opc_id")
    safe_scenario_id = ensure_safe_id(scenario_id, "scenario_id")
    repo = WorkspaceRepo(root)
    scenario = repo.load_scenario(safe_opc_id, safe_scenario_id)
    nodes = list(scenario["graph"]["nodes"])
    raw_edges = scenario["graph"].get("edges") or []
    edges, _ = parse_edges(raw_edges)
    node_specs = scenario.get("node_specs") or {}
    business_labels: dict[str, str] = {}
    for n in nodes:
        spec = node_specs.get(str(n))
        if isinstance(spec, dict):
            bl = spec.get("business_label")
            if isinstance(bl, str) and bl.strip():
                business_labels[str(n)] = bl.strip()
    return {
        "opc_id": safe_opc_id,
        "scenario_id": safe_scenario_id,
        "nodes": nodes,
        "edges": edges,
        "topological_order": topological_order(nodes, edges),
        "reviews": repo.load_graph_reviews(safe_opc_id, safe_scenario_id)["reviews"],
        "business_labels": business_labels,
    }


def submit_graph_review(
    root: Path,
    opc_id: str,
    scenario_id: str,
    node: str,
    comment: str,
    review_type: str,
) -> dict[str, Any]:
    safe_opc_id = ensure_safe_id(opc_id, "opc_id")
    safe_scenario_id = ensure_safe_id(scenario_id, "scenario_id")
    review = {
        "at": utc_now_iso(),
        "node": node,
        "type": review_type,
        "comment": comment.strip(),
        "status": "submitted",
    }
    repo = WorkspaceRepo(root)
    return repo.append_graph_review(safe_opc_id, safe_scenario_id, review)

