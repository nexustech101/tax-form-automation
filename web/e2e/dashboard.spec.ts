/**
 * The dashboard against the real API on fake models (see e2e/global-setup.ts). One serial story: sign in,
 * upload, overview, review, theme, assistant (panel, then page and New chat), delete. With the fake models every
 * extracted form has two flagged fields (an amount raised by 1000 -> not_printed, a text value with " | X" ->
 * multiple_values).
 */
import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

const SAMPLES = resolve(import.meta.dirname, "..", "..", "tests", "samples");

test("the dashboard workflow", async ({ page }) => {
  test.setTimeout(300_000);
  await test.step("rejects a wrong key, then signs in", async () => {
    await page.goto("/");
    await page.getByLabel("API key").fill("ak_not_a_key");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("This key is not valid, expired or revoked.")).toBeVisible();
    await page.getByLabel("API key").fill(process.env.E2E_CLIENT_KEY!);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByText("No documents yet")).toBeVisible();
  });

  await test.step("uploads two files for a client", async () => {
    await page.getByRole("button", { name: "Upload documents" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Upload documents" });
    await dialog.getByLabel("Client").fill("Gonzalez");
    await dialog.getByLabel("Tax year").fill("2025");
    await dialog.getByTestId("upload-input").setInputFiles([resolve(SAMPLES, "w2.pdf"), resolve(SAMPLES, "stack.pdf")]);
    await dialog.getByRole("button", { name: "Upload 2 files" }).click();
    await expect(dialog.getByText("1 form extracted · 2 flagged fields")).toBeVisible({ timeout: 90_000 });
    await expect(dialog.getByText("3 forms extracted · 6 flagged fields")).toBeVisible({ timeout: 90_000 });
    await dialog.getByRole("button", { name: "Done" }).click();
    await expect(page.getByRole("row", { name: /w2/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /stack/ })).toContainText("Gonzalez");
  });

  await test.step("overview counts the uploads", async () => {
    await page.getByRole("link", { name: "Overview" }).click();
    await expect(page.getByRole("group", { name: "Documents" })).toContainText("2");
    await expect(page.getByRole("group", { name: "Forms extracted" })).toContainText("4");
    await expect(page.getByRole("group", { name: "Awaiting review" })).toContainText("4");
    await expect(page.getByRole("group", { name: "Open flags" })).toContainText("8");
  });

  await test.step("reviews a W-2: confirm, correct, reject, approve", async () => {
    await page.getByRole("link", { name: "Documents" }).click();
    await page.getByRole("link", { name: "w2", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Form W-2 Wage and Tax Statement" })).toBeVisible();

    const approve = page.getByRole("button", { name: "Approve" });
    await expect(approve).toBeDisabled();
    await expect(page.getByTestId("approve-reason")).toHaveText(/Decide 2 flagged fields/);

    const control = page.locator('[data-field="control_number"]');
    await control.click();
    await control.getByRole("button", { name: /Confirm/ }).click();
    await expect(control).toHaveAttribute("data-decision", "confirmed");

    const wages = page.locator('[data-field="wages_tips_other_comp"]');
    await wages.click();
    await wages.getByRole("button", { name: /Correct/ }).click();
    await wages.getByLabel(/Corrected value/).fill("68,412.55");
    await wages.getByLabel(/Corrected value/).press("Enter");
    await expect(wages).toHaveAttribute("data-decision", "corrected");
    await expect(wages).toContainText("$68,412.55");

    await page.getByRole("radio", { name: /All/ }).click();
    const withheld = page.locator('[data-field="federal_income_tax_withheld"]');
    await withheld.click();
    await page.keyboard.press("r");
    await expect(withheld).toHaveAttribute("data-decision", "rejected");
    await withheld.click();
    await page.keyboard.press("u");
    await expect(withheld).toHaveAttribute("data-decision", "none");

    await expect(approve).toBeEnabled();
    await approve.click();
    await expect(page.getByText(/Approved by e2e-client/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Reopen" })).toBeVisible();
  });

  await test.step("switches to dark mode and keeps it", async () => {
    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("radio", { name: "Light" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });

  await test.step("the assistant answers with a source that opens the document", async () => {
    await page.getByRole("link", { name: "Overview" }).click();
    await page.getByRole("button", { name: "Assistant" }).click();
    const panel = page.getByRole("complementary", { name: "Assistant" });
    await panel.getByLabel("Question").fill("What were the wages tips other compensation?");
    await panel.getByLabel("Question").press("Enter");
    await expect(panel.getByTestId("tool-activity")).toContainText("Searched");
    await expect(panel.getByTestId("citation").first()).toBeVisible();
    await panel.getByTestId("source").first().click();
    await expect(page).toHaveURL(/\/documents\/[\w-]+\?page=\d+/);
    await expect(page.getByRole("link", { name: "Documents" }).first()).toBeVisible();
    await expect(page.getByRole("tablist", { name: "Forms in this document" })).toBeVisible();
    await panel.getByRole("button", { name: "Close assistant" }).click();
  });

  await test.step("the assistant page continues the conversation; New chat clears it", async () => {
    await page.getByRole("link", { name: "Assistant" }).click();
    const log = page.getByRole("log", { name: "Conversation" });
    await expect(log.getByTestId("citation").first()).toBeVisible();
    const question = page.getByLabel("Question");
    await question.fill("list the documents with open flags");
    await question.press("Enter");
    await expect(question).toHaveValue("");
    await expect(log.getByTestId("tool-activity").last()).toContainText("Listed documents");
    await page.getByRole("button", { name: "New chat" }).click();
    await expect(log).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /^Good (morning|afternoon|evening)$/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "New chat" })).toHaveCount(0);
  });

  await test.step("deletes a document", async () => {
    await page.getByRole("link", { name: "Documents" }).first().click();
    await page.getByRole("button", { name: "Delete stack" }).click();
    await page.getByRole("dialog", { name: "Delete document?" }).getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Deleted stack")).toBeVisible();
    await expect(page.getByRole("row", { name: /stack/ })).toHaveCount(0);
    await expect(page.getByText("1–1 of 1 document")).toBeVisible();
  });
});
