"""Spec-driven runtime executor."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from org.agent_task_graph import topological_order

from ..contracts.mappers import load_node_result
from ..contracts.validators import validate_output_example
from ..observability.diagnostics import build_diagnostic
from ..observability.events import node_event
from ..shared.io import atomic_write_json, ensure_dir
from ..shared.slug import target_account_to_slug
from ..shared.workspace import WorkspaceRepo, utc_now_iso
from ..specs.graph_edges import parse_edges
from ..specs.scenario_spec import ScenarioSpec
from .adapters.external_cmd import run_external_cmd
from .node_runner import run_llm_node


def ensure_spec_ready(scenario: dict[str, Any]) -> ScenarioSpec:
    return ScenarioSpec.from_dict(scenario)


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _extract_text(parsed_output: dict[str, Any] | list[Any] | str) -> str:
    if isinstance(parsed_output, str):
        return parsed_output.strip()
    if not isinstance(parsed_output, dict):
        return ""
    outputs = parsed_output.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        row = outputs[0]
        for k in ("article_markdown", "content", "text", "body"):
            val = row.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(outputs, dict):
        for k in ("article_markdown", "content", "text", "body"):
            val = outputs.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    summary = parsed_output.get("summary")
    if isinstance(summary, str):
        return summary.strip()
    return ""


def _validate_article_text(text: str) -> tuple[bool, str]:
    body = (text or "").strip()
    if len(body) < 260:
        return False, "article too short"
    para_count = len([x for x in body.split("\n\n") if x.strip()])
    if para_count < 3:
        return False, "article paragraphs too few"
    if body.count("。") + body.count("!") + body.count("！") + body.count(".") < 5:
        return False, "article punctuation too sparse"
    return True, ""


def _extract_topic_rows(parsed_output: dict[str, Any] | list[Any] | str, topic_days: int) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    outputs: Any = None
    if isinstance(parsed_output, dict):
        outputs = parsed_output.get("outputs")
    elif isinstance(parsed_output, list):
        outputs = parsed_output
    if isinstance(outputs, list):
        for idx, row in enumerate(outputs, start=1):
            if not isinstance(row, dict):
                continue
            day = int(row.get("day") or idx)
            topic = str(row.get("topic") or "").strip()
            angle = str(row.get("angle") or "").strip()
            if topic:
                rows.append({"day": day, "topic": topic, "angle": angle})
    if len(rows) < topic_days:
        return [], f"outputs[] too short, expect >= {topic_days}"
    return rows[:topic_days], ""


def _source_summary(run_dir: Path) -> str:
    payload = _read_json(run_dir / "source_materials.json")
    if not isinstance(payload, dict):
        return ""
    count = int(payload.get("source_files_count") or 0)
    sample = payload.get("source_files_sample") if isinstance(payload.get("source_files_sample"), list) else []
    refs = payload.get("reference_accounts") if isinstance(payload.get("reference_accounts"), list) else []
    names = [Path(str(x)).name for x in sample[:8] if isinstance(x, str)]
    return f"source_files_count={count}; sample_files={names}; reference_accounts={refs}"


def _gate_eval(expr: str, *, ai_tone_score: float, soft_threshold: float, hard_threshold: float) -> bool:
    raw = (expr or "").strip()
    if not raw:
        return ai_tone_score >= soft_threshold
    values = {
        "ai_tone_score": ai_tone_score,
        "soft_threshold": soft_threshold,
        "hard_threshold": hard_threshold,
    }
    parts = raw.split()
    if len(parts) != 3:
        return ai_tone_score >= soft_threshold
    left, op, right = parts
    if left not in values or right not in values:
        return ai_tone_score >= soft_threshold
    lv = float(values[left])
    rv = float(values[right])
    if op == ">=":
        return lv >= rv
    if op == ">":
        return lv > rv
    if op == "<=":
        return lv <= rv
    if op == "<":
        return lv < rv
    if op == "==":
        return lv == rv
    return ai_tone_score >= soft_threshold


def _validate_edge_required_payload(
    *,
    run_dir: Path,
    upstream_nodes: list[str],
    edge_required: dict[tuple[str, str], list[str]],
    current_node: str,
) -> tuple[bool, str]:
    """Verify each upstream edge's required_payload exists in upstream result. Returns (ok, error_msg)."""
    for up in upstream_nodes:
        key = (up, current_node)
        required = edge_required.get(key)
        if not required:
            continue
        payload = load_node_result(run_dir, up)
        if not isinstance(payload, dict):
            return False, f"edge {up}->{current_node}: upstream {up} result missing or not object"
        missing = [k for k in required if k not in payload]
        if missing:
            return False, f"edge {up}->{current_node}: required_payload missing: {missing}"
    return True, ""


def _predecessors(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    prev: dict[str, list[str]] = {n: [] for n in nodes}
    for left, right in edges:
        prev.setdefault(right, []).append(left)
    return prev


def _pick_from_upstream(
    key: str,
    upstream_nodes: list[str],
    run_dir: Path,
    merged_inputs: dict[str, Any],
) -> tuple[Any, str]:
    """Resolve a key from merged_inputs or upstream node payloads. No implicit field mapping."""
    if key in merged_inputs:
        return merged_inputs.get(key), "merged_inputs"
    for up in upstream_nodes:
        payload = load_node_result(run_dir, up)
        if key in payload:
            return payload.get(key), f"{up}.result.{key}"
    return None, "missing"


def _resolve_contract_context(
    *,
    node: str,
    node_spec: dict[str, Any],
    merged_inputs: dict[str, Any],
    run_dir: Path,
    upstream_nodes: list[str],
    day_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    input_contract = node_spec.get("input_contract") if isinstance(node_spec.get("input_contract"), dict) else {}
    from_globals = input_contract.get("from_globals") if isinstance(input_contract.get("from_globals"), list) else []
    from_upstream = input_contract.get("from_upstream") if isinstance(input_contract.get("from_upstream"), list) else []
    ctx: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []

    for key in from_globals:
        k = str(key)
        val = merged_inputs.get(k)
        if val is not None:
            ctx[k] = val
            evidence.append({"field": k, "source": "global", "ref": f"inputs.{k}"})

    for item in from_upstream:
        if isinstance(item, str):
            k = str(item)
            val, ref = _pick_from_upstream(k, upstream_nodes, run_dir, merged_inputs)
            if val is not None:
                ctx[k] = val
                evidence.append({"field": k, "source": "upstream", "ref": ref})
            continue
        if not isinstance(item, dict):
            continue
        up_node = str(item.get("node") or "").strip()
        mapping = item.get("map") if isinstance(item.get("map"), dict) else {}
        if not up_node or not mapping:
            continue
        payload = load_node_result(run_dir, up_node)
        for src_key, dst_key in mapping.items():
            src = str(src_key)
            dst = str(dst_key)
            if src in payload:
                ctx[dst] = payload.get(src)
                evidence.append({"field": dst, "source": "upstream_map", "ref": f"{up_node}.result.{src}"})
                continue
            if src == "summary":
                summary = payload.get("summary")
                if summary is not None:
                    ctx[dst] = summary
                    evidence.append({"field": dst, "source": "upstream_map", "ref": f"{up_node}.result.summary"})
            if src == "outputs":
                outputs = payload.get("outputs")
                if outputs is not None:
                    ctx[dst] = outputs
                    evidence.append({"field": dst, "source": "upstream_map", "ref": f"{up_node}.result.outputs"})

    if isinstance(day_context, dict):
        for k, v in day_context.items():
            ctx[k] = v
            evidence.append({"field": k, "source": "day_context", "ref": k})

    from_runtime = input_contract.get("from_runtime") if isinstance(input_contract.get("from_runtime"), list) else []
    for k in from_runtime:
        key = str(k)
        if key in ctx:
            continue
        if key == "target_account_slug":
            target = ctx.get("target_account") or merged_inputs.get("target_account")
            if target:
                ctx[key] = target_account_to_slug(str(target))
                evidence.append({"field": key, "source": "runtime", "ref": "target_account_to_slug"})

    ctx.setdefault("topic_days", merged_inputs.get("topic_days"))
    ctx.setdefault("publish_day", merged_inputs.get("publish_day"))
    return ctx, evidence


def _on_exhaust_policy(node_spec: dict[str, Any]) -> str:
    policy = node_spec.get("failure_policy") if isinstance(node_spec.get("failure_policy"), dict) else {}
    mode = str(policy.get("on_exhaust") or "fail_fast").strip()
    if mode not in {"fail_fast", "continue_with_guard"}:
        return "fail_fast"
    return mode


def execute_spec_run(
    *,
    repo: WorkspaceRepo,
    opc_id: str,
    scenario_id: str,
    inputs: dict[str, Any],
    execute_integrations: bool,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    resume_from_node: str | None = None,
) -> dict[str, Any]:
    scenario = repo.load_scenario(opc_id, scenario_id)
    spec = ensure_spec_ready(scenario)
    if not run_id:
        run_id = f"run-{uuid.uuid4().hex[:10]}"
    run_dir = repo.artifacts_dir / run_id
    ensure_dir(run_dir)

    inherited_artifacts: list[str] = []
    if parent_run_id:
        parent_dir = repo.artifacts_dir / parent_run_id
        if not parent_dir.exists() or not parent_dir.is_dir():
            raise ValueError(f"parent run artifacts not found: {parent_run_id}")
        for src in sorted(parent_dir.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(parent_dir)
            dst = run_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            inherited_artifacts.append(str(dst))

    nodes = list(spec.graph.get("nodes") or [])
    raw_edges = spec.graph.get("edges") or []
    edges, edge_required_payload = parse_edges(raw_edges)
    order = topological_order(nodes, edges)
    node_prev = _predecessors(nodes, edges)
    start_idx = 0
    if resume_from_node:
        if not parent_run_id:
            raise ValueError("resume_from_node requires parent_run_id")
        if resume_from_node not in order:
            raise ValueError(f"resume_from_node not in scenario graph: {resume_from_node}")
        start_idx = order.index(resume_from_node)

    defaults = spec.defaults or {}
    merged_inputs = dict(inputs)
    topic_days = int(merged_inputs.get("topic_days") or defaults.get("topic_days") or 7)
    topic_days = min(max(topic_days, 1), 14)
    publish_day = int(merged_inputs.get("publish_day") or 1)
    publish_day = min(max(publish_day, 1), topic_days)
    merged_inputs["topic_days"] = topic_days
    merged_inputs["publish_day"] = publish_day
    use_cursor_agent = bool(merged_inputs.get("use_cursor_agent", True))

    timeline: list[dict[str, Any]] = []
    artifacts: list[str] = []
    if inherited_artifacts:
        artifacts.extend(inherited_artifacts)
    diagnostics: list[dict[str, Any]] = []
    edge_evidence: list[dict[str, Any]] = []
    status = "succeed"
    ai_score = 0.0
    publish_result: dict[str, Any] | None = None
    decision_ticket_id: str | None = None
    topic_items: list[dict[str, Any]] = []

    run_payload: dict[str, Any] = {
        "run_id": run_id,
        "opc_id": opc_id,
        "scenario_id": scenario_id,
        "parent_run_id": parent_run_id,
        "resume_from_node": resume_from_node,
        "resume_strategy": "from_failed_node" if resume_from_node else None,
        "status": "running",
        "created_at": utc_now_iso(),
        "inputs": merged_inputs,
        "timeline": timeline,
        "artifacts": artifacts,
        "inherited_artifacts_count": len(inherited_artifacts),
        "ai_tone_score": ai_score,
        "decision_required": False,
        "decision_ticket_id": None,
        "publish_result": None,
    }
    repo.save_run(run_id, run_payload)

    def persist() -> None:
        run_payload.update(
            {
                "status": "running" if status == "succeed" else status,
                "timeline": timeline,
                "artifacts": artifacts,
                "ai_tone_score": ai_score,
                "decision_required": bool(decision_ticket_id),
                "decision_ticket_id": decision_ticket_id,
                "publish_result": publish_result,
            }
        )
        repo.save_run(run_id, run_payload)

    def track(path: Path) -> None:
        raw = str(path)
        if raw not in artifacts:
            artifacts.append(raw)

    def fail(node: str, error: str) -> None:
        nonlocal status
        status = "failed"
        timeline.append(node_event(node, "failed", error=error))
        repo.append_error_log(
            {
                "at": utc_now_iso(),
                "opc_id": opc_id,
                "scenario_id": scenario_id,
                "run_id": run_id,
                "node": node,
                "error": error,
            }
        )
        persist()

    def write_artifact(
        *,
        path: Path,
        node: str,
        attempt: int,
        inputs_snapshot: dict[str, Any],
        prompt_snapshot: str,
        raw_runtime_result: dict[str, Any],
        result_payload: dict[str, Any] | list[Any] | str,
        contract_ok: bool,
        contract_errors: list[str],
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "node": node,
            "attempt": attempt,
            "status": "succeed" if contract_ok else "failed",
            "inputs_snapshot": inputs_snapshot,
            "prompt_snapshot": prompt_snapshot,
            "raw_runtime_result": raw_runtime_result,
            "result": result_payload,
            "contract_validation": {"passed": contract_ok, "errors": contract_errors},
            "generated_at": utc_now_iso(),
        }
        if extra:
            payload.update(extra)
        atomic_write_json(path, payload)
        track(path)

    def write_failure_artifact(
        *,
        node: str,
        attempt: int,
        context: dict[str, Any],
        runtime_result: dict[str, Any],
        parsed_output: dict[str, Any] | list[Any] | str,
        contract_errors: list[str],
        day: int | None = None,
    ) -> None:
        suffix = f".day{day}" if isinstance(day, int) and day > 0 else ""
        path = run_dir / f"{node}{suffix}.failed.json"
        write_artifact(
            path=path,
            node=node,
            attempt=attempt,
            inputs_snapshot=context,
            prompt_snapshot="",
            raw_runtime_result=runtime_result,
            result_payload=parsed_output,
            contract_ok=False,
            contract_errors=contract_errors,
            extra={"day": day} if day else None,
        )

    if start_idx > 0:
        for skipped in order[:start_idx]:
            timeline.append(node_event(skipped, "skipped", reason=f"resume_from_node={resume_from_node}"))
        persist()

    for node in order[start_idx:]:
        timeline.append(node_event(node, "running"))
        persist()
        node_spec = spec.node_specs.get(node)
        if not isinstance(node_spec, dict):
            diagnostics.append(
                build_diagnostic(
                    node=node,
                    day=None,
                    reason_code="missing_node_spec",
                    human_message=f"missing node_specs for {node}",
                    fix_hint="检查 scenario spec 的 node_specs 是否包含该节点",
                )
            )
            fail(node, f"missing node_specs for {node}")
            break
        upstream_nodes = node_prev.get(node, [])
        ok, err = _validate_edge_required_payload(
            run_dir=run_dir,
            upstream_nodes=upstream_nodes,
            edge_required=edge_required_payload,
            current_node=node,
        )
        if not ok:
            diagnostics.append(
                build_diagnostic(
                    node=node,
                    day=None,
                    reason_code="edge_payload_missing",
                    human_message=err,
                    fix_hint="确保上游节点已成功执行并产出 required_payload 字段，或从上游节点重试",
                )
            )
            fail(node, err)
            break
        if node == "RetroAgent":
            parts = []
            for d in diagnostics:
                rc = str(d.get("reason_code") or "")
                msg = str(d.get("human_message") or "")
                if rc or msg:
                    parts.append(f"{rc}: {msg}".strip())
            merged_inputs["diagnostics_summary"] = "\n".join(parts) if parts else "no diagnostics"
            merged_inputs["scenario_id"] = scenario_id
            merged_inputs["graph_nodes"] = " -> ".join(nodes)
        mode = str(node_spec.get("execution_mode") or "single")

        if mode == "internal_source_collect":
            context, input_evidence = _resolve_contract_context(
                node=node,
                node_spec=node_spec,
                merged_inputs=merged_inputs,
                run_dir=run_dir,
                upstream_nodes=node_prev.get(node, []),
            )
            edge_evidence.append({"node": node, "day": None, "inputs": input_evidence})
            source_data_dir = context.get("source_data_dir")
            target_account = str(context.get("target_account") or "")
            reference_accounts = context.get("reference_accounts") if isinstance(context.get("reference_accounts"), list) else []
            out = run_dir / "source_materials.json"
            if source_data_dir:
                source_dir = Path(str(source_data_dir)).expanduser().resolve()
                if not source_dir.exists() or not source_dir.is_dir():
                    diagnostics.append(
                        build_diagnostic(
                            node=node,
                            day=None,
                            reason_code="source_data_dir_invalid",
                            human_message=f"source_data_dir is not a valid directory: {source_dir}",
                            fix_hint="检查 inputs.source_data_dir 路径是否存在且为可读目录",
                        )
                    )
                    fail(node, f"source_data_dir is not a valid directory: {source_dir}")
                    break
                files = sorted(str(p) for p in source_dir.rglob("*") if p.is_file())
                atomic_write_json(
                    out,
                    {
                        "source": "pre-scraped",
                        "source_data_dir": str(source_dir),
                        "source_files_count": len(files),
                        "source_files_sample": files[:50],
                        "target_account": target_account,
                        "reference_accounts": reference_accounts,
                        "topic_days": topic_days,
                    },
                )
                track(out)
            else:
                atomic_write_json(
                    out,
                    {
                        "source": "gzh-scraper",
                        "target_account": target_account,
                        "reference_accounts": reference_accounts,
                        "topic_days": topic_days,
                    },
                )
                track(out)
                if execute_integrations:
                    command_tpl = scenario["execution"]["source_collect"]["command"]
                    command = [
                        p.format(
                            scraper_config_path=str(run_dir / "scraper-config.json"),
                            scraper_output_dir=str(run_dir / "scraper-output"),
                        )
                        for p in command_tpl
                    ]
                    atomic_write_json(
                        run_dir / "scraper-config.json",
                        {
                            "accounts": reference_accounts,
                            "target_account": target_account,
                        },
                    )
                    timeline.append(node_event(node, "integration", result=run_external_cmd(command=command, cwd=repo.root)))
            merged_inputs["source_materials_summary"] = _source_summary(run_dir)
            output_payload = _read_json(out) or {}
            example = node_spec.get("output_example") if isinstance(node_spec.get("output_example"), dict) else None
            if not example:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=None,
                        reason_code="config_error",
                        human_message=f"{node} requires output_example",
                        fix_hint="在 node_specs 中为该节点补充 output_example",
                    )
                )
                fail(node, f"{node} requires output_example")
                break
            ok, errs = validate_output_example(example, output_payload)
            write_artifact(
                path=run_dir / f"{node}.json",
                node=node,
                attempt=1,
                inputs_snapshot=context,
                prompt_snapshot="",
                raw_runtime_result={},
                result_payload=output_payload if isinstance(output_payload, (dict, list, str)) else "",
                contract_ok=ok,
                contract_errors=errs,
                extra={"input_evidence": input_evidence},
            )
            if not ok:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=None,
                        reason_code="contract_violation",
                        human_message=f"{node} output contract violation: {'; '.join(errs)}",
                        fix_hint="检查 internal_source_collect 产出是否满足 output_example",
                    )
                )
                fail(node, f"{node} output contract violation: {'; '.join(errs)}")
                break
            timeline.append(node_event(node, "succeed"))
            persist()
            continue

        if mode == "internal_publish":
            context, input_evidence = _resolve_contract_context(
                node=node,
                node_spec=node_spec,
                merged_inputs=merged_inputs,
                run_dir=run_dir,
                upstream_nodes=node_prev.get(node, []),
            )
            edge_evidence.append({"node": node, "day": None, "inputs": input_evidence})
            article_path = run_dir / f"article_humanized_day{publish_day}.md"
            if not article_path.exists():
                article_path = run_dir / f"article_edited_day{publish_day}.md"
            if not article_path.exists():
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=publish_day,
                        reason_code="missing_publish_artifact",
                        human_message=f"missing publish artifact for day {publish_day}",
                        fix_hint="确保 EditorAgent 或 AIToneRewriterAgent 已成功产出 article_edited_day{N}.md 或 article_humanized_day{N}.md",
                    )
                )
                fail(node, f"missing publish artifact for day {publish_day}")
                break
            compliance_day = _read_json(run_dir / f"ComplianceAgent.day{publish_day}.json")
            passed = False
            if isinstance(compliance_day, dict):
                r = compliance_day.get("result")
                if isinstance(r, dict):
                    outputs = r.get("outputs")
                    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
                        passed = bool(outputs[0].get("passed", False))
            if not passed:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=publish_day,
                        reason_code="compliance_failed",
                        human_message=f"day {publish_day} compliance failed; publishing blocked",
                        fix_hint="修改 publish_day 对应文章内容使其通过 ComplianceAgent 校验，或调整 publish_day 选择已合规的天数",
                    )
                )
                fail(node, f"day {publish_day} compliance failed; publishing blocked")
                break
            cmd_tpl = scenario["execution"]["publisher"]["command"]
            target_account = str(context.get("target_account") or "")
            target_slug = str(context.get("target_account_slug") or target_account_to_slug(target_account))
            command = [
                p.format(
                    article_path=str(article_path),
                    target_account=target_account,
                    target_account_slug=target_slug,
                )
                for p in cmd_tpl
            ]
            if "--account" in command:
                i = command.index("--account")
                if i + 1 < len(command):
                    command[i + 1] = target_slug
            publish_result = {
                "target": target_account,
                "target_account_slug": target_slug,
                "saved_to_draft": False,
                "integration_executed": execute_integrations,
                "command": command,
                "jobs": [],
            }
            if execute_integrations:
                result = run_external_cmd(command=command, cwd=repo.root, timeout_sec=180)
                publish_result["result"] = result
                if int(result.get("returncode", 1)) == 0:
                    publish_result["saved_to_draft"] = True
                else:
                    diagnostics.append(
                        build_diagnostic(
                            node=node,
                            day=None,
                            reason_code="publisher_failed",
                            human_message=f"publisher failed: {result.get('stderr')}",
                            fix_hint="检查 copublisher 配置与目标账号，修复后重试入草稿箱",
                        )
                    )
                    fail(node, f"publisher failed: {result.get('stderr')}")
                    break
            atomic_write_json(run_dir / "publish_result.json", publish_result)
            track(run_dir / "publish_result.json")
            merged_inputs["publish_result_summary"] = str(publish_result)
            example = node_spec.get("output_example") if isinstance(node_spec.get("output_example"), dict) else None
            if not example:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=None,
                        reason_code="config_error",
                        human_message=f"{node} requires output_example",
                        fix_hint="在 node_specs 中为该节点补充 output_example",
                    )
                )
                fail(node, f"{node} requires output_example")
                break
            ok, errs = validate_output_example(example, publish_result)
            write_artifact(
                path=run_dir / f"{node}.json",
                node=node,
                attempt=1,
                inputs_snapshot=context,
                prompt_snapshot="",
                raw_runtime_result={},
                result_payload=publish_result,
                contract_ok=ok,
                contract_errors=errs,
                extra={"input_evidence": input_evidence},
            )
            if not ok:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=None,
                        reason_code="contract_violation",
                        human_message=f"{node} output contract violation: {'; '.join(errs)}",
                        fix_hint="检查 internal_publish 产出 publish_result 是否满足 output_example",
                    )
                )
                fail(node, f"{node} output contract violation: {'; '.join(errs)}")
                break
            soft_threshold = float((defaults.get("ai_tone") or {}).get("soft_threshold", 0.5))
            if ai_score >= soft_threshold:
                decision_ticket_id = f"ticket-{uuid.uuid4().hex[:8]}"
                repo.save_decision(
                    decision_ticket_id,
                    {
                        "ticket_id": decision_ticket_id,
                        "opc_id": opc_id,
                        "run_id": run_id,
                        "status": "pending",
                        "owner": "COO",
                        "reason": "AI tone score reached review threshold",
                        "summary": "AI 味分数达到复审阈值；建议先确认改写稿质量，再决定是否直接发布。",
                        "ai_tone_score": ai_score,
                        "options": ["accept_and_publish", "hold_and_rewrite"],
                        "recommended_option": "hold_and_rewrite",
                        "evidence_refs": [str(run_dir / "ai_tone_report.json"), str(run_dir / "publish_result.json")],
                        "created_at": utc_now_iso(),
                    },
                )
            timeline.append(node_event(node, "succeed"))
            persist()
            continue

        if not use_cursor_agent:
            diagnostics.append(
                build_diagnostic(
                    node=node,
                    day=None,
                    reason_code="cursor_agent_missing",
                    human_message="cursor agent result missing",
                    fix_hint="确保节点配置了 cursor_cli runtime 且执行环境可调用 cursor agent",
                )
            )
            fail(node, "cursor agent result missing")
            break

        attempts = int((node_spec.get("retry") or {}).get("max_attempts") or 2)
        attempts = max(1, min(attempts, 5))
        on_exhaust = _on_exhaust_policy(node_spec)

        if mode == "single":
            contract_context, input_evidence = _resolve_contract_context(
                node=node,
                node_spec=node_spec,
                merged_inputs=merged_inputs,
                run_dir=run_dir,
                upstream_nodes=node_prev.get(node, []),
            )
            edge_evidence.append({"node": node, "day": None, "inputs": input_evidence})
            result = None
            for attempt in range(1, attempts + 1):
                context = dict(contract_context)
                result = run_llm_node(
                    workspace=repo.root,
                    node=node,
                    node_spec=node_spec,
                    context=context,
                    run_id=run_id,
                    agent_workspace=run_dir,
                )
                rc = int((result.get("runtime_result") or {}).get("returncode", 1))
                if rc != 0:
                    if attempt == attempts:
                        if on_exhaust == "fail_fast":
                            write_failure_artifact(
                                node=node,
                                attempt=attempt,
                                context=context,
                                runtime_result=(result.get("runtime_result") or {}) if isinstance(result, dict) else {},
                                parsed_output=(result.get("parsed_output") if isinstance(result, dict) else "") or "",
                                contract_errors=["runtime returncode != 0"],
                            )
                            diagnostics.append(
                                build_diagnostic(
                                    node=node,
                                    day=None,
                                    reason_code="cursor_failed",
                                    human_message=f"cursor agent failed: {(result.get('runtime_result') or {}).get('stderr')}",
                                    fix_hint="查看 stderr 详细信息，修复后从该节点重试",
                                )
                            )
                            fail(node, f"cursor agent failed: {(result.get('runtime_result') or {}).get('stderr')}")
                        else:
                            diagnostics.append(
                                build_diagnostic(
                                    node=node,
                                    day=None,
                                    reason_code="cursor_failed_continue",
                                    human_message=f"cursor failed after retries: {(result.get('runtime_result') or {}).get('stderr')}",
                                    fix_hint="continue_with_guard",
                                )
                            )
                    continue
                if bool(result.get("contract_ok")):
                    break
                if attempt == attempts:
                    if on_exhaust == "fail_fast":
                        write_failure_artifact(
                            node=node,
                            attempt=attempt,
                            context=context,
                            runtime_result=(result.get("runtime_result") or {}) if isinstance(result, dict) else {},
                            parsed_output=(result.get("parsed_output") if isinstance(result, dict) else "") or "",
                            contract_errors=list(result.get("contract_errors") or []) if isinstance(result, dict) else [],
                        )
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=None,
                                reason_code="contract_violation",
                                human_message=f"{node} output contract violation: {'; '.join(result.get('contract_errors') or [])}",
                                fix_hint="检查节点输出格式是否符合 output_example，必要时调整 prompt 或重试",
                            )
                        )
                        fail(node, f"{node} output contract violation: {'; '.join(result.get('contract_errors') or [])}")
                    else:
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=None,
                                reason_code="contract_failed_continue",
                                human_message=f"output contract failed after retries: {'; '.join(result.get('contract_errors') or [])}",
                                fix_hint="continue_with_guard",
                            )
                        )
            if status == "failed":
                break
            if not isinstance(result, dict) or not bool(result.get("contract_ok")):
                timeline.append(node_event(node, "skipped", reason="continue_with_guard"))
                persist()
                continue
            parsed_output = (result or {}).get("parsed_output") if isinstance(result, dict) else {}
            if node == "TopicBatchPlannerAgent":
                rows, reason = _extract_topic_rows(parsed_output, topic_days)
                if not rows:
                    diagnostics.append(
                        build_diagnostic(
                            node=node,
                            day=None,
                            reason_code="topic_rows_invalid",
                            human_message=reason,
                            fix_hint="检查 TopicBatchPlannerAgent 输出是否包含足够的 day/topic/angle 行",
                        )
                    )
                    fail(node, reason)
                    break
                topic_items = rows
            if node == "BenchmarkAgent" and isinstance(parsed_output, dict):
                merged_inputs["benchmark_summary"] = str(parsed_output.get("summary") or "").strip()
                merged_inputs["benchmark_outputs"] = parsed_output.get("outputs") if isinstance(parsed_output.get("outputs"), list) else []
            if node == "MetricsAgent" and isinstance(parsed_output, dict):
                merged_inputs["metrics_summary"] = str(parsed_output.get("summary") or "").strip()
                merged_inputs["metrics_outputs"] = str(parsed_output.get("outputs") or [])
            out = run_dir / f"{node}.json"
            write_artifact(
                path=out,
                node=node,
                attempt=1,
                inputs_snapshot=context,
                prompt_snapshot=str((result or {}).get("prompt_snapshot") or ""),
                raw_runtime_result=(result or {}).get("runtime_result") or {},
                result_payload=parsed_output if isinstance(parsed_output, (dict, list, str)) else "",
                contract_ok=bool((result or {}).get("contract_ok")),
                contract_errors=list((result or {}).get("contract_errors") or []),
                extra={"input_evidence": input_evidence},
            )
            timeline.append(node_event(node, "succeed"))
            persist()
            continue

        if mode in {"per_day_article", "per_day_editor", "per_day_compliance", "per_day_ai_detect", "per_day_ai_rewrite"}:
            if not topic_items:
                rows = load_node_result(run_dir, "TopicBatchPlannerAgent").get("outputs")
                if isinstance(rows, list):
                    topic_items = [x for x in rows if isinstance(x, dict)]
            if not topic_items:
                diagnostics.append(
                    build_diagnostic(
                        node=node,
                        day=None,
                        reason_code="topic_items_missing",
                        human_message="topic items missing",
                        fix_hint="确保 TopicBatchPlannerAgent 已成功执行并产出 day/topic/angle 行",
                    )
                )
                fail(node, "topic items missing")
                break
            for row in topic_items:
                day = int(row.get("day") or 0)
                if day <= 0:
                    continue
                day_context: dict[str, Any] = {"day": day, "topic": row.get("topic"), "angle": row.get("angle")}
                source_text = ""
                if mode == "per_day_article":
                    pass
                elif mode == "per_day_editor":
                    p = run_dir / f"article_draft_day{day}.md"
                    if not p.exists():
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=day,
                                reason_code="missing_draft_artifact",
                                human_message=f"missing draft artifact for day {day}",
                                fix_hint="确保 DraftWriterAgent 已成功执行并产出 article_draft_day{N}.md",
                            )
                        )
                        fail(node, f"missing draft artifact for day {day}")
                        break
                    source_text = p.read_text(encoding="utf-8")
                    day_context["article_markdown"] = source_text
                elif mode == "per_day_compliance":
                    p = run_dir / f"article_edited_day{day}.md"
                    if not p.exists():
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=day,
                                reason_code="missing_edited_artifact",
                                human_message=f"missing edited artifact for day {day}",
                                fix_hint="确保 EditorAgent 已成功执行并产出 article_edited_day{N}.md",
                            )
                        )
                        fail(node, f"missing edited artifact for day {day}")
                        break
                    day_context["article_markdown"] = p.read_text(encoding="utf-8")
                elif mode == "per_day_ai_detect":
                    p = run_dir / f"article_edited_day{day}.md"
                    if not p.exists():
                        continue
                    day_context["article_markdown"] = p.read_text(encoding="utf-8")
                    compliance_day = _read_json(run_dir / f"ComplianceAgent.day{day}.json")
                    passed = True
                    note = ""
                    if isinstance(compliance_day, dict):
                        r = compliance_day.get("result")
                        if isinstance(r, dict):
                            outputs = r.get("outputs")
                            if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
                                passed = bool(outputs[0].get("passed", True))
                                note = str(outputs[0].get("note") or "")
                    day_context["compliance_day_passed"] = passed
                    day_context["compliance_day_note"] = note
                    day_context.setdefault("compliance_summary", str(merged_inputs.get("compliance_summary") or ""))
                elif mode == "per_day_ai_rewrite":
                    p = run_dir / f"article_edited_day{day}.md"
                    if not p.exists():
                        continue
                    score_payload = _read_json(run_dir / f"ai_tone_report_day{day}.json")
                    score = float(score_payload.get("ai_tone_score") or 0.0) if isinstance(score_payload, dict) else 0.0
                    soft_threshold = float((defaults.get("ai_tone") or {}).get("soft_threshold", 0.5))
                    hard_threshold = float((defaults.get("ai_tone") or {}).get("hard_threshold", 0.7))
                    if not _gate_eval(str(node_spec.get("gate") or ""), ai_tone_score=score, soft_threshold=soft_threshold, hard_threshold=hard_threshold):
                        continue
                    day_context["ai_tone_score"] = score
                    source_text = p.read_text(encoding="utf-8")
                    day_context["article_markdown"] = source_text

                context, input_evidence = _resolve_contract_context(
                    node=node,
                    node_spec=node_spec,
                    merged_inputs=merged_inputs,
                    run_dir=run_dir,
                    upstream_nodes=node_prev.get(node, []),
                    day_context=day_context,
                )
                edge_evidence.append({"node": node, "day": day, "inputs": input_evidence})

                day_result = None
                for attempt in range(1, attempts + 1):
                    day_result = run_llm_node(
                        workspace=repo.root,
                        node=node,
                        node_spec=node_spec,
                        context=context,
                        run_id=run_id,
                        day=day,
                        agent_workspace=run_dir,
                    )
                    rc = int(((day_result or {}).get("runtime_result") or {}).get("returncode", 1))
                    if rc != 0:
                        if attempt == attempts:
                            if on_exhaust == "fail_fast":
                                write_failure_artifact(
                                    node=node,
                                    attempt=attempt,
                                    context=context,
                                    runtime_result=((day_result or {}).get("runtime_result") or {}),
                                    parsed_output=((day_result or {}).get("parsed_output") or ""),
                                    contract_errors=["runtime returncode != 0"],
                                    day=day,
                                )
                                diagnostics.append(
                                    build_diagnostic(
                                        node=node,
                                        day=day,
                                        reason_code="cursor_failed",
                                        human_message=f"day {day} cursor failed: {((day_result or {}).get('runtime_result') or {}).get('stderr')}",
                                        fix_hint="查看 stderr 详细信息，修复后从该节点重试",
                                    )
                                )
                                fail(node, f"day {day} cursor failed: {((day_result or {}).get('runtime_result') or {}).get('stderr')}")
                            else:
                                diagnostics.append(
                                    build_diagnostic(
                                        node=node,
                                        day=day,
                                        reason_code="cursor_failed_continue",
                                        human_message=f"day {day} cursor failed after retries",
                                        fix_hint="continue_with_guard",
                                    )
                                )
                        continue
                    if bool((day_result or {}).get("contract_ok")):
                        break
                    if attempt == attempts:
                        if on_exhaust == "fail_fast":
                            write_failure_artifact(
                                node=node,
                                attempt=attempt,
                                context=context,
                                runtime_result=((day_result or {}).get("runtime_result") or {}),
                                parsed_output=((day_result or {}).get("parsed_output") or ""),
                                contract_errors=list((day_result or {}).get("contract_errors") or []),
                                day=day,
                            )
                            diagnostics.append(
                                build_diagnostic(
                                    node=node,
                                    day=day,
                                    reason_code="contract_violation",
                                    human_message=f"day {day} output contract violation: {'; '.join((day_result or {}).get('contract_errors') or [])}",
                                    fix_hint="检查节点输出格式是否符合 output_example，必要时调整 prompt 或重试",
                                )
                            )
                            fail(node, f"day {day} output contract violation: {'; '.join((day_result or {}).get('contract_errors') or [])}")
                        else:
                            diagnostics.append(
                                build_diagnostic(
                                    node=node,
                                    day=day,
                                    reason_code="contract_failed_continue",
                                    human_message=f"day {day} output contract failed after retries",
                                    fix_hint="continue_with_guard",
                                )
                            )
                if status == "failed":
                    break
                if not isinstance(day_result, dict) or not bool((day_result or {}).get("contract_ok")):
                    continue
                parsed = (day_result or {}).get("parsed_output")
                article_text = _extract_text(parsed)

                if mode == "per_day_article":
                    ok, reason = _validate_article_text(article_text)
                    if not ok:
                        failed_path = run_dir / f"{node}.day{day}.validation_failed.md"
                        failed_path.write_text(article_text or "", encoding="utf-8")
                        track(failed_path)
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=day,
                                reason_code="article_validation",
                                human_message=f"day {day} {reason}",
                                fix_hint="确保正文不少于 260 字、至少 3 段、标点充分",
                            )
                        )
                        fail(node, f"day {day} {reason}")
                        break
                    out = run_dir / f"article_draft_day{day}.md"
                    out.write_text(article_text, encoding="utf-8")
                    track(out)
                elif mode == "per_day_editor":
                    ok, reason = _validate_article_text(article_text)
                    if not ok:
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=day,
                                reason_code="article_validation",
                                human_message=f"day {day} {reason}",
                                fix_hint="确保正文不少于 260 字、至少 3 段、标点充分",
                            )
                        )
                        fail(node, f"day {day} {reason}")
                        break
                    out = run_dir / f"article_edited_day{day}.md"
                    out.write_text(article_text, encoding="utf-8")
                    track(out)
                elif mode == "per_day_compliance":
                    # consolidated in node summary below
                    pass
                elif mode == "per_day_ai_detect":
                    score = float((parsed or {}).get("ai_tone_score", 0.0)) if isinstance(parsed, dict) else 0.0
                    ai_score = max(ai_score, score)
                    report = run_dir / f"ai_tone_report_day{day}.json"
                    soft_threshold = float((defaults.get("ai_tone") or {}).get("soft_threshold", 0.5))
                    hard_threshold = float((defaults.get("ai_tone") or {}).get("hard_threshold", 0.7))
                    atomic_write_json(
                        report,
                        {
                            "day": day,
                            "ai_tone_score": score,
                            "hard_threshold": hard_threshold,
                            "soft_threshold": soft_threshold,
                            "needs_rewrite": score >= hard_threshold,
                            "needs_review": score >= soft_threshold,
                        },
                    )
                    track(report)
                elif mode == "per_day_ai_rewrite":
                    ok, _ = _validate_article_text(article_text)
                    if not ok:
                        article_text = source_text
                        diagnostics.append(
                            build_diagnostic(
                                node=node,
                                day=day,
                                reason_code="rewrite_fallback_applied",
                                human_message="rewriter output invalid after retries",
                                fix_hint="fallback_to_edited",
                                sample=source_text,
                            )
                        )
                    out = run_dir / f"article_humanized_day{day}.md"
                    out.write_text(article_text, encoding="utf-8")
                    track(out)

                day_json = run_dir / f"{node}.day{day}.json"
                write_artifact(
                    path=day_json,
                    node=node,
                    attempt=1,
                    inputs_snapshot=context,
                    prompt_snapshot=str((day_result or {}).get("prompt_snapshot") or ""),
                    raw_runtime_result=(day_result or {}).get("runtime_result") or {},
                    result_payload=parsed if isinstance(parsed, (dict, list, str)) else "",
                    contract_ok=bool((day_result or {}).get("contract_ok")),
                    contract_errors=list((day_result or {}).get("contract_errors") or []),
                    extra={"day": day, "input_evidence": input_evidence},
                )
                persist()
            if status == "failed":
                break

            if mode == "per_day_compliance":
                outputs: list[dict[str, Any]] = []
                for row in topic_items:
                    day = int(row.get("day") or 0)
                    payload = _read_json(run_dir / f"{node}.day{day}.json")
                    passed = True
                    note = "passed"
                    if isinstance(payload, dict):
                        r = payload.get("result")
                        if isinstance(r, dict):
                            out_rows = r.get("outputs")
                            if isinstance(out_rows, list) and out_rows and isinstance(out_rows[0], dict):
                                passed = bool(out_rows[0].get("passed", True))
                                n = str(out_rows[0].get("note") or "").strip()
                                if n:
                                    note = n
                    outputs.append({"day": day, "passed": passed, "note": note})
                summary_payload = {
                    "summary": f"compliance checked for {len(outputs)} day articles",
                    "outputs": outputs,
                    "quality_checks": [{"check": "all_passed", "passed": all(bool(x.get('passed')) for x in outputs)}],
                }
                merged_inputs["compliance_summary"] = summary_payload["summary"]
                write_artifact(
                    path=run_dir / f"{node}.json",
                    node=node,
                    attempt=1,
                    inputs_snapshot=dict(merged_inputs),
                    prompt_snapshot="",
                    raw_runtime_result={},
                    result_payload=summary_payload,
                    contract_ok=True,
                    contract_errors=[],
                )
            elif mode == "per_day_ai_detect":
                soft_threshold = float((defaults.get("ai_tone") or {}).get("soft_threshold", 0.5))
                hard_threshold = float((defaults.get("ai_tone") or {}).get("hard_threshold", 0.7))
                report = run_dir / "ai_tone_report.json"
                atomic_write_json(
                    report,
                    {
                        "ai_tone_score": ai_score,
                        "hard_threshold": hard_threshold,
                        "soft_threshold": soft_threshold,
                        "needs_rewrite": ai_score >= hard_threshold,
                        "needs_review": ai_score >= soft_threshold,
                    },
                )
                track(report)
                merged_inputs["ai_tone_summary"] = str(_read_json(report) or {})

            timeline.append(node_event(node, "succeed"))
            persist()
            continue

        diagnostics.append(
            build_diagnostic(
                node=node,
                day=None,
                reason_code="unknown_mode",
                human_message=f"unknown execution_mode: {mode}",
                fix_hint="检查 node_specs 中 execution_mode 配置是否支持",
            )
        )
        fail(node, f"unknown execution_mode: {mode}")
        break

    run_payload = {
        "run_id": run_id,
        "opc_id": opc_id,
        "scenario_id": scenario_id,
        "parent_run_id": parent_run_id,
        "resume_from_node": resume_from_node,
        "resume_strategy": "from_failed_node" if resume_from_node else None,
        "status": status,
        "created_at": run_payload["created_at"],
        "inputs": merged_inputs,
        "timeline": timeline,
        "artifacts": artifacts,
        "inherited_artifacts_count": len(inherited_artifacts),
        "ai_tone_score": ai_score,
        "decision_required": bool(decision_ticket_id),
        "decision_ticket_id": decision_ticket_id,
        "publish_result": publish_result,
        "diagnostics": diagnostics,
    }

    release_manifest = _build_release_manifest(
        run_dir=run_dir,
        run_id=run_id,
        opc_id=opc_id,
        scenario_id=scenario_id,
        target_account=str(merged_inputs.get("target_account") or ""),
        topic_days=topic_days,
        publish_day=publish_day,
    )
    release_manifest_path = run_dir / "release_manifest.json"
    atomic_write_json(release_manifest_path, release_manifest)
    if str(release_manifest_path) not in run_payload["artifacts"]:
        run_payload["artifacts"].append(str(release_manifest_path))

    edge_evidence_path = run_dir / "edge_evidence.json"
    atomic_write_json(
        edge_evidence_path,
        {
            "run_id": run_id,
            "opc_id": opc_id,
            "scenario_id": scenario_id,
            "generated_at": utc_now_iso(),
            "records": edge_evidence,
        },
    )
    if str(edge_evidence_path) not in run_payload["artifacts"]:
        run_payload["artifacts"].append(str(edge_evidence_path))

    if diagnostics:
        diagnostics_path = run_dir / "diagnostics.json"
        atomic_write_json(
            diagnostics_path,
            {
                "run_id": run_id,
                "opc_id": opc_id,
                "scenario_id": scenario_id,
                "generated_at": utc_now_iso(),
                "issues": diagnostics,
            },
        )
        run_payload["artifacts"].append(str(diagnostics_path))
    if diagnostics:
        run_payload["diagnostics"] = diagnostics
    repo.save_run(run_id, run_payload)
    return run_payload


def _build_release_manifest(
    run_dir: Path,
    run_id: str,
    opc_id: str,
    scenario_id: str,
    target_account: str,
    topic_days: int,
    publish_day: int,
) -> dict[str, Any]:
    topic_items: list[dict[str, Any]] = []
    planner = _read_json(run_dir / "TopicBatchPlannerAgent.json")
    if isinstance(planner, dict):
        result = planner.get("result")
        rows, _ = _extract_topic_rows(result if isinstance(result, (dict, list, str)) else {}, topic_days)
        topic_items = rows

    publish_result = _read_json(run_dir / "publish_result.json")
    saved: set[str] = set()
    if isinstance(publish_result, dict):
        jobs = publish_result.get("jobs")
        if isinstance(jobs, list):
            for row in jobs:
                if isinstance(row, dict) and row.get("status") == "draft_saved":
                    cid = str(row.get("candidate_id") or "").strip()
                    if cid:
                        saved.add(cid)

    def compliance_passed(day: int) -> bool | None:
        payload = _read_json(run_dir / f"ComplianceAgent.day{day}.json")
        if not isinstance(payload, dict):
            return None
        r = payload.get("result")
        if not isinstance(r, dict):
            return None
        outputs = r.get("outputs")
        if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], dict):
            return None
        return bool(outputs[0].get("passed", False))

    def quality(day: int) -> tuple[float | None, str]:
        payload = _read_json(run_dir / f"ai_tone_report_day{day}.json")
        if not isinstance(payload, dict):
            payload = _read_json(run_dir / "ai_tone_report.json")
        if not isinstance(payload, dict):
            return None, "unknown"
        raw = payload.get("ai_tone_score")
        if not isinstance(raw, (int, float)):
            return None, "unknown"
        score = float(raw)
        if score >= 0.7:
            return score, "high"
        if score >= 0.5:
            return score, "medium"
        return score, "low"

    def evidence_refs_for_day(day: int) -> list[str]:
        refs: list[str] = [f"ComplianceAgent.day{day}.json"]
        ai_report = run_dir / f"ai_tone_report_day{day}.json"
        if ai_report.exists():
            refs.append(f"ai_tone_report_day{day}.json")
        else:
            refs.append("ai_tone_report.json")
        return refs

    items: list[dict[str, Any]] = []
    for row in topic_items:
        day = int(row.get("day") or 0)
        draft = run_dir / f"article_draft_day{day}.md"
        edited = run_dir / f"article_edited_day{day}.md"
        humanized = run_dir / f"article_humanized_day{day}.md"
        publish_target = humanized if humanized.exists() else edited
        if not publish_target.exists():
            publish_target = draft
        candidates: list[dict[str, Any]] = []
        if draft.exists() or edited.exists() or humanized.exists():
            score, risk = quality(day)
            passed = compliance_passed(day)
            cid = f"{run_id}-day-{day}-main"
            candidates.append(
                {
                    "candidate_id": cid,
                    "day": day,
                    "title": f"Day {day} candidate",
                    "draft_artifact": str(draft) if draft.exists() else None,
                    "edited_artifact": str(edited) if edited.exists() else None,
                    "humanized_artifact": str(humanized) if humanized.exists() else None,
                    "publish_target_artifact": str(publish_target) if publish_target.exists() else None,
                    "quality": {
                        "compliance_passed": passed,
                        "ai_tone_score": score,
                        "risk_level": risk,
                    },
                    "publish_status": "draft_saved" if cid in saved else "ready",
                    "evidence_refs": evidence_refs_for_day(day),
                }
            )
        items.append(
            {
                "day": day,
                "topic_id": f"{run_id}/day-{day}",
                "topic": row.get("topic"),
                "angle": row.get("angle"),
                "candidates": candidates,
            }
        )
    return {
        "run_id": run_id,
        "opc_id": opc_id,
        "scenario_id": scenario_id,
        "target_account": target_account,
        "generated_at": utc_now_iso(),
        "items": items,
    }

