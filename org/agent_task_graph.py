"""AgentTaskGraph：智能体协作图（有向无环图）注册制。

参见 docs/org.md §5。COO 根据需求生成任务流，各节点对应执行阶段。
注册表初始为空，由业务包在 import 或首次调用时注册。
"""

from __future__ import annotations

from typing import Any

_GRAPH_REGISTRY: dict[str, tuple[list[str], list[tuple[str, str]]]] = {}


def register_graph(
    scenario: str,
    nodes: list[str],
    edges: list[tuple[str, str]],
) -> None:
    """注册 scenario 的 DAG。由业务包调用。"""
    _GRAPH_REGISTRY[scenario] = (list(nodes), list(edges))


def get_graph(scenario: str) -> tuple[list[str], list[tuple[str, str]]]:
    """返回 (nodes, edges)。scenario 未注册则 raise ValueError。"""
    if scenario not in _GRAPH_REGISTRY:
        raise ValueError(f"scenario not registered: {scenario}")
    return _GRAPH_REGISTRY[scenario]


def topological_order(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """DAG 拓扑序，供 COO 按 §5 步骤 5、7 调度。"""
    from collections import deque

    in_deg: dict[str, int] = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for u, v in edges:
        if u in in_deg and v in in_deg:
            adj[u].append(v)
            in_deg[v] += 1
    q = deque(n for n in nodes if in_deg[n] == 0)
    order: list[str] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            in_deg[v] -= 1
            if in_deg[v] == 0:
                q.append(v)
    if len(order) != len(nodes):
        raise ValueError("graph has cycle or unknown nodes")
    return order


def describe_graph(scenario: str) -> dict[str, Any]:
    """返回图的元信息，供 COO 使用。"""
    nodes, edges = get_graph(scenario)
    return {
        "scenario": scenario,
        "nodes": nodes,
        "edges": edges,
        "topological_order": topological_order(nodes, edges),
        "is_dag": True,
    }
