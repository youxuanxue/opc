# OPC 平台产品设计与实现文档

> 「简单可以比复杂更难。你必须努力让你的思维清晰到足以做到简单。」 — 乔布斯
>
> 以乔布斯产品标准审视：克制、聚焦、默认优于配置、细节即品牌。

---

## 1. 产品北极星（乔布斯视角）

### 1.1 五条原则

| 原则 | 含义 | 检验问题 |
|------|------|----------|
| **一键结果** | 用户输入意图与约束，系统输出可发布物 | 用户是否必须理解编排细节才能完成目标？ |
| **单一真相源** | 图、提示、契约、运行策略、观测全部由 scenario spec 声明 | 是否有隐式逻辑藏在引擎代码中？ |
| **无假边** | 每条边承载显式、可校验的 payload；字段映射在 spec 中声明 | 上游输出能否直接对应下游 input_contract？ |
| **默认可靠** | contract-first 执行、确定性诊断、可重放 run | 失败时能否回答：在哪、为何、如何修/重试？ |
| **隐藏复杂度** | 用户看到稳定结果与 actionable 错误 | 用户是否被迫接触 orchestration 内部实现？ |

### 1.2 我们说「不」的事（聚焦）

- **不**增加「双视图切换」「节点大量 hover 指标」「预计剩余时间」等噪音
- **不**让用户跨页面找证据；待审批项点击即见证据链
- **不**使用同义异名（如 article_markdown vs article_text）增加心智负担
- **不**在引擎中硬编码字段映射（benchmark_summary ← summary 等）
- **不**让 topic_days 等关键默认值在 spec、模板、示例中不一致

---

## 2. 模板与平台对照分析

### 2.1 数据来源

| 类型 | 路径 |
|------|------|
| 模板 | `opc_platform/templates/weekly-topic-batch.v2.json` |
| 平台 | `opc_platform/runtime/executor.py`、`contracts/validators.py`、`domain/templates.py` |
| 规范 | 见模板 `node_specs`、`input_contract` |

### 2.2 当前实现亮点（已达标）

1. **边级 required_payload**：每条边声明 `required_payload`，执行前校验上游结果（`_validate_edge_required_payload`）。
2. **Contract-first**：`validate_output_contract` 支持 `required_top_level`、`outputs_item_required`、`quality_checks_required_fields`、`value_range`。
3. **7 步生命周期**：resolve input → map edge → render prompt → execute → validate → persist → route。
4. **release_manifest.json**：每次 run 生成，支持发布中心以「发布对象」而非「文件」为中心。
5. **gate + failure_policy**：AIToneRewriterAgent 的 gate、continue_with_guard 已实现。
6. **内部节点**：`internal_source_collect`、`internal_publish` 与 LLM 节点分离，职责清晰。

### 2.3 乔布斯式问题（模板与平台 Gaps）

#### A. 隐式字段映射（违反「单一真相源」）

| 上游输出 | 下游 input_contract 声明 | 实际解析来源 | 问题 |
|----------|---------------------------|--------------|------|
| BenchmarkAgent.summary | TopicBatchPlannerAgent: benchmark_summary | executor L770-771 注入 merged_inputs | 引擎 `_pick_from_upstream` L196-205 特判 |
| BenchmarkAgent.outputs | TopicBatchPlannerAgent: benchmark_outputs | 同上 | 同上 |
| DraftWriterAgent.article_markdown | EditorAgent: article_text | executor L813 从 article_draft_day{N}.md 读入 day_context | 映射未在 spec 声明 |

**根因**：模板使用语义化别名（benchmark_summary）而上游输出规范字段（summary）；引擎通过 `_pick_from_upstream` 硬编码映射补全。

#### B. release_manifest 与产品计划不一致

**产品计划定义**：`candidates[].quality` 含 `compliance_passed`、`ai_tone_score`、`risk_level`；含 `evidence_refs[]`。

**当前实现**（`_build_release_manifest` L1141-1182）：`quality` 仅含 `ai_tone_score`、`risk_level`；无 `compliance_passed`；无 `evidence_refs`。

**影响**：发布中心 UI（`web/src/App.tsx` L1126、L1136）已预留展示 `compliance_passed`、`evidence_refs`，但后端未填充，导致「未通过/未知」或空数组。

#### C. topic_days 默认值（已统一）

| 位置 | 值 | 说明 |
|------|-----|------|
| 模板 defaults.topic_days | 7 | weekly-topic-batch.v2.json |
| inputs_schema.default | 7 | 模板 inputs_schema |
| executor 回退 | 7 | executor.py L338 |

已统一为 7（周计划语义）。

#### D. 业务标签缺失

模板节点全为技术命名（SourceCollectAgent、BenchmarkAgent 等）。产品计划要求「流程与 Agent 页」以业务标签展示（选题、成稿、发布），默认折叠技术细节。

**改进**：在 node_specs 中增加可选 `business_label`。

#### E. 错误体验未完全 action-oriented

spec 的 UX 契约：错误应 action-oriented（「Day 3 缺少 angle，点击重试并应用建议补丁」）。当前 `fail()` 多返回通用 error，部分路径未写入 `diagnostics`，缺少 `fix_hint`、`sample` 的结构化注入。

**改进**：每个 `fail(node, error)` 调用前，先 `diagnostics.append(build_diagnostic(...))`；UI 展示「按建议重试」。

---

## 3. 模板设计改进建议

### 3.1 显式字段映射（消除 engine 特判）

TopicBatchPlannerAgent 的 `input_contract` 改为显式 map：

```json
"input_contract": {
  "from_globals": ["objective", "topic_days"],
  "from_upstream": [
    {"node": "BenchmarkAgent", "map": {"summary": "benchmark_summary", "outputs": "benchmark_outputs"}}
  ]
}
```

**平台**：`_resolve_contract_context` 已支持 `{node, map}`（executor L250-272）。迁移后，可移除 `_pick_from_upstream` 中对 benchmark_summary/metrics_summary/compliance_summary 的特判（或保留向后兼容，新 spec 优先 map）。

### 3.2 统一 article 字段语义

DraftWriterAgent 输出 `article_markdown`；EditorAgent 输入 `article_text`。

**推荐**：EditorAgent 的 input_contract 改为 `article_markdown`；day_context 由运行时从 DraftWriterAgent 的 outputs[].article_markdown 或 article_draft_day{N}.md 注入，字段名统一。

### 3.3 增加 business_label（可选）

```json
"SourceCollectAgent": {"business_label": "素材收集"},
"BenchmarkAgent": {"business_label": "对标分析"},
"TopicBatchPlannerAgent": {"business_label": "选题规划"},
"DraftWriterAgent": {"business_label": "初稿撰写"},
"EditorAgent": {"business_label": "编辑润色"},
"ComplianceAgent": {"business_label": "合规校验"},
"AIToneDetectorAgent": {"business_label": "AI 味检测"},
"AIToneRewriterAgent": {"business_label": "AI 味改写"},
"PublisherAgent": {"business_label": "发布"},
"MetricsAgent": {"business_label": "指标汇总"},
"RetroAgent": {"business_label": "复盘"}
```

### 3.4 统一 topic_days 默认值

```json
"defaults": {"topic_days": 7, "ai_tone": {"hard_threshold": 0.7, "soft_threshold": 0.5}},
"inputs_schema": {
  "properties": {
    "topic_days": {"type": "integer", "minimum": 1, "maximum": 14, "default": 7}
  }
}
```

---

## 4. 平台实现改进建议

### 4.1 release_manifest 补齐字段

在 `_build_release_manifest` 的 `quality()` 内，为每个 day 增加：

- `compliance_passed`：从 `ComplianceAgent.day{N}.json` 的 `result.outputs[0].passed` 读取；若无该产物则 `null`。
- `evidence_refs`：`[f"ComplianceAgent.day{day}.json", f"ai_tone_report_day{day}.json"]`（或对应实际路径）。

### 4.2 失败路径统一写入 diagnostics

所有 `fail(node, error)` 调用前，执行：

```python
diagnostics.append(build_diagnostic(
    node=node, day=day_if_per_day,
    reason_code="...",
    human_message=error,
    fix_hint="..." if can_infer else None,
    sample="..." if can_provide else None,
))
```

确保 UI 能展示「按建议重试」。

### 4.3 编译期 placeholder 校验

在 load scenario 时（如 `ScenarioSpec.from_dict` 或独立 compiler）：

- `required_placeholders` 的变量必须在 context_block/task_block 中出现；
- prompt 中使用的 `{xxx}` 必须在 `allowed_placeholders` 中；
- 未知 placeholder → 编译错误，避免运行时才发现。

---

## 5. 产品信息架构（CEO 视角）

原则：**先经营结果，后过程细节；先动作按钮，后技术数据。**

### 5.1 顶层导航（4 项）

1. **今日工作台**（默认首页）
2. **内容计划**（本周选题与草稿）
3. **发布中心**（草稿箱、发布队列、发布结果）
4. **决策与风险**（待审批、异常、建议）

「流程与 Agent」作为二级入口，收纳于「决策与风险」内；默认以 `business_label` 展示，技术节点名折叠。

### 5.2 首页（今日工作台）布局

- **第一层**：经营状态条（待办、待审批、可发布、风险）
- **第二层**：今日三件事（行动卡），仅一个 Primary CTA 高亮
- **第三层**：结果与趋势（发布完成率、阅读/收藏/转发、AI 味风险命中）

### 5.3 发布中心：以 release_manifest 为唯一数据源

- 主列表按 DayItem 展示：Day、选题、候选稿状态、质量门禁（合规、AI 味）、发布状态
- 右侧详情抽屉：证据链（topic → draft → edit → compliance → ai-tone → final）
- 仅对 `publish_status=ready` 且 `compliance_passed=true` 显示「入草稿箱」；`risk_level=high` 时需先审批
- 高级模式：默认折叠，展示原始 Agent 产物路径

### 5.4 执行全景图（简化版）

- 卡片内仅三块：`run-xxx · 进行中（3/10）`、完整 DAG + 当前节点高亮、**唯一主按钮**（动态）
- 主按钮规则：运行中→查看当前节点；失败→查看失败原因；成功→进入发布中心；无运行→立即生成本周计划
- **不**增加：双视图切换、节点大量 hover 指标、预计剩余时间

---

## 6. release_manifest 规范（与实现对齐）

### 6.1 目标结构

```json
{
  "run_id": "run-xxx",
  "opc_id": "...",
  "scenario_id": "weekly-topic-batch",
  "target_account": "职场螺丝刀",
  "generated_at": "...",
  "items": [
    {
      "day": 1,
      "topic_id": "run-xxx/day-1",
      "topic": "...",
      "angle": "...",
      "candidates": [
        {
          "candidate_id": "run-xxx-day-1-main",
          "draft_artifact": "...",
          "edited_artifact": "...",
          "humanized_artifact": "...",
          "publish_target_artifact": "...",
          "quality": {
            "compliance_passed": true,
            "ai_tone_score": 0.42,
            "risk_level": "low"
          },
          "publish_status": "ready",
          "evidence_refs": ["ComplianceAgent.day1.json", "ai_tone_report_day1.json"]
        }
      ]
    }
  ]
}
```

### 6.2 实现检查清单

- [x] `quality.compliance_passed` 从 ComplianceAgent 产物读取
- [x] `evidence_refs` 列出合规与 AI 味证据路径
- [x] 发布中心 UI 只读此结构，不拼接杂散文件；入草稿箱仅对 compliance_passed=true 且 risk_level≠high 可用

---

## 7. 研发任务拆解（按优先级）

### 第一轮（必须）

| 序号 | 任务 | 负责 |
|------|------|------|
| 1 | release_manifest 补齐 compliance_passed、evidence_refs | executor |
| 2 | 模板 TopicBatchPlannerAgent 改为 from_upstream map；移除 _pick_from_upstream 特判 | spec + executor |
| 3 | 统一 article_markdown / article_text，EditorAgent 输入改为 article_markdown | template + executor |
| 4 | 首页改造成今日工作台，指标卡首屏化 | web |
| 5 | 顶层导航收敛到 4 项，技术视图收纳为二级 | web |

### 第二轮（增强）

| 序号 | 任务 | 负责 |
|------|------|------|
| 6 | node_specs 增加 business_label，流程页以业务标签展示 | template + web |
| 7 | 失败路径统一写入 diagnostics，UI 展示 fix_hint / 按建议重试 | executor + web |
| 8 | 编译期 placeholder 校验 | specs/compiler |
| 9 | topic_days 默认值统一为 7 | template |

### 第三轮（可选）✅ 已完成

| 序号 | 任务 | 负责 | 状态 |
|------|------|------|------|
| 10 | 内容计划页本周日历视图 | web | ✅ |
| 11 | 发布中心批量入草稿箱、按风险过滤 | web | ✅ |
| 12 | 执行全景图当前节点业务解释文案映射 | web | ✅ |

---

## 8. 验收标准（乔布斯式）

### 8.1 产品体验

1. 新用户 30 秒内能说出「我现在要做什么」
2. 每日关键动作（审批 + 发布）3 分钟内完成
3. 待审批项点击后不需要跨页面找证据
4. 首屏主按钮点击率 > 70%
5. 单次任务链路页面跳转步数 ≤ 3

### 8.2 技术与数据

1. 100% 边有真实 payload 消费，可追溯
2. 100% prompt 由 spec 驱动，无业务 prompt 硬编码
3. 任意失败可回答：在哪、为何、如何修/重试
4. release_manifest 为发布中心唯一主数据源，结构符合 6.1
5. 无隐式字段映射，from_upstream 的 map 在 spec 中显式声明

### 8.3 工程约束（不变）

- 禁止模块级副作用
- 分层依赖单向
- 外部输入路径安全校验（拒绝 `..`、`/`）
- HTTP 请求体大小限制
- 状态文件原子写（temp + rename）

---

## 9. 附录：weekly-topic-batch 流程概览

```
SourceCollectAgent → BenchmarkAgent → TopicBatchPlannerAgent → DraftWriterAgent
    → EditorAgent → ComplianceAgent → AIToneDetectorAgent → [AIToneRewriterAgent]
    → PublisherAgent → MetricsAgent → RetroAgent
```

- **AIToneRewriterAgent**：gate `ai_tone_score >= soft_threshold`；未命中则跳过。
- **发布前**：必须 `compliance_day_passed = true`。
- **终稿选择**：`humanized_day_{d}` 优先，否则 `edited_day_{d}`，否则阻断发布。
