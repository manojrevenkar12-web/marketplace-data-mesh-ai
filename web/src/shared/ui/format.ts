export const bytes = (n: number | undefined): string => {
  if (n === undefined) return "-";
  if (n < 1e3) return `${n} B`;
  if (n < 1e6) return `${(n / 1e3).toFixed(1)} kB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
};

export const pct = (x: number | null | undefined): string => (x === null || x === undefined ? "-" : `${(x * 100).toFixed(1)}%`);

export const cell = (v: unknown): string => {
  if (v === null || v === undefined) return "empty";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-GB") : v.toLocaleString("en-GB", { maximumFractionDigits: 2 });
  return String(v);
};

export const domainOf = (product: string): string => product.split(".")[0];

/** "null_spike" and "QualityCheckFailed" both read as a sentence-case phrase. */
export const label = (s: string): string => {
  const words = s
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};
