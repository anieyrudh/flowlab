import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { FluidProject, Vec2, VtkResultDataset } from "../types";
import {
  DEFAULT_RESULT_WORLD_SPAN,
  RESULT_SURFACE_Z_OFFSET,
  createCinemaLightRig,
  createResultBoundaryMaterial,
  createResultSurfaceMaterial,
  createSchematicPipeMaterials,
  exteriorTriangleCount,
  extractExteriorCellFaces,
  resultSurfaceTriangles,
  resultWorldSpanForNetwork
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

  it("renders a five-block O-grid-like volume without exposing four internal interfaces", () => {
    const points: [number, number, number][] = [];
    const pointIndex = new Map<string, number>();
    const indexFor = (point: [number, number, number]) => {
      const key = point.join(",");
      const existing = pointIndex.get(key);
      if (existing !== undefined) return existing;
      const index = points.length;
      points.push(point);
      pointIndex.set(key, index);
      return index;
    };
    const block = (y0: number, y1: number, z0: number, z1: number) =>
      [
        [0, y0, z0],
        [1, y0, z0],
        [1, y1, z0],
        [0, y1, z0],
        [0, y0, z1],
        [1, y0, z1],
        [1, y1, z1],
        [0, y1, z1]
      ].map((point) => indexFor(point as [number, number, number]));
    const volume = dataset(
      points,
      [
        block(0, 1, 0, 1),
        block(1, 2, 0, 1),
        block(-1, 0, 0, 1),
        block(0, 1, 1, 2),
        block(0, 1, -1, 0)
      ],
      [12, 12, 12, 12, 12]
    );

    const faces = extractExteriorCellFaces(volume);
    expect(faces).toHaveLength(22);
    expect(exteriorTriangleCount(volume)).toBe(44);
    expect(new Set(faces.map((face) => face.ownerCellIndex))).toEqual(new Set([0, 1, 2, 3, 4]));
    expect([0, 1, 2].map((axis) => Math.max(...points.map((point) => point[axis])) - Math.min(...points.map((point) => point[axis])))).toEqual([1, 3, 3]);
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

/** Widest gap between any two channels of a colour: 0 is perfectly neutral. */
function channelSpread(color: THREE.Color) {
  return Math.max(color.r, color.g, color.b) - Math.min(color.r, color.g, color.b);
}

describe("Cinema light rig", () => {
  const rig = createCinemaLightRig();
  const lights = Object.values(rig);

  it("uses no positional lights, so shading never depends on where a surface sits", () => {
    expect(lights.some((light) => light instanceof THREE.PointLight)).toBe(false);
    expect(lights.some((light) => light instanceof THREE.SpotLight)).toBe(false);
    const positional = lights.filter((light) => light instanceof THREE.DirectionalLight);
    expect(positional).toHaveLength(3);
    expect(rig.ambient).toBeInstanceOf(THREE.HemisphereLight);
  });

  it("keeps every light neutral so hue stays in the materials", () => {
    lights.forEach((light) => {
      expect(channelSpread(light.color)).toBeLessThanOrEqual(0.08);
    });
    expect(channelSpread(rig.ambient.groundColor)).toBeLessThanOrEqual(0.08);
    [rig.key, rig.fill, rig.rim].forEach((light) => {
      expect(light.color.getHex()).toBe(0xffffff);
    });
  });

  it("reads as a key plus softer fill and rim", () => {
    expect(rig.key.intensity).toBeGreaterThan(rig.fill.intensity);
    expect(rig.fill.intensity).toBeGreaterThan(rig.rim.intensity);
    expect(rig.rim.intensity).toBeGreaterThan(0);
    expect(rig.ambient.intensity).toBeGreaterThan(0);
  });

  it("lights the XY network plane from above, with the hemisphere axis on +Z", () => {
    expect(rig.key.position.z).toBeGreaterThan(0);
    expect(rig.fill.position.z).toBeGreaterThan(0);
    expect(rig.key.position.z).toBeGreaterThan(Math.abs(rig.key.position.x));
    // The key and fill straddle the network in X so form is described from both sides.
    expect(Math.sign(rig.key.position.x)).toBe(-Math.sign(rig.fill.position.x));
    expect(rig.ambient.position.clone().normalize().z).toBeCloseTo(1, 6);
  });
});

describe("Solved domain presentation", () => {
  it("shows solver output unlit, opaque and un-fogged so pixels equal colour-map colours", () => {
    const material = createResultSurfaceMaterial();

    expect(material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(material.vertexColors).toBe(true);
    expect(material.transparent).toBe(false);
    expect(material.opacity).toBe(1);
    expect(material.fog).toBe(false);
    expect(material.toneMapped).toBe(false);
    expect(material.depthWrite).toBe(true);
  });

  it("draws a boundary that is not washed out by fog or tone mapping", () => {
    const material = createResultBoundaryMaterial();

    expect(material.fog).toBe(false);
    expect(material.toneMapped).toBe(false);
    expect(material.opacity).toBeGreaterThan(0.8);
  });

  it("sits below the drawn network plane so the schematic never hides the data", () => {
    expect(RESULT_SURFACE_Z_OFFSET).toBeLessThan(0);
  });
});

describe("Schematic network presentation", () => {
  const solved = createResultSurfaceMaterial();

  it.each([
    { name: "idle", active: false },
    { name: "selected", active: true }
  ])("draws the $name drawn pipe as a ghosted wireframe cage", ({ active }) => {
    const { cage, core } = createSchematicPipeMaterials(new THREE.Color(0x00ff00), active);

    expect(cage.wireframe).toBe(true);
    expect(cage.transparent).toBe(true);
    expect(core.transparent).toBe(true);
  });

  it.each([
    { name: "idle", active: false },
    { name: "selected", active: true }
  ])("never lets the $name illustration read as solidly as solver output", ({ active }) => {
    const { cage, core } = createSchematicPipeMaterials(new THREE.Color(0x00ff00), active);

    expect(cage.opacity).toBeLessThan(solved.opacity);
    expect(core.opacity).toBeLessThan(solved.opacity);
    // Emissive would let the illustration glow past the unlit solved surface.
    expect(core.emissiveIntensity).toBeLessThan(1);
  });

  it("keeps the drawn pipe core on the network overlay colour", () => {
    const overlay = new THREE.Color(0x0ad7ff);

    expect(createSchematicPipeMaterials(overlay, false).core.color.getHex()).toBe(overlay.getHex());
  });
});

describe("Solved domain scale", () => {
  const project = (positions: Vec2[]) =>
    ({
      nodes: Object.fromEntries(positions.map((position, index) => [`n${index}`, { id: `n${index}`, position }]))
    }) as unknown as FluidProject;

  it("matches the extent of the drawn network so both read at a comparable size", () => {
    const span = resultWorldSpanForNetwork(project([{ x: 0, y: 0 }, { x: 370, y: 120 }]), 74);

    expect(span).toBeCloseTo((370 / 74) * 0.92, 6);
  });

  it("clamps a tiny network up and a sprawling one down", () => {
    const tiny = resultWorldSpanForNetwork(project([{ x: 0, y: 0 }, { x: 10, y: 0 }]), 74);
    const sprawling = resultWorldSpanForNetwork(project([{ x: 0, y: 0 }, { x: 4000, y: 0 }]), 74);

    expect(tiny).toBeGreaterThan(10 / 74);
    expect(tiny).toBeLessThan(sprawling);
    expect(sprawling).toBeLessThan(2 * DEFAULT_RESULT_WORLD_SPAN);
  });

  it.each([
    { name: "no nodes", positions: [] as Vec2[] },
    { name: "a single node", positions: [{ x: 40, y: 40 }] },
    { name: "coincident nodes", positions: [{ x: 40, y: 40 }, { x: 40, y: 40 }] }
  ])("falls back to the default extent for $name", ({ positions }) => {
    expect(resultWorldSpanForNetwork(project(positions), 74)).toBe(DEFAULT_RESULT_WORLD_SPAN);
  });
});
