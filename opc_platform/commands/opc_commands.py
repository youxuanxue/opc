"""Commands for OPC lifecycle and catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.templates import load_template
from ..shared.ids import ensure_safe_id
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def init_workspace(root: Path) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    return repo.init_workspace()


def create_opc(root: Path, opc_id: str, name: str, template: str) -> dict[str, Any]:
    safe_id = ensure_safe_id(opc_id, "opc_id")
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    tpl = load_template(template_name=template, opc_id=safe_id, name=name)
    repo.save_manifest(safe_id, tpl["manifest"])
    for scenario_id, scenario in tpl["scenarios"].items():
        ensure_safe_id(scenario_id, "scenario_id")
        repo.save_scenario(safe_id, scenario_id, scenario)

    catalog = repo.read_catalog()
    existing = [x for x in catalog.get("opcs", []) if x.get("opc_id") == safe_id]
    if not existing:
        catalog.setdefault("opcs", []).append(
            {
                "opc_id": safe_id,
                "name": name,
                "manifest_path": str(repo.opc_manifest_path(safe_id)),
                "created_at": utc_now_iso(),
            }
        )
    repo.write_catalog(catalog)
    return {"opc_id": safe_id, "name": name, "template": template}


def describe_opc(root: Path, opc_id: str) -> dict[str, Any]:
    safe_id = ensure_safe_id(opc_id, "opc_id")
    repo = WorkspaceRepo(root)
    manifest = repo.load_manifest(safe_id)
    scenarios = []
    for p in sorted((repo.opc_dir(safe_id) / "scenarios").glob("*.json")):
        scenarios.append(p.stem)
    return {"manifest": manifest, "scenarios": scenarios}


def list_catalog(root: Path) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    return repo.read_catalog()


def get_app_config(root: Path) -> dict[str, Any]:
    """App config for frontend: default OPC create, scenario, etc."""
    return {
        "default_opc_create": {
            "opc_id": "gzh-curator",
            "name": "GzhCuratorOpc",
            "template": "gzh-curator",
        },
        "default_scenario_id": "weekly-topic-batch",
    }


def get_planning_defaults(
    root: Path,
    scenario_id: str,
    opc_id: str | None = None,
) -> dict[str, Any]:
    """Get planning form defaults: OPC manifest overrides template defaults."""
    from ..domain.templates import extract_planning_defaults, load_weekly_spec_safe

    repo = WorkspaceRepo(root)
    repo.init_workspace()

    template = load_weekly_spec_safe()
    if not template:
        return {"objective": "", "reference_accounts": [], "topic_days": 7, "source_data_dir": "", "target_account": ""}

    out: dict[str, Any] = extract_planning_defaults(template)

    opc_id_str = (opc_id or "").strip() if opc_id is not None else ""
    if opc_id_str:
        try:
            safe_opc_id = ensure_safe_id(opc_id_str, "opc_id")
            manifest = repo.load_manifest(safe_opc_id)
            if isinstance(manifest.get("objective"), str) and manifest["objective"].strip():
                out["objective"] = manifest["objective"].strip()
            refs = manifest.get("references")
            if isinstance(refs, list) and refs:
                out["reference_accounts"] = [str(x).strip() for x in refs if x]
            if isinstance(manifest.get("topic_days"), (int, float)):
                out["topic_days"] = int(manifest["topic_days"])
            if isinstance(manifest.get("source_data_dir"), str):
                out["source_data_dir"] = manifest["source_data_dir"].strip()
            if isinstance(manifest.get("target_account"), str) and manifest["target_account"].strip():
                out["target_account"] = manifest["target_account"].strip()
            # Also try scenario defaults from OPC's scenario
            try:
                scenario = repo.load_scenario(safe_opc_id, scenario_id)
                sc_defaults = scenario.get("defaults") or {}
                if sc_defaults.get("topic_days") is not None:
                    out["topic_days"] = int(sc_defaults["topic_days"])
            except Exception:
                pass
        except Exception:
            pass

    if not (out["objective"] or "").strip():
        raise ValueError(
            "objective 未配置：模板 weekly-topic-batch.v2.json 与 OPC manifest 中均无有效 objective"
        )
    return out

