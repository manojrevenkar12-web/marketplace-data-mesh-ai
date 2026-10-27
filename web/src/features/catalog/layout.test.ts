import { describe, expect, it } from "vitest";
import { map } from "../../shared/styles/theme";
import { DATASETS } from "../../test/fixtures";
import { depths, layout, trackPath } from "./layout";

describe("transit map layout", () => {
  it("orders products by lineage depth", () => {
    const d = depths(DATASETS);
    expect(d.get("listings.listing_events")).toBe(0);
    expect(d.get("listings.active_listings_daily")).toBe(1);
    expect(d.get("leads.dealer_lead_performance")).toBe(1);
  });

  it("survives a lineage cycle", () => {
    const a = { ...DATASETS[0], product: "x.a", domain: "x", inputs: ["x.b"] };
    const b = { ...DATASETS[0], product: "x.b", domain: "x", inputs: ["x.a"] };
    expect(() => layout([a, b])).not.toThrow();
  });

  it("draws a straight track along a line and a 45 degree transfer between lines", () => {
    expect(trackPath({ x: 0, y: 10 }, { x: 100, y: 10 })).toBe("M 0 10 H 100");
    const d = trackPath({ x: 0, y: 0 }, { x: 300, y: 100 });
    const [, , , , , , x2, y2] = d.split(" ").map(Number);
    expect(d).toMatch(/V 100$/);
    expect(300 - x2).toBe(0);
    expect(y2).toBe(100 - map.approach); // 45 degrees: dropped as far as it ran
  });

  it("keeps every station inside the drawing and every track off other stations' names", () => {
    const L = layout(DATASETS);
    for (const s of L.stations) {
      expect(s.x + map.labelHalf).toBeLessThanOrEqual(L.width);
      expect(s.y).toBeLessThan(L.height);
    }
    // sample each transfer and check it never passes under another station's name
    for (const t of L.tracks.filter((t) => !t.sameLine)) {
      const pts = t.d.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
      const [x0, y0, hx, lx, ly, vy] = pts;
      expect(pts).toHaveLength(6);
      const segs: [number, number, number, number][] = [
        [x0, y0, hx, y0],
        [hx, y0, lx, ly],
        [lx, ly, lx, vy],
      ];
      for (const s of L.stations.filter((s) => s.product !== t.from && s.product !== t.to)) {
        for (const [ax, ay, bx, by] of segs) {
          for (let k = 0; k <= 20; k++) {
            const x = ax + ((bx - ax) * k) / 20;
            const y = ay + ((by - ay) * k) / 20;
            const underName = y > s.y + map.station && y < s.y + map.labelGap * 2 && Math.abs(x - s.x) < map.labelHalf;
            expect(underName, `${t.from} > ${t.to} crosses ${s.product}`).toBe(false);
          }
        }
      }
    }
  });

  it("fans tracks arriving at one station side by side", () => {
    const L = layout(DATASETS);
    const into = L.tracks.filter((t) => t.to === "leads.dealer_lead_performance" && !t.sameLine);
    const ends = into.map((t) => Number(/L (\S+)/.exec(t.d)?.[1]));
    expect(new Set(ends).size).toBe(into.length);
  });
});
