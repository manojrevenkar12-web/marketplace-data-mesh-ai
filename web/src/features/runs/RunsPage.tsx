// Every run that did not publish, newest first, and the diagnosis of the selected one.

import { useSearchParams } from "react-router-dom";
import { useRuns } from "../../shared/api/hooks";
import type { RunRow } from "../../shared/api/types";
import { label } from "../../shared/ui/format";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import { DiagnosisPanel } from "./DiagnosisPanel";
import "./runs.css";

function RunItem({ r, selected, onSelect }: { r: RunRow; selected: boolean; onSelect: () => void }) {
  return (
    <li>
      <button type="button" className={`run-item${selected ? " selected" : ""}`} aria-pressed={selected} onClick={onSelect}>
        <span className="run-top">
          <span className={`line-mark line-${r.product.split(".")[0]}`} aria-hidden="true" />
          <strong>{r.product}</strong>
          <span className="muted">{r.dt}</span>
        </span>
        <span className="run-bottom">
          <span className={`chip ${r.status === "upstream_failed" ? "" : "stop"}`}>{label(r.error_type ?? r.status)}</span>
          <span className={`chip ${r.fixed ? "ok" : "warn"}`}>{r.fixed ? "Day fixed since" : "Still broken"}</span>
          <span className="muted">step {r.step || "-"}</span>
        </span>
      </button>
    </li>
  );
}

export function RunsPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("run");
  const runs = useRuns();
  const open = runs.data?.filter((r) => !r.fixed) ?? [];
  const fixed = runs.data?.filter((r) => r.fixed) ?? [];
  const select = (id: string) => setParams({ run: id });

  return (
    <Page
      title="Failed runs"
      lede="Runs that did not publish, on the datasets you can see. Bad data stops here; the published partition stays as it was."
    >
      <div className="runs-grid">
        <div className="runs-list">
          {runs.isPending ? (
            <Skeleton lines={10} label="Loading runs" />
          ) : runs.isError ? (
            <ErrorState title="Runs did not load" error={runs.error} onRetry={() => void runs.refetch()} />
          ) : runs.data.length === 0 ? (
            <EmptyState title="No failed runs">Every run on the datasets you can see published its partition.</EmptyState>
          ) : (
            <>
              <h2 className="runs-h">Still broken ({open.length})</h2>
              {open.length === 0 ? (
                <p className="muted">None: every failed day has been fixed by a later run.</p>
              ) : (
                <ul className="plain-list">
                  {open.map((r) => (
                    <RunItem key={r.id} r={r} selected={r.id === selected} onSelect={() => select(r.id)} />
                  ))}
                </ul>
              )}
              <h2 className="runs-h">Fixed by a later run ({fixed.length})</h2>
              <ul className="plain-list">
                {fixed.map((r) => (
                  <RunItem key={r.id} r={r} selected={r.id === selected} onSelect={() => select(r.id)} />
                ))}
              </ul>
            </>
          )}
        </div>
        <div className="runs-detail">
          <DiagnosisPanel runId={selected} />
        </div>
      </div>
    </Page>
  );
}
