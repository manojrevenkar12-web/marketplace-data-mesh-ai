// What the platform's AI features measured, from `mesh eval`, next to the plain baseline each one replaces.
// The blind held-out sets are shown as first run, including where they did worse.

import { motion } from "framer-motion";
import { ApiError } from "../../shared/api/client";
import { useEvaluation } from "../../shared/api/hooks";
import type { Evaluation } from "../../shared/api/types";
import { motion as m } from "../../shared/styles/theme";
import { pct } from "../../shared/ui/format";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import "./evaluation.css";

function Compare({ label, value, baseline, baselineLabel }: { label: string; value: number; baseline?: number; baselineLabel?: string }) {
  return (
    <div className="compare">
      <span className="compare-label">{label}</span>
      <div className="compare-bars">
        <div className="bar-row">
          <span className="bar-name">Platform</span>
          <span className="bar-track">
            <motion.span
              className="bar bar-us"
              initial={{ scaleX: 0 }}
              animate={{ scaleX: value }}
              transition={{ duration: m.slow, ease: m.ease }}
            />
          </span>
          <span className="bar-value">{pct(value)}</span>
        </div>
        {baseline !== undefined && (
          <div className="bar-row">
            <span className="bar-name">{baselineLabel}</span>
            <span className="bar-track">
              <motion.span
                className="bar bar-base"
                initial={{ scaleX: 0 }}
                animate={{ scaleX: baseline }}
                transition={{ duration: m.slow, ease: m.ease, delay: m.stagger }}
              />
            </span>
            <span className="bar-value">{pct(baseline)}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function Body({ e }: { e: Evaluation }) {
  const t = e.triage.clean;
  const a = e.ask.clean;
  const inj = e.ask.injected_metadata;
  return (
    <>
      <p className="eval-scope">
        Simulated marketplace {e.world.start} to {e.world.end}; {e.world.live_incidents} incidents in the evaluation window from{" "}
        {e.world.live_from}. Model: {e.model}. Generated {e.generated_at}.
      </p>
      <div className="eval-grid">
        <section className="panel" aria-labelledby="ev-triage">
          <h2 id="ev-triage">Failed-run triage</h2>
          <Compare label="Right team paged" value={t.right_team} baseline={t.naive_alert_right_team} baselineLabel="Plain alert" />
          <Compare label="Right cause" value={t.right_cause} />
          <p className="muted">
            {t.diagnoses} failed or blocked runs. A plain alert pages the failed job's owner; often the fix belongs upstream.
          </p>
        </section>
        <section className="panel" aria-labelledby="ev-review">
          <h2 id="ev-review">Change review</h2>
          <Compare label="Harmful changes blocked" value={e.review.harmful_blocked} />
          <Compare label="Safe changes approved" value={e.review.safe_approved} />
          <p className="muted">
            Of the changes that caused incidents in the simulation, {e.review.incident_changes_blocked} of {e.review.incident_changes_reviewed}{" "}
            would have been blocked before merge.
          </p>
        </section>
        <section className="panel" aria-labelledby="ev-ask">
          <h2 id="ev-ask">Questions in plain words</h2>
          <Compare label="Answered right" value={a.answered_right ?? 0} />
          {inj?.answered_right !== undefined && (
            <Compare label="With instructions planted in metadata" value={inj.answered_right} />
          )}
          <p>
            Data shown that the person may not see: <strong>{Object.values(e.ask).reduce((s, x) => s + (x.leaks ?? 0), 0)}</strong> across
            every condition, including a misbehaving model and a model that is down.
          </p>
          <p className="muted">
            {e.guard.would_run} of {e.guard.attack_queries} attack queries would run. Dataset search puts the right product first{" "}
            {pct(e.discovery.top1)} of the time, in the top three {pct(e.discovery.top3)}.
          </p>
        </section>
        <section className="panel" aria-labelledby="ev-pipe">
          <h2 id="ev-pipe">Pipelines</h2>
          <p className="eval-big">
            <strong>{e.pipelines.bad_partitions_stopped}</strong> bad partitions stopped before publishing,{" "}
            <strong>{e.pipelines.bad_partitions_published}</strong> published.
          </p>
          <p className="muted">
            {e.pipelines.runs} runs; {e.pipelines.first_attempts_failed} first attempts failed; days still broken after fixes:{" "}
            {e.pipelines.days_still_broken.length}.
          </p>
        </section>
      </div>
      <section className="panel" aria-labelledby="ev-heldout">
        <h2 id="ev-heldout">Blind held-out sets</h2>
        <p className="muted">
          Written from a guide by someone who had not seen the code, run once, and kept as first run. Where they score lower than
          the results above, the gap is the honest estimate of how a feature does on cases nobody tuned it for.
        </p>
        {e.heldout.length === 0 ? (
          <EmptyState title="No held-out runs yet">Run <code>mesh heldout run SET</code> to record a first run.</EmptyState>
        ) : (
          <ul className="plain-list heldout">
            {e.heldout.map((h) => (
              <li key={h.set}>
                <span className="heldout-set">
                  <code>{h.set}</code>
                  <span className={`chip ${h.status === "blind" ? "ask" : ""}`}>{h.status}</span>
                </span>
                <span>{h.headline}</span>
                <span className="muted">First run {h.ran_at.slice(0, 10)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export function EvaluationPage() {
  const q = useEvaluation();
  return (
    <Page title="Evaluation" lede="Measured by mesh eval on every release; the release gate fails if a number falls below its threshold.">
      {q.isPending ? (
        <Skeleton lines={10} label="Loading the evaluation" />
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 404 ? (
          <EmptyState title="No evaluation yet">
            Run <code>mesh eval</code> to build the report.
          </EmptyState>
        ) : (
          <ErrorState title="The evaluation did not load" error={q.error} onRetry={() => void q.refetch()} />
        )
      ) : (
        <Body e={q.data} />
      )}
    </Page>
  );
}
