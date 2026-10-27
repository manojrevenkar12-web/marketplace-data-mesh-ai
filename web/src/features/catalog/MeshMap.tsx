// The mesh as a transit map: each domain a line, each data product a station, lineage the track.
// The tracks draw themselves in lineage order once when the page loads; that is the console's one
// orchestrated moment. Status is spelled out under each station, never carried by colour alone.

import { motion, useReducedMotion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import type { Dataset } from "../../shared/api/types";
import { domainLine, map, motion as m } from "../../shared/styles/theme";
import { layout } from "./layout";

const statusText = (d: Dataset): { text: string; tone: "ok" | "warn" | "stop" | "none" } => {
  if (!d.visible) return { text: "no access", tone: "none" };
  if (d.quality === "failed") return { text: "checks failed", tone: "stop" };
  if (d.freshness === "late") return { text: "late", tone: "warn" };
  if (d.freshness === "no data") return { text: "no data", tone: "warn" };
  if (d.quality === "warnings") return { text: "warnings", tone: "warn" };
  return { text: d.latest_dt ? `to ${d.latest_dt}` : "", tone: "ok" };
};

export function MeshMap({ datasets }: { datasets: Dataset[] }) {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const L = layout(datasets);
  const draw = (delay: number) =>
    reduce ? { duration: 0 } : { pathLength: { delay, duration: m.slow, ease: m.ease }, opacity: { delay, duration: m.fast } };

  return (
    <figure className="mesh-map">
      <svg
        viewBox={`0 0 ${L.width} ${L.height}`}
        width={L.width}
        height={L.height}
        role="group"
        aria-label={`Lineage of ${L.stations.length} data products across ${L.lanes.length} domains`}
      >
        {L.lanes.map((lane, i) => (
          <g key={lane.domain}>
            <text className="map-lane" x={0} y={lane.y - map.labelGap} fill={domainLine[lane.domain]}>
              {lane.domain}
            </text>
            <motion.path
              d={`M ${lane.from} ${lane.y} H ${lane.to}`}
              stroke={domainLine[lane.domain]}
              strokeWidth={map.track}
              strokeLinecap="round"
              fill="none"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 1 }}
              transition={draw(i * m.stagger)}
            />
          </g>
        ))}
        {L.tracks
          .filter((t) => !t.sameLine)
          .map((t) => (
            <motion.path
              key={`${t.from}>${t.to}`}
              d={t.d}
              stroke={domainLine[t.domain]}
              strokeWidth={map.transfer}
              strokeLinejoin="round"
              strokeLinecap="round"
              fill="none"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 1 }}
              transition={draw(m.slow / 2 + t.depth * m.slow * 0.5)}
            >
              <title>{`${t.to} reads ${t.from}`}</title>
            </motion.path>
          ))}
        {L.stations.map((s, i) => {
          const st = statusText(s.dataset);
          const go = () => navigate(`/datasets/${s.product}`);
          return (
            <motion.g
              key={s.product}
              className={`map-station tone-${st.tone}`}
              role="link"
              tabIndex={0}
              aria-label={`${s.product}, ${st.text || "fresh"}`}
              onClick={go}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  go();
                }
              }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={reduce ? { duration: 0 } : { delay: m.slow + i * m.stagger, duration: m.base }}
            >
              <circle
                cx={s.x}
                cy={s.y}
                r={map.station}
                stroke={domainLine[s.domain]}
                strokeWidth={map.track}
                strokeDasharray={s.dataset.visible ? undefined : `${map.track} ${map.track}`}
                className="map-dot"
              />
              <text x={s.x} y={s.y + map.labelGap + map.station} textAnchor="middle" className="map-name">
                {s.name}
              </text>
              {st.text && (
                <text x={s.x} y={s.y + map.labelGap * 1.65 + map.station} textAnchor="middle" className="map-status">
                  {st.text}
                </text>
              )}
            </motion.g>
          );
        })}
      </svg>
      <figcaption>
        Lines are domains, stations are data products, and a track leaving a line means another domain builds on that product.
        Select a station to open it.
      </figcaption>
    </figure>
  );
}
