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
  test("keeps each stage's controls contained and its dock aligned", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.getByText("FlowLab")).toBeVisible();
    await expect(page.getByText("Components")).toBeVisible();
    await expect(page.getByText("Project")).toBeVisible();
    await expect(page.getByText("Layers")).toBeVisible();
    await expect(page.locator("#inspector-panel").getByText("Inspector")).toBeVisible();
    const stages = page.getByRole("navigation", { name: "FlowLab workflow stages" });
    await expect(stages.getByRole("button", { name: /Define/ })).toBeVisible();
    await expect(stages.getByRole("button", { name: /Estimate/ })).toBeVisible();
    await expect(stages.getByRole("button", { name: /CFD/ })).toBeVisible();
    await expect(stages.getByRole("button", { name: /Inspect/ })).toBeVisible();

    await expect(page.locator("#components-panel")).toBeVisible();
    await expect(page.locator("#project-layers-panel")).toBeVisible();
    await expect(page.locator("#reference-cases-panel")).toBeHidden();
    await expect(page.locator(".run-status")).toBeHidden();

    await stages.getByRole("button", { name: /Estimate/ }).click();
    await expect(page.locator("#components-panel")).toBeHidden();
    await expect(page.locator("#project-layers-panel")).toBeHidden();
    await expect(page.locator("#reference-cases-panel")).toBeHidden();
    await expect(page.locator(".run-status")).toBeHidden();
    await expect(page.locator("#inspector-panel").getByRole("heading", { name: "Instant estimate" })).toBeVisible();
    const estimatePanels = page.getByRole("navigation", { name: "Workspace panels" });
    await expect(estimatePanels.getByRole("button", { name: "Sweep" })).toBeVisible();
    await expect(estimatePanels.getByRole("button", { name: "Metrics" })).toBeVisible();
    await expect(estimatePanels.getByRole("button", { name: "Diagnostics" })).toHaveCount(0);
    await expect(page.getByText("Sweep: inlet flow rate")).toBeVisible();

    await stages.getByRole("button", { name: /CFD/ }).click();
    await expect(page.getByText("Mesh controls")).toBeVisible();
    await expect(page.locator("#components-panel")).toBeHidden();
    await expect(page.locator("#project-layers-panel")).toBeHidden();
    await expect(page.locator("#reference-cases-panel")).toBeHidden();
    await expect(page.locator(".run-status")).toBeVisible();
    const cfdPanels = page.getByRole("navigation", { name: "Workspace panels" });
    await expect(cfdPanels.getByRole("button", { name: "Field viewer" })).toHaveCount(0);
    await expect(cfdPanels.getByRole("button", { name: "Diagnostics" })).toBeVisible();
    await expect(cfdPanels.getByRole("button", { name: "Warnings" })).toBeVisible();
    await expect(page.getByText("Solver diagnostics", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate and queue experimental CFD case" })).toBeDisabled();
    await expect(page.getByText(/Instant 1D runs in the Estimate stage/i)).toBeVisible();

    await stages.getByRole("button", { name: /Inspect/ }).click();
    await expect(page.locator("#components-panel")).toBeHidden();
    await expect(page.locator("#project-layers-panel")).toBeHidden();
    await expect(page.locator("#reference-cases-panel")).toBeVisible();
    await expect(page.locator(".run-status")).toBeVisible();
    await expect(page.getByRole("button", { name: "Import VTK/VTU" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Load fixture result" })).toBeVisible();
    const inspectPanels = page.getByRole("navigation", { name: "Workspace panels" });
    await expect(inspectPanels.getByRole("button", { name: "Field viewer" })).toBeVisible();

    const schematic = page.getByTestId("schematic-canvas");
    const cinema = page.getByTestId("cinema-canvas");
    await expect(schematic).toBeVisible();
    await expect(cinema).toBeVisible();
    expect((await schematic.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(420);
    expect((await cinema.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(420);

    const divider = page.getByTestId("workspace-divider");
    await expect(divider).toHaveAttribute("aria-valuenow", "50");
    await divider.press("ArrowRight");
    await expect(divider).toHaveAttribute("aria-valuenow", "55");
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.workspace.dual-view.v1") ?? ""))
      .toContain('"schematicRatio":55');

    await assertNoViewportOverflow(page);
  });

  test("keeps the schematic painted after selection, result loading, and a resize", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");

    const schematic = page.getByTestId("schematic-canvas");
    async function expectPainted() {
      await expect.poll(() => schematic.evaluate((element) => {
        const canvas = element as HTMLCanvasElement;
        const context = canvas.getContext("2d");
        if (!context || canvas.width === 0 || canvas.height === 0) return 0;
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        let luminance = 0;
        for (let index = 0; index < pixels.length; index += 128) luminance += pixels[index] + pixels[index + 1] + pixels[index + 2];
        return luminance;
      })).toBeGreaterThan(0);
    }

    await expectPainted();
    await page.getByRole("button", { name: /^Nodes \(3\)$/ }).click();
    await expect(schematic).toHaveAttribute("data-selected-id", "source");
    await expectPainted();
    await page.getByRole("navigation", { name: "FlowLab workflow stages" }).getByRole("button", { name: /Inspect/ }).click();
    await page.getByRole("button", { name: "Load fixture result" }).click();
    await page.getByTestId("workspace-divider").press("ArrowRight");
    await expectPainted();
  });

  test("uses an inspector overlay and a one-pane fallback before either view is compressed", async ({ page }) => {
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.goto("/");

    const inspectorToggle = page.getByRole("button", { name: "Inspector" });
    await expect(inspectorToggle).toBeVisible();
    await inspectorToggle.click();
    await expect(inspectorToggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator(".workspace-shell")).toHaveClass(/inspector-overlay-open/);

    await page.setViewportSize({ width: 1024, height: 900 });
    const viewSwitcher = page.getByLabel("Workspace view");
    await expect(viewSwitcher).toBeVisible();
    await expect(page.getByTestId("schematic-pane")).toBeVisible();
    await viewSwitcher.getByRole("button", { name: "3D view" }).click();
    await expect(page.getByTestId("cinema-pane")).toBeVisible();
    await expect(page.getByTestId("schematic-pane")).toBeHidden();
  });
});
