// The frame of every screen: a heading block that rises in once on load, then the content.

import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { motion as m } from "../styles/theme";
import "./page.css";

export function Page({ title, lede, aside, children }: { title: string; lede?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="page">
      <motion.header
        className="page-head"
        initial={{ opacity: 0, y: m.rise }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: m.base, ease: m.ease }}
      >
        <div>
          <h1>{title}</h1>
          {lede && <p className="page-lede">{lede}</p>}
        </div>
        {aside}
      </motion.header>
      {children}
    </section>
  );
}
