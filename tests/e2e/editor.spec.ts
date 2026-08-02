import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const nativeOpenFoamResult = `# vtk DataFile Version 3.0
FlowLab OpenFOAM native time 0.002
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
CELL_DATA 1
SCALARS p float 1
LOOKUP_TABLE default
101325
VECTORS U float
1.5 0 0
`;

const generatedVolumeMesh = `# vtk DataFile Version 3.0
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

const multiEdgeOpenFoamResult = `# vtk DataFile Version 3.0
FlowLab multi-edge OpenFOAM native time 1
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0
1 0 0
2 0 0
3 0 0
0 1 0
1 1 0
2 1 0
3 1 0
CELLS 3 15
4 0 1 5 4
4 1 2 6 5
4 2 3 7 6
CELL_TYPES 3
9
9
9
CELL_DATA 3
SCALARS p float 1
LOOKUP_TABLE default
103000
102000
101000
SCALARS flowlabSourceCellId float 1
LOOKUP_TABLE default
0
1
2
VECTORS U float
1 0 0
1.5 0 0
2 0 0
`;

const importedHexVolumeResult = `# vtk DataFile Version 3.0
FlowLab imported hexahedral volume
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
POINT_DATA 8
SCALARS pressure float 1
LOOKUP_TABLE default
0
1
2
1
1
2
3
2
`;

const patchMetricsFixture = {
  schema: "flowlab.patch_metrics.v1",
  status: "partial",
  patches: {
    inlet: {
      patchName: "inlet",
      role: "inlet",
      flowRate: { value: -0.012, unit: "m3/s", time: 0.002, path: "postProcessing/patchFlowRate/0/patchFlowRate.dat" },
      averagePressure: { value: 101325, unit: "Pa", time: 0.002, path: "postProcessing/patchAverage/0/p.dat", field: "p" },
      sources: ["postProcessing/patchFlowRate/0/patchFlowRate.dat"]
    },
    outlet: {
      patchName: "outlet",
      role: "outlet",
      flowRate: { value: 0.0118, unit: "m3/s", time: 0.002, path: "postProcessing/patchFlowRate/0/patchFlowRate.dat" },
      averagePressure: { value: 99000, unit: "Pa", time: 0.002, path: "postProcessing/patchAverage/0/p.dat", field: "p" },
      sources: ["postProcessing/patchAverage/0/p.dat"]
    },
    walls: {
      patchName: "walls",
      role: "wall",
      wallShear: { min: 0.4, mean: 1.1, max: 2.8, unit: "Pa", time: 0.002, path: "postProcessing/wallShearStress/0/wallShearStress.dat" },
      sources: ["postProcessing/wallShearStress/0/wallShearStress.dat"]
    }
  },
  flowBalance: { inletFlow: 0.012, outletFlow: 0.0118, imbalance: -0.0002, relativeImbalance: 0.0166666667, unit: "m3/s", inletPatches: ["inlet"], outletPatches: ["outlet"] },
  pressureDrops: [{ fromPatch: "inlet", toPatch: "outlet", inletPressure: 101325, outletPressure: 99000, deltaP: 2325, unit: "Pa" }],
  forces: [
    {
      patchName: "walls",
      time: 0.002,
      force: { x: 1.1, y: 2.2, z: 3.3 },
      moment: { x: 4.4, y: 5.5, z: 6.6 },
      forceMagnitude: 4.1158,
      momentMagnitude: 9.6525,
      path: "postProcessing/wallForces/0/forces.dat"
    }
  ],
  pressureProbes: [{ path: "postProcessing/probes/0/p", time: 0.002, sampleCount: 2, minPressure: 99000, maxPressure: 101325, pressureSpan: 2325, unit: "Pa" }],
  warnings: ["OpenFOAM patchAverage pressure output is partial in this mock."],
  sources: [{ path: "postProcessing/patchFlowRate/0/patchFlowRate.dat", kind: "patch-flow-rate", status: "parsed" }]
};

function meshQualityFixture(kind: "passed" | "failed" | "missing" | "production" = "passed") {
  const commonArtifacts = [
    { path: "mesh/production_mesh_acceptance.json", exists: true, size: 1024, text: "{}" },
    { path: "log.surfaceFeatureExtract", exists: kind !== "missing", size: 32, text: "Extracting features\n" },
    { path: "log.blockMesh", exists: kind !== "missing", size: 32, text: "Mesh OK\n" },
    { path: "log.snappyHexMesh", exists: kind !== "missing", size: 48, text: "Layer addition phase\n" },
    { path: "log.checkMesh", exists: kind !== "missing", size: 64, text: kind === "failed" ? "Failed 2 mesh checks.\n" : "Failed 0 mesh checks.\n" },
    { path: "log.yPlus", exists: kind === "passed" || kind === "production", size: 32, text: "yPlus\n" }
  ];
  if (kind === "missing") {
    return {
      schema: "flowlab.mesh_quality_summary.v1",
      status: "blocked",
      productionReady: false,
      approvalStatus: "blocked",
      nativeQualityStatus: "openfoam-native-quality-blocked",
      solverAcceptanceStatus: "blocked",
      openfoam: {
        status: "blocked",
        commandRuns: [
          { command: "surfaceFeatureExtract", status: "missing-command", exitCode: null },
          { command: "snappyHexMesh", status: "missing-command", exitCode: null },
          { command: "checkMesh", status: "missing-command", exitCode: null }
        ],
        qualityMetrics: {},
        yPlusEvidence: { status: "missing", blockingReason: "Missing y-plus or wall-distance evidence." },
        layerSummary: { status: "not-run", excerpts: [] },
        blockingReasons: [
          "Missing OpenFOAM native mesh command `surfaceFeatureExtract`.",
          "Missing OpenFOAM native mesh command `snappyHexMesh`.",
          "Missing OpenFOAM native mesh command `checkMesh`."
        ]
      },
      artifacts: commonArtifacts,
      artifactLimitBytes: 120000
    };
  }
  const failed = kind === "failed";
  const production = kind === "production";
  return {
    schema: "flowlab.mesh_quality_summary.v1",
    status: failed ? "blocked" : "passed",
    productionReady: production,
    approvalStatus: production ? "approved" : "blocked",
    nativeQualityStatus: failed ? "openfoam-native-quality-blocked" : "openfoam-native-quality-passed",
    solverAcceptanceStatus: production ? "production-ready" : failed ? "blocked" : "native-evidence-passed",
    openfoam: {
      status: failed ? "blocked" : "passed",
      commandRuns: [
        { command: "surfaceFeatureExtract", status: "complete", exitCode: 0, logPath: "log.surfaceFeatureExtract" },
        { command: "blockMesh", status: "complete", exitCode: 0, logPath: "log.blockMesh" },
        { command: "snappyHexMesh -overwrite", status: "complete", exitCode: 0, logPath: "log.snappyHexMesh" },
        { command: "checkMesh -allGeometry -allTopology", status: "complete", exitCode: 0, logPath: "log.checkMesh" }
      ],
      qualityMetrics: {
        failedChecks: failed ? 2 : 0,
        maxNonOrthogonality: failed ? 72.5 : 12.5,
        maxSkewness: failed ? 4.2 : 0.42,
        maxAspectRatio: failed ? 90 : 5,
        minVolume: failed ? -1e-12 : 1e-9,
        passed: !failed
      },
      yPlusEvidence: failed
        ? { status: "missing", blockingReason: "Missing y-plus or wall-distance evidence." }
        : { status: "present", min: 0.8, mean: 18.5, max: 42, sampleCount: 4, files: ["postProcessing/yPlus/0/yPlus.dat"] },
      layerSummary: { status: "present", excerptCount: 1, excerpts: ["Layer addition phase"] },
      blockingReasons: failed
        ? ["OpenFOAM checkMesh failed 2 check(s)."]
        : production
          ? []
          : ["Generated starter triSurface has not been CAD/B-rep reviewed; production approval remains blocked."]
    },
    artifacts: commonArtifacts,
    artifactLimitBytes: 120000
  };
}

async function openFresh(
  page: Page,
  meshKind: "passed" | "failed" | "missing" | "production" = "passed",
  options: { keepCinema?: boolean; runnableOpenfoam?: boolean; verifiedMultiEdgeLink?: boolean; resultMode?: "full" | "none" | "indexed" } = {}
) {
  const { runnableOpenfoam = false, verifiedMultiEdgeLink = false, resultMode = "full" } = options;
  let generatedProjectText = "";
  let generatedProjectSha256 = "";
  await page.route("**/api/health", async (route) => {
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/solvers", async (route) => {
    await route.fulfill({
      json: [{ id: "instant-1d", label: "Instant 1D hydraulics", installed: true, execution: "browser", notes: [] }]
    });
  });
  await page.route("**/api/runtime", async (route) => {
    await route.fulfill({
      json: [
        { solver: "instant-1d", runnable: true, preferredExecution: "browser", blockers: [], notes: [] },
        runnableOpenfoam
          ? {
              solver: "openfoam",
              runnable: true,
              preferredExecution: "native",
              dockerImage: "flowlab/openfoam11-gmsh:2026-07-13",
              dockerAvailable: false,
              nativeCommand: "foamRun",
              nativeAvailable: true,
              blockers: [],
              notes: []
            }
          : {
              solver: "openfoam",
              runnable: false,
              preferredExecution: "none",
              dockerImage: "flowlab/openfoam11-gmsh:2026-07-13",
              dockerAvailable: false,
              nativeCommand: "foamRun",
              nativeAvailable: false,
              blockers: ["Docker daemon is unavailable.", "Native command `foamRun` was not found on PATH."],
              notes: []
            }
      ]
    });
  });
  await page.route("**/api/cases/generate", async (route) => {
    const payload = route.request().postDataJSON() as { project?: unknown };
    generatedProjectText = JSON.stringify(payload.project ?? {}, null, 2);
    generatedProjectSha256 = createHash("sha256").update(generatedProjectText).digest("hex");
    const identityContractText = JSON.stringify({
      schema: "flowlab.source-cell-identity-contract.v1",
      orderingAssumptionAllowed: false,
      sourceCellCount: 3
    });
    const identityContractSha256 = createHash("sha256").update(identityContractText).digest("hex");
    const resultComponentMap = verifiedMultiEdgeLink
      ? {
          version: 2,
          projectSha256: generatedProjectSha256,
          artifactBindings: [
            {
              artifactName: "postProcessing/flowlabNative/*.vtk",
              scope: "cell-ranges",
              sourceCellCount: 3,
              identitySchema: "flowlab.openfoam-source-cell-identity.v1",
              identityField: "flowlabSourceCellId",
              identityContractSha256,
              cellRanges: [
                { edgeId: "inlet", cellStart: 0, cellCount: 1 },
                { edgeId: "outlet", cellStart: 2, cellCount: 1 }
              ],
              unownedCellRanges: [
                {
                  artifactId: "generated:y-junction:junction-core:v1",
                  cellStart: 1,
                  cellCount: 1,
                  schematicOwner: null
                }
              ]
            }
          ]
        }
      : null;
    await route.fulfill({
      json: {
        id: "case-openfoam-e2e",
        projectName: "Venturi Cavitation Lab",
        solver: "openfoam",
        advancedMode: "incompressible-navier-stokes",
        status: "generated",
        files: {
          ...(resultMode !== "full"
            ? {
                "flowlab_project.json": generatedProjectText,
                "mesh/flowlab_mesh.vtk": generatedVolumeMesh
              }
            : {}),
          ...(verifiedMultiEdgeLink
            ? {
              "flowlab_project.json": generatedProjectText,
              "constant/flowlab_result_identity_contract.json": identityContractText,
              "flowlab_case_manifest.json": JSON.stringify({
                files: {
                  "flowlab_project.json": { sha256: generatedProjectSha256 },
                  "constant/flowlab_result_identity_contract.json": { sha256: identityContractSha256 }
                },
                resultComponentMap
              })
            }
            : {})
        },
        runCommand: ["bash", "Allrun"],
        provenance: [],
        resultComponentMap
      }
    });
  });
  await page.route("**/api/jobs", async (route) => {
    await route.fulfill({
      json: {
        id: "job-openfoam-e2e",
        caseId: "case-openfoam-e2e",
        solver: "openfoam",
        status: resultMode === "none" ? "running" : "complete",
        createdAt: "2026-06-11T00:00:00Z",
        updatedAt: "2026-06-11T00:00:02Z",
        finishedAt: resultMode === "none" ? undefined : "2026-06-11T00:00:02Z",
        caseDir: "/tmp/flowlab/case",
        execution: "native",
        command: ["bash", "Allrun"],
        logs: ["Time = 0.002", "Solver process exited successfully with code 0."],
        exitCode: resultMode === "none" ? undefined : 0,
        result: resultMode === "none"
          ? null
          : resultMode === "indexed"
            ? {
                caseDir: "/tmp/flowlab/case",
                resultFiles: [],
                diagnosticFiles: [],
                progressive: false
              }
            : {
          caseDir: "/tmp/flowlab/case",
          exitCode: 0,
          logsCaptured: 5,
          logSummary: {
            solver: "openfoam",
            lineCount: 5,
            lastLines: ["Time = 0.002"],
            latestTime: 0.002,
            residuals: {
              Ux: { initial: 0.12, final: 0.0000075, iterations: 2 },
              p: { initial: 0.4, final: 0.00009, iterations: 3 }
            },
            warnings: ["WARNING: Courant number adjusted"]
          },
          resultFiles: [
            {
              path: "postProcessing/flowlabNative/time_0_002.vtk",
              size: (verifiedMultiEdgeLink ? multiEdgeOpenFoamResult : nativeOpenFoamResult).length,
              text: verifiedMultiEdgeLink ? multiEdgeOpenFoamResult : nativeOpenFoamResult,
              time: 0.002,
              timeText: "0.002",
              timeSource: "openfoam-time-directory",
              sourceFields: ["U", "p"]
            }
          ],
          diagnosticFiles: [{ path: "postProcessing/residuals/0/residuals.dat", size: 32, text: "Time Ux p" }],
          diagnosticSummary: [
            {
              path: "postProcessing/residuals/0/residuals.dat",
              kind: "residuals",
              columns: ["Time", "Ux", "p"],
              rowCount: 2,
              latest: { Time: 0.002, Ux: 0.0000075, p: 0.00009 }
            }
          ],
          meshQuality: meshQualityFixture(meshKind),
          patchMetrics: patchMetricsFixture,
          progressive: false
        }
      }
    });
  });
  await page.route("**/api/jobs/job-openfoam-e2e/artifacts?**", async (route) => {
    await route.fulfill({
      json: {
        artifacts: [{
          path: "postProcessing/flowlabNative/thinned-only.vtk",
          size: nativeOpenFoamResult.length,
          kind: "result",
          fieldSummary: {
            schema: "flowlab.result_field_summary.v1",
            format: "legacy-vtk-ascii-v1",
            pointCount: 4,
            cellCount: 1,
            fields: [
              { name: "p", location: "cell", kind: "scalar", tupleCount: 1, min: 101325, max: 101325, mean: 101325 },
              { name: "U", location: "cell", kind: "vector-magnitude", tupleCount: 1, min: 1.5, max: 1.5, mean: 1.5 }
            ]
          }
        }],
        count: 1,
        truncated: false
      }
    });
  });
  await page.route("**/api/jobs/job-openfoam-e2e/artifact/preview?**", async (route) => {
    await route.fulfill({
      json: {
        path: "postProcessing/flowlabNative/thinned-only.vtk",
        size: nativeOpenFoamResult.length,
        schema: "flowlab.result_preview.v1",
        format: "legacy-vtk-ascii-v1",
        sourcePointCount: 8,
        sourceCellCount: 2,
        pointCount: 4,
        cellCount: 1,
        pointLimit: 500,
        cellLimit: 500,
        truncated: true,
        pointIndices: [0, 1, 2, 3],
        cellIndices: [0],
        points: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
        cells: [[0, 1, 2, 3]],
        cellTypes: [9],
        fieldSummary: {
          schema: "flowlab.result_field_summary.v1",
          format: "legacy-vtk-ascii-v1",
          pointCount: 4,
          cellCount: 1,
          fields: [
            { name: "p", location: "cell", kind: "scalar", tupleCount: 1, min: 101325, max: 101325, mean: 101325 },
            { name: "U", location: "cell", kind: "vector-magnitude", tupleCount: 1, min: 1.5, max: 1.5, mean: 1.5 }
          ]
        },
        fieldSamples: {
          point: [],
          cell: [
            { name: "p", kind: "scalar", values: [101325] },
            { name: "U", kind: "vector", values: [[1.5, 0, 0]], magnitudes: [1.5] }
          ]
        }
      }
    });
  });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.localStorage.clear());
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("schematic-canvas")).toBeVisible();
  await expect(page.getByTestId("cinema-canvas")).toBeVisible();
  await page.getByRole("navigation", { name: "FlowLab workflow stages" }).getByRole("button", { name: /Define/ }).click();
}

async function showStage(page: Page, stage: "Define" | "Estimate" | "CFD" | "Inspect") {
  await page.getByRole("navigation", { name: "FlowLab workflow stages" }).getByRole("button", { name: new RegExp(stage) }).click();
}

async function loadFixtureResult(page: Page) {
  await showStage(page, "Inspect");
  await page.getByRole("button", { name: /Load fixture result/i }).click();
}

async function installDerivedVisualizationRoutes(page: Page, linked: boolean) {
  const payloads: Record<string, Buffer> = {
    "values-000.bin": Buffer.from(new Float32Array([0, 1, 0, 1, 0, 1, 0, 1]).buffer),
    "validity.bin": Buffer.from(new Uint8Array(8).fill(1).buffer),
    "source-cell-ids.bin": Buffer.from(new Uint32Array(8).fill(0).buffer),
    "subcell-ids.bin": Buffer.from(new Uint8Array(8).buffer),
    "ambiguity.bin": Buffer.from(new Uint8Array(8).buffer),
    "spatial-weights.bin": Buffer.from(new Float32Array(32).fill(0.25).buffer)
  };
  const descriptor = (name: string, dtype: "float32" | "uint32" | "uint8", components: number, count: number) => ({
    schema: "flowlab.derived_visualization_blob.v1",
    name,
    dtype,
    components,
    count,
    byteOrder: "little-endian",
    byteLength: payloads[name].byteLength,
    sha256: createHash("sha256").update(payloads[name]).digest("hex")
  });
  const descriptors = [
    descriptor("values-000.bin", "float32", 1, 8),
    descriptor("validity.bin", "uint8", 1, 8),
    descriptor("source-cell-ids.bin", "uint32", 1, 8),
    descriptor("subcell-ids.bin", "uint8", 1, 8),
    descriptor("ambiguity.bin", "uint8", 1, 8),
    descriptor("spatial-weights.bin", "float32", 4, 8)
  ];
  const byName = Object.fromEntries(descriptors.map((entry) => [entry.name, entry]));
  const requestSha256 = "a".repeat(64);
  const manifest = {
    schema: "flowlab.derived_visualization_manifest.v1",
    requestSchema: "flowlab.derived_visualization_request.v1",
    requestSha256,
    manifestSha256: "b".repeat(64),
    operation: "volume",
    visualizationOnly: true,
    scientificStateEffect: "none",
    releaseStateEffect: "none",
    unitAuthority: linked ? "case-contract" : "user-declared",
    sourceArtifacts: [
      {
        path: linked ? "postProcessing/flowlabNative/time_0_002.vtk" : "venturi-result.vtk",
        time: 0.002,
        size: 100,
        sha256: "c".repeat(64),
        geometryDigest: "d".repeat(64),
        cellOrderDigest: "e".repeat(64)
      }
    ],
    componentResolution: {
      status: linked ? "source-cell-map" : "probe-only",
      reason: linked ? "explicit generated map" : "imported",
      map: linked
        ? {
            version: 2,
            projectSha256: "f".repeat(64),
            artifactBindings: [{
              artifactName: "postProcessing/flowlabNative/*.vtk",
              scope: "cell-ranges",
              sourceCellCount: 3,
              identitySchema: "flowlab.openfoam-source-cell-identity.v1",
              identityField: "flowlabSourceCellId",
              identityContractSha256: "1".repeat(64),
              cellRanges: [
                { edgeId: "inlet", cellStart: 0, cellCount: 1 },
                { edgeId: "outlet", cellStart: 2, cellCount: 1 }
              ],
              unownedCellRanges: [{
                artifactId: "generated:test:unowned:v1",
                cellStart: 1,
                cellCount: 1,
                schematicOwner: null
              }]
            }]
          }
        : null
    },
    limits: {
      defaultGridDimension: 64,
      maxGridDimension: 96,
      artifactSetBytes: 48 * 1024 * 1024,
      browserResidencyBytes: 96 * 1024 * 1024,
      derivedCacheBytesPerJob: 256 * 1024 * 1024,
      maxSeeds: 512,
      maxPathlineVertices: 250000,
      maxIsoTriangles: 500000,
      overflowBehavior: "reject"
    },
    grid: {
      dimensions: [2, 2, 2],
      voxelCount: 8,
      bounds: { min: [0, 0, 0], max: [1, 1, 1] },
      spacing: [0.5, 0.5, 0.5],
      sampleLocation: "voxel-center"
    },
    provenance: {
      validity: byName["validity.bin"],
      sourceCellIds: byName["source-cell-ids.bin"],
      subcellIds: byName["subcell-ids.bin"],
      ambiguity: byName["ambiguity.bin"],
      spatialWeights: byName["spatial-weights.bin"],
      invalidSourceCellId: 0xffffffff,
      ambiguousSelections: "probe-only"
    },
    fields: [
      {
        name: linked ? "U" : "pressure",
        location: linked ? "cell" : "point",
        kind: "scalar",
        unit: linked ? "m/s" : "Pa",
        values: byName["values-000.bin"],
        validity: byName["validity.bin"]
      }
    ],
    gradients: [],
    blobs: descriptors,
    browserResidencyBytes: Object.values(payloads).reduce((sum, payload) => sum + payload.byteLength, 0)
  };
  await page.route("**/api/derived/import", async (route) => route.fulfill({ json: manifest }));
  await page.route("**/api/jobs/*/derived", async (route) => route.fulfill({ json: manifest }));
  await page.route("**/blob/*.bin", async (route) => {
    const name = new URL(route.request().url()).pathname.split("/").at(-1)!;
    await route.fulfill({ status: 200, contentType: "application/octet-stream", body: payloads[name] });
  });
}

async function waitProjectSaved(page: Page) {
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
    .toContain('"version":1');
}

async function switchToSchematic(page: Page) {
  await expect(page.getByTestId("schematic-canvas")).toBeVisible();
}

async function worldToScreen(page: Page, point: { x: number; y: number }) {
  await waitProjectSaved(page);
  await switchToSchematic(page);
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      })
  );
  return page.evaluate((worldPoint) => {
    const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="schematic-canvas"]');
    if (!canvas) throw new Error("Canvas missing.");
    const rect = canvas.getBoundingClientRect();
    const scale = Number(canvas.dataset.viewScale ?? 1);
    const offset = {
      x: Number(canvas.dataset.viewOffsetX ?? 0),
      y: Number(canvas.dataset.viewOffsetY ?? 0)
    };
    return {
      x: rect.left + offset.x + worldPoint.x * scale,
      y: rect.top + offset.y + worldPoint.y * scale
    };
  }, point);
}

async function dragCanvas(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  const start = await worldToScreen(page, from);
  const end = await worldToScreen(page, to);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
}

test.describe("FlowLab editor workspace", () => {
  test("wires the bounded full O-grid controls into the generated product request", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true });
    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByLabel("Mesh mode").selectOption("full-ogrid");

    await expect(page.getByLabel("Run mode")).toHaveValue("steady");
    await expect(page.getByLabel("Full O-grid axial cells")).toHaveValue("16");
    await expect(page.getByLabel("Full O-grid annular radial cells")).toHaveValue("4");
    await expect(page.getByLabel("Full O-grid circumferential cells")).toHaveValue("32");
    await expect(page.getByLabel("Full O-grid core cells per side")).toHaveValue("8");

    await page.getByLabel("Mesh resolution").selectOption("medium");
    await expect(page.getByLabel("Full O-grid axial cells")).toHaveValue("32");
    await expect(page.getByLabel("Full O-grid annular radial cells")).toHaveValue("8");
    await expect(page.getByLabel("Full O-grid circumferential cells")).toHaveValue("64");
    await expect(page.getByLabel("Full O-grid core cells per side")).toHaveValue("16");

    const generatedRequest = page.waitForRequest((request) => request.url().endsWith("/api/cases/generate"));
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    const request = await generatedRequest;
    const payload = request.postDataJSON() as {
      project: {
        solver: {
          meshMode?: string;
          runMode?: string;
          turbulence?: string;
          meshControls?: Record<string, number>;
        };
      };
    };
    expect(payload.project.solver).toMatchObject({
      meshMode: "full-ogrid",
      runMode: "steady",
      turbulence: "laminar",
      meshControls: {
        fullOGridAxialCells: 32,
        fullOGridAnnularRadialCells: 8,
        fullOGridCircumferentialCells: 64,
        fullOGridCoreCellsPerSide: 16
      }
    });
  });

  test("wires the canonical elbow preset and its frozen 3D scope into the product request", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true });
    await page.getByLabel("Preset").selectOption("Canonical 90° Elbow (Experimental)");
    await showStage(page, "CFD");

    await expect(page.getByRole("combobox", { name: "Solver" })).toHaveValue("openfoam");
    await expect(page.getByLabel("Mesh mode")).toHaveValue("curved-elbow-ogrid");
    await expect(page.getByLabel("Run mode")).toHaveValue("steady");
    await expect(page.getByLabel("Curved-elbow inlet axial cells")).toHaveValue("28");
    await expect(page.getByLabel("Curved-elbow bend axial cells")).toHaveValue("16");
    await expect(page.getByLabel("Curved-elbow outlet axial cells")).toHaveValue("28");
    await expect(page.getByLabel("Curved-elbow annular radial cells")).toHaveValue("2");
    await expect(page.getByLabel("Curved-elbow circumferential cells")).toHaveValue("16");
    await expect(page.getByLabel("Curved-elbow core cells per side")).toHaveValue("4");

    const generatedRequest = page.waitForRequest((request) => request.url().endsWith("/api/cases/generate"));
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    const request = await generatedRequest;
    const payload = request.postDataJSON() as {
      project: {
        solver: {
          meshMode?: string;
          runMode?: string;
          turbulence?: string;
          meshControls?: Record<string, number>;
          curvedElbowVerification?: Record<string, number | string>;
        };
      };
    };
    expect(payload.project.solver).toMatchObject({
      meshMode: "curved-elbow-ogrid",
      runMode: "steady",
      turbulence: "laminar",
      meshControls: {
        curvedElbowInletAxialCells: 28,
        curvedElbowBendAxialCells: 16,
        curvedElbowOutletAxialCells: 28,
        curvedElbowAnnularRadialCells: 2,
        curvedElbowCircumferentialCells: 16,
        curvedElbowCoreCellsPerSide: 4
      },
      curvedElbowVerification: {
        contractId: "canonical-circular-elbow-re100-v2",
        boundaryCondition: "fully-developed-parabolic-inlet-pressure-outlet",
        diameterM: 0.01,
        centrelineRadiusM: 0.03,
        inletLegLengthM: 0.1,
        outletLegLengthM: 0.1,
        bendAngleDegrees: 90
      }
    });
  });

  test("renders the linked 3D canvas with WebGL primitives and shared selection", async ({ page }) => {
    test.setTimeout(45_000);
    await openFresh(page, "passed", { keepCinema: true });
    await showStage(page, "Inspect");
    await page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: "Field viewer" }).click();

    const canvas = page.getByTestId("cinema-canvas");
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.canvasRenderMode)).toBe("cinema");
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.cinemaObjectCount ?? 0))).toBeGreaterThan(10);
    await page.getByLabel("3D view controls").getByRole("button", { name: "Top" }).click();
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.cinemaCameraPitch)).toBe("76");

    const sourcePoint = await canvas.evaluate((element) => {
      const positions = JSON.parse((element as HTMLCanvasElement).dataset.cinemaNodePositions ?? "{}") as Record<string, { x: number; y: number }>;
      const rect = element.getBoundingClientRect();
      const point = positions.source;
      if (!point) throw new Error("Source projection missing.");
      return { x: rect.left + point.x, y: rect.top + point.y };
    });
    await page.mouse.click(sourcePoint.x, sourcePoint.y);
    await expect.poll(() => canvas.getAttribute("data-selected-id")).not.toBe("");
    await expect(page.getByTestId("schematic-canvas")).toHaveAttribute(
      "data-selected-id",
      await canvas.getAttribute("data-selected-id") ?? ""
    );

    await loadFixtureResult(page);
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.resultViewMode)).toBe("3d");
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.canvasRenderMode)).toBe("cinema");
    await expect(page.getByText(/Using pressure from venturi-result\.vtk/)).toBeVisible();
  });

  test("governs concept and generated-case preview states", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true, resultMode: "none" });
    await showStage(page, "Define");
    await expect(page.getByText("Concept preview", { exact: true }).first()).toBeVisible();

    await showStage(page, "Estimate");
    await expect(page.getByRole("button", { name: "Illustrative estimate animation—not CFD" })).toHaveAttribute("aria-pressed", "false");

    await showStage(page, "CFD");
    await page.getByLabel("Mesh mode").selectOption("full-ogrid");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await expect(page.getByText("Generated-case mesh preview", { exact: true }).first()).toBeVisible();
  });

  test("promotes the full solver-produced mesh over generated previews", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true });
    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await expect(page.getByText("Solver-produced mesh", { exact: true }).first()).toBeVisible();
  });

  test("governs thinned, imported, 2D fallback, and fixture preview states", async ({ page }) => {
    test.setTimeout(45_000);
    await openFresh(page, "passed", { runnableOpenfoam: true, resultMode: "indexed" });
    await showStage(page, "CFD");
    await page.getByLabel("Mesh mode").selectOption("full-ogrid");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await page.getByRole("button", { name: "Index field files" }).click();
    await page.getByLabel("Indexed result artifacts").getByRole("button", { name: "Preview" }).click();
    await expect(page.getByText("Thinned artifact preview — surface only", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Derive" })).toBeDisabled();

    await showStage(page, "Inspect");
    await page.getByTestId("result-import-file").setInputFiles("public/fixtures/venturi-result.vtk");
    await expect(page.getByText("Imported result — probe only", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Use 2D projection fallback" }).click();
    await expect(page.getByText("2D projection fallback — WebGL/accessibility/export", { exact: true })).toBeVisible();
    await expect(page.getByTestId("cinema-canvas")).toHaveAttribute("data-canvas-render-mode", "projection");

    await page.getByLabel("Examples / Developer tooling").getByRole("button", { name: "Load fixture result" }).click();
    await expect(page.getByText("Fixture result — developer example · probe only", { exact: true }).first()).toBeVisible();
  });

  test("rejects steady streamline derivation for imported probe-only results", async ({ page }) => {
    await openFresh(page, "passed", { keepCinema: true });
    await loadFixtureResult(page);
    const controls = page.getByLabel("Steady streamline controls");
    await expect(controls).toContainText("Deterministic RK4 through loaded U(x,y,z)");
    await expect(controls).toContainText("passive animation, not transient pathlines");
    await expect(controls.getByRole("button", { name: "Derive" })).toBeDisabled();
    await expect(controls.getByRole("button", { name: "Automatic inlet seeds" })).toBeDisabled();
  });

  test("keeps max-seed governed streamline cinema frames within budget", async ({ page }) => {
    test.setTimeout(90_000);
    await openFresh(page, "passed", { keepCinema: true, runnableOpenfoam: true, verifiedMultiEdgeLink: true });
    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await showStage(page, "Inspect");
    const controls = page.getByLabel("Steady streamline controls");
    await controls.getByLabel("Streamline seed count").selectOption("256");
    await controls.getByRole("button", { name: "Derive" }).click();
    await expect(page.getByTestId("streamline-status")).toContainText("256 steady streamlines");

    await page.evaluate(() => window.__flowlabEditorPerformance?.reset());
    // Collect frames until there are enough samples for a meaningful p95, rather
    // than sampling a fixed wall-clock window. A CI runner has no GPU and renders
    // through software, so a fixed 750 ms window made the required sample count
    // depend on machine speed: it produced 9 frames where the test asks for more
    // than 10, and the frame-time budget below never got to run. Waiting on the
    // sample count keeps that budget unchanged and still fails if rendering
    // genuinely stalls.
    await page.waitForFunction(
      () => (window.__flowlabEditorPerformance?.get()?.counts["cinema-frame"] ?? 0) > 10,
      undefined,
      { timeout: 30_000 }
    );
    const performanceSnapshot = await page.evaluate(() => window.__flowlabEditorPerformance?.get());
    expect(performanceSnapshot).toBeTruthy();
    await test.info().attach("streamline-cinema-performance.json", {
      body: Buffer.from(JSON.stringify(performanceSnapshot, null, 2)),
      contentType: "application/json"
    });
    expect(performanceSnapshot?.counts["cinema-frame"]).toBeGreaterThan(10);
    expect(performanceSnapshot?.p95["cinema-frame"]).toBeLessThan(16);
  });

  test("selects multi-edge generated results only through verified source-cell provenance", async ({ page }) => {
    test.setTimeout(120_000);
    await openFresh(page, "passed", { runnableOpenfoam: true, verifiedMultiEdgeLink: true });
    await page.getByRole("button", { name: /^Nodes \(3\)$/ }).click();
    await expect(page.getByTestId("schematic-canvas")).toHaveAttribute("data-selected-id", "source");

    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await showStage(page, "Inspect");
    await expect(page.getByText(/Verified per-cell case link/i).first()).toBeVisible();

    const cinema = page.getByTestId("cinema-canvas");
    const schematic = page.getByTestId("schematic-canvas");
    const box = await cinema.boundingBox();
    if (!box) throw new Error("Cinema canvas bounds missing.");

    const linkedEdges = new Set<string>();
    for (const yFraction of [0.35, 0.45, 0.55, 0.65]) {
      for (let xFraction = 0.2; xFraction <= 0.8; xFraction += 0.05) {
        await page.mouse.click(box.x + box.width * xFraction, box.y + box.height * yFraction);
        const selectedId = await schematic.getAttribute("data-selected-id");
        if (selectedId === "inlet" || selectedId === "outlet") linkedEdges.add(selectedId);
        if (linkedEdges.size === 2) break;
      }
      if (linkedEdges.size === 2) break;
    }

    expect(linkedEdges).toEqual(new Set(["inlet", "outlet"]));
    await expect(cinema).toHaveAttribute("data-selected-id", await schematic.getAttribute("data-selected-id") ?? "");
  });

  test("keeps viewport gestures, visible camera actions, and result modes coherent", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true });

    const canvas = page.getByTestId("schematic-canvas");
    const viewportActions = page.getByTestId("schematic-pane").getByLabel("Viewport controls");
    await expect(viewportActions).toBeVisible();
    const canvasBox = await canvas.boundingBox();
    if (!canvasBox) throw new Error("Canvas bounds missing.");

    const initialViewport = await canvas.evaluate((element) => ({
      zoom: Number((element as HTMLCanvasElement).dataset.viewScale ?? 1),
      panX: Number((element as HTMLCanvasElement).dataset.viewOffsetX ?? 0),
      panY: Number((element as HTMLCanvasElement).dataset.viewOffsetY ?? 0)
    }));
    await page.mouse.move(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
    await page.mouse.wheel(0, -240);
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.viewScale ?? 1))).toBeGreaterThan(initialViewport.zoom);

    await page.mouse.move(canvasBox.x + 24, canvasBox.y + 24);
    await page.mouse.down();
    await page.mouse.move(canvasBox.x + 64, canvasBox.y + 48, { steps: 6 });
    await page.mouse.up();
    await expect
      .poll(() => canvas.evaluate((element) => ({
        panX: Number((element as HTMLCanvasElement).dataset.viewOffsetX ?? 0),
        panY: Number((element as HTMLCanvasElement).dataset.viewOffsetY ?? 0)
      })))
      .not.toEqual({ panX: initialViewport.panX, panY: initialViewport.panY });

    await viewportActions.getByRole("button", { name: "Reset viewport" }).click();
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.viewScale ?? 1))).toBe(1);
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.viewOffsetX ?? 0))).toBe(0);
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.viewOffsetY ?? 0))).toBe(0);

    await viewportActions.getByRole("button", { name: "Fit viewport" }).click();
    await viewportActions.getByRole("button", { name: "Reset viewport" }).click();

    await loadFixtureResult(page);
    await expect.poll(() => page.getByTestId("cinema-canvas").evaluate((element) => (element as HTMLCanvasElement).dataset.resultViewMode)).toBe("3d");
  });

  test("keeps illustrative estimate motion opt-in and separate from model undo", async ({ page }) => {
    await openFresh(page);
    await showStage(page, "Estimate");
    const canvas = page.getByTestId("schematic-canvas");
    await canvas.focus();
    const illustration = page.getByRole("button", { name: "Illustrative estimate animation—not CFD" });
    await expect(illustration).toHaveAttribute("aria-pressed", "false");
    const pausedPhase = await canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.previewPhase);
    await page.waitForTimeout(120);
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.previewPhase)).toBe(pausedPhase);

    await illustration.click();
    await expect(illustration).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => canvas.evaluate((element) => (element as HTMLCanvasElement).dataset.previewPhase)).not.toBe(pausedPhase);

    await showStage(page, "Define");
    await page.getByTitle("Add pump").click();
    await expect(page.getByRole("heading", { name: "Pump" })).toBeVisible();
    await expect(page.getByTestId("cinema-canvas")).toHaveAttribute("data-selected-id", "pump-4");
    await expect(page.getByRole("button", { name: "Undo" })).toBeEnabled();
    await page.keyboard.press("Control+z");
    await expect(page.getByRole("heading", { name: "Pump" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Redo" })).toBeEnabled();
    await page.keyboard.press("Control+Shift+z");
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"pump-4"');
  });

  test("keeps 3D camera drags free of scene rebuilds and within the interaction budget", async ({ page }) => {
    test.setTimeout(45_000);
    await openFresh(page, "passed", { keepCinema: true });
    const canvas = page.getByTestId("cinema-canvas");
    await expect.poll(() => canvas.evaluate((element) => Number((element as HTMLCanvasElement).dataset.cinemaObjectCount ?? 0))).toBeGreaterThan(10);

    const canvasBox = await canvas.boundingBox();
    if (!canvasBox) throw new Error("Cinema canvas bounds missing.");
    const cameraStart = { x: canvasBox.x + 24, y: canvasBox.y + 24 };
    await page.evaluate(() => window.__flowlabEditorPerformance?.reset());
    await page.mouse.move(cameraStart.x, cameraStart.y);
    await page.mouse.down();
    await page.mouse.move(cameraStart.x + 36, cameraStart.y + 16, { steps: 12 });
    await page.mouse.up();
    await page.waitForTimeout(120);

    const performanceSnapshot = await page.evaluate(() => window.__flowlabEditorPerformance?.get());
    expect(performanceSnapshot).toBeTruthy();
    await test.info().attach("editor-performance.json", {
      body: Buffer.from(JSON.stringify(performanceSnapshot, null, 2)),
      contentType: "application/json"
    });
    expect(performanceSnapshot?.counts["cinema-build"]).toBe(0);
    expect(performanceSnapshot?.p95["pointer-update"]).toBeLessThan(16);
    expect(performanceSnapshot?.p95["cinema-frame"]).toBeLessThan(16);
    expect(performanceSnapshot?.droppedFrames).toBeLessThan(performanceSnapshot?.counts["cinema-frame"] ?? 1);
  });

  test("keeps the compact desktop layout contained at 1100 pixels", async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 800 });
    await openFresh(page);
    const layout = await page.evaluate(() => {
      const dock = document.querySelector<HTMLElement>(".bottom-dock");
      const header = document.querySelector<HTMLElement>(".top-hud");
      return {
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        dockWidth: dock?.scrollWidth ?? 0,
        dockClientWidth: dock?.clientWidth ?? 0,
        headerWidth: header?.scrollWidth ?? 0,
        headerClientWidth: header?.clientWidth ?? 0,
        overflow: [...document.querySelectorAll<HTMLElement>("*")]
          .map((element) => ({ tag: element.tagName, className: element.className.toString(), right: Math.ceil(element.getBoundingClientRect().right) }))
          .filter((element) => element.right > window.innerWidth + 2)
      };
    });
    expect(layout.overflow).toEqual([]);
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
    expect(layout.dockWidth).toBeLessThanOrEqual(layout.dockClientWidth);
    expect(layout.headerWidth).toBeLessThanOrEqual(layout.headerClientWidth);
    await expect(page.getByRole("navigation", { name: "Workspace panels" })).toBeVisible();
    await expect(page.getByTestId("schematic-canvas")).toBeVisible();
    await expect(page.getByTestId("cinema-canvas")).toBeVisible();
  });

  test("creates, drags, rotates, aims, and deletes a component", async ({ page }) => {
    await openFresh(page);

    await page.getByTitle("Add pump").click();
    await expect(page.getByRole("heading", { name: "Pump" })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain("pump-4");

    // Drops snap to the schematic grid, and then to the nearest free cell when the
    // requested one would crowd another component: (396, 230) rounds to (400, 240),
    // which is too close to the throat at (420, 260), so the pump takes the next cell.
    await dragCanvas(page, { x: 336, y: 180 }, { x: 396, y: 230 });
    await expect(page.getByLabel("X px value")).toHaveValue("360");
    await expect(page.getByLabel("Y px value")).toHaveValue("200");

    await page.getByTestId("rotation-degrees-input").fill("45");
    await expect(page.getByTestId("rotation-degrees-input")).toHaveValue("45");
    await page.getByLabel("Aim presets").getByRole("button", { name: "South" }).click();
    await expect(page.getByTestId("rotation-degrees-input")).toHaveValue("90");

    await page.getByLabel("Selected component actions").getByRole("button", { name: /delete/i }).click();
    await expect(page.getByRole("heading", { name: "Pump" })).toHaveCount(0);
  });

  test("creates a pipe by dragging between ports and edits its endpoint", async ({ page }) => {
    await openFresh(page);

    await dragCanvas(page, { x: 120, y: 233 }, { x: 720, y: 233 });
    await expect(page.getByRole("heading", { name: "Pipe 3" })).toBeVisible();
    await expect(page.getByLabel("To node")).toHaveValue("sink");

    await page.getByLabel("To node").selectOption("throat");
    await expect(page.getByLabel("To node")).toHaveValue("throat");
    await expect(page.getByText(/Pressure drop/)).toBeVisible();
  });

  test("persists project edits through reload and rejects invalid imports", async ({ page }) => {
    await openFresh(page);
    await page.getByTitle("Add sink").click();

    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain("sink-4");

    await page.reload();
    await expect(page.getByText("Restored saved project.")).toBeVisible();

    await page.getByTestId("project-import-file").setInputFiles({
      name: "invalid.flowlab.json",
      mimeType: "application/json",
      buffer: Buffer.from("{}")
    });
    await expect(page.getByText(/Invalid project:/)).toBeVisible();
  });

  test("shows parsed solver progress after queueing an advanced job", async ({ page }) => {
    await openFresh(page, "passed", { runnableOpenfoam: true });
    await showStage(page, "CFD");

    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();

    await expect(page.getByText("Job complete")).toBeVisible();
    await expect(page.getByText(/Final artifacts: 1 field, 1 diagnostic/)).toBeVisible();
    await expect(page.getByLabel("Diagnostic table summary")).toContainText("residuals");
    await expect(page.getByLabel("Diagnostic table summary")).toContainText("Ux 7.50e-6");
    await expect(page.getByLabel("Solver progress summary")).toContainText("Time");
    await expect(page.getByLabel("Solver progress summary")).toContainText("0.002");
    await expect(page.getByLabel("Residual summary")).toContainText("Ux: 7.50e-6");
    await expect(page.getByLabel("Residual summary")).toContainText("p: 9.00e-5");
    await expect(page.getByLabel("Mesh QA panel")).toContainText("Native passed");
    await expect(page.getByLabel("Native mesh command list")).toContainText("snappyHexMesh -overwrite");
    await expect(page.getByLabel("checkMesh metrics")).toContainText("12.5");
    await expect(page.getByLabel("Y plus evidence")).toContainText("max 42");
    await expect(page.getByLabel("Mesh QA blockers")).toContainText("CAD/B-rep reviewed");
    await expect(page.getByRole("slider", { name: "Result timestep" })).toHaveAttribute(
      "aria-valuetext",
      "t0.002 postProcessing/flowlabNative/time_0_002.vtk"
    );
    await expect(page.getByText(/Using U from postProcessing\/flowlabNative\/time_0_002\.vtk/)).toBeVisible();
    await expect(page.getByLabel("OpenFOAM patch metrics")).toContainText("Patch Metrics");
    await expect(page.getByLabel("Patch flow balance")).toContainText("in 0.012 / out 0.0118 m3/s");
    await expect(page.getByLabel("Patch pressure drops")).toContainText("2.325 kPa");
    await expect(page.getByLabel("Wall shear metrics")).toContainText("mean 1.1 Pa");
    await expect(page.getByLabel("Integrated force metrics")).toContainText("|F| 4.1158 N");
    await expect(page.getByLabel("Patch metric warnings")).toContainText("partial");
    await expect(page.getByText("1 warning")).toBeVisible();
  });

  test("imports reviewed multi-surface STL geometry and shows production-ready mesh QA", async ({ page }) => {
    await openFresh(page, "production", { runnableOpenfoam: true });
    await page.getByRole("navigation", { name: "Workspace panels" }).getByRole("button", { name: "Mesh QA" }).click();

    await expect(page.getByLabel("Reviewed geometry controls")).toContainText("Generated starter");
    await page.getByTestId("reviewed-stl-file").setInputFiles([
      {
        name: "inlet-cap.stl",
        mimeType: "model/stl",
        buffer: Buffer.from(
          [
            "solid inletCap",
            "  facet normal 0 0 1",
            "    outer loop",
            "      vertex 0 0 0",
            "      vertex 1 0 0",
            "      vertex 0 1 0",
            "    endloop",
            "  endfacet",
            "endsolid inletCap"
          ].join("\n")
        )
      },
      {
        name: "outlet-cap.stl",
        mimeType: "model/stl",
        buffer: Buffer.from(
          [
            "solid outletCap",
            "  facet normal 0 0 1",
            "    outer loop",
            "      vertex 2 0 0",
            "      vertex 3 0 0",
            "      vertex 2 1 0",
            "    endloop",
            "  endfacet",
            "endsolid outletCap"
          ].join("\n")
        )
      },
      {
        name: "main-wall.stl",
        mimeType: "model/stl",
        buffer: Buffer.from(
          [
            "solid mainWall",
            "  facet normal 0 0 1",
            "    outer loop",
            "      vertex 0 0 1",
            "      vertex 1 0 1",
            "      vertex 0 1 1",
            "    endloop",
            "  endfacet",
            "endsolid mainWall"
          ].join("\n")
        )
      }
    ]);

    await expect(page.getByLabel("Reviewed geometry controls")).toContainText("3 imported surfaces");
    await expect(page.getByLabel("Reviewed STL surface table")).toContainText("inlet cap");
    await expect(page.getByLabel("Reviewed STL surface table")).toContainText("outlet cap");
    await expect(page.getByLabel("Reviewed STL surface table")).toContainText("main wall");
    await expect(page.getByLabel("STL metadata")).toContainText("Triangles");
    await expect(page.getByLabel("STL metadata")).toContainText("1");
    await expect(page.getByLabel("STL metadata")).toContainText("open");
    await expect(page.getByLabel("STL preview")).toBeVisible();
    await expect(page.getByText("Imported STL preview — setup geometry only", { exact: true })).toBeVisible();
    await expect(page.getByText(/not solver-produced result evidence/i)).toBeVisible();
    const boundaryConditionStatus = page
      .getByLabel("Reviewed STL surface table")
      .getByText(/BC|boundary condition|unset|required|ready/i);
    if ((await boundaryConditionStatus.count()) > 0) {
      await expect(page.getByLabel("Reviewed STL surface table")).toContainText(/BC|boundary condition/i);
      await expect(page.getByLabel("Reviewed STL surface table")).toContainText(/unset|required|ready/i);
    } else {
      test.info().annotations.push({
        type: "pending-contract",
        description: "Reviewed STL surface rows should expose per-surface OpenFOAM boundary-condition status."
      });
    }
    await page.getByLabel("inlet cap patch name").fill("inletPatch");
    await page.getByLabel("outlet cap patch name").fill("outletPatch");
    await page.getByLabel("main wall patch name").fill("wallPatch");
    await expect(page.getByLabel("Reviewed STL surface table")).toContainText("BC ready");
    await page.getByLabel("inlet cap boundary condition").selectOption("mass-flow-inlet");
    await page.getByLabel("inlet cap mass flow rate").fill("2.5");
    await page.getByLabel("outlet cap boundary condition").selectOption("pressure-outlet");
    await page.getByLabel("outlet cap pressure").fill("0");
    await page.getByLabel("main wall boundary condition").selectOption("temperature-wall");
    await page.getByLabel("main wall temperature").fill("315");
    await page.getByLabel("inlet cap reviewed").check();
    await page.getByLabel("outlet cap reviewed").check();
    await page.getByLabel("main wall reviewed").check();
    await page.getByLabel("main wall notes").fill("Reviewed against CAD rev B before native mesh QA.");
    await expect(page.getByLabel("Reviewed STL surface table")).toContainText("Reviewed inlet, outlet, and wall surfaces are ready");
    await page.getByLabel("Preview outlet cap").click();
    await expect(page.getByLabel("STL preview")).toBeVisible();

    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"surfaces"');
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"surfaceName":"inlet cap"');
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"patchName":"inletPatch"');
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"cadReviewed":true');
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"boundaryCondition":{"type":"mass-flow-inlet"');
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("flowlab.project.v1") ?? ""))
      .toContain('"temperature":315');

    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();

    await expect(page.getByLabel("Mesh QA panel")).toContainText("Ready");
    await expect(page.getByLabel("Mesh QA panel")).toContainText("approved");
    await expect(page.getByLabel("Mesh QA panel")).toContainText("User reviewed");
  });

  test("shows mesh QA missing command blockers", async ({ page }) => {
    await openFresh(page, "missing", { runnableOpenfoam: true });
    await showStage(page, "CFD");

    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();

    await expect(page.getByLabel("Mesh QA panel")).toContainText("Blocked");
    await expect(page.getByLabel("Native mesh command list")).toContainText("surfaceFeatureExtract");
    await expect(page.getByLabel("Native mesh command list")).toContainText("missing-command");
    await expect(page.getByLabel("Mesh QA blockers")).toContainText("Missing OpenFOAM native mesh command `surfaceFeatureExtract`");
  });

  test("shows failed checkMesh reason in mesh QA", async ({ page }) => {
    await openFresh(page, "failed", { runnableOpenfoam: true });
    await showStage(page, "CFD");

    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();

    await expect(page.getByLabel("Mesh QA panel")).toContainText("Blocked");
    await expect(page.getByLabel("checkMesh metrics")).toContainText("2");
    await expect(page.getByLabel("checkMesh metrics")).toContainText("72.5");
    await expect(page.getByLabel("Mesh QA blockers")).toContainText("OpenFOAM checkMesh failed 2 check(s).");
  });

  test("shows desktop result playback controls after loading a fixture", async ({ page }) => {
    await openFresh(page);

    await loadFixtureResult(page);

    await expect(page.getByText(/Using pressure from venturi-result\.vtk/)).toBeVisible();
    await expect(page.getByLabel("3D result camera controls")).toContainText("Yaw");
    await page.getByLabel("Result camera yaw").fill("50");
    await page.getByLabel("Result camera pitch").fill("32");
    await page.getByLabel("Result camera zoom").fill("1.4");
    await expect
      .poll(() => page.getByTestId("cinema-canvas").evaluate((canvas) => (canvas as HTMLCanvasElement).dataset.resultViewMode))
      .toBe("3d");
    await expect
      .poll(() => page.getByTestId("cinema-canvas").evaluate((canvas) => (canvas as HTMLCanvasElement).dataset.resultCameraYaw))
      .toBe("50");
    await page.getByLabel("Reset result camera").click();
    await expect(page.getByLabel("Result camera yaw")).toHaveValue("-32");
    await page.getByLabel("Variable").selectOption("reynolds");
    await expect(page.getByLabel("Result field warning")).toContainText("No Reynolds field is loaded");
    await page.getByLabel("Variable").selectOption("velocity");
    await expect(page.getByLabel("Result field warning")).toHaveCount(0);
    await expect(page.getByLabel("Result timestep controls")).toBeVisible();
    await expect(page.getByRole("button", { name: "Previous result timestep" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Next result timestep" })).toBeDisabled();
    await expect(page.getByLabel("Result playback speed")).toHaveValue("1");
    await expect(page.getByRole("button", { name: "Loop result playback" })).toHaveAttribute("aria-pressed", "true");
  });

  test("rejects imported derived volume controls without component-map authority", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await openFresh(page);
    await showStage(page, "Inspect");
    await page.getByTestId("result-import-file").setInputFiles({
      name: "imported-volume.vtk",
      mimeType: "text/plain",
      buffer: Buffer.from(importedHexVolumeResult)
    });

    await expect(page.getByLabel("Derived field unit")).toHaveValue("Pa");
    await expect(page.getByRole("button", { name: "Build derived volume" })).toBeDisabled();
    await expect(page.getByLabel("Derived visualization controls")).toContainText("Explicit resultComponentMap authority is required");
    await expect(page.getByLabel("Derived visualization status")).toHaveCount(0);
    expect(consoleErrors).toEqual([]);
  });

  test("builds a generated-job derived volume through the explicit source-cell map", async ({ page }) => {
    await installDerivedVisualizationRoutes(page, true);
    await openFresh(page, "passed", { runnableOpenfoam: true, verifiedMultiEdgeLink: true });
    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await page.getByRole("button", { name: "Generate and queue experimental CFD case" }).click();
    await showStage(page, "Inspect");

    await page.getByRole("button", { name: "Build derived volume" }).click();
    await expect(page.getByLabel("Derived visualization status")).toContainText("source-cell linked");
    await expect(page.getByLabel("Derived visualization status")).toContainText("2×2×2 volume");
  });

  test("renders the desktop workspace without console errors", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await openFresh(page);

    await expect(page.getByTestId("schematic-canvas")).toBeVisible();
    await expect(page.getByTestId("cinema-canvas")).toBeVisible();
    await expect(page.getByText("Components")).toBeVisible();
    await expect(page.locator("#inspector-panel").getByText("Inspector")).toBeVisible();
    await showStage(page, "CFD");
    await page.getByRole("combobox", { name: "Solver" }).selectOption("openfoam");
    await expect(page.getByLabel("Solver runtime readiness")).toContainText("OpenFOAM readiness");
    await expect(page.getByText(/OpenFOAM cannot run locally yet/)).toBeVisible();
    expect(consoleErrors).toEqual([]);
  });
});
