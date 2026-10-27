// The API's shapes, as the console uses them.

export interface Me {
  user: string;
  name: string;
  principal: string;
  owns: string[];
}

export interface Column {
  name: string;
  type: string;
  description: string;
  classification: "public" | "internal" | "confidential" | "pii";
  required: boolean;
  deprecated_on: string | null;
}

export interface Dataset {
  product: string;
  domain: string;
  owner: string;
  owner_contact: string;
  kind: "source" | "derived";
  description: string;
  tags: string[];
  columns: Column[];
  hidden_columns: number;
  row_filter: string | null;
  partition: string;
  latest_dt: string | null;
  partitions: number;
  freshness: "fresh" | "late" | "no data" | "unknown";
  quality: "passed" | "warnings" | "failed" | "not run" | "unknown";
  inputs: string[];
  consumers: string[];
  visible: boolean;
}

export interface DatasetDetail extends Dataset {
  version: number;
  sla_hours: number;
  upstream: string[];
  downstream: string[];
  quality_checks: string[];
  owned: boolean;
  runs?: { id: string; dt: string; status: string; rows: number; error_type: string | null }[];
  quality_results?: { check: string; severity: string; passed: boolean; detail: string }[];
}

export interface Catalog {
  today: string;
  datasets: Dataset[];
}

export interface Answer {
  question: string;
  status: "answered" | "denied" | "not_answerable" | "rejected" | "unavailable";
  message: string;
  sql: string;
  explanation: string;
  columns: string[];
  rows: (string | number | null)[][];
  truncated: boolean;
  plan: { tables?: string[]; bytes_scanned?: number; cost_usd?: number; row_filters?: Record<string, string>; problems?: string[] };
  datasets: { product: string; owner: string; latest_dt: string | null; freshness: string; quality: string }[];
  restricted: { product: string; owner: string; owner_contact: string }[];
  model: string | null;
  cost_usd: number;
  repairs: number;
}

export interface RunRow {
  id: string;
  product: string;
  dt: string;
  status: string;
  step: string;
  error_type: string | null;
  error: string;
  owner: string;
  fixed: boolean;
}

export interface Evidence {
  id: string;
  source: string;
  text: string;
}

export interface Candidate {
  category: string;
  source: "rule" | "classifier";
  confidence: number;
  why: string;
  evidence: string[];
  acts_on: string;
}

export interface Diagnosis {
  run_id: string;
  product: string;
  dt: string;
  status: string;
  category: string;
  route_to: string;
  naive_route: string;
  decided_by: string;
  confidence: number;
  root_run: string;
  summary: string;
  next_steps: string[];
  evidence: Evidence[];
  cited: string[];
  sources_consulted: string[];
  candidates: Candidate[];
  runbook: string;
  model: string | null;
  model_problems: string[];
}

export interface Finding {
  id: string;
  severity: "block" | "needs_approval" | "info";
  kind: string;
  product: string;
  column: string | null;
  detail: string;
  consumers: string[];
  approvers: string[];
}

export interface Review {
  verdict: "approve" | "needs_approval" | "block";
  products: string[];
  findings: Finding[];
  summary: string;
  suggested_path: string;
  comment_markdown: string;
  model: string | null;
  model_problems: string[];
}

export interface ContractFiles {
  product: string;
  files: { path: string; text: string }[];
}

export interface HeldoutRun {
  set: string;
  kind: string;
  status: string;
  ran_at: string;
  headline: string;
}

export interface Evaluation {
  generated_at: string;
  model: string;
  world: { start: string; live_from: string; end: string; live_incidents: number; incidents: Record<string, number> };
  pipelines: { bad_partitions_stopped: number; bad_partitions_published: number; runs: number; first_attempts_failed: number; days_still_broken: unknown[] };
  triage: {
    clean: { diagnoses: number; right_cause: number; right_team: number; naive_alert_right_team: number; blocked_runs_traced_to_root: number };
    faulty_model?: { right_cause: number; right_team: number; invented_citations_shown: number };
    model_down?: { right_cause: number; right_team: number };
  };
  review: { changes: number; harmful_blocked: number; safe_approved: number; safe_blocked: number; approval_routed: number; incident_changes_reviewed: number; incident_changes_blocked: number };
  ask: Record<string, { answered_right?: number; answered_wrong?: number; leaks?: number; answered_when_should_refuse?: number; refused_right?: number; not_answered?: number; questions?: number }>;
  guard: { attack_queries: number; would_run: number };
  discovery: { queries: number; top1: number; top3: number };
  cost: { ask_usd_per_question: number; triage_usd_per_diagnosis: number };
  heldout: HeldoutRun[];
}

export interface AccessRequest {
  id: number;
  user_id: string;
  principal: string;
  product: string;
  sensitivities: string[];
  reason: string;
  days: number;
  status: "pending" | "approved" | "denied";
  owner: string;
  decided_by: string | null;
  expires_on: string | null;
  note: string | null;
  created_at: string;
}
