"""AgentSpec：统一 Agent 接口规范，映射到 action package。

参见 docs/org.md §4、§4.1。action package 是 AgentSpec 的具象表示。
§4.1 Template 强约束为设计选项；当前通过 schema_guard 对 status/execution 做核心层校验，
符合 §4.1 折中（核心层强约束、扩展层代码约定）。

Constraints (§4)：CheckState 中任意检测结果不合法（failed / cancelled / timeout）时，
须取消所有 Skills、更新 TaskState，并将 I 回退给上一 Agent。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# org.md §4 Constraints：触发约束的 CheckState 取值
INVALID_CHECK_STATES = frozenset({"failed", "cancelled", "timeout", "blocked"})


def is_constraint_triggered(action: dict[str, Any]) -> bool:
    """CheckState 是否触发 AgentSpec 约束（应取消 Skills 并停止流转）。

    action.status 映射到 CheckState；blocked 视为 failed 语义。
    """
    status = str(action.get("status") or "").lower()
    return status in INVALID_CHECK_STATES


def should_cancel_skills_and_stop(action: dict[str, Any]) -> bool:
    """当 CheckState 不合法时，返回 True 表示应取消 Skills 并停止执行。

    供 COO / 业务层在节点间调用，确保符合 org.md §4 约束。
    """
    return is_constraint_triggered(action)


@dataclass
class AgentSpec:
    """AgentSpec 规范：Inputs / CheckRules / CheckState / Skills / TaskState / Outputs。

    action package 是 AgentSpec 的具象表示，字段映射由业务层约定，例如：
    - Inputs      ← source_signals, constraints, objective
    - CheckRules  ← governance (Policy + Risk), validation.acceptance_criteria
    - CheckState  ← status, approval, transitions
    - Skills      ← execution.owner_agent, execution.collaborators
    - TaskState   ← result, evidence_refs
    - Outputs     ← evidence_refs, 子 action 派发等
    """

    inputs: dict[str, Any]  # I₁..Iₖ：业务参数
    check_rules: list[str]  # R₁..Rₖ：校验规则（policy/risk/acceptance_criteria）
    check_state: str  # pending | running | succeed | failed | cancelled | timeout
    skills: list[str]  # S₁..Sₘ：owner_agent + collaborators
    task_state: dict[str, Any] | None  # T₁..Tₘ 执行结果
    outputs: list[dict[str, Any]]  # Oₙ → Aₙ 输出及下游

    @classmethod
    def from_action(cls, action: dict[str, Any]) -> "AgentSpec":
        """从 action package 反序列化为 AgentSpec。"""
        exec_ = action.get("execution") or {}
        skills = [str(exec_.get("owner_agent") or "")]
        skills.extend(str(c) for c in exec_.get("collaborators") or [])
        skills = [s for s in skills if s]

        validation = action.get("validation") or {}
        rules = list(validation.get("acceptance_criteria") or [])
        rules.extend(str(k) for k in (validation.get("kill_criteria") or []))

        status = str(action.get("status") or "proposed")
        check_state = "running" if status in ("approved", "running") else (
            "succeed" if status == "done" else (
                "failed" if status == "blocked" else (
                    "cancelled" if status == "cancelled" else (
                        "timeout" if status == "timeout" else "pending"
                    )
                )
            )
        )

        outputs: list[dict[str, Any]] = []
        for ref in (action.get("result") or {}).get("evidence_refs") or []:
            outputs.append({"path": ref, "downstream": None})

        return cls(
            inputs={
                "source_signals": action.get("source_signals"),
                "constraints": action.get("constraints"),
                "objective": action.get("objective"),
            },
            check_rules=[str(r) for r in rules],
            check_state=check_state,
            skills=skills,
            task_state=action.get("result"),
            outputs=outputs,
        )

    def to_action_fragment(self) -> dict[str, Any]:
        """导出可用于 action 的字段子集（仅 skills 相关）。"""
        return {
            "execution": {
                "owner_agent": self.skills[0] if self.skills else "",
                "collaborators": self.skills[1:] if len(self.skills) > 1 else [],
            },
        }
