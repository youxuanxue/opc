"""Node runner lifecycle utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts.validators import validate_output_example
from ..shared.workspace import WorkspaceRepo
from ..specs.compiler import compile_prompt_from_node_spec
from .adapters.cursor_cli import run_cursor_cli


def run_llm_node(
    *,
    workspace: Path,
    node: str,
    node_spec: dict[str, Any],
    context: dict[str, Any],
    run_id: str | None = None,
    day: int | None = None,
    agent_workspace: Path | None = None,
) -> dict[str, Any]:
    """Run Cursor agent node.

    workspace: project root (for WorkspaceRepo, logs under .opc/)
    agent_workspace: if provided, agent runs with this as cwd/--workspace so any
        files it creates go here (e.g. .opc/artifacts/{run_id}/). Defaults to workspace.
    """
    prompt, _ = compile_prompt_from_node_spec(node_spec=node_spec, context=context)
    runtime_cfg = node_spec.get("runtime") if isinstance(node_spec.get("runtime"), dict) else {}
    agent_cwd = agent_workspace if agent_workspace is not None else workspace
    runtime_result = run_cursor_cli(
        workspace=agent_cwd,
        prompt=prompt,
        merged_inputs=context,
        runtime_cfg=runtime_cfg,
    )
    parsed_output: dict[str, Any] | list[Any] | str = ""
    stdout_text = str(runtime_result.get("stdout") or "").strip()
    if stdout_text:
        parsed_output = _extract_cursor_payload(stdout_text)
    output_example = node_spec.get("output_example") if isinstance(node_spec.get("output_example"), dict) else None
    if not output_example:
        ok, errors = False, ["output_example is required"]
    else:
        parsed_output = _normalize_with_example(parsed_output, example=output_example, node=node)
        value_range_raw = node_spec.get("output_example_value_range")
        value_range: dict[str, tuple[float, float]] | None = None
        if isinstance(value_range_raw, dict):
            value_range = {}
            for k, rng in value_range_raw.items():
                if isinstance(rng, (list, tuple)) and len(rng) == 2:
                    value_range[str(k)] = (float(rng[0]), float(rng[1]))
        ok, errors = validate_output_example(output_example, parsed_output, value_range=value_range)
    try:
        repo = WorkspaceRepo(workspace)
        repo.append_cursor_agent_io_log(
            node=node,
            prompt=prompt,
            output=parsed_output,
            run_id=run_id,
            day=day,
            raw_stdout=stdout_text or None,
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        "runtime_result": runtime_result,
        "prompt_snapshot": prompt,
        "parsed_output": parsed_output,
        "contract_ok": bool(ok),
        "contract_errors": errors,
    }


def _normalize_with_example(
    parsed_output: dict[str, Any] | list[Any] | str,
    *,
    example: dict[str, Any],
    node: str,
) -> dict[str, Any] | list[Any] | str:
    if not isinstance(parsed_output, dict):
        return parsed_output
    required_top = list(example.keys())
    normalized = dict(parsed_output)
    if "summary" in required_top and not isinstance(normalized.get("summary"), str):
        outputs = normalized.get("outputs")
        if isinstance(outputs, list):
            normalized["summary"] = f"{node} generated {len(outputs)} outputs"
        else:
            normalized["summary"] = f"{node} generated output"
    if "outputs" in required_top and not isinstance(normalized.get("outputs"), list):
        normalized["outputs"] = []
    if "quality_checks" in required_top and not isinstance(
        normalized.get("quality_checks"), (list, dict)
    ):
        normalized["quality_checks"] = []
    return normalized


def _try_parse_json_text(text: str) -> dict[str, Any] | list[Any] | str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = raw
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1]).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    # Fallback: extract JSON object/array from mixed text.
    for left, right in (("{", "}"), ("[", "]")):
        start = cleaned.find(left)
        end = cleaned.rfind(right)
        if start >= 0 and end > start:
            snippet = cleaned[start : end + 1].strip()
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue
    return raw


def _extract_cursor_payload(stdout_text: str) -> dict[str, Any] | list[Any] | str:
    parsed = _try_parse_json_text(stdout_text)
    if isinstance(parsed, dict):
        result_field = parsed.get("result")
        if isinstance(result_field, str):
            nested = _try_parse_json_text(result_field)
            if nested:
                return nested
        return parsed
    return parsed

