// Why a run failed and who has to act, with the evidence it was decided on. Cited lines are marked; the
// model only explains a decision that rules or the classifier made, and an uncited claim is never shown.

import { motion } from "framer-motion";
import { ApiError } from "../../shared/api/client";
import { useDiagnosis, useFeedback } from "../../shared/api/hooks";
import type { Diagnosis } from "../../shared/api/types";
import { motion as m } from "../../shared/styles/theme";
import { label, pct } from "../../shared/ui/format";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";

const DECIDED: Record<string, string> = {
  rule: "a rule that matched the evidence",
  classifier: "the ticket classifier (no rule matched)",
  model: "the model, between candidates the evidence allowed",
  "rule (model disagreed)": "a rule; the model disagreed and was overruled",
  uncertain: "nothing decided it: a person has to look",
};

function Routing({ d }: { d: Diagnosis }) {
  const differs = d.route_to !== d.naive_route;
  return (
    <div className="diag-route">
      <div>
        <span className="diag-k">Send to</span>
        <strong className="diag-team">{d.route_to}</strong>
      </div>
      <div>
        <span className="diag-k">A plain failure alert would page</span>
        <span className={differs ? "diag-naive diag-naive-wrong" : "diag-naive"}>{d.naive_route}</span>
        {differs && <span className="chip warn">different team</span>}
      </div>
    </div>
  );
}

function Feedback({ d }: { d: Diagnosis }) {
  const fb = useFeedback();
  if (fb.isSuccess) return <p className="muted" role="status">Thanks, recorded.</p>;
  return (
    <div className="ask-feedback">
      <span className="muted">Was the diagnosis right?</span>
      <button type="button" className="button" disabled={fb.isPending} onClick={() => fb.mutate({ feature: "triage", ref: d.run_id, helpful: true })}>
        Yes
      </button>
      <button type="button" className="button" disabled={fb.isPending} onClick={() => fb.mutate({ feature: "triage", ref: d.run_id, helpful: false })}>
        No
      </button>
    </div>
  );
}

export function DiagnosisPanel({ runId }: { runId: string | null }) {
  const q = useDiagnosis(runId);
  if (!runId)
    return (
      <EmptyState title="Select a failed run">
        The diagnosis names the cause, the team that has to act, and the evidence it was decided on.
      </EmptyState>
    );
  if (q.isPending) return <Skeleton lines={8} label="Collecting evidence" />;
  if (q.isError)
    return q.error instanceof ApiError && q.error.status === 404 ? (
      <EmptyState title="Run not found">Run {runId} is not visible to you or no longer exists.</EmptyState>
    ) : (
      <ErrorState title="The diagnosis failed" error={q.error} onRetry={() => void q.refetch()} />
    );

  const d = q.data;
  const cited = new Set(d.cited);
  return (
    <motion.article
      key={d.run_id}
      className="panel diag"
      initial={{ opacity: 0, y: m.rise }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: m.base, ease: m.ease }}
      aria-labelledby="diag-title"
    >
      <header className="diag-head">
        <h2 id="diag-title">{label(d.category)}</h2>
        <p className="muted">
          {d.product} on {d.dt}. Decided by {DECIDED[d.decided_by] ?? d.decided_by}; confidence {pct(d.confidence)}.
        </p>
      </header>
      <Routing d={d} />
      {d.root_run && d.root_run !== d.run_id && (
        <p>
          This run was blocked by an upstream failure: <code>{d.root_run}</code>.
        </p>
      )}
      <p>{d.summary}</p>
      {d.next_steps.length > 0 && (
        <section>
          <h3>Next steps</h3>
          <ol className="diag-steps">
            {d.next_steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          {d.runbook && (
            <p className="muted">
              Runbook: <code>{d.runbook}</code>
            </p>
          )}
        </section>
      )}
      <section>
        <h3>Evidence</h3>
        {d.evidence.length === 0 ? (
          <p className="muted">No evidence was collected.</p>
        ) : (
          <ul className="diag-evidence">
            {d.evidence.map((e) => (
              <li key={e.id} className={cited.has(e.id) ? "cited" : undefined}>
                <span className="diag-ev-id">
                  {e.id}
                  {cited.has(e.id) && <span className="visually-hidden"> (cited)</span>}
                </span>
                <span className="diag-ev-src">{e.source}</span>
                <span className="diag-ev-text">{e.text}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="muted">
          Marked lines are cited by the summary. Sources consulted: {d.sources_consulted.join(", ") || "none"}.
          {d.model_problems.length > 0 && ` The model's draft was discarded: ${d.model_problems.join("; ")}.`}
        </p>
      </section>
      <Feedback d={d} />
    </motion.article>
  );
}
