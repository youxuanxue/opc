"""Workspace repository helpers for /.opc artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_write_json, ensure_dir, read_json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.opc_root = root / ".opc"
        self.opcs_dir = self.opc_root / "opcs"
        self.runs_dir = self.opc_root / "runs"
        self.decisions_dir = self.opc_root / "decisions"
        self.artifacts_dir = self.opc_root / "artifacts"
        self.logs_dir = self.opc_root / "logs"
        self.web_dir = self.opc_root / "web"
        self.catalog_path = self.opc_root / "catalog.json"

    def init_workspace(self) -> dict[str, Any]:
        ensure_dir(self.opc_root)
        ensure_dir(self.opcs_dir)
        ensure_dir(self.runs_dir)
        ensure_dir(self.decisions_dir)
        ensure_dir(self.artifacts_dir)
        ensure_dir(self.logs_dir)
        ensure_dir(self.web_dir)
        if not self.catalog_path.exists():
            atomic_write_json(
                self.catalog_path,
                {
                    "version": "1.0.0",
                    "generated_at": utc_now_iso(),
                    "opcs": [],
                },
            )
        return self.read_catalog()

    def read_catalog(self) -> dict[str, Any]:
        return read_json(self.catalog_path, default={"version": "1.0.0", "opcs": []})

    def write_catalog(self, catalog: dict[str, Any]) -> None:
        catalog = dict(catalog)
        catalog["generated_at"] = utc_now_iso()
        atomic_write_json(self.catalog_path, catalog)

    def opc_dir(self, opc_id: str) -> Path:
        return self.opcs_dir / opc_id

    def opc_manifest_path(self, opc_id: str) -> Path:
        return self.opc_dir(opc_id) / "manifest.json"

    def scenario_path(self, opc_id: str, scenario_id: str) -> Path:
        return self.opc_dir(opc_id) / "scenarios" / f"{scenario_id}.json"

    def graph_review_path(self, opc_id: str, scenario_id: str) -> Path:
        return self.opc_dir(opc_id) / "graph_reviews" / f"{scenario_id}.json"

    def save_manifest(self, opc_id: str, manifest: dict[str, Any]) -> None:
        ensure_dir(self.opc_dir(opc_id) / "scenarios")
        atomic_write_json(self.opc_manifest_path(opc_id), manifest)

    def load_manifest(self, opc_id: str) -> dict[str, Any]:
        data = read_json(self.opc_manifest_path(opc_id))
        if data is None:
            raise ValueError(f"manifest not found for opc: {opc_id}")
        return data

    def save_scenario(self, opc_id: str, scenario_id: str, scenario: dict[str, Any]) -> None:
        atomic_write_json(self.scenario_path(opc_id, scenario_id), scenario)

    def load_scenario(self, opc_id: str, scenario_id: str) -> dict[str, Any]:
        data = read_json(self.scenario_path(opc_id, scenario_id))
        if data is None:
            raise ValueError(f"scenario not found: {opc_id}/{scenario_id}")
        return data

    def append_graph_review(self, opc_id: str, scenario_id: str, review: dict[str, Any]) -> dict[str, Any]:
        path = self.graph_review_path(opc_id, scenario_id)
        ensure_dir(path.parent)
        payload = read_json(path, default={"opc_id": opc_id, "scenario_id": scenario_id, "reviews": []})
        payload["reviews"].append(review)
        atomic_write_json(path, payload)
        return payload

    def load_graph_reviews(self, opc_id: str, scenario_id: str) -> dict[str, Any]:
        return read_json(
            self.graph_review_path(opc_id, scenario_id),
            default={"opc_id": opc_id, "scenario_id": scenario_id, "reviews": []},
        )

    def run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def save_run(self, run_id: str, run_payload: dict[str, Any]) -> None:
        atomic_write_json(self.run_path(run_id), run_payload)

    def load_run(self, run_id: str) -> dict[str, Any]:
        data = read_json(self.run_path(run_id))
        if data is None:
            raise ValueError(f"run not found: {run_id}")
        return data

    def list_runs(self, opc_id: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.runs_dir.glob("*.json")):
            data = read_json(path)
            if not data:
                continue
            if opc_id and data.get("opc_id") != opc_id:
                continue
            items.append(data)
        items.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
        return items

    def decision_path(self, ticket_id: str) -> Path:
        return self.decisions_dir / f"{ticket_id}.json"

    def save_decision(self, ticket_id: str, payload: dict[str, Any]) -> None:
        atomic_write_json(self.decision_path(ticket_id), payload)

    def list_decisions(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.decisions_dir.glob("*.json")):
            data = read_json(path)
            if data:
                items.append(data)
        return items

    def errors_log_path(self) -> Path:
        return self.logs_dir / "errors.jsonl"

    def cursor_prompt_log_path(self) -> Path:
        return self.logs_dir / "cursor_agent_prompts.jsonl"

    def append_cursor_prompt_log(
        self,
        node: str,
        prompt: str,
        run_id: str | None = None,
        day: int | None = None,
    ) -> None:
        ensure_dir(self.logs_dir)
        payload: dict[str, Any] = {
            "node": node,
            "at": utc_now_iso(),
            "prompt": prompt,
        }
        if run_id:
            payload["run_id"] = run_id
        if day is not None:
            payload["day"] = day
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self.cursor_prompt_log_path().open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def append_cursor_agent_io_log(
        self,
        node: str,
        prompt: str,
        output: Any,
        run_id: str | None = None,
        day: int | None = None,
        raw_stdout: str | None = None,
    ) -> None:
        """Append one jsonl line with agent input (prompt) and output for debugging."""
        ensure_dir(self.logs_dir)
        payload: dict[str, Any] = {
            "node": node,
            "at": utc_now_iso(),
            "prompt": prompt,
            "output": output,
        }
        if run_id:
            payload["run_id"] = run_id
        if day is not None:
            payload["day"] = day
        if raw_stdout is not None and raw_stdout != "":
            payload["raw_stdout"] = raw_stdout
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self.cursor_prompt_log_path().open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def append_error_log(self, payload: dict[str, Any]) -> None:
        ensure_dir(self.logs_dir)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self.errors_log_path().open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def read_error_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        path = self.errors_log_path()
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if limit > 0:
            return rows[-limit:]
        return rows

