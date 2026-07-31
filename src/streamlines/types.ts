import type { ResultColorMap, VtkResultDataset } from "../types";

export type StreamlineVec3 = [number, number, number];

export const STREAMLINE_LIMITS = {
  defaultSeeds: 64,
  maxSeeds: 256,
  maxVerticesPerLine: 1_024,
  maxTotalVertices: 65_536,
  maxSprites: 256
} as const;

export type StreamlineTerminationReason =
  | "active"
  | "domain-exit"
  | "zero-velocity"
  | "max-vertices"
  | "total-vertex-limit"
  | "cancelled"
  | "seed-outside-domain"
  | "invalid-velocity-field";

export type StreamlineInterpolationMethod =
  | "point-barycentric-triangle"
  | "point-barycentric-tetra-decomposition"
  | "cell-piecewise-constant";

export type StreamlineInterpolationProvenance = {
  renderedCellId: number;
  sourceCellId: number;
  pointIds: number[];
  weights: number[];
  method: StreamlineInterpolationMethod;
};

export type StreamlineVertex = {
  position: StreamlineVec3;
  velocity: StreamlineVec3;
  speed: number;
  fields: Record<string, number>;
  provenance: StreamlineInterpolationProvenance;
  terminationReason: StreamlineTerminationReason;
};

export type StreamlineLine = {
  seedIndex: number;
  vertices: StreamlineVertex[];
  terminationReason: StreamlineTerminationReason;
};

export type StreamlineResult = {
  schema: "flowlab.steady_streamlines.v1";
  terminology: "steady-streamline";
  sourceName: string;
  sourceIdentity: "verified-case-cell-order" | "artifact-local-unlinked";
  spatialDimension: 2 | 3;
  velocityField: string;
  velocityLocation: "point" | "cell";
  velocityInterpolation: "barycentric point field" | "piecewise constant cell field";
  fieldInterpolations: Record<string, "barycentric point field" | "piecewise constant cell field">;
  lines: StreamlineLine[];
  seedCount: number;
  vertexCount: number;
  limits: typeof STREAMLINE_LIMITS;
};

export type StreamlineSeedPlane = {
  origin: StreamlineVec3;
  axisU: StreamlineVec3;
  axisV: StreamlineVec3;
  countU: number;
  countV: number;
};

export type StreamlineRequest = {
  dataset: VtkResultDataset;
  seeds: StreamlineVec3[];
  stepSize?: number;
  maxVerticesPerLine?: number;
  maxTotalVertices?: number;
  sourceIdentity?: StreamlineResult["sourceIdentity"];
};

export type StreamlineWorkerRequest = Omit<StreamlineRequest, "dataset"> & {
  id: string;
  dataset: VtkResultDataset;
};

export type StreamlineWorkerResponse =
  | { id: string; status: "complete"; result: StreamlineResult }
  | { id: string; status: "error"; error: string };

export type StreamlineDisplayOptions = {
  colorField: "velocity" | "pressure" | "temperature" | "phase" | "vorticity";
  colorMap: ResultColorMap;
  showLines: boolean;
  showSprites: boolean;
  reducedMotion: boolean;
};
