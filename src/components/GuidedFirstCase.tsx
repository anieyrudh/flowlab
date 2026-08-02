import type { CSSProperties, ReactElement } from "react";
import type { ChannelShape, FluidProject, PatchMetrics, SimulationResult } from "../types";

/**
 * GuidedFirstCase turns a single straight pipe into a teaching loop:
 * estimate, run, read, and then compare the run against the analytic answer.
 *
 * It is deliberately honest about two things a beginner cannot see:
 *
 *  1. `planar-2d`, FlowLab's default mesh mode, builds a flat one-cell-thick
 *     channel, not a round pipe. A flat channel of gap H obeys plane-Poiseuille
 *     (12*mu*U*L/H^2). A round pipe of diameter D obeys Hagen-Poiseuille
 *     (32*mu*U*L/D^2). At H = D the two differ by the factor 32/12.
 *  2. OpenFOAM writes incompressible pressure in kinematic units (m2/s2). The
 *     patch metric is labelled Pa but carries p/rho, so this component
 *     multiplies it by the fluid density before any comparison.
 *
 * No number here is validated against a physical experiment. The comparison
 * checks the solver against textbook theory only.
 */

const LAMINAR_REYNOLDS_LIMIT = 2300;

export type AnalyticLawId = "plane-poiseuille" | "hagen-poiseuille";

export type AnalyticLaw = {
  id: AnalyticLawId;
  name: string;
  geometry: string;
  formula: string;
  pressureDropPa: number;
};

export type GuidedStepStatus = "done" | "next" | "waiting";

export type GuidedStep = {
  id: string;
  title: string;
  detail: string;
  status: GuidedStepStatus;
};

export type GuidedCfdReading = {
  pressureDropPa: number;
  rawValue: number;
  convertedFromKinematic: boolean;
  fromPatch: string;
  toPatch: string;
};

export type GuidedFirstCaseModel = {
  supported: boolean;
  blockedReason: string | null;
  edgeId: string | null;
  edgeLabel: string | null;
  lengthM: number;
  diameterM: number | null;
  gapM: number;
  meanVelocityMPerS: number;
  meanVelocityFromFlowDemand: boolean;
  reynolds: number;
  laminar: boolean;
  meshModeLabel: string;
  meshGeometryLabel: string;
  matchingLaw: AnalyticLaw | null;
  otherLaw: AnalyticLaw | null;
  estimatePressureDropPa: number | null;
  estimateLengthM: number | null;
  estimateReynolds: number | null;
  cfd: GuidedCfdReading | null;
  errorPercent: number | null;
  steps: GuidedStep[];
};

export type GuidedFirstCaseProps = {
  project: FluidProject;
  result: SimulationResult;
  patchMetrics?: PatchMetrics | null;
};

/** Pressure loss of a fully developed laminar flow in a round pipe. */
export function hagenPoiseuillePressureDropPa(input: {
  dynamicViscosity: number;
  meanVelocityMPerS: number;
  lengthM: number;
  diameterM: number;
}): number {
  const { dynamicViscosity, meanVelocityMPerS, lengthM, diameterM } = input;
  if (diameterM <= 0) return Number.NaN;
  return (32 * dynamicViscosity * meanVelocityMPerS * lengthM) / diameterM ** 2;
}

/** Pressure loss of a fully developed laminar flow between two flat plates. */
export function planePoiseuillePressureDropPa(input: {
  dynamicViscosity: number;
  meanVelocityMPerS: number;
  lengthM: number;
  gapM: number;
}): number {
  const { dynamicViscosity, meanVelocityMPerS, lengthM, gapM } = input;
  if (gapM <= 0) return Number.NaN;
  return (12 * dynamicViscosity * meanVelocityMPerS * lengthM) / gapM ** 2;
}

function shapeArea(shape: ChannelShape): number {
  return shape.kind === "circular" ? (Math.PI * shape.diameter ** 2) / 4 : shape.width * shape.height;
}

function shapeHydraulicDiameter(shape: ChannelShape): number {
  if (shape.kind === "circular") return shape.diameter;
  return (2 * shape.width * shape.height) / (shape.width + shape.height);
}

/** The across-the-gap size the `planar-2d` mesher extrudes for this shape. */
function shapeGap(shape: ChannelShape): number {
  return shape.kind === "circular" ? shape.diameter : shape.height;
}

const MESH_MODE_LABELS: Record<string, string> = {
  "planar-2d": "Planar 2D",
  axisymmetric: "Axisymmetric (3D pipe)",
  "full-ogrid": "Full 360 O-grid",
  "curved-elbow-ogrid": "Canonical 90 degree elbow",
  "y-junction": "Y junction"
};

const ROUND_PIPE_MESH_MODES = new Set(["axisymmetric", "full-ogrid"]);

function unsupported(reason: string): GuidedFirstCaseModel {
  return {
    supported: false,
    blockedReason: reason,
    edgeId: null,
    edgeLabel: null,
    lengthM: 0,
    diameterM: null,
    gapM: 0,
    meanVelocityMPerS: 0,
    meanVelocityFromFlowDemand: false,
    reynolds: 0,
    laminar: false,
    meshModeLabel: "",
    meshGeometryLabel: "",
    matchingLaw: null,
    otherLaw: null,
    estimatePressureDropPa: null,
    estimateLengthM: null,
    estimateReynolds: null,
    cfd: null,
    errorPercent: null,
    steps: []
  };
}

function readCfdPressureDrop(project: FluidProject, patchMetrics: PatchMetrics | null | undefined): GuidedCfdReading | null {
  const drop = patchMetrics?.pressureDrops?.[0];
  if (!drop || !Number.isFinite(drop.deltaP)) return null;
  // OpenFOAM incompressible cases solve for kinematic pressure p/rho in m2/s2.
  // FlowLab labels the patch metric Pa but does not scale it, so scale it here.
  const convertedFromKinematic = project.solver.advancedMode === "incompressible-navier-stokes";
  const density = project.fluid.density > 0 ? project.fluid.density : 1;
  return {
    pressureDropPa: convertedFromKinematic ? drop.deltaP * density : drop.deltaP,
    rawValue: drop.deltaP,
    convertedFromKinematic,
    fromPatch: drop.fromPatch,
    toPatch: drop.toPatch
  };
}

/**
 * Builds everything the panel shows. Kept pure so the arithmetic is testable
 * without React.
 */
export function guidedFirstCaseModel(
  project: FluidProject,
  result: SimulationResult,
  patchMetrics?: PatchMetrics | null
): GuidedFirstCaseModel {
  const edges = Object.values(project.edges);
  if (edges.length !== 1) {
    return unsupported("The guided comparison needs a project with one pipe. This project has " + edges.length + ".");
  }

  const edge = edges[0];
  if (edge.type !== "pipe") {
    return unsupported("The guided comparison needs a straight pipe. This component is a " + edge.type + ".");
  }
  if (!Number.isFinite(edge.length) || edge.length <= 0) {
    return unsupported("The pipe needs a positive length.");
  }

  const meshMode = project.solver.meshMode ?? "planar-2d";
  if (!ROUND_PIPE_MESH_MODES.has(meshMode) && meshMode !== "planar-2d") {
    return unsupported("The guided comparison covers the straight-pipe mesh modes only. This case uses " + (MESH_MODE_LABELS[meshMode] ?? meshMode) + ".");
  }

  const shape = edge.shape;
  const area = shapeArea(shape);
  if (!(area > 0)) return unsupported("The pipe needs a positive cross-section.");

  // The OpenFOAM case takes its inlet speed from the sink flow demand divided
  // by the pipe area. With no flow demand the case falls back to 1 m/s.
  const sink = Object.values(project.nodes).find((node) => node.type === "sink");
  const flowDemand = sink?.flowDemand;
  const meanVelocityFromFlowDemand = typeof flowDemand === "number" && Number.isFinite(flowDemand) && flowDemand !== 0;
  const meanVelocityMPerS = meanVelocityFromFlowDemand ? Math.abs(flowDemand) / area : 1;

  const hydraulicDiameter = shapeHydraulicDiameter(shape);
  const reynolds = (project.fluid.density * meanVelocityMPerS * hydraulicDiameter) / project.fluid.dynamicViscosity;
  const gapM = shapeGap(shape);
  const diameterM = shape.kind === "circular" ? shape.diameter : null;

  const planeLaw: AnalyticLaw = {
    id: "plane-poiseuille",
    name: "Plane-Poiseuille",
    geometry: "Flat channel, gap " + formatLength(gapM),
    formula: "12 * mu * U * L / H^2",
    pressureDropPa: planePoiseuillePressureDropPa({
      dynamicViscosity: project.fluid.dynamicViscosity,
      meanVelocityMPerS,
      lengthM: edge.length,
      gapM
    })
  };
  const roundLaw: AnalyticLaw | null =
    diameterM === null
      ? null
      : {
          id: "hagen-poiseuille",
          name: "Hagen-Poiseuille",
          geometry: "Round pipe, diameter " + formatLength(diameterM),
          formula: "32 * mu * U * L / D^2",
          pressureDropPa: hagenPoiseuillePressureDropPa({
            dynamicViscosity: project.fluid.dynamicViscosity,
            meanVelocityMPerS,
            lengthM: edge.length,
            diameterM
          })
        };

  const roundPipeMesh = ROUND_PIPE_MESH_MODES.has(meshMode);
  if (roundPipeMesh && !roundLaw) {
    return unsupported("A round-pipe mesh mode needs a circular pipe.");
  }
  const matchingLaw = roundPipeMesh ? roundLaw : planeLaw;
  const otherLaw = roundPipeMesh ? planeLaw : roundLaw;

  const edgeResult = result.edgeResults[edge.id] ?? null;
  const estimatePressureDropPa = edgeResult && Number.isFinite(edgeResult.pressureDrop) ? edgeResult.pressureDrop : null;
  const estimateLengthM = edgeResult && Number.isFinite(edgeResult.effectiveLength) ? edgeResult.effectiveLength : null;
  const estimateReynolds = edgeResult && Number.isFinite(edgeResult.reynolds) ? edgeResult.reynolds : null;

  const cfd = readCfdPressureDrop(project, patchMetrics);
  const errorPercent =
    cfd && matchingLaw && Number.isFinite(matchingLaw.pressureDropPa) && matchingLaw.pressureDropPa !== 0
      ? ((cfd.pressureDropPa - matchingLaw.pressureDropPa) / matchingLaw.pressureDropPa) * 100
      : null;

  const laminar = reynolds < LAMINAR_REYNOLDS_LIMIT;
  const openfoamSelected = project.solver.tier === "openfoam";
  const steadySelected = project.solver.runMode === "steady";

  const steps: GuidedStep[] = [
    {
      id: "open",
      title: "Open a straight pipe",
      detail: edge.label + ": length " + formatLength(edge.length) + ", " + (diameterM === null ? "gap " + formatLength(gapM) : "diameter " + formatLength(diameterM)) + ".",
      status: "done"
    },
    {
      id: "estimate",
      title: "Read the instant estimate",
      detail:
        estimateReynolds === null
          ? "Click 02 Estimate."
          : "Reynolds " +
            formatNumber(estimateReynolds, 1) +
            (estimateReynolds < LAMINAR_REYNOLDS_LIMIT
              ? ". The flow is laminar, so it is inside FlowLab's evidence range."
              : ". WARNING: this is above 2300. FlowLab has no accuracy evidence here."),
      status: estimateReynolds === null ? "next" : "done"
    },
    {
      id: "run",
      title: "Run the CFD case",
      detail: cfd
        ? "The run gave a pressure drop between " + cfd.fromPatch + " and " + cfd.toPatch + "."
        : openfoamSelected
          ? steadySelected
            ? "Click Generate and queue experimental CFD case."
            : "Set Run mode to Steady. Transient does not converge to a pressure drop."
          : "Set Solver to OpenFOAM in the Inspector.",
      status: cfd ? "done" : estimateReynolds === null ? "waiting" : "next"
    },
    {
      id: "read",
      title: "Read the result",
      detail: cfd
        ? "Pressure drop " + formatPressure(cfd.pressureDropPa) + "."
        : "The pressure drop appears here after the run.",
      status: cfd ? "done" : "waiting"
    },
    {
      id: "compare",
      title: "Compare with the analytic answer",
      detail:
        errorPercent === null || !matchingLaw
          ? "The comparison appears here after the run."
          : "The run is " + formatSignedPercent(errorPercent) + " away from " + matchingLaw.name + ".",
      status: errorPercent === null ? "waiting" : "done"
    }
  ];

  return {
    supported: true,
    blockedReason: null,
    edgeId: edge.id,
    edgeLabel: edge.label,
    lengthM: edge.length,
    diameterM,
    gapM,
    meanVelocityMPerS,
    meanVelocityFromFlowDemand,
    reynolds,
    laminar,
    meshModeLabel: MESH_MODE_LABELS[meshMode] ?? meshMode,
    meshGeometryLabel: roundPipeMesh ? "round pipe" : "flat one-cell-thick channel",
    matchingLaw,
    otherLaw,
    estimatePressureDropPa,
    estimateLengthM,
    estimateReynolds,
    cfd,
    errorPercent,
    steps
  };
}

function formatNumber(value: number, digits = 3): string {
  if (!Number.isFinite(value)) return "-";
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 1e-3 || magnitude >= 1e6)) return value.toExponential(3);
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function formatLength(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return value < 0.1 ? formatNumber(value * 1000, 2) + " mm" : formatNumber(value, 4) + " m";
}

function formatPressure(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return Math.abs(value) >= 1000 ? formatNumber(value / 1000, 3) + " kPa" : formatNumber(value, 3) + " Pa";
}

function formatSignedPercent(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return (value >= 0 ? "+" : "-") + formatNumber(Math.abs(value), 2) + "%";
}

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 12,
  border: "1px solid var(--line, rgba(183, 199, 210, 0.16))",
  borderRadius: "var(--radius, 8px)",
  background: "var(--panel-soft, #0c1116)",
  color: "var(--text, #eef8ff)",
  fontSize: 12,
  lineHeight: 1.45
};

const headingStyle: CSSProperties = { margin: 0, fontSize: 12, letterSpacing: "0.06em", textTransform: "uppercase" };
const mutedStyle: CSSProperties = { margin: 0, color: "var(--muted, #9aa9b4)" };
const listStyle: CSSProperties = { listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 };
const tableStyle: CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 12 };
const cellStyle: CSSProperties = { textAlign: "left", padding: "4px 6px", borderBottom: "1px solid var(--line, rgba(183, 199, 210, 0.16))" };
const numberCellStyle: CSSProperties = { ...cellStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" };

const STATUS_MARK: Record<GuidedStepStatus, string> = { done: "[x]", next: "[>]", waiting: "[ ]" };

export function GuidedFirstCase({ project, result, patchMetrics = null }: GuidedFirstCaseProps): ReactElement {
  const model = guidedFirstCaseModel(project, result, patchMetrics);

  if (!model.supported) {
    return (
      <section style={panelStyle} aria-label="Guided first case" data-testid="guided-first-case">
        <h3 style={headingStyle}>Guided first case</h3>
        <p style={mutedStyle}>{model.blockedReason}</p>
        <p style={mutedStyle}>Open the Laminar Starter Pipe preset to use this guide.</p>
      </section>
    );
  }

  const matching = model.matchingLaw;

  return (
    <section style={panelStyle} aria-label="Guided first case" data-testid="guided-first-case">
      <h3 style={headingStyle}>Guided first case</h3>

      <ol style={listStyle} data-testid="guided-first-case-steps">
        {model.steps.map((step, index) => (
          <li key={step.id} data-testid={"guided-step-" + step.id} data-status={step.status}>
            <strong>
              <span aria-hidden="true">{STATUS_MARK[step.status]}</span> {index + 1}. {step.title}
            </strong>
            <div style={mutedStyle}>{step.detail}</div>
          </li>
        ))}
      </ol>

      <p style={mutedStyle} data-testid="guided-operating-point">
        Operating point: mean speed {formatNumber(model.meanVelocityMPerS, 4)} m/s, Reynolds {formatNumber(model.reynolds, 1)}.{" "}
        {model.laminar
          ? "The flow is laminar."
          : "WARNING: Reynolds is at or above 2300. FlowLab has no accuracy evidence above this value."}
        {model.meanVelocityFromFlowDemand
          ? ""
          : " The pipe has no flow demand, so the CFD case uses its 1 m/s fallback speed."}
      </p>

      <p style={mutedStyle} data-testid="guided-mesh-note">
        Mesh mode {model.meshModeLabel} builds a {model.meshGeometryLabel}. The law that matches it is{" "}
        {matching ? matching.name : "-"}.
      </p>

      <table style={tableStyle} data-testid="guided-comparison">
        <caption style={{ ...mutedStyle, captionSide: "bottom", paddingTop: 6, textAlign: "left" }}>
          Analytic values use the pipe length {formatLength(model.lengthM)} and the mean speed{" "}
          {formatNumber(model.meanVelocityMPerS, 4)} m/s.
        </caption>
        <thead>
          <tr>
            <th style={cellStyle} scope="col">
              Source
            </th>
            <th style={numberCellStyle} scope="col">
              Pressure drop
            </th>
            <th style={numberCellStyle} scope="col">
              Error
            </th>
          </tr>
        </thead>
        <tbody>
          <tr data-testid="guided-row-estimate">
            <th style={cellStyle} scope="row">
              Instant 1D estimate
              {model.estimateLengthM === null ? null : (
                <div style={mutedStyle}>Uses the port-to-port length {formatLength(model.estimateLengthM)}.</div>
              )}
            </th>
            <td style={numberCellStyle}>
              {model.estimatePressureDropPa === null ? "-" : formatPressure(model.estimatePressureDropPa)}
            </td>
            <td style={numberCellStyle}>-</td>
          </tr>
          <tr data-testid="guided-row-cfd">
            <th style={cellStyle} scope="row">
              CFD run ({model.meshModeLabel})
              {model.cfd?.convertedFromKinematic ? (
                <div style={mutedStyle}>
                  Converted from kinematic pressure {formatNumber(model.cfd.rawValue, 4)} m2/s2 with density{" "}
                  {formatNumber(project.fluid.density, 1)} kg/m3.
                </div>
              ) : null}
            </th>
            <td style={numberCellStyle} data-testid="guided-cfd-pressure">
              {model.cfd === null ? "No run yet" : formatPressure(model.cfd.pressureDropPa)}
            </td>
            <td style={numberCellStyle} data-testid="guided-cfd-error">
              {model.errorPercent === null ? "-" : formatSignedPercent(model.errorPercent)}
            </td>
          </tr>
          {matching ? (
            <tr data-testid="guided-row-matching-law">
              <th style={cellStyle} scope="row">
                {matching.name} (analytic)
                <div style={mutedStyle}>
                  {matching.geometry}. {matching.formula}.
                </div>
              </th>
              <td style={numberCellStyle}>{formatPressure(matching.pressureDropPa)}</td>
              <td style={numberCellStyle}>reference</td>
            </tr>
          ) : null}
          {model.otherLaw ? (
            <tr data-testid="guided-row-other-law">
              <th style={cellStyle} scope="row">
                {model.otherLaw.name} (other geometry)
                <div style={mutedStyle}>
                  {model.otherLaw.geometry}. {model.otherLaw.formula}.
                </div>
              </th>
              <td style={numberCellStyle}>{formatPressure(model.otherLaw.pressureDropPa)}</td>
              <td style={numberCellStyle}>not applicable</td>
            </tr>
          ) : null}
        </tbody>
      </table>

      <p style={mutedStyle} data-testid="guided-honesty-note">
        The analytic value is the fully developed answer. The CFD case starts with a flat speed profile at the inlet, thus
        the run includes an entrance loss. No FlowLab result is validated against a physical experiment. This comparison
        tests the solver against theory only.
      </p>
    </section>
  );
}
