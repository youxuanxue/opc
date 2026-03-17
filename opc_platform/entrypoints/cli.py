"""OPC CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..commands.decision_commands import approve_decision, list_decisions
from ..commands.graph_commands import submit_graph_review, view_graph
from ..commands.opc_commands import create_opc, describe_opc, init_workspace, list_catalog
from ..commands.publish_commands import trigger_publish
from ..commands.run_commands import fail_run, retry_scenario_run, run_scenario, watch_run
from ..commands.web_commands import build_web_assets, serve_web

MAX_INPUT_BYTES = 1_000_000


def _load_input_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise ValueError(f"input file not found: {p}")
    if p.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"input file too large: {p.stat().st_size} > {MAX_INPUT_BYTES}")
    return json.loads(p.read_text(encoding="utf-8"))


def _dump(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, dict):
        for k, v in payload.items():
            print(f"{k}: {v}")
        return
    if isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, ensure_ascii=False))
        return
    print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opc")
    parser.add_argument("--workspace", default=".", help="Workspace root path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize .opc workspace")

    opc_cmd = sub.add_parser("opc", help="Manage OPC entities")
    opc_sub = opc_cmd.add_subparsers(dest="opc_action", required=True)
    p_create = opc_sub.add_parser("create", help="Create a new OPC")
    p_create.add_argument("--id", required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--from-template", required=True)
    p_create.add_argument("--account", default=None, help="Account preset key (required for gzh-curator)")
    p_describe = opc_sub.add_parser("describe", help="Describe OPC")
    p_describe.add_argument("--id", required=True)

    catalog_cmd = sub.add_parser("catalog", help="Catalog operations")
    catalog_sub = catalog_cmd.add_subparsers(dest="catalog_action", required=True)
    catalog_sub.add_parser("list")

    scenario_cmd = sub.add_parser("scenario", help="Scenario operations")
    scenario_sub = scenario_cmd.add_subparsers(dest="scenario_action", required=True)
    p_run = scenario_sub.add_parser("run", help="Run one scenario")
    p_run.add_argument("--opc", required=True)
    p_run.add_argument("--scenario", required=True)
    p_run.add_argument("--input", default=None)
    p_run.add_argument("--topic-days", type=int, default=None)
    p_run.add_argument("--source-data-dir", default=None, help="Pre-scraped materials directory")
    p_run.add_argument("--execute-integrations", action="store_true")
    p_retry = scenario_sub.add_parser("retry", help="Retry from failed node")
    p_retry.add_argument("--run", required=True, help="Parent failed run id")
    p_retry.add_argument("--from-node", default=None, help="Start from this node (default: first failed node)")
    p_retry.add_argument("--input", default=None, help="JSON file with input overrides")
    p_retry.add_argument("--execute-integrations", action="store_true")

    run_cmd = sub.add_parser("run", help="Run inspection")
    run_sub = run_cmd.add_subparsers(dest="run_action", required=True)
    p_watch = run_sub.add_parser("watch", help="Show run details")
    p_watch.add_argument("--run", required=True)
    p_fail = run_sub.add_parser("fail", help="Mark stuck run as failed so it can be retried")
    p_fail.add_argument("--run", required=True)
    p_fail.add_argument("--node", default=None, help="Node to mark failed (default: last running node)")
    p_fail.add_argument("--reason", default="", help="Failure reason")

    decision_cmd = sub.add_parser("decision", help="Decision center")
    decision_sub = decision_cmd.add_subparsers(dest="decision_action", required=True)
    p_list = decision_sub.add_parser("list")
    p_list.add_argument("--opc", default=None)
    p_approve = decision_sub.add_parser("approve")
    p_approve.add_argument("--ticket", required=True)
    p_approve.add_argument("--option", required=True)

    publish_cmd = sub.add_parser("publish", help="Publish commands")
    publish_sub = publish_cmd.add_subparsers(dest="publish_action", required=True)
    p_trigger = publish_sub.add_parser("trigger")
    p_trigger.add_argument("--run", required=True)

    graph_cmd = sub.add_parser("graph", help="Read-only graph with review comments")
    graph_sub = graph_cmd.add_subparsers(dest="graph_action", required=True)
    p_graph_view = graph_sub.add_parser("view")
    p_graph_view.add_argument("--opc", required=True)
    p_graph_view.add_argument("--scenario", required=True)
    p_graph_review = graph_sub.add_parser("review")
    p_graph_review.add_argument("--opc", required=True)
    p_graph_review.add_argument("--scenario", required=True)
    p_graph_review.add_argument("--node", required=True)
    p_graph_review.add_argument("--type", default="adjustment")
    p_graph_review.add_argument("--comment", required=True)

    web_cmd = sub.add_parser("web", help="Web visualization")
    web_sub = web_cmd.add_subparsers(dest="web_action", required=True)
    web_sub.add_parser("build")
    p_serve = web_sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8787)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.workspace).resolve()
    as_json = bool(args.json)

    try:
        if args.command == "init":
            payload = init_workspace(root)
            _dump(payload, as_json)
            return 0

        if args.command == "opc" and args.opc_action == "create":
            payload = create_opc(root, args.id, args.name, args.from_template, args.account)
            _dump(payload, as_json)
            return 0

        if args.command == "opc" and args.opc_action == "describe":
            payload = describe_opc(root, args.id)
            _dump(payload, as_json)
            return 0

        if args.command == "catalog" and args.catalog_action == "list":
            payload = list_catalog(root)
            _dump(payload, as_json)
            return 0

        if args.command == "scenario" and args.scenario_action == "run":
            inputs = _load_input_json(args.input)
            if args.topic_days is not None:
                inputs["topic_days"] = int(args.topic_days)
            if args.source_data_dir:
                inputs["source_data_dir"] = args.source_data_dir
            payload = run_scenario(
                root=root,
                opc_id=args.opc,
                scenario_id=args.scenario,
                inputs=inputs,
                execute_integrations=bool(args.execute_integrations),
            )
            _dump(payload, as_json)
            return 0

        if args.command == "scenario" and args.scenario_action == "retry":
            overrides = _load_input_json(args.input)
            payload = retry_scenario_run(
                root=root,
                run_id=args.run,
                from_node=args.from_node,
                input_overrides=overrides,
                execute_integrations=bool(args.execute_integrations),
            )
            _dump(payload, as_json)
            return 0

        if args.command == "run" and args.run_action == "watch":
            payload = watch_run(root=root, run_id=args.run)
            _dump(payload, as_json)
            return 0

        if args.command == "run" and args.run_action == "fail":
            payload = fail_run(
                root=root,
                run_id=args.run,
                node=args.node,
                reason=args.reason,
            )
            _dump(payload, as_json)
            return 0

        if args.command == "decision" and args.decision_action == "list":
            payload = list_decisions(root=root, opc_id=args.opc)
            _dump(payload, as_json)
            return 0

        if args.command == "decision" and args.decision_action == "approve":
            payload = approve_decision(root=root, ticket_id=args.ticket, option=args.option)
            _dump(payload, as_json)
            return 0

        if args.command == "publish" and args.publish_action == "trigger":
            payload = trigger_publish(root=root, run_id=args.run)
            _dump(payload, as_json)
            return 0

        if args.command == "graph" and args.graph_action == "view":
            payload = view_graph(root=root, opc_id=args.opc, scenario_id=args.scenario)
            _dump(payload, as_json)
            return 0

        if args.command == "graph" and args.graph_action == "review":
            payload = submit_graph_review(
                root=root,
                opc_id=args.opc,
                scenario_id=args.scenario,
                node=args.node,
                comment=args.comment,
                review_type=args.type,
            )
            _dump(payload, as_json)
            return 0

        if args.command == "web" and args.web_action == "build":
            path = build_web_assets(root)
            _dump({"index": str(path)}, as_json)
            return 0

        if args.command == "web" and args.web_action == "serve":
            serve_web(root=root, host=args.host, port=args.port)
            return 0

        parser.print_help()
        return 2
    except Exception as exc:  # noqa: BLE001
        if as_json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

