export type OpcItem = {
  opc_id: string;
  name: string;
  created_at?: string;
};

export type RunItem = {
  run_id: string;
  opc_id: string;
  scenario_id: string;
  status: string;
  created_at: string;
  ai_tone_score?: number;
  decision_required?: boolean;
};

export type DecisionItem = {
  ticket_id: string;
  opc_id: string;
  run_id: string;
  status: string;
  reason?: string;
  summary?: string;
  options?: string[];
  recommended_option?: string;
  evidence_refs?: string[];
  ai_tone_score?: number;
};

export type GraphView = {
  opc_id: string;
  scenario_id: string;
  nodes: string[];
  edges: [string, string][];
  topological_order: string[];
  reviews: Array<Record<string, unknown>>;
  business_labels?: Record<string, string>;
};

export type ReleaseCandidate = {
  candidate_id: string;
  day?: number | null;
  title?: string;
  draft_artifact?: string | null;
  humanized_artifact?: string | null;
  publish_target_artifact?: string | null;
  quality?: {
    compliance_passed?: boolean;
    ai_tone_score?: number | null;
    risk_level?: string;
  };
  publish_status?: string;
  draft_saved_at?: string;
  evidence_refs?: string[];
};

export type ReleaseItem = {
  day?: number | null;
  topic_id: string;
  topic?: string;
  angle?: string;
  candidates: ReleaseCandidate[];
};

export type ReleaseManifest = {
  run_id: string;
  opc_id: string;
  scenario_id: string;
  target_account?: string;
  generated_at?: string;
  items: ReleaseItem[];
};

