// What data exists, who owns it, whether today's data is in, and whether you can read it.

import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useCatalog, useRuns } from "../../shared/api/hooks";
import type { Dataset } from "../../shared/api/types";
import { Freshness, Quality } from "../../shared/ui/Badges";
import { Page } from "../../shared/ui/Page";
import { EmptyState, ErrorState, Skeleton } from "../../shared/ui/states";
import { MeshMap } from "./MeshMap";
import "./catalog.css";

function Row({ d }: { d: Dataset }) {
  return (
    <li className="catalog-row">
      <div className="catalog-main">
        <Link to={`/datasets/${d.product}`} className="catalog-name">
          <span className={`line-mark line-${d.domain}`} aria-hidden="true" />
          {d.product}
        </Link>
        <p className="catalog-desc">{d.description}</p>
      </div>
      <dl className="catalog-facts">
        <div>
          <dt>Owner</dt>
          <dd>{d.owner}</dd>
        </div>
        <div>
          <dt>Latest day</dt>
          <dd>{d.latest_dt ?? "none"}</dd>
        </div>
        <div className="catalog-chips">
          {d.visible ? (
            <>
              <Freshness value={d.freshness} />
              <Quality value={d.quality} />
              {d.hidden_columns > 0 && <span className="chip">{d.hidden_columns} columns hidden</span>}
              {d.row_filter && <span className="chip ask">Rows: {d.row_filter}</span>}
            </>
          ) : (
            <span className="chip">No access: ask {d.owner}</span>
          )}
        </div>
      </dl>
    </li>
  );
}

function Today({ datasets }: { datasets: Dataset[] }) {
  const runs = useRuns();
  const readable = datasets.filter((d) => d.visible);
  const fresh = readable.filter((d) => d.freshness === "fresh").length;
  const failing = readable.filter((d) => d.quality === "failed").map((d) => d.product);
  const late = readable.filter((d) => d.freshness !== "fresh").map((d) => d.product);
  const hidden = readable.reduce((n, d) => n + d.hidden_columns, 0);
  const broken = runs.data?.filter((r) => !r.fixed).length;
  return (
    <aside className="today" aria-labelledby="today-h">
      <h2 id="today-h">Today</h2>
      <dl>
        <div>
          <dt>Fresh</dt>
          <dd>
            <span className="today-figure">
              {fresh} of {readable.length}
            </span>
            <span className="today-note">{late.length > 0 ? `Late: ${late.join(", ")}` : "Every product you can read is up to date."}</span>
          </dd>
        </div>
        <div>
          <dt>Failed days not yet fixed</dt>
          <dd>
            {runs.isPending ? (
              <span className="muted">counting</span>
            ) : runs.isError ? (
              <button type="button" className="link-button" onClick={() => void runs.refetch()}>
                Could not count; try again
              </button>
            ) : (
              <>
                <span className="today-figure">{broken}</span>
                <Link to="/runs">{broken ? "diagnose them" : "see past failures"}</Link>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Quality checks failing</dt>
          <dd>
            <span className="today-figure">{failing.length}</span>
            {failing.length > 0 && <span className="today-note">{failing.join(", ")}</span>}
          </dd>
        </div>
        <div>
          <dt>Your access</dt>
          <dd>
            {readable.length} of {datasets.length} products
            {hidden > 0 && `, ${hidden} columns hidden`}
          </dd>
        </div>
      </dl>
    </aside>
  );
}

export function CatalogPage() {
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const all = useCatalog("");
  const found = useCatalog(q);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setQ(draft.trim());
  };

  return (
    <Page
      title="Data products"
      lede={
        all.data
          ? `${all.data.datasets.length} products in ${new Set(all.data.datasets.map((d) => d.domain)).size} domains, as of ${all.data.today}.`
          : "Every domain's published datasets, with their owners and today's status."
      }
    >
      {all.isPending ? (
        <Skeleton lines={4} label="Loading the map" />
      ) : all.isError ? (
        <ErrorState title="The catalog did not load" error={all.error} onRetry={() => void all.refetch()} />
      ) : all.data.datasets.length === 0 ? (
        <EmptyState title="No data products yet">
          Publish one with <code>mesh new-product</code> or the new-data-product skill; it appears here after its first run.
        </EmptyState>
      ) : (
        <div className="catalog-hero">
          <MeshMap datasets={all.data.datasets} />
          <Today datasets={all.data.datasets} />
        </div>
      )}

      <form className="catalog-search" onSubmit={submit} role="search">
        <label htmlFor="catalog-q">Find a dataset by what it holds</label>
        <div className="catalog-search-row">
          <input
            id="catalog-q"
            className="field"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="leads per dealer, Anfragen, asking price"
          />
          <button type="submit" className="button">
            Search
          </button>
          {q && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setDraft("");
                setQ("");
              }}
            >
              Show all
            </button>
          )}
        </div>
      </form>

      {found.isPending ? (
        <Skeleton lines={6} label="Searching" />
      ) : found.isError ? (
        <ErrorState title="Search failed" error={found.error} onRetry={() => void found.refetch()} />
      ) : found.data.datasets.length === 0 ? (
        <EmptyState title={`Nothing matches "${q}"`}>
          Try a column name, a business term, or the German word your team uses. Searches cover descriptions, columns and the glossary.
        </EmptyState>
      ) : (
        <ul className="catalog-list" aria-label={q ? `Datasets matching ${q}` : "All datasets"}>
          {found.data.datasets.map((d) => (
            <Row key={d.product} d={d} />
          ))}
        </ul>
      )}
    </Page>
  );
}
