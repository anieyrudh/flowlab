import { describe, expect, it } from "vitest";
import { mixerPreset, venturiPreset } from "./data/presets";
import { parseProject } from "./projectSchema";

describe("project network validation", () => {
  it("accepts legacy projects with implicit multi-input junction ports", () => {
    const parsed = parseProject(structuredClone(mixerPreset));

    expect(parsed.ok).toBe(true);
  });

  it("rejects self-looping edges", () => {
    const project = structuredClone(venturiPreset);
    project.edges.inlet.to = "source";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("cannot connect a node to itself") });
  });

  it("rejects explicit invalid endpoint port directions", () => {
    const project = structuredClone(venturiPreset);
    project.edges.inlet.fromPort = "inlet";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("cannot use inlet as a source-side port") });
  });

  it("rejects explicitly duplicated port occupancy", () => {
    const project = structuredClone(venturiPreset);
    project.edges.inlet.fromPort = "north";
    project.edges.outlet.to = "source";
    project.edges.outlet.toPort = "north";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("reuses north") });
  });

  it("rejects duplicated default port occupancy after normalization", () => {
    const project = structuredClone(venturiPreset);
    project.edges.branch = {
      ...project.edges.inlet,
      id: "branch",
      label: "Branch pipe",
      to: "sink"
    };

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("reuses outlet") });
  });

  it("rejects disconnected imported networks by default", () => {
    const project = structuredClone(venturiPreset);
    project.nodes.orphan = {
      id: "orphan",
      type: "junction",
      label: "Orphan junction",
      position: { x: 40, y: 40 },
      elevation: 0
    };

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("disconnected components") });
  });

  it("can allow disconnected topology warnings for saved editor state", () => {
    const project = structuredClone(venturiPreset);
    project.nodes.orphan = {
      id: "orphan",
      type: "junction",
      label: "Orphan junction",
      position: { x: 40, y: 40 },
      elevation: 0
    };

    const parsed = parseProject(project, { allowTopologyWarnings: true });

    expect(parsed.ok).toBe(true);
  });

  it("accepts adaptive mesh planning settings", () => {
    const project = structuredClone(venturiPreset);
    project.solver.adaptiveMesh = {
      enabled: true,
      targetField: "pressure",
      errorMode: "relative-error",
      adaptEvery: 3,
      maxCells: 500000,
      minCellSize: 0.0005,
      maxCellSize: 0.05,
      gradation: 1.25,
      writeAdaptedState: false
    };

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("accepts bounded asymmetric Venturi dimensions", () => {
    const project = structuredClone(venturiPreset);
    const edge = project.edges.inlet;
    edge.outletDiameter = 0.09;
    edge.throatDiameter = 0.05;
    edge.throatPosition = 0.4;
    edge.throatLength = 0.2;

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("rejects a Venturi throat that is not smaller than both end diameters", () => {
    const project = structuredClone(venturiPreset);
    const edge = project.edges.inlet;
    edge.outletDiameter = 0.07;
    edge.throatDiameter = 0.08;

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("throatDiameter must be smaller") });
  });

  it("rejects a Venturi throat section that extends outside the edge", () => {
    const project = structuredClone(venturiPreset);
    const edge = project.edges.inlet;
    edge.throatPosition = 0.1;
    edge.throatLength = edge.length;

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("throatLength must fit") });
  });

  it("accepts the narrow OpenFOAM parallel-candidate configuration", () => {
    const project = structuredClone(venturiPreset);
    project.solver.performance = {
      openfoamParallel: {
        enabled: true,
        ranks: 4,
        decomposition: "scotch"
      }
    };

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("accepts a prospectively frozen axisymmetric straight-pipe benchmark contract", () => {
    const project = structuredClone(venturiPreset);
    project.solver.meshMode = "axisymmetric";
    project.solver.runMode = "steady";
    project.solver.turbulence = "laminar";
    project.solver.meshControls = {
      axisymmetricAxialCells: 16,
      axisymmetricRadialCells: 4
    };
    project.solver.axisymmetricBenchmark = {
      fixtureId: "straight-pipe",
      boundaryCondition: "periodic-pressure-gradient",
      lengthM: 0.024,
      volumetricFlowRateM3PerS: 1e-5
    };

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("rejects an incomplete exact axisymmetric refinement pair", () => {
    const project = structuredClone(venturiPreset);
    project.solver.meshControls = {
      axisymmetricAxialCells: 16
    };

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({
      ok: false,
      message: expect.stringContaining("axial and radial cell counts must be supplied together")
    });
  });

  it("rejects ambiguous imported networks without source and sink boundaries", () => {
    const project = structuredClone(venturiPreset);
    project.nodes.source.type = "junction";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("needs at least one source and one sink boundary") });
  });
});
