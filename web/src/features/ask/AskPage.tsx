// Ask a question in plain words. The model drafts SQL; the query engine checks it against your grants, runs it,
// and the rows come back to you only. A refusal says why and who can grant access.

import { AnimatePresence, motion } from "framer-motion";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useAsk, useFeedback } from "../../shared/api/hooks";
import type { Answer } from "../../shared/api/types";
import { motion as m } from "../../shared/styles/theme";
import { bytes } from "../../shared/ui/format";
import { Page } from "../../shared/ui/Page";
import { ResultTable } from "../../shared/ui/ResultTable";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import "./ask.css";

const EXAMPLES = [
  "How many leads did we get per country in the last 7 days?",
  "Wie viele Anfragen gab es gestern in Deutschland?",
  "Active stock by make",
  "Top 5 dealers by leads yesterday",
];

const HEADINGS: Record<Answer["status"], string> = {
  answered: "Answer",
  denied: "Not shown: this needs access you do not have",
  not_answerable: "Not answered from the data you can read",
  rejected: "The drafted query was not safe to run",
  unavailable: "The assistant is unavailable",
};

function Feedback({ answer }: { answer: Answer }) {
  const fb = useFeedback();
  if (fb.isSuccess) return <p className="muted" role="status">Thanks, recorded.</p>;
  return (
    <div className="ask-feedback">
      <span className="muted">Was this right?</span>
      <button type="button" className="button" disabled={fb.isPending} onClick={() => fb.mutate({ feature: "ask", ref: answer.question, helpful: true })}>
        Yes
      </button>
      <button type="button" className="button" disabled={fb.isPending} onClick={() => fb.mutate({ feature: "ask", ref: answer.question, helpful: false })}>
        No
      </button>
      {fb.isError && <span className="form-error">{fb.error.message}</span>}
    </div>
  );
}

function Result({ a }: { a: Answer }) {
  const tone = a.status === "answered" ? "ok" : a.status === "denied" ? "warn" : a.status === "unavailable" ? "stop" : "";
  return (
    <motion.article
      className={`panel ask-result tone-${tone}`}
      initial={{ opacity: 0, y: m.rise }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: m.base, ease: m.ease }}
      aria-live="polite"
    >
      <h2>{HEADINGS[a.status]}</h2>
      {a.message && <p>{a.message}</p>}
      {a.status === "answered" &&
        (a.rows.length === 0 ? (
          <EmptyState title="The query ran and returned no rows">
            Nothing matched for the days asked about. Check the latest day of the datasets below.
          </EmptyState>
        ) : (
          <>
            <ResultTable columns={a.columns} rows={a.rows} caption={a.question} />
            {a.truncated && <p className="muted">Only the first rows are shown. Narrow the question to see the rest.</p>}
          </>
        ))}
      {a.restricted.length > 0 && (
        <div className="ask-restricted">
          <h3>Who can grant access</h3>
          <ul className="plain-list">
            {a.restricted.map((r) => (
              <li key={r.product}>
                <Link to={`/datasets/${r.product}`}>{r.product}</Link>: {r.owner} ({r.owner_contact})
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.sql && (
        <details className="ask-sql" open={a.status !== "answered"}>
          <summary>How it was answered</summary>
          {a.explanation && <p>{a.explanation}</p>}
          <pre className="sql">{a.sql}</pre>
          <p className="muted">
            Read {a.plan.tables?.join(", ") || "no table"}; scanned {bytes(a.plan.bytes_scanned)}
            {a.plan.row_filters && Object.keys(a.plan.row_filters).length > 0 &&
              `; your row filter applied: ${Object.values(a.plan.row_filters).join(", ")}`}
            {a.repairs > 0 && `; the first draft was rejected and repaired once`}. Model: {a.model ?? "none"}.
          </p>
          {a.plan.problems && a.plan.problems.length > 0 && (
            <ul className="ask-problems">
              {a.plan.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </details>
      )}
      {a.datasets.length > 0 && (
        <p className="muted">
          Data as of:{" "}
          {a.datasets.map((d, i) => (
            <span key={d.product}>
              {i > 0 && ", "}
              <Link to={`/datasets/${d.product}`}>{d.product}</Link> {d.latest_dt ?? "no data"} ({d.freshness})
            </span>
          ))}
        </p>
      )}
      {(a.status === "answered" || a.status === "not_answerable") && <Feedback answer={a} />}
    </motion.article>
  );
}

export function AskPage() {
  const [question, setQuestion] = useState("");
  const ask = useAsk();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (question.trim().length >= 3) ask.mutate(question.trim());
  };

  return (
    <Page
      title="Ask the data"
      lede="Questions in English or German. You get rows only from datasets your access covers, and the query that produced them."
    >
      <form className="ask-form" onSubmit={submit}>
        <label htmlFor="ask-q">Your question</label>
        <textarea id="ask-q" className="field" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} />
        <div className="ask-actions">
          <button type="submit" className="button primary" disabled={ask.isPending || question.trim().length < 3}>
            {ask.isPending ? "Answering" : "Ask"}
          </button>
          <div className="ask-examples" aria-label="Example questions">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" className="link-button" onClick={() => setQuestion(x)}>
                {x}
              </button>
            ))}
          </div>
        </div>
      </form>

      <AnimatePresence mode="wait">
        {ask.isPending ? (
          <motion.div key="wait" exit={{ opacity: 0 }}>
            <Skeleton lines={5} label="Drafting and checking the query" />
          </motion.div>
        ) : ask.isError ? (
          <motion.div key="err" exit={{ opacity: 0 }}>
            <ErrorState title="The question was not sent" error={ask.error} onRetry={() => ask.mutate(question.trim())} />
          </motion.div>
        ) : ask.data ? (
          <Result key={ask.data.question} a={ask.data} />
        ) : (
          <motion.div key="empty" exit={{ opacity: 0 }}>
            <EmptyState title="Nothing asked yet">
              Pick an example or type a question. Answers name the datasets and days they come from.
            </EmptyState>
          </motion.div>
        )}
      </AnimatePresence>
    </Page>
  );
}
