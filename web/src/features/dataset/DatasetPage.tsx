// One data product: its contract as you may see it, where it comes from and goes to, and its recent runs.

import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../shared/api/client";
import { useDataset } from "../../shared/api/hooks";
import type { DatasetDetail } from "../../shared/api/types";
import { Classification, Freshness, Quality } from "../../shared/ui/Badges";
import { cell, label } from "../../shared/ui/format";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import { RequestAccess } from "./RequestAccess";
import "./dataset.css";

function Links({ products, none }: { products: string[]; none: string }) {
  if (!products.length) return <p className="muted">{none}</p>;
  return (
    <ul className="plain-list">
      {products.map((p) => (
        <li key={p}>
          <Link to={`/datasets/${p}`}>
            <span className={`line-mark line-${p.split(".")[0]}`} aria-hidden="true" /> {p}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Columns({ d }: { d: DatasetDetail }) {
  return (
    <section className="panel" aria-labelledby="cols">
      <h2 id="cols">Columns</h2>
      <div className="scroll-x">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Type</th>
              <th scope="col">Sensitivity</th>
              <th scope="col">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {d.columns.map((c) => (
              <tr key={c.name}>
                <td>
                  <code>{c.name}</code>
                  {c.deprecated_on && <span className="chip warn">Removed after {c.deprecated_on}</span>}
                </td>
                <td>{c.type}</td>
                <td>
                  <Classification value={c.classification} />
                </td>
                <td>{c.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(d.hidden_columns > 0 || d.row_filter) && (
        <p className="dataset-note">
          {d.hidden_columns > 0 && `${d.hidden_columns} columns are above your access and not listed. `}
          {d.row_filter && `You see only rows where ${d.row_filter}.`}
        </p>
      )}
    </section>
  );
}

function Runs({ d }: { d: DatasetDetail }) {
  const runs = d.runs ?? [];
  return (
    <section className="panel" aria-labelledby="runs">
      <h2 id="runs">Recent runs</h2>
      {runs.length === 0 ? (
        <EmptyState title="No runs yet">The first scheduled run publishes this dataset's first partition.</EmptyState>
      ) : (
        <div className="scroll-x">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Day</th>
                <th scope="col">Result</th>
                <th scope="col">Rows</th>
                <th scope="col">Run</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.dt}</td>
                  <td>
                    <span className={`chip ${r.status === "success" ? "ok" : "stop"}`}>
                      {r.status === "success" ? "Published" : label(r.error_type ?? r.status)}
                    </span>
                  </td>
                  <td className="num">{cell(r.rows)}</td>
                  <td>
                    {r.status === "success" ? (
                      <code>{r.id}</code>
                    ) : (
                      <Link to={`/runs?run=${encodeURIComponent(r.id)}`}>Diagnose {r.id}</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Checks({ d }: { d: DatasetDetail }) {
  const results = d.quality_results ?? [];
  return (
    <section className="panel" aria-labelledby="checks">
      <h2 id="checks">Quality checks on {d.latest_dt ?? "the latest day"}</h2>
      {results.length === 0 ? (
        <p className="muted">Declared: {d.quality_checks.join(", ") || "none"}. No results for the latest day yet.</p>
      ) : (
        <ul className="plain-list checks">
          {results.map((q, i) => (
            <li key={`${q.check}-${i}`}>
              <span className={`chip ${q.passed ? "ok" : q.severity === "error" ? "stop" : "warn"}`}>
                {q.passed ? "Passed" : q.severity === "error" ? "Failed" : "Warning"}
              </span>
              <code>{q.check}</code>
              <span className="muted">{q.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function DatasetPage() {
  const { product = "" } = useParams();
  const q = useDataset(product);

  if (q.isPending)
    return (
      <Page title={product}>
        <Skeleton lines={10} label="Loading the dataset" />
      </Page>
    );
  if (q.isError)
    return (
      <Page title={product}>
        {q.error instanceof ApiError && q.error.status === 404 ? (
          <EmptyState title="No such dataset" action={<Link to="/catalog">Back to the catalog</Link>}>
            {product} is not in the catalog. It may have been renamed or retired.
          </EmptyState>
        ) : (
          <ErrorState title="The dataset did not load" error={q.error} onRetry={() => void q.refetch()} />
        )}
      </Page>
    );

  const d = q.data;
  return (
    <Page
      title={d.product}
      lede={d.description}
      aside={
        d.visible ? (
          <div className="chips-row">
            <Freshness value={d.freshness} />
            <Quality value={d.quality} />
          </div>
        ) : (
          <span className="chip">No access</span>
        )
      }
    >
      <dl className="dataset-facts">
        <div>
          <dt>Owner</dt>
          <dd>
            {d.owner} <span className="muted">({d.owner_contact})</span>
          </dd>
        </div>
        <div>
          <dt>Kind</dt>
          <dd>{d.kind === "source" ? "Source, loaded from the domain's system" : "Derived, built from other products"}</dd>
        </div>
        <div>
          <dt>Contract</dt>
          <dd>Version {d.version}</dd>
        </div>
        <div>
          <dt>Fresh within</dt>
          <dd>{d.sla_hours} hours</dd>
        </div>
        <div>
          <dt>Partitioned by</dt>
          <dd>
            <code>{d.partition}</code>, {d.partitions} days, latest {d.latest_dt ?? "none"}
          </dd>
        </div>
      </dl>

      <div className="dataset-grid">
        <div className="dataset-main">
          {d.visible ? (
            <>
              <Columns d={d} />
              <Checks d={d} />
              <Runs d={d} />
            </>
          ) : (
            <EmptyState title="You cannot read this dataset">
              Its columns, runs and checks are shown to people with access. Ask {d.owner} with the form.
            </EmptyState>
          )}
        </div>
        <aside className="dataset-side">
          <section className="panel" aria-labelledby="up">
            <h2 id="up">Built from</h2>
            <Links products={d.upstream} none="Nothing on the platform: loaded from the domain's own system." />
          </section>
          <section className="panel" aria-labelledby="down">
            <h2 id="down">Used by</h2>
            <Links products={d.downstream} none="No other data product reads it yet." />
          </section>
          {!d.owned && (!d.visible || d.hidden_columns > 0) && (
            <RequestAccess product={d.product} owner={d.owner} needsColumns={d.visible && d.hidden_columns > 0} />
          )}
        </aside>
      </div>
    </Page>
  );
}
