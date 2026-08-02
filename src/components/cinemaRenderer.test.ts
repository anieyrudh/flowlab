import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { FluidProject, Vec2, VtkResultDataset } from "../types";
import {
  CINEMA_VIEW_HEIGHT,
  DEFAULT_RESULT_WORLD_SPAN,
  RESULT_SURFACE_Z_OFFSET,
  applyCinemaCamera,
  buildSweptTubeGeometry,
  cinemaFitZoom,
  cinemaOrthographicFrustum,
  clampCinemaZoom,
  createCinemaAmbientLight,
  createCinemaCamera,
  createConstantOutlineMaterial,
  createConstantSurfaceMaterial,
  createResultBoundaryMaterial,
  createResultSurfaceMaterial,
  createSchematicPipeMaterials,
  exteriorTriangleCount,
  extractExteriorCellFaces,
  resultSurfaceTriangles,
  resultWorldSpanForNetwork,
  steppedTone
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

describe("Constant shading", () => {
  it("lights the scene with one omnidirectional source, so no shading can depend on orientation", () => {
    const ambient = createCinemaAmbientLight();

    expect(ambient).toBeInstanceOf(THREE.AmbientLight);
    expect(ambient).not.toBeInstanceOf(THREE.DirectionalLight);
    expect(ambient).not.toBeInstanceOf(THREE.HemisphereLight);
    expect(ambient.intensity).toBeGreaterThan(0);
  });

  it("keeps the light neutral so hue stays in the materials", () => {
    const ambient = createCinemaAmbientLight();

    expect(channelSpread(ambient.color)).toBe(0);
    expect(ambient.color.getHex()).toBe(0xffffff);
  });

  it("draws network surfaces unlit and un-fogged, so a pixel depends only on its material", () => {
    const material = createConstantSurfaceMaterial({ color: 0x3f939a, opacity: 0.5 });

    expect(material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(material.fog).toBe(false);
    expect(material.transparent).toBe(true);
    expect(createConstantSurfaceMaterial({ color: 0x3f939a }).transparent).toBe(false);
    expect(createConstantOutlineMaterial(0xffffff, 0.5).fog).toBe(false);
  });

  it("steps tone by a fixed factor rather than by where a surface faces", () => {
    const base = steppedTone(0x808080, "base");
    const shadow = steppedTone(0x808080, "shadow");
    const raised = steppedTone(0x808080, "raised");

    expect(base.getHex()).toBe(0x808080);
    expect(shadow.r).toBeLessThan(base.r);
    expect(raised.r).toBeGreaterThan(base.r);
    // Deterministic: the same colour and step always give the same value.
    expect(steppedTone(0x808080, "shadow").getHex()).toBe(shadow.getHex());
    // A step never blows past white, so the ladder stays a ladder.
    expect(steppedTone(0xffffff, "raised").getHex()).toBe(0xffffff);
  });
});

describe("Orthographic framing", () => {
  it("frames a fixed world height, so a diameter measures the same wherever it sits", () => {
    const wide = cinemaOrthographicFrustum(1200, 600, 1);

    expect(wide.top - wide.bottom).toBeCloseTo(CINEMA_VIEW_HEIGHT, 6);
    // Only the width follows the canvas; the framed height is the same at any aspect.
    expect(wide.right - wide.left).toBeCloseTo(CINEMA_VIEW_HEIGHT * 2, 6);
    const tall = cinemaOrthographicFrustum(600, 1200, 1);
    expect(tall.top - tall.bottom).toBeCloseTo(CINEMA_VIEW_HEIGHT, 6);
    expect(tall.right - tall.left).toBeCloseTo(CINEMA_VIEW_HEIGHT / 2, 6);
  });

  it("shows less world as zoom goes up, and clamps at the ends of the range", () => {
    const near = cinemaOrthographicFrustum(800, 600, 1.5);
    const far = cinemaOrthographicFrustum(800, 600, 0.6);

    expect(near.top).toBeLessThan(far.top);
    expect(cinemaOrthographicFrustum(800, 600, 99).top).toBeCloseTo(
      cinemaOrthographicFrustum(800, 600, clampCinemaZoom(99)).top,
      9
    );
  });

  it("fits a bigger network at a smaller zoom, and never magnifies a sketch past the floor", () => {
    expect(cinemaFitZoom(8)).toBeLessThan(cinemaFitZoom(2));
    expect(cinemaFitZoom(0)).toBe(2);
    expect(cinemaFitZoom(1e6)).toBe(0.25);
    // Whatever it returns has to keep the network inside the frame it asked for.
    const span = 4;
    expect(CINEMA_VIEW_HEIGHT / cinemaFitZoom(span)).toBeGreaterThanOrEqual(span);
  });

  it("builds an orthographic camera whose up axis matches the scene's +Z", () => {
    const camera = createCinemaCamera(900, 600, { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } });

    expect(camera).toBeInstanceOf(THREE.OrthographicCamera);
    expect(camera.up.z).toBe(1);
  });

  it("keeps parallel pipes parallel: equal world lengths project to equal screen lengths", () => {
    const camera = createCinemaCamera(800, 800, { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } });
    camera.updateMatrixWorld();
    const spanAt = (y: number) =>
      new THREE.Vector3(1, y, 0).project(camera).distanceTo(new THREE.Vector3(-1, y, 0).project(camera));

    // Under perspective the nearer run would measure wider; under orthographic it cannot.
    expect(spanAt(-2)).toBeCloseTo(spanAt(2), 9);
  });

  it("re-derives the frustum on resize instead of stretching the projection", () => {
    const settings = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } };
    const camera = createCinemaCamera(800, 600, settings);
    applyCinemaCamera(camera, settings, 1600, 600);

    expect(camera.top - camera.bottom).toBeCloseTo(CINEMA_VIEW_HEIGHT, 6);
    expect(camera.right - camera.left).toBeCloseTo((CINEMA_VIEW_HEIGHT * 1600) / 600, 6);
  });

  it("holds the network inside the near and far planes across the whole pitch range", () => {
    const settings = { yaw: 40, pitch: 78, zoom: 1, pan: { x: 0, y: 0 } };
    const camera = createCinemaCamera(800, 600, settings);
    camera.updateMatrixWorld();
    const depth = new THREE.Vector3(0, 0, 0).applyMatrix4(camera.matrixWorldInverse).z;

    expect(-depth).toBeGreaterThan(camera.near);
    expect(-depth).toBeLessThan(camera.far);
  });
});

describe("Swept pipe geometry", () => {
  const straight = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 0, 0)];

  it("sweeps a constant-radius section along every point of the path", () => {
    const geometry = buildSweptTubeGeometry(straight, 0.25, 8);
    const position = geometry.getAttribute("position");

    expect(position.count).toBe(2 * 8);
    for (let index = 0; index < position.count; index += 1) {
      const radius = Math.hypot(position.getY(index), position.getZ(index));
      expect(radius).toBeCloseTo(0.25, 6);
    }
  });

  it("follows a bent path rather than cutting the corner", () => {
    const bent = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 0, 0), new THREE.Vector3(2, 2, 0)];
    const geometry = buildSweptTubeGeometry(bent, 0.1, 6);
    const position = geometry.getAttribute("position");
    const nearest = (target: THREE.Vector3) => {
      let closest = Number.POSITIVE_INFINITY;
      for (let index = 0; index < position.count; index += 1) {
        closest = Math.min(closest, target.distanceTo(new THREE.Vector3().fromBufferAttribute(position, index)));
      }
      return closest;
    };

    // The tube reaches the corner the route turns at ...
    expect(nearest(new THREE.Vector3(2, 0, 0))).toBeLessThanOrEqual(0.1 + 1e-6);
    // ... and stays off the chord a straight tube between the two ends would have taken.
    expect(nearest(new THREE.Vector3(1, 1, 0))).toBeGreaterThan(0.5);
  });

  it("gives two identically shaped pipes identical geometry wherever they point", () => {
    const east = buildSweptTubeGeometry([new THREE.Vector3(0, 0, 0), new THREE.Vector3(3, 0, 0)], 0.2, 10);
    const north = buildSweptTubeGeometry([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 3, 0)], 0.2, 10);
    const sectionRadius = (geometry: THREE.BufferGeometry, axis: "x" | "y") => {
      const position = geometry.getAttribute("position");
      const offsets: number[] = [];
      for (let index = 0; index < position.count; index += 1) {
        offsets.push(
          axis === "x"
            ? Math.hypot(position.getY(index), position.getZ(index))
            : Math.hypot(position.getX(index), position.getZ(index))
        );
      }
      return offsets;
    };

    expect(east.getAttribute("position").count).toBe(north.getAttribute("position").count);
    sectionRadius(east, "x").forEach((radius) => expect(radius).toBeCloseTo(0.2, 6));
    sectionRadius(north, "y").forEach((radius) => expect(radius).toBeCloseTo(0.2, 6));
  });

  it("survives a degenerate path without emitting geometry", () => {
    expect(buildSweptTubeGeometry([], 0.2, 8).getAttribute("position")).toBeUndefined();
    expect(
      buildSweptTubeGeometry([new THREE.Vector3(1, 1, 0), new THREE.Vector3(1, 1, 0)], 0.2, 8).getAttribute("position")
    ).toBeUndefined();
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
    const { cage, core, outline } = createSchematicPipeMaterials(new THREE.Color(0x00ff00), active);

    expect(cage.opacity).toBeLessThan(solved.opacity);
    expect(core.opacity).toBeLessThan(solved.opacity);
    expect(outline.opacity).toBeLessThan(solved.opacity);
    // Unlit and non-emissive by construction, so the illustration has no way to
    // glow past the opaque solved surface however the scene is lit.
    expect(core).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect("emissive" in core).toBe(false);
  });

  it.each([
    { name: "idle", active: false },
    { name: "selected", active: true }
  ])("shades the $name pipe by material alone, never by orientation or distance", ({ active }) => {
    const { cage, core } = createSchematicPipeMaterials(new THREE.Color(0x00ff00), active);

    [cage, core].forEach((material) => {
      expect(material).toBeInstanceOf(THREE.MeshBasicMaterial);
      expect(material.fog).toBe(false);
    });
  });

  it("keeps the drawn pipe core on the network overlay colour, one tone step down when idle", () => {
    const overlay = new THREE.Color(0x0ad7ff);

    expect(createSchematicPipeMaterials(overlay, true).core.color.getHex()).toBe(overlay.getHex());
    expect(createSchematicPipeMaterials(overlay, false).core.color.getHex()).toBe(
      steppedTone(overlay, "shadow").getHex()
    );
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
