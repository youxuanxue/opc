"""通用 org 多智能体协作架构实现。参见 docs/org.md。

注意：为避免模块级副作用，__init__.py 不做 eager import。
调用方应从子模块显式导入，或使用此处的惰性导出。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentSpec",
    "INVALID_CHECK_STATES",
    "create_agent_config",
    "describe_graph",
    "get_agent_config",
    "get_graph",
    "is_constraint_triggered",
    "register_agent_config",
    "register_graph",
    "should_cancel_skills_and_stop",
    "topological_order",
]


def __getattr__(name: str) -> Any:
    if name in {"AgentSpec", "INVALID_CHECK_STATES", "is_constraint_triggered", "should_cancel_skills_and_stop"}:
        from .agent_spec import (
            AgentSpec,
            INVALID_CHECK_STATES,
            is_constraint_triggered,
            should_cancel_skills_and_stop,
        )

        mapping = {
            "AgentSpec": AgentSpec,
            "INVALID_CHECK_STATES": INVALID_CHECK_STATES,
            "is_constraint_triggered": is_constraint_triggered,
            "should_cancel_skills_and_stop": should_cancel_skills_and_stop,
        }
        return mapping[name]

    if name in {"describe_graph", "get_graph", "register_graph", "topological_order"}:
        from .agent_task_graph import describe_graph, get_graph, register_graph, topological_order

        mapping = {
            "describe_graph": describe_graph,
            "get_graph": get_graph,
            "register_graph": register_graph,
            "topological_order": topological_order,
        }
        return mapping[name]

    if name in {"create_agent_config", "get_agent_config", "register_agent_config"}:
        from .meta_agent import create_agent_config, get_agent_config, register_agent_config

        mapping = {
            "create_agent_config": create_agent_config,
            "get_agent_config": get_agent_config,
            "register_agent_config": register_agent_config,
        }
        return mapping[name]

    raise AttributeError(name)
