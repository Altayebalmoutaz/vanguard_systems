import { expect, test } from "@playwright/test";

test.describe("dashboard smoke", () => {
  test("login page renders", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText("Staff sign-in")).toBeVisible();
  });

  test("home eligibility dashboard loads", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByText(/what.?s happening with your eligibility verifications/i),
    ).toBeVisible({
      timeout: 15_000,
    });
  });

  test("opendental page loads", async ({ page }) => {
    await page.goto("/opendental");
    await expect(page.getByRole("heading", { name: /OpenDental/i })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("voice page loads", async ({ page }) => {
    await page.goto("/voice");
    await expect(page.getByRole("heading", { name: /Voice Agent/i })).toBeVisible({
      timeout: 15_000,
    });
  });
});
