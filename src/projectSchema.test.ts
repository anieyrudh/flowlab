import { describe, expect, it } from "vitest";
import { mixerPreset, pipeLossPreset, venturiPreset } from "./data/presets";
import { parseProject } from "./projectSchema";
import type { FluidProject } from "./types";

function boundedYJunctionProject(): FluidProject {
  const project = structuredClone(pipeLossPreset);
  const source = { ...project.nodes.source, position: { x: 100, y: 250 } };
  const sinkTemplate = project.nodes.sink;
  const pipe = project.edges.pipe;
  project.name = "Bounded symmetric Y-junction";
  project.nodes = {
    source,
    junction: {
      id: "junction",
      type: "junction",
      label: "Generated junction",
      position: { x: 300, y: 250 },
      elevation: 0
    },
    upper: {
      ...sinkTemplate,
      id: "upper",
      label: "Upper outlet",
      position: { x: 500, y: 365.47 },
      flowDemand: 2.3561944901923448e-7
    },
    lower: {
      ...sinkTemplate,
      id: "lower",
      label: "Lower outlet",
      position: { x: 500, y: 134.53 },
      flowDemand: 2.3561944901923448e-7
    }
  };
  const edgeBase = {
    ...pipe,
    length: 0.027,
    shape: { kind: "circular" as const, diameter: 0.006 },
    outletDiameter: undefined
  };
  project.edges = {
    "inlet-pipe": {
      ...edgeBase,
      id: "inlet-pipe",
      label: "Inlet pipe",
      from: "source",
      to: "junction"
    },
    "upper-branch": {
      ...edgeBase,
      id: "upper-branch",
      label: "Upper branch",
      from: "junction",
      to: "upper"
    },
    "lower-branch": {
      ...edgeBase,
      id: "lower-branch",
      label: "Lower branch",
      from: "junction",
      to: "lower"
    }
  };
  project.solver = {
    ...project.solver,
    tier: "openfoam",
    meshMode: "y-junction",
    runMode: "steady",
    turbulence: "laminar",
    meshControls: { yJunctionCellSizeM: 0.001125 }
  };
  return project;
}

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

  it("accepts the bounded full-revolution O-grid pipe mode", () => {
    const project = structuredClone(pipeLossPreset);
    project.solver.meshMode = "full-ogrid";
    project.solver.runMode = "steady";
    project.solver.turbulence = "laminar";
    project.solver.meshControls = {
      fullOGridAxialCells: 16,
      fullOGridAnnularRadialCells: 4,
      fullOGridCircumferentialCells: 32,
      fullOGridCoreCellsPerSide: 8
    };

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("rejects a non-conformal full O-grid refinement tuple", () => {
    const project = structuredClone(pipeLossPreset);
    project.solver.meshMode = "full-ogrid";
    project.solver.runMode = "steady";
    project.solver.turbulence = "laminar";
    project.solver.meshControls = {
      fullOGridAxialCells: 16,
      fullOGridAnnularRadialCells: 4,
      fullOGridCircumferentialCells: 32,
      fullOGridCoreCellsPerSide: 7
    };

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("core cells per side") });
  });

  it("rejects unsupported full O-grid topology", () => {
    const project = structuredClone(venturiPreset);
    project.solver.meshMode = "full-ogrid";
    project.solver.runMode = "steady";
    project.solver.turbulence = "laminar";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("exactly one source") });
  });

  it("accepts a prospective full O-grid verification request only with exact four-dimensional counts", () => {
    const project = structuredClone(pipeLossPreset);
    project.solver.meshMode = "full-ogrid";
    project.solver.runMode = "steady";
    project.solver.turbulence = "laminar";
    project.solver.meshControls = {
      fullOGridAxialCells: 16,
      fullOGridAnnularRadialCells: 4,
      fullOGridCircumferentialCells: 32,
      fullOGridCoreCellsPerSide: 8
    };
    project.solver.fullOGridVerification = {
      contractId: "straight-circular-pipe-hagen-poiseuille-v1",
      boundaryCondition: "fully-developed-parabolic-inlet-pressure-outlet",
      lengthM: 44,
      volumetricFlowRateM3PerS: 1e-5
    };

    const parsed = parseProject(project);

    expect(parsed.ok).toBe(true);
  });

  it("accepts only the bounded symmetric true-3D Y-junction topology", () => {
    expect(parseProject(boundedYJunctionProject()).ok).toBe(true);

    const asymmetric = boundedYJunctionProject();
    asymmetric.edges["lower-branch"].shape = { kind: "circular", diameter: 0.005 };
    expect(parseProject(asymmetric)).toMatchObject({
      ok: false,
      message: expect.stringContaining("identical constant diameter")
    });
  });

  it("fails closed when Y-junction ownership would have ambiguous branch topology", () => {
    const project = boundedYJunctionProject();
    project.nodes.lower.position.y = project.nodes.upper.position.y;

    expect(parseProject(project)).toMatchObject({
      ok: false,
      message: expect.stringContaining("one upper and one lower branch")
    });
  });

  it("accepts only a complete, internally consistent fixed-master Y-junction request", () => {
    const project = boundedYJunctionProject();
    project.solver.meshControls = {
      yJunctionCellSizeM: 0.000375,
      yJunctionMasterCellSizeM: 0.00075,
      yJunctionRefinementFactor: 2
    };
    expect(parseProject(project).ok).toBe(true);

    const incomplete = boundedYJunctionProject();
    incomplete.solver.meshControls = {
      yJunctionCellSizeM: 0.000375,
      yJunctionMasterCellSizeM: 0.00075
    };
    expect(parseProject(incomplete)).toMatchObject({
      ok: false,
      message: expect.stringContaining("requires both master cell size and refinement factor")
    });

    const inconsistent = boundedYJunctionProject();
    inconsistent.solver.meshControls = {
      yJunctionCellSizeM: 0.0001875,
      yJunctionMasterCellSizeM: 0.00075,
      yJunctionRefinementFactor: 2
    };
    expect(parseProject(inconsistent)).toMatchObject({
      ok: false,
      message: expect.stringContaining("must equal master cell size divided by refinement factor")
    });
  });

  it("rejects ambiguous imported networks without source and sink boundaries", () => {
    const project = structuredClone(venturiPreset);
    project.nodes.source.type = "junction";

    const parsed = parseProject(project);

    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("needs at least one source and one sink boundary") });
  });
});
