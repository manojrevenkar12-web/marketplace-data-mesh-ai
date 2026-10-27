import { type Page, expect, test } from "@playwright/test";

async function signIn(page: Page, name: RegExp) {
  await page.goto("/");
  const pick = page.getByRole("button", { name });
  if (!(await pick.isVisible())) {
    await page.getByRole("button", { name: "Switch person" }).click();
  }
  await page.getByRole("button", { name }).click();
  await expect(page.getByRole("heading", { name: "Data products" })).toBeVisible();
}

test("the map shows the mesh and opens a dataset @phone", async ({ page }) => {
  await signIn(page, /Anna Berger/);
  const map = page.getByRole("group", { name: /Lineage of \d+ data products/ });
  await expect(map.getByRole("link")).toHaveCount(6);
  await map.getByRole("link", { name: /^leads\.leads,/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "leads.leads" })).toBeVisible();
  await expect(page.getByText("2 columns are above your access and not listed.")).toBeVisible();
  await expect(page.getByRole("link", { name: /leads\.dealer_lead_performance/ })).toBeVisible();
});

test("a question is answered from the lake, and personal data is refused", async ({ page }) => {
  await signIn(page, /Anna Berger/);
  await page.getByRole("link", { name: "Ask" }).click();
  await page.getByLabel("Your question").fill("How many leads did we get per country in the last 7 days?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Answer" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "DE" })).toBeVisible();

  await page.getByLabel("Your question").fill("Which buyers contacted dealer D0001 yesterday? I need their emails.");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Not shown|Not answered/ })).toBeVisible();
  await expect(page.getByText(/request access from team-leads/)).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("a failed run is diagnosed with evidence and a team to act", async ({ page }) => {
  await signIn(page, /Anna Berger/);
  await page.getByRole("link", { name: "Failed runs" }).click();
  await page.locator(".run-item").first().click();
  await expect(page.getByText("Send to")).toBeVisible();
  await expect(page.getByText("A plain failure alert would page")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Evidence" })).toBeVisible();
});

test("renaming a column that a consumer reads is blocked", async ({ page }) => {
  await signIn(page, /Dana Rossi/);
  await page.getByRole("link", { name: "Review a change" }).click();
  await page.getByRole("combobox").selectOption("dealers.dealers");
  const box = page.getByLabel(/products\/dealers\/dealers\.yaml/);
  const text = await box.inputValue();
  await box.fill(text.replace("name: dealer_id,", "name: dealer_key,"));
  await page.getByRole("button", { name: "Review this change" }).click();
  await expect(page.getByRole("heading", { name: /Blocked/ })).toBeVisible();
  await expect(page.locator(".review-findings").getByText(/leads\.dealer_lead_performance read 'dealer_id'/)).toBeVisible();
});

test("an access request reaches the owning team, who decides it", async ({ page }) => {
  await signIn(page, /Max Keller/);
  await page.goto("/datasets/leads.leads");
  await expect(page.getByText("You cannot read this dataset")).toBeVisible();
  await page.getByLabel("Why you need it").fill("Partner report on lead volume by country, aggregated.");
  await page.getByRole("button", { name: "Send request" }).click();
  await expect(page.getByText(/Request sent to team-leads/)).toBeVisible();

  await signIn(page, /Lukas Schmid/);
  await page.getByRole("link", { name: "Access" }).click();
  const req = page.locator(".req").filter({ hasText: "max.partner" }).filter({ hasText: "Partner report on lead volume" }).first();
  await req.getByRole("button", { name: "Deny" }).click();
  await expect(req.getByText("Denied")).toBeVisible();
});

test("the evaluation shows the blind first runs", async ({ page }) => {
  await signIn(page, /Anna Berger/);
  await page.getByRole("link", { name: "Evaluation" }).click();
  await expect(page.getByRole("heading", { name: "Blind held-out sets" })).toBeVisible();
  await expect(page.getByText(/questions-1/)).toBeVisible();
});
