"""MetaAgent：按 AgentSpec 创建业务 Agent 的工厂。

参见 docs/org.md §3.1。注册表初始为空，由业务包在 import 或首次调用时注册。
"""

from __future__ import annotations

from typing import Any

_AGENT_CONFIG_REGISTRY: dict[str, dict[str, Any]] = {}


def register_agent_config(scenario: str, config: dict[str, Any]) -> None:
    """注册 scenario 的 agent 配置。由业务包调用。"""
    _AGENT_CONFIG_REGISTRY[scenario] = dict(config)


def get_agent_config(scenario: str) -> dict[str, Any]:
    """返回 scenario 的 agent 配置。未注册则 raise ValueError。"""
    if scenario not in _AGENT_CONFIG_REGISTRY:
        raise ValueError(f"scenario not registered: {scenario}")
    return _AGENT_CONFIG_REGISTRY[scenario]


def create_agent_config(scenario: str) -> dict[str, Any]:
    """按 AgentSpec 规范创建业务 Agent 的 execution 配置（org.md §3.1 createAgent）。

    从注册表获取，产出 owner_agent、collaborators、sla_hours 等供 COO 注入 action.execution。

    Returns:
        {"owner_agent": str, "collaborators": list[str], ...}
    """
    return dict(get_agent_config(scenario))
