// Choose who to be. In a deployment this is single sign-on; the demo seeds one key per user.

import { motion } from "framer-motion";
import { motion as m } from "../../shared/styles/theme";
import { DEMO_USERS, session } from "./session";
import "./signin.css";

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  return (
    <main className="signin">
      <div className="signin-head">
        <h1>Data mesh console</h1>
        <p>
          The marketplace's data products, the questions you can ask of them, and what happens when they break. Sign in as one of the
          demo people; each sees only what their grants allow.
        </p>
      </div>
      <ul className="signin-list">
        {DEMO_USERS.map((u, i) => (
          <motion.li
            key={u.user}
            initial={{ opacity: 0, y: m.rise }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * m.stagger, duration: m.base, ease: m.ease }}
          >
            <button
              type="button"
              className="signin-user"
              onClick={() => {
                session.set({ key: `demo-${u.user}`, user: u.user });
                onSignedIn();
              }}
            >
              <span className="signin-name">{u.name}</span>
              <span className="signin-role">{u.role}</span>
            </button>
          </motion.li>
        ))}
      </ul>
    </main>
  );
}
