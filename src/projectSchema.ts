import { z } from "zod";
import type { FluidEdge, FluidNode, FluidProject, NodeId, PipePortId } from "./types";

const portIds = ["inlet", "outlet", "north", "south"] as const;

const vec2Schema = z.object({
  x: z.number().finite(),
  y: z.number().finite()
});

const fluidNodeSchema = z.object({
  id: z.string().min(1),
  type: z.enum(["source", "sink", "pump", "mixer", "junction"]),
  label: z.string().min(1),
  position: vec2Schema,
  rotation: z.number().finite().optional(),
  elevation: z.number().finite(),
  pressure: z.number().finite().optional(),
  flowDemand: z.number().finite().optional(),
  head: z.number().finite().optional(),
  pumpCurveA: z.number().finite().optional(),
  concentration: z.number().finite().optional(),
  boundary: z.enum(["pressure", "flow"]).optional()
});

const channelShapeSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("circular"), diameter: z.number().positive() }),
  z.object({ kind: z.literal("rectangular"), width: z.number().positive(), height: z.number().positive() })
]);

const boundaryTagRoles = ["inlet", "outlet", "wall", "interface"] as const;

function isAsciiStl(text: string) {
  const normalized = text.toLowerCase();
  return /^[\x00-\x7F]*$/.test(text) && normalized.includes("solid") && normalized.includes("facet normal") && normalized.includes("vertex");
}

function isSafeStlPath(path: string) {
  if (!/\.stl$/i.test(path)) return false;
  if (path.includes("\0") || path.includes("..")) return false;
  if (/^([a-z]+:)?\/\//i.test(path)) return false;
  if (path.startsWith("/") || path.startsWith("\\") || /^[a-z]:[\\/]/i.test(path)) return false;
  return true;
}

const stlBoundsSchema = z.object({
  min: z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() }),
  max: z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() })
});

const boundaryTagSchema = z
  .object({
    role: z.enum(boundaryTagRoles),
    patchName: z.string().max(80)
  })
  .superRefine((tag, context) => {
    if (tag.patchName.trim() && !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(tag.patchName.trim())) {
      context.addIssue({
        code: "custom",
        path: ["patchName"],
        message: "OpenFOAM patch names must start with a letter or underscore and contain only letters, numbers, underscores, or hyphens"
      });
    }
  });

const reviewedGeometryMetadataSchema = z.object({
  triangleCount: z.number().int().nonnegative(),
  bounds: stlBoundsSchema.nullable(),
  openEdgeCount: z.number().int().nonnegative(),
  nonManifoldEdgeCount: z.number().int().nonnegative(),
  watertightStatus: z.enum(["closed", "open", "non-manifold", "unknown"]),
  asciiValid: z.boolean(),
  validation: z.array(z.string())
});

const boundaryConditionTypesByRole = {
  inlet: ["velocity-inlet", "mass-flow-inlet", "pressure-inlet"],
  outlet: ["pressure-outlet", "outflow"],
  wall: ["no-slip-wall", "slip-wall", "rough-wall", "heat-flux-wall", "temperature-wall"],
  interface: ["coupled-interface", "mapped-interface"]
} as const;

const boundaryConditionSchema = z
  .object({
    type: z.enum([
      "velocity-inlet",
      "mass-flow-inlet",
      "pressure-inlet",
      "pressure-outlet",
      "outflow",
      "no-slip-wall",
      "slip-wall",
      "rough-wall",
      "heat-flux-wall",
      "temperature-wall",
      "coupled-interface",
      "mapped-interface"
    ]),
    status: z.enum(["unset", "ready", "placeholder"]).optional(),
    velocity: z.object({ x: z.number().finite(), y: z.number().finite(), z: z.number().finite() }).optional(),
    massFlowRate: z.number().finite().optional(),
    pressure: z.number().finite().optional(),
    temperature: z.number().finite().optional(),
    heatFlux: z.number().finite().optional(),
    roughness: z.number().finite().nonnegative().optional(),
    notes: z.string().max(1000).optional()
  })
  .optional();

const reviewedGeometrySurfaceSchema = z
  .object({
    id: z.string().min(1),
    surfaceName: z.string().min(1).max(120),
    role: z.enum(boundaryTagRoles),
    patchName: z.string().min(1).max(80),
    sourceType: z.enum(["uploaded-stl", "local-stl-path"]),
    cadReviewed: z.boolean(),
    reviewedAt: z.string().datetime().nullable().optional(),
    notes: z.string().max(2000).optional(),
    boundaryCondition: boundaryConditionSchema,
    stlText: z.string().optional(),
    stlPath: z.string().optional(),
    metadata: reviewedGeometryMetadataSchema.optional()
  })
  .superRefine((surface, context) => {
    if (!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(surface.patchName.trim())) {
      context.addIssue({
        code: "custom",
        path: ["patchName"],
        message: "OpenFOAM patch names must start with a letter or underscore and contain only letters, numbers, underscores, or hyphens"
      });
    }

    if (surface.stlPath && !isSafeStlPath(surface.stlPath)) {
      context.addIssue({
        code: "custom",
        path: ["stlPath"],
        message: "STL path must be a safe relative .stl path"
      });
    }

    if (surface.stlText && !isAsciiStl(surface.stlText)) {
      context.addIssue({
        code: "custom",
        path: ["stlText"],
        message: "Uploaded STL must be ASCII and include solid, facet normal, and vertex records"
      });
    }

    if (surface.sourceType === "uploaded-stl" && !surface.stlText) {
      context.addIssue({
        code: "custom",
        path: ["stlText"],
        message: "Uploaded STL surface requires STL text"
      });
    }

    if (surface.sourceType === "local-stl-path" && !surface.stlPath) {
      context.addIssue({
        code: "custom",
        path: ["stlPath"],
        message: "Local STL surface requires a safe relative STL path"
      });
    }

    const boundaryCondition = surface.boundaryCondition;
    if (boundaryCondition) {
      const allowedTypes = boundaryConditionTypesByRole[surface.role] as readonly string[];
      if (!allowedTypes.includes(boundaryCondition.type)) {
        context.addIssue({
          code: "custom",
          path: ["boundaryCondition", "type"],
          message: `Boundary condition ${boundaryCondition.type} is not valid for ${surface.role} surfaces`
        });
      }
    }
  });

const reviewedGeometrySchema = z
  .object({
    sourceType: z.enum(["flowlab-generated", "uploaded-stl", "local-stl-path"]),
    cadReviewed: z.boolean(),
    reviewedAt: z.string().datetime().nullable().optional(),
    reviewNotes: z.string().max(2000).optional(),
    stlText: z.string().optional(),
    stlPath: z.string().optional(),
    metadata: reviewedGeometryMetadataSchema.optional(),
    boundaryTags: z.array(boundaryTagSchema).optional(),
    surfaces: z.array(reviewedGeometrySurfaceSchema).optional()
  })
  .superRefine((geometry, context) => {
    const hasSurfaces = Boolean(geometry.surfaces?.length);

    if (geometry.stlPath && !isSafeStlPath(geometry.stlPath)) {
      context.addIssue({
        code: "custom",
        path: ["stlPath"],
        message: "STL path must be a safe relative .stl path"
      });
    }

    if (geometry.stlText && !isAsciiStl(geometry.stlText)) {
      context.addIssue({
        code: "custom",
        path: ["stlText"],
        message: "Uploaded STL must be ASCII and include solid, facet normal, and vertex records"
      });
    }

    if (geometry.sourceType === "uploaded-stl" && !geometry.stlText && !hasSurfaces) {
      context.addIssue({
        code: "custom",
        path: ["stlText"],
        message: "Uploaded STL geometry requires STL text"
      });
    }

    if (geometry.sourceType === "local-stl-path" && !geometry.stlPath && !hasSurfaces) {
      context.addIssue({
        code: "custom",
        path: ["stlPath"],
        message: "Local STL geometry requires a safe relative STL path"
      });
    }

    if (geometry.sourceType === "flowlab-generated" && geometry.cadReviewed) {
      context.addIssue({
        code: "custom",
        path: ["cadReviewed"],
        message: "FlowLab-generated starter geometry cannot be marked CAD reviewed"
      });
    }

    if (geometry.surfaces?.length) {
      const patchNames = new Map<string, number>();
      geometry.surfaces.forEach((surface, index) => {
        const patchName = surface.patchName.trim();
        const previous = patchNames.get(patchName);
        if (previous !== undefined) {
          context.addIssue({
            code: "custom",
            path: ["surfaces", index, "patchName"],
            message: `Duplicate OpenFOAM patch name also used by surface ${previous + 1}`
          });
        }
        patchNames.set(patchName, index);
      });
    }
  });

const adaptiveMeshSchema = z.object({
  enabled: z.boolean(),
  targetField: z.enum(["velocity", "pressure", "temperature", "phase", "wall-shear", "residual"]),
  errorMode: z.enum(["gradient", "relative-error", "absolute-error"]),
  adaptEvery: z.number().int().min(1).max(100),
  maxCells: z.number().int().min(100).max(50_000_000),
  minCellSize: z.number().positive(),
  maxCellSize: z.number().positive(),
  gradation: z.number().min(1).max(5),
  writeAdaptedState: z.boolean()
});

const fluidEdgeSchema = z.object({
  id: z.string().min(1),
  type: z.enum(["pipe", "venturi", "bend", "valve", "nozzle", "contraction", "expansion"]),
  label: z.string().min(1),
  from: z.string().min(1),
  to: z.string().min(1),
  fromPort: z.enum(portIds).optional(),
  toPort: z.enum(portIds).optional(),
  length: z.number().positive(),
  shape: channelShapeSchema,
  roughness: z.number().nonnegative(),
  minorLossK: z.number().nonnegative(),
  throatDiameter: z.number().positive().optional(),
  outletDiameter: z.number().positive().optional(),
  throatPosition: z.number().min(0).max(1).optional(),
  throatLength: z.number().nonnegative().optional(),
  dischargeCoefficient: z.number().positive().optional(),
  valveOpening: z.number().min(0).max(1).optional()
}).superRefine((edge, context) => {
  if (edge.shape.kind !== "circular") {
    if (edge.outletDiameter !== undefined || edge.throatDiameter !== undefined) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Axisymmetric diameter controls require a circular edge.",
        path: ["shape"]
      });
    }
    return;
  }
  const outletDiameter = edge.outletDiameter ?? edge.shape.diameter;
  if (edge.type === "venturi") {
    if (edge.throatDiameter === undefined) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Venturi edges require throatDiameter.",
        path: ["throatDiameter"]
      });
    } else if (edge.throatDiameter >= Math.min(edge.shape.diameter, outletDiameter)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Venturi throatDiameter must be smaller than the inlet and outlet diameters.",
        path: ["throatDiameter"]
      });
    }
    const throatPosition = edge.throatPosition ?? 0.5;
    const throatLength = edge.throatLength ?? 0;
    const throatCenter = throatPosition * edge.length;
    if (throatPosition <= 0 || throatPosition >= 1) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Venturi throatPosition must be between 0 and 1.",
        path: ["throatPosition"]
      });
    } else if (throatCenter - throatLength / 2 <= 0 || throatCenter + throatLength / 2 >= edge.length) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Venturi throatLength must fit inside the edge length.",
        path: ["throatLength"]
      });
    }
  }
});

const projectSchema = z.object({
  version: z.literal(1),
  name: z.string().min(1),
  fluid: z.object({
    density: z.number().positive(),
    dynamicViscosity: z.number().positive(),
    vaporPressure: z.number().nonnegative(),
    bulkModulus: z.number().positive(),
    temperature: z.number().positive()
  }),
  nodes: z.record(z.string(), fluidNodeSchema),
  edges: z.record(z.string(), fluidEdgeSchema),
  sweeps: z.array(
    z.object({
      id: z.string().min(1),
      targetId: z.string().min(1),
      targetKind: z.enum(["edge", "node", "fluid"]),
      parameter: z.string().min(1),
      min: z.number().finite(),
      max: z.number().finite(),
      steps: z.number().int().min(2)
    })
  ),
  solver: z.object({
    tier: z.enum(["instant-1d", "openfoam", "su2", "code-saturne", "mujoco"]),
    advancedMode: z.enum([
      "incompressible-navier-stokes",
      "compressible-flow",
      "heat-transfer",
      "conjugate-heat-transfer",
      "water-hammer",
      "multiphase-vof",
      "cavitation",
      "rigid-body-fluid-forces"
    ]),
    turbulence: z.enum(["laminar", "rans-k-epsilon", "rans-sst", "les", "dns"]),
    meshResolution: z.enum(["coarse", "medium", "fine"]),
    runMode: z.enum(["transient", "steady"]).optional(),
    meshMode: z.enum(["planar-2d", "axisymmetric", "full-ogrid", "curved-elbow-ogrid"]).optional(),
    axisymmetricBenchmark: z
      .object({
        fixtureId: z.literal("straight-pipe"),
        boundaryCondition: z.literal("periodic-pressure-gradient"),
        lengthM: z.number().positive(),
        volumetricFlowRateM3PerS: z.number().positive()
      })
      .optional(),
    fullOGridVerification: z
      .object({
        contractId: z.literal("straight-circular-pipe-hagen-poiseuille-v1"),
        boundaryCondition: z.literal("fully-developed-parabolic-inlet-pressure-outlet"),
        lengthM: z.number().positive(),
        volumetricFlowRateM3PerS: z.number().positive()
      })
      .optional(),
    curvedElbowVerification: z
      .object({
        contractId: z.literal("canonical-circular-elbow-re100-v2"),
        boundaryCondition: z.literal("fully-developed-parabolic-inlet-pressure-outlet"),
        diameterM: z.number().positive(),
        centrelineRadiusM: z.number().positive(),
        inletLegLengthM: z.number().positive(),
        outletLegLengthM: z.number().positive(),
        bendAngleDegrees: z.literal(90),
        volumetricFlowRateM3PerS: z.number().positive(),
        qoiHistoryWriteIntervalIterations: z.literal(1)
      })
      .optional(),
    reviewedGeometry: reviewedGeometrySchema.optional(),
    meshControls: z
      .object({
        longitudinalRefinement: z.number().int().min(1).max(4).optional(),
        boundaryLayerLayers: z.number().int().min(0).max(12).optional(),
        boundaryLayerGrowthRate: z.number().min(1).max(3).optional(),
        axisymmetricAxialCells: z.number().int().min(4).max(4096).optional(),
        axisymmetricRadialCells: z.number().int().min(2).max(1024).optional(),
        fullOGridAxialCells: z.number().int().min(4).max(4096).optional(),
        fullOGridAnnularRadialCells: z.number().int().min(2).max(1024).optional(),
        fullOGridCircumferentialCells: z.number().int().min(16).max(4096).optional(),
        fullOGridCoreCellsPerSide: z.number().int().min(4).max(1024).optional(),
        curvedElbowInletAxialCells: z.number().int().min(4).max(4096).optional(),
        curvedElbowBendAxialCells: z.number().int().min(4).max(4096).optional(),
        curvedElbowOutletAxialCells: z.number().int().min(4).max(4096).optional(),
        curvedElbowAnnularRadialCells: z.number().int().min(2).max(1024).optional(),
        curvedElbowCircumferentialCells: z.number().int().min(16).max(4096).optional(),
        curvedElbowCoreCellsPerSide: z.number().int().min(4).max(1024).optional(),
        transverseDistribution: z.enum(["boundary-layer", "uniform"]).optional(),
        targetYPlus: z.number().positive().optional(),
        refinementRegions: z
          .array(
            z.object({
              edgeId: z.string().min(1),
              factor: z.number().int().min(1).max(4),
              reason: z.string().optional()
            })
          )
          .optional(),
        featureRefinement: z
          .object({
            enabled: z.boolean().optional(),
            factor: z.number().int().min(1).max(4).optional(),
            clusterStrength: z.number().min(0).max(0.95).optional()
          })
          .optional(),
        quality: z
          .object({
            minCellArea: z.number().positive().optional(),
            maxAspectRatio: z.number().positive().optional(),
            minInteriorAngleDeg: z.number().positive().max(89).optional()
          })
          .optional()
      })
      .optional(),
    adaptiveMesh: adaptiveMeshSchema.optional(),
    performance: z
      .object({
        openfoamParallel: z
          .object({
            enabled: z.boolean(),
            ranks: z.number().int().min(2).max(256),
            decomposition: z.literal("scotch")
          })
          .optional()
      })
      .optional(),
    maxIterations: z.number().int().positive(),
    tolerance: z.number().positive()
  }).superRefine((solver, context) => {
    const axial = solver.meshControls?.axisymmetricAxialCells;
    const radial = solver.meshControls?.axisymmetricRadialCells;
    if ((axial === undefined) !== (radial === undefined)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["meshControls"],
        message: "Exact axisymmetric axial and radial cell counts must be supplied together."
      });
    }
    const fullCounts = [
      solver.meshControls?.fullOGridAxialCells,
      solver.meshControls?.fullOGridAnnularRadialCells,
      solver.meshControls?.fullOGridCircumferentialCells,
      solver.meshControls?.fullOGridCoreCellsPerSide
    ];
    const suppliedFullCounts = fullCounts.filter((value) => value !== undefined).length;
    if (suppliedFullCounts !== 0 && suppliedFullCounts !== fullCounts.length) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["meshControls"],
        message: "Exact full O-grid axial, annular-radial, circumferential, and core cell counts must be supplied together."
      });
    }
    const circumference = solver.meshControls?.fullOGridCircumferentialCells;
    const core = solver.meshControls?.fullOGridCoreCellsPerSide;
    if (circumference !== undefined && (circumference % 4 !== 0 || core !== circumference / 4)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["meshControls"],
        message: "Full O-grid circumference must be divisible by four and core cells per side must equal circumference/4."
      });
    }
    if (solver.meshMode === "full-ogrid") {
      if (
        solver.advancedMode !== "incompressible-navier-stokes" ||
        solver.runMode !== "steady" ||
        solver.turbulence !== "laminar"
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["meshMode"],
          message: "Full O-grid mode is limited to steady incompressible laminar flow."
        });
      }
    }
    if (solver.fullOGridVerification) {
      if (solver.meshMode !== "full-ogrid") {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["fullOGridVerification"],
          message: "The full O-grid verification contract requires full-ogrid mesh mode."
        });
      }
      if (suppliedFullCounts !== fullCounts.length) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["meshControls"],
          message: "The full O-grid verification contract requires exact four-dimensional cell counts."
        });
      }
    }
    const elbowCounts = [
      solver.meshControls?.curvedElbowInletAxialCells,
      solver.meshControls?.curvedElbowBendAxialCells,
      solver.meshControls?.curvedElbowOutletAxialCells,
      solver.meshControls?.curvedElbowAnnularRadialCells,
      solver.meshControls?.curvedElbowCircumferentialCells,
      solver.meshControls?.curvedElbowCoreCellsPerSide
    ];
    const suppliedElbowCounts = elbowCounts.filter((value) => value !== undefined).length;
    if (suppliedElbowCounts !== 0 && suppliedElbowCounts !== elbowCounts.length) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["meshControls"],
        message: "Exact curved-elbow inlet, bend, outlet, annular-radial, circumferential, and core cell counts must be supplied together."
      });
    }
    const elbowCircumference = solver.meshControls?.curvedElbowCircumferentialCells;
    const elbowCore = solver.meshControls?.curvedElbowCoreCellsPerSide;
    if (elbowCircumference !== undefined && (elbowCircumference % 4 !== 0 || elbowCore !== elbowCircumference / 4)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["meshControls"],
        message: "Curved-elbow circumference must be divisible by four and core cells per side must equal circumference/4."
      });
    }
    if (solver.meshMode === "curved-elbow-ogrid") {
      if (
        solver.advancedMode !== "incompressible-navier-stokes" ||
        solver.runMode !== "steady" ||
        solver.turbulence !== "laminar"
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["meshMode"],
          message: "Curved-elbow O-grid mode is limited to steady incompressible laminar flow."
        });
      }
      if (!solver.curvedElbowVerification) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["curvedElbowVerification"],
          message: "Curved-elbow O-grid mode requires the explicit canonical verification request."
        });
      }
      if (suppliedElbowCounts !== elbowCounts.length) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["meshControls"],
          message: "The curved-elbow verification contract requires exact six-dimensional cell counts."
        });
      }
    }
    if (solver.curvedElbowVerification && solver.meshMode !== "curved-elbow-ogrid") {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["curvedElbowVerification"],
        message: "The curved-elbow verification contract requires curved-elbow-ogrid mesh mode."
      });
    }
    if (solver.axisymmetricBenchmark) {
      if (solver.meshMode !== "axisymmetric") {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["axisymmetricBenchmark"],
          message: "The straight-pipe benchmark contract requires axisymmetric mesh mode."
        });
      }
      if (solver.runMode !== "steady" || solver.turbulence !== "laminar") {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["axisymmetricBenchmark"],
          message: "The straight-pipe benchmark contract requires a steady laminar solver."
        });
      }
      if (axial === undefined || radial === undefined) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["meshControls"],
          message: "The straight-pipe benchmark contract requires exact axisymmetric cell counts."
        });
      }
    }
  }),
  visualization: z.object({
    mode: z.enum(["design", "simulate", "sweep", "analyze"]),
    overlay: z.enum(["velocity", "pressure", "reynolds", "temperature", "phase", "residuals", "geometry"]),
    particles: z.boolean(),
    streamlines: z.boolean(),
    grid: z.boolean()
  }),
  viewport: z.object({ x: z.number().finite(), y: z.number().finite(), zoom: z.number().positive() })
});

type ParseProjectOptions = {
  allowTopologyWarnings?: boolean;
};

function defaultPortFor(node: FluidNode, role: "from" | "to"): PipePortId {
  if (node.type === "source") return "outlet";
  if (node.type === "sink") return "inlet";
  return role === "from" ? "outlet" : "inlet";
}

function inferRotation(node: FluidNode, nodes: Record<NodeId, FluidNode>, edges: Record<string, FluidEdge>): number {
  if (typeof node.rotation === "number") return node.rotation;
  const edge = Object.values(edges).find((candidate) => candidate.from === node.id || candidate.to === node.id);
  if (!edge) return 0;
  const otherId = edge.from === node.id ? edge.to : edge.from;
  const other = nodes[otherId];
  if (!other) return 0;
  return Math.round((Math.atan2(other.position.y - node.position.y, other.position.x - node.position.x) * 180) / Math.PI);
}

export function normalizeProject(project: FluidProject): FluidProject {
  const nodes = Object.fromEntries(
    Object.entries(project.nodes).map(([id, node]) => [
      id,
      {
        ...node,
        id,
        rotation: inferRotation(node, project.nodes, project.edges)
      }
    ])
  ) as Record<NodeId, FluidNode>;

  const edges = Object.fromEntries(
    Object.entries(project.edges).map(([id, edge]) => {
      const from = nodes[edge.from];
      const to = nodes[edge.to];
      return [
        id,
        {
          ...edge,
          id,
          fromPort: edge.fromPort ?? (from ? defaultPortFor(from, "from") : "outlet"),
          toPort: edge.toPort ?? (to ? defaultPortFor(to, "to") : "inlet")
        }
      ];
    })
  ) as Record<string, FluidEdge>;

  return {
    ...project,
    nodes,
    edges
  };
}

function canSharePort(node: FluidNode): boolean {
  return node.type === "mixer" || node.type === "junction";
}

function validateNetwork(project: FluidProject): string | null {
  const occupied = new Map<string, string>();

  for (const edge of Object.values(project.edges)) {
    if (edge.from === edge.to) return `${edge.label} cannot connect a node to itself.`;
    if (!project.nodes[edge.from] || !project.nodes[edge.to]) return `${edge.label} references a missing endpoint node.`;

    if (edge.fromPort === "inlet") return `${edge.label} cannot use inlet as a source-side port.`;
    if (edge.toPort === "outlet") return `${edge.label} cannot use outlet as a target-side port.`;

    const endpoints: Array<[string, PipePortId | undefined]> = [
      [edge.from, edge.fromPort],
      [edge.to, edge.toPort]
    ];
    for (const [nodeId, port] of endpoints) {
      const node = project.nodes[nodeId];
      if (!node || !port || canSharePort(node)) continue;
      const key = `${nodeId}:${port}`;
      const previous = occupied.get(key);
      if (previous) return `${edge.label} reuses ${port} on ${node.label}; already occupied by ${previous}.`;
      occupied.set(key, edge.label);
    }
  }

  return null;
}

function validateTopology(project: FluidProject): string | null {
  const parent = new Map<string, string>();

  function find(nodeId: string): string {
    const current = parent.get(nodeId) ?? nodeId;
    if (current === nodeId) return current;
    const root = find(current);
    parent.set(nodeId, root);
    return root;
  }

  function union(a: string, b: string) {
    const rootA = find(a);
    const rootB = find(b);
    if (rootA !== rootB) parent.set(rootB, rootA);
  }

  for (const nodeId of Object.keys(project.nodes)) {
    parent.set(nodeId, nodeId);
  }

  for (const edge of Object.values(project.edges)) {
    if (project.nodes[edge.from] && project.nodes[edge.to] && edge.from !== edge.to) {
      union(edge.from, edge.to);
    }
  }

  const components = new Map<string, FluidNode[]>();
  for (const node of Object.values(project.nodes)) {
    const root = find(node.id);
    components.set(root, [...(components.get(root) ?? []), node]);
  }

  if (components.size > 1) return `Network has ${components.size} disconnected components.`;

  for (const nodes of components.values()) {
    const connectedEdgeCount = Object.values(project.edges).filter((edge) => nodes.some((node) => node.id === edge.from || node.id === edge.to)).length;
    if (connectedEdgeCount === 0) return `${nodes[0].label} is not connected to the hydraulic network.`;

    const hasSource = nodes.some((node) => node.type === "source");
    const hasSink = nodes.some((node) => node.type === "sink");
    if (!hasSource || !hasSink) {
      return `${nodes.map((node) => node.label).join(", ")} component needs at least one source and one sink boundary.`;
    }
  }

  return null;
}

function validateFullOGrid(project: FluidProject): string | null {
  if (project.solver.meshMode !== "full-ogrid") return null;
  const nodes = Object.values(project.nodes);
  const edges = Object.values(project.edges);
  if (nodes.length !== 2 || edges.length !== 1) {
    return "Full O-grid mode requires exactly one source, one sink, and one connecting pipe.";
  }
  const edge = edges[0];
  const source = project.nodes[edge.from];
  const sink = project.nodes[edge.to];
  if (!source || !sink || source.type !== "source" || sink.type !== "sink") {
    return "Full O-grid pipe must connect one source directly to one sink.";
  }
  if (edge.type !== "pipe" || edge.shape.kind !== "circular") {
    return "Full O-grid mode supports only one straight constant-diameter circular pipe.";
  }
  if (edge.outletDiameter !== undefined && Math.abs(edge.outletDiameter - edge.shape.diameter) > Math.max(1e-15, edge.shape.diameter * 1e-12)) {
    return "Full O-grid pipe must have one constant diameter.";
  }
  if (source.position.x === sink.position.x && source.position.y === sink.position.y) {
    return "Full O-grid source and sink require distinct editor positions.";
  }
  const verification = project.solver.fullOGridVerification;
  if (verification && Math.abs(verification.lengthM - edge.length) > Math.max(1e-15, edge.length * 1e-12)) {
    return "Full O-grid verification length must equal the editor pipe physical length.";
  }
  return null;
}

function validateCurvedElbowOGrid(project: FluidProject): string | null {
  if (project.solver.meshMode !== "curved-elbow-ogrid") return null;
  const nodes = Object.values(project.nodes);
  const edges = Object.values(project.edges);
  if (nodes.length !== 2 || edges.length !== 1) {
    return "Curved-elbow O-grid mode requires exactly one source, one sink, and one bounded bend edge.";
  }
  const edge = edges[0];
  const source = project.nodes[edge.from];
  const sink = project.nodes[edge.to];
  if (!source || !sink || source.type !== "source" || sink.type !== "sink") {
    return "Curved-elbow path must connect one source directly to one sink.";
  }
  if (edge.type !== "bend" || edge.shape.kind !== "circular") {
    return "Curved-elbow O-grid mode supports only one circular bend edge.";
  }
  if (
    edge.outletDiameter !== undefined
    && Math.abs(edge.outletDiameter - edge.shape.diameter)
      > Math.max(1e-15, edge.shape.diameter * 1e-12)
  ) {
    return "Curved-elbow O-grid mode requires one constant diameter.";
  }
  const verification = project.solver.curvedElbowVerification;
  if (!verification) return "Curved-elbow O-grid mode requires its explicit verification request.";
  const matches = (left: number, right: number) =>
    Math.abs(left - right) <= Math.max(1e-15, Math.abs(right) * 1e-12);
  if (!matches(verification.diameterM, edge.shape.diameter)) {
    return "Curved-elbow verification diameter must match the bend edge diameter.";
  }
  if (!matches(verification.centrelineRadiusM / verification.diameterM, 3)) {
    return "Curved-elbow O-grid mode requires centreline radius Rc/D=3.";
  }
  if (!matches(verification.inletLegLengthM / verification.diameterM, 10)) {
    return "Curved-elbow O-grid mode requires an exact 10D inlet leg.";
  }
  if (!matches(verification.outletLegLengthM / verification.diameterM, 10)) {
    return "Curved-elbow O-grid mode requires an exact 10D outlet leg.";
  }
  const totalLength =
    verification.inletLegLengthM
    + verification.centrelineRadiusM * Math.PI / 2
    + verification.outletLegLengthM;
  if (!matches(edge.length, totalLength)) {
    return "Curved-elbow edge length must equal its inlet leg, 90-degree centreline arc, and outlet leg.";
  }
  if (source.position.x === sink.position.x && source.position.y === sink.position.y) {
    return "Curved-elbow source and sink require distinct editor positions.";
  }
  const area = Math.PI * edge.shape.diameter ** 2 / 4;
  const meanVelocity = verification.volumetricFlowRateM3PerS / area;
  const reynolds =
    project.fluid.density
    * meanVelocity
    * edge.shape.diameter
    / project.fluid.dynamicViscosity;
  if (Math.abs(reynolds - 100) > 1) {
    return "Curved-elbow verification request requires Reynolds number approximately 100.";
  }
  return null;
}

export function parseProject(input: unknown, options: ParseProjectOptions = {}): { ok: true; project: FluidProject } | { ok: false; message: string } {
  const parsed = projectSchema.safeParse(input);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const path = issue?.path.length ? issue.path.join(".") : "project";
    return { ok: false, message: `${path}: ${issue?.message ?? "Invalid project"}` };
  }

  const project = normalizeProject(parsed.data as FluidProject);
  const fullOGridError = validateFullOGrid(project);
  if (fullOGridError) return { ok: false, message: fullOGridError };
  const curvedElbowError = validateCurvedElbowOGrid(project);
  if (curvedElbowError) return { ok: false, message: curvedElbowError };
  const validationError = validateNetwork(project);
  if (validationError) return { ok: false, message: validationError };
  if (!options.allowTopologyWarnings) {
    const topologyError = validateTopology(project);
    if (topologyError) return { ok: false, message: topologyError };
  }
  return { ok: true, project };
}
