"""Built-in OPC templates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_WEEKLY_SPEC_PATH = Path(__file__).resolve().parent.parent / "templates" / "weekly-topic-batch.v2.json"


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


def gzh_curator_template(opc_id: str, name: str) -> dict[str, Any]:
    spec = _load_weekly_spec()
    pd = extract_planning_defaults(spec)
    manifest = {
        "opc_id": opc_id,
        "name": name,
        "template_name": "gzh-curator",
        "version": "0.1.0",
        "industry": "career-content",
        "ceo_persona": "professional civil engineer",
        "target_account": str(pd.get("target_account") or "职场螺丝刀"),
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


def load_template(template_name: str, opc_id: str, name: str) -> dict[str, Any]:
    if template_name == "gzh-curator":
        return gzh_curator_template(opc_id=opc_id, name=name)
    raise ValueError(f"unknown template: {template_name}")

