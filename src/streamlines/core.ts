import type { VtkResultDataset } from "../types";
import { maximumOf, pointExtent, pointSpans } from "../numeric";
import {
  STREAMLINE_LIMITS,
  type StreamlineInterpolationMethod,
  type StreamlineInterpolationProvenance,
  type StreamlineLine,
  type StreamlineRequest,
  type StreamlineResult,
  type StreamlineSeedPlane,
  type StreamlineTerminationReason,
  type StreamlineVec3,
  type StreamlineVertex
} from "./types";

const EPSILON = 1e-9;
const ZERO_SPEED = 1e-12;
const CELL_POINT_COUNTS: Record<number, number> = { 5: 3, 9: 4, 10: 4, 12: 8, 13: 6, 14: 5 };
const TETRA_DECOMPOSITIONS: Record<number, number[][]> = {
  10: [[0, 1, 2, 3]],
  12: [
    [0, 1, 2, 6],
    [0, 2, 3, 6],
    [0, 3, 7, 6],
    [0, 7, 4, 6],
    [0, 4, 5, 6],
    [0, 5, 1, 6]
  ],
  13: [
    [0, 1, 2, 3],
    [1, 2, 4, 3],
    [2, 4, 5, 3]
  ],
  14: [
    [0, 1, 2, 4],
    [0, 2, 3, 4]
  ]
};
const TRIANGLE_DECOMPOSITIONS: Record<number, number[][]> = {
  5: [[0, 1, 2]],
  9: [
    [0, 1, 2],
    [0, 2, 3]
  ]
};

type LocatedPoint = {
  renderedCellId: number;
  sourceCellId: number;
  pointIds: number[];
  weights: number[];
  pointMethod: StreamlineInterpolationMethod;
};

type Sample = {
  velocity: StreamlineVec3;
  fields: Record<string, number>;
  provenance: StreamlineInterpolationProvenance;
};

type CellBounds = {
  min: StreamlineVec3;
  max: StreamlineVec3;
};

type CellLocator = {
  bounds: CellBounds[];
  minimum: StreamlineVec3;
  maximum: StreamlineVec3;
  divisions: StreamlineVec3;
  buckets: Map<string, number[]>;
};

type ResolvedColorField = {
  location: "point" | "cell";
  kind: "scalar" | "vector";
  values: number[] | StreamlineVec3[];
};

function add(a: StreamlineVec3, b: StreamlineVec3): StreamlineVec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function subtract(a: StreamlineVec3, b: StreamlineVec3): StreamlineVec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function scale(a: StreamlineVec3, value: number): StreamlineVec3 {
  return [a[0] * value, a[1] * value, a[2] * value];
}

function dot(a: StreamlineVec3, b: StreamlineVec3) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: StreamlineVec3, b: StreamlineVec3): StreamlineVec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function magnitude(value: StreamlineVec3) {
  return Math.hypot(value[0], value[1], value[2]);
}

function finiteVec3(value: unknown): value is StreamlineVec3 {
  return Array.isArray(value) && value.length === 3 && value.every((component) => Number.isFinite(component));
}

function withinWeights(weights: number[]) {
  return weights.every((weight) => weight >= -EPSILON && weight <= 1 + EPSILON)
    && Math.abs(weights.reduce((sum, weight) => sum + weight, 0) - 1) <= 1e-7;
}

function triangleWeights(point: StreamlineVec3, a: StreamlineVec3, b: StreamlineVec3, c: StreamlineVec3): number[] | null {
  const v0 = subtract(b, a);
  const v1 = subtract(c, a);
  const v2 = subtract(point, a);
  const normal = cross(v0, v1);
  const normalMagnitude = magnitude(normal);
  if (normalMagnitude <= EPSILON) return null;
  if (Math.abs(dot(v2, normal)) / normalMagnitude > EPSILON * 10) return null;
  const d00 = dot(v0, v0);
  const d01 = dot(v0, v1);
  const d11 = dot(v1, v1);
  const d20 = dot(v2, v0);
  const d21 = dot(v2, v1);
  const denominator = d00 * d11 - d01 * d01;
  if (Math.abs(denominator) <= EPSILON) return null;
  const second = (d11 * d20 - d01 * d21) / denominator;
  const third = (d00 * d21 - d01 * d20) / denominator;
  const weights = [1 - second - third, second, third];
  return withinWeights(weights) ? weights : null;
}

function determinant(a: StreamlineVec3, b: StreamlineVec3, c: StreamlineVec3) {
  return dot(a, cross(b, c));
}

function tetraWeights(
  point: StreamlineVec3,
  a: StreamlineVec3,
  b: StreamlineVec3,
  c: StreamlineVec3,
  d: StreamlineVec3
): number[] | null {
  const ab = subtract(b, a);
  const ac = subtract(c, a);
  const ad = subtract(d, a);
  const ap = subtract(point, a);
  const denominator = determinant(ab, ac, ad);
  if (Math.abs(denominator) <= EPSILON) return null;
  const second = determinant(ap, ac, ad) / denominator;
  const third = determinant(ab, ap, ad) / denominator;
  const fourth = determinant(ab, ac, ap) / denominator;
  const weights = [1 - second - third - fourth, second, third, fourth];
  return withinWeights(weights) ? weights : null;
}

function boundsForCell(dataset: VtkResultDataset, cell: number[]): CellBounds {
  const points = cell.map((index) => dataset.points[index]);
  return {
    min: [0, 1, 2].map((axis) => Math.min(...points.map((point) => point[axis]))) as StreamlineVec3,
    max: [0, 1, 2].map((axis) => Math.max(...points.map((point) => point[axis]))) as StreamlineVec3
  };
}

function inBounds(point: StreamlineVec3, bounds: CellBounds) {
  return point.every((value, axis) => value >= bounds.min[axis] - EPSILON && value <= bounds.max[axis] + EPSILON);
}

function locatorAxisBucket(locator: CellLocator, value: number, axis: number) {
  const span = locator.maximum[axis] - locator.minimum[axis];
  const divisions = locator.divisions[axis];
  if (span <= EPSILON || divisions === 1) return 0;
  const normalized = (value - locator.minimum[axis]) / span;
  return Math.min(divisions - 1, Math.max(0, Math.floor(normalized * divisions)));
}

function locatorKey(x: number, y: number, z: number) {
  return `${x}:${y}:${z}`;
}

function buildCellLocator(dataset: VtkResultDataset): CellLocator {
  const { min: minimum, max: maximum } = pointExtent(dataset.points);
  const spans = maximum.map((value, axis) => value - minimum[axis]) as StreamlineVec3;
  const activeDimensions = Math.max(1, spans.filter((span) => span > EPSILON).length);
  const division = Math.min(64, Math.max(1, Math.ceil(dataset.cells.length ** (1 / activeDimensions))));
  const divisions = spans.map((span) => span > EPSILON ? division : 1) as StreamlineVec3;
  const locator: CellLocator = {
    bounds: dataset.cells.map((cell) => boundsForCell(dataset, cell)),
    minimum,
    maximum,
    divisions,
    buckets: new Map()
  };
  locator.bounds.forEach((bounds, renderedCellId) => {
    const lower = bounds.min.map((value, axis) => locatorAxisBucket(locator, value - EPSILON, axis));
    const upper = bounds.max.map((value, axis) => locatorAxisBucket(locator, value + EPSILON, axis));
    for (let z = lower[2]; z <= upper[2]; z += 1) {
      for (let y = lower[1]; y <= upper[1]; y += 1) {
        for (let x = lower[0]; x <= upper[0]; x += 1) {
          const key = locatorKey(x, y, z);
          const candidates = locator.buckets.get(key) ?? [];
          candidates.push(renderedCellId);
          locator.buckets.set(key, candidates);
        }
      }
    }
  });
  return locator;
}

function locatePoint(dataset: VtkResultDataset, locator: CellLocator, point: StreamlineVec3): LocatedPoint | null {
  if (point.some((value, axis) => value < locator.minimum[axis] - EPSILON || value > locator.maximum[axis] + EPSILON)) {
    return null;
  }
  const key = locatorKey(
    locatorAxisBucket(locator, point[0], 0),
    locatorAxisBucket(locator, point[1], 1),
    locatorAxisBucket(locator, point[2], 2)
  );
  for (const renderedCellId of locator.buckets.get(key) ?? []) {
    const cell = dataset.cells[renderedCellId];
    if (!inBounds(point, locator.bounds[renderedCellId])) continue;
    const cellType = dataset.cellTypes[renderedCellId];
    const sourceCellId = dataset.sourceCellIndices?.[renderedCellId];
    if (!Number.isInteger(sourceCellId)) continue;
    for (const localIds of TRIANGLE_DECOMPOSITIONS[cellType] ?? []) {
      const pointIds = localIds.map((index) => cell[index]);
      const weights = triangleWeights(point, dataset.points[pointIds[0]], dataset.points[pointIds[1]], dataset.points[pointIds[2]]);
      if (weights) {
        return {
          renderedCellId,
          sourceCellId: sourceCellId as number,
          pointIds,
          weights,
          pointMethod: "point-barycentric-triangle"
        };
      }
    }
    for (const localIds of TETRA_DECOMPOSITIONS[cellType] ?? []) {
      const pointIds = localIds.map((index) => cell[index]);
      const weights = tetraWeights(
        point,
        dataset.points[pointIds[0]],
        dataset.points[pointIds[1]],
        dataset.points[pointIds[2]],
        dataset.points[pointIds[3]]
      );
      if (weights) {
        return {
          renderedCellId,
          sourceCellId: sourceCellId as number,
          pointIds,
          weights,
          pointMethod: "point-barycentric-tetra-decomposition"
        };
      }
    }
  }
  return null;
}

function interpolateScalar(values: number[], location: "point" | "cell", located: LocatedPoint) {
  if (location === "cell") return values[located.renderedCellId];
  return located.pointIds.reduce((sum, pointId, index) => sum + values[pointId] * located.weights[index], 0);
}

function interpolateVector(
  values: StreamlineVec3[],
  location: "point" | "cell",
  located: LocatedPoint
): StreamlineVec3 {
  if (location === "cell") return values[located.renderedCellId];
  return located.pointIds.reduce<StreamlineVec3>(
    (sum, pointId, index) => add(sum, scale(values[pointId], located.weights[index])),
    [0, 0, 0]
  );
}

function findNamedField<T>(fields: Record<string, T>, aliases: string[]): [string, T] | null {
  for (const alias of aliases) {
    const key = Object.keys(fields).find((candidate) => candidate.toLowerCase() === alias.toLowerCase());
    if (key) return [key, fields[key]];
  }
  return null;
}

function velocityField(dataset: VtkResultDataset): { name: string; location: "point" | "cell"; values: StreamlineVec3[] } | null {
  const point = findNamedField(dataset.pointData.vectors, ["U", "velocity", "vel"]);
  if (point) return { name: point[0], location: "point", values: point[1] };
  const cell = findNamedField(dataset.cellData.vectors, ["U", "velocity", "vel"]);
  return cell ? { name: cell[0], location: "cell", values: cell[1] } : null;
}

const COLOR_FIELDS = {
  pressure: ["p", "p_rgh", "pressure", "static_pressure"],
  temperature: ["T", "temperature", "temp"],
  phase: ["alpha", "alpha.water", "phase", "phase_fraction"],
  vorticity: ["vorticity", "omega"]
} as const;

function resolveColorFields(dataset: VtkResultDataset): Partial<Record<keyof typeof COLOR_FIELDS, ResolvedColorField>> {
  const resolved: Partial<Record<keyof typeof COLOR_FIELDS, ResolvedColorField>> = {};
  (Object.keys(COLOR_FIELDS) as Array<keyof typeof COLOR_FIELDS>).forEach((name) => {
    const aliases = [...COLOR_FIELDS[name]];
    for (const location of ["point", "cell"] as const) {
      const data = location === "point" ? dataset.pointData : dataset.cellData;
      const scalar = findNamedField(data.scalars, aliases);
      if (scalar) {
        resolved[name] = { location, kind: "scalar", values: scalar[1] };
        return;
      }
      const vector = findNamedField(data.vectors, aliases);
      if (vector) {
        resolved[name] = { location, kind: "vector", values: vector[1] };
        return;
      }
    }
  });
  return resolved;
}

function explicitFieldValue(field: ResolvedColorField, located: LocatedPoint): number {
  if (field.kind === "scalar") {
    return interpolateScalar(field.values as number[], field.location, located);
  }
  return magnitude(interpolateVector(field.values as StreamlineVec3[], field.location, located));
}

function velocitySample(
  dataset: VtkResultDataset,
  locator: CellLocator,
  velocity: NonNullable<ReturnType<typeof velocityField>>,
  point: StreamlineVec3
): { located: LocatedPoint; vector: StreamlineVec3 } | null {
  const located = locatePoint(dataset, locator, point);
  if (!located) return null;
  return { located, vector: interpolateVector(velocity.values, velocity.location, located) };
}

function sampleAt(
  dataset: VtkResultDataset,
  locator: CellLocator,
  velocity: NonNullable<ReturnType<typeof velocityField>>,
  colorFields: ReturnType<typeof resolveColorFields>,
  point: StreamlineVec3
): Sample | null {
  const sampledVelocity = velocitySample(dataset, locator, velocity, point);
  if (!sampledVelocity) return null;
  const { located, vector } = sampledVelocity;
  const speed = magnitude(vector);
  const fields: Record<string, number> = { velocity: speed };
  (Object.entries(colorFields) as Array<[keyof typeof COLOR_FIELDS, ResolvedColorField]>).forEach(([name, field]) => {
    const value = explicitFieldValue(field, located);
    if (Number.isFinite(value)) fields[name] = value;
  });
  return {
    velocity: vector,
    fields,
    provenance: {
      renderedCellId: located.renderedCellId,
      sourceCellId: located.sourceCellId,
      pointIds: velocity.location === "point" ? located.pointIds : [],
      weights: velocity.location === "point" ? located.weights : [1],
      method: velocity.location === "point" ? located.pointMethod : "cell-piecewise-constant"
    }
  };
}

function normalizedDirection(velocity: StreamlineVec3): StreamlineVec3 | null {
  const speed = magnitude(velocity);
  return speed <= ZERO_SPEED ? null : scale(velocity, 1 / speed);
}

function rk4Step(
  dataset: VtkResultDataset,
  locator: CellLocator,
  velocity: NonNullable<ReturnType<typeof velocityField>>,
  point: StreamlineVec3,
  stepSize: number
): StreamlineVec3 | null {
  const first = velocitySample(dataset, locator, velocity, point);
  const k1 = first && normalizedDirection(first.vector);
  if (!k1) return null;
  const second = velocitySample(dataset, locator, velocity, add(point, scale(k1, stepSize / 2)));
  const k2 = second && normalizedDirection(second.vector);
  if (!k2) return null;
  const third = velocitySample(dataset, locator, velocity, add(point, scale(k2, stepSize / 2)));
  const k3 = third && normalizedDirection(third.vector);
  if (!k3) return null;
  const fourth = velocitySample(dataset, locator, velocity, add(point, scale(k3, stepSize)));
  const k4 = fourth && normalizedDirection(fourth.vector);
  if (!k4) return null;
  return add(
    point,
    scale(
      [
        k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0],
        k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1],
        k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]
      ],
      stepSize / 6
    )
  );
}

function setFinalTermination(vertices: StreamlineVertex[], reason: StreamlineTerminationReason) {
  if (vertices.length > 0) vertices[vertices.length - 1].terminationReason = reason;
}

function defaultStepSize(dataset: VtkResultDataset) {
  const spans = pointSpans(dataset.points);
  return maximumOf(spans, EPSILON) / 200;
}

export function assertFullStreamlineDataset(dataset: VtkResultDataset) {
  if (
    dataset.sourceCellCount !== dataset.cells.length
    || dataset.sourceCellIndices?.length !== dataset.cells.length
    || dataset.sourceCellIndices.some((sourceId, renderedId) => sourceId !== renderedId)
  ) {
    throw new Error("Full result required.");
  }
  if (
    dataset.cells.length === 0
    || dataset.points.length === 0
    || dataset.cellTypes.length !== dataset.cells.length
    || dataset.cells.some((cell, index) => {
      const expected = CELL_POINT_COUNTS[dataset.cellTypes[index]];
      return expected === undefined || cell.length !== expected || cell.some((pointId) => pointId < 0 || pointId >= dataset.points.length);
    })
  ) {
    throw new Error("Complete supported topology required.");
  }
}

export function generatePlaneSeeds(plane: StreamlineSeedPlane): StreamlineVec3[] {
  const countU = Math.max(1, Math.floor(plane.countU));
  const countV = Math.max(1, Math.floor(plane.countV));
  if (countU * countV > STREAMLINE_LIMITS.maxSeeds) {
    throw new Error(`Seed count exceeds ${STREAMLINE_LIMITS.maxSeeds}.`);
  }
  const seeds: StreamlineVec3[] = [];
  for (let v = 0; v < countV; v += 1) {
    for (let u = 0; u < countU; u += 1) {
      const fractionU = countU === 1 ? 0.5 : u / (countU - 1);
      const fractionV = countV === 1 ? 0.5 : v / (countV - 1);
      seeds.push(add(plane.origin, add(scale(plane.axisU, fractionU), scale(plane.axisV, fractionV))));
    }
  }
  return seeds;
}

export function datasetSeedPlane(
  dataset: VtkResultDataset,
  normalAxis: 0 | 1 | 2,
  normalizedPosition: number,
  seedCount: number = STREAMLINE_LIMITS.defaultSeeds
): StreamlineSeedPlane {
  const { min: minimum, max: maximum } = pointExtent(dataset.points);
  const spans = maximum.map((value, axis) => value - minimum[axis]) as StreamlineVec3;
  const tangents = ([0, 1, 2] as const).filter((axis) => axis !== normalAxis && spans[axis] > EPSILON);
  const firstAxis = tangents[0] ?? ((normalAxis + 1) % 3 as 0 | 1 | 2);
  const secondAxis = tangents[1];
  const boundedCount = Math.min(Math.max(1, Math.floor(seedCount)), STREAMLINE_LIMITS.maxSeeds);
  const countV = secondAxis === undefined ? 1 : Math.max(1, Math.floor(Math.sqrt(boundedCount)));
  const countU = Math.max(1, Math.floor(boundedCount / countV));
  const origin: StreamlineVec3 = [...minimum];
  origin[normalAxis] = minimum[normalAxis] + spans[normalAxis] * Math.max(1e-6, Math.min(1 - 1e-6, normalizedPosition));
  const axisU: StreamlineVec3 = [0, 0, 0];
  const axisV: StreamlineVec3 = [0, 0, 0];
  const inset = 0.02;
  origin[firstAxis] = minimum[firstAxis] + spans[firstAxis] * inset;
  axisU[firstAxis] = spans[firstAxis] * (1 - inset * 2);
  if (secondAxis !== undefined) {
    origin[secondAxis] = minimum[secondAxis] + spans[secondAxis] * inset;
    axisV[secondAxis] = spans[secondAxis] * (1 - inset * 2);
  }
  return { origin, axisU, axisV, countU, countV };
}

export function datasetSpatialDimension(dataset: VtkResultDataset): 2 | 3 {
  const spans = pointSpans(dataset.points);
  return spans.filter((span) => span > EPSILON).length <= 2 ? 2 : 3;
}

export function integrateSteadyStreamlines(
  request: StreamlineRequest,
  shouldCancel: () => boolean = () => false
): StreamlineResult {
  const { dataset } = request;
  assertFullStreamlineDataset(dataset);
  if (request.seeds.length > STREAMLINE_LIMITS.maxSeeds) {
    throw new Error(`Seed count exceeds ${STREAMLINE_LIMITS.maxSeeds}.`);
  }
  if (!request.seeds.every(finiteVec3)) throw new Error("Streamline seeds must be finite XYZ coordinates.");
  const velocity = velocityField(dataset);
  if (!velocity) throw new Error("A loaded U/velocity vector field is required.");
  const locator = buildCellLocator(dataset);
  const colorFields = resolveColorFields(dataset);
  const stepSize = request.stepSize ?? defaultStepSize(dataset);
  if (!Number.isFinite(stepSize) || stepSize <= 0) throw new Error("Streamline step size must be positive.");
  const perLineLimit = Math.min(
    Math.max(1, Math.floor(request.maxVerticesPerLine ?? STREAMLINE_LIMITS.maxVerticesPerLine)),
    STREAMLINE_LIMITS.maxVerticesPerLine
  );
  const totalLimit = Math.min(
    Math.max(1, Math.floor(request.maxTotalVertices ?? STREAMLINE_LIMITS.maxTotalVertices)),
    STREAMLINE_LIMITS.maxTotalVertices
  );
  const lines: StreamlineLine[] = [];
  let vertexCount = 0;

  for (let seedIndex = 0; seedIndex < request.seeds.length; seedIndex += 1) {
    const vertices: StreamlineVertex[] = [];
    let point = request.seeds[seedIndex];
    let terminationReason: StreamlineTerminationReason = "max-vertices";
    while (vertices.length < perLineLimit) {
      if (shouldCancel()) {
        terminationReason = "cancelled";
        break;
      }
      if (vertexCount >= totalLimit) {
        terminationReason = "total-vertex-limit";
        break;
      }
      const sample = sampleAt(dataset, locator, velocity, colorFields, point);
      if (!sample) {
        terminationReason = vertices.length === 0 ? "seed-outside-domain" : "domain-exit";
        break;
      }
      const speed = magnitude(sample.velocity);
      vertices.push({
        position: [...point],
        velocity: [...sample.velocity],
        speed,
        fields: sample.fields,
        provenance: sample.provenance,
        terminationReason: "active"
      });
      vertexCount += 1;
      if (speed <= ZERO_SPEED) {
        terminationReason = "zero-velocity";
        break;
      }
      const nextPoint = rk4Step(dataset, locator, velocity, point, stepSize);
      if (!nextPoint || !velocitySample(dataset, locator, velocity, nextPoint)) {
        terminationReason = "domain-exit";
        break;
      }
      point = nextPoint;
    }
    setFinalTermination(vertices, terminationReason);
    lines.push({ seedIndex, vertices, terminationReason });
    if (terminationReason === "cancelled" || terminationReason === "total-vertex-limit") break;
  }

  return {
    schema: "flowlab.steady_streamlines.v1",
    terminology: "steady-streamline",
    sourceName: dataset.sourceName ?? "loaded result",
    sourceIdentity: request.sourceIdentity ?? "artifact-local-unlinked",
    spatialDimension: datasetSpatialDimension(dataset),
    velocityField: velocity.name,
    velocityLocation: velocity.location,
    velocityInterpolation: velocity.location === "point" ? "barycentric point field" : "piecewise constant cell field",
    fieldInterpolations: {
      velocity: velocity.location === "point" ? "barycentric point field" : "piecewise constant cell field",
      ...Object.fromEntries(
        Object.entries(colorFields).map(([name, field]) => [
          name,
          field?.location === "point" ? "barycentric point field" : "piecewise constant cell field"
        ])
      )
    },
    lines,
    seedCount: request.seeds.length,
    vertexCount,
    limits: STREAMLINE_LIMITS
  };
}
