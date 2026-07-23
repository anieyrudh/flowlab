import * as THREE from "three";
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
    float alpha = smoothstep(0.5, 0.16, length(uv));
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

function createPipeMesh(start: THREE.Vector3, end: THREE.Vector3, radius: number, material: THREE.Material) {
  const length = Math.max(start.distanceTo(end), 0.01);
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 36, 1, true), material);
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
  resultColorMap: ResultColorMap
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
  const meshScale = 5.2 / bounds.span;
  const triangles = resultSurfaceTriangles(dataset);

  triangles.forEach(({ pointIndices, ownerCellIndex }) => {
    const cell = dataset.cells[ownerCellIndex];
    pointIndices.forEach((pointIndex) => {
      const point = dataset.points[pointIndex];
      const color = new THREE.Color(
        resultValueColor(fieldValueForDatasetPoint(fieldValues.values, fieldValues.location, pointIndex, ownerCellIndex, cell), maxValue, resultColorMap)
      );
      positions.push((point[0] - bounds.center[0]) * meshScale, (point[1] - bounds.center[1]) * meshScale, (point[2] - bounds.center[2]) * meshScale + 0.16);
      colors.push(color.r, color.g, color.b);
    });
  });

  if (positions.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const material = new THREE.MeshBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.82,
    side: THREE.DoubleSide,
    depthWrite: true
  });
  const surface = new THREE.Mesh(geometry, material);
  surface.name = `VTK ${fieldValues.field} exterior surface`;
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
  resultFieldSelection: ResultFieldSelection | null;
  resultVectorComponent: ResultVectorComponent;
  resultColorMap: ResultColorMap;
  selectedId: string | null;
  selectedKind?: "node" | "edge" | null;
}): CinemaRuntime {
  const buildStarted = performance.now();
  const { canvas, width, height, project, result, cinemaCamera = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } }, resultDataset, resultFieldSelection, resultVectorComponent, resultColorMap, selectedId, selectedKind } = options;
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

  scene.add(new THREE.AmbientLight(0x87c8ff, 0.58));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.25);
  keyLight.position.set(-3.8, -5.2, 7.2);
  scene.add(keyLight);
  const rimLight = new THREE.PointLight(0x20d7ff, 3.8, 14);
  rimLight.position.set(4, -2, 3.4);
  scene.add(rimLight);
  const warmLight = new THREE.PointLight(0xffbe4d, 2.1, 10);
  warmLight.position.set(0.5, 2.8, 1.4);
  scene.add(warmLight);

  const grid = new THREE.GridHelper(12, 24, 0x24465b, 0x132636);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.42;
  scene.add(grid);

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(16, 10),
    new THREE.MeshBasicMaterial({ color: 0x06121c, transparent: true, opacity: 0.58, side: THREE.DoubleSide })
  );
  floor.position.z = -0.45;
  scene.add(floor);

  const resultSurface = resultDataset
    ? addResultSurfaceMesh(
        scene,
        resultDataset,
        project.visualization.overlay,
        resultFieldSelection,
        resultVectorComponent,
        resultColorMap
      )
    : null;

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
    const glassMaterial = new THREE.MeshPhysicalMaterial({
      color: 0x68e8ff,
      transparent: true,
      opacity: active ? 0.4 : 0.26,
      roughness: 0.13,
      metalness: 0.06,
      transmission: 0.52,
      thickness: 0.48,
      emissive: active ? 0x4bd7ff : 0x052f42,
      emissiveIntensity: active ? 0.9 : 0.34
    });
    const coreMaterial = new THREE.MeshStandardMaterial({
      color,
      transparent: true,
      opacity: 0.88,
      roughness: 0.22,
      metalness: 0.04,
      emissive: color,
      emissiveIntensity: active ? 1.2 : 0.62
    });
    const outerPipe = createPipeMesh(start, end, radius * 1.42, glassMaterial);
    const innerPipe = createPipeMesh(start, end, Math.max(0.034, radius * 0.62), coreMaterial);
    registerPickable(outerPipe, { kind: "edge", id: edge.id });
    registerPickable(innerPipe, { kind: "edge", id: edge.id });
    scene.add(outerPipe, innerPipe);

    const ringGeometry = new THREE.TorusGeometry(radius * 1.62, 0.015, 10, 42);
    const ringMaterial = new THREE.MeshStandardMaterial({ color: active ? 0xffd54f : 0xa8d2e2, metalness: 0.82, roughness: 0.2, emissive: active ? 0x5f4200 : 0x06131a });
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
    if (edge.type === "venturi") {
      throat = createPipeMesh(
        start.clone().lerp(end, 0.42),
        start.clone().lerp(end, 0.58),
        Math.max(0.03, radius * 0.35),
        new THREE.MeshStandardMaterial({ color: 0xffd54f, emissive: 0xff6b35, emissiveIntensity: 1.1, roughness: 0.18 })
      );
      registerPickable(throat, { kind: "edge", id: edge.id });
      scene.add(throat);
    } else if (edge.type === "valve") {
      const midpoint = start.clone().lerp(end, 0.5);
      valve = new THREE.Mesh(new THREE.OctahedronGeometry(radius * 1.65, 0), new THREE.MeshStandardMaterial({ color: 0xff9f43, metalness: 0.58, roughness: 0.2, emissive: 0x3a1800 }));
      valve.position.copy(midpoint).setZ(radius * 1.7);
      registerPickable(valve, { kind: "edge", id: edge.id });
      scene.add(valve);
    } else if (edge.type === "bend") {
      bend = new THREE.Mesh(new THREE.TorusGeometry(radius * 1.3, 0.02, 8, 40), new THREE.MeshStandardMaterial({ color: 0x66d9ff, emissive: 0x073344, roughness: 0.16 }));
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
    scene.add(new THREE.Points(particleGeometry, particleMaterial));
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
    const material = new THREE.MeshStandardMaterial({
      color: active ? 0xffd54f : node.type === "pump" ? 0x73889a : node.type === "mixer" ? 0x1e8a92 : 0x123447,
      metalness: node.type === "pump" ? 0.76 : 0.42,
      roughness: 0.22,
      emissive: active ? 0x4f3700 : 0x041722,
      emissiveIntensity: active ? 0.9 : 0.28
    });

    let body: THREE.Mesh;
    if (node.type === "pump") {
      body = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.3, 40), material);
      body.rotation.x = Math.PI / 2;
      const impeller = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.03, 14, 42), new THREE.MeshStandardMaterial({ color: 0xb2c7d6, metalness: 0.82, roughness: 0.16, emissive: 0x08131a }));
      impeller.position.z = 0.03;
      nodeGroup.add(impeller);
    } else if (node.type === "source" || node.type === "sink") {
      body = new THREE.Mesh(
        new THREE.CylinderGeometry(0.42, 0.42, 0.66, 48, 1, true),
        new THREE.MeshPhysicalMaterial({ color: 0x4bd7ff, transparent: true, opacity: 0.32, roughness: 0.08, metalness: 0.02, transmission: 0.34, thickness: 0.3, emissive: 0x06344a })
      );
      body.rotation.x = Math.PI / 2;
      body.position.z = 0.14;
    } else if (node.type === "mixer") {
      body = new THREE.Mesh(new THREE.SphereGeometry(0.34, 36, 20), material);
      const swirl = new THREE.Mesh(new THREE.TorusKnotGeometry(0.22, 0.015, 84, 8), new THREE.MeshBasicMaterial({ color: 0x35cfff, transparent: true, opacity: 0.7 }));
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
      (hit.point.z - 0.16) / resultSurface.meshScale + resultSurface.bounds.center[2]
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
    render(time: number, advancePreview = true) {
      if (advancePreview) particleUniforms.uTime.value = time / 1000;
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
