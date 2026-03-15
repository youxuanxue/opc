import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type {
  DecisionItem,
  GraphView,
  OpcItem,
  ReleaseCandidate,
  ReleaseManifest,
  RunItem,
} from "./types";

type Tab = "workbench" | "planning" | "publishing" | "decisionRisk";
type CompletionFeedback = {
  title: string;
  detail: string;
  metricHint?: string;
};

type NextBestAction = {
  title: string;
  desc: string;
  cta: string;
  rationale: string;
  expectedResult: string;
  onClick: () => void;
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
};

type NodeExecStatus = "pending" | "running" | "succeed" | "failed" | "cancelled" | "timeout";

type NodePoint = {
  x: number;
  y: number;
  level: number;
};

function normalizeNodeStatus(value: unknown): NodeExecStatus | null {
  const raw = String(value || "").toLowerCase();
  if (
    raw === "pending" ||
    raw === "running" ||
    raw === "succeed" ||
    raw === "failed" ||
    raw === "cancelled" ||
    raw === "timeout"
  ) {
    return raw;
  }
  return null;
}

function buildNodeStatusMapFromTimeline(timeline: Array<Record<string, unknown>>): Record<string, NodeExecStatus> {
  const next: Record<string, NodeExecStatus> = {};
  for (const item of timeline) {
    const node = String(item.node ?? "");
    const status = normalizeNodeStatus(item.status);
    if (!node || !status) continue;
    next[node] = status;
  }
  return next;
}

function findFailedNodeFromTimeline(timeline: Array<Record<string, unknown>>): string {
  const failed = timeline.find((item) => String(item.status || "") === "failed");
  return String(failed?.node ?? "");
}

function buildNodeLayout(nodes: string[], edges: [string, string][]): Record<string, NodePoint> {
  const indegree: Record<string, number> = {};
  const parents: Record<string, string[]> = {};
  for (const node of nodes) {
    indegree[node] = 0;
    parents[node] = [];
  }
  for (const [from, to] of edges) {
    if (!(from in indegree) || !(to in indegree)) continue;
    indegree[to] += 1;
    parents[to].push(from);
  }

  const level: Record<string, number> = {};
  const queue = nodes.filter((n) => indegree[n] === 0);
  for (const n of queue) level[n] = 0;

  const children: Record<string, string[]> = {};
  for (const node of nodes) children[node] = [];
  for (const [from, to] of edges) {
    if (!(from in children)) continue;
    children[from].push(to);
  }

  while (queue.length > 0) {
    const current = queue.shift() as string;
    const base = level[current] ?? 0;
    for (const child of children[current] ?? []) {
      level[child] = Math.max(level[child] ?? 0, base + 1);
      indegree[child] -= 1;
      if (indegree[child] === 0) queue.push(child);
    }
  }

  const byLevel: Record<number, string[]> = {};
  for (const node of nodes) {
    const lv = level[node] ?? 0;
    if (!byLevel[lv]) byLevel[lv] = [];
    byLevel[lv].push(node);
  }

  const layout: Record<string, NodePoint> = {};
  const xGap = 190;
  const yGap = 88;
  const xBase = 40;
  const yBase = 52;
  for (const [lvRaw, list] of Object.entries(byLevel)) {
    const lv = Number(lvRaw);
    list.forEach((node, idx) => {
      layout[node] = {
        level: lv,
        x: xBase + lv * xGap,
        y: yBase + idx * yGap,
      };
    });
  }
  return layout;
}

export function App() {
  const [tab, setTab] = useState<Tab>("workbench");
  const [theme, setTheme] = useState<Record<string, string>>({});
  const [opcs, setOpcs] = useState<OpcItem[]>([]);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [decisions, setDecisions] = useState<DecisionItem[]>([]);
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [selectedOpc, setSelectedOpc] = useState<string>("");
  const [scenario, setScenario] = useState<string>("");
  const [appConfig, setAppConfig] = useState<{
    default_opc_create?: { opc_id: string; name: string; template: string };
    default_scenario_id?: string;
  }>({});
  const [message, setMessage] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<CompletionFeedback | null>(null);
  const [selectedTicketId, setSelectedTicketId] = useState<string>("");
  const [selectedDecisionRunDetail, setSelectedDecisionRunDetail] = useState<Record<string, unknown> | null>(null);
  const [streamLogs, setStreamLogs] = useState<string[]>([]);
  const [errorLogs, setErrorLogs] = useState<Array<Record<string, unknown>>>([]);
  const [releaseRunId, setReleaseRunId] = useState<string>("");
  const [releaseManifest, setReleaseManifest] = useState<ReleaseManifest | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<ReleaseCandidate | null>(null);
  const [selectedCandidateContent, setSelectedCandidateContent] = useState<string>("");
  const [showPublishEvidence, setShowPublishEvidence] = useState<boolean>(false);
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [nodeStatusMap, setNodeStatusMap] = useState<Record<string, NodeExecStatus>>({});
  const [currentNode, setCurrentNode] = useState<string>("");
  const [streamDisconnected, setStreamDisconnected] = useState<boolean>(false);
  const [showRetryAdvanced, setShowRetryAdvanced] = useState<boolean>(false);
  const [retryFromNodeInput, setRetryFromNodeInput] = useState<string>("");
  const [runDiagnostics, setRunDiagnostics] = useState<Array<{ node?: string; day?: number; human_message?: string; fix_hint?: string }>>([]);
  const [planningRunId, setPlanningRunId] = useState<string>("");
  const [planningManifest, setPlanningManifest] = useState<ReleaseManifest | null>(null);
  const [publishRiskFilter, setPublishRiskFilter] = useState<string>("all");
  const streamRef = useRef<EventSource | null>(null);

  const [objective, setObjective] = useState("");
  const [refs, setRefs] = useState("");
  const [topicDaysInput, setTopicDaysInput] = useState<string>("");
  const [sourceDataDir, setSourceDataDir] = useState("");
  const [targetAccount, setTargetAccount] = useState("");

  const cssVars = useMemo(
    () =>
      ({
        "--primary": theme.primary ?? "#0F4C81",
        "--success": theme.success ?? "#2CB67D",
        "--warning": theme.warning ?? "#F59E0B",
        "--danger": theme.danger ?? "#E11D48",
      }) as Record<string, string>,
    [theme]
  );

  const activeRun = useMemo(
    () => runs.find((r) => r.run_id === activeRunId) ?? runs.find((r) => r.status === "running") ?? null,
    [runs, activeRunId]
  );

  const executionGraph = useMemo(() => {
    const nodes = graph?.nodes ?? [];
    const edges = graph?.edges ?? [];
    const layout = buildNodeLayout(nodes, edges);
    const maxLevel = Math.max(0, ...Object.values(layout).map((p) => p.level));
    const maxY = Math.max(0, ...Object.values(layout).map((p) => p.y));
    return {
      nodes,
      edges,
      layout,
      width: 220 + maxLevel * 190,
      height: Math.max(140, maxY + 70),
    };
  }, [graph]);

  const completedNodeCount = useMemo(
    () => Object.values(nodeStatusMap).filter((s) => s === "succeed").length,
    [nodeStatusMap]
  );

  function upsertNodeStatus(node: string, status: NodeExecStatus) {
    setNodeStatusMap((prev) => ({ ...prev, [node]: status }));
  }

  function attachRunStream(runId: string) {
    if (!runId) return;
    if (streamRef.current) {
      streamRef.current.close();
      streamRef.current = null;
    }
    setActiveRunId(runId);
    setStreamDisconnected(false);
    const es = new EventSource(`/api/run/stream?run_id=${encodeURIComponent(runId)}`);
    streamRef.current = es;

    es.addEventListener("progress", (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as {
        run_id: string;
        status: string;
        latest?: { node?: string; status?: string; error?: string };
      };
      setMessage(`运行中: ${data.run_id}`);
      setStreamLogs((prev) => {
        const line = `[${new Date().toLocaleTimeString()}] ${data.latest?.node ?? "node"} ${data.latest?.status ?? data.status}${
          data.latest?.error ? ` | ${data.latest.error}` : ""
        }`;
        return [...prev.slice(-19), line];
      });
      const status = normalizeNodeStatus(data.latest?.status);
      const node = String(data.latest?.node ?? "");
      if (node && status) {
        upsertNodeStatus(node, status);
        setCurrentNode(node);
      }
    });

    es.addEventListener("done", (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as {
        run_id?: string;
        status?: string;
        timeline?: Array<Record<string, unknown>>;
      };
      const doneRunId = data.run_id ?? runId;
      const timeline = data.timeline ?? [];
      if (timeline.length > 0) {
        setNodeStatusMap(buildNodeStatusMapFromTimeline(timeline));
        const latest = [...timeline]
          .reverse()
          .find((x) => typeof x.node === "string" && normalizeNodeStatus(x.status));
        setCurrentNode(String(latest?.node ?? ""));
        const failedNode = findFailedNodeFromTimeline(timeline);
        if (failedNode) {
          setRetryFromNodeInput(failedNode);
        }
      }
      if (data.status === "succeed") {
        setMessage(`运行成功: ${doneRunId}`);
        setStreamLogs((prev) => [...prev.slice(-19), `[${new Date().toLocaleTimeString()}] run ${doneRunId} succeed`]);
        setFeedback({
          title: "已完成：内容执行批次创建",
          detail: "本周内容链路已进入自动执行队列。",
          metricHint: "经营回报：本周计划完成度 +1",
        });
      } else {
        setMessage(`运行失败: ${doneRunId}`);
      }
      void refreshAll();
      es.close();
      if (streamRef.current === es) {
        streamRef.current = null;
      }
    });

    es.onerror = () => {
      setStreamDisconnected(true);
      setMessage(`运行状态流中断: ${runId}`);
      setStreamLogs((prev) => [...prev.slice(-19), `[${new Date().toLocaleTimeString()}] stream disconnected: ${runId}`]);
      es.close();
      if (streamRef.current === es) {
        streamRef.current = null;
      }
    };
  }

  async function refreshAll() {
    setLoading(true);
    try {
      const [t, o, r, d] = await Promise.all([
        api.theme(),
        api.opcs(),
        api.runs(selectedOpc || undefined),
        api.decisions(selectedOpc || undefined),
      ]);
      const errors = await api.errorLogs(20);
      setTheme(t);
      setOpcs(o);
      if (!selectedOpc && o.length > 0) {
        setSelectedOpc(o[0].opc_id);
      }
      setRuns(r);
      setDecisions(d);
      if (selectedOpc) {
        const effScenario = scenario || appConfig.default_scenario_id;
        if (effScenario) {
          const g = await api.graph(selectedOpc, effScenario);
          setGraph(g);
        }
      } else {
        setGraph(null);
      }
      setErrorLogs(errors);
      if (!feedback) {
        setFeedback({
          title: "今日经营状态已同步",
          detail: `当前待审批 ${d.filter((x) => x.status !== "approved").length} 项，最近执行批次 ${r[0]?.run_id ?? "暂无"}`,
          metricHint: "先完成“唯一主动作”，再处理次优先任务。",
        });
      }
    } catch (err) {
      setMessage(String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const cfg = await api.config();
        setAppConfig(cfg);
        if (!scenario && cfg.default_scenario_id) {
          setScenario(cfg.default_scenario_id);
        }
      } catch {
        // keep defaults on error
      }
    })();
  }, []);

  useEffect(() => {
    void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOpc, scenario]);

  useEffect(() => {
    const effectiveScenario = scenario || appConfig.default_scenario_id || "weekly-topic-batch";
    if (!effectiveScenario) return;
    void (async () => {
      try {
        const defaults = await api.planningDefaults(effectiveScenario, selectedOpc || undefined);
        // #region agent log
        try {
          fetch("http://127.0.0.1:7609/ingest/3d4d7740-3689-4d56-9984-7c27de7dd5ad", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "be1443" },
            body: JSON.stringify({
              sessionId: "be1443",
              hypothesisId: "H4,H5",
              location: "App.tsx.planningDefaults",
              message: "frontend_received",
              data: { objective: String(defaults?.objective ?? "").slice(0, 120), selectedOpc },
              timestamp: Date.now(),
            }),
          }).catch(() => {});
        } catch (_) {}
        // #endregion
        setObjective(String(defaults.objective ?? "").trim());
        setRefs(
          Array.isArray(defaults.reference_accounts)
            ? defaults.reference_accounts.map(String).join(", ")
            : String(defaults.reference_accounts ?? "")
        );
        const td = defaults.topic_days;
        setTopicDaysInput(
          td != null && Number.isFinite(td) ? String(Math.trunc(Number(td))) : ""
        );
        setSourceDataDir(String(defaults.source_data_dir ?? "").trim());
        setTargetAccount(String(defaults.target_account ?? "").trim());
      } catch {
        // keep current values on error
      }
    })();
  }, [selectedOpc, scenario, appConfig.default_scenario_id]);

  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.close();
        streamRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const pending = decisions.filter((d) => d.status !== "approved");
    if (!selectedTicketId && pending.length > 0) {
      setSelectedTicketId(pending[0].ticket_id);
    }
    if (selectedTicketId && !decisions.some((d) => d.ticket_id === selectedTicketId)) {
      setSelectedTicketId(pending[0]?.ticket_id ?? "");
    }
  }, [decisions, selectedTicketId]);

  useEffect(() => {
    const completed = runs.filter((r) => r.status === "succeed");
    if (!releaseRunId && completed.length > 0) {
      setReleaseRunId(completed[0].run_id);
      return;
    }
    if (releaseRunId && !completed.some((r) => r.run_id === releaseRunId)) {
      setReleaseRunId(completed[0]?.run_id ?? "");
    }
  }, [runs, releaseRunId]);

  useEffect(() => {
    if (!releaseRunId) {
      setReleaseManifest(null);
      setSelectedCandidate(null);
      return;
    }
    void loadReleaseManifest(releaseRunId);
  }, [releaseRunId]);

  useEffect(() => {
    if (tab !== "planning") return;
    const completed = runs.filter((r) => r.status === "succeed");
    if (!planningRunId && completed.length > 0) {
      setPlanningRunId(completed[0].run_id);
      return;
    }
    if (planningRunId && !completed.some((r) => r.run_id === planningRunId)) {
      setPlanningRunId(completed[0]?.run_id ?? "");
    }
  }, [tab, runs, planningRunId]);

  useEffect(() => {
    if (!planningRunId || tab !== "planning") {
      setPlanningManifest(null);
      return;
    }
    void (async () => {
      try {
        const manifest = await api.releaseManifest(planningRunId);
        setPlanningManifest(manifest);
      } catch {
        setPlanningManifest(null);
      }
    })();
  }, [planningRunId, tab]);

  useEffect(() => {
    const running = runs.find((r) => r.status === "running");
    if (running && running.run_id !== activeRunId) {
      setActiveRunId(running.run_id);
    }
    if (!running && activeRunId && !runs.some((r) => r.run_id === activeRunId)) {
      setActiveRunId("");
      setCurrentNode("");
      setNodeStatusMap({});
    }
  }, [runs, activeRunId]);

  const reconnectRetriesRef = useRef(0);

  useEffect(() => {
    if (!activeRunId) return;
    reconnectRetriesRef.current = 0;
    void (async () => {
      try {
        const detail = await api.run(activeRunId);
        const timeline = Array.isArray(detail.timeline) ? (detail.timeline as Array<Record<string, unknown>>) : [];
        if (timeline.length > 0) {
          setNodeStatusMap(buildNodeStatusMapFromTimeline(timeline));
          const latest = [...timeline]
            .reverse()
            .find((x) => typeof x.node === "string" && normalizeNodeStatus(x.status));
          setCurrentNode(String(latest?.node ?? ""));
        }
        const diags = Array.isArray(detail.diagnostics) ? detail.diagnostics : [];
        setRunDiagnostics(diags);
        if (detail.status === "running" && !streamRef.current) {
          attachRunStream(activeRunId);
        }
      } catch {
        setRunDiagnostics([]);
        // keep UI resilient: if detail fetch fails, card still renders with graph defaults
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRunId]);

  // When stream disconnects, retry attach after delay if run is still active (avoids reconnect loop via EventSource)
  const MAX_RECONNECT_RETRIES = 5;
  useEffect(() => {
    if (!streamDisconnected || !activeRunId) return;
    const delay = Math.min(2000 + reconnectRetriesRef.current * 1000, 8000);
    const t = setTimeout(() => {
      void (async () => {
        try {
          const detail = await api.run(activeRunId);
          if (detail.status === "running" && !streamRef.current) {
            reconnectRetriesRef.current = Math.min(reconnectRetriesRef.current + 1, MAX_RECONNECT_RETRIES);
            if (reconnectRetriesRef.current < MAX_RECONNECT_RETRIES) {
              setStreamDisconnected(false);
              attachRunStream(activeRunId);
            }
          }
        } catch {
          // keep disconnected
        }
      })();
    }, delay);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamDisconnected, activeRunId]);

  async function loadPlanningManifest(runId: string) {
    try {
      const manifest = await api.releaseManifest(runId);
      setPlanningManifest(manifest);
    } catch {
      setPlanningManifest(null);
    }
  }

  async function loadReleaseManifest(runId: string) {
    try {
      const manifest = await api.releaseManifest(runId);
      setReleaseManifest(manifest);
      const first = (manifest.items || []).flatMap((x) => x.candidates || [])[0] ?? null;
      setSelectedCandidate(first);
      if (first) {
        const detail = await api.releaseCandidateContent(runId, first.candidate_id);
        setSelectedCandidateContent(detail.content);
      } else {
        setSelectedCandidateContent("");
      }
    } catch {
      setReleaseManifest(null);
      setSelectedCandidate(null);
      setSelectedCandidateContent("");
    }
  }

  async function selectCandidate(candidate: ReleaseCandidate) {
    setSelectedCandidate(candidate);
    if (!releaseRunId) return;
    try {
      const detail = await api.releaseCandidateContent(releaseRunId, candidate.candidate_id);
      setSelectedCandidateContent(detail.content);
    } catch (err) {
      setSelectedCandidateContent("");
      setMessage(String(err));
    }
  }

  useEffect(() => {
    const d = decisions.find((x) => x.ticket_id === selectedTicketId);
    if (!d) {
      setSelectedDecisionRunDetail(null);
      return;
    }
    void (async () => {
      try {
        const detail = await api.run(d.run_id);
        setSelectedDecisionRunDetail(detail);
      } catch {
        setSelectedDecisionRunDetail(null);
      }
    })();
  }, [decisions, selectedTicketId]);

  async function runScenario() {
    if (!selectedOpc) {
      setMessage("请先创建 OPC，再执行“立即生成”。");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const inputs: Record<string, unknown> = {
        objective,
        reference_accounts: refs
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
        target_account: targetAccount || undefined,
        source_data_dir: sourceDataDir,
      };
      const topicDays = Number(topicDaysInput);
      if (Number.isFinite(topicDays) && topicDaysInput.trim() !== "") {
        inputs.topic_days = Math.min(14, Math.max(1, Math.trunc(topicDays)));
      }
      const payload = await api.startScenario({
        opc_id: selectedOpc,
        scenario_id: (scenario || appConfig.default_scenario_id) ?? "",
        execute_integrations: false,
        inputs,
      });
      const runPayload = payload as {
        run_id?: string;
      };
      const runId = runPayload.run_id ?? "-";
      setActiveRunId(runId);
      setCurrentNode("");
      setNodeStatusMap({});
      setStreamDisconnected(false);
      setMessage(`运行中: ${runId}`);
      setStreamLogs([`[${new Date().toLocaleTimeString()}] run ${runId} started`]);
      setFeedback({
        title: "执行进行中",
        detail: "系统正在逐节点执行，请查看实时状态。",
      });
      attachRunStream(runId);
    } catch (err) {
      setMessage(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function retryFailedRun(targetRunId: string, explicitFromNode?: string) {
    if (!targetRunId) return;
    setLoading(true);
    setMessage("");
    try {
      const runDetail = await api.run(targetRunId);
      const timeline = Array.isArray(runDetail.timeline) ? (runDetail.timeline as Array<Record<string, unknown>>) : [];
      const detectedFailedNode = findFailedNodeFromTimeline(timeline);
      const fromNode = (explicitFromNode || detectedFailedNode || "").trim();
      const payload = await api.retryScenario({
        run_id: targetRunId,
        from_node: fromNode || undefined,
        execute_integrations: false,
      });
      const runId = String((payload as { run_id?: string }).run_id ?? "");
      if (!runId) {
        throw new Error("重试启动失败：未返回 run_id");
      }
      setActiveRunId(runId);
      setCurrentNode("");
      setNodeStatusMap({});
      setStreamDisconnected(false);
      setMessage(`恢复执行中: ${runId}`);
      setStreamLogs((prev) => [
        ...prev.slice(-18),
        `[${new Date().toLocaleTimeString()}] retry ${targetRunId} -> ${runId} from ${fromNode || "failed-node"}`,
      ]);
      setFeedback({
        title: "已恢复执行",
        detail: `从 ${fromNode || "失败节点"} 继续执行，系统正在推进后续节点。`,
        metricHint: "经营回报：避免整条链路重跑，缩短恢复时长。",
      });
      attachRunStream(runId);
      await refreshAll();
    } catch (err) {
      setMessage(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function createDefaultOpc() {
    const cfg = appConfig.default_opc_create;
    if (!cfg) {
      setMessage("配置加载中，请稍候重试");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const payload = await api.createOpc({
        opc_id: cfg.opc_id,
        name: cfg.name,
        template: cfg.template,
      });
      setSelectedOpc(String((payload as { opc_id?: string }).opc_id ?? cfg.opc_id));
      setFeedback({
        title: "已完成：初始化 OPC",
        detail: `已创建 ${cfg.name}，可以继续生成本周内容计划。`,
      });
      await refreshAll();
    } catch (err) {
      setMessage(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function batchSaveToDraftbox(runId: string, candidateIds: string[]) {
    if (!runId || candidateIds.length === 0) return;
    setLoading(true);
    setMessage("");
    try {
      let saved = 0;
      for (const cid of candidateIds) {
        try {
          const response = await api.saveCandidateToDraftbox(runId, cid);
          setReleaseManifest(response.manifest);
          saved += 1;
        } catch {
          setMessage(`入草稿箱部分失败：${cid}`);
        }
      }
      if (saved > 0) {
        setMessage(`批量入草稿箱完成：${saved}/${candidateIds.length} 篇`);
        setFeedback({
          title: "批量入草稿箱完成",
          detail: `已成功将 ${saved} 篇稿件入草稿箱。`,
          metricHint: "经营回报：可发布任务减少",
        });
      }
      await refreshAll();
    } catch (err) {
      setMessage(String(err));
    } finally {
      setLoading(false);
    }
  }

  async function approve(ticketId: string, option: string) {
    try {
      await api.approveDecision(ticketId, option);
      setMessage(`已审批 ${ticketId}`);
      setFeedback({
        title: "已完成：关键审批",
        detail: "风险决策已下发给 COO，发布链路可继续。",
        metricHint: "经营回报：待审批数 -1",
      });
      await refreshAll();
    } catch (err) {
      setMessage(String(err));
    }
  }

  async function saveToDraftbox(runId: string, candidateId: string) {
    try {
      const response = await api.saveCandidateToDraftbox(runId, candidateId);
      const updated = response.manifest;
      const publishResult = response.publish_result;
      setReleaseManifest(updated);
      const updatedCandidate =
        updated.items.flatMap((x) => x.candidates || []).find((x) => x.candidate_id === candidateId) ??
        null;
      setSelectedCandidate(updatedCandidate);
      if (updatedCandidate) {
        const detail = await api.releaseCandidateContent(runId, updatedCandidate.candidate_id);
        setSelectedCandidateContent(detail.content);
      }
      const status = String((publishResult.last_job as { status?: string } | undefined)?.status ?? "failed");
      if (status === "draft_saved") {
        setMessage(`已入草稿箱: ${candidateId}`);
        setFeedback({
          title: "已完成：入草稿箱",
          detail: "copublisher 已执行成功，稿件已进入公众号草稿箱。",
          metricHint: "经营回报：今日可发布任务 -1",
        });
      } else {
        setMessage(`入草稿箱失败: ${candidateId}`);
        setFeedback({
          title: "入草稿箱失败",
          detail: "copublisher 执行失败，请查看错误日志与 publish_result。",
          metricHint: "建议先修复命令或账号配置后重试。",
        });
      }
      await refreshAll();
    } catch (err) {
      setMessage(String(err));
    }
  }

  const pendingDecisions = decisions.filter((d) => d.status !== "approved");
  const publishableRuns = runs.filter((r) => r.status === "succeed");
  const riskRuns = runs.filter((r) => (r.ai_tone_score ?? 0) >= 0.7);
  const doneRuns = runs.filter((r) => r.status === "succeed");
  const failedRuns = runs.filter((r) => r.status === "failed" || r.status === "cancelled" || r.status === "timeout");
  const failedRunForRetry =
    (activeRun && (activeRun.status === "failed" || activeRun.status === "cancelled" || activeRun.status === "timeout")
      ? activeRun
      : null) ?? failedRuns[0] ?? null;

  const selectedDecision = decisions.find((d) => d.ticket_id === selectedTicketId);
  const releaseItems = releaseManifest?.items ?? [];
  const filteredReleaseItems =
    publishRiskFilter === "all"
      ? releaseItems
      : releaseItems
          .map((item) => ({
            ...item,
            candidates: (item.candidates ?? []).filter(
              (c) => (c.quality?.risk_level ?? "unknown") === publishRiskFilter
            ),
          }))
          .filter((item) => item.candidates.length > 0);
  const releaseCandidates = releaseItems.flatMap((x) => x.candidates ?? []);
  const batchEligibleCandidates = releaseCandidates.filter(
    (c) =>
      c.publish_status === "ready" &&
      c.quality?.compliance_passed === true &&
      c.quality?.risk_level !== "high"
  );
  const releaseSummary = {
    plannedDays: releaseItems.filter((x) => typeof x.day === "number").length,
    candidates: releaseCandidates.length,
    ready: releaseCandidates.filter((x) => x.publish_status === "ready").length,
    draftSaved: releaseCandidates.filter((x) => x.publish_status === "draft_saved").length,
  };

  const executionState: "idle" | "running" | "failed" | "succeed" = (() => {
    if (!activeRun) return "idle";
    if (activeRun.status === "running") return "running";
    if (activeRun.status === "failed" || activeRun.status === "cancelled" || activeRun.status === "timeout") {
      return "failed";
    }
    if (activeRun.status === "succeed") return "succeed";
    return "idle";
  })();

  const currentNodeLabel = currentNode
    ? executionState === "running"
      ? `${graph?.business_labels?.[currentNode] ?? currentNode} 进行中`
      : `${graph?.business_labels?.[currentNode] ?? currentNode}（${currentNode}）`
    : "等待节点开始";

  const executionHeadline =
    executionState === "idle"
      ? "当前无进行中的执行批次"
      : `${activeRun?.run_id ?? "-"} · ${
          executionState === "running" ? "进行中" : executionState === "failed" ? "执行失败" : "已完成"
        }（${completedNodeCount}/${executionGraph.nodes.length || 0}）`;

  const nextBestAction: NextBestAction = (() => {
    if (!selectedOpc || opcs.length === 0) {
      return {
        title: "先初始化你的 OPC",
        desc: `系统尚未发现可执行主体，先创建 ${appConfig.default_opc_create?.name ?? "OPC"} 才能开始全链路。`,
        cta: "创建 OPC 并开始",
        rationale: "当前阶段先完成基础搭建，避免后续动作无效。",
        expectedResult: "创建后将自动进入计划生成阶段。",
        onClick: () => void createDefaultOpc(),
      };
    }
    if (executionState === "running") {
      return {
        title: "专注当前执行批次",
        desc: `批次 ${activeRun?.run_id ?? "-"} 正在运行，优先跟踪当前节点进度。`,
        cta: "查看当前进度",
        rationale: "进行中阶段保持单一目标，避免重复触发新批次。",
        expectedResult: "你将看到节点进展与异常，便于及时干预。",
        onClick: () => setTab("decisionRisk"),
      };
    }
    if (pendingDecisions.length > 0) {
      const ticket = pendingDecisions[0];
      return {
        title: "审批今日关键项",
        desc: `当前有 ${pendingDecisions.length} 条待审批，先清理最高优先级阻塞。`,
        cta: "审批最高优先项",
        rationale: "审批是发布链路继续推进的前置条件。",
        expectedResult: "审批完成后，链路将继续流向发布准备。",
        onClick: () => void approve(ticket.ticket_id, "accept_and_publish"),
        secondaryAction: {
          label: "进入决策与风险",
          onClick: () => setTab("decisionRisk"),
        },
      };
    }
    if (failedRunForRetry) {
      return {
        title: "恢复失败批次（优先）",
        desc: `检测到失败批次 ${failedRunForRetry.run_id}，建议从失败节点继续执行。`,
        cta: "从失败节点继续",
        rationale: "局部恢复比整链重跑更快，且能保留已有成果。",
        expectedResult: "系统会从失败节点续跑并实时更新全景图。",
        onClick: () => void retryFailedRun(failedRunForRetry.run_id, retryFromNodeInput),
        secondaryAction: {
          label: showRetryAdvanced ? "隐藏高级重试" : "显示高级重试",
          onClick: () => setShowRetryAdvanced((s) => !s),
        },
      };
    }
    if (publishableRuns.length > 0) {
      return {
        title: "处理发布候选稿",
        desc: "已有成功批次可发布，进入发布中心完成入草稿箱动作。",
        cta: "进入发布中心",
        rationale: "当前最短路径是把已完成内容转化为可发布资产。",
        expectedResult: "完成后可发布任务会持续减少，推进当日目标。",
        onClick: () => setTab("publishing"),
      };
    }
    if (doneRuns.length > 0) {
      return {
        title: "查看复盘并开启下一周",
        desc: "本轮执行已完成，建议先看复盘再生成新一周计划。",
        cta: "查看复盘并开启下一周",
        rationale: "先复盘再开新批次，能持续提升内容质量与成功率。",
        expectedResult: "你将带着上轮经验进入下轮计划。",
        onClick: () => setTab("decisionRisk"),
        secondaryAction: {
          label: "进入内容计划（高级配置）",
          onClick: () => setTab("planning"),
        },
      };
    }
    return {
      title: "生成本周内容计划",
      desc: "当前没有进行中或可发布批次，先生成本周内容排期。",
      cta: "生成本周计划",
      rationale: "先创建可执行批次，后续审批、发布和复盘才有对象。",
      expectedResult: "系统会立即启动执行并展示实时节点进度。",
      onClick: () => void runScenario(),
      secondaryAction: {
        label: "进入内容计划（高级配置）",
        onClick: () => setTab("planning"),
      },
    };
  })();

  return (
    <div className="layout" style={cssVars}>
      <header className="hero">
        <h1>OPC 今日工作台</h1>
        <p>30 秒看懂状态，3 分钟完成关键动作。</p>
      </header>

      <section className="toolbar">
        <label>
          OPC
          <select value={selectedOpc} onChange={(e) => setSelectedOpc(e.target.value)}>
            {opcs.length === 0 && <option value="">暂无 OPC</option>}
            {opcs.map((o) => (
              <option key={o.opc_id} value={o.opc_id}>
                {o.opc_id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Scenario
          <input
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
            disabled={!selectedOpc}
          />
        </label>
        <button onClick={() => void refreshAll()} disabled={loading}>
          Refresh
        </button>
      </section>

      <nav className="tabs">
        {(
          [
            ["workbench", "今日工作台"],
            ["planning", "内容计划"],
            ["publishing", "发布中心"],
            ["decisionRisk", "决策与风险"],
          ] as Array<[Tab, string]>
        ).map(([t, label]) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {label}
          </button>
        ))}
      </nav>

      {message && <div className="message">{message}</div>}
      {feedback && tab !== "workbench" && (
        <section className="card feedback-card">
          <h3>{feedback.title}</h3>
          <p>{feedback.detail}</p>
          {feedback.metricHint && <strong>{feedback.metricHint}</strong>}
        </section>
      )}

      {tab === "workbench" && (
        <>
          <section className="metrics-strip">
            <article className="metric-card">
              <h4>今日待办</h4>
              <p>{Math.max(pendingDecisions.length + publishableRuns.length, 1)}</p>
            </article>
            <article className="metric-card danger">
              <h4>待你审批</h4>
              <p>{pendingDecisions.length}</p>
            </article>
            <article className="metric-card success">
              <h4>今日可发布</h4>
              <p>{publishableRuns.length}</p>
            </article>
            <article className="metric-card warning">
              <h4>风险告警</h4>
              <p>{riskRuns.length}</p>
            </article>
          </section>

          <section className="card execution-overview-card">
            <div className="execution-overview-head">
              <div>
                <h3>执行全景图</h3>
                <p>{executionHeadline}</p>
              </div>
              <button className="primary-cta" onClick={nextBestAction.onClick} disabled={loading}>
                {nextBestAction.cta}
              </button>
            </div>
            <p className="execution-overview-node">当前节点：{currentNodeLabel}</p>
            {executionState === "failed" && (
              <div className="execution-retry-panel">
                <p>系统建议：从失败节点继续执行，避免整条链路重跑。</p>
                {runDiagnostics.length > 0 && (
                  <div className="execution-diagnostics">
                    {runDiagnostics.map((d, i) => (
                      <div key={i}>
                        <strong>{d.node ?? "未知"} {d.day != null ? `Day ${d.day}` : ""}:</strong> {d.human_message ?? ""}
                        {d.fix_hint && <p className="fix-hint">建议：{d.fix_hint}</p>}
                      </div>
                    ))}
                  </div>
                )}
                <button onClick={() => setShowRetryAdvanced((s) => !s)}>
                  {showRetryAdvanced ? "隐藏高级重试" : "显示高级重试"}
                </button>
                {showRetryAdvanced && (
                  <div className="execution-retry-advanced">
                    <label>
                      从节点开始（可选）
                      <input
                        value={retryFromNodeInput}
                        placeholder="默认自动定位失败节点"
                        onChange={(e) => setRetryFromNodeInput(e.target.value)}
                      />
                    </label>
                    <button
                      disabled={loading || !(activeRun?.run_id || failedRunForRetry?.run_id)}
                      onClick={() =>
                        void retryFailedRun(
                          activeRun?.run_id || failedRunForRetry?.run_id || "",
                          retryFromNodeInput
                        )
                      }
                    >
                      启动恢复执行
                    </button>
                  </div>
                )}
              </div>
            )}
            {streamDisconnected && <p className="execution-warning">状态连接中断，正在重连</p>}
            {executionGraph.nodes.length === 0 ? (
              <p>当前无可展示流程图。请先创建 OPC 并选择业务场景。</p>
            ) : (
              <div className="execution-graph-wrap">
                <svg width={executionGraph.width} height={executionGraph.height}>
                  {executionGraph.edges.map(([from, to]) => {
                    const fromPoint = executionGraph.layout[from];
                    const toPoint = executionGraph.layout[to];
                    if (!fromPoint || !toPoint) return null;
                    const fromStatus = nodeStatusMap[from] ?? "pending";
                    const edgeDone =
                      fromStatus === "succeed" || fromStatus === "running" || fromStatus === "failed";
                    return (
                      <line
                        key={`${from}-${to}`}
                        x1={fromPoint.x + 66}
                        y1={fromPoint.y}
                        x2={toPoint.x - 66}
                        y2={toPoint.y}
                        className={edgeDone ? "exec-edge done" : "exec-edge"}
                      />
                    );
                  })}
                  {executionGraph.nodes.map((node) => {
                    const point = executionGraph.layout[node];
                    if (!point) return null;
                    const status = nodeStatusMap[node] ?? "pending";
                    const isCurrent = node === currentNode;
                    const label = graph?.business_labels?.[node] ?? node;
                    return (
                      <g key={node}>
                        <rect
                          x={point.x - 66}
                          y={point.y - 18}
                          width={132}
                          height={36}
                          rx={10}
                          className={`exec-node status-${status}${isCurrent ? " current" : ""}`}
                        />
                        <text x={point.x} y={point.y + 5} textAnchor="middle" className="exec-node-label">
                          {label}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            )}
          </section>

          <section className="card primary-action-card">
            <h3>{nextBestAction.title}</h3>
            <p>{nextBestAction.desc}</p>
            <p><strong>为什么现在做：</strong>{nextBestAction.rationale}</p>
            <p><strong>预期结果：</strong>{nextBestAction.expectedResult}</p>
            {nextBestAction.secondaryAction && (
              <button onClick={nextBestAction.secondaryAction.onClick} disabled={loading}>
                {nextBestAction.secondaryAction.label}
              </button>
            )}
          </section>

          <section className="card">
            <h3>实时执行日志</h3>
            {streamLogs.length === 0 ? (
              <p>暂无进行中的执行日志。触发顶部主动作后会实时显示节点进度。</p>
            ) : (
              <pre>{streamLogs.join("\n")}</pre>
            )}
          </section>
        </>
      )}

      {tab === "planning" && (
        <>
          {planningManifest && planningManifest.items && planningManifest.items.length > 0 && (
            <section className="card">
              <h3>本周日历</h3>
              <div className="planning-toolbar">
                <label>
                  批次
                  <select
                    value={planningRunId}
                    onChange={(e) => setPlanningRunId(e.target.value)}
                  >
                    {runs.filter((r) => r.status === "succeed").map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        {r.run_id}
                      </option>
                    ))}
                    {runs.filter((r) => r.status === "succeed").length === 0 && (
                      <option value="">暂无成功批次</option>
                    )}
                  </select>
                </label>
              </div>
              <div className="calendar-grid">
                {planningManifest.items.map((item) => {
                  const c = (item.candidates ?? [])[0];
                  const status = c?.publish_status ?? "暂无";
                  const statusLabel = status === "draft_saved" ? "已入稿" : status === "ready" ? "待入稿" : status;
                  return (
                    <article key={item.topic_id ?? item.day} className="calendar-day-card">
                      <h4>Day {item.day ?? "-"}</h4>
                      <p className="calendar-topic">{item.topic ?? "-"}</p>
                      <small className="calendar-angle">{item.angle ?? ""}</small>
                      <span className={`pill calendar-status ${status}`}>{statusLabel}</span>
                    </article>
                  );
                })}
              </div>
            </section>
          )}
          <section className="card form-card">
            <h3>生成本周计划</h3>
            <label>
              Objective
            <textarea value={objective} onChange={(e) => setObjective(e.target.value)} />
          </label>
          <label>
            Reference Accounts (comma separated)
            <input value={refs} onChange={(e) => setRefs(e.target.value)} />
          </label>
          <label>
            Topic Days
            <input
              type="number"
              min={1}
              max={14}
              value={topicDaysInput}
              placeholder="留空使用后端默认值"
              onChange={(e) => setTopicDaysInput(e.target.value)}
            />
          </label>
          <label>
            Source Data Dir
            <input value={sourceDataDir} onChange={(e) => setSourceDataDir(e.target.value)} />
          </label>
          <button className="primary-cta" onClick={() => void runScenario()} disabled={loading}>
            生成本周计划
          </button>
        </section>
        </>
      )}

      {tab === "publishing" && (
        <>
          <section className="card">
            <h3>发布工作台</h3>
            <div className="publish-toolbar">
              <label>
                执行批次
                <select value={releaseRunId} onChange={(e) => setReleaseRunId(e.target.value)}>
                  {runs
                    .filter((r) => r.status === "succeed")
                    .map((r) => (
                      <option key={r.run_id} value={r.run_id}>
                        {r.run_id}
                      </option>
                    ))}
                  {runs.filter((r) => r.status === "succeed").length === 0 && (
                    <option value="">暂无成功批次</option>
                  )}
                </select>
              </label>
              <button onClick={() => releaseRunId && void loadReleaseManifest(releaseRunId)} disabled={!releaseRunId}>
                刷新发布视图
              </button>
              {releaseManifest && (
                <>
                  <span className="publish-filter-label">按风险:</span>
                  {(["all", "low", "medium", "high"] as const).map((v) => (
                    <button
                      key={v}
                      className={publishRiskFilter === v ? "active" : ""}
                      onClick={() => setPublishRiskFilter(v)}
                    >
                      {v === "all" ? "全部" : v === "low" ? "低" : v === "medium" ? "中" : "高"}
                    </button>
                  ))}
                  {batchEligibleCandidates.length > 0 && (
                    <button
                      className="primary-cta"
                      disabled={loading}
                      onClick={() =>
                        void batchSaveToDraftbox(
                          releaseManifest.run_id,
                          batchEligibleCandidates.map((c) => c.candidate_id).filter(Boolean)
                        )
                      }
                    >
                      批量入草稿箱 ({batchEligibleCandidates.length})
                    </button>
                  )}
                </>
              )}
            </div>
            {!releaseManifest ? (
              <p>当前批次暂无发布清单。请先完成一次成功运行，系统会自动生成 `release_manifest.json`。</p>
            ) : (
              <>
                <section className="metrics-strip publish-metrics">
                  <article className="metric-card">
                    <h4>计划天数</h4>
                    <p>{releaseSummary.plannedDays}</p>
                  </article>
                  <article className="metric-card">
                    <h4>候选稿数</h4>
                    <p>{releaseSummary.candidates}</p>
                  </article>
                  <article className="metric-card warning">
                    <h4>待入草稿箱</h4>
                    <p>{releaseSummary.ready}</p>
                  </article>
                  <article className="metric-card success">
                    <h4>已入草稿箱</h4>
                    <p>{releaseSummary.draftSaved}</p>
                  </article>
                </section>

                <table>
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th>选题</th>
                      <th>候选稿</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredReleaseItems.map((item) => (
                      <tr key={item.topic_id}>
                        <td>{item.day ?? "-"}</td>
                        <td>
                          <div>{item.topic ?? "-"}</div>
                          <small>{item.angle ?? ""}</small>
                        </td>
                        <td>
                          {(item.candidates ?? []).length === 0
                            ? "暂无"
                            : (item.candidates ?? []).map((c) => (
                                <button
                                  key={c.candidate_id}
                                  className={selectedCandidate?.candidate_id === c.candidate_id ? "active" : ""}
                                  onClick={() => void selectCandidate(c)}
                                >
                                  {c.title ?? c.candidate_id}
                                </button>
                              ))}
                        </td>
                        <td>
                          {(item.candidates ?? []).map((c) => (
                            <span key={c.candidate_id} className="pill">
                              {c.publish_status ?? "unknown"}
                            </span>
                          ))}
                        </td>
                        <td>
                          {(item.candidates ?? []).map((c) => {
                            const canPublish =
                              c.publish_status === "ready" &&
                              c.quality?.compliance_passed === true &&
                              c.quality?.risk_level !== "high";
                            const alreadySaved = c.publish_status === "draft_saved";
                            let label = "已入草稿箱";
                            if (!alreadySaved) {
                              if (!c.quality?.compliance_passed) label = "合规未通过";
                              else if (c.quality?.risk_level === "high") label = "需先审批";
                              else label = "入草稿箱";
                            }
                            return (
                              <button
                                key={c.candidate_id}
                                className="primary-cta"
                                disabled={alreadySaved || !canPublish}
                                onClick={() => void saveToDraftbox(releaseManifest.run_id, c.candidate_id)}
                              >
                                {label}
                              </button>
                            );
                          })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="card">
            <h3>候选稿详情</h3>
            {!selectedCandidate ? (
              <p>选择候选稿后，可查看质量结果与证据链。</p>
            ) : (
              <div className="decision-review">
                <div className="decision-main">
                  <p><strong>Candidate:</strong> {selectedCandidate.candidate_id}</p>
                  <p><strong>状态:</strong> {selectedCandidate.publish_status ?? "-"}</p>
                  <p><strong>AI 味分数:</strong> {selectedCandidate.quality?.ai_tone_score ?? "-"}</p>
                  <p><strong>风险等级:</strong> {selectedCandidate.quality?.risk_level ?? "-"}</p>
                  <p><strong>合规:</strong> {selectedCandidate.quality?.compliance_passed ? "通过" : "未通过/未知"}</p>
                  <p><strong>发布稿件:</strong> {selectedCandidate.publish_target_artifact ?? "-"}</p>
                  <h4>文章内容</h4>
                  <pre>{selectedCandidateContent || "该候选稿暂无可预览内容"}</pre>
                </div>
                <div className="decision-evidence">
                  <button onClick={() => setShowPublishEvidence((s) => !s)}>
                    {showPublishEvidence ? "隐藏高级证据" : "显示高级证据"}
                  </button>
                  {showPublishEvidence ? (
                    <pre>{JSON.stringify(selectedCandidate.evidence_refs ?? [], null, 2)}</pre>
                  ) : (
                    <p>高级证据默认折叠，减少决策噪音。</p>
                  )}
                </div>
              </div>
            )}
          </section>
        </>
      )}

      {tab === "decisionRisk" && (
        <>
          <section className="card">
            <h3>待审批内容（先看再批）</h3>
            {!selectedDecision ? (
              <p>暂无待审批项。你可以先在“内容计划”创建新的执行批次。</p>
            ) : (
              <div className="decision-review">
                <div className="decision-main">
                  <p><strong>Ticket:</strong> {selectedDecision.ticket_id}</p>
                  <p><strong>Run:</strong> {selectedDecision.run_id}</p>
                  <p><strong>原因:</strong> {selectedDecision.reason ?? "-"}</p>
                  <p><strong>说明:</strong> {selectedDecision.summary ?? "-"}</p>
                  <p><strong>AI 分数:</strong> {selectedDecision.ai_tone_score ?? "-"}</p>
                  <p><strong>推荐方案:</strong> {selectedDecision.recommended_option ?? "-"}</p>
                  <div className="decision-actions">
                    {(selectedDecision.options ?? []).map((opt) => (
                      <button
                        key={opt}
                        className={opt === selectedDecision.recommended_option ? "primary-cta" : ""}
                        onClick={() => void approve(selectedDecision.ticket_id, opt)}
                      >
                        审批：{opt}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="decision-evidence">
                  <h4>证据摘要</h4>
                  <pre>{JSON.stringify(selectedDecision.evidence_refs ?? [], null, 2)}</pre>
                  <h4>执行摘要</h4>
                  <pre>{JSON.stringify(selectedDecisionRunDetail?.timeline ?? [], null, 2)}</pre>
                </div>
              </div>
            )}
          </section>

          <section className="card">
            <h3>决策与风险</h3>
            <table>
              <thead>
                <tr>
                  <th>Ticket</th>
                  <th>Reason</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d) => (
                  <tr key={d.ticket_id}>
                    <td>{d.ticket_id}</td>
                    <td>{d.reason ?? "-"}</td>
                    <td>{d.status}</td>
                    <td>
                      <button onClick={() => setSelectedTicketId(d.ticket_id)}>
                        查看内容
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}

      {tab === "planning" && (
        <section className="card">
          <h3>OPC 概览</h3>
          <table>
            <thead>
              <tr>
                <th>OPC ID</th>
                <th>Name</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {opcs.map((o) => (
                <tr key={o.opc_id}>
                  <td>{o.opc_id}</td>
                  <td>{o.name}</td>
                  <td>{o.created_at ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

