import type { DecisionItem, GraphView, OpcItem, ReleaseManifest, RunItem } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    const txt = await r.text();
    let msg = txt || `HTTP ${r.status}`;
    try {
      const parsed = JSON.parse(txt) as { error?: string };
      if (parsed?.error) msg = parsed.error;
    } catch {
      /* use raw txt */
    }
    throw new Error(msg);
  }
  return (await r.json()) as T;
}

export const api = {
  theme: () => req<Record<string, string>>("/api/theme"),
  config: () =>
    req<{
      default_opc_create: { opc_id: string; name: string; template: string; account_preset?: string };
      default_scenario_id: string;
    }>("/api/config"),
  opcs: () => req<OpcItem[]>("/api/opcs"),
  presets: () => req<{ presets: Array<{ key: string; target_account: string; name: string }> }>("/api/opc/presets"),
  createOpc: (payload: { opc_id: string; name: string; template: string; account_preset?: string }) =>
    req<Record<string, unknown>>("/api/opc/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  opc: (id: string) => req<Record<string, unknown>>(`/api/opc/${encodeURIComponent(id)}`),
  runs: (opcId?: string) =>
    req<RunItem[]>(`/api/runs${opcId ? `?opc_id=${encodeURIComponent(opcId)}` : ""}`),
  errorLogs: (limit = 50) =>
    req<Array<Record<string, unknown>>>(`/api/logs/errors?limit=${encodeURIComponent(String(limit))}`),
  run: (runId: string) => req<Record<string, unknown>>(`/api/run/${encodeURIComponent(runId)}`),
  runArtifacts: (runId: string) =>
    req<Array<Record<string, unknown>>>(`/api/run/artifacts?run_id=${encodeURIComponent(runId)}`),
  runArtifactPreview: (runId: string, name: string) =>
    req<Record<string, unknown>>(
      `/api/run/artifact?run_id=${encodeURIComponent(runId)}&name=${encodeURIComponent(name)}`
    ),
  releaseManifest: (runId: string) =>
    req<ReleaseManifest>(`/api/run/release?run_id=${encodeURIComponent(runId)}`),
  releaseCandidateContent: (runId: string, candidateId: string) =>
    req<{ candidate_id: string; artifact_path: string; content: string; truncated: boolean }>(
      `/api/run/release/candidate-content?run_id=${encodeURIComponent(runId)}&candidate_id=${encodeURIComponent(candidateId)}`
    ),
  saveCandidateToDraftbox: (runId: string, candidateId: string) =>
    req<{ manifest: ReleaseManifest; publish_result: Record<string, unknown> }>(
      "/api/run/release/draftbox",
      {
        method: "POST",
        body: JSON.stringify({ run_id: runId, candidate_id: candidateId }),
      }
    ),
  decisions: (opcId?: string) =>
    req<DecisionItem[]>(
      `/api/decisions${opcId ? `?opc_id=${encodeURIComponent(opcId)}` : ""}`
    ),
  approveDecision: (ticketId: string, option: string) =>
    req<Record<string, unknown>>("/api/decision/approve", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId, option }),
    }),
  graph: (opcId: string, scenarioId: string) =>
    req<GraphView>(
      `/api/graph/view?opc_id=${encodeURIComponent(opcId)}&scenario_id=${encodeURIComponent(scenarioId)}`
    ),
  planningDefaults: (scenarioId: string, opcId?: string) =>
    req<{
      objective: string;
      reference_accounts: string[];
      topic_days: number;
      source_data_dir: string;
      target_account: string;
    }>(
      `/api/planning-defaults?scenario_id=${encodeURIComponent(scenarioId)}${opcId ? `&opc_id=${encodeURIComponent(opcId)}` : ""}`,
      { cache: "no-store" }
    ),
  reviewGraph: (payload: {
    opc_id: string;
    scenario_id: string;
    node: string;
    comment: string;
    review_type: string;
  }) =>
    req<Record<string, unknown>>("/api/graph/review", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  runScenario: (payload: {
    opc_id: string;
    scenario_id: string;
    inputs: Record<string, unknown>;
    execute_integrations: boolean;
  }) =>
    req<Record<string, unknown>>("/api/scenario/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startScenario: (payload: {
    opc_id: string;
    scenario_id: string;
    inputs: Record<string, unknown>;
    execute_integrations: boolean;
  }) =>
    req<Record<string, unknown>>("/api/scenario/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retryScenario: (payload: {
    run_id: string;
    from_node?: string;
    input_overrides?: Record<string, unknown>;
    execute_integrations: boolean;
  }) =>
    req<Record<string, unknown>>("/api/scenario/retry", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  triggerPublish: (runId: string) =>
    req<Record<string, unknown>>("/api/publish/trigger", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),
};

