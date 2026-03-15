from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opc_platform.domain.engine import (
    _build_cursor_prompt,
    _load_benchmark_inputs_from_artifact,
    _validate_structured_node_output,
)


class EngineBenchmarkTest(unittest.TestCase):
    def test_benchmark_contract_requires_dimension_and_value(self) -> None:
        ok_payload = {
            "summary": "benchmark summary",
            "outputs": [
                {"dimension": "受众画像", "value": "工程行业基层到中层"},
                {"dimension": "内容定位", "value": "土木职场生存与升职"},
                {"dimension": "表达风格", "value": "接地气+方法论"},
            ],
            "quality_checks": [{"check": "schema_valid", "passed": True}],
        }
        valid, reason = _validate_structured_node_output(
            node="BenchmarkAgent",
            parsed_output=ok_payload,
            topic_days=7,
        )
        self.assertTrue(valid)
        self.assertEqual(reason, "")

        bad_payload = {
            "summary": "benchmark summary",
            "outputs": [
                {"dimension": "受众画像", "value": "工程行业基层到中层"},
                {"dimension": "内容定位"},
                {"dimension": "表达风格", "value": "接地气+方法论"},
            ],
            "quality_checks": [{"check": "schema_valid", "passed": True}],
        }
        valid, reason = _validate_structured_node_output(
            node="BenchmarkAgent",
            parsed_output=bad_payload,
            topic_days=7,
        )
        self.assertFalse(valid)
        self.assertEqual(reason, "outputs[].value required")

    def test_topic_planner_prompt_contains_benchmark_inputs(self) -> None:
        prompt = _build_cursor_prompt(
            node="TopicBatchPlannerAgent",
            merged_inputs={
                "objective": "test objective",
                "reference_accounts": ["A"],
                "target_account": "T",
                "benchmark_summary": "先做定位再做选题",
                "benchmark_outputs": [
                    {"dimension": "受众画像", "value": "工程行业新人"},
                    {"dimension": "内容定位", "value": "职场生存+升职"},
                ],
            },
            topic_days=7,
        )
        self.assertIn("上游 BenchmarkAgent 输入", prompt)
        self.assertIn("benchmark_summary: 先做定位再做选题", prompt)
        self.assertIn("- 受众画像: 工程行业新人", prompt)

    def test_load_benchmark_inputs_from_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            payload = {
                "node": "BenchmarkAgent",
                "result": {
                    "summary": "summary from artifact",
                    "outputs": [
                        {"dimension": "受众画像", "value": "value-a"},
                        {"dimension": "表达风格", "value": "value-b"},
                    ],
                },
            }
            (run_dir / "BenchmarkAgent.json").write_text(str(payload).replace("'", '"'), encoding="utf-8")
            loaded = _load_benchmark_inputs_from_artifact(run_dir)
            self.assertEqual(loaded.get("benchmark_summary"), "summary from artifact")
            self.assertEqual(len(loaded.get("benchmark_outputs") or []), 2)


if __name__ == "__main__":
    unittest.main()
