"""Prompt compiler with placeholder allowlist and required checks."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def derive_placeholders_from_input_contract(input_contract: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Derive allowed_placeholders and required_placeholders from input_contract.

    allowed = from_globals + from_upstream (string keys + map values) + from_runtime
    required = input_contract.required if present, else from_globals + from_upstream keys
    """
    if not isinstance(input_contract, dict):
        return [], []

    from_globals = input_contract.get("from_globals")
    from_upstream = input_contract.get("from_upstream")
    from_runtime = input_contract.get("from_runtime")
    explicit_required = input_contract.get("required")

    allowed: list[str] = []
    upstream_keys: list[str] = []

    if isinstance(from_globals, list):
        for k in from_globals:
            allowed.append(str(k))
    if isinstance(from_upstream, list):
        for item in from_upstream:
            if isinstance(item, str):
                k = str(item)
                allowed.append(k)
                upstream_keys.append(k)
            elif isinstance(item, dict):
                mapping = item.get("map") if isinstance(item.get("map"), dict) else {}
                for dst in mapping.values():
                    k = str(dst)
                    allowed.append(k)
                    upstream_keys.append(k)
    if isinstance(from_runtime, list):
        for k in from_runtime:
            allowed.append(str(k))

    allowed = list(dict.fromkeys(allowed))

    if isinstance(explicit_required, list):
        required = [str(k) for k in explicit_required]
    else:
        gset = {str(k) for k in (from_globals or [])}
        uset = set(upstream_keys)
        required = [k for k in allowed if k in gset or k in uset]

    return allowed, required


def _to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    return "" if value is None else str(value)


def _format_output_example_block(output_example: dict[str, Any]) -> str:
    """Format output_example as prompt-injectable block. Escapes braces for format_map."""
    example_json = json.dumps(output_example, ensure_ascii=False, indent=2)
    escaped = example_json.replace("{", "{{").replace("}", "}}")
    return escaped


def compile_prompt_from_node_spec(node_spec: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[str]]:
    prompt_cfg = node_spec.get("prompt") if isinstance(node_spec.get("prompt"), dict) else {}
    system_block = str(prompt_cfg.get("system_block") or "").strip()
    context_block = str(prompt_cfg.get("context_block") or "").strip()
    task_block = str(prompt_cfg.get("task_block") or "").strip()
    if not (system_block or context_block or task_block):
        raise ValueError("prompt blocks are required: system_block/context_block/task_block")

    blocks: list[str] = [system_block]
    output_example = node_spec.get("output_example") if isinstance(node_spec.get("output_example"), dict) else None
    if output_example:
        blocks.append(_format_output_example_block(output_example))
    blocks.extend([context_block, task_block])
    template = "\n\n".join([x for x in blocks if x])
    found = sorted(set(PLACEHOLDER_RE.findall(template)))
    allowed = node_spec.get("allowed_placeholders") if isinstance(node_spec.get("allowed_placeholders"), list) else None
    required = node_spec.get("required_placeholders") if isinstance(node_spec.get("required_placeholders"), list) else None
    if allowed is None or required is None:
        derived_allowed, derived_required = derive_placeholders_from_input_contract(
            node_spec.get("input_contract") if isinstance(node_spec.get("input_contract"), dict) else {}
        )
        if allowed is None:
            allowed = derived_allowed
        if required is None:
            required = derived_required
    unknown = [x for x in found if x not in allowed]
    if unknown:
        raise ValueError(f"unknown placeholders: {unknown}")
    missing = [x for x in required if not _to_text(context.get(x)).strip()]
    if missing:
        raise ValueError(f"required placeholders missing values: {missing}")
    values = defaultdict(str)
    for key in allowed:
        values[key] = _to_text(context.get(key))
    return template.format_map(values), found

