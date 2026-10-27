// Motion and domain colours for code that cannot read CSS variables directly (Framer Motion, SVG maps).

export const motion = {
  fast: 0.12,
  base: 0.24,
  slow: 0.6,
  ease: [0.2, 0.7, 0.2, 1] as [number, number, number, number],
  stagger: 0.05,
  rise: 8,
} as const;

export const domainLine: Record<string, string> = {
  listings: "var(--line-listings)",
  dealers: "var(--line-dealers)",
  leads: "var(--line-leads)",
  pricing: "var(--line-pricing)",
};

export const map = {
  laneHeight: 64,
  laneGap: 12,
  stationGap: 300,
  inset: 120,
  station: 9,
  track: 4,
  transfer: 2.5,
  fan: 6, // distance between tracks arriving side by side at one station
  approach: 26, // the straight run into a station from another line
  label: 13,
  labelGap: 24,
  labelHalf: 90, // half the width of the longest station name
} as const;

/** Domains in the order their lines run down the map. */
export const DOMAINS = ["listings", "dealers", "leads", "pricing"] as const;
