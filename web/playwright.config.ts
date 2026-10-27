import { defineConfig, devices } from "@playwright/test";

// End to end against the real API and a production build. The API needs a built world (`mesh sim --small`,
// `mesh train`) and a seeded database (`mesh demo --seed-only`); CI does both before this runs.
const db = process.env.MESH_DATABASE_URL ?? "postgresql://mesh:mesh@localhost:5432/mesh";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL: "http://127.0.0.1:5173", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] }, grep: /@phone/ },
  ],
  webServer: [
    {
      command: "../.venv/bin/mesh serve --host 127.0.0.1 --port 8080",
      url: "http://127.0.0.1:8080/readyz",
      env: { MESH_DATABASE_URL: db },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run build && npm run preview",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
