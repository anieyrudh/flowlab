import { describe, expect, it } from "vitest";
import type { VtkResultDataset } from "../types";
import {
  exteriorTriangleCount,
  extractExteriorCellFaces,
  resultSurfaceTriangles
} from "./cinemaRenderer";

function dataset(
  points: [number, number, number][],
  cells: number[][],
  cellTypes: number[]
): VtkResultDataset {
  return {
    format: "legacy-vtk-ascii-v1",
    points,
    cells,
    cellTypes,
    pointData: { scalars: {}, vectors: {} },
    cellData: { scalars: {}, vectors: {} },
    fields: []
  };
}

const hexPoints: [number, number, number][] = [
  [0, 0, 0],
  [1, 0, 0],
  [1, 1, 0],
  [0, 1, 0],
  [0, 0, 1],
  [1, 0, 1],
  [1, 1, 1],
  [0, 1, 1]
];

describe("Cinema VTK exterior surface extraction", () => {
  it("extracts six faces and twelve triangles from one hexahedron", () => {
    const volume = dataset(hexPoints, [[0, 1, 2, 3, 4, 5, 6, 7]], [12]);

    expect(extractExteriorCellFaces(volume)).toHaveLength(6);
    expect(exteriorTriangleCount(volume)).toBe(12);
    expect(resultSurfaceTriangles(volume)).toHaveLength(12);
    expect(resultSurfaceTriangles(volume).every((triangle) => triangle.ownerCellIndex === 0)).toBe(true);
    expect(resultSurfaceTriangles(volume).every((triangle) => triangle.pointIndices.length === 3)).toBe(true);
  });

  it("removes the shared face between two adjacent hexahedra", () => {
    const points: [number, number, number][] = [
      ...hexPoints,
      [2, 0, 0],
      [2, 1, 0],
      [2, 0, 1],
      [2, 1, 1]
    ];
    const volume = dataset(
      points,
      [
        [0, 1, 2, 3, 4, 5, 6, 7],
        [1, 8, 9, 2, 5, 10, 11, 6]
      ],
      [12, 12]
    );

    const faces = extractExteriorCellFaces(volume);
    expect(faces).toHaveLength(10);
    expect(exteriorTriangleCount(volume)).toBe(20);
    expect(new Set(faces.map((face) => face.ownerCellIndex))).toEqual(new Set([0, 1]));
    expect(new Set(resultSurfaceTriangles(volume).map((triangle) => triangle.ownerCellIndex))).toEqual(
      new Set([0, 1])
    );
  });

  it.each([
    {
      name: "tetrahedron",
      points: [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1]
      ] as [number, number, number][],
      cell: [0, 1, 2, 3],
      type: 10,
      faces: 4,
      triangles: 4
    },
    {
      name: "wedge",
      points: [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [1, 0, 1],
        [0, 1, 1]
      ] as [number, number, number][],
      cell: [0, 1, 2, 3, 4, 5],
      type: 13,
      faces: 5,
      triangles: 8
    },
    {
      name: "pyramid",
      points: [
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
        [0.5, 0.5, 1]
      ] as [number, number, number][],
      cell: [0, 1, 2, 3, 4],
      type: 14,
      faces: 5,
      triangles: 6
    }
  ])("extracts the exterior of a $name", ({ points, cell, type, faces, triangles }) => {
    const volume = dataset(points, [cell], [type]);

    expect(extractExteriorCellFaces(volume)).toHaveLength(faces);
    expect(exteriorTriangleCount(volume)).toBe(triangles);
  });
});
