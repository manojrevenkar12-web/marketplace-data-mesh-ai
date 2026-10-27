// Your access requests, and the requests your team decides as a dataset owner. A decision is final.

import { AnimatePresence, motion } from "framer-motion";
import { Link } from "react-router-dom";
import { useAccessRequests, useDecide } from "../../shared/api/hooks";
import type { AccessRequest } from "../../shared/api/types";
import { motion as m } from "../../shared/styles/theme";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import "./access.css";

const STATUS: Record<AccessRequest["status"], string> = { pending: "Waiting for the owner", approved: "Approved", denied: "Denied" };
const tone = (s: AccessRequest["status"]) => (s === "approved" ? "ok" : s === "denied" ? "stop" : "warn");
const extra = (r: AccessRequest) => r.sensitivities.filter((s) => s !== "public" && s !== "internal");

function Summary({ r }: { r: AccessRequest }) {
  const levels = extra(r);
  return (
    <div className="req-summary">
      <span className="req-title">
        <Link to={`/datasets/${r.product}`}>{r.product}</Link>
        <span className={`chip ${tone(r.status)}`}>{STATUS[r.status]}</span>
      </span>
      <span className="muted">
        {levels.length ? `Including ${levels.map((l) => (l === "pii" ? "personal data" : l)).join(" and ")}` : "Columns at internal level"}, for{" "}
        {r.days} days. Asked {r.created_at.slice(0, 10)}.
        {r.status === "approved" && r.expires_on && ` Ends ${r.expires_on}.`}
        {r.decided_by && ` Decided by ${r.decided_by}.`}
      </span>
      <q className="req-reason">{r.reason}</q>
    </div>
  );
}

function ToDecide({ r }: { r: AccessRequest }) {
  const decide = useDecide();
  return (
    <motion.li layout transition={{ duration: m.base, ease: m.ease }} className="req">
      <div>
        <p className="req-who">
          <strong>{r.user_id}</strong> <span className="muted">({r.principal})</span>
        </p>
        <Summary r={r} />
      </div>
      {r.status === "pending" && (
        <div className="req-actions">
          <button
            type="button"
            className="button primary"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: r.id, decision: "approve" })}
          >
            Approve for {r.days} days
          </button>
          <button type="button" className="button" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, decision: "deny" })}>
            Deny
          </button>
          {decide.isError && (
            <p className="form-error" role="alert">
              {decide.error.message}
            </p>
          )}
        </div>
      )}
    </motion.li>
  );
}

export function AccessPage() {
  const q = useAccessRequests();
  return (
    <Page
      title="Access"
      lede="Access is granted by the team that owns a dataset, for a limited time, and every request and decision is in the audit log."
    >
      {q.isPending ? (
        <Skeleton lines={8} label="Loading requests" />
      ) : q.isError ? (
        <ErrorState title="Requests did not load" error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <div className="access-grid">
          <section aria-labelledby="decide-h">
            <h2 id="decide-h">For your team to decide</h2>
            {q.data.to_decide.length === 0 ? (
              <EmptyState title="Nothing to decide">Requests for datasets your team owns appear here.</EmptyState>
            ) : (
              <ul className="plain-list access-list">
                <AnimatePresence initial={false}>
                  {q.data.to_decide.map((r) => (
                    <ToDecide key={r.id} r={r} />
                  ))}
                </AnimatePresence>
              </ul>
            )}
          </section>
          <section aria-labelledby="mine-h">
            <h2 id="mine-h">Your requests</h2>
            {q.data.mine.length === 0 ? (
              <EmptyState title="No requests yet" action={<Link to="/catalog">Find a dataset</Link>}>
                Open a dataset you cannot fully read and request access from its page.
              </EmptyState>
            ) : (
              <ul className="plain-list access-list">
                {q.data.mine.map((r) => (
                  <li key={r.id} className="req">
                    <Summary r={r} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Page>
  );
}
