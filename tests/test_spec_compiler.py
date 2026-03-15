from __future__ import annotations

import unittest

from opc_platform.runtime.executor import ensure_spec_ready
from opc_platform.specs.compiler import compile_prompt_from_node_spec


class SpecCompilerTest(unittest.TestCase):
    def test_compile_prompt_rejects_unknown_placeholder(self) -> None:
        node_spec = {
            "allowed_placeholders": ["objective"],
            "required_placeholders": ["objective"],
            "prompt": {
                "system_block": "role",
                "context_block": "x={unknown_key}",
                "task_block": "do",
            },
        }
        with self.assertRaises(ValueError):
            compile_prompt_from_node_spec(node_spec=node_spec, context={"objective": "a"})

    def test_compile_prompt_rejects_missing_required(self) -> None:
        node_spec = {
            "allowed_placeholders": ["objective"],
            "required_placeholders": ["objective"],
            "prompt": {
                "system_block": "role",
                "context_block": "x={objective}",
                "task_block": "do",
            },
        }
        with self.assertRaises(ValueError):
            compile_prompt_from_node_spec(node_spec=node_spec, context={})

    def test_spec_ready_requires_node_specs(self) -> None:
        scenario = {
            "scenario_id": "s1",
            "graph": {
                "nodes": ["BenchmarkAgent"],
                "edges": [],
            },
            "node_specs": {},
        }
        with self.assertRaises(ValueError):
            ensure_spec_ready(scenario)

    def test_compile_prompt_injects_output_example_block(self) -> None:
        node_spec = {
            "allowed_placeholders": ["topic_days"],
            "required_placeholders": ["topic_days"],
            "output_example": {
                "summary": "规划说明",
                "outputs": [{"day": 1, "topic": "汇报", "angle": "三点支撑"}],
                "quality_checks": [],
            },
            "prompt": {
                "system_block": "你是 Planner。",
                "context_block": "topic_days: {topic_days}",
                "task_block": "规划选题。",
            },
        }
        prompt, _ = compile_prompt_from_node_spec(
            node_spec=node_spec, context={"topic_days": "2"}
        )
        self.assertIn('"summary": "规划说明"', prompt)
        self.assertIn('"outputs"', prompt)
        self.assertIn("day", prompt)
        self.assertIn("topic", prompt)
        self.assertIn("angle", prompt)

    def test_derive_placeholders_from_input_contract(self) -> None:
        """Option B: when allowed/required absent, derive from input_contract."""
        node_spec = {
            "input_contract": {
                "from_globals": ["objective", "topic_days"],
                "from_upstream": [{"node": "Upstream", "map": {"summary": "up_summary"}}],
                "required": ["objective", "topic_days", "up_summary"],
            },
            "prompt": {
                "system_block": "role",
                "context_block": "obj={objective}\ndays={topic_days}\nsum={up_summary}",
                "task_block": "do",
            },
        }
        prompt, found = compile_prompt_from_node_spec(
            node_spec=node_spec,
            context={"objective": "x", "topic_days": "7", "up_summary": "ok"},
        )
        self.assertIn("obj=x", prompt)
        self.assertIn("days=7", prompt)
        self.assertIn("sum=ok", prompt)


if __name__ == "__main__":
    unittest.main()

