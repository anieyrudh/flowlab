import { expect, test, type Page } from "@playwright/test";

async function assertNoViewportOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const elements = [...document.body.querySelectorAll<HTMLElement>("*")];
    const offenders = elements
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          className: element.className.toString(),
          text: element.innerText?.slice(0, 40) ?? "",
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom
        };
      })
      .filter((rect) => rect.right > window.innerWidth + 2 || rect.left < -2);
    return {
      offenders,
      bodyWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth
    };
  });
  expect(overflow.bodyWidth).toBeLessThanOrEqual(overflow.viewportWidth + 2);
  expect(overflow.offenders).toEqual([]);
}

test.describe("FlowLab workspace shell", () => {
  test("keeps the desktop side-panel layout visible and usable", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByText("FlowLab")).toBeVisible();
    await expect(page.getByText("Components")).toBeVisible();
    await expect(page.getByText("Project")).toBeVisible();
    await expect(page.getByText("Layers")).toBeVisible();
    await expect(page.getByText("Inspector")).toBeVisible();
    await expect(page.getByText("Mesh controls")).toBeVisible();
    const workspacePanels = page.getByRole("navigation", { name: "Workspace panels" });
    await expect(workspacePanels.getByRole("button", { name: "Field viewer" })).toBeVisible();
    await expect(workspacePanels.getByRole("button", { name: "Metrics" })).toBeVisible();
    await expect(workspacePanels.getByRole("button", { name: "Warnings" })).toBeVisible();

    const canvas = page.getByTestId("simulation-canvas");
    await expect(canvas).toBeVisible();
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox?.width ?? 0).toBeGreaterThan(420);
    expect(canvasBox?.height ?? 0).toBeGreaterThan(360);

    await assertNoViewportOverflow(page);
  });
});
