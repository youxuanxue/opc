from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from opc_platform.commands.graph_commands import submit_graph_review, view_graph
from opc_platform.commands.opc_commands import create_opc, init_workspace
from opc_platform.commands.run_commands import retry_scenario_run, run_scenario
from opc_platform.shared.ids import ensure_safe_id


class MvpTest(unittest.TestCase):
    def test_id_validation_blocks_path_tokens(self) -> None:
        with self.assertRaises(ValueError):
            ensure_safe_id("../oops", "opc_id")

    def test_create_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            create_opc(root, "gzh-curator", "GzhCuratorOpc", "gzh-curator", "zhichangluosidao")
            result = run_scenario(
                root=root,
                opc_id="gzh-curator",
                scenario_id="weekly-topic-batch",
                inputs={
                    "objective": "test",
                    "reference_accounts": ["刘润"],
                    "use_cursor_agent": False,
                },
                execute_integrations=False,
            )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["artifacts"])
            run_path = root / ".opc" / "runs" / f"{result['run_id']}.json"
            self.assertTrue(run_path.exists())
            payload = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], result["run_id"])

    def test_graph_review(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            create_opc(root, "gzh-curator", "GzhCuratorOpc", "gzh-curator", "zhichangluosidao")
            submit_graph_review(
                root=root,
                opc_id="gzh-curator",
                scenario_id="weekly-topic-batch",
                node="PublisherAgent",
                comment="Add manual approval gate",
                review_type="approval",
            )
            graph = view_graph(root, "gzh-curator", "weekly-topic-batch")
            self.assertEqual(len(graph["reviews"]), 1)

    def test_retry_from_failed_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            create_opc(root, "gzh-curator", "GzhCuratorOpc", "gzh-curator", "zhichangluosidao")
            first = run_scenario(
                root=root,
                opc_id="gzh-curator",
                scenario_id="weekly-topic-batch",
                inputs={
                    "objective": "test",
                    "reference_accounts": ["刘润"],
                    "use_cursor_agent": False,
                },
                execute_integrations=False,
            )
            self.assertEqual(first["status"], "failed")
            retried = retry_scenario_run(
                root=root,
                run_id=first["run_id"],
                from_node=None,
                input_overrides=None,
                execute_integrations=False,
            )
            self.assertEqual(retried["status"], "failed")
            self.assertEqual(retried.get("parent_run_id"), first["run_id"])
            self.assertEqual(retried.get("resume_from_node"), "BenchmarkAgent")
            timeline = retried.get("timeline") or []
            self.assertTrue(any(x.get("status") == "skipped" and x.get("node") == "SourceCollectAgent" for x in timeline))

    def test_run_syncs_scenario_spec_from_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            create_opc(root, "gzh-curator", "GzhCuratorOpc", "gzh-curator", "zhichangluosidao")
            scenario_path = root / ".opc" / "opcs" / "gzh-curator" / "scenarios" / "weekly-topic-batch.json"
            scenario_payload = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenario_payload.pop("node_specs", None)
            scenario_path.write_text(json.dumps(scenario_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            _ = run_scenario(
                root=root,
                opc_id="gzh-curator",
                scenario_id="weekly-topic-batch",
                inputs={
                    "objective": "test",
                    "reference_accounts": ["刘润"],
                    "use_cursor_agent": False,
                },
                execute_integrations=False,
            )
            reloaded = json.loads(scenario_path.read_text(encoding="utf-8"))
            self.assertIn("node_specs", reloaded)
            self.assertIn("BenchmarkAgent", reloaded.get("node_specs") or {})


if __name__ == "__main__":
    unittest.main()

