export type Vec2 = { x: number; y: number };
export type NodeId = string;
export type EdgeId = string;
export type PipePortId = "inlet" | "outlet" | "north" | "south";

export type SolverTier = "instant-1d" | "openfoam" | "su2" | "code-saturne" | "mujoco";
export type ResultColorMap = "turbo" | "viridis" | "thermal" | "grayscale";
export type ResultViewMode = "2d" | "3d";
export type CanvasRenderMode = "cinema" | "schematic";
export type ResultCamera = {
  yaw: number;
  pitch: number;
  zoom: number;
};
export type OverlayMode =
  | "velocity"
  | "pressure"
  | "reynolds"
  | "temperature"
  | "phase"
  | "residuals"
  | "geometry";
export type WorkspaceMode = "design" | "simulate" | "sweep" | "analyze";

export type FluidParams = {
  density: number;
  dynamicViscosity: number;
  vaporPressure: number;
  bulkModulus: number;
  temperature: number;
};

export type BoundaryKind = "pressure" | "flow";

export type FluidNode = {
  id: NodeId;
  type: "source" | "sink" | "pump" | "mixer" | "junction";
  label: string;
  position: Vec2;
  rotation?: number;
  elevation: number;
  pressure?: number;
  flowDemand?: number;
  head?: number;
  pumpCurveA?: number;
  concentration?: number;
  boundary?: BoundaryKind;
};

export type ChannelShape =
  | { kind: "circular"; diameter: number }
  | { kind: "rectangular"; width: number; height: number };

export type FluidEdge = {
  id: EdgeId;
  type: "pipe" | "venturi" | "bend" | "valve" | "nozzle" | "contraction" | "expansion";
  label: string;
  from: NodeId;
  to: NodeId;
  fromPort?: PipePortId;
  toPort?: PipePortId;
  length: number;
  shape: ChannelShape;
  roughness: number;
  minorLossK: number;
  throatDiameter?: number;
  outletDiameter?: number;
  throatPosition?: number;
  throatLength?: number;
  dischargeCoefficient?: number;
  valveOpening?: number;
};

export type SweepConfig = {
  id: string;
  targetId: string;
  targetKind: "edge" | "node" | "fluid";
  parameter: string;
  min: number;
  max: number;
  steps: number;
};

export type VisualizationSettings = {
  mode: WorkspaceMode;
  overlay: OverlayMode;
  particles: boolean;
  streamlines: boolean;
  grid: boolean;
};

export type AdvancedPhysicsMode =
  | "incompressible-navier-stokes"
  | "compressible-flow"
  | "heat-transfer"
  | "conjugate-heat-transfer"
  | "water-hammer"
  | "multiphase-vof"
  | "cavitation"
  | "rigid-body-fluid-forces";

export type ReviewedGeometryBoundaryRole = "inlet" | "outlet" | "wall" | "interface";

export type ReviewedGeometryBoundaryTag = {
  role: ReviewedGeometryBoundaryRole;
  patchName: string;
};

export type ReviewedSurfaceBoundaryConditionType =
  | "velocity-inlet"
  | "mass-flow-inlet"
  | "pressure-inlet"
  | "pressure-outlet"
  | "outflow"
  | "no-slip-wall"
  | "slip-wall"
  | "rough-wall"
  | "heat-flux-wall"
  | "temperature-wall"
  | "coupled-interface"
  | "mapped-interface";

export type ReviewedSurfaceBoundaryConditionStatus = "unset" | "ready" | "placeholder";

export type ReviewedSurfaceBoundaryCondition = {
  type: ReviewedSurfaceBoundaryConditionType;
  status?: ReviewedSurfaceBoundaryConditionStatus;
  velocity?: { x: number; y: number; z: number };
  massFlowRate?: number;
  pressure?: number;
  temperature?: number;
  heatFlux?: number;
  roughness?: number;
  notes?: string;
};

export type ReviewedGeometryMetadata = {
  triangleCount: number;
  bounds: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  } | null;
  openEdgeCount: number;
  nonManifoldEdgeCount: number;
  watertightStatus: "closed" | "open" | "non-manifold" | "unknown";
  asciiValid: boolean;
  validation: string[];
};

export type ReviewedGeometrySurface = {
  id: string;
  surfaceName: string;
  role: ReviewedGeometryBoundaryRole;
  patchName: string;
  sourceType: "uploaded-stl" | "local-stl-path";
  cadReviewed: boolean;
  reviewedAt?: string | null;
  notes?: string;
  boundaryCondition?: ReviewedSurfaceBoundaryCondition;
  stlText?: string;
  stlPath?: string;
  metadata?: ReviewedGeometryMetadata;
};

export type ReviewedGeometrySource = {
  sourceType: "flowlab-generated" | "uploaded-stl" | "local-stl-path";
  cadReviewed: boolean;
  reviewedAt?: string | null;
  reviewNotes?: string;
  stlText?: string;
  stlPath?: string;
  metadata?: ReviewedGeometryMetadata;
  boundaryTags?: ReviewedGeometryBoundaryTag[];
  surfaces?: ReviewedGeometrySurface[];
};

export type SolverSettings = {
  tier: SolverTier;
  advancedMode: AdvancedPhysicsMode;
  turbulence: "laminar" | "rans-k-epsilon" | "rans-sst" | "les" | "dns";
  meshResolution: "coarse" | "medium" | "fine";
  // OpenFOAM incompressible run mode. "transient" (default) is a short starter
  // solve; "steady" runs steady-state SIMPLE to convergence so the case yields a
  // fully-developed pressure drop. Honored only for incompressible-navier-stokes.
  runMode?: "transient" | "steady";
  // OpenFOAM mesh topology. "planar-2d" (default) is a one-cell-thick channel
  // strip; "axisymmetric" compiles a circular profile into a 3D wedge; and
  // "full-ogrid" is the bounded full-360, five-block straight-pipe volume.
  meshMode?: "planar-2d" | "axisymmetric" | "full-ogrid";
  axisymmetricBenchmark?: {
    fixtureId: "straight-pipe";
    boundaryCondition: "periodic-pressure-gradient";
    lengthM: number;
    volumetricFlowRateM3PerS: number;
  };
  fullOGridVerification?: {
    contractId: "straight-circular-pipe-hagen-poiseuille-v1";
    boundaryCondition: "fully-developed-parabolic-inlet-pressure-outlet";
    lengthM: number;
    volumetricFlowRateM3PerS: number;
  };
  reviewedGeometry?: ReviewedGeometrySource;
  meshControls?: {
    longitudinalRefinement?: number;
    boundaryLayerLayers?: number;
    boundaryLayerGrowthRate?: number;
    axisymmetricAxialCells?: number;
    axisymmetricRadialCells?: number;
    fullOGridAxialCells?: number;
    fullOGridAnnularRadialCells?: number;
    fullOGridCircumferentialCells?: number;
    fullOGridCoreCellsPerSide?: number;
    // Transverse (across-gap) cell distribution. "boundary-layer" (default)
    // clusters cells at the walls for near-wall/turbulent resolution; "uniform"
    // spaces them evenly, which resolves a laminar parabolic core far better and
    // roughly triples pressure-drop accuracy for internal laminar flow.
    transverseDistribution?: "boundary-layer" | "uniform";
    targetYPlus?: number;
    refinementRegions?: Array<{ edgeId: EdgeId; factor: number; reason?: string }>;
    featureRefinement?: {
      enabled?: boolean;
      factor?: number;
      clusterStrength?: number;
    };
    quality?: {
      minCellArea?: number;
      maxAspectRatio?: number;
      minInteriorAngleDeg?: number;
    };
  };
  adaptiveMesh?: {
    enabled: boolean;
    targetField: "velocity" | "pressure" | "temperature" | "phase" | "wall-shear" | "residual";
    errorMode: "gradient" | "relative-error" | "absolute-error";
    adaptEvery: number;
    maxCells: number;
    minCellSize: number;
    maxCellSize: number;
    gradation: number;
    writeAdaptedState: boolean;
  };
  performance?: {
    openfoamParallel?: {
      enabled: boolean;
      ranks: number;
      decomposition: "scotch";
    };
  };
  maxIterations: number;
  tolerance: number;
};

export type FluidProject = {
  version: 1;
  name: string;
  fluid: FluidParams;
  nodes: Record<NodeId, FluidNode>;
  edges: Record<EdgeId, FluidEdge>;
  sweeps: SweepConfig[];
  solver: SolverSettings;
  visualization: VisualizationSettings;
  viewport: { x: number; y: number; zoom: number };
};

export type SimulationWarning = {
  id: string;
  severity: "info" | "warning" | "error";
  message: string;
  targetId?: string;
};

export type EdgeResult = {
  flowRate: number;
  velocity: number;
  reynolds: number;
  frictionFactor: number;
  effectiveLength: number;
  bendAngle: number;
  geometryMinorLossK: number;
  majorHeadLoss: number;
  minorHeadLoss: number;
  pressureDrop: number;
  regime: "laminar" | "transitional" | "turbulent";
  cavitationRisk: boolean;
};

export type NodeResult = {
  pressure: number;
  head: number;
  massResidual: number;
  concentration?: number;
};

export type ControlVolumeResult = {
  targetId: string;
  massResidual: number;
  momentumFlux: number;
  pressureForce: number;
  reactionForce: Vec2;
};

export type WaterHammerResult = {
  waveSpeed: number;
  pressureRise: number;
  criticalClosureTime: number;
};

export type SimulationResult = {
  stable: boolean;
  converged: boolean;
  timestep: number;
  edgeResults: Record<EdgeId, EdgeResult>;
  nodeResults: Record<NodeId, NodeResult>;
  controlVolumes: Record<string, ControlVolumeResult>;
  waterHammer: Record<EdgeId, WaterHammerResult>;
  warnings: SimulationWarning[];
};

export type SolverCapability = {
  id: SolverTier;
  label: string;
  installed: boolean;
  execution: "browser" | "docker" | "native";
  notes: string[];
};

export type SolverRuntimeStatus = {
  solver: SolverTier;
  runnable: boolean;
  preferredExecution: "browser" | "docker" | "native" | "none";
  dockerImage?: string | null;
  dockerAvailable?: boolean | null;
  nativeCommand?: string | null;
  nativeAvailable?: boolean | null;
  pythonModule?: string | null;
  pythonModuleAvailable?: boolean | null;
  blockers: string[];
  notes: string[];
};

export type SolverCase = {
  id: string;
  projectName: string;
  solver: SolverTier;
  advancedMode: AdvancedPhysicsMode;
  status: "generated" | "queued" | "running" | "complete" | "failed" | "blocked" | "cancelled";
  files: Record<string, string>;
  runCommand: string[];
  provenance: string[];
  evidenceCapability: EvidenceCapability;
};

export type EvidenceCapability = {
  status: "validated-benchmark" | "experimental";
  promotionBlocked: boolean;
  blockingReasons: string[];
  validationPath: string[];
  allowedClaims: string[];
  prohibitedClaims: string[];
  evidenceId?: string | null;
  immutableEvidence: Array<{ path: string; sha256: string }>;
};

export type JobRecord = {
  id: string;
  caseId: string;
  solver: SolverTier;
  status: SolverCase["status"];
  createdAt: string;
  updatedAt: string;
  finishedAt?: string | null;
  caseDir?: string | null;
  execution: "docker" | "native" | "browser" | "none";
  command: string[];
  logs: string[];
  error?: string | null;
  exitCode?: number | null;
  result?: Partial<SimulationResult> | JobResultPayload | Record<string, unknown> | null;
  evidenceCapability: EvidenceCapability;
};

export type RecentJob = {
  job: JobRecord;
  case: SolverCase | null;
};

export type RecentJobsResponse = {
  jobs: RecentJob[];
};

export type SolverLogResidual = {
  initial?: number;
  final?: number;
  iterations?: number;
};

export type SolverLogSummary = {
  solver: SolverTier;
  lineCount: number;
  lastLines: string[];
  timeSteps?: number[];
  latestTime?: number;
  iterations?: number[];
  latestIteration?: number;
  residuals?: Record<string, SolverLogResidual>;
  warnings?: string[];
  errors?: string[];
};

export type MeshQualityCommandRun = {
  command: string;
  resolvedCommand?: string;
  execution?: string;
  required?: boolean;
  exitCode?: number | null;
  status: "complete" | "failed" | "missing-command" | "confirmed" | "cancelled" | string;
  logPath?: string | null;
  lineCount?: number;
};

export type MeshQualitySummary = {
  schema: "flowlab.mesh_quality_summary.v1";
  status: string;
  productionReady: boolean;
  approvalStatus: string;
  nativeQualityStatus?: string | null;
  solverAcceptanceStatus?: string | null;
  acceptanceError?: string | null;
  openfoam: {
    status: string;
    commandRuns: MeshQualityCommandRun[];
    qualityMetrics: {
      failedChecks?: number | null;
      maxNonOrthogonality?: number | null;
      averageNonOrthogonality?: number | null;
      maxSkewness?: number | null;
      maxAspectRatio?: number | null;
      minVolume?: number | null;
      passed?: boolean;
      counts?: Record<string, number>;
    };
    yPlusEvidence: {
      status?: string;
      files?: string[];
      min?: number;
      mean?: number;
      max?: number;
      sampleCount?: number;
      blockingReason?: string;
    };
    layerSummary: {
      status?: string;
      excerptCount?: number;
      excerpts?: string[];
    };
    blockingReasons: string[];
  };
  artifacts: Array<{
    path: string;
    exists: boolean;
    size: number;
    text?: string;
    skipped?: string;
  }>;
  artifactLimitBytes: number;
};

export type JobResultPayload = {
  caseDir?: string | null;
  exitCode?: number;
  logsCaptured?: number;
  logSummary?: SolverLogSummary;
  resultFiles?: JobArtifactFile[];
  diagnosticFiles?: JobArtifactFile[];
  diagnosticSummary?: DiagnosticSummary[];
  patchMetrics?: PatchMetrics;
  diagnosticsAcceptance?: OpenFoamDiagnosticsAcceptance;
  meshQuality?: MeshQualitySummary;
  resultCollection?: Record<string, unknown> | null;
  visualPostprocessing?: Record<string, unknown> | null;
  artifactManifest?: {
    path: string;
    schema: string;
    status?: string;
    resultCount?: number;
    diagnosticCount?: number;
  };
  progressive?: boolean;
  validatedBenchmark?: ValidatedOpenBoundaryRun;
};

export type ValidatedOpenBoundaryRun = {
  schema: "flowlab.validated_open_boundary_run.v1" | string;
  benchmarkId: string;
  status: "accepted" | "rejected" | string;
  allChecksPassed: boolean;
  cellsPerAxis: number;
  scope: string;
  checks: Record<string, boolean>;
  errors: Record<string, number>;
  openFoamForces: Record<string, { pressure: number[]; viscous: number[] }>;
  directFaceIntegration: Record<string, unknown>;
  analytic: Record<string, number[]>;
  flux: { inlet: number; outlet: number };
  artifacts: Record<string, string>;
};

export type ReferenceCase = {
  id: string;
  label: string;
  solver: SolverTier;
  source: {
    kind: string;
    repo: string;
    url: string;
    casePath: string;
    expectedFiles: string[];
  };
  physics: AdvancedPhysicsMode[] | string[];
  importMode: string;
  notes: string[];
};

export type ReferenceCaseRegistry = {
  schema: "flowlab.reference_cases.registry.v1" | string;
  updatedAt?: string;
  cases: ReferenceCase[];
};

export type ValidatedBenchmark = {
  id: string;
  label: string;
  scientificStatus: "analysis-only-narrow-envelope" | string;
  capabilityStatus: "validated-benchmark" | string;
  promotionBlocked: boolean;
  blockingReasons?: string[];
  applicability: string[];
  limits: string[];
  metrics: Record<string, unknown>;
  evidence: Array<{ path: string; sha256: string }>;
};

export type ValidatedBenchmarkRegistry = {
  schema: "flowlab.validated_benchmark_registry.v1" | string;
  benchmarks: ValidatedBenchmark[];
};

export type ValidatedPresetLaunch = {
  case: SolverCase;
  job: JobRecord;
};

export type ReferenceCaseImportPlan = {
  schema: "flowlab.reference_case_import_plan.v1" | string;
  caseId: string;
  label: string;
  solver: SolverTier;
  physics: string[];
  source: ReferenceCase["source"];
  importMode: string;
  requiredUserActions: string[];
  generatedArtifacts: Record<string, unknown>;
  limitations: string[];
  notes: string[];
};

export type OpenFoamDiagnosticsAcceptance = {
  schema: "flowlab.openfoam_diagnostics_acceptance.v1" | string;
  status: "complete" | "partial" | "missing" | string;
  advancedMode?: string | null;
  generatedFunctionObjects?: string[];
  observedOutputs?: Record<string, string[]>;
  missingDiagnostics?: Array<{ kind: string; functionObject: string; reason: string }>;
  parserStatus?: string;
  completionGate?: {
    strict?: boolean;
    status?: "pass" | "partial" | "fail" | string;
    blockingReasons?: string[];
  };
  patchMetrics?: PatchMetrics;
  commandExitCodes?: {
    solver?: number | null;
    nativeMesh?: Array<{ command?: string; exitCode?: number | null; status?: string; logPath?: string | null }>;
  };
};

export type PatchMetrics = {
  schema: "flowlab.patch_metrics.v1";
  status: "complete" | "partial" | "missing" | "unparsed" | string;
  patches: Record<
    string,
    {
      patchName: string;
      role: "inlet" | "outlet" | "wall" | "interface" | "unknown" | string;
      flowRate?: { value: number; unit: string; time?: number; path: string };
      averagePressure?: { value: number; unit: string; time?: number; path: string; field: string };
      wallShear?: { min?: number; mean?: number; max?: number; unit: string; time?: number; path: string };
      force?: PatchForceMetric;
      sources: string[];
    }
  >;
  flowBalance?: {
    inletFlow: number;
    outletFlow: number;
    imbalance: number;
    relativeImbalance: number;
    unit: string;
    inletPatches: string[];
    outletPatches: string[];
  } | null;
  pressureDrops: Array<{
    fromPatch: string;
    toPatch: string;
    inletPressure: number;
    outletPressure: number;
    deltaP: number;
    unit: string;
  }>;
  forces: PatchForceMetric[];
  pressureProbes: Array<{
    path: string;
    time?: number;
    sampleCount: number;
    minPressure: number;
    maxPressure: number;
    pressureSpan: number;
    unit: string;
  }>;
  warnings: string[];
  sources: Array<{ path: string; kind: string; status: string }>;
};

export type PatchForceMetric = {
  patchName: string;
  time?: number;
  force: { x: number; y: number; z: number };
  moment: { x: number; y: number; z: number };
  forceMagnitude: number;
  momentMagnitude: number;
  path: string;
  components?: Record<string, number>;
};

export type ResultFieldSummaryItem = {
  name: string;
  location: "point" | "cell";
  kind: "scalar" | "vector-magnitude";
  tupleCount: number;
  min: number;
  max: number;
  mean: number;
  stdDev?: number;
  p50?: number;
  p95?: number;
};

export type ResultFieldSummary = {
  schema: "flowlab.result_field_summary.v1";
  format: VtkResultDataset["format"];
  pointCount: number;
  cellCount: number;
  fields: ResultFieldSummaryItem[];
};

export type JobArtifactFile = {
  path: string;
  size: number;
  text?: string;
  skipped?: string;
  fieldSummary?: ResultFieldSummary;
  fieldSummaryError?: string;
  collectionSummary?: ResultCollectionSummary;
  time?: number;
  timeText?: string;
  timeSource?: "pvd" | "openfoam-time-directory";
  sourceFields?: string[];
};

export type JobArtifactIndex = {
  artifacts: Array<{
    path: string;
    size: number;
    kind: "result" | "diagnostic";
    fieldSummary?: ResultFieldSummary;
    fieldSummaryError?: string;
    collectionSummary?: ResultCollectionSummary;
    time?: number;
    timeText?: string;
    timeSource?: "pvd" | "openfoam-time-directory";
    collectionPath?: string;
    collectionIndex?: number;
  }>;
  count: number;
  truncated: boolean;
};

export type ResultCollectionSummary = {
  schema: "flowlab.pvd_collection.v1";
  format: "pvd-ascii-v1";
  path: string;
  datasetCount: number;
  referencedResultCount: number;
  missingResultCount: number;
  unsafeReferenceCount: number;
  truncated: boolean;
  skipped?: string;
  error?: string;
  datasets: Array<{
    index: number;
    time: number | null;
    timeText: string;
    file: string | null;
    exists: boolean;
    pathError?: string;
    part?: string;
    group?: string;
    name?: string;
  }>;
};

export type JobArtifactChunk = {
  path: string;
  size: number;
  offset: number;
  limit: number;
  text: string;
  nextOffset: number;
  complete: boolean;
};

export type JobArtifactPreviewFieldSample =
  | { name: string; kind: "scalar"; values: number[] }
  | { name: string; kind: "vector"; values: [number, number, number][]; magnitudes: number[] };

export type JobArtifactPreview = {
  path: string;
  size: number;
  time?: number;
  timeText?: string;
  timeSource?: "pvd" | "openfoam-time-directory";
  schema?: "flowlab.result_preview.v1";
  format?: VtkResultDataset["format"];
  sourcePointCount?: number;
  sourceCellCount?: number;
  pointCount?: number;
  cellCount?: number;
  pointLimit?: number;
  cellLimit?: number;
  truncated?: boolean;
  pointIndices?: number[];
  cellIndices?: number[];
  points?: [number, number, number][];
  cells?: number[][];
  cellTypes?: number[];
  fieldSummary?: ResultFieldSummary;
  fieldSamples?: {
    point: JobArtifactPreviewFieldSample[];
    cell: JobArtifactPreviewFieldSample[];
  };
  skipped?: string;
};

export type DiagnosticSummary =
  | {
      path: string;
      kind: string;
      columns: string[];
      rowCount: number;
      latest: Record<string, number>;
    }
  | {
      path: string;
      kind: string;
      lineCount: number;
      excerpts: string[];
    };

export type VtkResultDataset = {
  format: "legacy-vtk-ascii-v1" | "legacy-vtk-polydata-ascii-v1" | "vtu-ascii-v1";
  points: [number, number, number][];
  cells: number[][];
  cellTypes: number[];
  pointData: {
    scalars: Record<string, number[]>;
    vectors: Record<string, [number, number, number][]>;
  };
  cellData: {
    scalars: Record<string, number[]>;
    vectors: Record<string, [number, number, number][]>;
  };
  fields: string[];
  sourceName?: string;
  sourceText?: string;
};
