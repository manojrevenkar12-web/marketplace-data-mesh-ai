// Places data products on the transit map: one lane per domain, one column per lineage depth.
// Pure functions, so the layout is tested without rendering.

import type { Dataset } from "../../shared/api/types";
import { DOMAINS, map } from "../../shared/styles/theme";

export interface Station {
  product: string;
  domain: string;
  name: string;
  x: number;
  y: number;
  dataset: Dataset;
}

export interface Track {
  from: string;
  to: string;
  domain: string; // the upstream product's domain: a track carries its line's colour
  sameLine: boolean;
  d: string;
  depth: number;
}

export interface Layout {
  width: number;
  height: number;
  lanes: { domain: string; y: number; from: number; to: number }[];
  stations: Station[];
  tracks: Track[];
}

export function depths(datasets: Dataset[]): Map<string, number> {
  const byName = new Map(datasets.map((d) => [d.product, d]));
  const memo = new Map<string, number>();
  const visit = (p: string, seen: Set<string>): number => {
    const known = memo.get(p);
    if (known !== undefined) return known;
    if (seen.has(p)) return 0; // a cycle is reported by lint; draw it flat rather than loop
    seen.add(p);
    const inputs = (byName.get(p)?.inputs ?? []).filter((i) => byName.has(i));
    const d = inputs.length ? 1 + Math.max(...inputs.map((i) => visit(i, seen))) : 0;
    memo.set(p, d);
    return d;
  };
  for (const d of datasets) visit(d.product, new Set());
  return memo;
}

/**
 * A track between two stations. Along one line it is straight. Into another line it runs along its own line,
 * turns 45 degrees, and drops straight into the station; tracks arriving at one station come in side by side,
 * ``offset`` apart, the farthest line on the left, so they never cross.
 */
export function trackPath(a: { x: number; y: number }, b: { x: number; y: number }, offset = 0): string {
  if (a.y === b.y) return `M ${a.x} ${a.y} H ${b.x}`;
  const dir = Math.sign(b.y - a.y);
  const dy = Math.abs(b.y - a.y);
  const arriveX = b.x + offset;
  const run = Math.max(0, Math.min(dy - map.approach, arriveX - a.x));
  return `M ${a.x} ${a.y} H ${arriveX - run} L ${arriveX} ${a.y + dir * run} V ${b.y}`;
}

export function layout(datasets: Dataset[]): Layout {
  const depth = depths(datasets);
  const laneY = (domain: string) => {
    const i = DOMAINS.indexOf(domain as (typeof DOMAINS)[number]);
    const lane = i < 0 ? DOMAINS.length : i;
    return map.laneHeight / 2 + lane * (map.laneHeight + map.laneGap) + map.labelGap;
  };
  // stations of one domain at the same depth sit side by side, half a column apart
  const slot = new Map<string, number>();
  const stations: Station[] = [...datasets]
    .sort((a, b) => a.product.localeCompare(b.product))
    .map((d) => {
      const col = depth.get(d.product) ?? 0;
      const key = `${d.domain}:${col}`;
      const n = slot.get(key) ?? 0;
      slot.set(key, n + 1);
      return {
        product: d.product,
        domain: d.domain,
        name: d.product.slice(d.domain.length + 1),
        x: map.inset + col * map.stationGap + n * (map.stationGap / 2),
        y: laneY(d.domain),
        dataset: d,
      };
    });
  const at = new Map(stations.map((s) => [s.product, s]));
  const inputsOf = (s: Station) => s.dataset.inputs.map((i) => at.get(i)).filter((u): u is Station => u !== undefined);

  // A track that drops through another line must not run through that line's station names: move the station it
  // arrives at along its line, half a column at a time, until every crossing is clear.
  const crossingX = (a: Station, b: Station, y: number): number => {
    const run = Math.max(0, Math.min(Math.abs(b.y - a.y) - map.approach, b.x - a.x));
    const down = Math.abs(y - a.y);
    return down <= run ? b.x - run + down : b.x;
  };
  for (let pass = 0; pass < stations.length; pass++) {
    let moved = false;
    for (const s of stations) {
      for (const up of inputsOf(s)) {
        if (up.domain === s.domain) continue;
        const [lo, hi] = [Math.min(up.y, s.y), Math.max(up.y, s.y)];
        const blocked = stations.some(
          (o) =>
            o !== s &&
            o !== up &&
            o.y >= lo &&
            o.y < hi &&
            (o.y > lo || o.y === up.y) && // stations between the lines, and the names under the line it leaves
            [o.y, o.y + map.labelGap, o.y + map.labelGap * 2]
              .filter((y) => y > lo && y < hi)
              .some((y) => Math.abs(crossingX(up, s, y) - o.x) < map.labelHalf),
        );
        if (blocked) {
          s.x += map.stationGap / 2;
          moved = true;
        }
      }
    }
    if (!moved) break;
  }

  const tracks: Track[] = [];
  for (const s of stations) {
    const ups = inputsOf(s);
    // arriving from other lines: farthest line first (leftmost), then fanned around the station's centre
    const transfers = ups.filter((u) => u.domain !== s.domain).sort((u, v) => Math.abs(v.y - s.y) - Math.abs(u.y - s.y));
    const offsetOf = (u: Station) => (transfers.indexOf(u) - (transfers.length - 1) / 2) * map.fan;
    for (const up of ups) {
      const sameLine = up.domain === s.domain;
      tracks.push({
        from: up.product,
        to: s.product,
        domain: up.domain,
        sameLine,
        d: trackPath(up, s, sameLine ? 0 : offsetOf(up)),
        depth: depth.get(s.product) ?? 0,
      });
    }
  }
  const domains = [...new Set(stations.map((s) => s.domain))];
  const lanes = DOMAINS.filter((d) => domains.includes(d)).map((domain) => {
    // a line runs on to where its last track turns off it
    const turns = tracks
      .filter((t) => t.domain === domain && !t.sameLine)
      .map((t) => Number(/^M \S+ \S+ H (\S+)/.exec(t.d)?.[1] ?? 0));
    const xs = [...stations.filter((s) => s.domain === domain).map((s) => s.x), ...turns];
    return { domain, y: laneY(domain), from: map.inset / 2, to: Math.max(...xs) + map.stationGap / 3 };
  });
  const maxX = Math.max(map.inset, ...stations.map((s) => s.x));
  const maxY = Math.max(map.laneHeight, ...stations.map((s) => s.y));
  return { width: maxX + map.inset * 1.5, height: maxY + map.laneHeight / 2 + map.labelGap, lanes, stations, tracks };
}
