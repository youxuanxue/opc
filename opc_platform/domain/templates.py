"""Built-in OPC templates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_WEEKLY_SPEC_PATH = Path(__file__).resolve().parent.parent / "templates" / "weekly-topic-batch.v2.json"
_GZH_PRESETS_PATH = Path(__file__).resolve().parent.parent / "templates" / "gzh-curator-accounts.json"


def _load_weekly_spec() -> dict[str, Any]:
    return json.loads(_WEEKLY_SPEC_PATH.read_text(encoding="utf-8"))


def load_weekly_spec_safe() -> dict[str, Any] | None:
    """Load weekly-topic-batch spec if file exists. Returns None if missing."""
    if not _WEEKLY_SPEC_PATH.exists():
        return None
    try:
        return json.loads(_WEEKLY_SPEC_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_gzh_curator_presets() -> dict[str, dict[str, Any]]:
    """Load account presets for gzh-curator template."""
    if not _GZH_PRESETS_PATH.exists():
        raise FileNotFoundError(f"preset file not found: {_GZH_PRESETS_PATH}")
    try:
        payload = json.loads(_GZH_PRESETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid preset json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("preset file must be a JSON object")
    out: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("preset key must be non-empty string")
        if not isinstance(value, dict):
            raise ValueError(f"preset {key} must be object")
        out[key] = value
    return out


def _require_non_empty_str(value: Any, *, field: str, preset: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"preset {preset} invalid required field: {field}")
    return value.strip()


def _normalize_references(value: Any, *, preset: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"preset {preset} invalid required field: references")
    refs = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    if len(refs) != len(value):
        raise ValueError(f"preset {preset} invalid required field: references")
    return refs


def _normalize_source_data_dir(value: Any, *, preset: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"preset {preset} invalid field type: source_data_dir")
    return value.strip()


def extract_planning_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Extract planning form defaults from template spec (defaults + inputs_schema.properties.default)."""
    defaults = dict(spec.get("defaults") or {})
    props = (spec.get("inputs_schema") or {}).get("properties") or {}
    refs = defaults.get("reference_accounts")
    if not isinstance(refs, list):
        refs = props.get("reference_accounts", {}).get("default")
    if not isinstance(refs, list):
        refs = []
    return {
        "objective": str(defaults.get("objective") or props.get("objective", {}).get("default") or ""),
        "reference_accounts": refs,
        "topic_days": int(defaults.get("topic_days") or props.get("topic_days", {}).get("default") or 7),
        "target_account": str(defaults.get("target_account") or props.get("target_account", {}).get("default") or ""),
        "source_data_dir": str(defaults.get("source_data_dir") or props.get("source_data_dir", {}).get("default") or ""),
    }


def gzh_curator_template(opc_id: str, name: str, account_preset: str) -> dict[str, Any]:
    preset_key = str(account_preset or "").strip()
    if not preset_key:
        raise ValueError("account_preset is required for gzh-curator")
    spec = _load_weekly_spec()
    pd = extract_planning_defaults(spec)
    presets = load_gzh_curator_presets()
    preset = presets.get(preset_key)
    if not isinstance(preset, dict):
        raise ValueError(f"unknown account_preset: {preset_key}")

    target_account = _require_non_empty_str(preset.get("target_account"), field="target_account", preset=preset_key)
    objective = _require_non_empty_str(preset.get("objective"), field="objective", preset=preset_key)
    references = _normalize_references(preset.get("references"), preset=preset_key)
    source_data_dir = _normalize_source_data_dir(preset.get("source_data_dir"), preset=preset_key)
    explicit_name = str(name or "").strip()
    preset_name = str(preset.get("name") or "").strip()
    resolved_name = explicit_name or preset_name or opc_id

    pd["target_account"] = target_account
    pd["objective"] = objective
    pd["reference_accounts"] = references
    pd["source_data_dir"] = source_data_dir
    manifest = {
        "opc_id": opc_id,
        "name": resolved_name,
        "template_name": "gzh-curator",
        "account_preset": preset_key,
        "version": "0.1.0",
        "industry": "career-content",
        "ceo_persona": "professional civil engineer",
        "target_account": str(pd.get("target_account") or ""),
        "objective": str(pd.get("objective") or ""),
        "references": list(pd.get("reference_accounts") or []),
        "source_data_dir": str(pd.get("source_data_dir") or ""),
        "topic_days": int(pd.get("topic_days") or 7),
        "capabilities": [
            "weekly topic batch planning",
            "article generation",
            "ai tone detection and rewriting",
            "wechat draft publishing",
        ],
        "scenarios": [
            "weekly-topic-batch",
            "daily-content-production",
            "daily-ai-tone-guard",
            "daily-content-publish",
            "weekly-retro",
        ],
        "integrations": {
            "gzh_scraper_root": os.environ.get("OPC_GZH_SCRAPER_ROOT", ""),
            "copublisher_root": os.environ.get("OPC_COPUBLISHER_ROOT", ""),
        },
    }
    scenario = spec

    return {"manifest": manifest, "scenarios": {"weekly-topic-batch": scenario}}


def load_template(
    template_name: str,
    opc_id: str,
    name: str,
    account_preset: str | None = None,
) -> dict[str, Any]:
    if template_name == "gzh-curator":
        preset_key = str(account_preset or "").strip()
        if not preset_key:
            raise ValueError("account_preset is required for template gzh-curator")
        return gzh_curator_template(opc_id=opc_id, name=name, account_preset=preset_key)
    raise ValueError(f"unknown template: {template_name}")

