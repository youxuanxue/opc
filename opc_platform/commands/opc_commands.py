"""Commands for OPC lifecycle and catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import templates
from ..shared.ids import ensure_safe_id
from ..shared.workspace import WorkspaceRepo, utc_now_iso


def init_workspace(root: Path) -> dict[str, Any]:
    repo = WorkspaceRepo(root)
    return repo.init_workspace()


def create_opc(
    root: Path,
    opc_id: str,
    name: str,
    template: str,
    account_preset: str | None = None,
) -> dict[str, Any]:
    safe_id = ensure_safe_id(opc_id, "opc_id")
    template_name = str(template or "").strip()
    if not template_name:
        raise ValueError("template is required")
    preset = str(account_preset or "").strip()
    if template_name == "gzh-curator" and not preset:
        raise ValueError("account_preset is required for template gzh-curator")
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    tpl = templates.load_template(
        template_name=template_name,
        opc_id=safe_id,
        name=name,
        account_preset=preset or None,
    )
    manifest = tpl["manifest"]
    manifest_name = str(manifest.get("name") or name or safe_id).strip() or safe_id
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
                "name": manifest_name,
                "manifest_path": str(repo.opc_manifest_path(safe_id)),
                "created_at": utc_now_iso(),
            }
        )
    repo.write_catalog(catalog)
    return {
        "opc_id": safe_id,
        "name": manifest_name,
        "template": template_name,
        "account_preset": manifest.get("account_preset"),
    }


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


def list_presets(root: Path) -> list[dict[str, str]]:
    _ = root
    rows: list[dict[str, str]] = []
    presets = templates.load_gzh_curator_presets()
    for key, value in presets.items():
        target_account = str(value.get("target_account") or "").strip()
        name = str(value.get("name") or "").strip()
        rows.append({"key": key, "target_account": target_account, "name": name})
    rows.sort(key=lambda item: item["key"])
    return rows


def _manifest_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return None


def merge_run_inputs(manifest: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Merge manifest planning defaults with caller inputs once and only once."""
    refs_raw = manifest.get("references")
    refs = [str(x).strip() for x in refs_raw if str(x).strip()] if isinstance(refs_raw, list) else []
    base: dict[str, Any] = {
        "objective": str(manifest.get("objective") or ""),
        "target_account": str(manifest.get("target_account") or ""),
        "reference_accounts": refs,
        "source_data_dir": str(manifest.get("source_data_dir") or ""),
    }
    topic_days = _manifest_int(manifest.get("topic_days"))
    if topic_days is not None:
        base["topic_days"] = topic_days
    publish_day = _manifest_int(manifest.get("publish_day"))
    if publish_day is not None:
        base["publish_day"] = publish_day

    raw_inputs = dict(inputs or {})
    if "source_data_dir" in raw_inputs:
        raw_source_dir = raw_inputs.get("source_data_dir")
        if raw_source_dir is None:
            raw_inputs["source_data_dir"] = ""
        elif not isinstance(raw_source_dir, str):
            raise ValueError("source_data_dir must be string")
        else:
            source_dir = raw_source_dir.strip()
            if ".." in source_dir:
                raise ValueError("source_data_dir contains forbidden path segment '..'")
            raw_inputs["source_data_dir"] = source_dir
    return {**base, **raw_inputs}


def get_app_config(root: Path) -> dict[str, Any]:
    """App config for frontend: default OPC create, scenario, etc."""
    return {
        "default_opc_create": {
            "opc_id": "gzh-curator",
            "name": "GzhCuratorOpc",
            "template": "gzh-curator",
            "account_preset": "zhichangluosidao",
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
    out: dict[str, Any]
    if not template:
        out = {"objective": "", "reference_accounts": [], "topic_days": 7, "source_data_dir": "", "target_account": ""}
    else:
        out = extract_planning_defaults(template)

    opc_id_text = str(opc_id or "").strip()
    if not opc_id_text:
        raise ValueError("请先创建或选择 OPC")
    try:
        safe_opc_id = ensure_safe_id(opc_id_text, "opc_id")
    except Exception as exc:
        raise ValueError("请先创建或选择 OPC") from exc

    manifest = repo.load_manifest(safe_opc_id)
    if isinstance(manifest.get("objective"), str):
        out["objective"] = manifest["objective"].strip()
    refs = manifest.get("references")
    if isinstance(refs, list):
        out["reference_accounts"] = [str(x).strip() for x in refs if str(x).strip()]
    manifest_topic_days = _manifest_int(manifest.get("topic_days"))
    if manifest_topic_days is not None:
        out["topic_days"] = manifest_topic_days
    if isinstance(manifest.get("source_data_dir"), str):
        out["source_data_dir"] = manifest["source_data_dir"].strip()
    if isinstance(manifest.get("target_account"), str):
        out["target_account"] = manifest["target_account"].strip()

    scenario = repo.load_scenario(safe_opc_id, scenario_id)
    sc_defaults = scenario.get("defaults") or {}
    scenario_topic_days = _manifest_int(sc_defaults.get("topic_days"))
    if scenario_topic_days is not None:
        out["topic_days"] = scenario_topic_days

    if not str(out.get("objective") or "").strip():
        raise ValueError("请先创建或选择 OPC")
    return out

