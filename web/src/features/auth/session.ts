// The signed-in key, kept per browser tab. Storage can be unavailable (private mode): fall back to memory.

export interface Session {
  key: string;
  user: string;
}

let memory: Session | null = null;
const KEY = "mesh.session";

export const session = {
  get(): Session | null {
    try {
      const raw = sessionStorage.getItem(KEY);
      return raw ? (JSON.parse(raw) as Session) : memory;
    } catch {
      return memory;
    }
  },
  set(s: Session | null) {
    memory = s;
    try {
      if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
      else sessionStorage.removeItem(KEY);
    } catch {
      /* memory only */
    }
  },
};

/** Demo users seeded by `mesh demo` (keys `demo-<user>`; never present in a real deployment). */
export const DEMO_USERS: { user: string; name: string; role: string }[] = [
  { user: "anna.analyst", name: "Anna Berger", role: "Central analytics: every domain, no personal data" },
  { user: "lukas.leads", name: "Lukas Schmid", role: "Leads team: owns the lead datasets" },
  { user: "dana.dealers", name: "Dana Rossi", role: "Dealers team: owns the dealer datasets" },
  { user: "lena.listings", name: "Lena Vogel", role: "Listings team: owns the listing datasets" },
  { user: "piet.pricing", name: "Piet de Vries", role: "Pricing data science" },
  { user: "sofia.success", name: "Sofia Wagner", role: "Dealer success Germany: German rows, with contacts" },
  { user: "max.partner", name: "Max Keller", role: "External partner reporting: public listing data only" },
];
