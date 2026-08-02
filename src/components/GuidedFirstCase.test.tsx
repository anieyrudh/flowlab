import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  GuidedFirstCase,
  guidedFirstCaseModel,
  hagenPoiseuillePressureDropPa,
  planePoiseuillePressureDropPa
} from "./GuidedFirstCase";
import { laminarStarterPreset, venturiPreset } from "../data/presets";
import { solveHydraulicNetwork } from "../physics/hydraulics";
import { normalizeProject } from "../projectSchema";
import type { FluidProject, PatchMetrics } from "../types";

afterEach(cleanup);

const starter = normalizeProject(structuredClone(laminarStarterPreset));
const starterResult = solveHydraulicNetwork(starter);

function patchMetricsWithDrop(deltaP: number): PatchMetrics {
  return {
    schema: "flowlab.patch_metrics.v1",
    status: "complete",
    patches: {},
    flowBalance: null,
    pressureDrops: [
      { fromPatch: "inlet", toPatch: "outlet", inletPressure: deltaP, outletPressure: 0, deltaP, unit: "Pa" }
    ],
    forces: [],
    pressureProbes: [],
    warnings: [],
    sources: []
  };
}

describe("analytic laminar pressure laws", () => {
  it("gives 13.92 kPa for the starter pipe as a round pipe", () => {
    // 32 * 0.087 * 1.0 * 2.0 / 0.02^2 = 5.568 / 4e-4 = 13920 Pa
    expect(
      hagenPoiseuillePressureDropPa({ dynamicViscosity: 0.087, meanVelocityMPerS: 1, lengthM: 2, diameterM: 0.02 })
    ).toBeCloseTo(13_920, 6);
  });

  it("gives 5.22 kPa for the starter pipe as a flat channel", () => {
    // 12 * 0.087 * 1.0 * 2.0 / 0.02^2 = 2.088 / 4e-4 = 5220 Pa
    expect(
      planePoiseuillePressureDropPa({ dynamicViscosity: 0.087, meanVelocityMPerS: 1, lengthM: 2, gapM: 0.02 })
    ).toBeCloseTo(5_220, 6);
  });

  it("keeps the 32/12 ratio between the round pipe and the flat channel", () => {
    const round = hagenPoiseuillePressureDropPa({
      dynamicViscosity: 0.087,
      meanVelocityMPerS: 1,
      lengthM: 2,
      diameterM: 0.02
    });
    const flat = planePoiseuillePressureDropPa({
      dynamicViscosity: 0.087,
      meanVelocityMPerS: 1,
      lengthM: 2,
      gapM: 0.02
    });
    expect(round / flat).toBeCloseTo(32 / 12, 12);
  });
});

describe("the laminar starter preset", () => {
  it("runs inside the laminar evidence range", () => {
    const edge = starterResult.edgeResults.tube;
    expect(edge.regime).toBe("laminar");
    expect(edge.reynolds).toBeLessThan(2300);
    // Exactly the design point now the solve is self-consistent.
    expect(edge.reynolds).toBeCloseTo(200, 6);
    expect(edge.velocity).toBeCloseTo(1, 6);
    expect(starterResult.stable).toBe(true);
    expect(starterResult.warnings).toEqual([]);
  });

  it("gives the CFD case the same operating point as the estimate", () => {
    const model = guidedFirstCaseModel(starter, starterResult);
    expect(model.meanVelocityFromFlowDemand).toBe(true);
    expect(model.meanVelocityMPerS).toBeCloseTo(1, 9);
    expect(model.reynolds).toBeCloseTo(200, 9);
    expect(model.laminar).toBe(true);
  });
});

describe("guidedFirstCaseModel", () => {
  it("matches planar-2d against the flat-channel law", () => {
    const model = guidedFirstCaseModel(starter, starterResult);
    expect(model.meshGeometryLabel).toBe("flat one-cell-thick channel");
    expect(model.matchingLaw?.id).toBe("plane-poiseuille");
    expect(model.matchingLaw?.pressureDropPa).toBeCloseTo(5_220, 6);
    expect(model.otherLaw?.id).toBe("hagen-poiseuille");
    expect(model.otherLaw?.pressureDropPa).toBeCloseTo(13_920, 6);
  });

  it("matches axisymmetric against the round-pipe law", () => {
    const project: FluidProject = {
      ...starter,
      solver: { ...starter.solver, meshMode: "axisymmetric" }
    };
    const model = guidedFirstCaseModel(project, starterResult);
    expect(model.meshGeometryLabel).toBe("round pipe");
    expect(model.matchingLaw?.id).toBe("hagen-poiseuille");
    expect(model.matchingLaw?.pressureDropPa).toBeCloseTo(13_920, 6);
  });

  it("converts the incompressible kinematic patch pressure into pascals", () => {
    // 6 m2/s2 * 870 kg/m3 = 5220 Pa, which is the exact analytic answer.
    const model = guidedFirstCaseModel(starter, starterResult, patchMetricsWithDrop(6));
    expect(model.cfd?.convertedFromKinematic).toBe(true);
    expect(model.cfd?.pressureDropPa).toBeCloseTo(5_220, 6);
    expect(model.errorPercent).toBeCloseTo(0, 9);
  });

  it("reports a signed error against the matching law", () => {
    const model = guidedFirstCaseModel(starter, starterResult, patchMetricsWithDrop(6.3));
    // 6.3 * 870 = 5481 Pa, which is 5% above 5220 Pa.
    expect(model.cfd?.pressureDropPa).toBeCloseTo(5_481, 6);
    expect(model.errorPercent).toBeCloseTo(5, 9);
  });

  it("does not scale compressible pressure, which is already in pascals", () => {
    const project: FluidProject = {
      ...starter,
      solver: { ...starter.solver, advancedMode: "compressible-flow" }
    };
    const model = guidedFirstCaseModel(project, starterResult, patchMetricsWithDrop(5_220));
    expect(model.cfd?.convertedFromKinematic).toBe(false);
    expect(model.cfd?.pressureDropPa).toBeCloseTo(5_220, 6);
  });

  it("marks the flow as outside the evidence range above Reynolds 2300", () => {
    const project: FluidProject = {
      ...starter,
      fluid: { ...starter.fluid, dynamicViscosity: 0.0005 }
    };
    const model = guidedFirstCaseModel(project, starterResult);
    expect(model.reynolds).toBeGreaterThan(2300);
    expect(model.laminar).toBe(false);
  });

  it("refuses a project that is not one straight pipe", () => {
    const model = guidedFirstCaseModel(venturiPreset, solveHydraulicNetwork(venturiPreset));
    expect(model.supported).toBe(false);
    expect(model.blockedReason).toContain("one pipe");
  });

  it("advances the steps as the case progresses", () => {
    const before = guidedFirstCaseModel(starter, starterResult);
    expect(before.steps.find((step) => step.id === "compare")?.status).toBe("waiting");

    const after = guidedFirstCaseModel(starter, starterResult, patchMetricsWithDrop(6));
    expect(after.steps.find((step) => step.id === "run")?.status).toBe("done");
    expect(after.steps.find((step) => step.id === "compare")?.status).toBe("done");
  });
});

describe("GuidedFirstCase panel", () => {
  it("shows the five steps and both laminar laws", () => {
    render(<GuidedFirstCase project={starter} result={starterResult} />);

    expect(screen.getByTestId("guided-first-case-steps").children).toHaveLength(5);
    expect(screen.getByTestId("guided-mesh-note")).toHaveTextContent("flat one-cell-thick channel");
    expect(screen.getByTestId("guided-row-matching-law")).toHaveTextContent("Plane-Poiseuille");
    expect(screen.getByTestId("guided-row-other-law")).toHaveTextContent("Hagen-Poiseuille");
    expect(screen.getByTestId("guided-cfd-pressure")).toHaveTextContent("No run yet");
  });

  it("shows the measured value, the analytic value, and the error after a run", () => {
    render(<GuidedFirstCase project={starter} result={starterResult} patchMetrics={patchMetricsWithDrop(6.3)} />);

    expect(screen.getByTestId("guided-cfd-pressure")).toHaveTextContent("5.481 kPa");
    expect(screen.getByTestId("guided-row-matching-law")).toHaveTextContent("5.22 kPa");
    expect(screen.getByTestId("guided-cfd-error")).toHaveTextContent("+5%");
  });

  it("never claims experimental validation", () => {
    render(<GuidedFirstCase project={starter} result={starterResult} patchMetrics={patchMetricsWithDrop(6)} />);
    expect(screen.getByTestId("guided-honesty-note")).toHaveTextContent(
      "No FlowLab result is validated against a physical experiment."
    );
  });

  it("explains why it cannot guide a multi-component project", () => {
    render(<GuidedFirstCase project={venturiPreset} result={solveHydraulicNetwork(venturiPreset)} />);
    expect(screen.getByTestId("guided-first-case")).toHaveTextContent("one pipe");
  });
});
