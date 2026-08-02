import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App, { buildResultTimelineCsv, snapshotTimeForFile, validatedRunStatusLabel } from "./App";
import { venturiPreset } from "./data/presets";
import { useFlowStore } from "./state/useFlowStore";

const fixture = `# vtk DataFile Version 3.0
FlowLab Venturi fixture result
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
POINT_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
260000
185000
72000
101325
VECTORS velocity float
1 0 0
2 0 0
3 0 0
4 0 0
CELL_DATA 1
FIELD attributes 2
p 1 1 float
75000
U 3 1 float
3 4 0
`;

const openFoamFixture = fixture.replace("SCALARS pressure", "SCALARS p").replace("VECTORS velocity", "VECTORS U");
const openFoamLaterFixture = openFoamFixture.replace("4 0 0\n", "40 0 0\n");
const openFoamLaterWithoutCellU = openFoamLaterFixture.replace(
  "FIELD attributes 2\np 1 1 float\n75000\nU 3 1 float\n3 4 0\n",
  "FIELD attributes 1\np 1 1 float\n75000\n"
);
const openFoamFieldSummary = {
  schema: "flowlab.result_field_summary.v1" as const,
  format: "legacy-vtk-ascii-v1" as const,
  pointCount: 4,
  cellCount: 1,
  fields: [
    { name: "p", location: "point" as const, kind: "scalar" as const, tupleCount: 4, min: 72000, max: 260000, mean: 154581.25 },
    { name: "U", location: "point" as const, kind: "vector-magnitude" as const, tupleCount: 4, min: 1, max: 4, mean: 2.5 },
    { name: "U", location: "cell" as const, kind: "vector-magnitude" as const, tupleCount: 1, min: 5, max: 5, mean: 5 }
  ]
};

const openFoamPatchMetrics = {
  schema: "flowlab.patch_metrics.v1" as const,
  status: "partial",
  patches: {
    inlet: {
      patchName: "inlet",
      role: "inlet",
      flowRate: { value: -0.012, unit: "m3/s", time: 0.002, path: "postProcessing/patchFlowRate/0/patchFlowRate.dat" },
      averagePressure: { value: 101325, unit: "Pa", time: 0.002, path: "postProcessing/patchAverage/0/p.dat", field: "p" },
      sources: ["postProcessing/patchFlowRate/0/patchFlowRate.dat", "postProcessing/patchAverage/0/p.dat"]
    },
    outlet: {
      patchName: "outlet",
      role: "outlet",
      flowRate: { value: 0.0118, unit: "m3/s", time: 0.002, path: "postProcessing/patchFlowRate/0/patchFlowRate.dat" },
      averagePressure: { value: 99000, unit: "Pa", time: 0.002, path: "postProcessing/patchAverage/0/p.dat", field: "p" },
      sources: ["postProcessing/patchFlowRate/0/patchFlowRate.dat", "postProcessing/patchAverage/0/p.dat"]
    },
    walls: {
      patchName: "walls",
      role: "wall",
      wallShear: { min: 0.4, mean: 1.1, max: 2.8, unit: "Pa", time: 0.002, path: "postProcessing/wallShearStress/0/wallShearStress.dat" },
      sources: ["postProcessing/wallShearStress/0/wallShearStress.dat"]
    }
  },
  flowBalance: {
    inletFlow: 0.012,
    outletFlow: 0.0118,
    imbalance: -0.0002,
    relativeImbalance: 0.0166666667,
    unit: "m3/s",
    inletPatches: ["inlet"],
    outletPatches: ["outlet"]
  },
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
  warnings: ["OpenFOAM wall shear evidence is partial in this mock."],
  sources: [{ path: "postProcessing/patchFlowRate/0/patchFlowRate.dat", kind: "patch-flow-rate", status: "parsed" }]
};

function mockCanvas() {
  const context = {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    quadraticCurveTo: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
    setLineDash: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    translate: vi.fn(),
    scale: vi.fn(),
    rotate: vi.fn()
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    bottom: 640,
    right: 960,
    width: 960,
    height: 640,
    toJSON: () => ({})
  } as DOMRect);
}

describe("FlowLab result visualization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    delete window.webkit;
    delete window.flowlabDesktop;
    window.localStorage.clear();
    useFlowStore.getState().setProject({ ...venturiPreset, visualization: { ...venturiPreset.visualization, mode: "sweep" } });
    mockCanvas();
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
        if (url === "/api/solvers") return new Response(JSON.stringify([{ id: "instant-1d", label: "Instant 1D hydraulics", installed: true, execution: "browser", notes: [] }]));
        if (url === "/api/runtime") {
          return new Response(
            JSON.stringify([
              {
                solver: "instant-1d",
                runnable: true,
                preferredExecution: "browser",
                blockers: [],
                notes: ["Instant 1D hydraulics run in the browser."]
              },
              {
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
            ])
          );
        }
        if (url === "/api/benchmarks/validated") {
          return new Response(JSON.stringify({
            schema: "flowlab.validated_benchmark_registry.v1",
            benchmarks: [{
              id: "periodic-all-hex-straight-pipe-v1",
              label: "Validated benchmark — periodic all-hex straight pipe",
              scientificStatus: "analysis-only-narrow-envelope",
              capabilityStatus: "validated-benchmark",
              promotionBlocked: true,
              applicability: ["Steady incompressible laminar Poiseuille flow."],
              limits: ["Not validated for open boundaries."],
              metrics: {},
              evidence: []
            }, {
              id: "laminar-open-boundary-all-hex-v1",
              label: "Candidate bounded regime — laminar open-boundary all-hex",
              scientificStatus: "campaign-promotion-blocked",
              capabilityStatus: "experimental",
              promotionBlocked: true,
              blockingReasons: [
                "Campaign gate `physicalEnvelopeAccepted` has not passed.",
                "Campaign gate `experimentalDatasetPinned` has not passed.",
                "Campaign gate `reproducibilityAccepted` has not passed."
              ],
              applicability: ["Steady incompressible laminar flow on the structured Cartesian all-hex family."],
              limits: ["Validated only for this bounded laminar contract."],
              metrics: {},
              evidence: []
            }]
          }));
        }
        if (url === "/api/benchmarks/validated/laminar-open-boundary-all-hex-v1/jobs") {
          const evidenceCapability = {
            status: "validated-benchmark",
            promotionBlocked: false,
            blockingReasons: [],
            validationPath: ["Exact immutable preset only."],
            allowedClaims: ["Bounded preset reproduction."],
            prohibitedClaims: ["production CFD"],
            evidenceId: "laminar-open-boundary-all-hex-v1",
            immutableEvidence: []
          };
          return new Response(JSON.stringify({
            case: {
              id: "case-validated-open-boundary",
              projectName: "Validated laminar open-boundary preset",
              solver: "openfoam",
              advancedMode: "incompressible-navier-stokes",
              status: "generated",
              files: {},
              runCommand: ["bash", "Allrun"],
              provenance: ["Exact immutable preset."],
              evidenceCapability
            },
            job: {
              id: "job-validated-open-boundary",
              caseId: "case-validated-open-boundary",
              solver: "openfoam",
              status: "complete",
              createdAt: "2026-07-16T00:00:00Z",
              updatedAt: "2026-07-16T00:00:02Z",
              finishedAt: "2026-07-16T00:00:02Z",
              caseDir: "/tmp/flowlab/validated",
              execution: "docker",
              command: ["docker", "run"],
              logs: ["FLOWLAB_VALIDATED_PRESET all runtime gates passed"],
              exitCode: 0,
              evidenceCapability,
              result: {
                resultFiles: [],
                diagnosticFiles: [],
                diagnosticSummary: [],
                progressive: false,
                validatedBenchmark: {
                  schema: "flowlab.validated_open_boundary_run.v1",
                  benchmarkId: "laminar-open-boundary-all-hex-v1",
                  status: "accepted",
                  allChecksPassed: true,
                  cellsPerAxis: 12,
                  scope: "Bounded plane-Poiseuille reproduction only; not a general or production CFD claim.",
                  checks: { openFoamForcesMatchDirectIntegration: true },
                  errors: {
                    openFoamVsDirectAbsolute: 1.7e-16,
                    analyticPressureForceRelative: 1.7e-15,
                    analyticWallViscousRelative: 6.3e-7,
                    analyticOpenViscousRelative: 7.8e-14
                  },
                  openFoamForces: {},
                  directFaceIntegration: {},
                  analytic: {},
                  flux: { inlet: -0.16, outlet: 0.16 },
                  artifacts: { fields: "VTK/*" }
                }
              }
            }
          }));
        }
        if (url === "/api/cases/generate") {
          return new Response(
            JSON.stringify({
              id: "case-openfoam-test",
              projectName: "Venturi Cavitation Lab",
              solver: "openfoam",
              advancedMode: "incompressible-navier-stokes",
              status: "generated",
              files: {},
              runCommand: ["bash", "Allrun"],
              provenance: []
            })
          );
        }
        if (url === "/api/jobs") {
          return new Response(
            JSON.stringify({
              id: "job-openfoam-test",
              caseId: "case-openfoam-test",
              solver: "openfoam",
              status: "complete",
              createdAt: "2026-06-11T00:00:00Z",
              updatedAt: "2026-06-11T00:00:02Z",
              finishedAt: "2026-06-11T00:00:02Z",
              caseDir: "/tmp/flowlab/case",
              execution: "native",
              command: ["bash", "Allrun"],
              logs: ["Time = 0.002", "Solver process exited successfully with code 0."],
              exitCode: 0,
              result: {
                caseDir: "/tmp/flowlab/case",
                exitCode: 0,
                logsCaptured: 5,
                logSummary: {
                  solver: "openfoam",
                  lineCount: 5,
                  lastLines: ["Time = 0.002"],
                  latestTime: 0.002,
                  timeSteps: [0.001, 0.002],
                  residuals: {
                    Ux: { initial: 0.12, final: 0.0000075, iterations: 2 },
                    p: { initial: 0.4, final: 0.00009, iterations: 3 }
                  },
                  warnings: ["WARNING: Courant number adjusted"]
                },
                resultFiles: [
                  { path: "VTK/case_1.vtk", size: 100, text: openFoamFixture, fieldSummary: openFoamFieldSummary },
                  { path: "VTK/case_2.vtk", size: 100, text: openFoamFixture, fieldSummary: openFoamFieldSummary },
                  { path: "VTK/large_case.vtk", size: 1024, skipped: "file too large" },
                  {
                    path: "<additional-result-files>",
                    size: 2048,
                    skipped: "2 additional VTK/VTU result file(s) omitted after collection limit 8"
                  }
                ],
                diagnosticFiles: [
                  { path: "postProcessing/residuals/0/residuals.dat", size: 32, text: "Time Ux p" },
                  { path: "postProcessing/logs/full.log", size: 800000, skipped: "file too large" }
                ],
                diagnosticSummary: [
                  {
                    path: "postProcessing/residuals/0/residuals.dat",
                    kind: "residuals",
                    columns: ["Time", "Ux", "p"],
                    rowCount: 2,
                    latest: { Time: 0.002, Ux: 0.0000075, p: 0.00009 }
                  }
                ],
                patchMetrics: openFoamPatchMetrics,
                progressive: false
              }
            })
          );
        }
        if (url.startsWith("/api/jobs/job-openfoam-test/artifact/chunk")) {
          const params = new URL(url, "http://localhost").searchParams;
          const offset = Number(params.get("offset") ?? 0);
          const text = openFoamLaterFixture.slice(offset);
          return new Response(
            JSON.stringify({
              path: params.get("path"),
              size: openFoamLaterFixture.length,
              offset,
              limit: 262144,
              text,
              nextOffset: openFoamLaterFixture.length,
              complete: true
            })
          );
        }
        if (url.startsWith("/api/jobs/job-openfoam-test/artifact/preview")) {
          const params = new URL(url, "http://localhost").searchParams;
          return new Response(
            JSON.stringify({
              path: params.get("path"),
              size: 100,
              schema: "flowlab.result_preview.v1",
              format: "legacy-vtk-ascii-v1",
              sourcePointCount: 4,
              sourceCellCount: 1,
              pointCount: 4,
              cellCount: 1,
              pointLimit: 500,
              cellLimit: 500,
              truncated: false,
              pointIndices: [0, 1, 2, 3],
              cellIndices: [0],
              points: [
                [0, 0, 0],
                [1, 0, 0],
                [1, 1, 0],
                [0, 1, 0]
              ],
              cells: [[0, 1, 2, 3]],
              cellTypes: [9],
              fieldSummary: openFoamFieldSummary,
              fieldSamples: {
                point: [
                  { name: "p", kind: "scalar", values: [260000, 185000, 72000, 101325] },
                  {
                    name: "U",
                    kind: "vector",
                    values: [
                      [1, 0, 0],
                      [2, 0, 0],
                      [3, 0, 0],
                      [4, 0, 0]
                    ],
                    magnitudes: [1, 2, 3, 4]
                  }
                ],
                cell: [{ name: "U", kind: "vector", values: [[3, 4, 0]], magnitudes: [5] }]
              }
            })
          );
        }
        if (url.startsWith("/api/jobs/job-openfoam-test/artifacts")) {
          return new Response(
            JSON.stringify({
              artifacts: [
                {
                  path: "VTK/collection.pvd",
                  size: 240,
                  kind: "result",
                  collectionSummary: {
                    schema: "flowlab.pvd_collection.v1",
                    format: "pvd-ascii-v1",
                    path: "VTK/collection.pvd",
                    datasetCount: 26,
                    referencedResultCount: 26,
                    missingResultCount: 0,
                    unsafeReferenceCount: 0,
                    truncated: false,
                    datasets: [
                      { index: 0, time: 0.001, timeText: "0.001", file: "VTK/case_1.vtk", exists: true },
                      { index: 1, time: 0.002, timeText: "0.002", file: "VTK/case_2.vtk", exists: true }
                    ]
                  }
                },
                { path: "VTK/case_1.vtk", size: 100, kind: "result", fieldSummary: openFoamFieldSummary, time: 0.001, timeText: "0.001", timeSource: "pvd", collectionPath: "VTK/collection.pvd", collectionIndex: 0 },
                { path: "VTK/case_2.vtk", size: 100, kind: "result", fieldSummary: openFoamFieldSummary, time: 0.002, timeText: "0.002", timeSource: "pvd", collectionPath: "VTK/collection.pvd", collectionIndex: 1 },
                { path: "VTK/large_case.vtk", size: openFoamLaterFixture.length, kind: "result" },
                ...Array.from({ length: 23 }, (_value, index) => ({
                  path: `VTK/case_${index + 3}.vtk`,
                  size: 100,
                  kind: "result",
                  fieldSummary: openFoamFieldSummary,
                  time: (index + 3) / 1000,
                  timeText: `0.${String(index + 3).padStart(3, "0")}`,
                  timeSource: "pvd",
                  collectionPath: "VTK/collection.pvd",
                  collectionIndex: index + 2
                }))
              ],
              count: 27,
              truncated: false
            })
          );
        }
        if (url === "/fixtures/venturi-result.vtk") return new Response(fixture);
        return new Response("not found", { status: 404 });
      })
    );
  });

  it("infers OpenFOAM VTK output time from zero-based solver time logs", () => {
    const timeSteps = Array.from({ length: 51 }, (_value, index) => index / 1000);

    expect(snapshotTimeForFile("VTK/case_50.vtk", 0, { solver: "openfoam", lineCount: 0, lastLines: [], timeSteps, latestTime: 0.05 })).toBe(0.05);
    expect(snapshotTimeForFile("VTK/case_25.vtk", 1, { solver: "openfoam", lineCount: 0, lastLines: [], timeSteps, latestTime: 0.05 })).toBe(0.025);
    expect(snapshotTimeForFile("VTK/case_1.vtk", 0, { solver: "openfoam", lineCount: 0, lastLines: [], timeSteps: [0.001, 0.002], latestTime: 0.002 })).toBe(0.001);
  });

  it("labels the pinned periodic evidence as a narrow benchmark, not a general result", async () => {
    render(<App />);

    expect(await screen.findByText("Validated regimes")).toBeTruthy();
    expect(screen.getByText("Validated benchmark — periodic all-hex straight pipe")).toBeTruthy();
    expect(screen.getByText(/Analysis-only narrow envelope · promotion blocked/)).toBeTruthy();
    expect(screen.getByText("Not validated for open boundaries.")).toBeTruthy();
    expect(screen.getByText("Candidate bounded regime — laminar open-boundary all-hex")).toBeTruthy();
    expect(screen.getByText(/Candidate regime · promotion blocked/)).toBeTruthy();
    expect(screen.getByText(/physicalEnvelopeAccepted/)).toBeTruthy();
    expect(screen.getByText(/experimentalDatasetPinned/)).toBeTruthy();
    expect(screen.getByText(/reproducibilityAccepted/)).toBeTruthy();
  });

  it("does not expose the validated preset action while the campaign is blocked", async () => {
    render(<App />);

    expect(await screen.findByText(/Candidate regime · promotion blocked/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Run validated coarse preset/i })).toBeNull();
  });

  it("does not let a historical coarse runtime result overrule the current campaign gate", () => {
    expect(validatedRunStatusLabel("validated-benchmark", "complete", true, true)).toBe(
      "Coarse runtime audit passed · current bounded-regime promotion is blocked"
    );
    expect(validatedRunStatusLabel("validated-benchmark", "complete", true, false)).toBe(
      "Validated bounded preset — every coarse runtime gate passed"
    );
  });

  it("exports result timeline statistics as CSV rows", () => {
    expect(
      buildResultTimelineCsv([
        {
          id: "one",
          label: "VTK/case_1.vtk",
          time: 0.001,
          field: "U",
          location: "cell",
          kind: "vector-magnitude",
          unit: { symbol: "m/s", label: "velocity" },
          min: 5,
          max: 5,
          mean: 5
        },
        {
          id: "missing",
          label: "VTK/case_2.vtk",
          time: 0.002,
          field: null,
          location: null,
          kind: null,
          unit: null,
          min: null,
          max: null,
          mean: null
        }
      ])
    ).toBe("snapshot,label,time,field,location,kind,unit,min,max,mean\n1,VTK/case_1.vtk,0.001,U,cell,vector-magnitude,m/s,5,5,5\n2,VTK/case_2.vtk,0.002,,,,,,,");
  });

  it("routes exports through the native desktop save bridge when packaged", async () => {
    const postMessage = vi.fn();
    window.webkit = { messageHandlers: { flowlabDesktop: { postMessage } } };
    render(<App />);

    fireEvent.click(screen.getByTitle("Export project"));

    expect(postMessage).toHaveBeenCalledWith({
      type: "save-files",
      files: [
        expect.objectContaining({
          filename: "venturi-cavitation-lab.flowlab.json",
          type: "application/json"
        })
      ]
    });
  });

  it("routes exports through the Electron desktop bridge when packaged", async () => {
    const saveFiles = vi.fn().mockResolvedValue({ status: "saved", message: "Exported project." });
    window.flowlabDesktop = { platform: "darwin", saveFiles };
    render(<App />);

    fireEvent.click(screen.getByTitle("Export project"));

    await waitFor(() => expect(saveFiles).toHaveBeenCalledTimes(1));
    expect(saveFiles.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        filename: expect.stringMatching(/\.flowlab\.json$/),
        type: "application/json"
      })
    ]);
  });

  it("switches an already-loaded recent run back to its newest artifact and reconciles velocity to U", async () => {
    render(<App />);
    fireEvent.change(await screen.findByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });
    fireEvent.click(screen.getByRole("button", { name: /Run CFD case/i }));

    expect(await screen.findByText("Server job job-openfoam-test: complete")).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/case_2\.vtk/)).toBeTruthy();

    fireEvent.click(within(screen.getByRole("navigation", { name: "FlowLab workflow stages" })).getByRole("button", { name: /Inspect$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Load fixture result/i }));
    const fixtureFields = await screen.findByLabelText("Loaded result fields");
    fireEvent.click(within(fixtureFields).getByTitle("velocity point vector, 4 tuples"));
    expect(screen.getByText(/Using velocity magnitude from venturi-result\.vtk/)).toBeTruthy();

    fireEvent.click(within(screen.getByLabelText("Recent solver runs")).getByTitle("job-openfoam-test"));

    expect(await screen.findByText(/Using U magnitude from VTK\/case_2\.vtk \(point data, m\/s\)/)).toBeTruthy();
  });

  it("loads the fixture result and switches from pressure to velocity overlays", async () => {
    render(<App />);

    expect(await screen.findByLabelText("Solver runtime readiness")).toHaveTextContent("Instant 1D readiness");
    fireEvent.change(screen.getByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });

    expect(screen.getByLabelText("Solver runtime readiness")).toHaveTextContent("OpenFOAM readiness");
    expect(screen.getByLabelText("Solver runtime readiness")).toHaveTextContent("native");

    fireEvent.click(within(screen.getByRole("navigation", { name: "FlowLab workflow stages" })).getByRole("button", { name: /Inspect$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Load fixture result/i }));

    expect(await screen.findByText(/Using pressure from venturi-result\.vtk/)).toBeTruthy();
    expect(screen.getAllByText("Fixture result — developer example · probe only").length).toBeGreaterThan(1);
    expect(screen.getByLabelText("Steady streamline controls")).toHaveTextContent("Deterministic RK4 through loaded U(x,y,z)");
    expect(screen.getByLabelText("Steady streamline controls")).toHaveTextContent("passive animation, not transient pathlines");
    expect(screen.getByRole("button", { name: "Derive" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Automatic inlet seeds" })).toBeDisabled();
    expect(screen.getByLabelText("3D result camera controls")).toHaveTextContent("Yaw");
    expect(screen.getByLabelText("Result camera yaw")).toHaveValue("-32");
    fireEvent.change(screen.getByLabelText("Result camera yaw"), { target: { value: "45" } });
    fireEvent.change(screen.getByLabelText("Result camera pitch"), { target: { value: "35" } });
    fireEvent.change(screen.getByLabelText("Result camera zoom"), { target: { value: "1.5" } });
    expect(screen.getByTestId("cinema-canvas").dataset.resultViewMode).toBe("3d");
    expect(screen.getByTestId("cinema-canvas").dataset.resultCameraYaw).toBe("45");
    expect(screen.getByTestId("cinema-canvas").dataset.resultCameraPitch).toBe("35");
    expect(screen.getByTestId("cinema-canvas").dataset.resultCameraZoom).toBe("1.5");
    fireEvent.click(screen.getByRole("button", { name: "Reset result camera" }));
    expect(screen.getByLabelText("Result camera yaw")).toHaveValue("-32");
    const fieldList = screen.getByLabelText("Loaded result fields");
    expect(within(fieldList).getByTitle("pressure point scalar, 4 tuples")).toBeTruthy();
    expect(within(fieldList).getByTitle("U cell vector, 1 tuple")).toBeTruthy();
    expect(screen.getByLabelText("Result field filter summary")).toHaveTextContent("4/4 fields shown");
    fireEvent.change(screen.getByLabelText("Filter result fields"), { target: { value: "cell" } });
    expect(screen.getByLabelText("Result field filter summary")).toHaveTextContent("2/4 fields shown");
    expect(screen.getByLabelText("Loaded result fields")).toHaveTextContent("U");
    expect(screen.getByLabelText("Loaded result fields")).not.toHaveTextContent("pressure");
    fireEvent.change(screen.getByLabelText("Filter result fields"), { target: { value: "vapour" } });
    expect(screen.getByLabelText("Result field filter summary")).toHaveTextContent("0/4 fields shown");
    expect(screen.getByText("No fields match the current filter.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter result fields"), { target: { value: "" } });
    const restoredFieldList = screen.getByLabelText("Loaded result fields");
    expect(restoredFieldList).toHaveTextContent("Pa");
    expect(restoredFieldList).toHaveTextContent("m/s");
    expect(screen.getByText("t0")).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("72,000");
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("Pa");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("Mean");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("154,581 Pa");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("P95");
    expect(screen.getByLabelText("Result field histogram")).toHaveTextContent("Distribution");
    expect(screen.getByLabelText("Result field histogram")).toHaveTextContent("scalar · point · Pa");
    expect(screen.getByLabelText("Result color map")).toHaveValue("turbo");
    expect(screen.getByLabelText("Result color ramp")).toHaveAttribute("title", "Turbo color map");
    fireEvent.change(screen.getByLabelText("Result color map"), { target: { value: "viridis" } });
    expect(screen.getByLabelText("Result color map")).toHaveValue("viridis");
    expect(screen.getByLabelText("Result color ramp")).toHaveAttribute("title", "Viridis color map");

    fireEvent.change(screen.getByLabelText(/Variable/i), { target: { value: "velocity" } });

    await waitFor(() => expect(screen.getByText(/Using velocity from venturi-result\.vtk/)).toBeTruthy());
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("4");
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("m/s");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("2.5 m/s");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("3.85 m/s");
    expect(screen.getByLabelText("Result field histogram")).toHaveTextContent("magnitude · point · m/s");

    fireEvent.change(screen.getByLabelText(/Variable/i), { target: { value: "reynolds" } });

    expect(await screen.findByLabelText("Result field warning")).toHaveTextContent("No Reynolds field is loaded");
    expect(screen.getByLabelText("Result field warning")).toHaveTextContent("Select an available field");
    fireEvent.change(screen.getByLabelText(/Variable/i), { target: { value: "velocity" } });

    fireEvent.pointerDown(screen.getByTestId("cinema-canvas"), { clientX: 1, clientY: 1, pointerId: 1 });

    expect(await screen.findByLabelText("Probe sample")).toHaveTextContent("velocity @ p2");
    expect(screen.getByLabelText("Probe sample")).toHaveTextContent("3 m/s");

    fireEvent.click(within(restoredFieldList).getByTitle("U cell vector, 1 tuple"));

    expect(await screen.findByText(/Using U magnitude from venturi-result\.vtk \(cell data, m\/s\)/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("5");
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("m/s");
    expect(screen.getByLabelText("Field statistics")).toHaveTextContent("0 m/s");
    expect(screen.getByLabelText("Result field histogram")).toHaveTextContent("magnitude · cell · m/s");

    fireEvent.change(screen.getByLabelText("Vector component"), { target: { value: "y" } });

    expect(await screen.findByText(/Using U y component from venturi-result\.vtk \(cell data, m\/s\)/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("4");
    expect(screen.getByLabelText("Result field histogram")).toHaveTextContent("y component · cell · m/s");

    fireEvent.pointerDown(screen.getByTestId("cinema-canvas"), { clientX: 1, clientY: 1, pointerId: 2 });

    expect(await screen.findByLabelText("Probe sample")).toHaveTextContent("U @ c0");
    expect(screen.getByLabelText("Probe sample")).toHaveTextContent("4 m/s");
  });

  it("keeps concept animation opt-in and labels imported data probe-only", async () => {
    render(<App />);
    const stages = screen.getByRole("navigation", { name: "FlowLab workflow stages" });

    fireEvent.click(within(stages).getByRole("button", { name: /Define/ }));
    expect(screen.getAllByText("Concept preview").length).toBeGreaterThan(0);

    fireEvent.click(within(stages).getByRole("button", { name: /Estimate/ }));
    const illustration = screen.getByRole("button", { name: "Illustrative estimate animation—not CFD" });
    expect(illustration).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(illustration);
    expect(illustration).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(stages).getByRole("button", { name: /Inspect/ }));
    const imported = new File([fixture], "imported-result.vtk", { type: "text/plain" });
    fireEvent.change(screen.getByTestId("result-import-file"), { target: { files: [imported] } });

    expect((await screen.findAllByText("Imported result — probe only")).length).toBeGreaterThan(0);
    expect(screen.getByText("2D projection fallback — WebGL/accessibility/export")).toBeTruthy();
    const projectionFallback = screen.getByRole("button", { name: "Use 2D projection fallback" });
    expect(projectionFallback).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(projectionFallback);
    expect(projectionFallback).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("cinema-canvas")).toHaveAttribute("data-canvas-render-mode", "projection");
    expect(screen.getByRole("button", { name: "Derive" })).toBeDisabled();
  });

  it("shows an eligible generated-case mesh as the CFD authority before solver fields arrive", async () => {
    const defaultFetch = vi.mocked(fetch);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/cases/generate") {
          return new Response(JSON.stringify({
            id: "case-generated-mesh-test",
            projectName: "Venturi Cavitation Lab",
            solver: "openfoam",
            advancedMode: "incompressible-navier-stokes",
            status: "generated",
            files: {
              "flowlab_project.json": JSON.stringify({ solver: { meshMode: "full-ogrid" } }),
              "mesh/flowlab_mesh.vtk": openFoamFixture
            },
            runCommand: ["bash", "Allrun"],
            provenance: []
          }));
        }
        if (url === "/api/jobs" && init?.method === "POST") {
          return new Response(JSON.stringify({
            id: "job-generated-mesh-test",
            caseId: "case-generated-mesh-test",
            solver: "openfoam",
            status: "running",
            createdAt: "2026-07-31T00:00:00Z",
            updatedAt: "2026-07-31T00:00:01Z",
            execution: "native",
            command: ["bash", "Allrun"],
            logs: ["Meshing"],
            result: null
          }));
        }
        return defaultFetch(input, init);
      })
    );

    render(<App />);
    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });
    fireEvent.click(screen.getByRole("button", { name: /Run CFD case/i }));

    expect((await screen.findAllByText("Generated-case mesh preview")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Deterministic pre-solve mesh from the current generated case/)).toBeTruthy();
  });

  it("persists advanced mesh controls from the inspector", async () => {
    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByLabelText("Mesh resolution"), { target: { value: "fine" } });
    fireEvent.change(screen.getByLabelText("Longitudinal refinement"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Boundary layer strip cells"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Boundary layer growth rate"), { target: { value: "1.4" } });
    fireEvent.change(screen.getByLabelText("Target y plus"), { target: { value: "8" } });
    fireEvent.click(screen.getByLabelText("Feature-aware mesh clustering"));
    fireEvent.change(screen.getByLabelText("Feature refinement factor"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Feature cluster strength"), { target: { value: "0.65" } });
    fireEvent.change(screen.getByLabelText("Selected edge refinement factor"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Mesh max aspect ratio"), { target: { value: "11" } });
    fireEvent.change(screen.getByLabelText("Mesh min interior angle"), { target: { value: "24" } });
    fireEvent.click(screen.getByLabelText("Enable adaptive mesh planning"));
    fireEvent.change(screen.getByLabelText("Adaptive mesh target field"), { target: { value: "pressure" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh error mode"), { target: { value: "relative-error" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh cadence"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh max cells"), { target: { value: "500000" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh min cell size"), { target: { value: "0.0005" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh max cell size"), { target: { value: "0.05" } });
    fireEvent.change(screen.getByLabelText("Adaptive mesh gradation"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByLabelText("Write adapted-state placeholder"));

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem("flowlab.project.v1") ?? "{}");
      expect(saved.solver.meshResolution).toBe("fine");
      expect(saved.solver.meshControls).toMatchObject({
        longitudinalRefinement: 3,
        boundaryLayerLayers: 4,
        boundaryLayerGrowthRate: 1.4,
        targetYPlus: 8,
        refinementRegions: [{ edgeId: "inlet", factor: 3, reason: "venturi-local-refinement" }],
        featureRefinement: { enabled: true, factor: 3, clusterStrength: 0.65 },
        quality: { maxAspectRatio: 11, minInteriorAngleDeg: 24 }
      });
      expect(saved.solver.adaptiveMesh).toMatchObject({
        enabled: true,
        targetField: "pressure",
        errorMode: "relative-error",
        adaptEvery: 3,
        maxCells: 500000,
        minCellSize: 0.0005,
        maxCellSize: 0.05,
        gradation: 1.25,
        writeAdaptedState: false
      });
    });
  });

  it("selects full O-grid mode with conformal defaults that refine in every material dimension", async () => {
    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByLabelText("Mesh mode"), { target: { value: "full-ogrid" } });

    expect(screen.getByLabelText("Run mode")).toHaveValue("steady");
    expect(screen.getByLabelText("Full O-grid axial cells")).toHaveValue(16);
    expect(screen.getByLabelText("Full O-grid annular radial cells")).toHaveValue(4);
    expect(screen.getByLabelText("Full O-grid circumferential cells")).toHaveValue(32);
    expect(screen.getByLabelText("Full O-grid core cells per side")).toHaveValue(8);

    fireEvent.change(screen.getByLabelText("Mesh resolution"), { target: { value: "medium" } });
    expect(screen.getByLabelText("Full O-grid axial cells")).toHaveValue(32);
    expect(screen.getByLabelText("Full O-grid annular radial cells")).toHaveValue(8);
    expect(screen.getByLabelText("Full O-grid circumferential cells")).toHaveValue(64);
    expect(screen.getByLabelText("Full O-grid core cells per side")).toHaveValue(16);

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem("flowlab.project.v1") ?? "{}");
      expect(saved.solver).toMatchObject({
        meshMode: "full-ogrid",
        runMode: "steady",
        turbulence: "laminar",
        meshResolution: "medium",
        meshControls: {
          fullOGridAxialCells: 32,
          fullOGridAnnularRadialCells: 8,
          fullOGridCircumferentialCells: 64,
          fullOGridCoreCellsPerSide: 16
        }
      });
    });
  });

  it("does not expose the numerically unqualified Y-junction as a selectable mesh mode", async () => {
    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    const meshMode = screen.getByLabelText("Mesh mode");
    expect(within(meshMode).queryByRole("option", { name: /Y-junction/i })).toBeNull();
    expect(screen.queryByLabelText("Y-junction cell size")).toBeNull();
  });

  it("shows parsed solver progress from a queued advanced job", async () => {
    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });
    fireEvent.click(screen.getByRole("button", { name: /Run CFD case/i }));

    expect(await screen.findByText("Job complete")).toBeTruthy();
    expect(screen.getAllByText("Solver-produced mesh").length).toBeGreaterThan(0);
    expect(screen.getByText(/Final artifacts: 2 field, 1 diagnostic · skipped 2 field, 1 diagnostic/)).toBeTruthy();
    expect(screen.getByLabelText("Skipped artifact summary")).toHaveTextContent("VTK/large_case.vtk");
    expect(screen.getByLabelText("Skipped artifact summary")).toHaveTextContent("file too large");
    expect(screen.getByText(/Snapshot 2\/2/)).toBeTruthy();
    expect(screen.getAllByText("t0.002").length).toBeGreaterThan(0);
    expect(screen.getByText(/Using U from VTK\/case_2\.vtk/)).toBeTruthy();
    expect(screen.getByLabelText("Diagnostic table summary")).toHaveTextContent("residuals");
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("2/2 snapshots");
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("All loaded snapshots contain the active field");
    expect(screen.getByLabelText("Diagnostic table summary")).toHaveTextContent("Ux 7.50e-6");
    expect(screen.getByLabelText("Solver diagnostics panel")).toHaveTextContent("0.002");
    expect(screen.getByLabelText("Solver residual convergence")).toHaveTextContent("Ux");
    expect(screen.getByLabelText("Solver residual convergence")).toHaveTextContent("7.50e-6");
    expect(screen.getByLabelText("Solver diagnostic tables")).toHaveTextContent("residuals");
    expect(screen.getByLabelText("Solver diagnostic tables")).toHaveTextContent("p 9.00e-5");
    expect(screen.getByLabelText("OpenFOAM patch metrics")).toHaveTextContent("Patch Metrics");
    expect(screen.getByLabelText("Patch flow balance")).toHaveTextContent("in 0.012 / out 0.0118 m3/s");
    expect(screen.getByLabelText("Patch pressure drops")).toHaveTextContent("2.325 kPa");
    expect(screen.getByLabelText("Wall shear metrics")).toHaveTextContent("mean 1.1 Pa");
    expect(screen.getByLabelText("Integrated force metrics")).toHaveTextContent("|F| 4.1158 N");
    expect(screen.getByLabelText("Patch metric warnings")).toHaveTextContent("partial");
    expect(screen.getByLabelText("Solver log warnings")).toHaveTextContent("Courant number adjusted");
    expect(screen.getByLabelText("Result field summary")).toHaveTextContent("VTK/case_1.vtk");
    expect(screen.getByLabelText("Result field summary")).toHaveTextContent("4 points, 1 cell");
    expect(screen.getByLabelText("Result field summary")).toHaveTextContent("p point scalar 4");
    expect(screen.getByLabelText("Solver progress summary")).toHaveTextContent("Time");
    expect(screen.getByLabelText("Solver progress summary")).toHaveTextContent("0.002");
    expect(screen.getByLabelText("Residual summary")).toHaveTextContent("Ux: 7.50e-6");
    expect(screen.getByLabelText("Residual summary")).toHaveTextContent("p: 9.00e-5");
    expect(screen.getByText("1 warning")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Load skipped field/i }));

    expect(await screen.findByText(/Snapshot 3\/3/)).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/large_case\.vtk/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Load skipped field/i }));

    expect(await screen.findByText(/Snapshot 3\/3/)).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/large_case\.vtk/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Index field files/i }));
    expect(await screen.findByLabelText("Indexed result artifacts")).toHaveTextContent("Indexed 27/27 field files");
    expect(screen.getByLabelText("Result collection manifests")).toHaveTextContent("VTK/collection.pvd: 26/26 timesteps");
    expect(screen.getByText("case_1.vtk")).toBeTruthy();
    expect(screen.getByLabelText("Indexed result artifacts")).toHaveTextContent("t=0.001");
    expect(screen.getByLabelText("Indexed result artifacts")).toHaveTextContent("3 fields: p point, U point, U cell");

    fireEvent.click(within(screen.getByLabelText("Indexed result artifacts")).getByRole("button", { name: /Preview sequence \(24\)/i }));

    expect(await screen.findByText(/Snapshot 25\/25/)).toBeTruthy();
    expect(screen.getAllByText("Thinned artifact preview — surface only").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Derive" })).toBeDisabled();
    expect(screen.getByText(/Using U from VTK\/case_24\.vtk preview/)).toBeTruthy();
    expect(screen.getByLabelText("Result field timeline")).toHaveTextContent("Trend");
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("25/25 snapshots");

    fireEvent.click(within(screen.getByLabelText("Indexed result artifacts")).getByRole("button", { name: /Preview next 1 \(24 loaded\)/i }));

    expect(await screen.findByText(/Snapshot 26\/26/)).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/case_25\.vtk preview/)).toBeTruthy();
    expect(within(screen.getByLabelText("Indexed result artifacts")).getByRole("button", { name: /Preview sequence loaded \(25\)/i })).toBeDisabled();

    fireEvent.click(within(screen.getByLabelText("Indexed result artifacts")).getAllByRole("button", { name: /^Preview$/i })[0]);
    await waitFor(() => expect(screen.getByLabelText("Result artifact preview")).toHaveTextContent("VTK/case_1.vtk"));
    expect(screen.getByLabelText("Result artifact preview")).toHaveTextContent("4/4 points, 1/1 cells");
    expect(screen.getByLabelText("Result artifact preview")).toHaveTextContent("p point 4");
    expect(await screen.findByText(/Snapshot 1\/26/)).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/case_1\.vtk/)).toBeTruthy();

    fireEvent.click(within(screen.getByLabelText("Indexed result artifacts")).getAllByRole("button", { name: /^Preview$/i })[0]);

    expect(await screen.findByText(/Snapshot 1\/26/)).toBeTruthy();
    expect(screen.getByText(/Using U from VTK\/case_1\.vtk/)).toBeTruthy();
  }, 20_000);

  it("blocks advanced case queueing when the live network has topology errors", async () => {
    useFlowStore.getState().setProject({ ...venturiPreset, visualization: { ...venturiPreset.visualization, mode: "sweep" } });
    useFlowStore.getState().updateEdge("inlet", { to: "source" });

    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });

    const queueButton = screen.getByRole("button", { name: /Run CFD case/i });
    expect(queueButton).toBeDisabled();
    expect(screen.getByText(/Fix 1 blocking network issue before queueing a solver case/i)).toBeTruthy();

    fireEvent.click(queueButton);

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/cases/generate")).toBe(false);
  });

  it("keeps browser-side Instant 1D out of the CFD queue and keeps VTK import in Inspect", async () => {
    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    const queueButton = screen.getByRole("button", { name: /Run CFD case/i });
    expect(queueButton).toBeDisabled();
    expect(screen.getByText(/Instant 1D runs in the Estimate stage/i)).toBeTruthy();
    expect(screen.queryByTestId("result-import-file")).toBeNull();

    fireEvent.click(within(screen.getByRole("navigation", { name: "FlowLab workflow stages" })).getByRole("button", { name: /Inspect$/ }));
    expect(screen.getByRole("button", { name: "Import VTK/VTU" })).toBeTruthy();
    expect(screen.getByTestId("result-import-file")).toBeTruthy();

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/cases/generate")).toBe(false);
  });

  it("keeps progressive solver snapshots playable as completed result files arrive", async () => {
    let resolvePoll: (response: Response) => void = () => undefined;
    const pollPromise = new Promise<Response>((resolve) => {
      resolvePoll = resolve;
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/health") return new Response(JSON.stringify({ status: "ok" }));
        if (url === "/api/solvers") return new Response(JSON.stringify([{ id: "openfoam", label: "OpenFOAM", installed: true, execution: "native", notes: [] }]));
        if (url === "/api/runtime") {
          return new Response(
            JSON.stringify([
              { solver: "instant-1d", runnable: true, preferredExecution: "browser", blockers: [], notes: [] },
              { solver: "openfoam", runnable: true, preferredExecution: "native", nativeCommand: "foamRun", nativeAvailable: true, blockers: [], notes: [] }
            ])
          );
        }
        if (url === "/api/cases/generate") {
          return new Response(
            JSON.stringify({
              id: "case-progressive-test",
              projectName: "Venturi Cavitation Lab",
              solver: "openfoam",
              advancedMode: "incompressible-navier-stokes",
              status: "generated",
              files: {},
              runCommand: ["bash", "Allrun"],
              provenance: []
            })
          );
        }
        if (url === "/api/jobs" && init?.method === "POST") {
          return new Response(
            JSON.stringify({
              id: "job-progressive-test",
              caseId: "case-progressive-test",
              solver: "openfoam",
              status: "running",
              createdAt: "2026-06-11T00:00:00Z",
              updatedAt: "2026-06-11T00:00:01Z",
              execution: "native",
              command: ["bash", "Allrun"],
              logs: ["Time = 0.001"],
              result: {
                logSummary: {
                  solver: "openfoam",
                  lineCount: 1,
                  lastLines: ["Time = 0.001"],
                  latestTime: 0.001,
                  timeSteps: [0.001],
                  residuals: {}
                },
                resultFiles: [
                  {
                    path: "postProcessing/flowlabNative/time_0_001.vtk",
                    size: 100,
                    text: openFoamFixture,
                    time: 0.001,
                    timeText: "0.001",
                    timeSource: "openfoam-time-directory",
                    sourceFields: ["U", "p"]
                  }
                ],
                diagnosticFiles: [],
                progressive: true
              }
            })
          );
        }
        if (url === "/api/jobs/job-progressive-test") return pollPromise;
        return new Response("not found", { status: 404 });
      })
    );

    render(<App />);

    await screen.findByLabelText("Solver runtime readiness");
    fireEvent.change(screen.getByRole("combobox", { name: /^Solver$/i }), { target: { value: "openfoam" } });
    fireEvent.click(screen.getByRole("button", { name: /Run CFD case/i }));

    expect(await screen.findByText("Server job job-progressive-test: running")).toBeTruthy();

    expect(await screen.findByText(/Progressive artifacts: 1 field, 0 diagnostic/)).toBeTruthy();
    expect(screen.getByText(/Snapshot 1\/1/)).toBeTruthy();
    expect(screen.getByText(/Using U from postProcessing\/flowlabNative\/time_0_001\.vtk/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("4");
    expect(screen.getByLabelText("Result timestep")).toHaveAttribute("aria-valuetext", "t0.001 postProcessing/flowlabNative/time_0_001.vtk");
    fireEvent.click(within(screen.getByLabelText("Loaded result fields")).getByTitle("U cell vector, 1 tuple"));
    expect(screen.getByText(/Using U magnitude from postProcessing\/flowlabNative\/time_0_001\.vtk \(cell data, m\/s\)/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("5");

    await act(async () => {
      resolvePoll(
        new Response(
          JSON.stringify({
            id: "job-progressive-test",
            caseId: "case-progressive-test",
            solver: "openfoam",
            status: "complete",
            createdAt: "2026-06-11T00:00:00Z",
            updatedAt: "2026-06-11T00:00:02Z",
            finishedAt: "2026-06-11T00:00:02Z",
            execution: "native",
            command: ["bash", "Allrun"],
            logs: ["Time = 0.001", "Time = 0.002", "Solver process exited successfully with code 0."],
            exitCode: 0,
            result: {
              logSummary: {
                solver: "openfoam",
                lineCount: 3,
                lastLines: ["Time = 0.002"],
                latestTime: 0.002,
                timeSteps: [0.001, 0.002],
                residuals: {}
              },
              resultFiles: [
                {
                  path: "postProcessing/flowlabNative/time_0_001.vtk",
                  size: 100,
                  text: openFoamFixture,
                  time: 0.001,
                  timeText: "0.001",
                  timeSource: "openfoam-time-directory",
                  sourceFields: ["U", "p"]
                },
                {
                  path: "postProcessing/flowlabNative/time_0_002.vtk",
                  size: 100,
                  text: openFoamLaterWithoutCellU,
                  time: 0.002,
                  timeText: "0.002",
                  timeSource: "openfoam-time-directory",
                  sourceFields: ["p"]
                }
              ],
              diagnosticFiles: [],
              patchMetrics: openFoamPatchMetrics,
              progressive: false
            }
          })
        )
      );
      await Promise.resolve();
    });

    expect(await screen.findByText("Job complete")).toBeTruthy();
    expect(screen.getByText(/Snapshot 2\/2/)).toBeTruthy();
    expect(screen.getByText(/Pinned U \(cell vector\) is unavailable in postProcessing\/flowlabNative\/time_0_002\.vtk/)).toBeTruthy();
    expect(screen.getByLabelText("Result timestep")).toHaveAttribute("aria-valuetext", "t0.002 postProcessing/flowlabNative/time_0_002.vtk");
    const timeline = screen.getByLabelText("Result field timeline");
    expect(timeline).toHaveTextContent("field unavailable");
    expect(within(timeline).getByTitle("t0.001 postProcessing/flowlabNative/time_0_001.vtk: mean 5 m/s, max 5 m/s")).toBeTruthy();
    expect(within(timeline).getByTitle("t0.002 postProcessing/flowlabNative/time_0_002.vtk: missing field")).toBeTruthy();
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("1/2 snapshots");
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("Missing 1: postProcessing/flowlabNative/time_0_002.vtk");
    expect(screen.getByLabelText("Result field coverage")).toHaveTextContent("U · cell · magnitude · m/s");
    expect(screen.getByLabelText("Result playback speed")).toHaveValue("1");

    const createdBlobs: Blob[] = [];
    const createObjectUrl = vi.fn((blob: Blob) => {
      createdBlobs.push(blob);
      return "blob:field-timeline";
    });
    const revokeObjectUrl = vi.fn();
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });

    fireEvent.click(screen.getByRole("button", { name: "Export active field timeline CSV" }));

    expect(anchorClick).toHaveBeenCalled();
    expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob));
    const csvText = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(createdBlobs[0]);
    });
    expect(csvText).toContain("1,postProcessing/flowlabNative/time_0_001.vtk,0.001,U,cell,vector-magnitude,m/s,5,5,5");
    expect(csvText).toContain("2,postProcessing/flowlabNative/time_0_002.vtk,0.002,,,,,,,");
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:field-timeline");

    fireEvent.change(screen.getByLabelText("Result playback speed"), { target: { value: "2" } });
    expect(screen.getByLabelText("Result playback speed")).toHaveValue("2");
    fireEvent.click(screen.getByRole("button", { name: "Loop result playback" }));
    expect(screen.getByRole("button", { name: "Loop result playback" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(within(timeline).getByTitle("t0.001 postProcessing/flowlabNative/time_0_001.vtk: mean 5 m/s, max 5 m/s"));

    expect(screen.getByText(/Snapshot 1\/2/)).toBeTruthy();
    expect(screen.getByText(/Using U magnitude from postProcessing\/flowlabNative\/time_0_001\.vtk \(cell data, m\/s\)/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("5");
    expect(screen.getByLabelText("Result timestep")).toHaveAttribute("aria-valuetext", "t0.001 postProcessing/flowlabNative/time_0_001.vtk");

    fireEvent.click(screen.getByRole("button", { name: "Next result timestep" }));
    expect(screen.getByText(/Snapshot 2\/2/)).toBeTruthy();
    expect(screen.getByText(/Pinned U \(cell vector\) is unavailable in postProcessing\/flowlabNative\/time_0_002\.vtk/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous result timestep" }));
    expect(screen.getByText(/Snapshot 1\/2/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Result timestep"), { target: { value: "0" } });

    expect(screen.getByText(/Snapshot 1\/2/)).toBeTruthy();
    expect(screen.getByText(/Using U magnitude from postProcessing\/flowlabNative\/time_0_001\.vtk \(cell data, m\/s\)/)).toBeTruthy();
    expect(screen.getByLabelText("Field min max")).toHaveTextContent("5");
    expect(screen.getByLabelText("Result timestep")).toHaveAttribute("aria-valuetext", "t0.001 postProcessing/flowlabNative/time_0_001.vtk");
  }, 20_000);
});
