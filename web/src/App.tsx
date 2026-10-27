// The console's shell: sign-in, the navigation rail, and one route per feature.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { session } from "./features/auth/session";
import { SignIn } from "./features/auth/SignIn";
import { AccessPage } from "./features/access/AccessPage";
import { AskPage } from "./features/ask/AskPage";
import { CatalogPage } from "./features/catalog/CatalogPage";
import { DatasetPage } from "./features/dataset/DatasetPage";
import { EvaluationPage } from "./features/evaluation/EvaluationPage";
import { ReviewPage } from "./features/review/ReviewPage";
import { RunsPage } from "./features/runs/RunsPage";
import { useMe } from "./shared/api/hooks";
import "./app.css";

const NAV = [
  { to: "/catalog", label: "Catalog" },
  { to: "/ask", label: "Ask" },
  { to: "/runs", label: "Failed runs" },
  { to: "/review", label: "Review a change" },
  { to: "/access", label: "Access" },
  { to: "/evaluation", label: "Evaluation" },
];

function Who({ onSignOut }: { onSignOut: () => void }) {
  const me = useMe();
  return (
    <div className="who">
      {me.data ? (
        <>
          <span className="who-name">{me.data.name}</span>
          <span className="who-group">{me.data.principal}</span>
        </>
      ) : (
        <span className="who-group">{me.isError ? "Key not accepted" : "Signing in"}</span>
      )}
      <button type="button" className="link-button" onClick={onSignOut}>
        Switch person
      </button>
    </div>
  );
}

export function App({ client }: { client?: QueryClient }) {
  const [qc] = useState(() => client ?? new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 30_000 } } }));
  const [signedIn, setSignedIn] = useState(() => session.get() !== null);
  if (!signedIn) return <SignIn onSignedIn={() => setSignedIn(true)} />;
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <div className="shell">
          <nav className="rail" aria-label="Main">
            <div className="brand">
              <span className="brand-mark" aria-hidden="true" />
              <span>Data mesh</span>
            </div>
            <ul>
              {NAV.map((n) => (
                <li key={n.to}>
                  <NavLink to={n.to}>{n.label}</NavLink>
                </li>
              ))}
            </ul>
            <Who
              onSignOut={() => {
                session.set(null);
                qc.clear();
                setSignedIn(false);
              }}
            />
          </nav>
          <main className="content">
            <Routes>
              <Route path="/" element={<Navigate to="/catalog" replace />} />
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/datasets/:product" element={<DatasetPage />} />
              <Route path="/ask" element={<AskPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/access" element={<AccessPage />} />
              <Route path="/evaluation" element={<EvaluationPage />} />
              <Route path="*" element={<Navigate to="/catalog" replace />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
