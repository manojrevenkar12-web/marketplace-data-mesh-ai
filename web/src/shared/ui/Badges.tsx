// Small status marks with words, never colour alone.

import type { Column, Dataset } from "../api/types";

export function Freshness({ value }: { value: Dataset["freshness"] }) {
  const tone = value === "fresh" ? "ok" : value === "late" ? "warn" : "";
  return <span className={`chip ${tone}`}>{value === "fresh" ? "Fresh" : value === "late" ? "Late" : value === "no data" ? "No data" : "Unknown"}</span>;
}

export function Quality({ value }: { value: Dataset["quality"] }) {
  const tone = value === "passed" ? "ok" : value === "warnings" ? "warn" : value === "failed" ? "stop" : "";
  const text = { passed: "Checks passed", warnings: "Warnings", failed: "Checks failed", "not run": "Not checked", unknown: "Unknown" }[value];
  return <span className={`chip ${tone}`}>{text}</span>;
}

export function Classification({ value }: { value: Column["classification"] }) {
  const tone = value === "pii" ? "stop" : value === "confidential" ? "warn" : value === "internal" ? "ask" : "";
  const text = { public: "Public", internal: "Internal", confidential: "Confidential", pii: "Personal data" }[value];
  return <span className={`chip ${tone}`}>{text}</span>;
}
