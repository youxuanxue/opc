"""Commands for publish workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..shared.ids import ensure_safe_token
from ..shared.io import atomic_write_json
from ..shared.slug import target_account_to_slug
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def _resolve_account_slug(run: dict[str, Any], target_account: str | None = None) -> str:
    inputs = run.get("inputs") or {}
    raw = str(inputs.get("target_account_slug") or "").strip()
    if raw:
        return raw
    account = str(
        target_account
        or run.get("publish_result", {}).get("target")
        or inputs.get("target_account")
        or "职场螺丝刀"
    )
    slug = target_account_to_slug(account)
    return slug or "zhichangluosidao"


def _run_command(command: list[str], cwd: Path, timeout_sec: int = 180) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return {
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "timeout_sec": timeout_sec,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "returncode": -9,
            "stdout": "",
            "stderr": f"command timed out after {timeout_sec}s",
            "timeout_sec": timeout_sec,
        }


def trigger_publish(root: Path, run_id: str) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    safe_run_id = ensure_safe_token(run_id, "run_id")
    run = repo.load_run(safe_run_id)
    result = dict(run.get("publish_result") or {})
    result["triggered_at"] = utc_now_iso()
    result["status"] = "draft_saved" if result.get("saved_to_draft") else "unknown"
    run["publish_result"] = result
    repo.save_run(safe_run_id, run)
    return {
        "run_id": safe_run_id,
        "status": result["status"],
        "target": result.get("target"),
        "saved_to_draft": result.get("saved_to_draft", False),
    }


def _resolve_copublisher_root(repo: WorkspaceRepo, run: dict[str, Any]) -> Path:
    """Resolve copublisher root: OPC manifest integrations > env OPC_COPUBLISHER_ROOT > raise."""
    import os

    opc_id = run.get("opc_id")
    if opc_id:
        try:
            manifest = repo.load_manifest(opc_id)
            integrations = manifest.get("integrations") or {}
            root = (integrations.get("copublisher_root") or "").strip()
            if root:
                return Path(root).expanduser().resolve()
        except Exception:
            pass
    root = os.environ.get("OPC_COPUBLISHER_ROOT", "").strip()
    if root:
        return Path(root).expanduser().resolve()
    raise ValueError(
        "copublisher 路径未配置：请在 OPC manifest.integrations.copublisher_root 或环境变量 OPC_COPUBLISHER_ROOT 中设置"
    )


def publish_candidate_to_draftbox(root: Path, run_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    safe_run_id = ensure_safe_token(run_id, "run_id")
    run = repo.load_run(safe_run_id)
    target_path = Path(str(candidate.get("publish_target_artifact") or "")).expanduser().resolve()
    if not target_path.exists():
        raise ValueError("publish target artifact not found")
    if not str(target_path).startswith(str(repo.opc_root.resolve())):
        raise ValueError("publish target path is out of workspace")
    account_slug = _resolve_account_slug(run)
    copublisher_root = _resolve_copublisher_root(repo, run)
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "copublisher",
        "gzh-drafts",
        str(target_path),
        "--account",
        account_slug,
    ]
    result = _run_command(command=command, cwd=copublisher_root)
    publish_result = dict(run.get("publish_result") or {})
    jobs = publish_result.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    job = {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "publish_target_artifact": str(target_path),
        "target_account_slug": account_slug,
        "triggered_at": utc_now_iso(),
        "command": command,
        "result": result,
        "status": "draft_saved" if int(result.get("returncode", 1)) == 0 else "failed",
    }
    jobs = [x for x in jobs if not isinstance(x, dict) or str(x.get("candidate_id") or "") != job["candidate_id"]]
    jobs.append(job)
    publish_result.update(
        {
            "target": publish_result.get("target") or run.get("inputs", {}).get("target_account"),
            "saved_to_draft": any(isinstance(x, dict) and x.get("status") == "draft_saved" for x in jobs),
            "jobs": jobs,
            "last_job": job,
            "triggered_at": utc_now_iso(),
        }
    )
    run["publish_result"] = publish_result
    repo.save_run(safe_run_id, run)
    publish_result_path = repo.artifacts_dir / safe_run_id / "publish_result.json"
    atomic_write_json(publish_result_path, publish_result)
    return publish_result

