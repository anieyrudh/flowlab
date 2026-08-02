import { describe, expect, it } from "vitest";
import type { VtkResultDataset } from "../types";
import {
  assertFullStreamlineDataset,
  datasetSeedPlane,
  generatePlaneSeeds,
  integrateSteadyStreamlines
} from "./core";

type Point = [number, number, number];

function dataset(points: Point[], cells: number[][], cellTypes: number[], velocity?: Point[]): VtkResultDataset {
  return {
    format: "legacy-vtk-ascii-v1",
    points,
    cells,
    cellTypes,
    pointData: {
      scalars: {
        p: points.map((point) => point[0] + point[1] + point[2]),
        T: points.map((_point, index) => 290 + index),
        "alpha.water": points.map((_point, index) => index / Math.max(points.length - 1, 1))
      },
      vectors: {
        U: velocity ?? points.map(() => [1, 0, 0] as Point),
        vorticity: points.map(() => [0, 0, 2] as Point)
      }
    },
    cellData: { scalars: {}, vectors: {} },
    fields: ["U", "p", "T", "alpha.water", "vorticity"],
    sourceCellIndices: cells.map((_cell, index) => index),
    sourceCellCount: cells.length,
    sourceName: "analytic-full.vtk"
  };
}

const supportedCells = [
  {
    name: "planar triangle",
    points: [[0, 0, 0], [1, 0, 0], [0, 1, 0]] as Point[],
    cell: [0, 1, 2],
    type: 5,
    seed: [0.2, 0.2, 0] as Point,
    method: "point-barycentric-triangle"
  },
  {
    name: "planar quad",
    points: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]] as Point[],
    cell: [0, 1, 2, 3],
    type: 9,
    seed: [0.2, 0.2, 0] as Point,
    method: "point-barycentric-triangle"
  },
  {
    name: "tetrahedron",
    points: [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]] as Point[],
    cell: [0, 1, 2, 3],
    type: 10,
    seed: [0.1, 0.1, 0.1] as Point,
    method: "point-barycentric-tetra-decomposition"
  },
  {
    name: "hexahedron",
    points: [
      [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
      [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]
    ] as Point[],
    cell: [0, 1, 2, 3, 4, 5, 6, 7],
    type: 12,
    seed: [0.2, 0.2, 0.2] as Point,
    method: "point-barycentric-tetra-decomposition"
  },
  {
    name: "wedge",
    points: [
      [0, 0, 0], [1, 0, 0], [0, 1, 0],
      [0, 0, 1], [1, 0, 1], [0, 1, 1]
    ] as Point[],
    cell: [0, 1, 2, 3, 4, 5],
    type: 13,
    seed: [0.1, 0.1, 0.2] as Point,
    method: "point-barycentric-tetra-decomposition"
  },
  {
    name: "pyramid",
    points: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 1]] as Point[],
    cell: [0, 1, 2, 3, 4],
    type: 14,
    seed: [0.4, 0.4, 0.2] as Point,
    method: "point-barycentric-tetra-decomposition"
  }
] as const;

describe("steady solver-derived streamlines", () => {
  it.each(supportedCells)("integrates through a $name with explicit cell and weight provenance", ({ points, cell, type, seed, method }) => {
    const result = integrateSteadyStreamlines({
      dataset: dataset([...points], [[...cell]], [type]),
      seeds: [[...seed]],
      stepSize: 0.01,
      maxVerticesPerLine: 2
    });
    const vertex = result.lines[0].vertices[0];

    expect(vertex.provenance).toMatchObject({
      renderedCellId: 0,
      sourceCellId: 0,
      method
    });
    expect(vertex.provenance.weights.reduce((sum, weight) => sum + weight, 0)).toBeCloseTo(1, 10);
    expect(vertex.terminationReason).toBe("active");
    expect(result.terminology).toBe("steady-streamline");
  });

  it("reproduces constant flow with deterministic RK4 steps and field colouring samples", () => {
    const volume = dataset(
      [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
      [[0, 1, 2, 3]],
      [9]
    );
    const first = integrateSteadyStreamlines({ dataset: volume, seeds: [[0.1, 0.5, 0]], stepSize: 0.1, maxVerticesPerLine: 5 });
    const second = integrateSteadyStreamlines({ dataset: volume, seeds: [[0.1, 0.5, 0]], stepSize: 0.1, maxVerticesPerLine: 5 });

    expect(second).toEqual(first);
    first.lines[0].vertices.forEach((vertex, index) => {
      expect(vertex.position[0]).toBeCloseTo(0.1 + index * 0.1, 12);
      expect(Number.isInteger(vertex.provenance.renderedCellId)).toBe(true);
      expect(Number.isInteger(vertex.provenance.sourceCellId)).toBe(true);
      expect(vertex.provenance.weights.reduce((sum, weight) => sum + weight, 0)).toBeCloseTo(1, 10);
      expect(vertex.terminationReason).toBe(index === first.lines[0].vertices.length - 1 ? "max-vertices" : "active");
    });
    expect(first.lines[0].vertices[0].fields).toMatchObject({
      velocity: 1,
      pressure: 0.6,
      vorticity: 2
    });
    expect(first.spatialDimension).toBe(2);
  });

  it("tracks a solid-body rotation analytic field", () => {
    const points: Point[] = [[-2, -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]];
    const velocity = points.map(([x, y]) => [-y, x, 0] as Point);
    const result = integrateSteadyStreamlines({
      dataset: dataset(points, [[0, 1, 2, 3]], [9], velocity),
      seeds: [[1, 0, 0]],
      stepSize: 0.02,
      maxVerticesPerLine: 80
    });
    const radialErrors = result.lines[0].vertices.map((vertex) => Math.abs(Math.hypot(vertex.position[0], vertex.position[1]) - 1));

    expect(Math.max(...radialErrors)).toBeLessThan(2e-4);
  });

  it("labels cell velocity interpolation as piecewise constant", () => {
    const volume = dataset(
      [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
      [[0, 1, 2, 3]],
      [9]
    );
    volume.pointData.vectors = {};
    delete volume.pointData.scalars.p;
    volume.cellData.vectors.U = [[2, 0, 0]];
    volume.cellData.scalars.p = [42];
    const result = integrateSteadyStreamlines({ dataset: volume, seeds: [[0.2, 0.5, 0]], stepSize: 0.1, maxVerticesPerLine: 2 });

    expect(result.velocityInterpolation).toBe("piecewise constant cell field");
    expect(result.fieldInterpolations).toMatchObject({
      velocity: "piecewise constant cell field",
      pressure: "piecewise constant cell field"
    });
    expect(result.lines[0].vertices[0].fields.pressure).toBe(42);
    expect(result.lines[0].vertices[0].provenance).toMatchObject({
      method: "cell-piecewise-constant",
      weights: [1],
      pointIds: []
    });
  });

  it("fails closed for thinned previews and incomplete topology", () => {
    const preview = dataset(
      [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
      [[0, 1, 2]],
      [5]
    );
    preview.sourceCellIndices = [6];
    preview.sourceCellCount = 8;
    expect(() => assertFullStreamlineDataset(preview)).toThrow("Full result required.");

    const incomplete = dataset([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1]], [5]);
    expect(() => assertFullStreamlineDataset(incomplete)).toThrow("Complete supported topology required.");
  });

  it("keeps imported full artifacts explicitly unlinked while retaining artifact-local source IDs", () => {
    const result = integrateSteadyStreamlines({
      dataset: dataset([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [5]),
      seeds: [[0.1, 0.1, 0]],
      maxVerticesPerLine: 1,
      sourceIdentity: "artifact-local-unlinked"
    });

    expect(result.sourceIdentity).toBe("artifact-local-unlinked");
    expect(result.lines[0].vertices[0].provenance.sourceCellId).toBe(0);
  });

  it("records zero velocity, domain exit, vertex cap, and total cap termination", () => {
    const volume = dataset([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [5]);
    const outside = integrateSteadyStreamlines({ dataset: volume, seeds: [[2, 2, 0]] });
    expect(outside.lines[0]).toMatchObject({ terminationReason: "seed-outside-domain", vertices: [] });

    volume.pointData.vectors.U = volume.points.map(() => [0, 0, 0]);
    const stopped = integrateSteadyStreamlines({ dataset: volume, seeds: [[0.1, 0.1, 0]] });
    expect(stopped.lines[0].vertices.at(-1)?.terminationReason).toBe("zero-velocity");

    volume.pointData.vectors.U = volume.points.map(() => [1, 0, 0]);
    const capped = integrateSteadyStreamlines({ dataset: volume, seeds: [[0.1, 0.1, 0]], stepSize: 0.01, maxVerticesPerLine: 2 });
    expect(capped.lines[0].terminationReason).toBe("max-vertices");
    expect(capped.lines[0].vertices.at(-1)?.terminationReason).toBe("max-vertices");

    const total = integrateSteadyStreamlines({
      dataset: volume,
      seeds: [[0.1, 0.1, 0], [0.1, 0.2, 0]],
      stepSize: 0.01,
      maxTotalVertices: 1
    });
    expect(total.lines[0].terminationReason).toBe("total-vertex-limit");
  });

  it("records cancellation deterministically at the last retained vertex", () => {
    const volume = dataset([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [5]);
    let checks = 0;
    const cancelled = integrateSteadyStreamlines(
      { dataset: volume, seeds: [[0.1, 0.1, 0]], stepSize: 0.01 },
      () => checks++ > 0
    );

    expect(cancelled.lines[0].terminationReason).toBe("cancelled");
    expect(cancelled.lines[0].vertices).toHaveLength(1);
    expect(cancelled.lines[0].vertices[0].terminationReason).toBe("cancelled");
  });

  it("generates deterministic user-plane seeds and enforces the 256-seed cap", () => {
    expect(generatePlaneSeeds({
      origin: [0, -1, -1],
      axisU: [0, 2, 0],
      axisV: [0, 0, 2],
      countU: 2,
      countV: 2
    })).toEqual([[0, -1, -1], [0, 1, -1], [0, -1, 1], [0, 1, 1]]);
    expect(() => generatePlaneSeeds({
      origin: [0, 0, 0],
      axisU: [1, 0, 0],
      axisV: [0, 1, 0],
      countU: 17,
      countV: 16
    })).toThrow("Seed count exceeds 256.");
  });

  it("builds a bounded user plane from loaded result bounds", () => {
    const volume = dataset(
      [[0, -1, -2], [10, -1, -2], [10, 1, -2], [0, 1, -2], [0, -1, 2], [10, -1, 2], [10, 1, 2], [0, 1, 2]],
      [[0, 1, 2, 3, 4, 5, 6, 7]],
      [12]
    );
    const plane = datasetSeedPlane(volume, 0, 0.25, 64);
    expect(plane).toMatchObject({ origin: [2.5, -0.96, -1.92], countU: 8, countV: 8 });
    expect(generatePlaneSeeds(plane)).toHaveLength(64);
  });
});
