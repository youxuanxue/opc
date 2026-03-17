from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from opc_platform.commands.opc_commands import create_opc, init_workspace, list_presets, merge_run_inputs
from opc_platform.commands.run_commands import run_scenario, start_scenario_run, watch_run
from opc_platform.shared.slug import target_account_to_slug


class MultiAccountTest(unittest.TestCase):
    def test_create_gzh_curator_requires_account_preset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            with self.assertRaises(ValueError):
                create_opc(root, "yiqi-growth", "懿起成长", "gzh-curator", None)

    def test_create_with_preset_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            payload = create_opc(root, "yiqi-growth", "", "gzh-curator", "yiqichengzhang")
            self.assertEqual(payload.get("opc_id"), "yiqi-growth")
            manifest = (root / ".opc" / "opcs" / "yiqi-growth" / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"account_preset": "yiqichengzhang"', manifest)
            self.assertIn('"target_account": "懿起成长"', manifest)

    def test_list_presets_and_slug(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            presets = list_presets(root)
            keys = {row["key"] for row in presets}
            self.assertIn("zhichangluosidao", keys)
            self.assertIn("yiqichengzhang", keys)
            self.assertEqual(target_account_to_slug("懿起成长"), "yiqichengzhang")

    def test_merge_run_inputs_and_source_data_dir_validation(self) -> None:
        manifest = {
            "objective": "manifest objective",
            "target_account": "懿起成长",
            "references": ["A", "B"],
            "source_data_dir": "",
            "topic_days": 7,
        }
        merged = merge_run_inputs(manifest, {"topic_days": 10, "source_data_dir": "/tmp/data"})
        self.assertEqual(merged["objective"], "manifest objective")
        self.assertEqual(merged["target_account"], "懿起成长")
        self.assertEqual(merged["reference_accounts"], ["A", "B"])
        self.assertEqual(merged["topic_days"], 10)
        with self.assertRaises(ValueError):
            merge_run_inputs(manifest, {"source_data_dir": "../hack"})

    def test_run_and_start_use_manifest_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            init_workspace(root)
            create_opc(root, "yiqi-growth", "", "gzh-curator", "yiqichengzhang")

            run_payload = run_scenario(
                root=root,
                opc_id="yiqi-growth",
                scenario_id="weekly-topic-batch",
                inputs={"use_cursor_agent": False},
                execute_integrations=False,
            )
            self.assertEqual(run_payload.get("inputs", {}).get("target_account"), "懿起成长")
            self.assertTrue(run_payload.get("inputs", {}).get("objective"))
            self.assertEqual(run_payload.get("status"), "failed")

            started = start_scenario_run(
                root=root,
                opc_id="yiqi-growth",
                scenario_id="weekly-topic-batch",
                inputs={"use_cursor_agent": False},
                execute_integrations=False,
            )
            run_id = str(started.get("run_id") or "")
            self.assertTrue(run_id)
            status = "running"
            detail: dict[str, object] = {}
            for _ in range(40):
                try:
                    detail = watch_run(root, run_id)
                except ValueError:
                    time.sleep(0.1)
                    continue
                status = str(detail.get("status") or "")
                if status != "running":
                    self.assertEqual(detail.get("inputs", {}).get("target_account"), "懿起成长")
                    break
                time.sleep(0.1)
            self.assertNotEqual(status, "running")

    def test_web_route_order_for_presets_before_opc_detail(self) -> None:
        content = Path("opc_platform/commands/web_commands.py").read_text(encoding="utf-8")
        self.assertLess(content.find('if path == "/api/opc/presets"'), content.find('if path.startswith("/api/opc/")'))


if __name__ == "__main__":
    unittest.main()
