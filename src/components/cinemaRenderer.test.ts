import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { VtkResultDataset } from "../types";
import {
  CINEMA_VIEW_HEIGHT,
  RESULT_SURFACE_Z_OFFSET,
  SOLVED_DOMAIN_VIEW_FILL,
  SOLVED_DOMAIN_WORLD_SPAN,
  applyCinemaCamera,
  buildSweptTubeGeometry,
  cinemaFitZoom,
  cinemaFitZoomForBox,
  cinemaOrthographicFrustum,
  clampCinemaZoom,
  createCinemaAmbientLight,
  createCinemaCamera,
  createConstantOutlineMaterial,
  createConstantSurfaceMaterial,
  createResultBoundaryMaterial,
  createResultSurfaceMaterial,
  createSchematicPipeMaterials,
  describeSolvedDomain,
  exteriorTriangleCount,
  extractExteriorCellFaces,
  resultSurfaceTriangles,
  solvedDomainCaptionPlacement,
  steppedTone,
  datasetBounds
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
  it("sizes the domain against what the camera frames, not against the drawn network", () => {
    expect(SOLVED_DOMAIN_WORLD_SPAN).toBeCloseTo(CINEMA_VIEW_HEIGHT * SOLVED_DOMAIN_VIEW_FILL, 9);
    // A single constant is the point: nothing about the user's layout can reach it,
    // so a measured extent can no longer change with how far apart two icons sit.
    expect(SOLVED_DOMAIN_WORLD_SPAN).toBeLessThan(CINEMA_VIEW_HEIGHT);
    expect(SOLVED_DOMAIN_WORLD_SPAN).toBeGreaterThan(CINEMA_VIEW_HEIGHT / 2);
  });

  it("keeps a margin around the domain at zoom 1, so an orbit cannot swing a corner out of frame", () => {
    expect(SOLVED_DOMAIN_VIEW_FILL).toBeLessThan(1);
    expect(CINEMA_VIEW_HEIGHT - SOLVED_DOMAIN_WORLD_SPAN).toBeGreaterThan(0.3);
  });

  it("is framed by Fit without cropping", () => {
    expect(CINEMA_VIEW_HEIGHT / cinemaFitZoom(SOLVED_DOMAIN_WORLD_SPAN)).toBeGreaterThanOrEqual(SOLVED_DOMAIN_WORLD_SPAN);
  });
});

describe("Framing the solved domain", () => {
  const square = { yaw: 0, pitch: 0 } as const;
  /** Screen-space half-extents of a box under a fit, as a fraction of the framed view. */
  const framed = (half: [number, number, number], settings: { yaw: number; pitch: number }, width: number, height: number) => {
    const zoom = cinemaFitZoomForBox(half, settings, width, height);
    const frustum = cinemaOrthographicFrustum(width, height, zoom);
    const camera = createCinemaCamera(width, height, { ...settings, zoom, pan: { x: 0, y: 0 } });
    camera.updateMatrixWorld();
    let widest = 0;
    let tallest = 0;
    [-1, 1].forEach((sx) =>
      [-1, 1].forEach((sy) =>
        [-1, 1].forEach((sz) => {
          const corner = new THREE.Vector3(half[0] * sx, half[1] * sy, half[2] * sz).applyMatrix4(camera.matrixWorldInverse);
          widest = Math.max(widest, Math.abs(corner.x));
          tallest = Math.max(tallest, Math.abs(corner.y));
        })
      )
    );
    return { zoom, width: widest / frustum.right, height: tallest / frustum.top };
  };

  it("fills the frame with a long strip instead of fitting a cube around it", () => {
    const strip: [number, number, number] = [2.98, 0.18, 0];
    const wide = framed(strip, square, 1200, 700);
    // The strip is the subject, so it takes most of the frame it lies along ...
    expect(wide.width).toBeGreaterThan(0.85);
    // ... and stays inside it.
    expect(wide.width).toBeLessThanOrEqual(1);
    expect(wide.height).toBeLessThanOrEqual(1);
    // The cube-shaped fit this replaced would have zoomed out instead of in.
    expect(wide.zoom).toBeGreaterThan(cinemaFitZoom(SOLVED_DOMAIN_WORLD_SPAN));
  });

  it("keeps the domain inside the frame from any orbit and canvas shape", () => {
    const domain: [number, number, number] = [2.4, 1.6, 0.9];
    [
      { yaw: 0, pitch: 0 },
      { yaw: -32, pitch: 24 },
      { yaw: 118, pitch: 76 },
      { yaw: -175, pitch: -12 }
    ].forEach((settings) => {
      [
        [1200, 700],
        [520, 900]
      ].forEach(([width, height]) => {
        const placed = framed(domain, settings, width, height);
        expect(placed.width).toBeLessThanOrEqual(1);
        expect(placed.height).toBeLessThanOrEqual(1);
      });
    });
  });

  it("zooms in on a small domain and out on a large one, within the usable range", () => {
    const small = cinemaFitZoomForBox([0.4, 0.4, 0.4], square, 1200, 700);
    const large = cinemaFitZoomForBox([9, 9, 9], square, 1200, 700);

    expect(small).toBeGreaterThan(large);
    expect(small).toBe(clampCinemaZoom(small));
    expect(large).toBe(clampCinemaZoom(large));
  });

  it("survives a degenerate domain rather than returning an unusable zoom", () => {
    expect(Number.isFinite(cinemaFitZoomForBox([0, 0, 0], square, 1200, 700))).toBe(true);
    expect(cinemaFitZoomForBox([0, 0, 0], square, 1200, 700)).toBe(clampCinemaZoom(Infinity));
  });
});

describe("Solved domain caption placement", () => {
  const caption = { cssWidth: 420, cssHeight: 96 };
  const place = (viewWidth: number, viewHeight: number, zoom: number) =>
    solvedDomainCaptionPlacement({ ...caption, viewWidth, viewHeight, zoom });

  /** Where the caption's edges land on screen, in CSS pixels from the bottom-left. */
  const onScreen = (viewWidth: number, viewHeight: number, zoom: number) => {
    const placement = place(viewWidth, viewHeight, zoom);
    const frustum = cinemaOrthographicFrustum(viewWidth, viewHeight, zoom);
    const perPixel = (frustum.top - frustum.bottom) / viewHeight;
    return {
      width: placement.width / perPixel,
      height: placement.height / perPixel,
      left: (placement.x - placement.width / 2 - frustum.left) / perPixel,
      bottom: (placement.y - placement.height / 2 - frustum.bottom) / perPixel
    };
  };

  it("renders the caption at its authored pixel size, whatever the scene is zoomed to", () => {
    const wide = onScreen(1200, 700, 1);
    const zoomedIn = onScreen(1200, 700, 1.8);
    const zoomedOut = onScreen(1200, 700, 0.45);

    expect(wide.width).toBeCloseTo(420, 6);
    expect(wide.height).toBeCloseTo(96, 6);
    // A label that grew as the data was zoomed would be competing with it again.
    expect(zoomedIn.width).toBeCloseTo(420, 6);
    expect(zoomedOut.width).toBeCloseTo(420, 6);
  });

  it("holds the same corner inset at every zoom", () => {
    [0.45, 1, 1.8].forEach((zoom) => {
      const placed = onScreen(1200, 700, zoom);
      expect(placed.left).toBeCloseTo(18, 6);
      expect(placed.bottom).toBeCloseTo(18, 6);
    });
  });

  it("scales itself down rather than spilling across a narrow canvas", () => {
    const narrow = onScreen(420, 700, 1);

    expect(narrow.width).toBeLessThanOrEqual(420 * 0.52 + 1e-9);
    // Shrinks in proportion, so the type ramp inside it stays intact.
    expect(narrow.height / narrow.width).toBeCloseTo(96 / 420, 6);
  });

  it("stays inside the frame it was placed in", () => {
    [
      [1200, 700],
      [420, 700],
      [900, 400]
    ].forEach(([width, height]) => {
      const placement = place(width, height, 1);
      const frustum = cinemaOrthographicFrustum(width, height, 1);
      expect(placement.x - placement.width / 2).toBeGreaterThanOrEqual(frustum.left);
      expect(placement.x + placement.width / 2).toBeLessThanOrEqual(frustum.right);
      expect(placement.y - placement.height / 2).toBeGreaterThanOrEqual(frustum.bottom);
      expect(placement.y + placement.height / 2).toBeLessThanOrEqual(frustum.top);
    });
  });
});

describe("Naming the solved domain", () => {
  /** Two stacked quad rows: a surface mesh with no thickness at all. */
  const sheet = dataset(
    [
      [0, 0, 0],
      [600, 0, 0],
      [600, 36, 0],
      [0, 36, 0]
    ],
    [[0, 1, 2, 3]],
    [9]
  );

  /** One cell thick across z: what the default `planar-2d` mesh mode produces. */
  const slab = dataset(hexPoints.map(([x, y, z]) => [x * 300, y * 20, z * 0.5] as [number, number, number]), [[0, 1, 2, 3, 4, 5, 6, 7]], [12]);

  it("calls a zero-thickness dataset a flat 2-D surface", () => {
    const description = describeSolvedDomain(sheet);

    expect(description.shape).toBe("sheet");
    expect(description.thinAxis).toBe(2);
    expect(description.extent).toEqual([600, 36, 0]);
    expect(description.lines[1]).toMatch(/flat 2-d domain/i);
  });

  it("calls a one-cell-thick dataset a flat 2-D domain and says it is not the drawn pipe", () => {
    const description = describeSolvedDomain(slab);

    expect(description.shape).toBe("slab");
    expect(description.layers).toBe(2);
    expect(description.lines[1]).toMatch(/one cell thick/i);
    expect(description.lines[2]).toMatch(/not the round pipe drawn in the schematic/i);
  });

  it("calls a domain resolved across all three axes a volume, and does not deny the pipe twice", () => {
    // A 2 x 2 x 2 block of hexahedra: three distinct coordinates on every axis.
    const lattice: [number, number, number][] = [];
    for (let z = 0; z < 3; z += 1) for (let y = 0; y < 3; y += 1) for (let x = 0; x < 3; x += 1) lattice.push([x, y, z]);
    const at = (x: number, y: number, z: number) => z * 9 + y * 3 + x;
    const cells: number[][] = [];
    for (let z = 0; z < 2; z += 1) {
      for (let y = 0; y < 2; y += 1) {
        for (let x = 0; x < 2; x += 1) {
          cells.push([
            at(x, y, z), at(x + 1, y, z), at(x + 1, y + 1, z), at(x, y + 1, z),
            at(x, y, z + 1), at(x + 1, y, z + 1), at(x + 1, y + 1, z + 1), at(x, y + 1, z + 1)
          ]);
        }
      }
    }
    const description = describeSolvedDomain(dataset(lattice, cells, cells.map(() => 12)));

    expect(description.shape).toBe("volume");
    expect(description.layers).toBe(3);
    expect(description.lines[1]).toMatch(/3-d volume domain/i);
    expect(description.lines[2]).not.toMatch(/round pipe/i);
    expect(description.lines[3]).toBe("2 × 2 × 2 in dataset units · 8 cells");
  });

  it("names the object and reports the measured extent and cell count, in dataset units", () => {
    const description = describeSolvedDomain(sheet);

    expect(description.lines[0]).toBe("SOLVED DOMAIN");
    expect(description.lines[3]).toBe("600 × 36 × 0 in dataset units · 1 cell");
    // No unit is asserted anywhere: a loaded VTK carries no units, and the
    // bundled fixture is authored in schematic pixels rather than metres.
    expect(description.lines.join(" ")).not.toMatch(/\bm\b|metre|meter|\bmm\b/i);
  });

  it("classifies by shape rather than by which axis happens to be thin", () => {
    const acrossX = dataset(
      [
        [0, 0, 0],
        [0, 600, 0],
        [0, 600, 36],
        [0, 0, 36]
      ],
      [[0, 1, 2, 3]],
      [9]
    );

    expect(describeSolvedDomain(acrossX).thinAxis).toBe(0);
    expect(describeSolvedDomain(acrossX).shape).toBe("sheet");
  });

  it("reads a millimetre-scale domain the same way as a metre-scale one", () => {
    const millimetres = dataset(hexPoints.map(([x, y, z]) => [x * 300, y * 20, z * 0.5] as [number, number, number]), [[0, 1, 2, 3, 4, 5, 6, 7]], [12]);
    const metres = dataset(hexPoints.map(([x, y, z]) => [x * 0.3, y * 0.02, z * 0.0005] as [number, number, number]), [[0, 1, 2, 3, 4, 5, 6, 7]], [12]);

    expect(describeSolvedDomain(metres).shape).toBe(describeSolvedDomain(millimetres).shape);
    expect(describeSolvedDomain(metres).layers).toBe(2);
  });

  it("survives an empty dataset instead of describing geometry that is not there", () => {
    const empty = describeSolvedDomain(dataset([], [], []));

    expect(empty.extent).toEqual([0, 0, 0]);
    expect(empty.layers).toBe(0);
    expect(empty.lines).toHaveLength(4);
  });
});

describe("datasetBounds on a large result", () => {
  it("does not overflow the argument limit", () => {
    // Spreading a per-point array into Math.min throws RangeError past roughly
    // 100k arguments, which is the size of result this view exists to show.
    const count = 300_000;
    const points: [number, number, number][] = new Array(count);
    for (let i = 0; i < count; i += 1) points[i] = [i, -i - 1, 0];
    const bounds = datasetBounds({ points, cells: [], fields: {} } as never);
    expect(bounds.min).toEqual([0, -count, 0]);
    expect(bounds.max).toEqual([count - 1, -1, 0]);
  });
});
