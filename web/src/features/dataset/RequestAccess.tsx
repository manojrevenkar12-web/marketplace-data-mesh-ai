// Ask the owning team for access. The owner decides on the Access screen; nothing is granted here.

import { AnimatePresence, motion } from "framer-motion";
import { type FormEvent, useState } from "react";
import { useRequestAccess } from "../../shared/api/hooks";
import { motion as m } from "../../shared/styles/theme";

const LEVELS = [
  { value: "confidential", label: "Confidential columns (commercial terms, internal scores)" },
  { value: "pii", label: "Personal data (names, emails, phone numbers)" },
] as const;

export function RequestAccess({ product, owner, needsColumns }: { product: string; owner: string; needsColumns: boolean }) {
  const [reason, setReason] = useState("");
  const [days, setDays] = useState(30);
  const [levels, setLevels] = useState<string[]>([]);
  const req = useRequestAccess();
  const tooShort = reason.trim().length < 10;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (tooShort) return;
    req.mutate({ product, reason: reason.trim(), days, sensitivities: ["public", "internal", ...levels] });
  };

  return (
    <div className="panel request">
      <h2>Request access</h2>
      <AnimatePresence mode="wait" initial={false}>
        {req.isSuccess ? (
          <motion.p
            key="sent"
            role="status"
            initial={{ opacity: 0, y: m.rise }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: m.base, ease: m.ease }}
          >
            Request sent to {req.data.owner}. You will see their decision under Access; approved access ends after {days} days.
          </motion.p>
        ) : (
          <motion.form key="form" onSubmit={submit} className="request-form" exit={{ opacity: 0 }} transition={{ duration: m.fast }}>
            <p className="muted">
              {owner} owns this dataset and decides. Say what you need it for; the request and the decision are kept in the audit log.
            </p>
            {needsColumns && (
              <fieldset>
                <legend>Columns you need beyond your current access</legend>
                {LEVELS.map((l) => (
                  <label key={l.value} className="check">
                    <input
                      type="checkbox"
                      checked={levels.includes(l.value)}
                      onChange={(e) => setLevels((xs) => (e.target.checked ? [...xs, l.value] : xs.filter((x) => x !== l.value)))}
                    />
                    {l.label}
                  </label>
                ))}
              </fieldset>
            )}
            <label htmlFor="req-reason">Why you need it</label>
            <textarea
              id="req-reason"
              className="field"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-describedby="req-reason-hint"
            />
            <span id="req-reason-hint" className="muted">
              At least 10 characters, for example the report or decision it feeds.
            </span>
            <label htmlFor="req-days">For how long</label>
            <select id="req-days" className="field" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
            {req.isError && (
              <p className="form-error" role="alert">
                {req.error.message}
              </p>
            )}
            <div>
              <button type="submit" className="button primary" disabled={tooShort || req.isPending}>
                {req.isPending ? "Sending request" : "Send request"}
              </button>
            </div>
          </motion.form>
        )}
      </AnimatePresence>
    </div>
  );
}
