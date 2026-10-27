// Propose a change to a contract or transform and see what review would say before opening a pull request:
// which consumers break, who has to approve, and whether the new SQL runs on the last days of real data.

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { useCatalog, useContractFiles, useReview } from "../../shared/api/hooks";
import type { Finding, Review } from "../../shared/api/types";
import { motion as m } from "../../shared/styles/theme";
import { label } from "../../shared/ui/format";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import "./review.css";

const VERDICT: Record<Review["verdict"], { text: string; tone: string }> = {
  approve: { text: "Safe to merge", tone: "ok" },
  needs_approval: { text: "Needs approval before merge", tone: "warn" },
  block: { text: "Blocked: this change would break something", tone: "stop" },
};

const SEVERITY: Record<Finding["severity"], { text: string; tone: string }> = {
  block: { text: "Blocks", tone: "stop" },
  needs_approval: { text: "Needs approval", tone: "warn" },
  info: { text: "Note", tone: "" },
};

function Findings({ r }: { r: Review }) {
  const v = VERDICT[r.verdict];
  const order = ["block", "needs_approval", "info"];
  const findings = [...r.findings].sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));
  return (
    <motion.section
      className={`panel review-result tone-${v.tone}`}
      initial={{ opacity: 0, y: m.rise }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: m.base, ease: m.ease }}
      aria-live="polite"
      aria-labelledby="verdict"
    >
      <h2 id="verdict">{v.text}</h2>
      <p>{r.summary}</p>
      {r.suggested_path && (
        <p>
          <strong>Safer path: </strong>
          {r.suggested_path}
        </p>
      )}
      {findings.length === 0 ? (
        <p className="muted">No findings: nothing downstream reads what changed.</p>
      ) : (
        <ul className="plain-list review-findings">
          {findings.map((f) => (
            <li key={f.id}>
              <span className={`chip ${SEVERITY[f.severity].tone}`}>{SEVERITY[f.severity].text}</span>
              <div>
                <strong>{label(f.kind)}</strong>
                {f.column && (
                  <>
                    {" "}
                    on <code>{f.column}</code>
                  </>
                )}{" "}
                <span className="muted">in {f.product}</span>
                <p>{f.detail}</p>
                {f.consumers.length > 0 && <p className="muted">Affects: {f.consumers.join(", ")}</p>}
                {f.approvers.length > 0 && <p className="muted">Approval from: {f.approvers.join(", ")}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary>The comment posted on the pull request</summary>
        <pre className="sql">{r.comment_markdown}</pre>
      </details>
    </motion.section>
  );
}

function Editor({ product }: { product: string }) {
  const files = useContractFiles(product);
  const review = useReview();
  const [texts, setTexts] = useState<Record<string, string>>({});
  useEffect(() => {
    if (files.data) setTexts(Object.fromEntries(files.data.files.map((f) => [f.path, f.text])));
    review.reset();
    // reset only when another product's files arrive
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files.data]);

  const changed = useMemo(
    () => (files.data?.files ?? []).filter((f) => texts[f.path] !== undefined && texts[f.path] !== f.text),
    [files.data, texts],
  );

  if (files.isPending) return <Skeleton lines={10} label="Loading the contract" />;
  if (files.isError) return <ErrorState title="The contract did not load" error={files.error} onRetry={() => void files.refetch()} />;
  if (files.data.files.length === 0) return <EmptyState title="No files for this product">Its contract is missing from the repository.</EmptyState>;

  return (
    <div className="review-grid">
      <form
        className="review-files"
        onSubmit={(e) => {
          e.preventDefault();
          if (changed.length) review.mutate(changed.map((f) => ({ path: f.path, new: texts[f.path] })));
        }}
      >
        {files.data.files.map((f) => (
          <div key={f.path} className="review-file">
            <label htmlFor={`file-${f.path}`}>
              <code>{f.path}</code>
              {texts[f.path] !== f.text && <span className="chip ask">edited</span>}
            </label>
            <textarea
              id={`file-${f.path}`}
              className="field review-text"
              spellCheck={false}
              value={texts[f.path] ?? ""}
              onChange={(e) => setTexts((t) => ({ ...t, [f.path]: e.target.value }))}
              rows={Math.min(28, Math.max(8, f.text.split("\n").length + 1))}
            />
          </div>
        ))}
        <div className="review-actions">
          <button type="submit" className="button primary" disabled={!changed.length || review.isPending}>
            {review.isPending ? "Reviewing" : "Review this change"}
          </button>
          <button
            type="button"
            className="button"
            disabled={!changed.length}
            onClick={() => {
              setTexts(Object.fromEntries(files.data.files.map((f) => [f.path, f.text])));
              review.reset();
            }}
          >
            Undo my edits
          </button>
          {!changed.length && <span className="muted">Edit a file to review the change.</span>}
        </div>
      </form>
      <div className="review-out">
        <AnimatePresence mode="wait">
          {review.isPending ? (
            <motion.div key="wait" exit={{ opacity: 0 }}>
              <Skeleton lines={8} label="Checking lineage, consumers and the last days of data" />
            </motion.div>
          ) : review.isError ? (
            <motion.div key="err" exit={{ opacity: 0 }}>
              <ErrorState
                title="The review did not run"
                error={review.error}
                onRetry={() => review.mutate(changed.map((f) => ({ path: f.path, new: texts[f.path] })))}
              />
            </motion.div>
          ) : review.data ? (
            <Findings key="r" r={review.data} />
          ) : (
            <motion.div key="empty" exit={{ opacity: 0 }}>
              <EmptyState title="No review yet">
                Try renaming a column, lowering a column's sensitivity, or loosening a quality check, then review the change.
              </EmptyState>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export function ReviewPage() {
  const catalog = useCatalog("");
  const [product, setProduct] = useState<string | null>(null);
  const products = catalog.data?.datasets.map((d) => d.product).sort() ?? [];
  useEffect(() => {
    if (!product && products.length) setProduct(products[0]);
  }, [product, products]);

  return (
    <Page
      title="Review a change"
      lede="The same review runs on every pull request that touches products/. Nothing here is merged or published."
      aside={
        products.length > 0 && (
          <label className="review-pick">
            <span>Data product</span>
            <select className="field" value={product ?? ""} onChange={(e) => setProduct(e.target.value)}>
              {products.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        )
      }
    >
      {catalog.isPending ? (
        <Skeleton lines={8} label="Loading products" />
      ) : catalog.isError ? (
        <ErrorState title="Products did not load" error={catalog.error} onRetry={() => void catalog.refetch()} />
      ) : !product ? (
        <EmptyState title="No data products to change">Create one with the new-data-product skill first.</EmptyState>
      ) : (
        <Editor key={product} product={product} />
      )}
    </Page>
  );
}
