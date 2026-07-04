import { expect, test } from "@playwright/test";

test.describe("dashboard smoke", () => {
  test("login page renders", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText("Staff sign-in")).toBeVisible();
  });

  test("home dashboard shell loads", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/Your revenue cycle at a glance/i)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("coding module loads", async ({ page }) => {
    await page.goto("/coding");
    await expect(page.getByRole("heading", { name: /medical coding/i })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("HITL inbox loads", async ({ page }) => {
    await page.goto("/hitl");
    await expect(page.getByRole("heading", { name: /HITL Inbox/i })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("claims module loads", async ({ page }) => {
    await page.goto("/claims");
    await expect(page.getByRole("heading", { name: "Claims", exact: true })).toBeVisible({
      timeout: 15_000,
    });
  });
});
