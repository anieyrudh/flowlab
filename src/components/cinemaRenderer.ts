import * as THREE from "three";
import type { DecodedDerivedVisualization } from "../results/derived";
import { fieldValuesForOverlay, fieldValuesForSelection, type ResultFieldSelection, type ResultVectorComponent } from "../results/vtk";
import type {
  FluidEdge,
  FluidNode,
  FluidProject,
  OverlayMode,
  PipePortId,
  ResultColorMap,
  SimulationResult,
  Vec2,
  VtkResultDataset
} from "../types";
import type { CinemaCameraState } from "./viewportModel";
import { recordEditorMetric } from "../performance/editorProfiler";
import { addStreamlineScene } from "../streamlines/render";
import type { StreamlineDisplayOptions, StreamlineResult } from "../streamlines/types";
import { buildDerivedPresentation, type DerivedPresentationOptions } from "./derivedRenderer";

export type CinemaPick =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | { kind: "port"; nodeId: string; port: PipePortId; point: Vec2 }
  | { kind: "rotate"; nodeId: string };

export type CinemaResultProbe = {
  point: [number, number, number];
  ownerCellIndex: number;
  nearestPointIndex: number;
  trianglePointIndices: [number, number, number];
  barycentricWeights: [number, number, number];
};

export type CinemaRuntime = {
  center: Vec2;
  worldScale: number;
  pickableCount: number;
  projectedNodePositions: Record<string, { x: number; y: number }>;
  engine: string;
  derivedFallback: "none" | "webgl2-required";
  render: (time: number, advancePreview?: boolean) => void;
  updateModel: (project: FluidProject, result: SimulationResult) => void;
  fitCamera: (settings: CinemaCameraState, project: FluidProject) => CinemaCameraState;
  updateCamera: (settings: CinemaCameraState) => void;
  resize: () => void;
  dispose: () => void;
  pickAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => CinemaPick | null;
  probeAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => CinemaResultProbe | null;
  pointAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => Vec2 | null;
};

const ports: PipePortId[] = ["inlet", "outlet", "north", "south"];

const paletteByOverlay: Record<OverlayMode, string[]> = {
  velocity: ["#0ad7ff", "#62f3bd", "#ffe15c", "#ff6f3d"],
  pressure: ["#284dff", "#04c6ff", "#f7d84b", "#ff4d5e"],
  reynolds: ["#57e389", "#ffd166", "#ff7a45", "#ff4d6d"],
  temperature: ["#4cc9f0", "#f7e733", "#ff8c42", "#ff365e"],
  phase: ["#3a86ff", "#80ffdb", "#f4d35e", "#ee6055"],
  residuals: ["#8b5cf6", "#c4b5fd", "#fb7185", "#f43f5e"],
  geometry: ["#8aa0b8", "#b9c7d8", "#ffffff", "#f5c542"]
};

const resultColorPalettes: Record<ResultColorMap, string[]> = {
  turbo: ["#2b4cff", "#00c2ff", "#67f3a5", "#ffe15c", "#ff6a3a", "#c5164f"],
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  thermal: ["#18206f", "#1954d2", "#1eb6ff", "#f7e733", "#ff8c42", "#d62839"],
  grayscale: ["#17212b", "#4b5d70", "#8da1b5", "#d6e2ec", "#ffffff"]
};

// --- Solved domain vs. schematic network -------------------------------------
//
// Two unrelated things share this scene and used to look alike:
//   * the SOLVED DOMAIN - the VTK dataset the solver actually produced. Under
//     the default `planar-2d` mesh mode that domain genuinely is a one-cell-thick
//     channel, so a flat coloured slab is the honest picture of it.
//   * the SCHEMATIC NETWORK - the pipe layout the user drew. It illustrates the
//     layout; it is never a solved field.
// Everything below keeps the two apart by construction: measured data is opaque,
// unlit, un-fogged and outlined, while the drawn network is a ghosted wireframe.

/**
 * World-space Z the solved surface is lifted to. Negative so the measured data
 * sits *under* the drawn network rather than being hidden by it, and still well
 * clear of the reference grid. `probeAt` and the derived overlay reuse this so
 * physical coordinates round-trip exactly.
 */
export const RESULT_SURFACE_Z_OFFSET = -0.24;

/** Largest world extent of the solved domain when there is no network to match. */
export const DEFAULT_RESULT_WORLD_SPAN = 5.2;

/**
 * Bounds on the solved domain's world extent, so it is never a speck and never
 * overruns the default framing. The upper bound sits just inside the ~6.5 world
 * units the default camera sees, which is why a sprawling network does not drag
 * the solved slab off-screen with it.
 */
const RESULT_WORLD_SPAN_RANGE: readonly [number, number] = [2.4, 5.8];

/** Above this triangle count the boundary outline is skipped to protect frame time. */
const MAX_OUTLINE_TRIANGLES = 120_000;

/** Crease angle above which the solved domain's outline draws an interior edge. */
const OUTLINE_CREASE_DEGREES = 30;

const flowParticleVertexShader = `
  uniform float uTime;
  attribute float aPhase;
  attribute vec3 aColor;
  varying vec3 vColor;
  void main() {
    vColor = aColor;
    vec3 p = position;
    p.z += sin(uTime * 2.6 + aPhase * 6.28318) * 0.035;
    vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = 6.6 * (1.0 + 0.38 * sin(uTime * 4.0 + aPhase * 8.0));
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const flowParticleFragmentShader = `
  varying vec3 vColor;
  void main() {
    vec2 uv = gl_PointCoord - vec2(0.5);
    // Held well below full alpha: these ticks illustrate flow direction on the
    // drawn network, they are not sampled field data, so they must not read as
    // brightly as the solved surface.
    float alpha = smoothstep(0.5, 0.16, length(uv)) * 0.5;
    gl_FragColor = vec4(vColor, alpha);
  }
`;

function valueColor(value: number, max: number, colors: string[]): string {
  const t = Math.max(0, Math.min(0.999, Math.abs(value) / Math.max(max, 1e-9)));
  return colors[Math.floor(t * colors.length)];
}

function overlayValueColor(value: number, max: number, overlay: OverlayMode): string {
  return valueColor(value, max, paletteByOverlay[overlay]);
}

function resultValueColor(value: number, max: number, colorMap: ResultColorMap): string {
  return valueColor(value, max, resultColorPalettes[colorMap]);
}

export type CinemaLightRig = {
  key: THREE.DirectionalLight;
  fill: THREE.DirectionalLight;
  rim: THREE.DirectionalLight;
  ambient: THREE.HemisphereLight;
};

/**
 * Neutral three-point rig.
 *
 * Every light here is directional or hemispherical, so the light a surface
 * receives depends only on its normal - never on where it happens to sit in the
 * scene. The rig this replaces added two saturated short-range PointLights
 * (cyan at x=+4, warm orange at y=+2.8), which tinted one identical material
 * cyan on one side of the network and orange on the other; that positional hue
 * shift was the inconsistency. Keeping all four lights white or near-white
 * leaves hue to the materials and puts the lighting to work describing form.
 *
 * Scene convention: the network lies in the XY plane and +Z is up, so the key
 * is high on +Z rather than the +Y a Y-up scene would use.
 */
export function createCinemaLightRig(): CinemaLightRig {
  const key = new THREE.DirectionalLight(0xffffff, 2.85);
  key.position.set(-3.6, -4.6, 7.4); // high, and over the viewer's left shoulder
  key.name = "Cinema key light";
  const fill = new THREE.DirectionalLight(0xffffff, 1.05);
  fill.position.set(4.9, -3.2, 2.1); // opposite the key, opens up the shadow side
  fill.name = "Cinema fill light";
  const rim = new THREE.DirectionalLight(0xffffff, 0.7);
  rim.position.set(0.7, 5.4, 3.1); // from behind, separates silhouettes from the background
  rim.name = "Cinema rim light";
  const ambient = new THREE.HemisphereLight(0xeef2f6, 0x1b2026, 0.85);
  ambient.position.set(0, 0, 1); // hemisphere axis follows the scene's +Z up
  ambient.name = "Cinema ambient";
  return { key, fill, rim, ambient };
}

/**
 * Material for the solved surface.
 *
 * Deliberately unlit, fully opaque, un-fogged and un-tone-mapped: the pixel a
 * reader sees has to be the colour-map colour for the sampled value. The
 * previous material was translucent (opacity 0.82) and picked up the scene fog,
 * so every value was blended toward the dark background before it reached the
 * eye and the picture quietly disagreed with the legend. Shading it would do the
 * same thing by another route.
 */
export function createResultSurfaceMaterial(): THREE.MeshBasicMaterial {
  const material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, depthWrite: true });
  material.fog = false;
  material.toneMapped = false;
  material.polygonOffset = true; // let the boundary outline win the depth test
  material.polygonOffsetFactor = 1;
  material.polygonOffsetUnits = 1;
  return material;
}

/** Crisp outline around the solved domain, so the slab reads as a measured extent. */
export function createResultBoundaryMaterial(): THREE.LineBasicMaterial {
  const material = new THREE.LineBasicMaterial({ color: 0xf2f8ff, transparent: true, opacity: 0.92 });
  material.fog = false;
  material.toneMapped = false;
  return material;
}

export type SchematicPipeMaterials = {
  cage: THREE.MeshBasicMaterial;
  core: THREE.MeshStandardMaterial;
};

/**
 * Materials for one drawn pipe.
 *
 * The outer surface becomes a ghosted wireframe cage - a treatment no solver
 * ever outputs - so the drawn network reads as a diagram at a glance. The core
 * still carries the network overlay colour but stays translucent, so it can
 * never look more authoritative than the opaque solved surface beneath it.
 */
export function createSchematicPipeMaterials(color: THREE.Color, active: boolean): SchematicPipeMaterials {
  const cage = new THREE.MeshBasicMaterial({
    color: active ? 0xffd98a : 0x7ea8c2,
    wireframe: true,
    transparent: true,
    opacity: active ? 0.36 : 0.2,
    depthWrite: false
  });
  const core = new THREE.MeshStandardMaterial({
    color,
    transparent: true,
    opacity: active ? 0.5 : 0.34,
    roughness: 0.36,
    metalness: 0.02,
    emissive: color,
    emissiveIntensity: active ? 0.42 : 0.16,
    depthWrite: false
  });
  return { cage, core };
}

/**
 * Size the solved domain to the drawn network so the two read at a comparable
 * scale in one frame instead of one dwarfing the other. `fitCamera` frames the
 * network, so tying the domain to the same extent keeps both in view.
 */
export function resultWorldSpanForNetwork(project: FluidProject, worldScale: number): number {
  const nodes = Object.values(project.nodes);
  if (nodes.length < 2) return DEFAULT_RESULT_WORLD_SPAN;
  const xs = nodes.map((node) => node.position.x);
  const ys = nodes.map((node) => node.position.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / worldScale;
  if (!Number.isFinite(span) || span <= 0) return DEFAULT_RESULT_WORLD_SPAN;
  const [minimum, maximum] = RESULT_WORLD_SPAN_RANGE;
  return Math.max(minimum, Math.min(maximum, span * 0.92));
}

function degreesToRadians(degrees: number) {
  return (degrees * Math.PI) / 180;
}

function nodeRadius(node: FluidNode) {
  if (node.type === "source" || node.type === "sink") return 17;
  if (node.type === "pump") return 19;
  return 14;
}

function portAngle(node: FluidNode, port: PipePortId) {
  const base = node.rotation ?? 0;
  if (port === "outlet") return base;
  if (port === "inlet") return base + 180;
  if (port === "north") return base - 90;
  return base + 90;
}

function portPosition(node: FluidNode, port: PipePortId): Vec2 {
  const radius = nodeRadius(node) + 10;
  const angle = degreesToRadians(portAngle(node, port));
  return {
    x: node.position.x + Math.cos(angle) * radius,
    y: node.position.y + Math.sin(angle) * radius
  };
}

function aimHandlePosition(node: FluidNode): Vec2 {
  const angle = degreesToRadians(node.rotation ?? 0);
  return {
    x: node.position.x + Math.cos(angle) * 46,
    y: node.position.y + Math.sin(angle) * 46
  };
}

function endpointPoint(edge: FluidEdge, endpoint: "from" | "to", nodes: Record<string, FluidNode>) {
  const node = nodes[endpoint === "from" ? edge.from : edge.to];
  if (!node) return null;
  return portPosition(node, endpoint === "from" ? (edge.fromPort ?? "outlet") : (edge.toPort ?? "inlet"));
}

function projectCenter(project: FluidProject): Vec2 {
  const nodes = Object.values(project.nodes);
  if (!nodes.length) return { x: 0, y: 0 };
  const minX = Math.min(...nodes.map((node) => node.position.x));
  const maxX = Math.max(...nodes.map((node) => node.position.x));
  const minY = Math.min(...nodes.map((node) => node.position.y));
  const maxY = Math.max(...nodes.map((node) => node.position.y));
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
}

function worldFromNetwork(point: Vec2, center: Vec2, worldScale: number, z = 0) {
  return new THREE.Vector3((point.x - center.x) / worldScale, -(point.y - center.y) / worldScale, z);
}

function networkFromWorld(point: THREE.Vector3, center: Vec2, worldScale: number): Vec2 {
  return {
    x: point.x * worldScale + center.x,
    y: -point.y * worldScale + center.y
  };
}

function edgeWorldEndpoints(edge: FluidEdge, project: FluidProject, center: Vec2, worldScale: number) {
  const from = endpointPoint(edge, "from", project.nodes) ?? project.nodes[edge.from]?.position;
  const to = endpointPoint(edge, "to", project.nodes) ?? project.nodes[edge.to]?.position;
  if (!from || !to) return null;
  return {
    start: worldFromNetwork(from, center, worldScale, 0),
    end: worldFromNetwork(to, center, worldScale, 0)
  };
}

function orientBetweenPoints(object: THREE.Object3D, start: THREE.Vector3, end: THREE.Vector3) {
  const midpoint = start.clone().add(end).multiplyScalar(0.5);
  const direction = end.clone().sub(start);
  object.position.copy(midpoint);
  object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
}

function createPipeMesh(start: THREE.Vector3, end: THREE.Vector3, radius: number, material: THREE.Material, radialSegments = 36) {
  const length = Math.max(start.distanceTo(end), 0.01);
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, radialSegments, 1, true), material);
  mesh.userData.pipeBaseLength = length;
  orientBetweenPoints(mesh, start, end);
  return mesh;
}

function datasetBounds(dataset: VtkResultDataset) {
  const xs = dataset.points.map((point) => point[0]);
  const ys = dataset.points.map((point) => point[1]);
  const zs = dataset.points.map((point) => point[2]);
  const min: [number, number, number] = [Math.min(...xs), Math.min(...ys), Math.min(...zs)];
  const max: [number, number, number] = [Math.max(...xs), Math.max(...ys), Math.max(...zs)];
  return {
    min,
    max,
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2] as [number, number, number],
    span: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1e-9)
  };
}

export type ExteriorCellFace = {
  pointIndices: number[];
  ownerCellIndex: number;
};

function cellFaces(cell: number[], cellType: number): number[][] {
  const face = (...indices: number[]) => indices.map((index) => cell[index]);
  if (cellType === 5 && cell.length === 3) return [face(0, 1, 2)];
  if ((cellType === 7 || cellType === 9) && cell.length >= 3) return [[...cell]];
  if (cellType === 10 && cell.length === 4) {
    return [face(0, 2, 1), face(0, 1, 3), face(1, 2, 3), face(2, 0, 3)];
  }
  if (cellType === 12 && cell.length === 8) {
    return [face(0, 3, 2, 1), face(4, 5, 6, 7), face(0, 1, 5, 4), face(1, 2, 6, 5), face(2, 3, 7, 6), face(3, 0, 4, 7)];
  }
  if (cellType === 13 && cell.length === 6) {
    return [face(0, 2, 1), face(3, 4, 5), face(0, 1, 4, 3), face(1, 2, 5, 4), face(2, 0, 3, 5)];
  }
  if (cellType === 14 && cell.length === 5) {
    return [face(0, 3, 2, 1), face(0, 1, 4), face(1, 2, 4), face(2, 3, 4), face(3, 0, 4)];
  }
  return [];
}

function orientedAwayFromCell(face: number[], cell: number[], points: VtkResultDataset["points"]): number[] {
  const unique = Array.from(new Set(face));
  if (unique.length < 3) return [];
  const a = new THREE.Vector3(...points[face[0]]);
  const b = new THREE.Vector3(...points[face[1]]);
  const c = new THREE.Vector3(...points[face[2]]);
  const normal = b.clone().sub(a).cross(c.clone().sub(a));
  if (normal.lengthSq() <= 1e-20) return [];
  const faceCenter = face.reduce((sum, index) => sum.add(new THREE.Vector3(...points[index])), new THREE.Vector3()).multiplyScalar(1 / face.length);
  const cellCenter = cell.reduce((sum, index) => sum.add(new THREE.Vector3(...points[index])), new THREE.Vector3()).multiplyScalar(1 / cell.length);
  return normal.dot(cellCenter.sub(faceCenter)) > 0 ? [...face].reverse() : face;
}

export function extractExteriorCellFaces(dataset: VtkResultDataset): ExteriorCellFace[] {
  const byKey = new Map<string, { count: number; face: ExteriorCellFace }>();
  dataset.cells.forEach((cell, ownerCellIndex) => {
    const cellType = dataset.cellTypes[ownerCellIndex];
    cellFaces(cell, cellType).forEach((candidate) => {
      const pointIndices = orientedAwayFromCell(candidate, cell, dataset.points);
      if (pointIndices.length < 3) return;
      const key = [...pointIndices].sort((left, right) => left - right).join(":");
      const existing = byKey.get(key);
      if (existing) {
        existing.count += 1;
      } else {
        byKey.set(key, { count: 1, face: { pointIndices, ownerCellIndex } });
      }
    });
  });
  return Array.from(byKey.values())
    .filter((entry) => entry.count === 1)
    .map((entry) => entry.face);
}

export function exteriorTriangleCount(dataset: VtkResultDataset): number {
  return extractExteriorCellFaces(dataset).reduce((count, face) => count + Math.max(0, face.pointIndices.length - 2), 0);
}

export type ResultSurfaceTriangle = {
  pointIndices: [number, number, number];
  ownerCellIndex: number;
};

export function resultSurfaceTriangles(dataset: VtkResultDataset): ResultSurfaceTriangle[] {
  return extractExteriorCellFaces(dataset).flatMap(({ pointIndices, ownerCellIndex }) => {
    const triangles: ResultSurfaceTriangle[] = [];
    for (let index = 1; index < pointIndices.length - 1; index += 1) {
      triangles.push({
        pointIndices: [pointIndices[0], pointIndices[index], pointIndices[index + 1]],
        ownerCellIndex
      });
    }
    return triangles;
  });
}

function fieldValueForDatasetPoint(values: number[], location: "point" | "cell", pointIndex: number, cellIndex: number, cell: number[]) {
  if (location === "point") return values[pointIndex] ?? 0;
  return values[cellIndex] ?? cell.reduce((sum, index) => sum + (values[index] ?? 0), 0) / Math.max(cell.length, 1);
}

function addResultSurfaceMesh(
  scene: THREE.Scene,
  dataset: VtkResultDataset,
  overlay: OverlayMode,
  resultFieldSelection: ResultFieldSelection | null,
  resultVectorComponent: ResultVectorComponent,
  resultColorMap: ResultColorMap,
  worldSpan: number
): {
  surface: THREE.Mesh;
  triangles: ResultSurfaceTriangle[];
  bounds: ReturnType<typeof datasetBounds>;
  meshScale: number;
} | null {
  const fieldValues = resultFieldSelection ? fieldValuesForSelection(dataset, resultFieldSelection, resultVectorComponent) : fieldValuesForOverlay(dataset, overlay);
  if (!fieldValues || dataset.cells.length === 0 || dataset.points.length === 0) return null;
  const bounds = datasetBounds(dataset);
  const maxValue = Math.max(...fieldValues.values.map((value) => Math.abs(value)), 1e-9);
  const positions: number[] = [];
  const colors: number[] = [];
  const meshScale = worldSpan / bounds.span;
  const triangles = resultSurfaceTriangles(dataset);

  triangles.forEach(({ pointIndices, ownerCellIndex }) => {
    const cell = dataset.cells[ownerCellIndex];
    pointIndices.forEach((pointIndex) => {
      const point = dataset.points[pointIndex];
      const color = new THREE.Color(
        resultValueColor(fieldValueForDatasetPoint(fieldValues.values, fieldValues.location, pointIndex, ownerCellIndex, cell), maxValue, resultColorMap)
      );
      positions.push(
        (point[0] - bounds.center[0]) * meshScale,
        (point[1] - bounds.center[1]) * meshScale,
        (point[2] - bounds.center[2]) * meshScale + RESULT_SURFACE_Z_OFFSET
      );
      colors.push(color.r, color.g, color.b);
    });
  });

  if (positions.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const surface = new THREE.Mesh(geometry, createResultSurfaceMaterial());
  surface.name = `Solved domain: VTK ${fieldValues.field} exterior surface`;

  // Outline the solved domain's own boundary. `EdgesGeometry` emits unshared
  // edges plus creases sharper than the threshold, so for the default
  // one-cell-thick `planar-2d` channel this traces the real extent of the solved
  // region - the flat slab reads as a measured patch instead of a stray
  // backdrop - while a curved axisymmetric domain keeps its smooth faceting
  // undrawn rather than being covered in a mesh wireframe.
  if (triangles.length <= MAX_OUTLINE_TRIANGLES) {
    const boundary = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, OUTLINE_CREASE_DEGREES), createResultBoundaryMaterial());
    boundary.name = "Solved domain boundary";
    boundary.renderOrder = 2;
    surface.add(boundary);
  }
  scene.add(surface);
  return { surface, triangles, bounds, meshScale };
}

function canvasNdc(canvas: HTMLCanvasElement, event: Pick<PointerEvent, "clientX" | "clientY">) {
  const rect = canvas.getBoundingClientRect();
  return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -(((event.clientY - rect.top) / rect.height) * 2 - 1));
}

function projectedNodePositions(project: FluidProject, center: Vec2, worldScale: number, camera: THREE.Camera, width: number, height: number) {
  return Object.fromEntries(
    Object.values(project.nodes).map((node) => {
      const projected = worldFromNetwork(node.position, center, worldScale, 0.08).project(camera);
      return [
        node.id,
        {
          x: Math.round(((projected.x + 1) / 2) * width),
          y: Math.round(((-projected.y + 1) / 2) * height)
        }
      ];
    })
  );
}

function applyCamera(camera: THREE.PerspectiveCamera, settings: CinemaCameraState) {
  const yaw = degreesToRadians(settings.yaw);
  const pitch = degreesToRadians(Math.max(-12, Math.min(78, settings.pitch)));
  const distance = 8.9 / Math.max(0.45, Math.min(1.8, settings.zoom));
  const horizontal = Math.cos(pitch) * distance;
  const target = new THREE.Vector3(settings.pan.x, settings.pan.y, 0);
  camera.position.set(target.x + Math.sin(yaw) * horizontal, target.y - Math.cos(yaw) * horizontal, target.z + Math.sin(pitch) * distance);
  camera.lookAt(target);
}

export function buildCinemaScene(options: {
  canvas: HTMLCanvasElement;
  width: number;
  height: number;
  project: FluidProject;
  result: SimulationResult;
  cinemaCamera?: CinemaCameraState;
  resultDataset?: VtkResultDataset | null;
  derivedVisualization?: DecodedDerivedVisualization | null;
  derivedPresentationOptions?: DerivedPresentationOptions;
  resultFieldSelection: ResultFieldSelection | null;
  resultVectorComponent: ResultVectorComponent;
  resultColorMap: ResultColorMap;
  streamlines?: StreamlineResult | null;
  streamlineDisplay?: StreamlineDisplayOptions;
  selectedId: string | null;
  selectedKind?: "node" | "edge" | null;
}): CinemaRuntime {
  const buildStarted = performance.now();
  const {
    canvas,
    width,
    height,
    project,
    result,
    cinemaCamera = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } },
    resultDataset,
    resultFieldSelection,
    resultVectorComponent,
    resultColorMap,
    streamlines,
    streamlineDisplay = {
      colorField: "velocity",
      colorMap: resultColorMap,
      showLines: true,
      showSprites: true,
      reducedMotion: false
    },
    derivedVisualization,
    derivedPresentationOptions,
    selectedId,
    selectedKind
  } = options;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height, false);
  renderer.setClearColor(0x02070d, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x02070d, 6.5, 15);
  const camera = new THREE.PerspectiveCamera(40, width / Math.max(height, 1), 0.1, 80);
  applyCamera(camera, cinemaCamera);
  camera.updateMatrixWorld();
  camera.updateProjectionMatrix();

  let currentProject = project;
  let center = projectCenter(project);
  const worldScale = 74;
  const pickables: THREE.Object3D[] = [];
  const raycaster = new THREE.Raycaster();
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  const particleUniforms = { uTime: { value: 0 } };
  const edgeVisuals = new Map<
    string,
    {
      outerPipe: THREE.Mesh;
      innerPipe: THREE.Mesh;
      rings: THREE.Mesh[];
      throat?: THREE.Mesh;
      valve?: THREE.Mesh;
      bend?: THREE.Mesh;
      particleStart: number;
      particleCount: number;
      coreMaterial: THREE.Material;
    }
  >();
  const nodeVisuals = new Map<string, { group: THREE.Group; ports: Map<PipePortId, THREE.Mesh>; handle?: THREE.Mesh; handleLine?: THREE.Line }>();
  const particleRanges: Array<{ edgeId: string; start: number; count: number }> = [];
  let particleGeometry: THREE.BufferGeometry | null = null;

  function updatePipeMesh(mesh: THREE.Mesh, start: THREE.Vector3, end: THREE.Vector3) {
    const baseLength = Number(mesh.userData.pipeBaseLength ?? 1);
    const length = Math.max(start.distanceTo(end), 0.01);
    mesh.scale.set(1, length / baseLength, 1);
    orientBetweenPoints(mesh, start, end);
  }

  const lights = createCinemaLightRig();
  scene.add(lights.key, lights.fill, lights.rim, lights.ambient);

  // The grid alone carries the ground reference. The dark PlaneGeometry that
  // used to sit here was a large flat rectangle directly beneath the solved
  // slab, and the two rectangles read as one washed-out gradient backdrop.
  const grid = new THREE.GridHelper(12, 24, 0x2b4d63, 0x16293a);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.62;
  grid.name = "Schematic ground reference";
  scene.add(grid);

  const resultWorldSpan = resultWorldSpanForNetwork(project, worldScale);
  const resultSurface = resultDataset
    ? addResultSurfaceMesh(
        scene,
        resultDataset,
        project.visualization.overlay,
        resultFieldSelection,
        resultVectorComponent,
        resultColorMap,
        resultWorldSpan
      )
    : null;
  const streamlineScene = streamlines && resultSurface
    ? addStreamlineScene(scene, streamlines, resultSurface.bounds, resultSurface.meshScale, streamlineDisplay)
    : null;
  const derivedPresentation = derivedVisualization
    ? buildDerivedPresentation(renderer, derivedVisualization, derivedPresentationOptions)
    : null;
  if (derivedPresentation) {
    const physicalBounds = resultDataset ? datasetBounds(resultDataset) : null;
    if (physicalBounds) {
      // Share the solved surface's own scale and lift, so derived overlays stay
      // registered with the data they were derived from.
      const meshScale = resultSurface?.meshScale ?? resultWorldSpan / physicalBounds.span;
      derivedPresentation.group.scale.setScalar(meshScale);
      derivedPresentation.group.position.set(
        -physicalBounds.center[0] * meshScale,
        -physicalBounds.center[1] * meshScale,
        -physicalBounds.center[2] * meshScale + RESULT_SURFACE_Z_OFFSET
      );
    }
    scene.add(derivedPresentation.group);
  }

  const edgeValues = Object.values(result.edgeResults).map((edge) => {
    if (project.visualization.overlay === "pressure") return edge.pressureDrop;
    if (project.visualization.overlay === "reynolds") return edge.reynolds;
    return edge.velocity;
  });
  const maxEdge = Math.max(...edgeValues, 1);
  const particlePositions: number[] = [];
  const particleColors: number[] = [];
  const particlePhases: number[] = [];

  function registerPickable(object: THREE.Object3D, userData: CinemaPick) {
    object.userData = { ...object.userData, cinemaPick: userData };
    pickables.push(object);
  }

  Object.values(project.edges).forEach((edge) => {
    const endpoints = edgeWorldEndpoints(edge, project, center, worldScale);
    const solved = result.edgeResults[edge.id];
    if (!endpoints || !solved) return;
    const { start, end } = endpoints;
    const metric = project.visualization.overlay === "pressure" ? solved.pressureDrop : project.visualization.overlay === "reynolds" ? solved.reynolds : solved.velocity;
    const color = new THREE.Color(overlayValueColor(metric, maxEdge, project.visualization.overlay));
    const radius = Math.max(0.065, edge.shape.kind === "circular" ? edge.shape.diameter * 0.86 : edge.shape.height * 0.76);
    const active = selectedKind === "edge" && selectedId === edge.id;
    const { cage: cageMaterial, core: coreMaterial } = createSchematicPipeMaterials(color, active);
    // A coarse, visibly faceted cage: few enough segments that the wireframe
    // reads as a drawing rather than as a shaded tube, and sparse enough that it
    // never stripes the solved surface behind it.
    const outerPipe = createPipeMesh(start, end, radius * 1.42, cageMaterial, 8);
    const innerPipe = createPipeMesh(start, end, Math.max(0.034, radius * 0.62), coreMaterial, 16);
    outerPipe.name = `Schematic pipe cage ${edge.id}`;
    innerPipe.name = `Schematic pipe core ${edge.id}`;
    registerPickable(outerPipe, { kind: "edge", id: edge.id });
    registerPickable(innerPipe, { kind: "edge", id: edge.id });
    scene.add(outerPipe, innerPipe);

    const ringGeometry = new THREE.TorusGeometry(radius * 1.62, 0.015, 10, 42);
    // Matte, not metallic: this scene has no environment map, so the old
    // metalness of 0.82 rendered near-black except where the removed saturated
    // point lights happened to strike it.
    const ringMaterial = new THREE.MeshStandardMaterial({
      color: active ? 0xffd98a : 0x9fb8c8,
      metalness: 0.18,
      roughness: 0.44,
      transparent: true,
      opacity: active ? 0.62 : 0.36,
      depthWrite: false,
      emissive: 0x0a1620,
      emissiveIntensity: 0.12
    });
    const rings: THREE.Mesh[] = [];
    [0.08, 0.92].forEach((t) => {
      const ring = new THREE.Mesh(ringGeometry.clone(), ringMaterial);
      const point = start.clone().lerp(end, t);
      ring.position.copy(point);
      ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), end.clone().sub(start).normalize());
      registerPickable(ring, { kind: "edge", id: edge.id });
      scene.add(ring);
      rings.push(ring);
    });

    let throat: THREE.Mesh | undefined;
    let valve: THREE.Mesh | undefined;
    let bend: THREE.Mesh | undefined;
    // Fittings stay part of the illustration: matte, translucent, and never
    // self-luminous enough to out-shout the solved surface.
    if (edge.type === "venturi") {
      throat = createPipeMesh(
        start.clone().lerp(end, 0.42),
        start.clone().lerp(end, 0.58),
        Math.max(0.03, radius * 0.35),
        new THREE.MeshStandardMaterial({
          color: 0xe8c775,
          roughness: 0.4,
          metalness: 0.08,
          transparent: true,
          opacity: 0.58,
          depthWrite: false,
          emissive: 0x2a1e08,
          emissiveIntensity: 0.2
        }),
        20
      );
      registerPickable(throat, { kind: "edge", id: edge.id });
      scene.add(throat);
    } else if (edge.type === "valve") {
      const midpoint = start.clone().lerp(end, 0.5);
      valve = new THREE.Mesh(
        new THREE.OctahedronGeometry(radius * 1.65, 0),
        new THREE.MeshStandardMaterial({
          color: 0xe0a266,
          metalness: 0.16,
          roughness: 0.42,
          transparent: true,
          opacity: 0.62,
          depthWrite: false,
          emissive: 0x241203,
          emissiveIntensity: 0.18
        })
      );
      valve.position.copy(midpoint).setZ(radius * 1.7);
      registerPickable(valve, { kind: "edge", id: edge.id });
      scene.add(valve);
    } else if (edge.type === "bend") {
      bend = new THREE.Mesh(
        new THREE.TorusGeometry(radius * 1.3, 0.02, 8, 40),
        new THREE.MeshStandardMaterial({
          color: 0x8fc6dc,
          roughness: 0.4,
          metalness: 0.1,
          transparent: true,
          opacity: 0.6,
          depthWrite: false,
          emissive: 0x0a1e28,
          emissiveIntensity: 0.16
        })
      );
      bend.position.copy(start.clone().lerp(end, 0.5)).setZ(radius * 1.4);
      registerPickable(bend, { kind: "edge", id: edge.id });
      scene.add(bend);
    }

    const particleStart = particlePositions.length / 3;
    const particleCount = Math.max(16, Math.min(56, Math.round(Math.abs(solved.velocity) * 9)));
    for (let index = 0; index < particleCount; index += 1) {
      const t = index / particleCount;
      const point = start.clone().lerp(end, t);
      particlePositions.push(point.x, point.y, point.z + 0.07 + (index % 4) * 0.014);
      particleColors.push(color.r, color.g, color.b);
      particlePhases.push(t + edge.id.length * 0.07);
    }
    particleRanges.push({ edgeId: edge.id, start: particleStart, count: particleCount });
    edgeVisuals.set(edge.id, { outerPipe, innerPipe, rings, throat, valve, bend, particleStart, particleCount, coreMaterial });
  });

  if (particlePositions.length > 0 && project.visualization.particles) {
    particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.Float32BufferAttribute(particlePositions, 3));
    particleGeometry.setAttribute("aColor", new THREE.Float32BufferAttribute(particleColors, 3));
    particleGeometry.setAttribute("aPhase", new THREE.Float32BufferAttribute(particlePhases, 1));
    const particleMaterial = new THREE.ShaderMaterial({
      uniforms: particleUniforms,
      vertexShader: flowParticleVertexShader,
      fragmentShader: flowParticleFragmentShader,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const particlePoints = new THREE.Points(particleGeometry, particleMaterial);
    particlePoints.name = "Schematic flow ticks (illustrative)";
    scene.add(particlePoints);
  }

  Object.values(project.nodes).forEach((node) => {
    const position = worldFromNetwork(node.position, center, worldScale, 0.08);
    const active = selectedKind === "node" && selectedId === node.id;
    const nodeGroup = new THREE.Group();
    const nodePortMeshes = new Map<PipePortId, THREE.Mesh>();
    let nodeHandle: THREE.Mesh | undefined;
    let nodeHandleLine: THREE.Line | undefined;
    nodeGroup.position.copy(position);
    nodeGroup.rotation.z = -degreesToRadians(node.rotation ?? 0);
    nodeGroup.name = `Schematic component ${node.id}`;
    // Equipment symbols: matte and translucent so they stay part of the drawing.
    // Metalness is low because there is no environment map for a metal to
    // reflect - the old high-metalness values only looked right under the
    // saturated point lights that have been removed.
    const material = new THREE.MeshStandardMaterial({
      color: active ? 0xf0c563 : node.type === "pump" ? 0x8ba0b1 : node.type === "mixer" ? 0x3f939a : 0x2b556d,
      metalness: node.type === "pump" ? 0.24 : 0.14,
      roughness: 0.45,
      transparent: true,
      opacity: active ? 0.82 : 0.66,
      emissive: active ? 0x3a2900 : 0x08161f,
      emissiveIntensity: active ? 0.4 : 0.14
    });

    let body: THREE.Mesh;
    if (node.type === "pump") {
      body = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.3, 40), material);
      body.rotation.x = Math.PI / 2;
      const impeller = new THREE.Mesh(
        new THREE.TorusGeometry(0.22, 0.03, 14, 42),
        new THREE.MeshStandardMaterial({ color: 0xc2d3de, metalness: 0.22, roughness: 0.4, transparent: true, opacity: 0.72, emissive: 0x0a141b, emissiveIntensity: 0.12 })
      );
      impeller.position.z = 0.03;
      nodeGroup.add(impeller);
    } else if (node.type === "source" || node.type === "sink") {
      // Ghosted vessel. `transmission` needs an environment to refract and this
      // scene has none, so a plainly translucent matte shell reads better and
      // behaves identically wherever the vessel sits.
      body = new THREE.Mesh(
        new THREE.CylinderGeometry(0.42, 0.42, 0.66, 48, 1, true),
        new THREE.MeshStandardMaterial({
          color: 0x7fc8e0,
          transparent: true,
          opacity: 0.24,
          roughness: 0.4,
          metalness: 0.04,
          side: THREE.DoubleSide,
          depthWrite: false,
          emissive: 0x0a2733,
          emissiveIntensity: 0.18
        })
      );
      body.rotation.x = Math.PI / 2;
      body.position.z = 0.14;
    } else if (node.type === "mixer") {
      body = new THREE.Mesh(new THREE.SphereGeometry(0.34, 36, 20), material);
      const swirl = new THREE.Mesh(
        new THREE.TorusKnotGeometry(0.22, 0.015, 84, 8),
        new THREE.MeshBasicMaterial({ color: 0x8ed2e6, transparent: true, opacity: 0.42, depthWrite: false })
      );
      nodeGroup.add(swirl);
    } else {
      body = new THREE.Mesh(new THREE.SphereGeometry(0.2, 30, 16), material);
    }
    registerPickable(body, { kind: "node", id: node.id });
    nodeGroup.add(body);
    const pickVolume = new THREE.Mesh(
      new THREE.SphereGeometry(node.type === "source" || node.type === "sink" ? 0.28 : 0.24, 16, 10),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.001, depthWrite: false })
    );
    registerPickable(pickVolume, { kind: "node", id: node.id });
    nodeGroup.add(pickVolume);

    ports.forEach((port) => {
      const portPoint = worldFromNetwork(portPosition(node, port), center, worldScale, 0.1);
      const portMesh = new THREE.Mesh(new THREE.SphereGeometry(port === "inlet" || port === "outlet" ? 0.064 : 0.048, 18, 12), new THREE.MeshBasicMaterial({ color: port === "inlet" || port === "outlet" ? 0x9dfbd7 : 0x8aa0b8 }));
      portMesh.position.copy(portPoint);
      registerPickable(portMesh, { kind: "port", nodeId: node.id, port, point: portPosition(node, port) });
      scene.add(portMesh);
      nodePortMeshes.set(port, portMesh);
    });

    if (active) {
      const handlePoint = worldFromNetwork(aimHandlePosition(node), center, worldScale, 0.16);
      nodeHandle = new THREE.Mesh(new THREE.SphereGeometry(0.08, 20, 14), new THREE.MeshBasicMaterial({ color: 0xffd54f }));
      nodeHandle.position.copy(handlePoint);
      registerPickable(nodeHandle, { kind: "rotate", nodeId: node.id });
      scene.add(nodeHandle);
      const handleLineGeometry = new THREE.BufferGeometry().setFromPoints([position.clone().setZ(0.13), handlePoint]);
      nodeHandleLine = new THREE.Line(handleLineGeometry, new THREE.LineBasicMaterial({ color: 0xffd54f, transparent: true, opacity: 0.72 }));
      scene.add(nodeHandleLine);
    }

    scene.add(nodeGroup);
    nodeVisuals.set(node.id, { group: nodeGroup, ports: nodePortMeshes, handle: nodeHandle, handleLine: nodeHandleLine });
  });

  function pickAt(event: Pick<PointerEvent, "clientX" | "clientY">): CinemaPick | null {
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const hits = raycaster.intersectObjects(pickables, false);
    const picks = hits.map((hit) => hit.object.userData.cinemaPick as CinemaPick | undefined).filter((pick): pick is CinemaPick => Boolean(pick));
    const priority = ["rotate", "port", "node", "edge"] satisfies CinemaPick["kind"][];
    for (const kind of priority) {
      const pick = picks.find((candidate) => candidate.kind === kind);
      if (pick) return pick;
    }
    return null;
  }

  function probeAt(event: Pick<PointerEvent, "clientX" | "clientY">): CinemaResultProbe | null {
    if (!resultDataset || !resultSurface) return null;
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const hit = raycaster.intersectObject(resultSurface.surface, false)[0];
    const triangleIndex = hit?.faceIndex;
    if (!hit || triangleIndex === undefined || triangleIndex === null || triangleIndex < 0) return null;
    const triangle = resultSurface.triangles[triangleIndex];
    if (!triangle) return null;
    const physicalPoint: [number, number, number] = [
      hit.point.x / resultSurface.meshScale + resultSurface.bounds.center[0],
      hit.point.y / resultSurface.meshScale + resultSurface.bounds.center[1],
      (hit.point.z - RESULT_SURFACE_Z_OFFSET) / resultSurface.meshScale + resultSurface.bounds.center[2]
    ];
    const [firstIndex, secondIndex, thirdIndex] = triangle.pointIndices;
    const barycentric = new THREE.Triangle(
      new THREE.Vector3(...resultDataset.points[firstIndex]),
      new THREE.Vector3(...resultDataset.points[secondIndex]),
      new THREE.Vector3(...resultDataset.points[thirdIndex])
    ).getBarycoord(new THREE.Vector3(...physicalPoint), new THREE.Vector3());
    if (!barycentric) return null;
    let nearestPointIndex = triangle.pointIndices[0];
    let nearestDistance = Number.POSITIVE_INFINITY;
    triangle.pointIndices.forEach((pointIndex) => {
      const point = resultDataset.points[pointIndex];
      const distance = Math.hypot(
        point[0] - physicalPoint[0],
        point[1] - physicalPoint[1],
        point[2] - physicalPoint[2]
      );
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestPointIndex = pointIndex;
      }
    });
    return {
      point: physicalPoint,
      ownerCellIndex: triangle.ownerCellIndex,
      nearestPointIndex,
      trianglePointIndices: triangle.pointIndices,
      barycentricWeights: [barycentric.x, barycentric.y, barycentric.z]
    };
  }

  function pointAt(event: Pick<PointerEvent, "clientX" | "clientY">): Vec2 | null {
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const world = new THREE.Vector3();
    const hit = raycaster.ray.intersectPlane(plane, world);
    if (!hit) return null;
    return networkFromWorld(world, center, worldScale);
  }

  function fitCamera(settings: CinemaCameraState, nextProject: FluidProject): CinemaCameraState {
    const nodes = Object.values(nextProject.nodes);
    if (nodes.length === 0) return { ...settings, zoom: 1, pan: { x: 0, y: 0 } };
    const minX = Math.min(...nodes.map((node) => node.position.x));
    const maxX = Math.max(...nodes.map((node) => node.position.x));
    const minY = Math.min(...nodes.map((node) => node.position.y));
    const maxY = Math.max(...nodes.map((node) => node.position.y));
    const worldSpan = Math.max(maxX - minX, maxY - minY, 1) / worldScale;
    const requiredDistance = Math.max(4.45, (worldSpan * 1.3) / (2 * Math.tan(degreesToRadians(camera.fov / 2))));
    return {
      ...settings,
      zoom: Math.max(0.25, Math.min(4, 8.9 / requiredDistance)),
      pan: { x: 0, y: 0 }
    };
  }

  function updateModel(nextProject: FluidProject, nextResult: SimulationResult) {
    currentProject = nextProject;
    const nextCenter = projectCenter(nextProject);
    center.x = nextCenter.x;
    center.y = nextCenter.y;
    Object.values(nextProject.nodes).forEach((node) => {
      const visual = nodeVisuals.get(node.id);
      if (!visual) return;
      visual.group.position.copy(worldFromNetwork(node.position, center, worldScale, 0.08));
      visual.group.rotation.z = -degreesToRadians(node.rotation ?? 0);
      ports.forEach((port) => {
        const portMesh = visual.ports.get(port);
        if (portMesh) portMesh.position.copy(worldFromNetwork(portPosition(node, port), center, worldScale, 0.1));
      });
      if (visual.handle) visual.handle.position.copy(worldFromNetwork(aimHandlePosition(node), center, worldScale, 0.16));
      if (visual.handleLine) {
        const handlePoint = worldFromNetwork(aimHandlePosition(node), center, worldScale, 0.16);
        const nodePoint = worldFromNetwork(node.position, center, worldScale, 0.13);
        visual.handleLine.geometry.dispose();
        visual.handleLine.geometry = new THREE.BufferGeometry().setFromPoints([nodePoint, handlePoint]);
      }
    });

    Object.values(nextProject.edges).forEach((edge) => {
      const visual = edgeVisuals.get(edge.id);
      const endpoints = edgeWorldEndpoints(edge, nextProject, center, worldScale);
      if (!visual || !endpoints) return;
      updatePipeMesh(visual.outerPipe, endpoints.start, endpoints.end);
      updatePipeMesh(visual.innerPipe, endpoints.start, endpoints.end);
      visual.rings.forEach((ring, index) => {
        const point = endpoints.start.clone().lerp(endpoints.end, index === 0 ? 0.08 : 0.92);
        ring.position.copy(point);
        ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), endpoints.end.clone().sub(endpoints.start).normalize());
      });
      if (visual.throat) updatePipeMesh(visual.throat, endpoints.start.clone().lerp(endpoints.end, 0.42), endpoints.start.clone().lerp(endpoints.end, 0.58));
      if (visual.valve) visual.valve.position.copy(endpoints.start.clone().lerp(endpoints.end, 0.5)).setZ(0.12);
      if (visual.bend) visual.bend.position.copy(endpoints.start.clone().lerp(endpoints.end, 0.5)).setZ(0.1);
    });

    const particlePosition = particleGeometry?.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (particlePosition) {
      particleRanges.forEach(({ edgeId, start, count }) => {
        const edge = nextProject.edges[edgeId];
        const endpoints = edge ? edgeWorldEndpoints(edge, nextProject, center, worldScale) : null;
        if (!endpoints) return;
        for (let index = 0; index < count; index += 1) {
          const point = endpoints.start.clone().lerp(endpoints.end, index / count);
          particlePosition.setXYZ(start + index, point.x, point.y, point.z + 0.07 + (index % 4) * 0.014);
        }
      });
      particlePosition.needsUpdate = true;
    }

    camera.updateMatrixWorld();
    Object.assign(projectedPositions, projectedNodePositions(nextProject, center, worldScale, camera, width, height));
    renderer.render(scene, camera);
    void nextResult;
  }

  function resize() {
    const nextRect = canvas.getBoundingClientRect();
    const nextWidth = Math.max(1, nextRect.width);
    const nextHeight = Math.max(1, nextRect.height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(nextWidth, nextHeight, false);
    camera.aspect = nextWidth / nextHeight;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
  }

  function dispose() {
    derivedPresentation?.dispose();
    scene.traverse((object: THREE.Object3D) => {
      const mesh = object as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose();
      const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
      else material?.dispose();
    });
    renderer.dispose();
  }

  const projectedPositions = projectedNodePositions(project, center, worldScale, camera, width, height);
  const runtime: CinemaRuntime = {
    center,
    worldScale,
    pickableCount: pickables.length,
    projectedNodePositions: projectedPositions,
    engine: `three.js r${THREE.REVISION}`,
    derivedFallback: derivedPresentation?.fallback ?? "none",
    render(time: number, advancePreview = true) {
      if (advancePreview) particleUniforms.uTime.value = time / 1000;
      streamlineScene?.update(time, advancePreview);
      if (advancePreview) derivedPresentation?.render(time / 1000);
      renderer.render(scene, camera);
    },
    updateModel,
    fitCamera,
    updateCamera(settings: CinemaCameraState) {
      applyCamera(camera, settings);
      camera.updateMatrixWorld();
      Object.assign(projectedPositions, projectedNodePositions(currentProject, center, worldScale, camera, width, height));
      renderer.render(scene, camera);
    },
    resize,
    dispose,
    pickAt,
    probeAt,
    pointAt
  };
  recordEditorMetric("cinema-build", performance.now() - buildStarted);
  return runtime;
}
