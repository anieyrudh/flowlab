import { type ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Beaker,
  Box,
  ChevronDown,
  CircleGauge,
  Crosshair,
  Download,
  EyeOff,
  FileDown,
  FlaskConical,
  Gauge,
  GitBranchPlus,
  Layers3,
  Move,
  Pause,
  Play,
  Plus,
  Repeat,
  Redo2,
  RotateCcw,
  SkipBack,
  SkipForward,
  SlidersHorizontal,
  Trash2,
  Undo2,
  Upload,
  Waves
} from "lucide-react";
import { SimulationCanvas } from "./components/SimulationCanvas";
import { presets } from "./data/presets";
import {
  datasetFromPreview,
  fieldAvailable,
  fieldCoverageForSnapshots,
  fieldDescriptiveStats,
  fieldHistogramForValues,
  fieldNameForOverlay,
  fieldStatsForOverlay,
  fieldValuesForOverlay,
  fieldValuesForSelection,
  formatFieldValueKind,
  listResultFields,
  parseVtkResult,
  sampleDatasetAtCanvasPoint,
  sampleDatasetAtWorldPoint,
  timelineStatsForSnapshots,
  type ResultFieldInventoryItem,
  type ResultFieldSelection,
  type ResultFieldTimelineSample,
  type ResultVectorComponent
} from "./results/vtk";
import {
  fetchHealth,
  fetchJob,
  fetchJobArtifactChunk,
  fetchJobArtifactPreview,
  fetchJobArtifacts,
  fetchRecentJobs,
  fetchReferenceCaseImportPlan,
  fetchReferenceCases,
  fetchValidatedBenchmarks,
  fetchRuntimeDiagnostics,
  fetchSolvers,
  generateSolverCase,
  queueJob,
  runValidatedPreset
} from "./services/backend";
import { parseProject } from "./projectSchema";
import { useFlowStore } from "./state/useFlowStore";
import { defaultCinemaCamera as initialCinemaCamera, type CinemaCameraState } from "./components/viewportModel";
import type {
  AdvancedPhysicsMode,
  CanvasRenderMode,
  FluidEdge,
  FluidNode,
  JobArtifactFile,
  JobArtifactIndex,
  JobArtifactPreview,
  JobRecord,
  JobResultPayload,
  MeshQualitySummary,
  NodeId,
  OverlayMode,
  PatchMetrics,
  PipePortId,
  RecentJob,
  ReferenceCase,
  ReferenceCaseImportPlan,
  ValidatedBenchmark,
  ReviewedGeometryBoundaryRole,
  ReviewedGeometryBoundaryTag,
  ReviewedGeometryMetadata,
  ReviewedGeometrySource,
  ReviewedGeometrySurface,
  ReviewedSurfaceBoundaryCondition,
  ReviewedSurfaceBoundaryConditionType,
  ResultCamera,
  ResultColorMap,
  ResultViewMode,
  SolverCapability,
  SolverCase,
  SolverLogSummary,
  SolverRuntimeStatus,
  SolverSettings,
  SolverTier,
  VtkResultDataset,
  WorkspaceMode
} from "./types";
import "./styles/app.css";

type ResultSnapshot = {
  id: string;
  label: string;
  time: number;
  dataset: VtkResultDataset;
  preview?: boolean;
};

type DockPanelId = "field" | "sweep" | "metrics" | "mesh" | "diagnostics" | "warnings";

const defaultDockPanelByMode: Record<WorkspaceMode, DockPanelId> = {
  design: "metrics",
  simulate: "diagnostics",
  sweep: "sweep",
  analyze: "field"
};

const dockPanelOptions: { id: DockPanelId; label: string }[] = [
  { id: "field", label: "Field viewer" },
  { id: "sweep", label: "Sweep" },
  { id: "metrics", label: "Metrics" },
  { id: "mesh", label: "Mesh QA" },
  { id: "diagnostics", label: "Diagnostics" },
  { id: "warnings", label: "Warnings" }
];

type DesktopExportFile = {
  filename: string;
  text: string;
  type: string;
};

declare global {
  interface Window {
    flowlabDesktop?: {
      platform: "darwin" | "win32";
      saveFiles: (files: DesktopExportFile[]) => Promise<{
        status: "saved" | "cancelled" | "error";
        message: string;
      }>;
    };
    webkit?: {
      messageHandlers?: {
        flowlabDesktop?: {
          postMessage: (payload: { type: "save-files"; files: DesktopExportFile[] }) => void;
        };
      };
    };
  }
}

type ProbeTarget = {
  kind: "canvas";
  point: { x: number; y: number };
  size: { width: number; height: number };
} | {
  kind: "surface";
  point: [number, number, number];
  ownerCellIndex: number;
  nearestPointIndex: number;
  trianglePointIndices: [number, number, number];
  barycentricWeights: [number, number, number];
};

const overlayOptions: { id: OverlayMode; label: string }[] = [
  { id: "velocity", label: "Velocity" },
  { id: "pressure", label: "Pressure" },
  { id: "reynolds", label: "Reynolds" },
  { id: "temperature", label: "Thermal" },
  { id: "phase", label: "Phase" },
  { id: "residuals", label: "Residuals" },
  { id: "geometry", label: "Geometry" }
];

const CLIENT_ARTIFACT_LOAD_LIMIT = 4_000_000;
const CLIENT_ARTIFACT_CHUNK_SIZE = 262_144;
const PREVIEW_SEQUENCE_LIMIT = 24;
const ACTIVE_JOB_STORAGE_KEY = "flowlab.active-job.v1";
const resultPlaybackRates = [0.5, 1, 2, 4] as const;
const vectorComponentOptions: { id: ResultVectorComponent; label: string }[] = [
  { id: "magnitude", label: "Magnitude" },
  { id: "x", label: "X" },
  { id: "y", label: "Y" },
  { id: "z", label: "Z" }
];

const resultColorMapOptions: { id: ResultColorMap; label: string; gradient: string }[] = [
  { id: "turbo", label: "Turbo", gradient: "linear-gradient(90deg, #2b4cff, #00c2ff, #67f3a5, #ffe15c, #ff6a3a, #c5164f)" },
  { id: "viridis", label: "Viridis", gradient: "linear-gradient(90deg, #440154, #3b528b, #21918c, #5ec962, #fde725)" },
  { id: "thermal", label: "Thermal", gradient: "linear-gradient(90deg, #18206f, #1954d2, #1eb6ff, #f7e733, #ff8c42, #d62839)" },
  { id: "grayscale", label: "Grayscale", gradient: "linear-gradient(90deg, #17212b, #4b5d70, #8da1b5, #d6e2ec, #ffffff)" }
];

const canvasRenderModeOptions: { id: CanvasRenderMode; label: string }[] = [
  { id: "cinema", label: "Cinema" },
  { id: "schematic", label: "Schematic" }
];

const defaultResultCamera: ResultCamera = { yaw: -32, pitch: 24, zoom: 1 };
const defaultCinemaCamera: CinemaCameraState = {
  ...initialCinemaCamera,
  ...defaultResultCamera
};

export function validatedRunStatusLabel(
  evidenceStatus: string,
  jobStatus: string | undefined,
  runtimeChecksPassed: boolean,
  currentPromotionBlocked: boolean
) {
  if (evidenceStatus === "experimental") {
    return "Experimental CFD output — not validated for production use";
  }
  if (runtimeChecksPassed && jobStatus === "complete") {
    return currentPromotionBlocked
      ? "Coarse runtime audit passed · current bounded-regime promotion is blocked"
      : "Validated bounded preset — every coarse runtime gate passed";
  }
  return "Validated preset definition — runtime label pending every required gate";
}

const modeOptions: { id: WorkspaceMode; label: string }[] = [
  { id: "design", label: "Design" },
  { id: "simulate", label: "Simulate" },
  { id: "sweep", label: "Sweep" },
  { id: "analyze", label: "Analyze" }
];

const advancedModes: { id: AdvancedPhysicsMode; label: string }[] = [
  { id: "incompressible-navier-stokes", label: "Incompressible" },
  { id: "compressible-flow", label: "Compressible" },
  { id: "heat-transfer", label: "Heat transfer" },
  { id: "conjugate-heat-transfer", label: "Conjugate heat" },
  { id: "water-hammer", label: "Water hammer" },
  { id: "multiphase-vof", label: "Multiphase VOF" },
  { id: "cavitation", label: "Cavitation" },
  { id: "rigid-body-fluid-forces", label: "MuJoCo forces" }
];

const solverLabels: Record<SolverTier, string> = {
  "instant-1d": "Instant 1D",
  openfoam: "OpenFOAM",
  su2: "SU2",
  "code-saturne": "Code_Saturne",
  mujoco: "MuJoCo"
};

function formatNumber(value: number, digits = 2) {
  if (!Number.isFinite(value)) return "n/a";
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function formatUnitSuffix(unit: { symbol: string } | null | undefined) {
  return unit?.symbol ? ` ${unit.symbol}` : "";
}

function formatResidual(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value)) return "n/a";
  if (Math.abs(value) < 0.001) return value.toExponential(2);
  return formatNumber(value, 4);
}

function residualRatio(initial?: number, final?: number) {
  if (!Number.isFinite(initial) || !Number.isFinite(final) || initial === undefined || final === undefined || initial === 0) return null;
  return Math.abs(final / initial);
}

function defaultReviewedGeometry(): ReviewedGeometrySource {
  return {
    sourceType: "flowlab-generated",
    cadReviewed: false,
    reviewedAt: null,
    reviewNotes: "",
    surfaces: []
  };
}

function stlSanityError(filename: string, text: string) {
  if (!/\.stl$/i.test(filename)) return "Reviewed geometry import requires a .stl file.";
  const normalized = text.toLowerCase();
  if (!/^[\x00-\x7F]*$/.test(text)) return "Uploaded STL must be ASCII text.";
  if (!normalized.includes("solid") || !normalized.includes("facet normal") || !normalized.includes("vertex")) {
    return "Uploaded STL must be ASCII and include solid, facet normal, and vertex records.";
  }
  return null;
}

const boundaryTagRoles: ReviewedGeometryBoundaryRole[] = ["inlet", "outlet", "wall", "interface"];
const requiredBoundaryTagRoles: ReviewedGeometryBoundaryRole[] = ["inlet", "outlet", "wall"];
const boundaryConditionOptionsByRole: Record<ReviewedGeometryBoundaryRole, Array<{ type: ReviewedSurfaceBoundaryConditionType; label: string }>> = {
  inlet: [
    { type: "velocity-inlet", label: "Velocity" },
    { type: "mass-flow-inlet", label: "Mass flow" },
    { type: "pressure-inlet", label: "Pressure inlet" }
  ],
  outlet: [
    { type: "pressure-outlet", label: "Pressure outlet" },
    { type: "outflow", label: "Outflow" }
  ],
  wall: [
    { type: "no-slip-wall", label: "No slip" },
    { type: "slip-wall", label: "Slip" },
    { type: "rough-wall", label: "Rough wall" },
    { type: "heat-flux-wall", label: "Heat flux" },
    { type: "temperature-wall", label: "Temperature" }
  ],
  interface: [
    { type: "coupled-interface", label: "Coupled" },
    { type: "mapped-interface", label: "Mapped" }
  ]
};

type StlVertex = { x: number; y: number; z: number };
type StlPreviewTriangle = [StlVertex, StlVertex, StlVertex];
type ParsedStlGeometry = ReviewedGeometryMetadata & {
  previewTriangles: StlPreviewTriangle[];
};

function makeSurfaceId() {
  return `surface-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function metadataFromParsedStl(parsed: ParsedStlGeometry): ReviewedGeometryMetadata {
  return {
    triangleCount: parsed.triangleCount,
    bounds: parsed.bounds,
    openEdgeCount: parsed.openEdgeCount,
    nonManifoldEdgeCount: parsed.nonManifoldEdgeCount,
    watertightStatus: parsed.watertightStatus,
    asciiValid: parsed.asciiValid,
    validation: parsed.validation
  };
}

function surfaceNameFromFile(filename: string) {
  return filename.replace(/\.stl$/i, "").replace(/[_-]+/g, " ").trim() || "reviewed surface";
}

function roleFromSurfaceName(name: string): ReviewedGeometryBoundaryRole {
  const normalized = name.toLowerCase();
  if (normalized.includes("inlet")) return "inlet";
  if (normalized.includes("outlet")) return "outlet";
  if (normalized.includes("interface")) return "interface";
  return "wall";
}

function safePatchName(value: string, fallback: string) {
  const sanitized = value
    .trim()
    .replace(/\.stl$/i, "")
    .replace(/[^A-Za-z0-9_-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^([^A-Za-z_])/, "_$1")
    .slice(0, 80);
  return sanitized || fallback;
}

function uniquePatchName(base: string, existing: Iterable<string>) {
  const used = new Set([...existing].map((value) => value.trim()).filter(Boolean));
  const safe = safePatchName(base, "patch");
  if (!used.has(safe)) return safe;
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${safe}_${index}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${safe}_${Date.now().toString(36)}`;
}

function defaultBoundaryConditionForType(type: ReviewedSurfaceBoundaryConditionType): ReviewedSurfaceBoundaryCondition {
  switch (type) {
    case "velocity-inlet":
      return { type, status: "ready", velocity: { x: 1, y: 0, z: 0 } };
    case "mass-flow-inlet":
      return { type, status: "ready", massFlowRate: 1 };
    case "pressure-inlet":
      return { type, status: "ready", pressure: 101325 };
    case "pressure-outlet":
      return { type, status: "ready", pressure: 0 };
    case "outflow":
      return { type, status: "ready" };
    case "no-slip-wall":
    case "slip-wall":
      return { type, status: "ready" };
    case "rough-wall":
      return { type, status: "ready", roughness: 0.0001 };
    case "heat-flux-wall":
      return { type, status: "ready", heatFlux: 0 };
    case "temperature-wall":
      return { type, status: "ready", temperature: 293.15 };
    case "coupled-interface":
    case "mapped-interface":
      return { type, status: "placeholder", notes: "Interface boundary condition placeholder; native coupling is not yet automated." };
  }
}

function defaultBoundaryConditionForRole(role: ReviewedGeometryBoundaryRole): ReviewedSurfaceBoundaryCondition {
  return defaultBoundaryConditionForType(boundaryConditionOptionsByRole[role][0].type);
}

function boundaryConditionTypeAllowedForRole(type: ReviewedSurfaceBoundaryConditionType | undefined, role: ReviewedGeometryBoundaryRole) {
  return Boolean(type && boundaryConditionOptionsByRole[role].some((option) => option.type === type));
}

function boundaryConditionLabel(type: ReviewedSurfaceBoundaryConditionType) {
  for (const options of Object.values(boundaryConditionOptionsByRole)) {
    const option = options.find((candidate) => candidate.type === type);
    if (option) return option.label;
  }
  return type;
}

function boundaryConditionStatus(surface: ReviewedGeometrySurface) {
  const boundaryCondition = surface.boundaryCondition;
  if (!boundaryCondition) return { label: "BC unset", className: "unset" };
  if (!boundaryConditionTypeAllowedForRole(boundaryCondition.type, surface.role)) return { label: "BC mismatch", className: "unset" };
  if (boundaryCondition.status === "placeholder") return { label: "placeholder", className: "placeholder" };
  return { label: "BC ready", className: "ready" };
}

function surfacePatchName(surface: ReviewedGeometrySurface) {
  return surface.patchName.trim();
}

function surfacesForGeometry(geometry: ReviewedGeometrySource): ReviewedGeometrySurface[] {
  if (geometry.surfaces?.length) return geometry.surfaces;
  if (!geometry.stlText && !geometry.stlPath) return [];
  const wallPatch = patchNameForRole(geometry.boundaryTags, "wall") || "wallPatch";
  const sourceType: ReviewedGeometrySurface["sourceType"] = geometry.sourceType === "local-stl-path" ? "local-stl-path" : "uploaded-stl";
  return [
    {
      id: "legacy-reviewed-surface",
      surfaceName: geometry.stlPath ? surfaceNameFromFile(geometry.stlPath) : "reviewedFlowLabSurfaces",
      role: "wall" as ReviewedGeometryBoundaryRole,
      patchName: wallPatch,
      sourceType,
      cadReviewed: geometry.cadReviewed,
      reviewedAt: geometry.reviewedAt,
      notes: geometry.reviewNotes,
      boundaryCondition: undefined,
      stlText: geometry.stlText,
      stlPath: geometry.stlPath,
      metadata: geometry.metadata
    }
  ];
}

function boundaryTagsFromSurfaces(surfaces: ReviewedGeometrySurface[]) {
  return boundaryTagRoles.map((role) => ({
    role,
    patchName: surfaces.find((surface) => surface.role === role && surfacePatchName(surface))?.patchName ?? ""
  }));
}

function requiredReviewedSurfaceCoverageComplete(surfaces: ReviewedGeometrySurface[]) {
  return requiredBoundaryTagRoles.every((role) =>
    surfaces.some((surface) => surface.role === role && surface.cadReviewed && surfacePatchName(surface) && !patchNameIssue(surface.patchName))
  );
}

function duplicatePatchNames(surfaces: ReviewedGeometrySurface[]) {
  const counts = new Map<string, number>();
  for (const surface of surfaces) {
    const patchName = surfacePatchName(surface);
    if (!patchName) continue;
    counts.set(patchName, (counts.get(patchName) ?? 0) + 1);
  }
  return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([patchName]) => patchName));
}

function hasRequiredReviewedGeometry(geometry: ReviewedGeometrySource) {
  const surfaces = geometry.surfaces ?? [];
  if (surfaces.length > 0) return requiredReviewedSurfaceCoverageComplete(surfaces) && duplicatePatchNames(surfaces).size === 0;
  return geometry.cadReviewed && requiredBoundaryTagsComplete(geometry.boundaryTags);
}

function synchronizeGeometrySurfaces(geometry: ReviewedGeometrySource, surfaces: ReviewedGeometrySurface[]): ReviewedGeometrySource {
  if (!surfaces.length) {
    return {
      ...defaultReviewedGeometry(),
      reviewNotes: geometry.reviewNotes ?? ""
    };
  }
  const combinedText = surfaces
    .map((surface) => surface.stlText?.trim())
    .filter((text): text is string => Boolean(text))
    .join("\n\n");
  const combinedMetadata = combinedText ? metadataFromParsedStl(parseAsciiStlGeometry(combinedText)) : surfaces[0]?.metadata;
  const allRequiredReviewed = requiredReviewedSurfaceCoverageComplete(surfaces);
  const firstPath = surfaces.length === 1 ? surfaces[0].stlPath : "reviewed-multi-surface.stl";
  return {
    ...geometry,
    sourceType: surfaces.some((surface) => surface.sourceType === "uploaded-stl") ? "uploaded-stl" : "local-stl-path",
    cadReviewed: allRequiredReviewed,
    reviewedAt: allRequiredReviewed ? new Date().toISOString() : null,
    stlText: combinedText || undefined,
    stlPath: firstPath,
    metadata: combinedMetadata,
    boundaryTags: boundaryTagsFromSurfaces(surfaces),
    surfaces
  };
}

function patchNameForRole(tags: ReviewedGeometryBoundaryTag[] | undefined, role: ReviewedGeometryBoundaryRole) {
  return tags?.find((tag) => tag.role === role)?.patchName ?? "";
}

function normalizedBoundaryTags(tags: ReviewedGeometryBoundaryTag[] | undefined) {
  return boundaryTagRoles.map((role) => ({ role, patchName: patchNameForRole(tags, role) }));
}

function requiredBoundaryTagsComplete(tags: ReviewedGeometryBoundaryTag[] | undefined) {
  return requiredBoundaryTagRoles.every((role) => patchNameForRole(tags, role).trim().length > 0);
}

function patchNameIssue(patchName: string) {
  if (!patchName.trim()) return null;
  return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(patchName.trim()) ? null : "Use OpenFOAM-safe patch names: letters, numbers, underscores, hyphens.";
}

function parseAsciiStlGeometry(text: string): ParsedStlGeometry {
  const asciiValid = /^[\x00-\x7F]*$/.test(text);
  const normalized = text.toLowerCase();
  const validation: string[] = [];
  const hasSolid = normalized.includes("solid");
  const hasFacetNormal = normalized.includes("facet normal");
  const hasVertex = normalized.includes("vertex");
  if (!asciiValid) validation.push("STL contains non-ASCII characters.");
  if (!hasSolid) validation.push("Missing solid header.");
  if (!hasFacetNormal) validation.push("Missing facet normal records.");
  if (!hasVertex) validation.push("Missing vertex records.");

  const vertexPattern =
    /^\s*vertex\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)/gim;
  const vertices = [...text.matchAll(vertexPattern)].map((match) => ({
    x: Number.parseFloat(match[1]),
    y: Number.parseFloat(match[2]),
    z: Number.parseFloat(match[3])
  }));
  const facetCount = text.match(/^\s*facet\s+normal\b/gim)?.length ?? 0;
  const triangleCount = Math.min(facetCount || Math.floor(vertices.length / 3), Math.floor(vertices.length / 3));
  const triangles: StlPreviewTriangle[] = [];
  for (let index = 0; index < triangleCount * 3; index += 3) {
    const triangle: StlPreviewTriangle = [vertices[index], vertices[index + 1], vertices[index + 2]];
    if (triangle.every((vertex) => Number.isFinite(vertex.x) && Number.isFinite(vertex.y) && Number.isFinite(vertex.z))) {
      triangles.push(triangle);
    }
  }

  if (facetCount !== triangleCount) validation.push("Facet and vertex counts do not align cleanly.");
  if (triangleCount === 0) validation.push("No complete triangular facets were parsed.");

  const bounds =
    vertices.length > 0
      ? vertices.reduce(
          (current, vertex) => ({
            min: {
              x: Math.min(current.min.x, vertex.x),
              y: Math.min(current.min.y, vertex.y),
              z: Math.min(current.min.z, vertex.z)
            },
            max: {
              x: Math.max(current.max.x, vertex.x),
              y: Math.max(current.max.y, vertex.y),
              z: Math.max(current.max.z, vertex.z)
            }
          }),
          {
            min: { x: Number.POSITIVE_INFINITY, y: Number.POSITIVE_INFINITY, z: Number.POSITIVE_INFINITY },
            max: { x: Number.NEGATIVE_INFINITY, y: Number.NEGATIVE_INFINITY, z: Number.NEGATIVE_INFINITY }
          }
        )
      : null;

  const edgeCounts = new Map<string, number>();
  const vertexKey = (vertex: StlVertex) => `${vertex.x.toPrecision(12)},${vertex.y.toPrecision(12)},${vertex.z.toPrecision(12)}`;
  const edgeKey = (a: StlVertex, b: StlVertex) => [vertexKey(a), vertexKey(b)].sort().join("|");
  for (const [a, b, c] of triangles) {
    for (const key of [edgeKey(a, b), edgeKey(b, c), edgeKey(c, a)]) {
      edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
    }
  }
  const edgeCountValues = [...edgeCounts.values()];
  const openEdgeCount = edgeCountValues.filter((count) => count === 1).length;
  const nonManifoldEdgeCount = edgeCountValues.filter((count) => count > 2).length;
  const watertightStatus = triangleCount === 0 ? "unknown" : nonManifoldEdgeCount > 0 ? "non-manifold" : openEdgeCount > 0 ? "open" : "closed";
  if (openEdgeCount > 0) validation.push(`${openEdgeCount} open edge(s) detected by triangle-edge incidence.`);
  if (nonManifoldEdgeCount > 0) validation.push(`${nonManifoldEdgeCount} non-manifold edge(s) detected by triangle-edge incidence.`);

  return {
    triangleCount,
    bounds,
    openEdgeCount,
    nonManifoldEdgeCount,
    watertightStatus,
    asciiValid: asciiValid && hasSolid && hasFacetNormal && hasVertex && triangleCount > 0,
    validation: validation.length ? validation : ["ASCII STL sanity checks passed."],
    previewTriangles: triangles.slice(0, 160)
  };
}

function formatBoundsAxis(min: number | undefined, max: number | undefined) {
  return typeof min === "number" && typeof max === "number" ? `${formatNumber(min, 3)} to ${formatNumber(max, 3)}` : "n/a";
}

function previewPoint(vertex: StlVertex, bounds: NonNullable<ReviewedGeometryMetadata["bounds"]>) {
  const width = Math.max(bounds.max.x - bounds.min.x, 1e-9);
  const height = Math.max(bounds.max.y - bounds.min.y, 1e-9);
  return {
    x: 8 + ((vertex.x - bounds.min.x) / width) * 164,
    y: 82 - ((vertex.y - bounds.min.y) / height) * 68
  };
}

function reviewedGeometryLabel(geometry: ReviewedGeometrySource | undefined) {
  if (!geometry || geometry.sourceType === "flowlab-generated") return "Generated starter triSurface";
  if (geometry.sourceType === "uploaded-stl") return "User-reviewed uploaded STL";
  return "User-reviewed local STL path";
}

function diagnosticLatestEntries(summary: NonNullable<JobResultPayload["diagnosticSummary"]>[number]) {
  if (!("latest" in summary)) return [];
  return Object.entries(summary.latest).filter(([, value]) => Number.isFinite(value)).slice(0, 4);
}

function selectedEntity(project: ReturnType<typeof useFlowStore.getState>["project"], kind: "node" | "edge" | null, id: string | null) {
  if (!kind || !id) return null;
  return kind === "node" ? project.nodes[id] : project.edges[id];
}

function csvCell(value: string | number | null | undefined) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll("\"", "\"\"")}"` : text;
}

export function buildResultTimelineCsv(samples: ResultFieldTimelineSample[]) {
  const header = ["snapshot", "label", "time", "field", "location", "kind", "unit", "min", "max", "mean"];
  const rows = samples.map((sample, index) =>
    [
      index + 1,
      sample.label,
      Number.isFinite(sample.time) ? sample.time : "",
      sample.field,
      sample.location,
      sample.kind,
      sample.unit?.symbol,
      sample.min,
      sample.max,
      sample.mean
    ]
      .map(csvCell)
      .join(",")
  );
  return [header.join(","), ...rows].join("\n");
}

export function timestepFromResultPath(path: string): number | null {
  const fileName = path.split("/").at(-1) ?? path;
  const match = fileName.match(/(?:^|[_-])(\d+(?:\.\d+)?)(?=\.(?:vtk|vtu)$)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

export function snapshotTimeForFile(path: string, fileIndex: number, logSummary?: SolverLogSummary | null): number {
  const step = timestepFromResultPath(path);
  const timeSteps = logSummary?.timeSteps ?? [];
  if (step !== null && Number.isInteger(step) && step >= 0 && timeSteps[0] === 0 && timeSteps[step] !== undefined) return timeSteps[step];
  if (step !== null && Number.isInteger(step) && step > 0 && timeSteps[step - 1] !== undefined) return timeSteps[step - 1];
  if (step !== null && timeSteps.includes(step)) return step;
  if (timeSteps.length === 1) return timeSteps[0];
  if (timeSteps[fileIndex] !== undefined) return timeSteps[fileIndex];
  if (step !== null) return step;
  if (typeof logSummary?.latestTime === "number") return logSummary.latestTime;
  return fileIndex;
}

function artifactSnapshotTime(path: string, fileIndex: number, artifact: Pick<JobArtifactFile | JobArtifactPreview, "time">, logSummary?: SolverLogSummary | null) {
  return typeof artifact.time === "number" && Number.isFinite(artifact.time) ? artifact.time : snapshotTimeForFile(path, fileIndex, logSummary);
}

function formatSnapshotTime(time: number) {
  return `t${formatNumber(time, Number.isInteger(time) ? 0 : 4)}`;
}

function resultFieldKey(field: Pick<ResultFieldInventoryItem, "field" | "location" | "kind">) {
  return `${field.location}:${field.kind}:${field.field}`;
}

function canonicalResultFieldName(field: string) {
  const normalized = field.trim().toLowerCase();
  if (["u", "velocity", "vel"].includes(normalized)) return "velocity";
  if (["p", "p_rgh", "pressure", "static_pressure", "total_pressure"].includes(normalized)) return "pressure";
  if (["t", "temperature", "temp"].includes(normalized)) return "temperature";
  return normalized;
}

function timelineLevel(value: number | null, min: number, max: number) {
  if (value === null || !Number.isFinite(value)) return 0;
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return 100;
  return Math.max(6, Math.min(100, ((value - min) / (max - min)) * 100));
}

export default function App() {
  const {
    project,
    result,
    sweepRuns,
    selectedId,
    selectedKind,
    backendOnline,
    setProject,
    select,
    setMode,
    setOverlay,
    updateVisualization,
    setSolverTier,
    setAdvancedMode,
    updateSolverSettings,
    updateSolverMeshControls,
    updateEdge,
    updateNode,
    addNode,
    addEdge,
    moveNode,
    rotateNode,
    connectEdge,
    updateEdgeEndpoint,
    deleteSelected,
    undo,
    redo,
    canUndo,
    canRedo,
    runInstant,
    runSweep,
    setBackendOnline
  } = useFlowStore();
  const [solvers, setSolvers] = useState<SolverCapability[]>([]);
  const [runtimeStatuses, setRuntimeStatuses] = useState<SolverRuntimeStatus[]>([]);
  const [referenceCases, setReferenceCases] = useState<ReferenceCase[]>([]);
  const [referenceCasePlan, setReferenceCasePlan] = useState<ReferenceCaseImportPlan | null>(null);
  const [referenceCaseError, setReferenceCaseError] = useState<string | null>(null);
  const [validatedBenchmarks, setValidatedBenchmarks] = useState<ValidatedBenchmark[]>([]);
  const [validatedBenchmarkError, setValidatedBenchmarkError] = useState<string | null>(null);
  const [validatedPresetBusy, setValidatedPresetBusy] = useState(false);
  const [caseRecord, setCaseRecord] = useState<SolverCase | null>(null);
  const [jobRecord, setJobRecord] = useState<JobRecord | null>(null);
  const [recentJobs, setRecentJobs] = useState<RecentJob[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(true);
  const [activeDockPanel, setActiveDockPanel] = useState<DockPanelId>("field");
  const [isRunning, setIsRunning] = useState(true);
  const [resultSnapshots, setResultSnapshots] = useState<ResultSnapshot[]>([]);
  const [activeResultIndex, setActiveResultIndex] = useState(0);
  const [isPlayingResults, setIsPlayingResults] = useState(false);
  const [resultPlaybackRate, setResultPlaybackRate] = useState<(typeof resultPlaybackRates)[number]>(1);
  const [resultPlaybackLoop, setResultPlaybackLoop] = useState(true);
  const [activeResultField, setActiveResultField] = useState<ResultFieldSelection | null>(null);
  const [activeVectorComponent, setActiveVectorComponent] = useState<ResultVectorComponent>("magnitude");
  const [resultColorMap, setResultColorMap] = useState<ResultColorMap>("turbo");
  const [resultViewMode, setResultViewMode] = useState<ResultViewMode>("2d");
  const [canvasRenderMode, setCanvasRenderMode] = useState<CanvasRenderMode>("cinema");
  const [cinemaCamera, setCinemaCamera] = useState<CinemaCameraState>(defaultCinemaCamera);
  const [resultCamera, setResultCamera] = useState<ResultCamera>(defaultResultCamera);
  const [resultFieldFilter, setResultFieldFilter] = useState("");
  const [probeTarget, setProbeTarget] = useState<ProbeTarget | null>(null);
  const [resultError, setResultError] = useState<string | null>(null);
  const [projectMessage, setProjectMessage] = useState<string | null>(null);
  const [storageReady, setStorageReady] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const resultFileRef = useRef<HTMLInputElement | null>(null);

  const selected = selectedEntity(project, selectedKind, selectedId);
  const selectedEdge = selectedKind === "edge" && selected ? (selected as FluidEdge) : null;
  const selectedNode = selectedKind === "node" && selected ? (selected as FluidNode) : null;
  const selectedEdgeResult = selectedEdge ? result.edgeResults[selectedEdge.id] : null;
  const selectedNodeResult = selectedNode ? result.nodeResults[selectedNode.id] : null;
  const activeSnapshot = resultSnapshots[activeResultIndex] ?? null;
  const loadedResult = activeSnapshot?.dataset ?? null;
  const currentOpenBoundaryCampaign = validatedBenchmarks.find(
    (benchmark) => benchmark.id === "laminar-open-boundary-all-hex-v1"
  ) ?? null;

  useEffect(() => {
    setActiveDockPanel(defaultDockPanelByMode[project.visualization.mode]);
  }, [project.visualization.mode]);

  useEffect(() => {
    if (!loadedResult) return;
    setActiveDockPanel("field");
    setCanvasRenderMode(resultViewMode === "3d" ? "cinema" : "schematic");
  }, [loadedResult, resultViewMode]);
  const loadedFieldInventory = useMemo(() => listResultFields(loadedResult), [loadedResult]);
  const filteredFieldInventory = useMemo(() => {
    const query = resultFieldFilter.trim().toLowerCase();
    if (!query) return loadedFieldInventory;
    return loadedFieldInventory.filter((field) =>
      [field.field, field.location, field.kind, field.overlay ?? "", field.unit.symbol, field.unit.label].some((value) => value.toLowerCase().includes(query))
    );
  }, [loadedFieldInventory, resultFieldFilter]);
  const selectedFieldValues = useMemo(
    () => fieldValuesForSelection(loadedResult, activeResultField, activeVectorComponent),
    [activeResultField, activeVectorComponent, loadedResult]
  );
  const activeFieldValues = useMemo(
    () => selectedFieldValues ?? (activeResultField ? null : fieldValuesForOverlay(loadedResult, project.visualization.overlay)),
    [activeResultField, loadedResult, project.visualization.overlay, selectedFieldValues]
  );
  const fieldStats = useMemo(() => {
    if (activeFieldValues?.values.length) {
      const stats = fieldDescriptiveStats(activeFieldValues.values);
      if (!stats) return null;
      return {
        field: activeFieldValues.field,
        kind: activeFieldValues.kind,
        location: activeFieldValues.location,
        unit: activeFieldValues.unit,
        ...stats
      };
    }
    return activeResultField ? null : fieldStatsForOverlay(loadedResult, project.visualization.overlay);
  }, [activeFieldValues, activeResultField, loadedResult, project.visualization.overlay]);
  const fieldHistogram = useMemo(() => fieldHistogramForValues(activeFieldValues?.values ?? [], 14), [activeFieldValues]);
  const fieldHistogramMaxCount = useMemo(() => Math.max(...fieldHistogram.map((bin) => bin.count), 0), [fieldHistogram]);
  const activeColorMap = resultColorMapOptions.find((option) => option.id === resultColorMap) ?? resultColorMapOptions[0];
  const activeOverlayLabel = overlayOptions.find((option) => option.id === project.visualization.overlay)?.label ?? project.visualization.overlay;
  const activeResultFieldWarning = loadedResult && !activeFieldValues
    ? activeResultField
      ? `Pinned field ${activeResultField.field} is unavailable in ${activeSnapshot?.label ?? loadedResult.sourceName ?? "this result"}.`
      : `No ${activeOverlayLabel} field is loaded in ${activeSnapshot?.label ?? loadedResult.sourceName ?? "this result"}. Select an available field or import a result containing ${fieldNameForOverlay(project.visualization.overlay) ?? activeOverlayLabel}.`
    : null;
  const resultTimelineStats = useMemo(
    () => timelineStatsForSnapshots(resultSnapshots, project.visualization.overlay, activeResultField, activeVectorComponent),
    [activeResultField, activeVectorComponent, project.visualization.overlay, resultSnapshots]
  );
  const resultFieldCoverage = useMemo(
    () => fieldCoverageForSnapshots(resultSnapshots, project.visualization.overlay, activeResultField, activeVectorComponent),
    [activeResultField, activeVectorComponent, project.visualization.overlay, resultSnapshots]
  );
  const resultTimelineMeanRange = useMemo(() => {
    const means = resultTimelineStats.map((sample) => sample.mean).filter((value): value is number => value !== null && Number.isFinite(value));
    return {
      min: means.length ? Math.min(...means) : 0,
      max: means.length ? Math.max(...means) : 0
    };
  }, [resultTimelineStats]);
  const activeTimelineSample = resultTimelineStats[activeResultIndex] ?? null;
  const probeSample = useMemo(
    () =>
      probeTarget?.kind === "surface"
        ? sampleDatasetAtWorldPoint(
            loadedResult,
            project.visualization.overlay,
            probeTarget.point,
            activeResultField,
            activeVectorComponent,
            probeTarget.ownerCellIndex,
            probeTarget.nearestPointIndex,
            {
              pointIndices: probeTarget.trianglePointIndices,
              weights: probeTarget.barycentricWeights
            }
          )
        : probeTarget
          ? sampleDatasetAtCanvasPoint(
              loadedResult,
              project.visualization.overlay,
              probeTarget.point,
              probeTarget.size,
              activeResultField,
              activeVectorComponent
            )
          : null,
    [activeResultField, activeVectorComponent, loadedResult, probeTarget, project.visualization.overlay]
  );
  const activeWarnings = result.warnings.filter((warning) => warning.severity !== "info");
  const blockingWarnings = useMemo(() => activeWarnings.filter((warning) => warning.severity === "error"), [activeWarnings]);
  const solverResultPayload = jobRecord?.result as JobResultPayload | null | undefined;
  const solverLogSummary = solverResultPayload?.logSummary ?? null;
  const meshQuality = solverResultPayload?.meshQuality ?? null;
  const patchMetrics = solverResultPayload?.patchMetrics ?? null;
  const solverResidualRows = useMemo(
    () => Object.entries(solverLogSummary?.residuals ?? {}).sort(([left], [right]) => left.localeCompare(right)).slice(0, 6),
    [solverLogSummary?.residuals]
  );
  const diagnosticDockRows = useMemo(() => (solverResultPayload?.diagnosticSummary ?? []).slice(0, 4), [solverResultPayload?.diagnosticSummary]);
  const diagnosticWarningRows = useMemo(() => [...(solverLogSummary?.warnings ?? []), ...(solverLogSummary?.errors ?? [])].slice(0, 3), [solverLogSummary?.errors, solverLogSummary?.warnings]);
  const activeRuntime = useMemo(
    () => runtimeStatuses.find((status) => status.solver === project.solver.tier) ?? null,
    [project.solver.tier, runtimeStatuses]
  );
  const actionPoint = useMemo(() => {
    if (selectedNode) return selectedNode.position;
    if (selectedEdge) {
      const from = project.nodes[selectedEdge.from]?.position;
      const to = project.nodes[selectedEdge.to]?.position;
      if (from && to) return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
    }
    return null;
  }, [project.nodes, selectedEdge, selectedNode]);

  const totals = useMemo(() => {
    const edges = Object.values(result.edgeResults);
    return {
      flow: edges.reduce((sum, edge) => sum + Math.abs(edge.flowRate), 0),
      pressureDrop: edges.reduce((sum, edge) => sum + edge.pressureDrop, 0),
      maxRe: Math.max(...edges.map((edge) => edge.reynolds), 0),
      cavitation: edges.filter((edge) => edge.cavitationRisk).length
    };
  }, [result]);

  useEffect(() => {
    fetchHealth().then(setBackendOnline);
    fetchSolvers()
      .then((nextSolvers) => {
        setSolvers(Array.isArray(nextSolvers) ? nextSolvers : []);
      })
      .catch(() => setSolvers([{ id: "instant-1d", label: "Instant 1D hydraulics", installed: true, execution: "browser", notes: [] }]));
    fetchRuntimeDiagnostics()
      .then((nextStatuses) => {
        setRuntimeStatuses(Array.isArray(nextStatuses) ? nextStatuses : []);
      })
      .catch(() => setRuntimeStatuses([]));
    fetchReferenceCases()
      .then((registry) => {
        setReferenceCases(Array.isArray(registry.cases) ? registry.cases : []);
        setReferenceCaseError(null);
      })
      .catch((error) => {
        setReferenceCases([]);
        setReferenceCaseError(error instanceof Error ? error.message : "Reference cases are unavailable.");
      });
    fetchValidatedBenchmarks()
      .then((registry) => {
        setValidatedBenchmarks(Array.isArray(registry.benchmarks) ? registry.benchmarks : []);
        setValidatedBenchmarkError(null);
      })
      .catch((error) => {
        setValidatedBenchmarks([]);
        setValidatedBenchmarkError(error instanceof Error ? error.message : "Validated benchmark evidence is unavailable.");
      });
    fetchRecentJobs()
      .then((response) => {
        const jobs = Array.isArray(response.jobs) ? response.jobs : [];
        setRecentJobs(jobs);
        const activeJobId = window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY);
        const activeRun = jobs.find((item) => item.job.id === activeJobId);
        if (activeRun) activateRecentRun(activeRun, false);
      })
      .catch(() => setRecentJobs([]));
  }, [setBackendOnline]);

  useEffect(() => {
    const onDesktopSaveResult = (event: Event) => {
      const detail = (event as CustomEvent<{ status?: string; message?: string }>).detail;
      if (detail?.message) setProjectMessage(detail.message);
    };
    window.addEventListener("flowlab-desktop-save-result", onDesktopSaveResult);
    return () => window.removeEventListener("flowlab-desktop-save-result", onDesktopSaveResult);
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem("flowlab.project.v1");
    if (saved) {
      try {
        const parsed = parseProject(JSON.parse(saved), { allowTopologyWarnings: true });
        if (parsed.ok) {
          setProject(parsed.project);
          setProjectMessage("Restored saved project.");
        } else {
          setProjectMessage(`Saved project ignored: ${parsed.message}`);
        }
      } catch {
        setProjectMessage("Saved project ignored: invalid JSON.");
      }
    }
    setStorageReady(true);
  }, [setProject]);

  useEffect(() => {
    if (!storageReady) return;
    window.localStorage.setItem("flowlab.project.v1", JSON.stringify(project));
  }, [project, storageReady]);

  useEffect(() => {
    if (!loadedResult || !activeResultField || selectedFieldValues) return;
    const canonical = canonicalResultFieldName(activeResultField.field);
    const replacement = loadedFieldInventory.find(
      (field) =>
        canonicalResultFieldName(field.field) === canonical &&
        field.kind === activeResultField.kind &&
        field.location === activeResultField.location
    );
    if (replacement) {
      setActiveResultField({ field: replacement.field, location: replacement.location, kind: replacement.kind });
    }
  }, [activeResultField, activeSnapshot?.id, loadedFieldInventory, loadedResult, selectedFieldValues]);

  useEffect(() => {
    if (!isPlayingResults || resultSnapshots.length <= 1) return;
    const timer = window.setInterval(() => {
      setActiveResultIndex((index) => {
        if (index < resultSnapshots.length - 1) return index + 1;
        if (resultPlaybackLoop) return 0;
        window.setTimeout(() => setIsPlayingResults(false), 0);
        return index;
      });
    }, Math.max(180, 850 / resultPlaybackRate));
    return () => window.clearInterval(timer);
  }, [isPlayingResults, resultPlaybackLoop, resultPlaybackRate, resultSnapshots.length]);

  useEffect(() => {
    function handleGlobalShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const editable = target?.matches("input, textarea, select, [contenteditable='true']");
      if (editable) return;
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (modifier && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (modifier && event.key === "1") {
        event.preventDefault();
        scrollToPanel("components-panel");
        return;
      }
      if (modifier && event.key === "4") {
        event.preventDefault();
        scrollToPanel("reference-cases-panel");
        return;
      }
      if (event.key === " ") {
        event.preventDefault();
        setIsRunning((value) => !value);
        return;
      }
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        deleteSelected();
      }
    }
    window.addEventListener("keydown", handleGlobalShortcut);
    return () => window.removeEventListener("keydown", handleGlobalShortcut);
  }, [deleteSelected, redo, undo]);

  function stepResultTimeline(delta: -1 | 1) {
    setIsPlayingResults(false);
    setActiveResultIndex((index) => Math.max(0, Math.min(index + delta, resultSnapshots.length - 1)));
  }

  function addResultSnapshot(dataset: VtkResultDataset, label = dataset.sourceName ?? "loaded result", logSummary?: SolverLogSummary | null, stableId?: string) {
    const snapshot: ResultSnapshot = {
      id: stableId ?? `${Date.now()}-${label}-${dataset.points.length}`,
      label,
      time: snapshotTimeForFile(label, resultSnapshots.length, logSummary),
      dataset
    };
    addResultSnapshots([snapshot], snapshot.id);
  }

  function addResultSnapshots(nextSnapshots: ResultSnapshot[], activeSnapshotId?: string) {
    setResultSnapshots((snapshots) => {
      const merged = [...snapshots];
      for (const snapshot of nextSnapshots) {
        const existingIndex = merged.findIndex((existing) => existing.id === snapshot.id);
        if (existingIndex >= 0) {
          if (!(merged[existingIndex].preview !== true && snapshot.preview === true)) {
            merged[existingIndex] = { ...snapshot, time: merged[existingIndex].time };
          }
        } else {
          merged.push(snapshot);
        }
      }
      const next = merged.sort((a, b) => a.time - b.time || a.label.localeCompare(b.label));
      const targetId = activeSnapshotId ?? nextSnapshots.at(-1)?.id;
      const targetIndex = targetId ? next.findIndex((snapshot) => snapshot.id === targetId) : -1;
      setActiveResultIndex(targetIndex >= 0 ? targetIndex : Math.max(0, next.length - 1));
      return next;
    });
    setProbeTarget(null);
  }

  function loadPreviewResultSnapshots(previews: JobArtifactPreview[], startIndex = 0) {
    const resultPayload = jobRecord?.result as JobResultPayload | null | undefined;
    const snapshots: ResultSnapshot[] = [];
    const jobId = jobRecord?.id ?? "unknown";
    for (const [index, preview] of previews.entries()) {
      try {
        const label = `${preview.path} preview`;
        snapshots.push({
          id: `job:${jobId}:${preview.path}`,
          label,
          time: artifactSnapshotTime(preview.path, startIndex + index, preview, resultPayload?.logSummary),
          dataset: datasetFromPreview(preview, label),
          preview: true
        });
      } catch {
        // Skip previews that are intentionally bounded away from full geometry.
      }
    }
    if (snapshots.length === 0) {
      setResultError("No loadable VTK/VTU preview snapshots were returned.");
      return;
    }
    addResultSnapshots(snapshots, snapshots.at(-1)?.id);
    setResultError(null);
  }

  function ingestJobResultFiles(job: JobRecord) {
    const resultPayload = job.result as JobResultPayload | null | undefined;
    const resultFiles = Array.isArray(resultPayload?.resultFiles) ? (resultPayload.resultFiles as JobArtifactFile[]) : [];
    const parsedSnapshots = resultFiles.flatMap((file, fileIndex) => {
      if (!file.text || !/\.(vtk|vtu)$/i.test(file.path)) return [];
      try {
        return [
          {
            id: `job:${job.id}:${file.path}`,
            label: file.path,
            time: artifactSnapshotTime(file.path, fileIndex, file, resultPayload?.logSummary),
            dataset: parseVtkResult(file.text, file.path)
          }
        ];
      } catch {
        return [];
      }
    });
    if (parsedSnapshots.length === 0) {
      if (resultFiles.some((file) => file.text && /\.(vtk|vtu)$/i.test(file.path))) {
        setResultError("No supported VTK/VTU point or cell field result files were found in this job.");
      }
      return;
    }
    setResultError(null);
    setResultSnapshots((snapshots) => {
      const existing = new Set(snapshots.map((snapshot) => snapshot.id));
      const nextItems = parsedSnapshots
        .filter((snapshot) => !existing.has(snapshot.id))
        .sort((a, b) => a.time - b.time || a.label.localeCompare(b.label));
      const next = nextItems.length > 0
        ? [...snapshots, ...nextItems].sort((a, b) => a.time - b.time || a.label.localeCompare(b.label))
        : snapshots;
      const targetId = parsedSnapshots.at(-1)?.id;
      const targetIndex = targetId ? next.findIndex((snapshot) => snapshot.id === targetId) : -1;
      if (targetIndex >= 0) setActiveResultIndex(targetIndex);
      return next;
    });
  }

  async function loadSkippedResultArtifact(path: string) {
    if (!jobRecord) return;
    setResultError(`Loading ${path} in chunks...`);
    try {
      let offset = 0;
      let text = "";
      while (true) {
        const chunk = await fetchJobArtifactChunk(jobRecord.id, path, offset, CLIENT_ARTIFACT_CHUNK_SIZE);
        if (chunk.size > CLIENT_ARTIFACT_LOAD_LIMIT) {
          throw new Error(`Result artifact is ${chunk.size.toLocaleString()} bytes; browser chunk loading is capped at ${CLIENT_ARTIFACT_LOAD_LIMIT.toLocaleString()} bytes.`);
        }
        text += chunk.text;
        offset = chunk.nextOffset;
        if (chunk.complete) break;
      }
      const resultPayload = jobRecord.result as JobResultPayload | null | undefined;
      addResultSnapshot(parseVtkResult(text, path), path, resultPayload?.logSummary, `job:${jobRecord.id}:${path}`);
      setResultError(null);
    } catch (error) {
      setResultError(error instanceof Error ? error.message : `Could not load ${path}.`);
    }
  }

  function loadPreviewResultSnapshot(preview: JobArtifactPreview) {
    try {
      const resultPayload = jobRecord?.result as JobResultPayload | null | undefined;
      const label = `${preview.path} preview`;
      const snapshot: ResultSnapshot = {
        id: `job:${jobRecord?.id ?? "unknown"}:${preview.path}`,
        label,
        time: artifactSnapshotTime(preview.path, resultSnapshots.length, preview, resultPayload?.logSummary),
        dataset: datasetFromPreview(preview, label),
        preview: true
      };
      addResultSnapshots([snapshot], snapshot.id);
      setResultError(null);
    } catch (error) {
      setResultError(error instanceof Error ? error.message : "Could not load preview result.");
    }
  }

  useEffect(() => {
    if (!jobRecord || ["complete", "failed", "blocked", "cancelled"].includes(jobRecord.status)) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const nextJob = await fetchJob(jobRecord.id);
        if (cancelled) return;
        setJobRecord(nextJob);
        setRecentJobs((items) =>
          items.map((item) => (item.job.id === nextJob.id ? { ...item, job: nextJob } : item))
        );
        ingestJobResultFiles(nextJob);
      } catch {
        // Keep the last known job state visible; the backend status chip already shows service health.
      }
    };
    const timer = window.setInterval(poll, 1000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobRecord]);

  function activateRecentRun(item: RecentJob, announce = true) {
    setCaseRecord(item.case);
    setJobRecord(item.job);
    ingestJobResultFiles(item.job);
    window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, item.job.id);
    if (announce) {
      setProjectMessage(`Viewing ${solverLabels[item.job.solver]} run ${item.job.id} (${item.job.status}).`);
    }
  }

  function rememberRun(solverCase: SolverCase, job: JobRecord) {
    const item = { case: solverCase, job };
    setRecentJobs((items) => [item, ...items.filter((existing) => existing.job.id !== job.id)].slice(0, 20));
    window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, job.id);
  }

  async function launchAdvancedCase() {
    if (blockingWarnings.length > 0) {
      setProjectMessage(`Fix ${blockingWarnings.length} blocking network issue${blockingWarnings.length === 1 ? "" : "s"} before queueing a solver case.`);
      setCaseRecord(null);
      setJobRecord(null);
      return;
    }
    try {
      const solverCase = await generateSolverCase(project, project.solver.tier, project.solver.advancedMode);
      setCaseRecord(solverCase);
      const queued = await queueJob(solverCase);
      setJobRecord(queued);
      rememberRun(solverCase, queued);
      ingestJobResultFiles(queued);
      setProjectMessage(`${solverLabels[queued.solver]} case queued as ${queued.id}.`);
    } catch (error) {
      setCaseRecord(null);
      setJobRecord(null);
      setProjectMessage(error instanceof Error ? error.message : "Could not generate or queue the solver case.");
    }
  }

  async function launchValidatedPreset(benchmarkId: string) {
    setValidatedPresetBusy(true);
    setValidatedBenchmarkError(null);
    setProjectMessage("Minting the immutable validated preset and queueing its coarse OpenFOAM run...");
    try {
      const launched = await runValidatedPreset(benchmarkId);
      setCaseRecord(launched.case);
      setJobRecord(launched.job);
      rememberRun(launched.case, launched.job);
      ingestJobResultFiles(launched.job);
      setProjectMessage(
        launched.job.status === "blocked"
          ? `Validated preset is eligible, but execution is blocked: ${launched.job.error ?? "OpenFOAM runtime unavailable"}`
          : "Validated preset queued. Its label remains conditional on every runtime gate passing."
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not launch the validated preset.";
      setValidatedBenchmarkError(message);
      setProjectMessage(`Validated preset launch failed: ${message}`);
    } finally {
      setValidatedPresetBusy(false);
    }
  }

  async function loadReferenceCasePlan(caseId: string) {
    setReferenceCaseError(null);
    try {
      setReferenceCasePlan(await fetchReferenceCaseImportPlan(caseId));
    } catch (error) {
      setReferenceCasePlan(null);
      setReferenceCaseError(error instanceof Error ? error.message : "Could not load the reference case import plan.");
    }
  }

  function exportProject() {
    downloadFiles([
      {
        filename: `${project.name.toLowerCase().replaceAll(" ", "-")}.flowlab.json`,
        text: JSON.stringify(project, null, 2),
        type: "application/json"
      }
    ]);
  }

  function exportResultTimelineCsv() {
    const slug = project.name.toLowerCase().replaceAll(" ", "-").replace(/[^a-z0-9._-]+/g, "-");
    downloadFiles([{ filename: `${slug}.flowlab.field-timeline.csv`, text: buildResultTimelineCsv(resultTimelineStats), type: "text/csv" }]);
  }

  function downloadFiles(files: DesktopExportFile[]) {
    const electronHandler = window.flowlabDesktop;
    if (electronHandler) {
      void electronHandler.saveFiles(files)
        .then((detail) => {
          window.dispatchEvent(new CustomEvent("flowlab-desktop-save-result", { detail }));
        })
        .catch((error) => {
          window.dispatchEvent(new CustomEvent("flowlab-desktop-save-result", {
            detail: {
              status: "error",
              message: error instanceof Error ? error.message : "Export failed."
            }
          }));
        });
      return;
    }
    const desktopHandler = window.webkit?.messageHandlers?.flowlabDesktop;
    if (desktopHandler) {
      desktopHandler.postMessage({ type: "save-files", files });
      return;
    }
    for (const file of files) {
      const blob = new Blob([file.text], { type: file.type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    }
  }

  function exportResultBundle() {
    const slug = project.name.toLowerCase().replaceAll(" ", "-");
    const files: DesktopExportFile[] = [
      { filename: `${slug}.flowlab.project.json`, text: JSON.stringify(project, null, 2), type: "application/json" },
      {
        filename: `${slug}.flowlab.result.json`,
        text: JSON.stringify({ projectName: project.name, instantResult: result, activeResultIndex, resultSnapshots }, null, 2),
        type: "application/json"
      }
    ];
    if (loadedResult?.sourceText) {
      const extension = loadedResult.format === "vtu-ascii-v1" ? "vtu" : "vtk";
      files.push({ filename: `${slug}.flowlab.loaded-result.${extension}`, text: loadedResult.sourceText, type: "text/plain" });
    }
    downloadFiles(files);
  }

  function importProject(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = parseProject(JSON.parse(String(reader.result)));
        if (!parsed.ok) {
          setProjectMessage(`Invalid project: ${parsed.message}`);
          return;
        }
        setProject(parsed.project);
        setProjectMessage(`Imported ${parsed.project.name}.`);
      } catch {
        setProjectMessage("Invalid project: file is not valid JSON.");
      } finally {
        if (fileRef.current) fileRef.current.value = "";
      }
    };
    reader.readAsText(file);
  }

  function importResult(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = parseVtkResult(String(reader.result), file.name);
        addResultSnapshot(parsed, file.name);
        setResultError(null);
        setMode("analyze");
        if (!fieldAvailable(parsed, project.visualization.overlay)) setOverlay("pressure");
      } catch (error) {
        setResultError(error instanceof Error ? error.message : "Could not parse result file.");
      }
    };
    reader.readAsText(file);
  }

  async function loadFixtureResult() {
    try {
      const response = await fetch("/fixtures/venturi-result.vtk");
      if (!response.ok) throw new Error(`Fixture request failed: ${response.status}`);
      const text = await response.text();
      addResultSnapshot(parseVtkResult(text, "venturi-result.vtk"), "venturi-result.vtk");
      setResultError(null);
      setMode("analyze");
      setOverlay("pressure");
    } catch (error) {
      setResultError(error instanceof Error ? error.message : "Could not load fixture result.");
    }
  }

  function scrollToPanel(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function chooseOverlay(overlay: OverlayMode) {
    setActiveResultField(null);
    setOverlay(overlay);
  }

  function setViewportMode(mode: CanvasRenderMode) {
    setCanvasRenderMode(mode);
    if (loadedResult) setResultViewMode(mode === "cinema" ? "3d" : "2d");
  }

  function setResultMode(mode: ResultViewMode) {
    setResultViewMode(mode);
    if (loadedResult) setCanvasRenderMode(mode === "3d" ? "cinema" : "schematic");
  }

  function handleAddEdge(type: FluidEdge["type"]) {
    const outcome = addEdge(type);
    setProjectMessage(outcome.ok ? `Added ${type} ${outcome.id}.` : outcome.message);
  }

  return (
    <main className="app-shell workspace-shell">
      <header className="top-hud">
        <div className="toolbar-left">
          <div className="brand-lockup">
            <Waves size={21} />
            <div>
              <strong>FlowLab</strong>
              <span>{project.name}</span>
            </div>
          </div>
          <div className="mode-tabs" aria-label="Workspace mode">
            {modeOptions.map((mode) => (
              <button key={mode.id} className={project.visualization.mode === mode.id ? "active" : ""} aria-pressed={project.visualization.mode === mode.id} onClick={() => setMode(mode.id)}>
                {mode.label}
              </button>
            ))}
          </div>
        </div>

        <div className="toolbar-right">
          <label>
            <span aria-hidden="true">New case solver</span>
            <select aria-label="Solver" value={project.solver.tier} onChange={(event) => setSolverTier(event.target.value as SolverTier)}>
              {Object.entries(solverLabels).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Mode
            <select value={project.solver.advancedMode} onChange={(event) => setAdvancedMode(event.target.value as AdvancedPhysicsMode)}>
              {advancedModes.map((mode) => (
                <option key={mode.id} value={mode.id}>
                  {mode.label}
                </option>
              ))}
            </select>
          </label>
          <div className={`solver-chip ${result.stable ? "ok" : "bad"}`}>
            <Activity size={16} />
            Preview {isRunning ? "running" : "paused"}
          </div>
          {jobRecord ? (
            <div className="viewing-run-chip" title={jobRecord.id}>
              Viewing: {solverLabels[jobRecord.solver]} · {jobRecord.status}
            </div>
          ) : null}
          <div className="toolbar-actions">
            <button aria-pressed={isRunning} aria-keyshortcuts="Space" onClick={() => setIsRunning((value) => !value)} title={isRunning ? "Pause preview" : "Run preview"}>
              {isRunning ? <Pause size={18} /> : <Play size={18} />}
              <span>{isRunning ? "Pause preview" : "Run preview"}</span>
            </button>
            <button type="button" aria-label="Undo" aria-keyshortcuts="Meta+Z Control+Z" disabled={!canUndo} onClick={undo} title="Undo model edit">
              <Undo2 size={17} />
            </button>
            <button type="button" aria-label="Redo" aria-keyshortcuts="Shift+Meta+Z Shift+Control+Z Meta+Y Control+Y" disabled={!canRedo} onClick={redo} title="Redo model edit">
              <Redo2 size={17} />
            </button>
            <button onClick={runInstant} title="Recompute">
              <RotateCcw size={18} />
            </button>
            <button onClick={exportProject} title="Export project">
              <Download size={18} />
            </button>
            <button onClick={exportResultBundle} title="Export project and results">
              <FileDown size={18} />
            </button>
            <button onClick={() => fileRef.current?.click()} title="Import project">
              <Upload size={18} />
            </button>
            <input
              ref={fileRef}
              aria-label="Project import file"
              data-testid="project-import-file"
              type="file"
              accept=".json,.flowlab.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) importProject(file);
              }}
            />
            <button onClick={() => resultFileRef.current?.click()} title="Import VTK/VTU result">
              <Upload size={18} />
            </button>
            <input
              ref={resultFileRef}
              aria-label="Result import file"
              data-testid="result-import-file"
              type="file"
              accept=".vtk,.vtu"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) importResult(file);
              }}
            />
          </div>
        </div>
      </header>

      <aside className="left-sidebar cinema-sidebar">
        <nav className="icon-rail" aria-label="Cinema workspace rail">
          <button className={project.visualization.mode === "simulate" ? "active" : ""} aria-pressed={project.visualization.mode === "simulate"} title="Simulate" onClick={() => setMode("simulate")}>
            <Play size={18} />
          </button>
          <button title="Components" onClick={() => scrollToPanel("components-panel")}>
            <Plus size={18} />
          </button>
          <button title="Layers" onClick={() => scrollToPanel("project-layers-panel")}>
            <Layers3 size={18} />
          </button>
          <button title="Reference Cases" onClick={() => scrollToPanel("reference-cases-panel")}>
            <FileDown size={18} />
          </button>
          <span />
          <button title="Solver diagnostics" onClick={() => scrollToPanel("solver-diagnostics-panel")}>
            <Activity size={18} />
          </button>
          <button title="Settings" onClick={() => scrollToPanel("inspector-panel")}>
            <SlidersHorizontal size={18} />
          </button>
        </nav>

        <div className="sidebar-stack">
          <section className="side-section component-library" id="components-panel">
            <div className="side-section-header">
              Components
              <small>⌘ 1</small>
            </div>
            <div className="component-rail" aria-label="Component palette">
              <button title="Add source" onClick={() => addNode("source")}>
                <Plus size={17} />
                Source
              </button>
              <button title="Add pump" onClick={() => addNode("pump")}>
                <Plus size={17} />
                Pump
              </button>
              <button title="Add Venturi" onClick={() => handleAddEdge("venturi")}>
                <GitBranchPlus size={17} />
                Venturi
              </button>
              <button title="Add pipe" onClick={() => handleAddEdge("pipe")}>
                <GitBranchPlus size={17} />
                Pipe
              </button>
              <button title="Add bend" onClick={() => handleAddEdge("bend")}>
                <GitBranchPlus size={17} />
                Elbow
              </button>
              <button title="Add valve" onClick={() => handleAddEdge("valve")}>
                <GitBranchPlus size={17} />
                Valve
              </button>
              <button title="Add mixer" onClick={() => addNode("mixer")}>
                <Plus size={17} />
                Mixer
              </button>
              <button title="Add sink" onClick={() => addNode("sink")}>
                <Plus size={17} />
                Sink
              </button>
            </div>
          </section>

          <section className="side-section project-tree" id="project-layers-panel">
            <div className="split-heading">
              <span>Project</span>
              <span>Layers</span>
            </div>
            <div className="tree-grid">
              <div className="tree-list">
                <strong>Demo Network</strong>
                <span>Geometry</span>
                <button
                  disabled={Object.keys(project.nodes).length === 0}
                  onClick={() => {
                    const first = Object.keys(project.nodes)[0];
                    if (first) select("node", first);
                  }}
                >
                  Nodes ({Object.keys(project.nodes).length})
                </button>
                <button
                  disabled={Object.keys(project.edges).length === 0}
                  onClick={() => {
                    const first = Object.keys(project.edges)[0];
                    if (first) select("edge", first);
                  }}
                >
                  Edges ({Object.keys(project.edges).length})
                </button>
                <span>Results</span>
                <button disabled={recentJobs.length === 0 && !loadedResult} onClick={() => recentJobs[0] && activateRecentRun(recentJobs[0])}>
                  {loadedResult ? `${resultSnapshots.length} field snapshots` : recentJobs.length > 0 ? "Last Run" : "No runs yet"}
                </button>
              </div>
              <div className="layer-list">
                <button aria-pressed={project.visualization.overlay !== "geometry"} onClick={() => chooseOverlay(project.visualization.overlay === "geometry" ? "velocity" : "geometry")}>
                  <span className="visibility-dot" style={{ background: "#19d4ff" }} />
                  Flow Field
                </button>
                <button aria-pressed={project.visualization.particles} onClick={() => updateVisualization({ particles: !project.visualization.particles })}>
                  <span className="visibility-dot" style={{ background: "#62f3bd" }} />
                  Particles
                </button>
                <button aria-pressed={project.visualization.mode === "analyze"} onClick={() => { setMode("analyze"); setProjectMessage("Probe mode enabled. Click the canvas to sample the active field."); }}>
                  <span className="visibility-dot" style={{ background: "#f7d84b" }} />
                  Probes
                </button>
                <button aria-pressed={project.visualization.grid} onClick={() => updateVisualization({ grid: !project.visualization.grid })}>
                  <span className="visibility-dot" style={{ background: "#7e91ff" }} />
                  Mesh grid
                </button>
                <button onClick={() => scrollToPanel("warnings-panel")}>
                  <span className="visibility-dot" style={{ background: "#ff9654" }} />
                  Risk flags
                </button>
              </div>
            </div>
          </section>

          <section className="side-section reference-cases-panel" id="reference-cases-panel" aria-label="Reference Cases">
            <div className="side-section-header">
              Reference Cases
              <small>⌘ 4</small>
            </div>
            <div className="reference-case-list">
              {referenceCases.length > 0 ? (
                referenceCases.slice(0, 5).map((item) => (
                  <button key={item.id} onClick={() => loadReferenceCasePlan(item.id)} className={referenceCasePlan?.caseId === item.id ? "active" : ""}>
                    <span>{item.label}</span>
                    <small>{solverLabels[item.solver]} · {item.source.casePath.split("/").slice(-2).join("/")}</small>
                  </button>
                ))
              ) : (
                <small>{referenceCaseError ?? "Reference cases loading..."}</small>
              )}
            </div>
            {referenceCasePlan ? (
              <div className="reference-case-plan" aria-label="Reference case import plan">
                <strong>{referenceCasePlan.label}</strong>
                <span>{referenceCasePlan.importMode}</span>
                <small>{referenceCasePlan.requiredUserActions[0]}</small>
              </div>
            ) : null}
            {referenceCaseError ? <small className="job-error">{referenceCaseError}</small> : null}
            <div className="reference-case-plan" aria-label="Validated regimes">
              <strong>Validated regimes</strong>
              {validatedBenchmarks.length > 0 ? validatedBenchmarks.map((benchmark) => (
                <div key={benchmark.id}>
                  <span>{benchmark.label}</span>
                  <small>
                    {benchmark.scientificStatus === "analysis-only-narrow-envelope"
                      ? "Analysis-only narrow envelope"
                      : benchmark.scientificStatus === "validated-bounded-regime"
                        ? "Validated bounded regime"
                        : benchmark.scientificStatus === "campaign-promotion-blocked"
                          ? "Candidate regime"
                        : benchmark.scientificStatus}
                    {benchmark.promotionBlocked ? " · promotion blocked" : " · bounded regime promoted"}
                  </small>
                  <small>{benchmark.applicability[0]}</small>
                  <small>{benchmark.limits[0]}</small>
                  {benchmark.promotionBlocked && benchmark.blockingReasons?.length ? (
                    <div className="blocking-reasons" aria-label={`${benchmark.label} blocking gates`}>
                      {benchmark.blockingReasons.map((reason) => (
                        <small className="job-error" key={reason}>{reason}</small>
                      ))}
                    </div>
                  ) : null}
                  {benchmark.id === "laminar-open-boundary-all-hex-v1" && !benchmark.promotionBlocked ? (
                    <button
                      type="button"
                      className="validated-preset-action"
                      onClick={() => launchValidatedPreset(benchmark.id)}
                      disabled={validatedPresetBusy}
                    >
                      <Beaker size={13} />
                      {validatedPresetBusy ? "Queueing validated preset..." : "Run validated coarse preset"}
                    </button>
                  ) : null}
                </div>
              )) : <small>{validatedBenchmarkError ?? "Validated regimes loading..."}</small>}
              {validatedBenchmarkError ? <small className="job-error">{validatedBenchmarkError}</small> : null}
            </div>
          </section>

          <section className="side-section run-status">
            <div className="panel-title">
              <CircleGauge size={16} />
              Run status
            </div>
            <dl className="status-list">
              <div>
                <dt>Time</dt>
                <dd>{activeSnapshot ? `t = ${formatSnapshotTime(activeSnapshot.time)}` : "live"}</dd>
              </div>
              <div>
                <dt>Backend</dt>
                <dd>{backendOnline ? "Online" : "Offline"}</dd>
              </div>
            </dl>
            <div className="recent-run-list" aria-label="Recent solver runs">
              <strong>Recent runs</strong>
              {recentJobs.length > 0 ? recentJobs.slice(0, 4).map((item) => (
                <button
                  key={item.job.id}
                  className={jobRecord?.id === item.job.id ? "active" : ""}
                  aria-pressed={jobRecord?.id === item.job.id}
                  onClick={() => activateRecentRun(item)}
                  title={item.job.id}
                >
                  <span>{solverLabels[item.job.solver]}</span>
                  <small>{item.job.status} · {new Date(item.job.updatedAt).toLocaleString()}</small>
                </button>
              )) : <small>No persisted solver runs yet.</small>}
            </div>
          </section>
        </div>
      </aside>

      <section className="canvas-region">
        <SimulationCanvas
          project={project}
          result={result}
          resultDataset={loadedResult}
          resultFieldSelection={activeResultField}
          resultVectorComponent={activeVectorComponent}
          resultColorMap={resultColorMap}
          canvasRenderMode={canvasRenderMode}
          cinemaCamera={cinemaCamera}
          resultViewMode={resultViewMode}
          resultCamera={resultCamera}
          previewPlaying={isRunning}
          onCinemaCameraChange={setCinemaCamera}
          selectedId={selectedId}
          selectedKind={selectedKind}
          onSelect={select}
          onMoveNode={moveNode}
          onRotateNode={rotateNode}
          onConnectEdge={connectEdge}
          onUpdateEdgeEndpoint={updateEdgeEndpoint}
          onProbePoint={(point, size, surfaceProbe) =>
            setProbeTarget(
              surfaceProbe
                ? { kind: "surface", ...surfaceProbe }
                : surfaceProbe === null
                  ? null
                  : { kind: "canvas", point, size }
            )
          }
        />
        <p id="canvas-status" className="sr-only" aria-live="polite">
          {selected ? `Selected ${selectedKind}: ${(selected as FluidNode | FluidEdge).label}. ${canvasRenderMode === "cinema" ? "Cinema 3D camera" : "Schematic 2D viewport"}. Press F to fit or 0 to reset.` : "No item selected."}
        </p>
        <section className="overlay-switcher">
          {overlayOptions.map((overlay) => (
            <button
              key={overlay.id}
              className={`${project.visualization.overlay === overlay.id ? "active" : ""} ${
                loadedResult && fieldAvailable(loadedResult, overlay.id) ? "has-field" : ""
              }`}
              title={
                loadedResult && fieldNameForOverlay(overlay.id)
                  ? fieldAvailable(loadedResult, overlay.id)
                    ? `Loaded VTK field: ${fieldNameForOverlay(overlay.id)}`
                    : `No loaded VTK field for ${overlay.label}`
                  : overlay.label
              }
              onClick={() => {
                chooseOverlay(overlay.id);
              }}
              aria-pressed={project.visualization.overlay === overlay.id}
            >
              {overlay.label}
            </button>
          ))}
        </section>

        {actionPoint && selectedId ? (
          <section
            className="action-pad"
            style={{
              left: `clamp(10px, ${actionPoint.x + 110}px, calc(100% - 132px))`,
              top: `clamp(10px, ${actionPoint.y - 140}px, calc(100% - 124px))`
            }}
            aria-label="Selected component actions"
          >
            <button onClick={() => setMode("design")} aria-pressed={project.visualization.mode === "design"}>
              <Move size={18} />
              <span>Edit</span>
            </button>
            <button onClick={deleteSelected}>
              <Trash2 size={18} />
              <span>Delete</span>
            </button>
            <button
              onClick={() => {
                setMode("analyze");
                chooseOverlay("velocity");
              }}
              aria-pressed={project.visualization.mode === "analyze"}
            >
              <Crosshair size={18} />
              <span>Analyze</span>
            </button>
            <button onClick={() => chooseOverlay(project.visualization.overlay === "geometry" ? "velocity" : "geometry")}>
              <EyeOff size={18} />
              <span>{project.visualization.overlay === "geometry" ? "Flow" : "Geometry"}</span>
            </button>
          </section>
        ) : null}
      </section>

      <aside className="inspector" id="inspector-panel">
        <div className="panel-title">
          <SlidersHorizontal size={16} />
          Inspector
        </div>
        <label>
          Preset
          <select
            value={project.name}
            onChange={(event) => {
              const next = presets.find((preset) => preset.name === event.target.value);
              if (next) setProject(next);
            }}
          >
            {presets.map((preset) => (
              <option key={preset.name}>{preset.name}</option>
            ))}
          </select>
        </label>
        {projectMessage ? <p className={projectMessage.startsWith("Invalid") ? "import-message error" : "import-message"}>{projectMessage}</p> : null}
        {selectedEdge ? (
          <EdgeInspector
            edge={selectedEdge}
            nodes={project.nodes}
            result={selectedEdgeResult}
            onChange={(patch) => updateEdge(selectedEdge.id, patch)}
            onEndpointChange={(endpoint, nodeId, port) => updateEdgeEndpoint(selectedEdge.id, endpoint, nodeId, port)}
          />
        ) : null}
        {selectedNode ? (
          <NodeInspector
            node={selectedNode}
            result={selectedNodeResult}
            onChange={(patch) => updateNode(selectedNode.id, patch)}
            onRotate={(rotation) => rotateNode(selectedNode.id, rotation)}
          />
        ) : null}

        <button className="accordion" onClick={() => setAdvancedOpen((value) => !value)}>
          <span>
            <Layers3 size={16} />
            Advanced solvers
          </span>
          <ChevronDown size={16} className={advancedOpen ? "open" : ""} />
        </button>
        {advancedOpen ? (
          <div className="advanced-block">
            <MeshControlsPanel
              solver={project.solver}
              selectedEdge={selectedEdge}
              onSolverChange={updateSolverSettings}
              onMeshControlsChange={updateSolverMeshControls}
            />
            <div className="solver-list">
              {solvers.map((solver) => (
                <div key={solver.id} className={solver.installed ? "solver-row installed" : "solver-row blocked"}>
                  <span>{solver.label}</span>
                  <small>{solver.installed ? solver.execution : "missing"}</small>
                </div>
              ))}
            </div>
            <button className="primary-action" onClick={launchAdvancedCase} disabled={blockingWarnings.length > 0}>
              <Box size={16} />
              Generate / queue case
            </button>
            {blockingWarnings.length > 0 ? (
              <small className="job-error">
                Fix {blockingWarnings.length} blocking network issue{blockingWarnings.length === 1 ? "" : "s"} before queueing a solver case.
              </small>
            ) : null}
            <button className="secondary-action" onClick={loadFixtureResult}>
              <Layers3 size={16} />
              Load fixture result
            </button>
            <small className="backend-state">
              Solver service: {backendOnline ? "online" : "offline"} · advanced jobs need Docker or native solvers
            </small>
            <RuntimeReadiness statuses={runtimeStatuses} activeSolver={project.solver.tier} backendOnline={backendOnline} />
            {activeRuntime && !activeRuntime.runnable ? (
              <small className="job-error">
                {solverLabels[project.solver.tier]} cannot run locally yet: {activeRuntime.blockers[0] ?? "runtime dependency missing"}
              </small>
            ) : null}
            <div className="result-state" aria-live="polite">
              <strong>Result overlay</strong>
              <span>
                {loadedResult
                  ? `Snapshot ${activeResultIndex + 1}/${resultSnapshots.length} · ${loadedResult.fields.join(", ")}`
                  : "No VTK/VTU result loaded"}
              </span>
              {resultError ? <small>{resultError}</small> : null}
            </div>
            {caseRecord ? (
              <CaseSummary
                solverCase={caseRecord}
                job={jobRecord}
                currentCampaign={currentOpenBoundaryCampaign}
                onLoadSkippedResult={loadSkippedResultArtifact}
                onPreviewResult={loadPreviewResultSnapshot}
                onPreviewResults={loadPreviewResultSnapshots}
              />
            ) : null}
          </div>
        ) : null}
      </aside>

      <footer className="bottom-dock">
        <nav className="dock-tabs" aria-label="Workspace panels">
          {dockPanelOptions.map((panel) => (
            <button
              key={panel.id}
              type="button"
              className={activeDockPanel === panel.id ? "active" : ""}
              aria-pressed={activeDockPanel === panel.id}
              onClick={() => setActiveDockPanel(panel.id)}
            >
              {panel.label}
              {panel.id === "warnings" && activeWarnings.length > 0 ? <strong>{activeWarnings.length}</strong> : null}
            </button>
          ))}
        </nav>
        <section className={`dock-panel field-viewer ${activeDockPanel === "field" ? "active" : ""}`} hidden={activeDockPanel !== "field"}>
          <div className="panel-title">
            <Layers3 size={16} />
            Field viewer
          </div>
          <div className="canvas-render-mode" aria-label="Canvas render mode">
            {canvasRenderModeOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                className={canvasRenderMode === option.id ? "active" : ""}
                aria-pressed={canvasRenderMode === option.id}
                onClick={() => setViewportMode(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
          {canvasRenderMode === "cinema" ? (
            <div className="cinema-camera-controls" aria-label="Cinema camera controls">
              <div className="cinema-camera-presets">
                <button type="button" aria-label="Reset Cinema camera" title="Reset Cinema camera" onClick={() => setCinemaCamera(defaultCinemaCamera)}>
                  <RotateCcw size={13} />
                </button>
                <button type="button" onClick={() => setCinemaCamera({ yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } })}>
                  Iso
                </button>
                <button type="button" onClick={() => setCinemaCamera({ yaw: 0, pitch: 76, zoom: 1.05, pan: { x: 0, y: 0 } })}>
                  Top
                </button>
                <button type="button" onClick={() => setCinemaCamera({ yaw: 0, pitch: 8, zoom: 1, pan: { x: 0, y: 0 } })}>
                  Front
                </button>
              </div>
              <div className="camera-sliders compact">
                <label>
                  Orbit
                  <input
                    aria-label="Cinema camera orbit"
                    type="range"
                    min="-180"
                    max="180"
                    step="1"
                    value={cinemaCamera.yaw}
                    onChange={(event) => setCinemaCamera((camera) => ({ ...camera, yaw: Number(event.target.value) }))}
                  />
                  <strong>{Math.round(cinemaCamera.yaw)} deg</strong>
                </label>
                <label>
                  Pitch
                  <input
                    aria-label="Cinema camera pitch"
                    type="range"
                    min="-12"
                    max="78"
                    step="1"
                    value={cinemaCamera.pitch}
                    onChange={(event) => setCinemaCamera((camera) => ({ ...camera, pitch: Number(event.target.value) }))}
                  />
                  <strong>{Math.round(cinemaCamera.pitch)} deg</strong>
                </label>
                <label>
                  Zoom
                  <input
                    aria-label="Cinema camera zoom"
                    type="range"
                    min="0.55"
                    max="1.8"
                    step="0.05"
                    value={cinemaCamera.zoom}
                    onChange={(event) => setCinemaCamera((camera) => ({ ...camera, zoom: Number(event.target.value) }))}
                  />
                  <strong>{formatNumber(cinemaCamera.zoom, 2)}x</strong>
                </label>
              </div>
            </div>
          ) : null}
          {loadedResult ? (
            <div className="result-view-controls" aria-label={resultViewMode === "3d" ? "3D result camera controls" : "2D result controls"}>
              <div className="result-view-mode" aria-label="Result view mode">
                <button type="button" title="2D result / Schematic" className={resultViewMode === "2d" ? "active" : ""} aria-pressed={resultViewMode === "2d"} onClick={() => setResultMode("2d")}>
                  2D
                </button>
                <button type="button" title="3D result / Cinema" className={resultViewMode === "3d" ? "active" : ""} aria-pressed={resultViewMode === "3d"} onClick={() => setResultMode("3d")}>
                  3D
                </button>
                <button
                  type="button"
                  aria-label="Reset result camera"
                  title="Reset result camera"
                  onClick={() => {
                    setResultMode("3d");
                    setResultCamera(defaultResultCamera);
                    setCinemaCamera(defaultCinemaCamera);
                  }}
                >
                  <RotateCcw size={13} />
                </button>
              </div>
              {resultViewMode === "3d" ? (
                <div className="camera-sliders">
                  <label>
                    Yaw
                    <input
                      aria-label="Result camera yaw"
                      type="range"
                      min="-180"
                      max="180"
                      step="1"
                      value={cinemaCamera.yaw}
                      onChange={(event) => {
                        const yaw = Number(event.target.value);
                        setCinemaCamera((camera) => ({ ...camera, yaw }));
                        setResultCamera((camera) => ({ ...camera, yaw }));
                      }}
                    />
                    <strong>{Math.round(cinemaCamera.yaw)} deg</strong>
                  </label>
                  <label>
                    Pitch
                    <input
                      aria-label="Result camera pitch"
                      type="range"
                      min="-80"
                      max="80"
                      step="1"
                      value={cinemaCamera.pitch}
                      onChange={(event) => {
                        const pitch = Number(event.target.value);
                        setCinemaCamera((camera) => ({ ...camera, pitch }));
                        setResultCamera((camera) => ({ ...camera, pitch }));
                      }}
                    />
                    <strong>{Math.round(cinemaCamera.pitch)} deg</strong>
                  </label>
                  <label>
                    Zoom
                    <input
                      aria-label="Result camera zoom"
                      type="range"
                      min="0.5"
                      max="2.2"
                      step="0.05"
                      value={cinemaCamera.zoom}
                      onChange={(event) => {
                        const zoom = Number(event.target.value);
                        setCinemaCamera((camera) => ({ ...camera, zoom }));
                        setResultCamera((camera) => ({ ...camera, zoom }));
                      }}
                    />
                    <strong>{formatNumber(cinemaCamera.zoom, 2)}x</strong>
                  </label>
                </div>
              ) : null}
            </div>
          ) : null}
          <label>
            Variable
            <select
              value={project.visualization.overlay}
              onChange={(event) => {
                setActiveResultField(null);
                setActiveVectorComponent("magnitude");
                setOverlay(event.target.value as OverlayMode);
              }}
            >
              {overlayOptions.map((overlay) => (
                <option key={overlay.id} value={overlay.id}>
                  {overlay.label}
                </option>
              ))}
            </select>
          </label>
          {loadedFieldInventory.length > 0 ? (
            <>
              <label className="field-filter-control">
                Filter fields
                <input
                  aria-label="Filter result fields"
                  type="search"
                  value={resultFieldFilter}
                  placeholder="name, location, kind"
                  onChange={(event) => setResultFieldFilter(event.target.value)}
                />
              </label>
              <div className="field-filter-summary" aria-label="Result field filter summary">
                {filteredFieldInventory.length}/{loadedFieldInventory.length} fields shown
              </div>
              {filteredFieldInventory.length > 0 ? (
                <div className="loaded-field-list" aria-label="Loaded result fields">
                  {filteredFieldInventory.map((field) => {
                    const key = resultFieldKey(field);
                    const active = activeResultField ? resultFieldKey(activeResultField) === key : field.overlay === project.visualization.overlay && fieldAvailable(loadedResult, project.visualization.overlay);
                    return (
                      <button
                        key={key}
                        type="button"
                        className={active ? "active" : ""}
                        onClick={() => {
                          setActiveResultField({ field: field.field, location: field.location, kind: field.kind });
                          setActiveVectorComponent("magnitude");
                          if (field.overlay) setOverlay(field.overlay as OverlayMode);
                          setProbeTarget(null);
                        }}
                        title={`${field.field} ${field.location} ${field.kind}, ${field.tupleCount} tuple${field.tupleCount === 1 ? "" : "s"}`}
                      >
                        <span className={`field-location ${field.location}`}>{field.location}</span>
                        <strong>{field.field}</strong>
                        <small>
                          {field.kind === "vector" ? "mag" : "scalar"} · {field.unit.symbol} · n={field.tupleCount}
                        </small>
                        <em>
                          {formatNumber(field.min, 3)}-{formatNumber(field.max, 3)}
                        </em>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <small className="field-filter-empty">No fields match the current filter.</small>
              )}
            </>
          ) : null}
          {activeResultField?.kind === "vector" ? (
            <label className="field-component-control">
              Vector component
              <select
                aria-label="Vector component"
                value={activeVectorComponent}
                onChange={(event) => {
                  setActiveVectorComponent(event.target.value as ResultVectorComponent);
                  setProbeTarget(null);
                }}
              >
                {vectorComponentOptions.map((component) => (
                  <option key={component.id} value={component.id}>
                    {component.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {loadedResult ? (
            <label className="field-component-control">
              Color map
              <select aria-label="Result color map" value={resultColorMap} onChange={(event) => setResultColorMap(event.target.value as ResultColorMap)}>
                {resultColorMapOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {resultSnapshots.length > 0 ? (
            <div className="time-controls" aria-label="Result timestep controls">
              <button
                type="button"
                onClick={() => stepResultTimeline(-1)}
                disabled={activeResultIndex === 0}
                title="Previous result timestep"
                aria-label="Previous result timestep"
              >
                <SkipBack size={14} />
              </button>
              <button type="button" onClick={() => setIsPlayingResults((value) => !value)} title={isPlayingResults ? "Pause result playback" : "Play result timesteps"}>
                {isPlayingResults ? <Pause size={14} /> : <Play size={14} />}
                <span>{isPlayingResults ? "Pause" : "Play"}</span>
              </button>
              <button
                type="button"
                onClick={() => stepResultTimeline(1)}
                disabled={activeResultIndex >= resultSnapshots.length - 1}
                title="Next result timestep"
                aria-label="Next result timestep"
              >
                <SkipForward size={14} />
              </button>
              <input
                aria-label="Result timestep"
                aria-valuetext={activeSnapshot ? `${formatSnapshotTime(activeSnapshot.time)} ${activeSnapshot.label}` : "No result timestep"}
                type="range"
                min={0}
                max={Math.max(resultSnapshots.length - 1, 0)}
                step={1}
                value={activeResultIndex}
                onChange={(event) => {
                  setIsPlayingResults(false);
                  setActiveResultIndex(Number(event.target.value));
                }}
              />
              <strong title={activeSnapshot?.label}>{activeSnapshot ? formatSnapshotTime(activeSnapshot.time) : "t0"}</strong>
              <div className="playback-options">
                <label>
                  Speed
                  <select
                    aria-label="Result playback speed"
                    value={resultPlaybackRate}
                    onChange={(event) => setResultPlaybackRate(Number(event.target.value) as (typeof resultPlaybackRates)[number])}
                  >
                    {resultPlaybackRates.map((rate) => (
                      <option key={rate} value={rate}>
                        {rate}x
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className={resultPlaybackLoop ? "active" : ""}
                  aria-pressed={resultPlaybackLoop}
                  aria-label="Loop result playback"
                  title={resultPlaybackLoop ? "Loop result playback" : "Hold on final result timestep"}
                  onClick={() => setResultPlaybackLoop((value) => !value)}
                >
                  <Repeat size={13} />
                  <span>{resultPlaybackLoop ? "Loop" : "Hold"}</span>
                </button>
              </div>
            </div>
          ) : null}
          {resultTimelineStats.length > 1 ? (
            <div className="field-timeline" aria-label="Result field timeline">
              <div className="field-timeline-header">
                <strong>Trend</strong>
                <span title={activeSnapshot?.label}>
                  {activeTimelineSample?.field
                    ? `${activeTimelineSample.field} mean ${formatNumber(activeTimelineSample.mean ?? Number.NaN, 3)}${formatUnitSuffix(activeTimelineSample.unit)}`
                    : "field unavailable"}
                </span>
                <button type="button" onClick={exportResultTimelineCsv} title="Export active field timeline CSV" aria-label="Export active field timeline CSV">
                  <Download size={12} />
                </button>
              </div>
              <div className="field-timeline-track">
                {resultTimelineStats.map((sample, index) => {
                  const level = timelineLevel(sample.mean, resultTimelineMeanRange.min, resultTimelineMeanRange.max);
                  const unitSuffix = formatUnitSuffix(sample.unit);
                  const sampleStatus = sample.mean === null ? "missing field" : `mean ${formatNumber(sample.mean, 3)}${unitSuffix}, max ${formatNumber(sample.max ?? Number.NaN, 3)}${unitSuffix}`;
                  return (
                    <button
                      key={sample.id}
                      type="button"
                      className={index === activeResultIndex ? "active" : ""}
                      onClick={() => {
                        setIsPlayingResults(false);
                        setActiveResultIndex(index);
                      }}
                      title={`${formatSnapshotTime(sample.time)} ${sample.label}: ${sampleStatus}`}
                    >
                      <span className="timeline-bar" style={{ width: `${level}%` }} />
                      <small>{formatSnapshotTime(sample.time)}</small>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
          {resultFieldCoverage.totalSnapshots > 1 ? (
            <div className="field-coverage" aria-label="Result field coverage">
              <div className="field-coverage-header">
                <strong>Coverage</strong>
                <span>
                  {resultFieldCoverage.presentSnapshots}/{resultFieldCoverage.totalSnapshots} snapshots
                </span>
              </div>
              <small>
                {resultFieldCoverage.missingSnapshots > 0
                  ? `Missing ${resultFieldCoverage.missingSnapshots}: ${resultFieldCoverage.missingLabels.slice(0, 3).join(", ")}${resultFieldCoverage.missingLabels.length > 3 ? "..." : ""}`
                  : "All loaded snapshots contain the active field"}
              </small>
              <small>
                {resultFieldCoverage.fields.length > 0
                  ? `${resultFieldCoverage.fields.join(", ")} · ${resultFieldCoverage.locations.join("/")} · ${resultFieldCoverage.kinds.map(formatFieldValueKind).join("/")} · ${resultFieldCoverage.units.map((unit) => unit.symbol).join("/")}`
                  : "No matching field loaded across the result timeline"}
              </small>
            </div>
          ) : null}
          {fieldHistogram.length > 0 && fieldStats ? (
            <div className="field-histogram" aria-label="Result field histogram">
              <div className="field-histogram-header">
                <strong>Distribution</strong>
                <span>
                  {formatFieldValueKind(fieldStats.kind)} · {fieldStats.location} · {fieldStats.unit.symbol}
                </span>
              </div>
              <div className="field-histogram-bars">
                {fieldHistogram.map((bin, index) => {
                  const height = fieldHistogramMaxCount > 0 ? Math.max(8, (bin.count / fieldHistogramMaxCount) * 100) : 0;
                  return (
                    <span
                      key={`${bin.min}-${bin.max}-${index}`}
                      style={{ height: `${height}%` }}
                      title={`${formatNumber(bin.min, 4)} to ${formatNumber(bin.max, 4)}: ${bin.count}`}
                    />
                  );
                })}
              </div>
              <div className="field-histogram-scale">
                <span>{formatNumber(fieldStats.min, 4)} {fieldStats.unit.symbol}</span>
                <span>{formatNumber(fieldStats.max, 4)} {fieldStats.unit.symbol}</span>
              </div>
            </div>
          ) : null}
          <div className="color-ramp" aria-label="Result color ramp" title={loadedResult ? `${activeColorMap.label} color map` : "Instant overlay color ramp"}>
            <span style={loadedResult ? { background: activeColorMap.gradient } : undefined} />
          </div>
          {fieldStats ? (
            <div className="field-scale" aria-label="Field min max">
              <span>{formatNumber(fieldStats.min, 4)} {fieldStats.unit.symbol}</span>
              <span>{fieldStats.field}</span>
              <span>{formatNumber(fieldStats.max, 4)} {fieldStats.unit.symbol}</span>
            </div>
          ) : null}
          {fieldStats ? (
            <div className="field-stat-grid" aria-label="Field statistics">
              <div>
                <span>Mean</span>
                <strong>{formatNumber(fieldStats.mean, 4)} {fieldStats.unit.symbol}</strong>
              </div>
              <div>
                <span>Std</span>
                <strong>{formatNumber(fieldStats.stdDev, 4)} {fieldStats.unit.symbol}</strong>
              </div>
              <div>
                <span>P50</span>
                <strong>{formatNumber(fieldStats.p50, 4)} {fieldStats.unit.symbol}</strong>
              </div>
              <div>
                <span>P95</span>
                <strong>{formatNumber(fieldStats.p95, 4)} {fieldStats.unit.symbol}</strong>
              </div>
            </div>
          ) : null}
          {activeResultFieldWarning ? (
            <div className="result-field-warning" aria-label="Result field warning" role="status">
              {activeResultFieldWarning}
            </div>
          ) : null}
          <small className="field-source">
            {loadedResult && activeResultField && fieldStats
              ? `Using ${fieldStats.field} ${formatFieldValueKind(fieldStats.kind)} from ${loadedResult.sourceName ?? "loaded result"} (${fieldStats.location} data, ${fieldStats.unit.symbol})`
              : loadedResult && activeResultField
                ? `Pinned ${activeResultField.field} (${activeResultField.location} ${activeResultField.kind}) is unavailable in ${loadedResult.sourceName ?? "this timestep"}`
                : loadedResult && fieldAvailable(loadedResult, project.visualization.overlay)
                  ? `Using ${fieldStats?.field ?? fieldNameForOverlay(project.visualization.overlay)} from ${loadedResult.sourceName ?? "loaded result"} (${fieldStats?.location ?? "point"} data${fieldStats ? `, ${fieldStats.unit.symbol}` : ""})`
                  : "Using instant 1D result unless a matching VTK/VTU field is loaded"}
          </small>
          {probeSample ? (
            <dl className="probe-readout" aria-label="Probe sample">
              <div>
                <dt>Probe</dt>
                <dd>
                  {probeSample.field} @{" "}
                  {probeTarget?.kind === "surface" && probeSample.location === "point"
                    ? "surface"
                    : `${probeSample.location === "cell" ? "c" : "p"}${probeSample.pointIndex}`}
                </dd>
              </div>
              <div>
                <dt>Value</dt>
                <dd>{formatNumber(probeSample.value, 4)} {probeSample.unit.symbol}</dd>
              </div>
              <div>
                <dt>{probeTarget?.kind === "surface" ? "Surface XYZ" : "Point XYZ"}</dt>
                <dd>
                  {formatNumber(probeSample.point[0], 6)}, {formatNumber(probeSample.point[1], 6)}, {formatNumber(probeSample.point[2], 6)}
                </dd>
              </div>
            </dl>
          ) : null}
        </section>

        <section className={`dock-panel sweep-tray ${activeDockPanel === "sweep" ? "active" : ""}`} hidden={activeDockPanel !== "sweep"}>
          <div className="panel-title">
            <FlaskConical size={16} />
            Sweep: inlet flow rate
          </div>
          <button className="primary-action" onClick={() => runSweep()}>
            <Beaker size={16} />
            Run sweep
          </button>
          <div className="sweep-runs">
            {sweepRuns.map((run) => {
              const firstEdge = Object.values(run.result.edgeResults)[0];
              return (
                <button key={run.index} className={firstEdge?.cavitationRisk ? "risk" : ""}>
                  <span>{formatNumber(run.value, 4)}</span>
                  <small>{firstEdge ? `${formatNumber(firstEdge.velocity)} m/s` : "no edge"}</small>
                </button>
              );
            })}
          </div>
        </section>

        <section className={`dock-panel metrics-panel ${activeDockPanel === "metrics" ? "active" : ""}`} hidden={activeDockPanel !== "metrics"}>
          <div className="panel-title">
            <CircleGauge size={16} />
            Metrics
          </div>
          <dl className="metric-grid">
            <div>
              <dt>Total flow</dt>
              <dd>{formatNumber(totals.flow, 4)} m3/s</dd>
            </div>
            <div>
              <dt>Pressure loss</dt>
              <dd>{formatNumber(totals.pressureDrop / 1000)} kPa</dd>
            </div>
            <div>
              <dt>Max Reynolds</dt>
              <dd>{formatNumber(totals.maxRe)}</dd>
            </div>
            <div>
              <dt>Cavitation</dt>
              <dd>{totals.cavitation}</dd>
            </div>
          </dl>
        </section>

        <MeshQualityPanel
          hidden={activeDockPanel !== "mesh"}
          meshQuality={meshQuality}
          solver={jobRecord?.solver ?? project.solver.tier}
          reviewedGeometry={project.solver.reviewedGeometry}
          onReviewedGeometryChange={(reviewedGeometry) => updateSolverSettings({ reviewedGeometry })}
        />

        <section className={`dock-panel diagnostics-panel ${activeDockPanel === "diagnostics" ? "active" : ""}`} id="solver-diagnostics-panel" hidden={activeDockPanel !== "diagnostics"}>
          <div className="panel-title">
            <Activity size={16} />
            Solver diagnostics
          </div>
          {solverLogSummary ? (
            <div className="solver-diagnostics" aria-label="Solver diagnostics panel">
              <div className="diagnostic-kpis">
                <div>
                  <span>Lines</span>
                  <strong>{solverLogSummary.lineCount}</strong>
                </div>
                <div>
                  <span>Time</span>
                  <strong>{formatResidual(solverLogSummary.latestTime)}</strong>
                </div>
                <div>
                  <span>Iter</span>
                  <strong>{solverLogSummary.latestIteration !== undefined ? formatNumber(solverLogSummary.latestIteration, 0) : "n/a"}</strong>
                </div>
              </div>
              {solverResidualRows.length > 0 ? (
                <div className="diagnostic-residuals" aria-label="Solver residual convergence">
                  {solverResidualRows.map(([field, residual]) => {
                    const ratio = residualRatio(residual.initial, residual.final);
                    const level = ratio === null ? 0 : Math.max(5, Math.min(100, (1 - Math.min(ratio, 1)) * 100));
                    return (
                      <div key={field}>
                        <span>{field}</span>
                        <strong>{formatResidual(residual.final)}</strong>
                        <i style={{ width: `${level}%` }} />
                        <small>{ratio === null ? "ratio n/a" : `${formatResidual(ratio)} final/initial`}</small>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <small>No parsed residuals yet.</small>
              )}
              {diagnosticDockRows.length > 0 ? (
                <div className="diagnostic-tables" aria-label="Solver diagnostic tables">
                  {diagnosticDockRows.map((summary) => (
                    <div key={`${summary.path}-${summary.kind}`} title={summary.path}>
                      <strong>{summary.kind}</strong>
                      <span>{"rowCount" in summary ? `${summary.rowCount} rows` : `${summary.lineCount} lines`}</span>
                      {"latest" in summary ? (
                        <small>{diagnosticLatestEntries(summary).map(([key, value]) => `${key} ${formatResidual(value)}`).join(" · ")}</small>
                      ) : (
                        <small>{summary.excerpts.slice(0, 2).join(" · ")}</small>
                      )}
                    </div>
                  ))}
                </div>
              ) : null}
              <PatchMetricsPanel patchMetrics={patchMetrics} solver={jobRecord?.solver ?? project.solver.tier} />
              {diagnosticWarningRows.length > 0 ? (
                <div className="diagnostic-log-flags" aria-label="Solver log warnings">
                  {diagnosticWarningRows.map((message) => <small key={message}>{message}</small>)}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="ok-line">No solver diagnostics loaded.</p>
          )}
        </section>

        <section className={`dock-panel warnings-panel ${activeDockPanel === "warnings" ? "active" : ""}`} id="warnings-panel" hidden={activeDockPanel !== "warnings"}>
          <div className="panel-title">
            <Gauge size={16} />
            Warnings
          </div>
          {activeWarnings.length > 0 ? (
            activeWarnings.slice(0, 4).map((warning) => <p key={warning.id}>{warning.message}</p>)
          ) : (
            <p className="ok-line">No blocking warnings.</p>
          )}
        </section>
      </footer>
    </main>
  );
}

function meshQaStatusLabel(meshQuality: MeshQualitySummary | null, solver: SolverTier, productionEligible = true) {
  if (solver !== "openfoam") return "OpenFOAM only";
  if (!meshQuality) return "Waiting";
  if (meshQuality.productionReady) return productionEligible ? "Ready" : "Needs tags";
  if (meshQuality.openfoam.status === "passed") return "Native passed";
  if (meshQuality.openfoam.status === "blocked") return "Blocked";
  if (meshQuality.openfoam.status === "unavailable") return "Unavailable";
  return meshQuality.openfoam.status;
}

function meshQaStatusClass(meshQuality: MeshQualitySummary | null, solver: SolverTier, productionEligible = true) {
  if (solver !== "openfoam" || !meshQuality) return "pending";
  if (meshQuality.productionReady) return productionEligible ? "ready" : "blocked";
  if (meshQuality.openfoam.status === "passed") return "native-passed";
  if (meshQuality.openfoam.status === "blocked") return "blocked";
  return "pending";
}

function formatMeshMetric(value: number | null | undefined, digits = 3) {
  return typeof value === "number" && Number.isFinite(value) ? formatNumber(value, digits) : "n/a";
}

function patchMetricStatusLabel(patchMetrics: PatchMetrics | null, solver: SolverTier) {
  if (solver !== "openfoam") return "OpenFOAM only";
  if (!patchMetrics) return "No metrics";
  if (patchMetrics.status === "complete") return "Complete";
  if (patchMetrics.status === "partial") return "Partial";
  if (patchMetrics.status === "unparsed") return "Unparsed";
  return "Missing";
}

function formatVectorMetric(vector: { x: number; y: number; z: number }, unit: string) {
  return `${formatNumber(vector.x, 3)}, ${formatNumber(vector.y, 3)}, ${formatNumber(vector.z, 3)} ${unit}`;
}

function PatchMetricsPanel({ patchMetrics, solver }: { patchMetrics: PatchMetrics | null; solver: SolverTier }) {
  const patches = Object.values(patchMetrics?.patches ?? {});
  const wallShearPatches = patches.filter((patch) => patch.wallShear);
  const status = patchMetricStatusLabel(patchMetrics, solver);
  const statusClass = patchMetrics?.status === "complete" ? "ready" : patchMetrics?.status === "partial" ? "partial" : "blocked";
  return (
    <div className="patch-metrics" aria-label="OpenFOAM patch metrics">
      <div className="patch-metrics-header">
        <strong>Patch Metrics</strong>
        <span className={`patch-metrics-badge ${statusClass}`}>{status}</span>
      </div>
      {solver !== "openfoam" ? (
        <small>Patch metrics are available for OpenFOAM post-processing outputs.</small>
      ) : patchMetrics ? (
        <>
          {patchMetrics.flowBalance ? (
            <div className="patch-metric-card" aria-label="Patch flow balance">
              <span>Flow balance</span>
              <strong>
                in {formatNumber(patchMetrics.flowBalance.inletFlow, 4)} / out {formatNumber(patchMetrics.flowBalance.outletFlow, 4)} {patchMetrics.flowBalance.unit}
              </strong>
              <small>
                imbalance {formatNumber(patchMetrics.flowBalance.imbalance, 4)} {patchMetrics.flowBalance.unit} ·{" "}
                {formatNumber(patchMetrics.flowBalance.relativeImbalance * 100, 3)}%
              </small>
            </div>
          ) : (
            <small>Flow balance unavailable.</small>
          )}
          {patchMetrics.pressureDrops.length > 0 ? (
            <div className="patch-metric-list" aria-label="Patch pressure drops">
              {patchMetrics.pressureDrops.slice(0, 3).map((drop) => (
                <div key={`${drop.fromPatch}-${drop.toPatch}`}>
                  <span>
                    {drop.fromPatch} {"->"} {drop.toPatch}
                  </span>
                  <strong>{formatNumber(drop.deltaP / 1000, 4)} kPa</strong>
                </div>
              ))}
            </div>
          ) : null}
          {wallShearPatches.length > 0 ? (
            <div className="patch-metric-list" aria-label="Wall shear metrics">
              {wallShearPatches.slice(0, 3).map((patch) => (
                <div key={`${patch.patchName}-wall-shear`}>
                  <span>{patch.patchName} shear</span>
                  <strong>
                    mean {formatNumber(patch.wallShear?.mean ?? Number.NaN, 4)} {patch.wallShear?.unit ?? "Pa"}
                  </strong>
                  <small>
                    min {formatNumber(patch.wallShear?.min ?? Number.NaN, 4)} · max {formatNumber(patch.wallShear?.max ?? Number.NaN, 4)}
                  </small>
                </div>
              ))}
            </div>
          ) : null}
          {patchMetrics.forces.length > 0 ? (
            <div className="patch-metric-list" aria-label="Integrated force metrics">
              {patchMetrics.forces.slice(0, 2).map((force) => (
                <div key={`${force.patchName}-${force.path}`}>
                  <span>{force.patchName} force</span>
                  <strong>|F| {formatNumber(force.forceMagnitude, 4)} N</strong>
                  <small>F {formatVectorMetric(force.force, "N")} · M {formatVectorMetric(force.moment, "N m")}</small>
                </div>
              ))}
            </div>
          ) : null}
          {patchMetrics.pressureProbes.length > 0 ? (
            <div className="patch-metric-card" aria-label="Pressure probe metrics">
              <span>Pressure probes</span>
              <strong>span {formatNumber(patchMetrics.pressureProbes[0].pressureSpan / 1000, 4)} kPa</strong>
              <small>
                {patchMetrics.pressureProbes[0].sampleCount} samples · {patchMetrics.pressureProbes[0].path}
              </small>
            </div>
          ) : null}
          {patchMetrics.warnings.length > 0 ? (
            <div className="patch-metric-warnings" aria-label="Patch metric warnings">
              {patchMetrics.warnings.slice(0, 4).map((warning) => (
                <small key={warning}>{warning}</small>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <small>Queue an OpenFOAM job to collect patch flow, pressure, shear, and force metrics.</small>
      )}
    </div>
  );
}

function MeshQualityPanel({
  meshQuality,
  solver,
  reviewedGeometry,
  onReviewedGeometryChange,
  hidden = false
}: {
  meshQuality: MeshQualitySummary | null;
  solver: SolverTier;
  reviewedGeometry?: ReviewedGeometrySource;
  onReviewedGeometryChange: (geometry: ReviewedGeometrySource | undefined) => void;
  hidden?: boolean;
}) {
  const metrics = meshQuality?.openfoam.qualityMetrics;
  const commandRuns = meshQuality?.openfoam.commandRuns ?? [];
  const blockers = meshQuality?.openfoam.blockingReasons ?? [];
  const yPlus = meshQuality?.openfoam.yPlusEvidence;
  const artifacts = meshQuality?.artifacts ?? [];
  const existingArtifacts = artifacts.filter((artifact) => artifact.exists).map((artifact) => artifact.path);
  const geometry = reviewedGeometry ?? defaultReviewedGeometry();
  const surfaces = surfacesForGeometry(geometry);
  const [selectedSurfaceId, setSelectedSurfaceId] = useState<string | null>(null);
  const [pastedSurfaceName, setPastedSurfaceName] = useState("");
  const [pastedStlText, setPastedStlText] = useState("");
  const selectedSurface = surfaces.find((surface) => surface.id === selectedSurfaceId) ?? surfaces[0] ?? null;
  const selectedParsedSurface = useMemo(
    () => (selectedSurface?.stlText ? parseAsciiStlGeometry(selectedSurface.stlText) : null),
    [selectedSurface?.id, selectedSurface?.stlText]
  );
  const legacyParsedGeometry = useMemo(() => (geometry.stlText ? parseAsciiStlGeometry(geometry.stlText) : null), [geometry.stlText]);
  const metadata = selectedSurface ? selectedParsedSurface ?? selectedSurface.metadata ?? null : legacyParsedGeometry ?? geometry.metadata ?? null;
  const previewGeometry = selectedSurface ? selectedParsedSurface : legacyParsedGeometry;
  const boundaryTags = normalizedBoundaryTags(geometry.boundaryTags);
  const boundaryTagsComplete = requiredBoundaryTagsComplete(geometry.boundaryTags);
  const surfaceCoverageComplete = surfaces.length > 0 ? requiredReviewedSurfaceCoverageComplete(surfaces) : boundaryTagsComplete;
  const duplicatedPatchNames = duplicatePatchNames(surfaces);
  const productionEligible = hasRequiredReviewedGeometry(geometry);
  const [geometryMessage, setGeometryMessage] = useState<string | null>(null);
  const triSurfaceState =
    surfaces.length > 0
      ? surfaces.every((surface) => surface.cadReviewed)
        ? `User reviewed surface${surfaces.length === 1 ? "" : "s"}`
        : `${surfaces.length} imported surface${surfaces.length === 1 ? "" : "s"}`
      : geometry.sourceType === "flowlab-generated"
        ? "Generated starter"
        : geometry.cadReviewed
          ? "User reviewed"
          : "Imported, review pending";

  function commitSurfaces(nextSurfaces: ReviewedGeometrySurface[], message?: string) {
    onReviewedGeometryChange(synchronizeGeometrySurfaces(geometry, nextSurfaces));
    if (message) setGeometryMessage(message);
  }

  async function importReviewedStls(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    const existingPatchNames = new Set(surfaces.map((surface) => surface.patchName));
    const imported: ReviewedGeometrySurface[] = [];
    for (const file of files) {
      const text = await file.text();
      const error = stlSanityError(file.name, text);
      if (error) {
        setGeometryMessage(`${file.name}: ${error}`);
        continue;
      }
      const parsed = parseAsciiStlGeometry(text);
      const surfaceName = surfaceNameFromFile(file.name);
      const role = roleFromSurfaceName(surfaceName);
      const patchName = uniquePatchName(`${role}_${safePatchName(surfaceName, role)}`, existingPatchNames);
      existingPatchNames.add(patchName);
      imported.push({
        id: makeSurfaceId(),
        surfaceName,
        role,
        patchName,
        sourceType: "uploaded-stl",
        cadReviewed: false,
        reviewedAt: null,
        notes: "",
        boundaryCondition: defaultBoundaryConditionForRole(role),
        stlText: text,
        stlPath: file.name,
        metadata: metadataFromParsedStl(parsed)
      });
    }
    if (!imported.length) return;
    setSelectedSurfaceId(imported[0].id);
    commitSurfaces([...surfaces, ...imported], `${imported.length} STL surface${imported.length === 1 ? "" : "s"} imported. Mark each reviewed after CAD/geometry review.`);
  }

  function addPastedSurface() {
    const name = pastedSurfaceName.trim() || "pasted surface";
    const filename = `${safePatchName(name, "pasted_surface")}.stl`;
    const error = stlSanityError(filename, pastedStlText);
    if (error) {
      setGeometryMessage(error);
      return;
    }
    const parsed = parseAsciiStlGeometry(pastedStlText);
    const role = roleFromSurfaceName(name);
    const patchName = uniquePatchName(`${role}_${safePatchName(name, role)}`, surfaces.map((surface) => surface.patchName));
    const surface: ReviewedGeometrySurface = {
      id: makeSurfaceId(),
      surfaceName: name,
      role,
      patchName,
      sourceType: "uploaded-stl",
      cadReviewed: false,
      reviewedAt: null,
      notes: "",
      boundaryCondition: defaultBoundaryConditionForRole(role),
      stlText: pastedStlText,
      stlPath: filename,
      metadata: metadataFromParsedStl(parsed)
    };
    setSelectedSurfaceId(surface.id);
    setPastedSurfaceName("");
    setPastedStlText("");
    commitSurfaces([...surfaces, surface], `${name} pasted as a reviewed STL candidate.`);
  }

  function updateSurface(surfaceId: string, patch: Partial<ReviewedGeometrySurface>) {
    const nextSurfaces = surfaces.map((surface) => (surface.id === surfaceId ? { ...surface, ...patch } : surface));
    commitSurfaces(nextSurfaces);
  }

  function setSurfaceRole(surface: ReviewedGeometrySurface, role: ReviewedGeometryBoundaryRole) {
    updateSurface(surface.id, {
      role,
      boundaryCondition: boundaryConditionTypeAllowedForRole(surface.boundaryCondition?.type, role) ? surface.boundaryCondition : defaultBoundaryConditionForRole(role)
    });
  }

  function setSurfaceBoundaryConditionType(surface: ReviewedGeometrySurface, type: ReviewedSurfaceBoundaryConditionType) {
    updateSurface(surface.id, {
      boundaryCondition: defaultBoundaryConditionForType(type)
    });
  }

  function updateSurfaceBoundaryCondition(surface: ReviewedGeometrySurface, patch: Partial<ReviewedSurfaceBoundaryCondition>) {
    updateSurface(surface.id, {
      boundaryCondition: {
        ...(surface.boundaryCondition ?? defaultBoundaryConditionForRole(surface.role)),
        ...patch
      }
    });
  }

  function setSurfaceReviewed(surfaceId: string, cadReviewed: boolean) {
    updateSurface(surfaceId, {
      cadReviewed,
      reviewedAt: cadReviewed ? new Date().toISOString() : null
    });
    setGeometryMessage(cadReviewed ? "Surface marked reviewed. Inlet, outlet, and wall surfaces are required for production readiness." : "Surface review flag cleared.");
  }

  function removeSurface(surfaceId: string) {
    const nextSurfaces = surfaces.filter((surface) => surface.id !== surfaceId);
    setSelectedSurfaceId(nextSurfaces[0]?.id ?? null);
    commitSurfaces(nextSurfaces, "Surface removed.");
  }

  function setCadReviewed(cadReviewed: boolean) {
    const now = cadReviewed ? new Date().toISOString() : null;
    onReviewedGeometryChange({
      ...geometry,
      cadReviewed,
      reviewedAt: now
    });
    setGeometryMessage(cadReviewed ? "Reviewed geometry flag set. Native mesh evidence is still required." : "Reviewed geometry flag cleared.");
  }

  function setBoundaryTag(role: ReviewedGeometryBoundaryRole, patchName: string) {
    const nextTags = boundaryTags.map((tag) => (tag.role === role ? { ...tag, patchName } : tag));
    onReviewedGeometryChange({
      ...geometry,
      boundaryTags: nextTags
    });
  }

  return (
    <section className="dock-panel mesh-qa-panel" aria-label="Mesh QA panel" hidden={hidden}>
      <div className="panel-title">
        <SlidersHorizontal size={16} />
        Mesh QA
        <span className={`mesh-qa-badge ${meshQaStatusClass(meshQuality, solver, productionEligible)}`}>{meshQaStatusLabel(meshQuality, solver, productionEligible)}</span>
      </div>
      <div className="mesh-reviewed-geometry" aria-label="Reviewed geometry controls">
        <div className="mesh-qa-status-row">
          <span>triSurface</span>
          <strong>{triSurfaceState}</strong>
          <small>
            {reviewedGeometryLabel(geometry)}
            {surfaces.length > 0 ? ` · ${surfaces.filter((surface) => surface.cadReviewed).length}/${surfaces.length} reviewed` : geometry.stlPath ? ` · ${geometry.stlPath}` : ""}
          </small>
        </div>
        <label className="mesh-file-control">
          <Upload size={14} />
          Import ASCII STL surfaces
          <input data-testid="reviewed-stl-file" type="file" accept=".stl" multiple onChange={importReviewedStls} />
        </label>
        <div className="mesh-paste-control" aria-label="Pasted STL surface controls">
          <input
            aria-label="Pasted surface name"
            value={pastedSurfaceName}
            placeholder="surface name, e.g. inlet cap"
            onChange={(event) => setPastedSurfaceName(event.target.value)}
          />
          <textarea
            aria-label="Pasted STL text"
            value={pastedStlText}
            rows={3}
            placeholder="solid inlet..."
            onChange={(event) => setPastedStlText(event.target.value)}
          />
          <button type="button" onClick={addPastedSurface} disabled={!pastedStlText.trim()}>
            Add pasted STL
          </button>
        </div>
        {surfaces.length > 0 ? (
          <div className="mesh-surface-table" aria-label="Reviewed STL surface table">
            <div className="mesh-surface-head">
              <span>Surface</span>
              <span>Role</span>
              <span>Patch</span>
              <span>BC</span>
              <span>QA</span>
            </div>
            {surfaces.map((surface) => {
              const isSelected = selectedSurface?.id === surface.id;
              const issue = patchNameIssue(surface.patchName) || (duplicatedPatchNames.has(surface.patchName.trim()) ? "Duplicate patch name." : null);
              const surfaceMetadata = surface.metadata;
              const boundaryCondition = boundaryConditionTypeAllowedForRole(surface.boundaryCondition?.type, surface.role) ? surface.boundaryCondition : undefined;
              const boundaryStatus = boundaryConditionStatus(surface);
              return (
                <div key={surface.id} className={`mesh-surface-row ${isSelected ? "selected" : ""}`}>
                  <button type="button" aria-label={`Preview ${surface.surfaceName}`} onClick={() => setSelectedSurfaceId(surface.id)}>
                    <strong>{surface.surfaceName}</strong>
                    <small>{surface.stlPath ?? surface.sourceType}</small>
                  </button>
                  <select
                    aria-label={`${surface.surfaceName} role`}
                    value={surface.role}
                    onChange={(event) => setSurfaceRole(surface, event.target.value as ReviewedGeometryBoundaryRole)}
                  >
                    {boundaryTagRoles.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                  <label>
                    <input
                      aria-label={`${surface.surfaceName} patch name`}
                      value={surface.patchName}
                      onChange={(event) => updateSurface(surface.id, { patchName: event.target.value })}
                    />
                    {issue ? <small>{issue}</small> : null}
                  </label>
                  <div className="mesh-surface-bc">
                    <select
                      aria-label={`${surface.surfaceName} boundary condition`}
                      value={boundaryCondition?.type ?? ""}
                      onChange={(event) => {
                        const value = event.target.value as ReviewedSurfaceBoundaryConditionType | "";
                        if (!value) updateSurface(surface.id, { boundaryCondition: undefined });
                        else setSurfaceBoundaryConditionType(surface, value);
                      }}
                    >
                      <option value="">Unset BC</option>
                      {boundaryConditionOptionsByRole[surface.role].map((option) => (
                        <option key={option.type} value={option.type}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <span className={`mesh-bc-status ${boundaryStatus.className}`}>{boundaryStatus.label}</span>
                    {boundaryCondition?.type === "velocity-inlet" ? (
                      <div className="mesh-bc-vector">
                        {(["x", "y", "z"] as const).map((axis) => (
                          <label key={axis}>
                            <span>U{axis}</span>
                            <input
                              aria-label={`${surface.surfaceName} velocity ${axis}`}
                              type="number"
                              step="0.1"
                              value={boundaryCondition.velocity?.[axis] ?? 0}
                              onChange={(event) =>
                                updateSurfaceBoundaryCondition(surface, {
                                  velocity: {
                                    x: boundaryCondition.velocity?.x ?? 0,
                                    y: boundaryCondition.velocity?.y ?? 0,
                                    z: boundaryCondition.velocity?.z ?? 0,
                                    [axis]: Number(event.target.value)
                                  }
                                })
                              }
                            />
                          </label>
                        ))}
                      </div>
                    ) : null}
                    {boundaryCondition?.type === "mass-flow-inlet" ? (
                      <label className="mesh-bc-scalar">
                        <span>kg/s</span>
                        <input
                          aria-label={`${surface.surfaceName} mass flow rate`}
                          type="number"
                          step="0.1"
                          value={boundaryCondition.massFlowRate ?? 0}
                          onChange={(event) => updateSurfaceBoundaryCondition(surface, { massFlowRate: Number(event.target.value) })}
                        />
                      </label>
                    ) : null}
                    {(boundaryCondition?.type === "pressure-inlet" || boundaryCondition?.type === "pressure-outlet") ? (
                      <label className="mesh-bc-scalar">
                        <span>Pa</span>
                        <input
                          aria-label={`${surface.surfaceName} pressure`}
                          type="number"
                          step="100"
                          value={boundaryCondition.pressure ?? 0}
                          onChange={(event) => updateSurfaceBoundaryCondition(surface, { pressure: Number(event.target.value) })}
                        />
                      </label>
                    ) : null}
                    {boundaryCondition?.type === "rough-wall" ? (
                      <label className="mesh-bc-scalar">
                        <span>eps</span>
                        <input
                          aria-label={`${surface.surfaceName} roughness`}
                          type="number"
                          step="0.0001"
                          value={boundaryCondition.roughness ?? 0}
                          onChange={(event) => updateSurfaceBoundaryCondition(surface, { roughness: Number(event.target.value) })}
                        />
                      </label>
                    ) : null}
                    {boundaryCondition?.type === "heat-flux-wall" ? (
                      <label className="mesh-bc-scalar">
                        <span>W/m2</span>
                        <input
                          aria-label={`${surface.surfaceName} heat flux`}
                          type="number"
                          step="10"
                          value={boundaryCondition.heatFlux ?? 0}
                          onChange={(event) => updateSurfaceBoundaryCondition(surface, { heatFlux: Number(event.target.value) })}
                        />
                      </label>
                    ) : null}
                    {boundaryCondition?.type === "temperature-wall" ? (
                      <label className="mesh-bc-scalar">
                        <span>K</span>
                        <input
                          aria-label={`${surface.surfaceName} temperature`}
                          type="number"
                          step="1"
                          value={boundaryCondition.temperature ?? 293.15}
                          onChange={(event) => updateSurfaceBoundaryCondition(surface, { temperature: Number(event.target.value) })}
                        />
                      </label>
                    ) : null}
                  </div>
                  <div className="mesh-surface-qa">
                    <span>{surfaceMetadata?.triangleCount.toLocaleString() ?? "n/a"} tri</span>
                    <span>{surfaceMetadata?.watertightStatus ?? "unknown"}</span>
                    <label>
                      <input
                        aria-label={`${surface.surfaceName} reviewed`}
                        type="checkbox"
                        checked={surface.cadReviewed}
                        onChange={(event) => setSurfaceReviewed(surface.id, event.target.checked)}
                      />
                      reviewed
                    </label>
                  </div>
                  <label className="mesh-surface-notes">
                    <span>Notes</span>
                    <input
                      aria-label={`${surface.surfaceName} notes`}
                      value={surface.notes ?? ""}
                      placeholder="reviewer/source notes"
                      onChange={(event) => updateSurface(surface.id, { notes: event.target.value })}
                    />
                  </label>
                  <button type="button" className="mesh-surface-remove" aria-label={`Remove ${surface.surfaceName}`} onClick={() => removeSurface(surface.id)}>
                    <Trash2 size={13} />
                  </button>
                </div>
              );
            })}
            <small className={surfaceCoverageComplete && duplicatedPatchNames.size === 0 ? "mesh-stl-validation ok" : "mesh-stl-validation"}>
              {surfaceCoverageComplete && duplicatedPatchNames.size === 0
                ? "Reviewed inlet, outlet, and wall surfaces are ready for native mesh evidence."
                : "Reviewed inlet, outlet, and wall surfaces with unique OpenFOAM patch names are required."}
            </small>
          </div>
        ) : null}
        {metadata ? (
          <div className="mesh-stl-metadata" aria-label="STL metadata">
            <div className="mesh-stl-preview" aria-label="STL preview">
              <svg viewBox="0 0 180 90" role="img" aria-label="Imported STL bounds preview">
                <rect x="7" y="7" width="166" height="76" rx="6" />
                {metadata.bounds && previewGeometry
                  ? previewGeometry.previewTriangles.map((triangle, index) => {
                      const points = triangle.map((vertex) => previewPoint(vertex, metadata.bounds!));
                      return (
                        <polygon
                          key={`${index}-${points[0].x}-${points[0].y}`}
                          points={points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")}
                        />
                      );
                    })
                  : null}
              </svg>
            </div>
            <div className="mesh-stl-stats">
              <div>
                <span>Triangles</span>
                <strong>{metadata.triangleCount.toLocaleString()}</strong>
              </div>
              <div>
                <span>Watertight-ish</span>
                <strong>{metadata.watertightStatus}</strong>
              </div>
              <div>
                <span>Open edges</span>
                <strong>{metadata.openEdgeCount.toLocaleString()}</strong>
              </div>
              <div>
                <span>ASCII</span>
                <strong>{metadata.asciiValid ? "valid" : "invalid"}</strong>
              </div>
            </div>
            <div className="mesh-stl-bounds">
              <small>X {formatBoundsAxis(metadata.bounds?.min.x, metadata.bounds?.max.x)}</small>
              <small>Y {formatBoundsAxis(metadata.bounds?.min.y, metadata.bounds?.max.y)}</small>
              <small>Z {formatBoundsAxis(metadata.bounds?.min.z, metadata.bounds?.max.z)}</small>
            </div>
            <small className={metadata.watertightStatus === "closed" ? "mesh-stl-validation ok" : "mesh-stl-validation"}>
              {metadata.validation.slice(0, 2).join(" ")}
            </small>
          </div>
        ) : null}
        {surfaces.length === 0 ? (
          <>
            <div className="mesh-boundary-tags" aria-label="Boundary tag controls">
              <div className="mesh-boundary-heading">
                <span>Boundary tags</span>
                <strong>{boundaryTagsComplete ? "ready" : "inlet/outlet/wall required"}</strong>
              </div>
              {boundaryTags.map((tag) => {
                const issue = patchNameIssue(tag.patchName);
                return (
                  <label key={tag.role}>
                    <span>{tag.role}</span>
                    <input
                      aria-label={`${tag.role} patch name`}
                      value={tag.patchName}
                      placeholder={`${tag.role}Patch`}
                      disabled={geometry.sourceType === "flowlab-generated"}
                      onChange={(event) => setBoundaryTag(tag.role, event.target.value)}
                    />
                    {issue ? <small>{issue}</small> : null}
                  </label>
                );
              })}
            </div>
            <label className="mesh-review-toggle">
              <input
                type="checkbox"
                checked={geometry.cadReviewed}
                disabled={geometry.sourceType === "flowlab-generated"}
                onChange={(event) => setCadReviewed(event.target.checked)}
              />
              Reviewed geometry required for production readiness
            </label>
            <label className="mesh-review-notes">
              Review notes
              <textarea
                value={geometry.reviewNotes ?? ""}
                rows={2}
                placeholder="Reviewer, source CAD, assumptions"
                onChange={(event) => onReviewedGeometryChange({ ...geometry, reviewNotes: event.target.value })}
              />
            </label>
            {geometry.reviewedAt ? <small className="mesh-reviewed-at">Reviewed at {geometry.reviewedAt}</small> : null}
          </>
        ) : selectedSurface?.reviewedAt ? (
          <small className="mesh-reviewed-at">Selected surface reviewed at {selectedSurface.reviewedAt}</small>
        ) : null}
        {geometryMessage ? <small className="mesh-geometry-message">{geometryMessage}</small> : null}
      </div>
      {solver !== "openfoam" ? (
        <p className="ok-line">Native mesh QA is currently tracked for OpenFOAM jobs.</p>
      ) : meshQuality ? (
        <div className="mesh-qa-content">
          <div className="mesh-qa-status-row">
            <span>Production</span>
            <strong>{meshQuality.productionReady && productionEligible ? "ready" : meshQuality.approvalStatus || "blocked"}</strong>
            <small>
              {meshQuality.productionReady && !productionEligible ? "reviewed geometry and inlet/outlet/wall tags required · " : meshQuality.approvalStatus ? `${meshQuality.approvalStatus} · ` : ""}
              {meshQuality.nativeQualityStatus ?? meshQuality.status}
            </small>
          </div>
          <div className="mesh-qa-commands" aria-label="Native mesh command list">
            {commandRuns.length > 0 ? (
              commandRuns.slice(0, 6).map((run) => (
                <div key={`${run.command}-${run.logPath ?? run.status}`} className={`mesh-command ${run.status}`}>
                  <span>{run.command}</span>
                  <strong>{run.status}</strong>
                  <small>{run.exitCode === null || run.exitCode === undefined ? "exit n/a" : `exit ${run.exitCode}`}{run.logPath ? ` · ${run.logPath}` : ""}</small>
                </div>
              ))
            ) : (
              <small>No native mesh commands have run yet.</small>
            )}
          </div>
          <div className="mesh-qa-metrics" aria-label="checkMesh metrics">
            <div>
              <span>Failed</span>
              <strong>{formatMeshMetric(metrics?.failedChecks, 0)}</strong>
            </div>
            <div>
              <span>Non-ortho</span>
              <strong>{formatMeshMetric(metrics?.maxNonOrthogonality)}</strong>
            </div>
            <div>
              <span>Skew</span>
              <strong>{formatMeshMetric(metrics?.maxSkewness)}</strong>
            </div>
            <div>
              <span>Aspect</span>
              <strong>{formatMeshMetric(metrics?.maxAspectRatio)}</strong>
            </div>
            <div>
              <span>Min vol</span>
              <strong>{formatMeshMetric(metrics?.minVolume, 2)}</strong>
            </div>
          </div>
          <div className="mesh-qa-yplus" aria-label="Y plus evidence">
            <span>Y+/wall</span>
            <strong>{yPlus?.status ?? "missing"}</strong>
            <small>
              {yPlus?.status === "present"
                ? `min ${formatMeshMetric(yPlus.min)} · mean ${formatMeshMetric(yPlus.mean)} · max ${formatMeshMetric(yPlus.max)}`
                : yPlus?.blockingReason ?? "waiting for y-plus or wall-distance evidence"}
            </small>
          </div>
          {blockers.length > 0 ? (
            <div className="mesh-qa-blockers" aria-label="Mesh QA blockers">
              {blockers.slice(0, 3).map((reason) => <small key={reason}>{reason}</small>)}
            </div>
          ) : null}
          {existingArtifacts.length > 0 ? (
            <small className="mesh-qa-artifacts" title={existingArtifacts.join(", ")}>
              Evidence: {existingArtifacts.slice(0, 3).join(", ")}{existingArtifacts.length > 3 ? `, +${existingArtifacts.length - 3}` : ""}
            </small>
          ) : null}
        </div>
      ) : (
        <p className="ok-line">Queue an OpenFOAM job to collect native mesh evidence.</p>
      )}
    </section>
  );
}

const defaultAdaptiveMesh: NonNullable<SolverSettings["adaptiveMesh"]> = {
  enabled: false,
  targetField: "velocity",
  errorMode: "gradient",
  adaptEvery: 5,
  maxCells: 250_000,
  minCellSize: 0.001,
  maxCellSize: 0.1,
  gradation: 1.4,
  writeAdaptedState: true
};

function MeshControlsPanel({
  solver,
  selectedEdge,
  onSolverChange,
  onMeshControlsChange
}: {
  solver: SolverSettings;
  selectedEdge: FluidEdge | null;
  onSolverChange: (patch: Partial<SolverSettings>) => void;
  onMeshControlsChange: (patch: Partial<NonNullable<SolverSettings["meshControls"]>>) => void;
}) {
  const controls = solver.meshControls ?? {};
  const featureRefinement = controls.featureRefinement ?? {};
  const quality = controls.quality ?? {};
  const adaptiveMesh = solver.adaptiveMesh ?? defaultAdaptiveMesh;
  const selectedRefinement = selectedEdge ? controls.refinementRegions?.find((region) => region.edgeId === selectedEdge.id) : null;
  const fullOGridDefaults = {
    coarse: { axial: 16, annular: 4, circumference: 32, core: 8 },
    medium: { axial: 32, annular: 8, circumference: 64, core: 16 },
    fine: { axial: 64, annular: 16, circumference: 128, core: 32 }
  }[solver.meshResolution];

  function fullOGridControls(
    patch: Partial<{ axial: number; annular: number; circumference: number; core: number }> = {}
  ) {
    return {
      ...controls,
      fullOGridAxialCells: patch.axial ?? controls.fullOGridAxialCells ?? fullOGridDefaults.axial,
      fullOGridAnnularRadialCells: patch.annular ?? controls.fullOGridAnnularRadialCells ?? fullOGridDefaults.annular,
      fullOGridCircumferentialCells: patch.circumference ?? controls.fullOGridCircumferentialCells ?? fullOGridDefaults.circumference,
      fullOGridCoreCellsPerSide: patch.core ?? controls.fullOGridCoreCellsPerSide ?? fullOGridDefaults.core
    };
  }

  function updateAdaptiveMesh(patch: Partial<NonNullable<SolverSettings["adaptiveMesh"]>>) {
    onSolverChange({
      adaptiveMesh: {
        ...adaptiveMesh,
        ...patch
      }
    });
  }

  function setSelectedEdgeRefinement(factor: number) {
    if (!selectedEdge) return;
    const nextFactor = Math.max(1, Math.min(4, Math.round(factor)));
    const existing = controls.refinementRegions ?? [];
    const remaining = existing.filter((region) => region.edgeId !== selectedEdge.id);
    onMeshControlsChange({
      refinementRegions:
        nextFactor > 1
          ? [
              ...remaining,
              {
                edgeId: selectedEdge.id,
                factor: nextFactor,
                reason: `${selectedEdge.type}-local-refinement`
              }
            ]
          : remaining
    });
  }

  return (
    <section className="mesh-controls-panel" aria-label="Production mesh controls">
      <div className="mesh-controls-heading">
        <strong>Mesh controls</strong>
        <span>starter mesh, not CAD-ready</span>
      </div>
      <label>
        Resolution
        <select
          aria-label="Mesh resolution"
          value={solver.meshResolution}
          onChange={(event) => {
            const meshResolution = event.target.value as SolverSettings["meshResolution"];
            const defaults = {
              coarse: { fullOGridAxialCells: 16, fullOGridAnnularRadialCells: 4, fullOGridCircumferentialCells: 32, fullOGridCoreCellsPerSide: 8 },
              medium: { fullOGridAxialCells: 32, fullOGridAnnularRadialCells: 8, fullOGridCircumferentialCells: 64, fullOGridCoreCellsPerSide: 16 },
              fine: { fullOGridAxialCells: 64, fullOGridAnnularRadialCells: 16, fullOGridCircumferentialCells: 128, fullOGridCoreCellsPerSide: 32 }
            }[meshResolution];
            onSolverChange({
              meshResolution,
              ...(solver.meshMode === "full-ogrid" ? { meshControls: { ...controls, ...defaults } } : {})
            });
          }}
        >
          <option value="coarse">Coarse</option>
          <option value="medium">Medium</option>
          <option value="fine">Fine</option>
        </select>
      </label>
      {solver.advancedMode === "incompressible-navier-stokes" && (
        <label>
          Run mode
          <select
            aria-label="Run mode"
            value={solver.runMode ?? "transient"}
            onChange={(event) => onSolverChange({ runMode: event.target.value as NonNullable<SolverSettings["runMode"]> })}
          >
            <option value="transient">Transient (quick starter)</option>
            <option value="steady">Steady (converged Δp)</option>
          </select>
        </label>
      )}
      {solver.advancedMode === "incompressible-navier-stokes" && (
        <label>
          Mesh mode
          <select
            aria-label="Mesh mode"
            value={solver.meshMode ?? "planar-2d"}
            onChange={(event) => {
              const meshMode = event.target.value as NonNullable<SolverSettings["meshMode"]>;
              onSolverChange(
                meshMode === "full-ogrid"
                  ? {
                      meshMode,
                      runMode: "steady",
                      turbulence: "laminar",
                      meshControls: fullOGridControls()
                    }
                  : { meshMode }
              );
            }}
          >
            <option value="planar-2d">Planar 2D (default)</option>
            <option value="axisymmetric">Axisymmetric (3D pipe)</option>
            <option value="full-ogrid">Full 360 O-grid (straight pipe)</option>
          </select>
        </label>
      )}
      {solver.meshMode === "full-ogrid" ? (
        <div className="mesh-control-grid" aria-label="Full O-grid exact cell controls">
          <label>
            Axial
            <input
              aria-label="Full O-grid axial cells"
              type="number"
              min={4}
              step={1}
              value={controls.fullOGridAxialCells ?? fullOGridDefaults.axial}
              onChange={(event) => onMeshControlsChange(fullOGridControls({ axial: Number(event.target.value) }))}
            />
          </label>
          <label>
            Annular radial
            <input
              aria-label="Full O-grid annular radial cells"
              type="number"
              min={2}
              step={1}
              value={controls.fullOGridAnnularRadialCells ?? fullOGridDefaults.annular}
              onChange={(event) => onMeshControlsChange(fullOGridControls({ annular: Number(event.target.value) }))}
            />
          </label>
          <label>
            Circumference
            <input
              aria-label="Full O-grid circumferential cells"
              type="number"
              min={16}
              step={4}
              value={controls.fullOGridCircumferentialCells ?? fullOGridDefaults.circumference}
              onChange={(event) => {
                const circumference = Number(event.target.value);
                onMeshControlsChange(fullOGridControls({ circumference, core: circumference / 4 }));
              }}
            />
          </label>
          <label>
            Core side
            <input
              aria-label="Full O-grid core cells per side"
              type="number"
              min={4}
              step={1}
              value={controls.fullOGridCoreCellsPerSide ?? fullOGridDefaults.core}
              onChange={(event) => onMeshControlsChange(fullOGridControls({ core: Number(event.target.value) }))}
            />
          </label>
        </div>
      ) : null}
      <label>
        Transverse cells
        <select
          aria-label="Transverse distribution"
          value={controls.transverseDistribution ?? "boundary-layer"}
          onChange={(event) => onMeshControlsChange({ transverseDistribution: event.target.value as "boundary-layer" | "uniform" })}
        >
          <option value="boundary-layer">Boundary layer (wall-clustered)</option>
          <option value="uniform">Uniform (laminar core)</option>
        </select>
      </label>
      <div className="mesh-control-grid">
        <label>
          Longitudinal x
          <input
            aria-label="Longitudinal refinement"
            type="number"
            min={1}
            max={4}
            step={1}
            value={controls.longitudinalRefinement ?? 1}
            onChange={(event) => onMeshControlsChange({ longitudinalRefinement: Number(event.target.value) })}
          />
        </label>
        <label>
          BL strips
          <input
            aria-label="Boundary layer strip cells"
            type="number"
            min={0}
            max={12}
            step={1}
            value={controls.boundaryLayerLayers ?? 1}
            onChange={(event) => onMeshControlsChange({ boundaryLayerLayers: Number(event.target.value) })}
          />
        </label>
        <label>
          Growth
          <input
            aria-label="Boundary layer growth rate"
            type="number"
            min={1}
            max={3}
            step={0.05}
            value={controls.boundaryLayerGrowthRate ?? 1.25}
            onChange={(event) => onMeshControlsChange({ boundaryLayerGrowthRate: Number(event.target.value) })}
          />
        </label>
        <label>
          Target y+
          <input
            aria-label="Target y plus"
            type="number"
            min={0.1}
            max={500}
            step={1}
            value={controls.targetYPlus ?? 30}
            onChange={(event) => onMeshControlsChange({ targetYPlus: Number(event.target.value) })}
          />
        </label>
      </div>
      <label className="toggle-row">
        <input
          aria-label="Feature-aware mesh clustering"
          type="checkbox"
          checked={Boolean(featureRefinement.enabled)}
          onChange={(event) => onMeshControlsChange({ featureRefinement: { ...featureRefinement, enabled: event.target.checked } })}
        />
        Feature clustering
      </label>
      <div className="mesh-control-grid">
        <label>
          Feature x
          <input
            aria-label="Feature refinement factor"
            type="number"
            min={1}
            max={4}
            step={1}
            value={featureRefinement.factor ?? 2}
            onChange={(event) => onMeshControlsChange({ featureRefinement: { ...featureRefinement, factor: Number(event.target.value) } })}
          />
        </label>
        <label>
          Cluster
          <input
            aria-label="Feature cluster strength"
            type="number"
            min={0}
            max={0.95}
            step={0.05}
            value={featureRefinement.clusterStrength ?? 0.55}
            onChange={(event) => onMeshControlsChange({ featureRefinement: { ...featureRefinement, clusterStrength: Number(event.target.value) } })}
          />
        </label>
      </div>
      {selectedEdge ? (
        <label>
          Selected edge refinement
          <input
            aria-label="Selected edge refinement factor"
            type="number"
            min={1}
            max={4}
            step={1}
            value={selectedRefinement?.factor ?? 1}
            onChange={(event) => setSelectedEdgeRefinement(Number(event.target.value))}
          />
        </label>
      ) : null}
      <div className="mesh-control-grid">
        <label>
          Max aspect
          <input
            aria-label="Mesh max aspect ratio"
            type="number"
            min={1}
            step={0.5}
            value={quality.maxAspectRatio ?? 18}
            onChange={(event) => onMeshControlsChange({ quality: { ...quality, maxAspectRatio: Number(event.target.value) } })}
          />
        </label>
        <label>
          Min angle
          <input
            aria-label="Mesh min interior angle"
            type="number"
            min={1}
            max={89}
            step={1}
            value={quality.minInteriorAngleDeg ?? 20}
            onChange={(event) => onMeshControlsChange({ quality: { ...quality, minInteriorAngleDeg: Number(event.target.value) } })}
          />
        </label>
      </div>
      <small>
        Exports `mesh/controls.json`, refinement, y-plus, prism-layer, adaptation, and native handoff manifests while production readiness remains blocked.
      </small>
      <div className="adaptive-mesh-panel" aria-label="Adaptive mesh planning">
        <div className="mesh-controls-heading">
          <strong>Adaptive mesh</strong>
          <span>config/export only</span>
        </div>
        <label className="toggle-row">
          <input
            aria-label="Enable adaptive mesh planning"
            type="checkbox"
            checked={adaptiveMesh.enabled}
            onChange={(event) => updateAdaptiveMesh({ enabled: event.target.checked })}
          />
          Plan native adaptation
        </label>
        <div className="mesh-control-grid">
          <label>
            Field
            <select
              aria-label="Adaptive mesh target field"
              value={adaptiveMesh.targetField}
              onChange={(event) => updateAdaptiveMesh({ targetField: event.target.value as NonNullable<SolverSettings["adaptiveMesh"]>["targetField"] })}
            >
              <option value="velocity">Velocity</option>
              <option value="pressure">Pressure</option>
              <option value="temperature">Temperature</option>
              <option value="phase">Phase</option>
              <option value="wall-shear">Wall shear</option>
              <option value="residual">Residual</option>
            </select>
          </label>
          <label>
            Error
            <select
              aria-label="Adaptive mesh error mode"
              value={adaptiveMesh.errorMode}
              onChange={(event) => updateAdaptiveMesh({ errorMode: event.target.value as NonNullable<SolverSettings["adaptiveMesh"]>["errorMode"] })}
            >
              <option value="gradient">Gradient</option>
              <option value="relative-error">Relative</option>
              <option value="absolute-error">Absolute</option>
            </select>
          </label>
          <label>
            Every
            <input
              aria-label="Adaptive mesh cadence"
              type="number"
              min={1}
              max={100}
              step={1}
              value={adaptiveMesh.adaptEvery}
              onChange={(event) => updateAdaptiveMesh({ adaptEvery: Number(event.target.value) })}
            />
          </label>
          <label>
            Max cells
            <input
              aria-label="Adaptive mesh max cells"
              type="number"
              min={100}
              max={50_000_000}
              step={1000}
              value={adaptiveMesh.maxCells}
              onChange={(event) => updateAdaptiveMesh({ maxCells: Number(event.target.value) })}
            />
          </label>
          <label>
            Min size
            <input
              aria-label="Adaptive mesh min cell size"
              type="number"
              min={0.000001}
              step={0.001}
              value={adaptiveMesh.minCellSize}
              onChange={(event) => updateAdaptiveMesh({ minCellSize: Number(event.target.value) })}
            />
          </label>
          <label>
            Max size
            <input
              aria-label="Adaptive mesh max cell size"
              type="number"
              min={0.000001}
              step={0.01}
              value={adaptiveMesh.maxCellSize}
              onChange={(event) => updateAdaptiveMesh({ maxCellSize: Number(event.target.value) })}
            />
          </label>
          <label>
            Gradation
            <input
              aria-label="Adaptive mesh gradation"
              type="number"
              min={1}
              max={5}
              step={0.1}
              value={adaptiveMesh.gradation}
              onChange={(event) => updateAdaptiveMesh({ gradation: Number(event.target.value) })}
            />
          </label>
        </div>
        <label className="toggle-row">
          <input
            aria-label="Write adapted-state placeholder"
            type="checkbox"
            checked={adaptiveMesh.writeAdaptedState}
            onChange={(event) => updateAdaptiveMesh({ writeAdaptedState: event.target.checked })}
          />
          Export adapted-state placeholder
        </label>
      </div>
    </section>
  );
}

function EdgeInspector({
  edge,
  nodes,
  result,
  onChange,
  onEndpointChange
}: {
  edge: FluidEdge;
  nodes: Record<string, FluidNode>;
  result: ReturnType<typeof useFlowStore.getState>["result"]["edgeResults"][string] | null;
  onChange: (patch: Partial<FluidEdge>) => void;
  onEndpointChange: (endpoint: "from" | "to", nodeId: string, port: PipePortId) => void;
}) {
  const rectangularWidth = edge.shape.kind === "rectangular" ? edge.shape.width : 0.1;
  const rectangularHeight = edge.shape.kind === "rectangular" ? edge.shape.height : 0.1;
  const nodeOptions = Object.values(nodes);

  return (
    <div className="inspector-section">
      <h2>{edge.label}</h2>
      <div className="endpoint-grid" aria-label="Pipe endpoint editor">
        <label>
          From node
          <select aria-label="From node" value={edge.from} onChange={(event) => onEndpointChange("from", event.target.value, edge.fromPort ?? "outlet")}>
            {nodeOptions.map((node) => (
              <option key={node.id} value={node.id}>
                {node.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          From port
          <select
            aria-label="From port"
            value={edge.fromPort ?? "outlet"}
            onChange={(event) => onEndpointChange("from", edge.from, event.target.value as PipePortId)}
          >
            <option value="outlet">Outlet</option>
            <option value="north">North</option>
            <option value="south">South</option>
          </select>
        </label>
        <label>
          To node
          <select aria-label="To node" value={edge.to} onChange={(event) => onEndpointChange("to", event.target.value, edge.toPort ?? "inlet")}>
            {nodeOptions.map((node) => (
              <option key={node.id} value={node.id}>
                {node.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          To port
          <select
            aria-label="To port"
            value={edge.toPort ?? "inlet"}
            onChange={(event) => onEndpointChange("to", edge.to, event.target.value as PipePortId)}
          >
            <option value="inlet">Inlet</option>
            <option value="north">North</option>
            <option value="south">South</option>
          </select>
        </label>
      </div>
      <label>
        Length m
        <input type="number" value={edge.length} min={0.1} step={0.5} onChange={(event) => onChange({ length: Number(event.target.value) })} />
      </label>
      {edge.shape.kind === "circular" ? (
        <>
          <label>
            Inlet diameter m
            <input
              type="number"
              value={edge.shape.diameter}
              min={0.005}
              step={0.005}
              onChange={(event) => onChange({ shape: { kind: "circular", diameter: Number(event.target.value) } })}
            />
          </label>
          {["pipe", "venturi", "expansion", "contraction", "nozzle"].includes(edge.type) && (
            <label>
              Outlet diameter m
              <input
                type="number"
                value={edge.outletDiameter ?? edge.shape.diameter}
                min={0.005}
                step={0.005}
                onChange={(event) => onChange({ outletDiameter: Number(event.target.value) })}
              />
            </label>
          )}
        </>
      ) : (
        <>
          <label>
            Width m
            <input
              type="number"
              value={edge.shape.width}
              min={0.005}
              step={0.005}
              onChange={(event) => onChange({ shape: { kind: "rectangular", width: Number(event.target.value), height: rectangularHeight } })}
            />
          </label>
          <label>
            Height m
            <input
              type="number"
              value={edge.shape.height}
              min={0.005}
              step={0.005}
              onChange={(event) => onChange({ shape: { kind: "rectangular", width: rectangularWidth, height: Number(event.target.value) } })}
            />
          </label>
        </>
      )}
      {edge.type === "venturi" ? (
        <>
          <label>
            Throat diameter m
            <input
              type="number"
              value={edge.throatDiameter ?? 0.06}
              min={0.005}
              step={0.005}
              onChange={(event) => onChange({ throatDiameter: Number(event.target.value) })}
            />
          </label>
          <label>
            Throat position
            <input
              type="number"
              value={edge.throatPosition ?? 0.5}
              min={0.05}
              max={0.95}
              step={0.05}
              onChange={(event) => onChange({ throatPosition: Number(event.target.value) })}
            />
          </label>
          <label>
            Throat length m
            <input
              type="number"
              value={edge.throatLength ?? 0}
              min={0}
              step={0.05}
              onChange={(event) => onChange({ throatLength: Number(event.target.value) })}
            />
          </label>
        </>
      ) : null}
      <label>
        Minor loss K
        <input
          type="number"
          value={edge.minorLossK}
          min={0}
          step={0.1}
          onChange={(event) => onChange({ minorLossK: Number(event.target.value) })}
        />
      </label>
      <label>
        Roughness m
        <input
          type="number"
          value={edge.roughness}
          min={0}
          step={0.00001}
          onChange={(event) => onChange({ roughness: Number(event.target.value) })}
        />
      </label>
      {result ? (
        <dl className="readouts">
          <div>
            <dt>Flow</dt>
            <dd>{formatNumber(result.flowRate, 5)} m3/s</dd>
          </div>
          <div>
            <dt>Velocity</dt>
            <dd>{formatNumber(result.velocity)} m/s</dd>
          </div>
          <div>
            <dt>Reynolds</dt>
            <dd>{formatNumber(result.reynolds)}</dd>
          </div>
          <div>
            <dt>Pressure drop</dt>
            <dd>{formatNumber(result.pressureDrop / 1000)} kPa</dd>
          </div>
          <div>
            <dt>Effective length</dt>
            <dd>{formatNumber(result.effectiveLength)} m</dd>
          </div>
          <div>
            <dt>Port bend</dt>
            <dd>{formatNumber(result.bendAngle, 0)} deg</dd>
          </div>
          <div>
            <dt>Geometry K</dt>
            <dd>{formatNumber(result.geometryMinorLossK, 3)}</dd>
          </div>
        </dl>
      ) : null}
    </div>
  );
}

function NodeInspector({
  node,
  result,
  onChange,
  onRotate
}: {
  node: FluidNode;
  result: ReturnType<typeof useFlowStore.getState>["result"]["nodeResults"][string] | null;
  onChange: (patch: Partial<FluidNode>) => void;
  onRotate: (rotation: number) => void;
}) {
  return (
    <div className="inspector-section">
      <h2>{node.label}</h2>
      <div className="coordinate-panel" aria-label="Node coordinates">
        <CoordinateControl
          axis="X"
          value={node.position.x}
          unit="px"
          min={40}
          max={960}
          step={1}
          resetValue={280}
          onChange={(value) => onChange({ position: { ...node.position, x: value } })}
        />
        <CoordinateControl
          axis="Y"
          value={node.position.y}
          unit="px"
          min={80}
          max={640}
          step={1}
          resetValue={260}
          onChange={(value) => onChange({ position: { ...node.position, y: value } })}
        />
        <CoordinateControl
          axis="Z"
          value={node.elevation}
          unit="m"
          min={-20}
          max={20}
          step={0.1}
          resetValue={0}
          onChange={(value) => onChange({ elevation: value })}
        />
        <CoordinateControl
          axis="A"
          value={node.rotation ?? 0}
          unit="deg"
          min={-180}
          max={180}
          step={1}
          resetValue={0}
          onChange={onRotate}
        />
      </div>
      <label>
        Pressure Pa
        <input
          type="number"
          value={node.pressure ?? 101_325}
          step={1000}
          onChange={(event) => onChange({ pressure: Number(event.target.value) })}
        />
      </label>
      <label>
        Elevation m
        <input type="number" value={node.elevation} step={0.1} onChange={(event) => onChange({ elevation: Number(event.target.value) })} />
      </label>
      <div className="aim-presets" aria-label="Aim presets">
        <button type="button" onClick={() => onRotate(0)}>
          East
        </button>
        <button type="button" onClick={() => onRotate(90)}>
          South
        </button>
        <button type="button" onClick={() => onRotate(180)}>
          West
        </button>
        <button type="button" onClick={() => onRotate(-90)}>
          North
        </button>
      </div>
      {node.type === "pump" ? (
        <label>
          Pump head m
          <input type="number" value={node.head ?? 10} step={0.5} onChange={(event) => onChange({ head: Number(event.target.value) })} />
        </label>
      ) : null}
      {result ? (
        <dl className="readouts">
          <div>
            <dt>Head</dt>
            <dd>{formatNumber(result.head)} m</dd>
          </div>
          <div>
            <dt>Residual</dt>
            <dd>{result.massResidual.toExponential(2)} m3/s</dd>
          </div>
          {typeof result.concentration === "number" ? (
            <div>
              <dt>Scalar mix</dt>
              <dd>{formatNumber(result.concentration, 3)}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </div>
  );
}

function CoordinateControl({
  axis,
  value,
  unit,
  min,
  max,
  step,
  resetValue,
  onChange
}: {
  axis: "X" | "Y" | "Z" | "A";
  value: number;
  unit: string;
  min: number;
  max: number;
  step: number;
  resetValue: number;
  onChange: (value: number) => void;
}) {
  const rangeLabel = axis === "A" ? "Rotation slider" : `${axis} ${unit} slider`;
  const valueLabel = axis === "A" ? "Rotation degrees" : `${axis} ${unit} value`;
  return (
    <div className="coordinate-row">
      <strong>{axis}</strong>
      <input
        aria-label={rangeLabel}
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <input
        aria-label={valueLabel}
        data-testid={axis === "A" ? "rotation-degrees-input" : undefined}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <span>{unit}</span>
      <button type="button" title={`Reset ${axis}`} onClick={() => onChange(resetValue)}>
        <RotateCcw size={16} />
      </button>
    </div>
  );
}

function RuntimeReadiness({
  statuses,
  activeSolver,
  backendOnline
}: {
  statuses: SolverRuntimeStatus[];
  activeSolver: SolverTier;
  backendOnline: boolean;
}) {
  const active = statuses.find((status) => status.solver === activeSolver) ?? null;
  if (!backendOnline) {
    return (
      <div className="runtime-card blocked" aria-label="Solver runtime readiness">
        <div className="runtime-card-header">
          <strong>Runtime readiness</strong>
          <span>offline</span>
        </div>
        <small>Start the solver service to inspect local CFD dependencies.</small>
      </div>
    );
  }

  if (statuses.length === 0) {
    return (
      <div className="runtime-card" aria-label="Solver runtime readiness">
        <div className="runtime-card-header">
          <strong>Runtime readiness</strong>
          <span>checking</span>
        </div>
        <small>Waiting for solver dependency diagnostics.</small>
      </div>
    );
  }

  return (
    <div className={active?.runnable ? "runtime-card ready" : "runtime-card blocked"} aria-label="Solver runtime readiness">
      <div className="runtime-card-header">
        <strong>{solverLabels[activeSolver]} readiness</strong>
        <span>{active?.runnable ? active.preferredExecution : "blocked"}</span>
      </div>
      <div className="runtime-grid">
        {statuses.map((status) => (
          <div key={status.solver} className={status.runnable ? "runtime-pill ready" : "runtime-pill blocked"}>
            <i aria-hidden="true" />
            <span>{solverLabels[status.solver]}</span>
            <small>{status.runnable ? status.preferredExecution : "missing"}</small>
          </div>
        ))}
      </div>
      {active?.blockers.length ? <small>{active.blockers.slice(0, 2).join(" ")}</small> : null}
    </div>
  );
}

function jobLogSummary(job: JobRecord | null): SolverLogSummary | null {
  const result = job?.result as JobResultPayload | null | undefined;
  return result?.logSummary ?? null;
}

function formatDiagnosticLatest(latest: Record<string, number>) {
  return Object.entries(latest)
    .slice(0, 3)
    .map(([key, value]) => `${key} ${formatResidual(value)}`)
    .join(" · ");
}

function formatDiagnosticSummary(summary: NonNullable<JobResultPayload["diagnosticSummary"]>[number]) {
  if ("latest" in summary) {
    return `${summary.kind} · ${summary.rowCount} rows · ${formatDiagnosticLatest(summary.latest)}`;
  }
  return `${summary.kind} · ${summary.lineCount} lines · ${summary.excerpts.slice(0, 2).join(" · ")}`;
}

function formatResultFieldSummary(file: JobArtifactFile) {
  const summary = file.fieldSummary;
  if (!summary || !summary.fields.length) return null;
  const fields = summary.fields
    .slice(0, 3)
    .map((field) => `${field.name} ${field.location} ${field.kind.replace("-magnitude", "")} ${field.tupleCount} · ${formatResidual(field.min)}-${formatResidual(field.max)}`)
    .join(" · ");
  const remaining = summary.fields.length > 3 ? ` · +${summary.fields.length - 3} fields` : "";
  const pointLabel = `${summary.pointCount} point${summary.pointCount === 1 ? "" : "s"}`;
  const cellLabel = `${summary.cellCount} cell${summary.cellCount === 1 ? "" : "s"}`;
  return `${file.path}: ${pointLabel}, ${cellLabel} · ${fields}${remaining}`;
}

function indexedArtifactFieldLabel(artifact: JobArtifactIndex["artifacts"][number]) {
  const summary = artifact.fieldSummary;
  if (!summary || !summary.fields.length) return null;
  const fieldNames = summary.fields.slice(0, 3).map((field) => `${field.name} ${field.location}`).join(", ");
  return `${summary.fields.length} field${summary.fields.length === 1 ? "" : "s"}: ${fieldNames}${summary.fields.length > 3 ? ", ..." : ""}`;
}

function formatResultCollectionSummary(artifact: JobArtifactIndex["artifacts"][number]) {
  const summary = artifact.collectionSummary;
  if (!summary) return null;
  if (summary.skipped) return `${artifact.path}: ${summary.skipped}`;
  if (summary.error) return `${artifact.path}: ${summary.error}`;
  const missing = summary.missingResultCount > 0 ? ` · ${summary.missingResultCount} missing` : "";
  const unsafe = summary.unsafeReferenceCount > 0 ? ` · ${summary.unsafeReferenceCount} unsafe` : "";
  const truncated = summary.truncated ? " · truncated" : "";
  return `${artifact.path}: ${summary.referencedResultCount}/${summary.datasetCount} timesteps${missing}${unsafe}${truncated}`;
}

function formatResultPreview(preview: JobArtifactPreview) {
  if (preview.skipped) return `${preview.path}: ${preview.skipped}`;
  if (!preview.schema) return `${preview.path}: preview unavailable`;
  const pointLabel = `${preview.pointCount ?? 0}/${preview.sourcePointCount ?? 0} points`;
  const cellLabel = `${preview.cellCount ?? 0}/${preview.sourceCellCount ?? 0} cells`;
  const fields = preview.fieldSummary?.fields
    .slice(0, 3)
    .map((field) => `${field.name} ${field.location} ${field.tupleCount}`)
    .join(" · ");
  const suffix = preview.truncated ? " · thinned" : "";
  return `${preview.path}: ${pointLabel}, ${cellLabel}${fields ? ` · ${fields}` : ""}${suffix}`;
}

function CaseSummary({
  solverCase,
  job,
  currentCampaign,
  onLoadSkippedResult,
  onPreviewResult,
  onPreviewResults
}: {
  solverCase: SolverCase;
  job: JobRecord | null;
  currentCampaign: ValidatedBenchmark | null;
  onLoadSkippedResult: (path: string) => void;
  onPreviewResult: (preview: JobArtifactPreview) => void;
  onPreviewResults: (previews: JobArtifactPreview[], startIndex?: number) => void;
}) {
  const [artifactIndex, setArtifactIndex] = useState<JobArtifactIndex | null>(null);
  const [artifactIndexError, setArtifactIndexError] = useState<string | null>(null);
  const [artifactIndexLoading, setArtifactIndexLoading] = useState(false);
  const [artifactPreview, setArtifactPreview] = useState<JobArtifactPreview | null>(null);
  const [artifactPreviewPath, setArtifactPreviewPath] = useState<string | null>(null);
  const [artifactPreviewError, setArtifactPreviewError] = useState<string | null>(null);
  const [artifactSequenceLoading, setArtifactSequenceLoading] = useState(false);
  const [artifactSequenceError, setArtifactSequenceError] = useState<string | null>(null);
  const [artifactSequenceCursor, setArtifactSequenceCursor] = useState(0);
  const summary = jobLogSummary(job);
  const resultPayload = job?.result as JobResultPayload | null | undefined;
  const validatedRun = resultPayload?.validatedBenchmark ?? null;
  const resultFiles = resultPayload?.resultFiles ?? [];
  const diagnosticFiles = resultPayload?.diagnosticFiles ?? [];
  const resultFileCount = resultFiles.filter((file) => Boolean(file.text)).length;
  const diagnosticFileCount = diagnosticFiles.filter((file) => Boolean(file.text)).length;
  const skippedResultCount = resultFiles.filter((file) => Boolean(file.skipped)).length;
  const skippedDiagnosticCount = diagnosticFiles.filter((file) => Boolean(file.skipped)).length;
  const firstSkippedArtifact = [...resultFiles, ...diagnosticFiles].find((file) => file.skipped);
  const loadableSkippedResult = resultFiles.find((file) => file.skipped && /\.(vtk|vtu)$/i.test(file.path));
  const firstFieldSummary = resultFiles.map(formatResultFieldSummary).find(Boolean);
  const firstDiagnosticSummary = resultPayload?.diagnosticSummary?.[0] ?? null;
  const residualRows = summary?.residuals ? Object.entries(summary.residuals).slice(0, 3) : [];
  const statusLabel = job ? `Job ${job.status}` : solverCase.status === "blocked" ? "Case blocked" : "Case generated";
  // Older retained job records and test fixtures predate the explicit
  // capability contract.  They must fail closed as experimental rather than
  // crash or accidentally receive the benchmark label.
  const evidenceCapability = job?.evidenceCapability ?? solverCase.evidenceCapability ?? { status: "experimental" as const };
  const currentPromotionBlocked = currentCampaign?.promotionBlocked !== false;
  const runStatusLabel = validatedRunStatusLabel(
    evidenceCapability.status,
    job?.status,
    validatedRun?.allChecksPassed === true,
    currentPromotionBlocked
  );
  const collectionArtifacts = (artifactIndex?.artifacts ?? []).filter((artifact) => artifact.collectionSummary);
  const concreteIndexedResults = [...(artifactIndex?.artifacts ?? [])]
    .filter((artifact) => /\.(vtk|vtu)$/i.test(artifact.path))
    .sort((left, right) => {
      const leftHasTime = typeof left.time === "number";
      const rightHasTime = typeof right.time === "number";
      const leftTime = leftHasTime ? left.time as number : null;
      const rightTime = rightHasTime ? right.time as number : null;
      if (leftTime !== null && rightTime !== null && leftTime !== rightTime) return leftTime - rightTime;
      if (leftHasTime !== rightHasTime) return leftHasTime ? -1 : 1;
      return left.path.localeCompare(right.path);
    });
  const sequenceIndexedResults = concreteIndexedResults.filter((artifact) => artifact.fieldSummary);
  const artifactSequenceRemaining = Math.max(sequenceIndexedResults.length - artifactSequenceCursor, 0);
  const artifactSequenceNextCount = Math.min(artifactSequenceRemaining, PREVIEW_SEQUENCE_LIMIT);
  useEffect(() => {
    setArtifactIndex(null);
    setArtifactIndexError(null);
    setArtifactIndexLoading(false);
    setArtifactPreview(null);
    setArtifactPreviewPath(null);
    setArtifactPreviewError(null);
    setArtifactSequenceLoading(false);
    setArtifactSequenceError(null);
    setArtifactSequenceCursor(0);
  }, [job?.id]);

  async function loadArtifactIndex() {
    if (!job) return;
    setArtifactIndexLoading(true);
    setArtifactIndexError(null);
    setArtifactSequenceCursor(0);
    setArtifactSequenceError(null);
    try {
      setArtifactIndex(await fetchJobArtifacts(job.id, "result", 200));
    } catch (error) {
      setArtifactIndexError(error instanceof Error ? error.message : "Could not list result artifacts.");
    } finally {
      setArtifactIndexLoading(false);
    }
  }

  async function loadArtifactPreview(path: string) {
    if (!job) return;
    setArtifactPreviewPath(path);
    setArtifactPreviewError(null);
    try {
      const preview = await fetchJobArtifactPreview(job.id, path, 500, 500);
      setArtifactPreview(preview);
      onPreviewResult(preview);
    } catch (error) {
      setArtifactPreview(null);
      setArtifactPreviewError(error instanceof Error ? error.message : "Could not preview result artifact.");
    } finally {
      setArtifactPreviewPath(null);
    }
  }

  async function loadArtifactPreviewSequence() {
    if (!job) return;
    const start = artifactSequenceCursor >= sequenceIndexedResults.length ? 0 : artifactSequenceCursor;
    const artifacts = sequenceIndexedResults.slice(start, start + PREVIEW_SEQUENCE_LIMIT);
    setArtifactSequenceLoading(true);
    setArtifactSequenceError(null);
    try {
      const previews: JobArtifactPreview[] = [];
      for (const artifact of artifacts) {
        previews.push(await fetchJobArtifactPreview(job.id, artifact.path, 500, 500));
      }
      if (previews.length === 0) {
        setArtifactSequenceError("No indexed VTK/VTU files are available for preview.");
        return;
      }
      setArtifactPreview(previews[previews.length - 1]);
      onPreviewResults(previews, start);
      setArtifactSequenceCursor(Math.min(start + previews.length, sequenceIndexedResults.length));
    } catch (error) {
      setArtifactPreview(null);
      setArtifactSequenceError(error instanceof Error ? error.message : "Could not preview result sequence.");
    } finally {
      setArtifactSequenceLoading(false);
    }
  }

  return (
    <div className="case-summary">
      <strong>{statusLabel}</strong>
      <small
        className={
          evidenceCapability.status === "experimental" ||
          (validatedRun?.allChecksPassed && job?.status === "complete" && currentPromotionBlocked)
            ? "job-error"
            : validatedRun?.allChecksPassed && job?.status === "complete"
              ? "validated-run-label"
              : undefined
        }
      >
        {runStatusLabel}
      </small>
      {validatedRun?.allChecksPassed && job?.status === "complete" && currentPromotionBlocked && currentCampaign?.blockingReasons?.length ? (
        <small className="job-error">Current gate: {currentCampaign.blockingReasons.join(" ")}</small>
      ) : null}
      <span>
        {solverCase.solver} · {solverCase.advancedMode}
      </span>
      <code>{solverCase.runCommand.join(" ")}</code>
      {job ? <small>{job.logs[job.logs.length - 1]}</small> : null}
      {resultPayload ? (
        <small className="artifact-counts">
          {resultPayload.progressive ? "Progressive" : "Final"} artifacts: {resultFileCount} field, {diagnosticFileCount} diagnostic
          {skippedResultCount || skippedDiagnosticCount ? ` · skipped ${skippedResultCount} field, ${skippedDiagnosticCount} diagnostic` : ""}
        </small>
      ) : null}
      {validatedRun ? (
        <div className={`validated-force-evidence ${validatedRun.allChecksPassed ? "passed" : "failed"}`} aria-label="Validated preset force evidence">
          <strong>{validatedRun.allChecksPassed ? "Force audit passed" : "Force audit failed"}</strong>
          <small>{validatedRun.cellsPerAxis}<sup>3</sup> all-hex cells · OpenFOAM vs direct faces vs analytic state</small>
          <div>
            <span>OpenFOAM ↔ faces</span>
            <code>{formatResidual(validatedRun.errors.openFoamVsDirectAbsolute)}</code>
            <span>Pressure ↔ analytic</span>
            <code>{formatResidual(validatedRun.errors.analyticPressureForceRelative)}</code>
            <span>Wall viscous ↔ analytic</span>
            <code>{formatResidual(validatedRun.errors.analyticWallViscousRelative)}</code>
            <span>Open viscous ↔ zero</span>
            <code>{formatResidual(validatedRun.errors.analyticOpenViscousRelative)}</code>
          </div>
          <small>{validatedRun.scope}</small>
        </div>
      ) : null}
      {firstDiagnosticSummary ? (
        <small className="diagnostic-summary" aria-label="Diagnostic table summary">
          {formatDiagnosticSummary(firstDiagnosticSummary)}
        </small>
      ) : null}
      {firstFieldSummary ? (
        <small className="diagnostic-summary" aria-label="Result field summary">
          Fields {firstFieldSummary}
        </small>
      ) : null}
      {firstSkippedArtifact ? (
        <small className="diagnostic-summary" aria-label="Skipped artifact summary">
          Skipped {firstSkippedArtifact.path}: {firstSkippedArtifact.skipped}
        </small>
      ) : null}
      {loadableSkippedResult ? (
        <button className="case-summary-action" type="button" onClick={() => onLoadSkippedResult(loadableSkippedResult.path)}>
          Load skipped field
        </button>
      ) : null}
      {job?.caseDir && resultPayload ? (
        <button className="case-summary-action" type="button" onClick={loadArtifactIndex} disabled={artifactIndexLoading}>
          {artifactIndexLoading ? "Indexing fields..." : "Index field files"}
        </button>
      ) : null}
      {artifactIndexError ? (
        <small className="diagnostic-summary" aria-label="Artifact index error">
          {artifactIndexError}
        </small>
      ) : null}
      {artifactIndex ? (
        <div className="artifact-index" aria-label="Indexed result artifacts">
          <small>
            Indexed {artifactIndex.artifacts.length}/{artifactIndex.count} field file{artifactIndex.count === 1 ? "" : "s"}
            {artifactIndex.truncated ? " · truncated" : ""}
          </small>
          {collectionArtifacts.length > 0 ? (
            <div className="artifact-collections" aria-label="Result collection manifests">
              {collectionArtifacts.slice(0, 3).map((artifact) => (
                <small key={artifact.path} title={artifact.path}>
                  {formatResultCollectionSummary(artifact)}
                </small>
              ))}
            </div>
          ) : null}
          {concreteIndexedResults.length > 1 ? (
            <button
              className="case-summary-action"
              type="button"
              onClick={loadArtifactPreviewSequence}
              disabled={artifactSequenceLoading || artifactSequenceRemaining === 0}
            >
              {artifactSequenceLoading
                ? "Previewing sequence..."
                : artifactSequenceRemaining === 0
                  ? `Preview sequence loaded (${sequenceIndexedResults.length})`
                  : artifactSequenceCursor === 0
                    ? `Preview sequence (${artifactSequenceNextCount})`
                    : `Preview next ${artifactSequenceNextCount} (${artifactSequenceCursor} loaded)`}
            </button>
          ) : null}
          {artifactSequenceError ? (
            <small className="diagnostic-summary" aria-label="Artifact sequence preview error">
              {artifactSequenceError}
            </small>
          ) : null}
          {concreteIndexedResults.slice(0, 6).map((artifact) => {
            const fieldLabel = indexedArtifactFieldLabel(artifact);
            return (
              <div key={artifact.path} className="artifact-item">
                <button
                  type="button"
                  title={`${artifact.path}, ${artifact.size.toLocaleString()} bytes${fieldLabel ? `, ${fieldLabel}` : ""}${artifact.timeSource ? `, t=${artifact.timeText ?? artifact.time}` : ""}`}
                  onClick={() => onLoadSkippedResult(artifact.path)}
                >
                  <span>{artifact.path.split("/").at(-1)}</span>
                  {artifact.timeSource ? (
                    <small>
                      t={artifact.timeText ?? formatNumber(artifact.time ?? Number.NaN, 4)} ·{" "}
                      {artifact.timeSource === "pvd" ? artifact.collectionPath : "OpenFOAM native fields"}
                    </small>
                  ) : null}
                  {fieldLabel ? <small>{fieldLabel}</small> : null}
                </button>
                <button type="button" className="artifact-preview-button" onClick={() => loadArtifactPreview(artifact.path)} disabled={artifactPreviewPath === artifact.path}>
                  {artifactPreviewPath === artifact.path ? "Previewing" : "Preview"}
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      {artifactPreview ? (
        <small className="diagnostic-summary" aria-label="Result artifact preview">
          Preview {formatResultPreview(artifactPreview)}
        </small>
      ) : null}
      {artifactPreviewError ? (
        <small className="diagnostic-summary" aria-label="Result artifact preview error">
          {artifactPreviewError}
        </small>
      ) : null}
      {summary ? (
        <div className="job-progress" aria-label="Solver progress summary">
          <div className="job-progress-grid">
            <span>Lines</span>
            <strong>{summary.lineCount}</strong>
            {summary.latestTime !== undefined ? (
              <>
                <span>Time</span>
                <strong>{formatResidual(summary.latestTime)}</strong>
              </>
            ) : null}
            {summary.latestIteration !== undefined ? (
              <>
                <span>Iter</span>
                <strong>{formatNumber(summary.latestIteration, 0)}</strong>
              </>
            ) : null}
          </div>
          {residualRows.length ? (
            <div className="residual-list" aria-label="Residual summary">
              {residualRows.map(([field, residual]) => (
                <span key={field}>
                  {field}: {formatResidual(residual.final)}
                </span>
              ))}
            </div>
          ) : null}
          {summary.warnings?.length || summary.errors?.length ? (
            <small className="log-flags">
              {summary.warnings?.length ? `${summary.warnings.length} warning${summary.warnings.length === 1 ? "" : "s"}` : null}
              {summary.warnings?.length && summary.errors?.length ? " · " : null}
              {summary.errors?.length ? `${summary.errors.length} error${summary.errors.length === 1 ? "" : "s"}` : null}
            </small>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
