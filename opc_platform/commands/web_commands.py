"""Web API and static frontend hosting for OPC."""

from __future__ import annotations

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..commands.decision_commands import approve_decision, list_decisions
from ..commands.graph_commands import submit_graph_review, view_graph
from ..commands.opc_commands import create_opc, describe_opc, get_app_config, get_planning_defaults, list_catalog
from ..commands.publish_commands import publish_candidate_to_draftbox, trigger_publish
from ..commands.run_commands import (
    list_runs,
    retry_scenario_run,
    run_scenario,
    start_retry_scenario_run,
    start_scenario_run,
    watch_run,
)
from ..shared.ids import ensure_safe_token
from ..shared.io import atomic_write_json, read_json
from ..shared.workspace import WorkspaceRepo

MAX_HTTP_BODY_BYTES = 1_000_000
THEME = {
    "primary": "#0F4C81",
    "success": "#2CB67D",
    "warning": "#F59E0B",
    "danger": "#E11D48",
    "bg": "#F2F4F7",
    "text": "#0B1020",
    "muted": "#475467",
}


def build_web_assets(root: Path) -> Path:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    frontend_dist = root / "web" / "dist"
    repo.web_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(repo.web_dir / "theme.json", THEME)
    if frontend_dist.exists():
        return frontend_dist / "index.html"
    index_path = repo.web_dir / "index.html"
    index_path.write_text(
        "<!doctype html><html><body><h2>Frontend not built.</h2>"
        "<p>Run: cd web && npm install && npm run build</p></body></html>",
        encoding="utf-8",
    )
    return index_path


def serve_web(root: Path, host: str, port: int) -> None:
    repo = WorkspaceRepo(root)
    repo.init_workspace()
    build_web_assets(root)
    frontend_dist = root / "web" / "dist"
    fallback_index = repo.web_dir / "index.html"

    class Handler(BaseHTTPRequestHandler):
        def _json_response(self, status: int, payload: dict | list, extra_headers: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            if length > MAX_HTTP_BODY_BYTES:
                raise ValueError(f"request body too large: {length}")
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8")) if raw else {}

        def _serve_static(self, path: str) -> bool:
            if path.startswith("/api/"):
                return False
            if path in {"/", ""}:
                target = (frontend_dist / "index.html") if frontend_dist.exists() else fallback_index
            else:
                rel = path.lstrip("/")
                target = (frontend_dist / rel) if frontend_dist.exists() else fallback_index
                if frontend_dist.exists() and not target.exists():
                    target = frontend_dist / "index.html"
            if not target.exists():
                return False
            data = target.read_bytes()
            ctype, _ = mimetypes.guess_type(str(target))
            self.send_response(200)
            self.send_header("Content-Type", ctype or "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

        def _send_sse_event(self, event: str, payload: dict) -> bool:
            """Send SSE event. Returns False if client disconnected (BrokenPipe/ConnectionReset)."""
            try:
                body = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/theme":
                    self._json_response(200, THEME)
                    return
                if path == "/api/catalog":
                    self._json_response(200, list_catalog(root))
                    return
                if path == "/api/opcs":
                    catalog = list_catalog(root)
                    self._json_response(200, catalog.get("opcs", []))
                    return
                if path.startswith("/api/opc/"):
                    opc_id = path.rsplit("/", 1)[-1]
                    self._json_response(200, describe_opc(root, opc_id))
                    return
                if path == "/api/config":
                    self._json_response(200, get_app_config(root))
                    return
                if path == "/api/planning-defaults":
                    scenario_id = str((query.get("scenario_id") or ["weekly-topic-batch"])[0])
                    opc_id = (query.get("opc_id") or [None])[0]
                    defaults = get_planning_defaults(root, scenario_id, opc_id=opc_id)
                    self._json_response(
                        200,
                        defaults,
                        extra_headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
                    )
                    return
                if path == "/api/runs":
                    opc_id = (query.get("opc_id") or [None])[0]
                    self._json_response(200, list_runs(root, opc_id=opc_id))
                    return
                if path == "/api/logs/errors":
                    repo_obj = WorkspaceRepo(root)
                    limit = int((query.get("limit") or ["200"])[0])
                    self._json_response(200, repo_obj.read_error_logs(limit=limit))
                    return
                if path == "/api/run/stream":
                    run_id = str((query.get("run_id") or [""])[0])
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
                    self.end_headers()

                    last_timeline_len = -1
                    last_status = ""
                    last_send_time = 0.0
                    KEEPALIVE_INTERVAL = 15.0
                    while True:
                        try:
                            run = watch_run(root, run_id)
                        except Exception:
                            if not self._send_sse_event("waiting", {"run_id": run_id, "status": "pending"}):
                                break
                            time.sleep(0.5)
                            continue

                        status = str(run.get("status") or "")
                        timeline = run.get("timeline") or []
                        now = time.monotonic()
                        if len(timeline) != last_timeline_len or status != last_status:
                            if not self._send_sse_event(
                                "progress",
                                {
                                    "run_id": run_id,
                                    "status": status,
                                    "timeline_len": len(timeline),
                                    "latest": timeline[-1] if timeline else None,
                                },
                            ):
                                break
                            last_timeline_len = len(timeline)
                            last_status = status
                            last_send_time = now

                        if status in {"succeed", "failed", "cancelled", "timeout"}:
                            self._send_sse_event("done", run)
                            self.close_connection = True
                            break
                        # Send keepalive comment when idle (e.g. BenchmarkAgent LLM running 90s+)
                        if now - last_send_time >= KEEPALIVE_INTERVAL:
                            try:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                break
                            last_send_time = now
                        time.sleep(0.8)
                    return
                if path == "/api/run/artifacts":
                    run_id = ensure_safe_token(str((query.get("run_id") or [""])[0]), "run_id")
                    run = watch_run(root, run_id)
                    payload: list[dict] = []
                    for p in run.get("artifacts") or []:
                        ap = Path(str(p)).resolve()
                        if not ap.exists():
                            continue
                        if not str(ap).startswith(str(repo.opc_root.resolve())):
                            continue
                        payload.append(
                            {
                                "name": ap.name,
                                "path": str(ap),
                                "size": ap.stat().st_size,
                            }
                        )
                    self._json_response(200, payload)
                    return
                if path == "/api/run/artifact":
                    run_id = ensure_safe_token(str((query.get("run_id") or [""])[0]), "run_id")
                    name = ensure_safe_token(str((query.get("name") or [""])[0]), "name")
                    run = watch_run(root, run_id)
                    target: Path | None = None
                    for p in run.get("artifacts") or []:
                        ap = Path(str(p)).resolve()
                        if ap.name != name:
                            continue
                        if not str(ap).startswith(str(repo.opc_root.resolve())):
                            continue
                        if ap.exists():
                            target = ap
                            break
                    if target is None:
                        raise ValueError("artifact not found")
                    raw = target.read_text(encoding="utf-8", errors="replace")
                    preview_limit = 30000
                    preview = raw[:preview_limit]
                    self._json_response(
                        200,
                        {
                            "name": target.name,
                            "path": str(target),
                            "preview": preview,
                            "truncated": len(raw) > preview_limit,
                            "size": target.stat().st_size,
                        },
                    )
                    return
                if path == "/api/run/release":
                    run_id = ensure_safe_token(str((query.get("run_id") or [""])[0]), "run_id")
                    run = watch_run(root, run_id)
                    release_path: Path | None = None
                    for p in run.get("artifacts") or []:
                        ap = Path(str(p)).resolve()
                        if ap.name == "release_manifest.json" and ap.exists():
                            release_path = ap
                            break
                    if release_path is None:
                        raise ValueError("release_manifest not found")
                    manifest = read_json(release_path)
                    if not manifest:
                        raise ValueError("release_manifest invalid")
                    self._json_response(200, manifest)
                    return
                if path == "/api/run/release/candidate-content":
                    run_id = ensure_safe_token(str((query.get("run_id") or [""])[0]), "run_id")
                    candidate_id = ensure_safe_token(str((query.get("candidate_id") or [""])[0]), "candidate_id")
                    run = watch_run(root, run_id)
                    release_path: Path | None = None
                    for p in run.get("artifacts") or []:
                        ap = Path(str(p)).resolve()
                        if ap.name == "release_manifest.json" and ap.exists():
                            release_path = ap
                            break
                    if release_path is None:
                        raise ValueError("release_manifest not found")
                    manifest = read_json(release_path)
                    if not isinstance(manifest, dict):
                        raise ValueError("release_manifest invalid")
                    target_path: Path | None = None
                    for item in manifest.get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        for cand in item.get("candidates") or []:
                            if not isinstance(cand, dict):
                                continue
                            if str(cand.get("candidate_id") or "") != candidate_id:
                                continue
                            raw_path = str(cand.get("publish_target_artifact") or cand.get("humanized_artifact") or cand.get("draft_artifact") or "")
                            if raw_path:
                                target_path = Path(raw_path).resolve()
                            break
                    if target_path is None or not target_path.exists():
                        raise ValueError("candidate artifact not found")
                    if not str(target_path).startswith(str(repo.opc_root.resolve())):
                        raise ValueError("candidate artifact path out of workspace")
                    raw = target_path.read_text(encoding="utf-8", errors="replace")
                    preview_limit = 100_000
                    self._json_response(
                        200,
                        {
                            "candidate_id": candidate_id,
                            "artifact_path": str(target_path),
                            "content": raw[:preview_limit],
                            "truncated": len(raw) > preview_limit,
                        },
                    )
                    return
                if path.startswith("/api/run/"):
                    run_id = path.rsplit("/", 1)[-1]
                    self._json_response(200, watch_run(root, run_id))
                    return
                if path == "/api/decisions":
                    opc_id = (query.get("opc_id") or [None])[0]
                    self._json_response(200, list_decisions(root, opc_id=opc_id))
                    return
                if path == "/api/graph/view":
                    opc_id = str((query.get("opc_id") or [""])[0])
                    scenario_id = str((query.get("scenario_id") or [""])[0])
                    self._json_response(200, view_graph(root, opc_id, scenario_id))
                    return
                if self._serve_static(path):
                    return
                self.send_response(404)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client disconnected, nothing to send
            except Exception as exc:  # noqa: BLE001
                try:
                    self._json_response(400, {"error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json_body()
                if path == "/api/scenario/run":
                    payload = run_scenario(
                        root=root,
                        opc_id=str(body.get("opc_id") or ""),
                        scenario_id=str(body.get("scenario_id") or ""),
                        inputs=body.get("inputs") or {},
                        execute_integrations=bool(body.get("execute_integrations", False)),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/scenario/start":
                    payload = start_scenario_run(
                        root=root,
                        opc_id=str(body.get("opc_id") or ""),
                        scenario_id=str(body.get("scenario_id") or ""),
                        inputs=body.get("inputs") or {},
                        execute_integrations=bool(body.get("execute_integrations", False)),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/scenario/retry":
                    payload = start_retry_scenario_run(
                        root=root,
                        run_id=str(body.get("run_id") or ""),
                        from_node=str(body.get("from_node") or "") or None,
                        input_overrides=body.get("input_overrides") or {},
                        execute_integrations=bool(body.get("execute_integrations", False)),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/scenario/retry/run":
                    payload = retry_scenario_run(
                        root=root,
                        run_id=str(body.get("run_id") or ""),
                        from_node=str(body.get("from_node") or "") or None,
                        input_overrides=body.get("input_overrides") or {},
                        execute_integrations=bool(body.get("execute_integrations", False)),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/decision/approve":
                    payload = approve_decision(
                        root=root,
                        ticket_id=str(body.get("ticket_id") or ""),
                        option=str(body.get("option") or "accept"),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/opc/create":
                    payload = create_opc(
                        root=root,
                        opc_id=str(body.get("opc_id") or ""),
                        name=str(body.get("name") or ""),
                        template=str(body.get("template") or "gzh-curator"),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/graph/review":
                    payload = submit_graph_review(
                        root=root,
                        opc_id=str(body.get("opc_id") or ""),
                        scenario_id=str(body.get("scenario_id") or ""),
                        node=str(body.get("node") or ""),
                        comment=str(body.get("comment") or ""),
                        review_type=str(body.get("review_type") or "adjustment"),
                    )
                    self._json_response(200, payload)
                    return
                if path == "/api/publish/trigger":
                    payload = trigger_publish(root=root, run_id=str(body.get("run_id") or ""))
                    self._json_response(200, payload)
                    return
                if path == "/api/run/release/draftbox":
                    run_id = ensure_safe_token(str(body.get("run_id") or ""), "run_id")
                    candidate_id = ensure_safe_token(str(body.get("candidate_id") or ""), "candidate_id")
                    run = watch_run(root, run_id)
                    release_path: Path | None = None
                    for p in run.get("artifacts") or []:
                        ap = Path(str(p)).resolve()
                        if ap.name == "release_manifest.json" and ap.exists():
                            release_path = ap
                            break
                    if release_path is None:
                        raise ValueError("release_manifest not found")
                    manifest = read_json(release_path)
                    if not isinstance(manifest, dict):
                        raise ValueError("release_manifest invalid")
                    found = False
                    found_candidate: dict | None = None
                    for item in manifest.get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        for cand in item.get("candidates") or []:
                            if not isinstance(cand, dict):
                                continue
                            if str(cand.get("candidate_id") or "") == candidate_id:
                                found_candidate = cand
                                found = True
                    if not found:
                        raise ValueError("candidate not found")
                    result = publish_candidate_to_draftbox(root=root, run_id=run_id, candidate=found_candidate or {})
                    for item in manifest.get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        for cand in item.get("candidates") or []:
                            if not isinstance(cand, dict):
                                continue
                            if str(cand.get("candidate_id") or "") == candidate_id:
                                status = str((result.get("last_job") or {}).get("status") or "failed")
                                cand["publish_status"] = "draft_saved" if status == "draft_saved" else "failed"
                                cand["draft_saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    atomic_write_json(release_path, manifest)
                    self._json_response(
                        200,
                        {
                            "manifest": manifest,
                            "publish_result": result,
                        },
                    )
                    return
                self.send_response(404)
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client disconnected, nothing to send
            except Exception as exc:  # noqa: BLE001
                try:
                    self._json_response(400, {"error": str(exc)})
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"OPC web serving at http://{host}:{port}")
    if frontend_dist.exists():
        print(f"frontend: {frontend_dist}")
    else:
        print(f"fallback web: {fallback_index}")
    server.serve_forever()

