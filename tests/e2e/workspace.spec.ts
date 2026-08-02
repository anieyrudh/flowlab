import { expect, test, type Page } from "@playwright/test";

/**
 * These shell tests encode the venturi topology, and the application now opens
 * on the two-node laminar starter. Below 1100 px the inspector is a closed
 * overlay, so the preset control has to be revealed before it can be used.
 */
async function usePresetFixture(page: Page) {
  const presetSelect = page.getByLabel("Preset");
  const toggle = page.getByRole("button", { name: "Inspector" }).first();
  const opened = !(await presetSelect.isVisible());
  if (opened) await toggle.click();
  await presetSelect.selectOption("Venturi Cavitation Lab");
  // Leave the inspector as it was found, so a test that drives the toggle
  // itself still starts from the closed state it expects.
  if (opened) await toggle.click();
}


const generatedScreenshotMesh = `# vtk DataFile Version 3.0
FlowLab generated-case volume mesh
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0
1 0 0
1 1 0
0 1 0
0 0 1
1 0 1
1 1 1
0 1 1
CELLS 1 9
8 0 1 2 3 4 5 6 7
CELL_TYPES 1
12
`;

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

async function assertPrimaryControlsAccessible(page: Page) {
  const unlabeled = await page.locator("button, select, input:not([type='hidden']), canvas").evaluateAll((elements) =>
    elements.flatMap((element) => {
      const id = element.getAttribute("id");
      const explicitLabel = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`)?.textContent : "";
      const wrappingLabel = element.closest("label")?.textContent;
      const name = [
        element.getAttribute("aria-label"),
        element.getAttribute("title"),
        explicitLabel,
        wrappingLabel,
        element.textContent
      ].find((value) => value?.trim());
      return name ? [] : [`${element.tagName.toLowerCase()}${id ? `#${id}` : ""}`];
    })
  );
  expect(unlabeled).toEqual([]);
}

test.describe("FlowLab workspace shell", () => {
  test("keeps each stage's controls contained and its dock aligned", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await usePresetFixture(page);
    await expect(page.getByText("FlowLab", { exact: true })).toBeVisible();
    await expect(page.getByText("Components")).toBeVisible();
    // The guided panel's copy also contains "project", and getByText is a
    // case-insensitive substring match, so this must name the heading exactly.
    await expect(page.getByText("Project", { exact: true })).toBeVisible();
    await expect(page.getByText("Layers")).toBeVisible();
    await expect(page.locator("#inspector-panel").getByText("Inspector", { exact: true })).toBeVisible();
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
    await usePresetFixture(page);

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
    await usePresetFixture(page);

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

  test("captures governed desktop stages and checks accessible primary controls", async ({ page }) => {
    await page.route("**/api/runtime", async (route) => {
      await route.fulfill({
        json: [
          { solver: "instant-1d", runnable: true, preferredExecution: "browser", blockers: [], notes: [] },
          {
            solver: "openfoam",
            runnable: true,
            preferredExecution: "native",
            nativeCommand: "foamRun",
            nativeAvailable: true,
            dockerAvailable: false,
            blockers: [],
            notes: []
          }
        ]
      });
    });
    await page.route("**/api/cases/generate", async (route) => {
      const payload = route.request().postDataJSON() as { project?: unknown };
      await route.fulfill({
        json: {
          id: "case-preview-screenshot",
          projectName: "Venturi Cavitation Lab",
          solver: "openfoam",
          advancedMode: "incompressible-navier-stokes",
          status: "generated",
          files: {
            "flowlab_project.json": JSON.stringify(payload.project ?? {}, null, 2),
            "mesh/flowlab_mesh.vtk": generatedScreenshotMesh
          },
          runCommand: ["bash", "Allrun"],
          provenance: []
        }
      });
    });
    await page.route("**/api/jobs", async (route) => {
      await route.fulfill({
        json: {
          id: "job-preview-screenshot",
          caseId: "case-preview-screenshot",
          solver: "openfoam",
          status: "running",
          createdAt: "2026-07-31T00:00:00Z",
          updatedAt: "2026-07-31T00:00:01Z",
          caseDir: "/tmp/flowlab/preview-screenshot",
          execution: "native",
          command: ["bash", "Allrun"],
          logs: ["Generating case mesh."]
        }
      });
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await usePresetFixture(page);
    const stages = page.getByRole("navigation", { name: "FlowLab workflow stages" });

    const define = stages.getByRole("button", { name: /Define/ });
    await define.focus();
    await expect(define).toBeFocused();
    await expect(page.getByText("Concept preview", { exact: true }).first()).toBeVisible();
    await assertPrimaryControlsAccessible(page);
    await page.screenshot({ path: "test-results/preview-governance/define.png" });

    await stages.getByRole("button", { name: /CFD/ }).click();
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByLabel("Mesh mode").selectOption("full-ogrid");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await expect(page.getByText("Generated-case mesh preview", { exact: true }).first()).toBeVisible();
    await assertPrimaryControlsAccessible(page);
    await page.screenshot({ path: "test-results/preview-governance/cfd.png" });

    await stages.getByRole("button", { name: /Inspect/ }).click();
    await page.getByLabel("Examples / Developer tooling").getByRole("button", { name: "Load fixture result" }).click();
    await expect(page.getByText("Fixture result — developer example · probe only", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("cinema-canvas")).toHaveAttribute("aria-describedby", "cinema-canvas-status");
    await assertPrimaryControlsAccessible(page);
    await page.screenshot({ path: "test-results/preview-governance/inspect-fixture.png" });
  });
});
