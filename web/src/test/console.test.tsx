// The console against a fake API: every screen's loading, failure, empty and data states, and what it sends.

import { QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { session } from "../features/auth/session";
import { ANSWER, DATASETS, DIAGNOSIS, EVALUATION, REVIEW, RUNS, detail, request } from "./fixtures";

type Handler = (url: string, init: RequestInit) => { status?: number; body: unknown } | undefined;
let calls: { url: string; method: string; body: unknown; auth: string | null }[] = [];

function api(handler: Handler) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = String(input);
      const headers = (init.headers ?? {}) as Record<string, string>;
      calls.push({
        url,
        method: init.method ?? "GET",
        body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
        auth: headers.authorization ?? null,
      });
      const r = handler(url, init) ?? defaults(url);
      if (!r) return new Response(JSON.stringify({ detail: `no fake for ${url}` }), { status: 500 });
      return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "content-type": "application/json" } });
    }),
  );
}

function defaults(url: string): { status?: number; body: unknown } | undefined {
  if (url === "/v1/me") return { body: { user: "anna.analyst", name: "Anna Berger", principal: "analysts-marketplace", owns: [] } };
  if (url.startsWith("/v1/catalog")) return { body: { today: "2026-10-01", datasets: DATASETS } };
  if (url === "/v1/runs") return { body: RUNS };
  return undefined;
}

function open(path: string) {
  session.set({ key: "demo-anna.analyst", user: "anna.analyst" });
  window.history.pushState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<App client={client} />);
}

beforeEach(() => api(() => undefined));
afterEach(() => vi.unstubAllGlobals());

describe("sign-in", () => {
  it("lists the demo people and signs in with their key", async () => {
    window.history.pushState({}, "", "/catalog");
    render(<App client={new QueryClient({ defaultOptions: { queries: { retry: false } } })} />);
    await userEvent.click(screen.getByRole("button", { name: /Anna Berger/ }));
    expect(await screen.findByRole("heading", { name: "Data products" })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.auth === "Bearer demo-anna.analyst")).toBe(true));
  });

  it("switching person clears the session", async () => {
    open("/catalog");
    await userEvent.click(await screen.findByRole("button", { name: "Switch person" }));
    expect(screen.getByRole("button", { name: /Max Keller/ })).toBeInTheDocument();
    expect(session.get()).toBeNull();
  });
});

describe("catalog", () => {
  it("draws every product as a station and lists them", async () => {
    open("/catalog");
    const map = await screen.findByRole("group", { name: /Lineage of 6 data products across 4 domains/ });
    expect(within(map).getAllByRole("link")).toHaveLength(6);
    expect(within(map).getByRole("link", { name: /pricing.price_ratings, no access/ })).toBeInTheDocument();
    expect(within(map).getByRole("link", { name: /leads.leads, late/ })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "All datasets" });
    expect(within(list).getByText("No access: ask team-pricing")).toBeInTheDocument();
    expect(within(list).getByText("3 columns hidden")).toBeInTheDocument();
  });

  it("opens a dataset from its station with the keyboard", async () => {
    api((url) => (url === "/v1/datasets/leads.leads" ? { body: detail("leads.leads") } : undefined));
    open("/catalog");
    const station = await screen.findByRole("link", { name: /^leads.leads,/ });
    station.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { level: 1, name: "leads.leads" })).toBeInTheDocument();
  });

  it("shows the failure and retries", async () => {
    let fail = true;
    api((url) => (url.startsWith("/v1/catalog") && fail ? { status: 503, body: { detail: "lake unavailable" } } : undefined));
    open("/catalog");
    expect(await screen.findByText("The catalog did not load")).toBeInTheDocument();
    expect(screen.getAllByText("lake unavailable").length).toBeGreaterThan(0);
    fail = false;
    await userEvent.click(screen.getAllByRole("button", { name: "Try again" })[0]);
    expect(await screen.findByRole("group", { name: /Lineage of 6/ })).toBeInTheDocument();
  });

  it("says when a search finds nothing", async () => {
    api((url) => (url === "/v1/catalog?q=weather" ? { body: { today: "2026-10-01", datasets: [] } } : undefined));
    open("/catalog");
    await userEvent.type(await screen.findByLabelText("Find a dataset by what it holds"), "weather");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText('Nothing matches "weather"')).toBeInTheDocument();
  });

  it("says when there are no products at all", async () => {
    api((url) => (url.startsWith("/v1/catalog") ? { body: { today: "2026-10-01", datasets: [] } } : undefined));
    open("/catalog");
    expect(await screen.findByText("No data products yet")).toBeInTheDocument();
  });
});

describe("dataset", () => {
  it("lists visible columns, says how many are hidden and requests access", async () => {
    api((url, init) => {
      if (url === "/v1/datasets/dealers.dealers") return { body: detail("dealers.dealers") };
      if (url === "/v1/access-requests" && init.method === "POST") return { status: 201, body: { id: 9, status: "pending", owner: "team-dealers" } };
      return undefined;
    });
    open("/datasets/dealers.dealers");
    expect(await screen.findByText("3 columns are above your access and not listed.")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send request" });
    expect(send).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/Confidential columns/));
    await userEvent.type(screen.getByLabelText("Why you need it"), "Reconcile dealer invoices for the Q3 close");
    await userEvent.click(send);
    expect(await screen.findByText(/Request sent to team-dealers/)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({
      product: "dealers.dealers",
      reason: "Reconcile dealer invoices for the Q3 close",
      days: 30,
      sensitivities: ["public", "internal", "confidential"],
    });
  });

  it("shows only the description of a dataset the person cannot read", async () => {
    api((url) =>
      url === "/v1/datasets/pricing.price_ratings" ? { body: detail("pricing.price_ratings", { runs: undefined, quality_results: undefined }) } : undefined,
    );
    open("/datasets/pricing.price_ratings");
    expect(await screen.findByText("You cannot read this dataset")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Columns" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Request access" })).toBeInTheDocument();
  });

  it("says when a dataset does not exist", async () => {
    api((url) => (url.startsWith("/v1/datasets/") ? { status: 404, body: { detail: "no dataset x.y" } } : undefined));
    open("/datasets/x.y");
    expect(await screen.findByText("No such dataset")).toBeInTheDocument();
  });

  it("links a failed run to its diagnosis", async () => {
    api((url) =>
      url === "/v1/datasets/leads.leads"
        ? { body: detail("leads.leads", { runs: [{ id: "leads.leads:2026-09-23:1", dt: "2026-09-23", status: "quality_failed", rows: 10, error_type: "QualityCheckFailed" }] }) }
        : undefined,
    );
    open("/datasets/leads.leads");
    expect(await screen.findByText("Quality check failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Diagnose leads.leads:2026-09-23:1" })).toHaveAttribute(
      "href",
      "/runs?run=leads.leads%3A2026-09-23%3A1",
    );
  });
});

describe("ask", () => {
  it("shows the rows, the query and where the data came from", async () => {
    api((url) => (url === "/v1/ask" ? { body: ANSWER } : undefined));
    open("/ask");
    await userEvent.type(screen.getByLabelText("Your question"), "Leads per country");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "17,869" })).toBeInTheDocument();
    expect(screen.getByText(ANSWER.sql)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/v1/ask")?.body).toEqual({ question: "Leads per country" });
  });

  it("names who can grant access when it refuses", async () => {
    api((url) =>
      url === "/v1/ask"
        ? {
            body: {
              ...ANSWER,
              status: "denied",
              message: "This needs buyer_email, which your access does not include.",
              columns: [],
              rows: [],
              restricted: [{ product: "leads.leads", owner: "team-leads", owner_contact: "#data-leads" }],
            },
          }
        : undefined,
    );
    open("/ask");
    await userEvent.click(screen.getByRole("button", { name: "Top 5 dealers by leads yesterday" }));
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("heading", { name: /needs access you do not have/ })).toBeInTheDocument();
    expect(screen.getByText(/team-leads \(#data-leads\)/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("starts empty and retries a failed request", async () => {
    let fail = true;
    api((url) => (url === "/v1/ask" ? (fail ? { status: 502, body: { detail: "gateway down" } } : { body: ANSWER }) : undefined));
    open("/ask");
    expect(screen.getByText("Nothing asked yet")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question"), "Leads per country");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("gateway down")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
  });

  it("records feedback", async () => {
    api((url) => {
      if (url === "/v1/ask") return { body: ANSWER };
      if (url === "/v1/feedback") return { status: 201, body: { status: "recorded" } };
      return undefined;
    });
    open("/ask");
    await userEvent.type(screen.getByLabelText("Your question"), "Leads per country");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await userEvent.click(await screen.findByRole("button", { name: "Yes" }));
    expect(await screen.findByText("Thanks, recorded.")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/v1/feedback")?.body).toEqual({ feature: "ask", ref: ANSWER.question, helpful: true });
  });
});

describe("failed runs", () => {
  it("separates days still broken and shows who has to act", async () => {
    api((url) => (url.includes("/diagnosis") ? { body: DIAGNOSIS } : undefined));
    open("/runs");
    expect(await screen.findByRole("heading", { name: "Still broken (1)" })).toBeInTheDocument();
    expect(screen.getByText("Select a failed run")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /leads.dealer_lead_performance/ }));
    expect(await screen.findByRole("heading", { name: "Upstream schema drift" })).toBeInTheDocument();
    expect(screen.getByText("team-listings")).toBeInTheDocument();
    expect(screen.getByText("different team")).toBeInTheDocument();
    expect(screen.getByText("(cited)").closest("li")).toHaveClass("cited");
    expect(window.location.search).toBe("?run=leads.dealer_lead_performance%3A2026-09-23%3A1");
  });

  it("says when nothing failed", async () => {
    api((url) => (url === "/v1/runs" ? { body: [] } : undefined));
    open("/runs");
    expect(await screen.findByText("No failed runs")).toBeInTheDocument();
  });

  it("retries a failed diagnosis", async () => {
    let fail = true;
    api((url) => (url.includes("/diagnosis") ? (fail ? { status: 500, body: { detail: "boom" } } : { body: DIAGNOSIS }) : undefined));
    open(`/runs?run=${encodeURIComponent(RUNS[0].id)}`);
    expect(await screen.findByText("The diagnosis failed")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Upstream schema drift" })).toBeInTheDocument();
  });
});

describe("review", () => {
  it("sends only the edited file and shows the verdict", async () => {
    api((url) => {
      if (url.startsWith("/v1/contracts/"))
        return {
          body: {
            product: "dealers.dealers",
            files: [{ path: "products/dealers/dealers.yaml", text: "columns:\n  - {name: dealer_id}\n" }],
          },
        };
      if (url === "/v1/reviews") return { body: REVIEW };
      return undefined;
    });
    open("/review");
    const box = await screen.findByLabelText(/products\/dealers\/dealers.yaml/);
    const button = screen.getByRole("button", { name: "Review this change" });
    expect(button).toBeDisabled();
    await userEvent.clear(box);
    await userEvent.type(box, "columns:{Enter}  - {{name: dealer_key}");
    await userEvent.click(button);
    expect(await screen.findByRole("heading", { name: /Blocked/ })).toBeInTheDocument();
    expect(screen.getByText(/Approval from: team-leads/)).toBeInTheDocument();
    const sent = calls.find((c) => c.url === "/v1/reviews")?.body as { files: { path: string; new: string }[] };
    expect(sent.files).toHaveLength(1);
    expect(sent.files[0].path).toBe("products/dealers/dealers.yaml");
    expect(sent.files[0].new).toContain("dealer_key");
  });
});

describe("access", () => {
  it("lets the owning team approve, and lists the person's own requests", async () => {
    let decided = false;
    api((url, init) => {
      if (url === "/v1/access-requests")
        return {
          body: {
            mine: [request({ id: 2, product: "leads.leads", status: "denied", decided_by: "lukas.leads" })],
            to_decide: [request({ status: decided ? "approved" : "pending", expires_on: decided ? "2026-10-31" : null })],
          },
        };
      if (url === "/v1/access-requests/1/decision" && init.method === "POST") {
        decided = true;
        return { body: { id: 1, status: "approved", expires_on: "2026-10-31" } };
      }
      return undefined;
    });
    open("/access");
    expect(await screen.findByText("Denied")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve for 30 days" }));
    expect(await screen.findByText(/Ends 2026-10-31/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/decision"))?.body).toEqual({ decision: "approve" });
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("explains empty lists", async () => {
    api((url) => (url === "/v1/access-requests" ? { body: { mine: [], to_decide: [] } } : undefined));
    open("/access");
    expect(await screen.findByText("Nothing to decide")).toBeInTheDocument();
    expect(screen.getByText("No requests yet")).toBeInTheDocument();
  });
});

describe("evaluation", () => {
  it("shows the measured numbers next to their baselines and the blind first runs", async () => {
    api((url) => (url === "/v1/evaluation" ? { body: EVALUATION } : undefined));
    open("/evaluation");
    expect(await screen.findByText("56.4%")).toBeInTheDocument();
    expect(screen.getByText(EVALUATION.heldout[0].headline)).toBeInTheDocument();
  });

  it("says how to build the report when there is none", async () => {
    api((url) => (url === "/v1/evaluation" ? { status: 404, body: { detail: "no evaluation report yet" } } : undefined));
    open("/evaluation");
    expect(await screen.findByText("No evaluation yet")).toBeInTheDocument();
  });
});
