"""Commands for scenario run and inspection."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from .opc_commands import merge_run_inputs
from ..domain.templates import load_template
from ..domain.engine import execute_run
from ..observability.events import node_event
from ..shared.ids import ensure_safe_id, ensure_safe_token
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def _sync_scenario_with_template(repo: WorkspaceRepo, opc_id: str, scenario_id: str) -> None:
    manifest = repo.load_manifest(opc_id)
    template_name = str(manifest.get("template_name") or "gzh-curator")
    opc_name = str(manifest.get("name") or opc_id)
    account_preset = str(manifest.get("account_preset") or "").strip()
    if template_name == "gzh-curator" and not account_preset:
        raise ValueError("manifest missing account_preset for template gzh-curator")
    tpl = load_template(
        template_name=template_name,
        opc_id=opc_id,
        name=opc_name,
        account_preset=account_preset or None,
    )
    scenarios = tpl.get("scenarios") if isinstance(tpl, dict) else None
    if not isinstance(scenarios, dict):
        raise ValueError(f"template scenarios malformed: {template_name}")
    scenario = scenarios.get(scenario_id)
    if not isinstance(scenario, dict):
        raise ValueError(f"scenario not found in template: {template_name}/{scenario_id}")
    repo.save_scenario(opc_id, scenario_id, scenario)


def run_scenario(
    root: Path,
    opc_id: str,
    scenario_id: str,
    inputs: dict[str, Any],
    execute_integrations: bool,
) -> dict[str, Any]:
    safe_opc_id = ensure_safe_id(opc_id, "opc_id")
    safe_scenario_id = ensure_safe_id(scenario_id, "scenario_id")
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    manifest = repo.load_manifest(safe_opc_id)
    _sync_scenario_with_template(repo=repo, opc_id=safe_opc_id, scenario_id=safe_scenario_id)
    merged_inputs = merge_run_inputs(manifest, inputs)
    return execute_run(
        repo=repo,
        opc_id=safe_opc_id,
        scenario_id=safe_scenario_id,
        inputs=merged_inputs,
        execute_integrations=execute_integrations,
    )


def watch_run(root: Path, run_id: str) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    safe_run_id = ensure_safe_token(run_id, "run_id")
    return repo.load_run(safe_run_id)


def list_runs(root: Path, opc_id: str | None = None) -> list[dict[str, Any]]:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    return repo.list_runs(opc_id=opc_id)


def fail_run(root: Path, run_id: str, node: str | None = None, reason: str = "") -> dict[str, Any]:
    """Mark a stuck run as failed at the given node so it can be retried."""
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    safe_run_id = ensure_safe_token(run_id, "run_id")
    payload = repo.load_run(safe_run_id)
    if str(payload.get("status") or "") != "running":
        raise ValueError(f"run {run_id} is not running (status={payload.get('status')})")
    timeline = list(payload.get("timeline") or [])
    target_node = node
    if not target_node:
        for ev in reversed(timeline):
            if isinstance(ev, dict) and str(ev.get("status") or "") == "running":
                target_node = str(ev.get("node") or "")
                break
    if not target_node:
        raise ValueError("no running node in timeline; specify --node")
    target_node = ensure_safe_token(target_node, "node")
    timeline.append(node_event(target_node, "failed", error=reason or "marked failed (stuck)"))
    payload["timeline"] = timeline
    payload["status"] = "failed"
    payload["updated_at"] = utc_now_iso()
    repo.save_run(safe_run_id, payload)
    return {"run_id": safe_run_id, "status": "failed", "failed_node": target_node}


def start_scenario_run(
    root: Path,
    opc_id: str,
    scenario_id: str,
    inputs: dict[str, Any],
    execute_integrations: bool,
) -> dict[str, Any]:
    safe_opc_id = ensure_safe_id(opc_id, "opc_id")
    safe_scenario_id = ensure_safe_id(scenario_id, "scenario_id")
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    manifest = repo.load_manifest(safe_opc_id)
    _sync_scenario_with_template(repo=repo, opc_id=safe_opc_id, scenario_id=safe_scenario_id)
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    merged_inputs = merge_run_inputs(manifest, inputs)

    def _target() -> None:
        try:
            execute_run(
                repo=repo,
                opc_id=safe_opc_id,
                scenario_id=safe_scenario_id,
                inputs=merged_inputs,
                execute_integrations=execute_integrations,
                run_id=run_id,
            )
        except Exception:  # noqa: BLE001
            try:
                payload = repo.load_run(run_id)
                timeline = list(payload.get("timeline") or [])
                last_node = ""
                for ev in reversed(timeline):
                    if isinstance(ev, dict) and str(ev.get("status") or "") == "running":
                        last_node = str(ev.get("node") or "")
                        break
                if last_node:
                    timeline.append(node_event(last_node, "failed", error="thread crashed (unhandled exception)"))
                payload["timeline"] = timeline
                payload["status"] = "failed"
                payload["updated_at"] = utc_now_iso()
                repo.save_run(run_id, payload)
            except Exception:  # noqa: BLE001
                pass  # best-effort persist on crash
            raise

    thread = threading.Thread(target=_target, name=f"opc-run-{run_id}", daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running"}


def retry_scenario_run(
    root: Path,
    run_id: str,
    from_node: str | None,
    input_overrides: dict[str, Any] | None,
    execute_integrations: bool,
) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    safe_run_id = ensure_safe_token(run_id, "run_id")
    parent = repo.load_run(safe_run_id)
    failed_node = ""
    for event in parent.get("timeline") or []:
        if isinstance(event, dict) and str(event.get("status") or "") == "failed":
            failed_node = str(event.get("node") or "")
            break
    resume_from = str(from_node or failed_node).strip()
    if not resume_from:
        raise ValueError("no failed node found; please provide from_node")
    resume_from = ensure_safe_token(resume_from, "from_node")
    parent_opc_id = ensure_safe_id(str(parent.get("opc_id") or ""), "opc_id")
    parent_scenario_id = ensure_safe_id(str(parent.get("scenario_id") or ""), "scenario_id")
    manifest = repo.load_manifest(parent_opc_id)
    _sync_scenario_with_template(repo=repo, opc_id=parent_opc_id, scenario_id=parent_scenario_id)
    retry_inputs = dict(parent.get("inputs") or {})
    if input_overrides:
        retry_inputs.update(input_overrides)
    merged_inputs = merge_run_inputs(manifest, retry_inputs)
    return execute_run(
        repo=repo,
        opc_id=parent_opc_id,
        scenario_id=parent_scenario_id,
        inputs=merged_inputs,
        execute_integrations=execute_integrations,
        parent_run_id=safe_run_id,
        resume_from_node=resume_from,
    )


def start_retry_scenario_run(
    root: Path,
    run_id: str,
    from_node: str | None,
    input_overrides: dict[str, Any] | None,
    execute_integrations: bool,
) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    safe_run_id = ensure_safe_token(run_id, "run_id")
    parent = repo.load_run(safe_run_id)
    failed_node = ""
    for event in parent.get("timeline") or []:
        if isinstance(event, dict) and str(event.get("status") or "") == "failed":
            failed_node = str(event.get("node") or "")
            break
    resume_from = str(from_node or failed_node).strip()
    if not resume_from:
        raise ValueError("no failed node found; please provide from_node")
    resume_from = ensure_safe_token(resume_from, "from_node")
    parent_opc_id = ensure_safe_id(str(parent.get("opc_id") or ""), "opc_id")
    parent_scenario_id = ensure_safe_id(str(parent.get("scenario_id") or ""), "scenario_id")
    manifest = repo.load_manifest(parent_opc_id)
    _sync_scenario_with_template(repo=repo, opc_id=parent_opc_id, scenario_id=parent_scenario_id)
    retry_inputs = dict(parent.get("inputs") or {})
    if input_overrides:
        retry_inputs.update(input_overrides)
    merged_inputs = merge_run_inputs(manifest, retry_inputs)
    new_run_id = f"run-{uuid.uuid4().hex[:10]}"

    def _target() -> None:
        try:
            execute_run(
                repo=repo,
                opc_id=parent_opc_id,
                scenario_id=parent_scenario_id,
                inputs=merged_inputs,
                execute_integrations=execute_integrations,
                run_id=new_run_id,
                parent_run_id=safe_run_id,
                resume_from_node=resume_from,
            )
        except Exception:  # noqa: BLE001
            try:
                payload = repo.load_run(new_run_id)
                timeline = list(payload.get("timeline") or [])
                last_node = resume_from
                for ev in reversed(timeline):
                    if isinstance(ev, dict) and str(ev.get("status") or "") == "running":
                        last_node = str(ev.get("node") or "")
                        break
                if last_node:
                    timeline.append(node_event(last_node, "failed", error="thread crashed (unhandled exception)"))
                payload["timeline"] = timeline
                payload["status"] = "failed"
                payload["updated_at"] = utc_now_iso()
                repo.save_run(new_run_id, payload)
            except Exception:  # noqa: BLE001
                pass
            raise

    thread = threading.Thread(target=_target, name=f"opc-retry-{new_run_id}", daemon=True)
    thread.start()
    return {
        "run_id": new_run_id,
        "status": "running",
        "parent_run_id": safe_run_id,
        "resume_from_node": resume_from,
    }

