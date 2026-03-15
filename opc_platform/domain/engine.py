"""Thin domain entrypoint for spec-driven execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..runtime.executor import execute_spec_run
from ..shared.workspace import WorkspaceRepo


def _build_cursor_prompt(
    node: str,
    merged_inputs: dict[str, Any],
    topic_days: int,
    node_spec: dict[str, Any] | None = None,
) -> str:
    """Legacy helper kept only for benchmark unit tests."""
    if isinstance(node_spec, dict):
        return ""
    if node == "TopicBatchPlannerAgent":
        summary = str(merged_inputs.get("benchmark_summary") or "")
        outputs = merged_inputs.get("benchmark_outputs") if isinstance(merged_inputs.get("benchmark_outputs"), list) else []
        lines: list[str] = []
        for row in outputs:
            if not isinstance(row, dict):
                continue
            dimension = str(row.get("dimension") or "").strip()
            value = str(row.get("value") or "").strip()
            if dimension and value:
                lines.append(f"- {dimension}: {value}")
        return (
            "你是 TopicBatchPlannerAgent。\n上游 BenchmarkAgent 输入（TopicBatchPlannerAgent 必须基于此规划选题）：\n"
            f"benchmark_summary: {summary}\n"
            f"benchmark_outputs:\n{chr(10).join(lines)}\n"
            f"topic_days: {topic_days}"
        )
    return f"你是 {node}。"


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_structured_node_output(
    node: str,
    parsed_output: dict[str, Any] | list[Any] | str,
    topic_days: int,
) -> tuple[bool, str]:
    """Legacy helper kept only for benchmark unit tests."""
    if not isinstance(parsed_output, dict):
        return False, "result must be a JSON object"
    if not _non_empty_text(parsed_output.get("summary")):
        return False, "summary must be non-empty string"
    outputs = parsed_output.get("outputs")
    if not isinstance(outputs, list):
        return False, "outputs must be array"
    qc = parsed_output.get("quality_checks")
    if not isinstance(qc, (list, dict)):
        return False, "quality_checks must be list or object"

    if node == "BenchmarkAgent":
        if len(outputs) < 3:
            return False, "outputs length must be >= 3"
        for row in outputs:
            if not isinstance(row, dict):
                return False, "outputs item must be object"
            if not _non_empty_text(row.get("dimension")):
                return False, "outputs[].dimension required"
            if not _non_empty_text(row.get("value")):
                return False, "outputs[].value required"
        return True, ""

    if node == "EditorAgent":
        if len(outputs) < topic_days:
            return False, f"outputs length must be >= {topic_days}"
        return True, ""

    if node == "ComplianceAgent":
        if len(outputs) < topic_days:
            return False, f"outputs length must be >= {topic_days}"
        for row in outputs[:topic_days]:
            if not isinstance(row, dict):
                return False, "outputs item must be object"
            if not isinstance(row.get("day"), int):
                return False, "outputs[].day must be integer"
            if not isinstance(row.get("passed"), bool):
                return False, "outputs[].passed must be boolean"
        return True, ""

    return True, ""


def _read_json_file(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_benchmark_inputs_from_artifact(run_dir: Path) -> dict[str, Any]:
    payload = _read_json_file(run_dir / "BenchmarkAgent.json")
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    if not isinstance(result, dict):
        return {}
    summary = str(result.get("summary") or "").strip()
    outputs_raw = result.get("outputs")
    outputs: list[dict[str, Any]] = []
    if isinstance(outputs_raw, list):
        for row in outputs_raw:
            if isinstance(row, dict):
                outputs.append(row)
    return {"benchmark_summary": summary, "benchmark_outputs": outputs}


def execute_run(
    repo: WorkspaceRepo,
    opc_id: str,
    scenario_id: str,
    inputs: dict[str, Any],
    execute_integrations: bool,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    resume_from_node: str | None = None,
) -> dict[str, Any]:
    return execute_spec_run(
        repo=repo,
        opc_id=opc_id,
        scenario_id=scenario_id,
        inputs=inputs,
        execute_integrations=execute_integrations,
        run_id=run_id,
        parent_run_id=parent_run_id,
        resume_from_node=resume_from_node,
    )
