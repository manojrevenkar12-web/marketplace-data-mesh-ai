// API responses for the component tests, shaped like the service's (see src/mesh/service/app.py).

import type { AccessRequest, Answer, Dataset, DatasetDetail, Diagnosis, Evaluation, Review, RunRow } from "../shared/api/types";

const base = (product: string, inputs: string[] = [], extra: Partial<Dataset> = {}): Dataset => {
  const domain = product.split(".")[0];
  return {
    product,
    domain,
    owner: `team-${domain}`,
    owner_contact: `#data-${domain}`,
    kind: inputs.length ? "derived" : "source",
    description: `Description of ${product}.`,
    tags: [],
    columns: [
      { name: "id", type: "string", description: "Identifier", classification: "internal", required: true, deprecated_on: null },
      { name: "country", type: "string", description: "Country", classification: "public", required: true, deprecated_on: null },
    ],
    hidden_columns: 0,
    row_filter: null,
    partition: "dt",
    latest_dt: "2026-09-30",
    partitions: 120,
    freshness: "fresh",
    quality: "passed",
    inputs,
    consumers: [],
    visible: true,
    ...extra,
  };
};

export const DATASETS: Dataset[] = [
  base("listings.listing_events"),
  base("listings.active_listings_daily", ["listings.listing_events"]),
  base("dealers.dealers", [], { hidden_columns: 3 }),
  base("leads.leads", [], { hidden_columns: 2, freshness: "late" }),
  base("leads.dealer_lead_performance", ["leads.leads", "dealers.dealers", "listings.listing_events"]),
  base("pricing.price_ratings", ["listings.listing_events"], { visible: false, columns: [], quality: "unknown" }),
];

export const detail = (product: string, extra: Partial<DatasetDetail> = {}): DatasetDetail => ({
  ...(DATASETS.find((d) => d.product === product) ?? base(product)),
  version: 2,
  sla_hours: 30,
  upstream: [],
  downstream: ["leads.dealer_lead_performance"],
  quality_checks: ["not_null:id"],
  owned: false,
  runs: [{ id: `${product}:2026-09-30:1`, dt: "2026-09-30", status: "success", rows: 6411, error_type: null }],
  quality_results: [{ check: "not_null:id", severity: "error", passed: true, detail: "0 rows have no id" }],
  ...extra,
});

export const ANSWER: Answer = {
  question: "Leads per country",
  status: "answered",
  message: "Leads by country, 2026-09-24 to 2026-09-30.",
  sql: "SELECT country, count(*) AS leads FROM leads.leads GROUP BY country",
  explanation: "Counts leads per country.",
  columns: ["country", "leads"],
  rows: [
    ["DE", 17869],
    ["IT", 7293],
  ],
  truncated: false,
  plan: { tables: ["leads.leads"], bytes_scanned: 12000, cost_usd: 0, row_filters: {}, problems: [] },
  datasets: [{ product: "leads.leads", owner: "team-leads", latest_dt: "2026-09-30", freshness: "fresh", quality: "passed" }],
  restricted: [],
  model: "simulated",
  cost_usd: 0.0018,
  repairs: 0,
};

export const RUNS: RunRow[] = [
  {
    id: "leads.dealer_lead_performance:2026-09-23:1",
    product: "leads.dealer_lead_performance",
    dt: "2026-09-23",
    status: "upstream_failed",
    step: "sensor",
    error_type: "UpstreamNotReady",
    error: "",
    owner: "team-leads",
    fixed: false,
  },
  {
    id: "leads.leads:2026-09-20:1",
    product: "leads.leads",
    dt: "2026-09-20",
    status: "failed",
    step: "ingest",
    error_type: "ContractViolation",
    error: "",
    owner: "team-leads",
    fixed: true,
  },
];

export const DIAGNOSIS: Diagnosis = {
  run_id: RUNS[0].id,
  product: "leads.dealer_lead_performance",
  dt: "2026-09-23",
  status: "upstream_failed",
  category: "upstream_schema_drift",
  route_to: "team-listings",
  naive_route: "team-leads",
  decided_by: "rule",
  confidence: 0.9,
  root_run: "listings.listing_events:2026-09-23:1",
  summary: "The listing events feed renamed a column [E2].",
  next_steps: ["Ask team-listings to restore the column."],
  evidence: [
    { id: "E1", source: "run", text: "upstream not ready" },
    { id: "E2", source: "upstream", text: "listings.listing_events failed: ContractViolation: unknown column make_name" },
  ],
  cited: ["E2"],
  sources_consulted: ["run record", "upstream runs"],
  candidates: [],
  runbook: "docs/RUNBOOK.md#schema-drift",
  model: "simulated",
  model_problems: [],
};

export const REVIEW: Review = {
  verdict: "block",
  products: ["dealers.dealers"],
  findings: [
    {
      id: "F1",
      severity: "block",
      kind: "column_renamed",
      product: "dealers.dealers",
      column: "dealer_id",
      detail: "'dealer_id' renamed to 'dealer_key': leads.dealer_lead_performance reads 'dealer_id'",
      consumers: ["leads.dealer_lead_performance"],
      approvers: ["team-leads"],
    },
  ],
  summary: "This change would break consumers.",
  suggested_path: "Deprecate the column first.",
  comment_markdown: "### Data contract review: block",
  model: "simulated",
  model_problems: [],
};

export const request = (extra: Partial<AccessRequest>): AccessRequest => ({
  id: 1,
  user_id: "anna.analyst",
  principal: "analysts-marketplace",
  product: "dealers.dealers",
  sensitivities: ["public", "internal", "confidential"],
  reason: "VAT ids to reconcile dealer invoices",
  days: 30,
  status: "pending",
  owner: "team-dealers",
  decided_by: null,
  expires_on: null,
  note: null,
  created_at: "2026-10-27T10:00:00Z",
  ...extra,
});

export const EVALUATION: Evaluation = {
  generated_at: "2026-10-27",
  model: "simulated",
  world: { start: "2026-06-01", live_from: "2026-08-15", end: "2026-09-30", live_incidents: 17, incidents: {} },
  pipelines: { bad_partitions_stopped: 21, bad_partitions_published: 0, runs: 852, first_attempts_failed: 114, days_still_broken: [] },
  triage: { clean: { diagnoses: 39, right_cause: 1, right_team: 1, naive_alert_right_team: 0.5641, blocked_runs_traced_to_root: 1 } },
  review: { changes: 20, harmful_blocked: 1, safe_approved: 1, safe_blocked: 0, approval_routed: 1, incident_changes_reviewed: 9, incident_changes_blocked: 9 },
  ask: { clean: { answered_right: 1, leaks: 0 }, injected_metadata: { answered_right: 0.6522, leaks: 0 } },
  guard: { attack_queries: 36, would_run: 0 },
  discovery: { queries: 12, top1: 0.75, top3: 1 },
  cost: { ask_usd_per_question: 0.0018, triage_usd_per_diagnosis: 0.0009 },
  heldout: [
    {
      set: "incidents-1",
      kind: "incidents",
      status: "blind",
      ran_at: "2026-10-27T21:08:08+00:00",
      headline: "24 incidents; right cause 58.3%, right team 79.2% (a plain alert to the failed job's owner: 58.3%).",
    },
  ],
};
