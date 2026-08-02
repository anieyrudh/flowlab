import { describe, expect, it } from "vitest";
import { presets, pipeLossPreset, venturiPreset } from "../src/data/presets";
import { flowRegime, frictionFactor, solveHydraulicNetwork } from "../src/physics/hydraulics";
import { runSweep } from "../src/physics/sweeps";
import type { FluidNode, FluidProject, PipePortId, Vec2 } from "../src/types";

function expectPositiveFinite(value: number): void {
  expect(Number.isFinite(value)).toBe(true);
  expect(value).toBeGreaterThan(0);
}

function expectNonNegativeFinite(value: number): void {
  expect(Number.isFinite(value)).toBe(true);
  expect(value).toBeGreaterThanOrEqual(0);
}

function nodeRadius(node: FluidNode): number {
  if (node.type === "source" || node.type === "sink") return 17;
  if (node.type === "pump") return 19;
  return 14;
}

function portAngle(node: FluidNode, port: PipePortId): number {
  const base = node.rotation ?? 0;
  if (port === "outlet") return base;
  if (port === "inlet") return base + 180;
  if (port === "north") return base - 90;
  return base + 90;
}

function portPosition(node: FluidNode, port: PipePortId): Vec2 {
  const radius = nodeRadius(node) + 10;
  const angle = (portAngle(node, port) * Math.PI) / 180;
  return {
    x: node.position.x + Math.cos(angle) * radius,
    y: node.position.y + Math.sin(angle) * radius
  };
}

function portDirection(project: FluidProject, edgeId: string): Vec2 {
  const edge = project.edges[edgeId];
  const from = portPosition(project.nodes[edge.from], edge.fromPort ?? "outlet");
  const to = portPosition(project.nodes[edge.to], edge.toPort ?? "inlet");
  const length = Math.hypot(to.x - from.x, to.y - from.y);
  return { x: (to.x - from.x) / length, y: (to.y - from.y) / length };
}

function portSpanLength(project: FluidProject, edgeId: string): number {
  const edge = project.edges[edgeId];
  const from = portPosition(project.nodes[edge.from], edge.fromPort ?? "outlet");
  const to = portPosition(project.nodes[edge.to], edge.toPort ?? "inlet");
  return Math.hypot(to.x - from.x, to.y - from.y);
}

function expectedEffectiveLength(project: FluidProject, edgeId: string): number {
  const edge = project.edges[edgeId];
  const from = project.nodes[edge.from];
  const to = project.nodes[edge.to];
  const centerLength = Math.hypot(to.position.x - from.position.x, to.position.y - from.position.y) || 1;
  return edge.length * portSpanLength(project, edgeId) / centerLength;
}

function tier1PortGeometryProject(fromPort: PipePortId, toPort: PipePortId): FluidProject {
  return {
    version: 1,
    name: "Port geometry hydraulics",
    fluid: {
      density: 998,
      dynamicViscosity: 1.002e-3,
      vaporPressure: 2_340,
      bulkModulus: 2.2e9,
      temperature: 293.15
    },
    solver: {
      tier: "instant-1d",
      advancedMode: "incompressible-navier-stokes",
      turbulence: "rans-sst",
      meshResolution: "coarse",
      maxIterations: 1200,
      tolerance: 1e-6
    },
    visualization: {
      mode: "simulate",
      overlay: "velocity",
      particles: true,
      streamlines: true,
      grid: true
    },
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: {
      source: {
        id: "source",
        type: "source",
        label: "Source",
        position: { x: 0, y: 0 },
        rotation: 0,
        elevation: 1,
        pressure: 220_000,
        boundary: "pressure"
      },
      sink: {
        id: "sink",
        type: "sink",
        label: "Sink",
        position: { x: 160, y: 0 },
        rotation: 0,
        elevation: 1,
        pressure: 120_000,
        boundary: "pressure"
      }
    },
    edges: {
      pipe: {
        id: "pipe",
        type: "pipe",
        label: "Port-aware pipe",
        from: "source",
        to: "sink",
        fromPort,
        toPort,
        length: 12,
        shape: { kind: "circular", diameter: 0.08 },
        roughness: 0.000045,
        minorLossK: 0.2
      }
    },
    sweeps: []
  };
}

describe("hydraulic correlations", () => {
  it("uses the 64/Re laminar friction factor", () => {
    expect(frictionFactor(1600, 0.000045, 0.1)).toBeCloseTo(64 / 1600, 12);
    expect(frictionFactor(0, 0.000045, 0.1)).toBe(0);
  });

  it("classifies Reynolds regimes at solver thresholds", () => {
    expect(flowRegime(2299.999)).toBe("laminar");
    expect(flowRegime(2300)).toBe("transitional");
    expect(flowRegime(4000)).toBe("transitional");
    expect(flowRegime(4000.001)).toBe("turbulent");
  });
});

describe("preset solves", () => {
  it("produces edge results, control volumes, and water hammer outputs for each preset", () => {
    for (const preset of presets) {
      const result = solveHydraulicNetwork(preset);
      const edgeIds = Object.keys(preset.edges);

      expect(result.stable).toBe(true);
      expect(result.converged).toBe(true);
      expect(Object.keys(result.edgeResults)).toEqual(edgeIds);
      expect(Object.keys(result.controlVolumes)).toEqual(edgeIds);
      expect(Object.keys(result.waterHammer)).toEqual(edgeIds);

      for (const edgeId of edgeIds) {
        const solved = result.edgeResults[edgeId];

        expectNonNegativeFinite(Math.abs(solved.flowRate));
        expectNonNegativeFinite(Math.abs(solved.velocity));
        expectNonNegativeFinite(solved.reynolds);
        expectNonNegativeFinite(solved.frictionFactor);
        expectPositiveFinite(solved.effectiveLength);
        expectNonNegativeFinite(solved.bendAngle);
        expectNonNegativeFinite(solved.geometryMinorLossK);
        expectNonNegativeFinite(solved.majorHeadLoss);
        expectNonNegativeFinite(solved.minorHeadLoss);
        expectNonNegativeFinite(solved.pressureDrop);
        expect(solved.regime).toBe(flowRegime(solved.reynolds));

        const hammer = result.waterHammer[edgeId];
        const expectedWaveSpeed = Math.sqrt(preset.fluid.bulkModulus / preset.fluid.density);
        expectPositiveFinite(hammer.waveSpeed);
        expect(hammer.waveSpeed).toBeCloseTo(expectedWaveSpeed, 10);
        expect(hammer.pressureRise).toBeCloseTo(
          preset.fluid.density * expectedWaveSpeed * Math.abs(solved.velocity),
          8
        );
        expect(hammer.criticalClosureTime).toBeCloseTo((2 * solved.effectiveLength) / expectedWaveSpeed, 10);
      }
    }
  });

  it("emits a cavitation warning for the venturi preset", () => {
    const result = solveHydraulicNetwork(venturiPreset);
    const inlet = result.edgeResults.inlet;

    expect(inlet.cavitationRisk).toBe(true);
    expect(result.warnings).toContainEqual(
      expect.objectContaining({
        id: "cavitation-inlet",
        severity: "warning",
        targetId: "inlet"
      })
    );
  });
});

describe("sweep execution", () => {
  it("runs the configured number of steps and includes endpoint values", () => {
    const sweep = pipeLossPreset.sweeps[0];
    const runs = runSweep(pipeLossPreset, sweep);

    expect(runs).toHaveLength(sweep.steps);
    expect(runs[0]).toMatchObject({ index: 0, value: sweep.min });
    expect(runs.at(-1)).toMatchObject({ index: sweep.steps - 1, value: sweep.max });

    for (const run of runs) {
      expect(run.result.edgeResults.pipe).toBeDefined();
      expectPositiveFinite(Math.abs(run.result.edgeResults.pipe.velocity));
    }
  });
});

describe("port-aware Tier 1 hydraulics", () => {
  it("uses selected port geometry for effective edge length and elevation grade", () => {
    const shortLevelRun = solveHydraulicNetwork(tier1PortGeometryProject("outlet", "inlet"));
    const longerRaisedRun = solveHydraulicNetwork(tier1PortGeometryProject("inlet", "north"));

    const shortPipe = shortLevelRun.edgeResults.pipe;
    const longerRaisedPipe = longerRaisedRun.edgeResults.pipe;
    const shortHammer = shortLevelRun.waterHammer.pipe;
    const longerRaisedHammer = longerRaisedRun.waterHammer.pipe;

    expectPositiveFinite(Math.abs(shortPipe.flowRate));
    expectPositiveFinite(Math.abs(longerRaisedPipe.flowRate));
    expect(shortPipe.effectiveLength).toBeCloseTo(expectedEffectiveLength(tier1PortGeometryProject("outlet", "inlet"), "pipe"), 10);
    expect(longerRaisedPipe.effectiveLength).toBeCloseTo(
      expectedEffectiveLength(tier1PortGeometryProject("inlet", "north"), "pipe"),
      10
    );
    expect(longerRaisedPipe.effectiveLength).toBeGreaterThan(shortPipe.effectiveLength);
    expect(shortPipe.flowRate).not.toBeCloseTo(longerRaisedPipe.flowRate, 10);
    expect(shortHammer.criticalClosureTime).not.toBeCloseTo(longerRaisedHammer.criticalClosureTime, 10);
  });

  it("adds bend/minor loss from the turn between selected ports", () => {
    const straightProject = tier1PortGeometryProject("outlet", "inlet");
    const elbowProject = tier1PortGeometryProject("north", "inlet");
    straightProject.edges.pipe = { ...straightProject.edges.pipe, type: "bend", minorLossK: 0 };
    elbowProject.edges.pipe = { ...elbowProject.edges.pipe, type: "bend", minorLossK: 0 };

    const straight = solveHydraulicNetwork(straightProject).edgeResults.pipe;
    const elbow = solveHydraulicNetwork(elbowProject).edgeResults.pipe;

    expect(straight.bendAngle).toBeCloseTo(0, 10);
    expect(straight.geometryMinorLossK).toBeCloseTo(0, 10);
    expect(elbow.bendAngle).toBeGreaterThan(straight.bendAngle);
    expect(elbow.geometryMinorLossK).toBeGreaterThan(straight.geometryMinorLossK);
    expect(elbow.minorHeadLoss).toBeGreaterThan(straight.minorHeadLoss);
    expect(Math.abs(elbow.flowRate)).toBeLessThan(Math.abs(straight.flowRate));

    // Once the flow agrees with its own friction factor, the loss identically
    // equals the driving head whatever the resistance: v^2 = 2*g*dH/resistance,
    // so rho*resistance*v^2/2 collapses to rho*g*dH. Resistance therefore sets
    // the flow, not the pressure drop, and the bend shows up as less flow
    // (asserted above). The source is at 220 kPa and the sink at 120 kPa, at
    // equal elevation, so both runs must consume exactly 100 kPa.
    expect(straight.pressureDrop).toBeCloseTo(100_000, 6);
    expect(elbow.pressureDrop).toBeCloseTo(100_000, 6);
  });

  it("matches Hagen-Poiseuille for a laminar round pipe", () => {
    // The whole point of iterating: a single pass seeded at Re = 100,000 cannot
    // reproduce a laminar answer. Oil keeps a normal-sized pipe laminar.
    const density = 870;
    const viscosity = 0.087;
    const diameter = 0.02;
    const length = 12;
    const project = tier1PortGeometryProject("outlet", "inlet");
    project.fluid = { ...project.fluid, density, dynamicViscosity: viscosity, vaporPressure: 100 };
    project.nodes.source = { ...project.nodes.source, pressure: 101_325 + 4_000, elevation: 0 };
    project.nodes.sink = { ...project.nodes.sink, pressure: 101_325, elevation: 0 };
    project.edges.pipe = {
      ...project.edges.pipe,
      length,
      shape: { kind: "circular", diameter },
      roughness: 0.0000015,
      minorLossK: 0
    };

    const solved = solveHydraulicNetwork(project);
    const pipe = solved.edgeResults.pipe;
    expect(solved.converged).toBe(true);
    expect(pipe.reynolds).toBeLessThan(2300);

    // Hagen-Poiseuille over the length the solver actually used.
    const velocity = Math.abs(pipe.flowRate) / (Math.PI * (diameter / 2) ** 2);
    const analytic = (32 * viscosity * velocity * pipe.effectiveLength) / diameter ** 2;
    expect(pipe.majorHeadLoss * density * 9.80665).toBeCloseTo(analytic, 6);
  });

  it("orients control-volume reaction force along the selected port-to-port span", () => {
    const project = tier1PortGeometryProject("south", "north");
    const result = solveHydraulicNetwork(project);
    const controlVolume = result.controlVolumes.pipe;
    const direction = portDirection(project, "pipe");
    const netAxialForce = controlVolume.pressureForce - controlVolume.momentumFlux;

    expect(Math.abs(direction.y)).toBeGreaterThan(0.1);
    expect(controlVolume.reactionForce.y).not.toBeCloseTo(0, 10);
    expect(Math.sign(controlVolume.reactionForce.y)).toBe(Math.sign(netAxialForce * direction.y));
  });
});

describe("network topology validation", () => {
  it("reports self-loop, invalid direction, and duplicate component ports as blocking errors", () => {
    const project = tier1PortGeometryProject("outlet", "inlet");
    project.edges.self = {
      ...project.edges.pipe,
      id: "self",
      label: "Self loop",
      from: "source",
      to: "source"
    };
    project.edges.badDirection = {
      ...project.edges.pipe,
      id: "badDirection",
      label: "Bad direction",
      fromPort: "inlet",
      toPort: "outlet"
    };
    project.edges.duplicate = {
      ...project.edges.pipe,
      id: "duplicate",
      label: "Duplicate outlet",
      toPort: "north"
    };

    const result = solveHydraulicNetwork(project);

    expect(result.stable).toBe(false);
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "self-loop-self", severity: "error" }));
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "invalid-from-port-badDirection", severity: "error" }));
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "invalid-to-port-badDirection", severity: "error" }));
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "duplicate-port-source-outlet", severity: "error" }));
  });

  it("reports disconnected and ambiguous live networks without rejecting the whole project", () => {
    const project = tier1PortGeometryProject("outlet", "inlet");
    project.nodes.orphan = {
      id: "orphan",
      type: "junction",
      label: "Orphan junction",
      position: { x: 50, y: 120 },
      rotation: 0,
      elevation: 0
    };
    project.nodes.deadEnd = {
      id: "deadEnd",
      type: "junction",
      label: "Dead-end junction",
      position: { x: 220, y: 120 },
      rotation: 0,
      elevation: 0
    };
    project.edges.deadPipe = {
      ...project.edges.pipe,
      id: "deadPipe",
      label: "Dead-end pipe",
      from: "orphan",
      to: "deadEnd",
      fromPort: "outlet",
      toPort: "inlet"
    };

    const result = solveHydraulicNetwork(project);

    expect(result.stable).toBe(true);
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "network-disconnected", severity: "warning" }));
    expect(result.warnings).toContainEqual(expect.objectContaining({ id: "ambiguous-deadEnd-orphan", severity: "warning" }));
  });
});
