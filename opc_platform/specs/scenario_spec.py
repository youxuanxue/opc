"""ScenarioSpec model validation (lightweight, stdlib-only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .compiler import derive_placeholders_from_input_contract

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _validate_node_placeholders(node: str, node_spec: dict[str, Any]) -> None:
    """Compile-time placeholder validation: required must appear in prompt; all used must be allowed."""
    prompt = node_spec.get("prompt") if isinstance(node_spec.get("prompt"), dict) else {}
    context_block = str(prompt.get("context_block") or "")
    task_block = str(prompt.get("task_block") or "")
    used_in_prompt = set(_PLACEHOLDER_RE.findall(context_block + "\n" + task_block))
    if not used_in_prompt:
        return

    allowed = node_spec.get("allowed_placeholders") if isinstance(node_spec.get("allowed_placeholders"), list) else None
    required = node_spec.get("required_placeholders") if isinstance(node_spec.get("required_placeholders"), list) else None
    if allowed is None or required is None:
        derived_a, derived_r = derive_placeholders_from_input_contract(
            node_spec.get("input_contract") if isinstance(node_spec.get("input_contract"), dict) else {}
        )
        if allowed is None:
            allowed = derived_a
        if required is None:
            required = derived_r
    allowed_set = set(str(x) for x in allowed)

    unknown = [x for x in used_in_prompt if x not in allowed_set]
    if unknown:
        raise ValueError(f"{node}: unknown placeholders (not in allowed_placeholders): {unknown}")

    missing_in_prompt = [x for x in required if x not in used_in_prompt]
    if missing_in_prompt:
        raise ValueError(f"{node}: required_placeholders must appear in context_block/task_block: {missing_in_prompt}")


@dataclass
class ScenarioSpec:
    scenario_id: str
    graph: dict[str, Any]
    node_specs: dict[str, Any]
    defaults: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScenarioSpec":
        scenario_id = str(payload.get("scenario_id") or "").strip()
        graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
        node_specs = payload.get("node_specs") if isinstance(payload.get("node_specs"), dict) else {}
        defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
        if not scenario_id:
            raise ValueError("scenario_id is required")
        nodes = graph.get("nodes")
        edges = graph.get("edges")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("graph.nodes is required")
        if not isinstance(edges, list):
            raise ValueError("graph.edges is required")
        missing = [str(n) for n in nodes if str(n) not in node_specs]
        if missing:
            raise ValueError(f"node_specs missing for nodes: {missing}")
        for node in nodes:
            spec = node_specs.get(str(node))
            prompt = spec.get("prompt") if isinstance(spec, dict) else {}
            has_blocks = bool(prompt.get("context_block") or prompt.get("task_block"))
            if isinstance(spec, dict) and has_blocks:
                _validate_node_placeholders(str(node), spec)
        return cls(scenario_id=scenario_id, graph=graph, node_specs=node_specs, defaults=defaults)

