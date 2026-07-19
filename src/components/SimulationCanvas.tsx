import { useEffect, useRef, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import {
  fieldValuesForOverlay,
  fieldValuesForSelection,
  formatFieldValueKind,
  projectDatasetToCanvas,
  type ResultFieldSelection,
  type ResultVectorComponent
} from "../results/vtk";
import type {
  EdgeResult,
  CanvasRenderMode,
  FluidEdge,
  FluidNode,
  FluidProject,
  OverlayMode,
  PipePortId,
  ResultCamera,
  ResultColorMap,
  ResultViewMode,
  SimulationResult,
  VtkResultDataset,
  Vec2
} from "../types";
import type { CinemaPick, CinemaRuntime } from "./cinemaRenderer";
import { recordEditorFrame, recordEditorMetric } from "../performance/editorProfiler";
import {
  clampViewportZoom,
  defaultSchematicViewport,
  fitSchematicViewport,
  panSchematicViewport,
  resetSchematicViewport,
  screenToWorld,
  type CinemaCameraState,
  type SchematicViewport,
  worldToScreen,
  zoomViewportAtPoint
} from "./viewportModel";

type Props = {
  project: FluidProject;
  result: SimulationResult;
  resultDataset?: VtkResultDataset | null;
  resultFieldSelection?: ResultFieldSelection | null;
  resultVectorComponent?: ResultVectorComponent;
  resultColorMap?: ResultColorMap;
  canvasRenderMode?: CanvasRenderMode;
  cinemaCamera?: CinemaCameraState;
  resultViewMode?: ResultViewMode;
  resultCamera?: ResultCamera;
  selectedId: string | null;
  selectedKind?: "node" | "edge" | null;
  onSelect: (kind: "node" | "edge", id: string) => void;
  onMoveNode: (id: string, position: Vec2) => void;
  onRotateNode?: (id: string, rotation: number) => void;
  onConnectEdge?: (from: string, to: string, fromPort: PipePortId, toPort: PipePortId) => void;
  onUpdateEdgeEndpoint?: (edgeId: string, endpoint: "from" | "to", nodeId: string, port: PipePortId) => void;
  onProbePoint?: (point: Vec2, size: { width: number; height: number }) => void;
  previewPlaying?: boolean;
  onCinemaCameraChange?: (camera: CinemaCameraState) => void;
};

type PortHit = { nodeId: string; port: PipePortId; point: Vec2 };
type ViewTransform = { scale: number; offset: Vec2 };
type DragState =
  | { kind: "node"; id: string; offsetX: number; offsetY: number; position: Vec2 }
  | { kind: "connect"; from: PortHit; pointer: Vec2 }
  | { kind: "endpoint"; edgeId: string; endpoint: "from" | "to"; pointer: Vec2 }
  | { kind: "rotate"; nodeId: string; rotation: number }
  | { kind: "canvas-pan"; start: Vec2; viewport: SchematicViewport; moved: boolean }
  | { kind: "cinema-orbit"; startX: number; startY: number; camera: CinemaCameraState; moved: boolean }
  | { kind: "cinema-pan"; startX: number; startY: number; camera: CinemaCameraState; moved: boolean };

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

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace("#", "");
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16)
  ];
}

function depthShade(color: string, level: number) {
  const [red, green, blue] = hexToRgb(color);
  const light = Math.max(0.68, Math.min(1.18, level));
  return `rgb(${Math.min(255, Math.round(red * light))}, ${Math.min(255, Math.round(green * light))}, ${Math.min(255, Math.round(blue * light))})`;
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

function edgeMidpoint(edge: FluidEdge, nodes: Record<string, FluidNode>) {
  const from = nodes[edge.from].position;
  const to = nodes[edge.to].position;
  return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
}

function endpointPoint(edge: FluidEdge, endpoint: "from" | "to", nodes: Record<string, FluidNode>) {
  const node = nodes[endpoint === "from" ? edge.from : edge.to];
  if (!node) return null;
  return portPosition(node, endpoint === "from" ? (edge.fromPort ?? "outlet") : (edge.toPort ?? "inlet"));
}

function computeViewTransform(project: FluidProject, width: number, height: number): ViewTransform {
  const positions = Object.values(project.nodes).map((node) => node.position);
  if (!positions.length) return { scale: 1, offset: { x: 0, y: 0 } };
  const minX = Math.min(...positions.map((point) => point.x));
  const maxX = Math.max(...positions.map((point) => point.x));
  const minY = Math.min(...positions.map((point) => point.y));
  const maxY = Math.max(...positions.map((point) => point.y));
  const padding = 96;
  const worldWidth = Math.max(1, maxX - minX + padding * 2);
  const worldHeight = Math.max(1, maxY - minY + padding * 2);
  const rawScale = Math.min((width - 48) / worldWidth, (height - 48) / worldHeight);
  if (rawScale >= 1) return { scale: 1, offset: { x: 0, y: 0 } };
  const scale = Math.max(0.45, rawScale);
  return {
    scale,
    offset: {
      x: (width - (minX + maxX) * scale) / 2,
      y: (height - (minY + maxY) * scale) / 2
    }
  };
}

function draftNode(node: FluidNode, drag: DragState | null): FluidNode {
  if (drag?.kind === "node" && drag.id === node.id) return { ...node, position: drag.position };
  if (drag?.kind === "rotate" && drag.nodeId === node.id) return { ...node, rotation: drag.rotation };
  return node;
}

function vectorMagnitude(vector: [number, number, number]) {
  return Math.hypot(vector[0], vector[1], vector[2]);
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
    span: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1)
  };
}

function makeCameraProjection(dataset: VtkResultDataset, camera: ResultCamera, canvasWidth: number, canvasHeight: number) {
  const bounds = datasetBounds(dataset);
  const yaw = degreesToRadians(camera.yaw);
  const pitch = degreesToRadians(camera.pitch);
  const cosYaw = Math.cos(yaw);
  const sinYaw = Math.sin(yaw);
  const cosPitch = Math.cos(pitch);
  const sinPitch = Math.sin(pitch);
  const scale = (Math.min(canvasWidth, canvasHeight) * 0.62 * camera.zoom) / bounds.span;

  function rotate(point: [number, number, number]) {
    const x = point[0] - bounds.center[0];
    const y = point[1] - bounds.center[1];
    const z = point[2] - bounds.center[2];
    const yawX = x * cosYaw - y * sinYaw;
    const yawY = x * sinYaw + y * cosYaw;
    const pitchY = yawY * cosPitch - z * sinPitch;
    const pitchZ = yawY * sinPitch + z * cosPitch;
    return { x: yawX, y: pitchY, z: pitchZ };
  }

  function project(point: [number, number, number]) {
    const rotated = rotate(point);
    return {
      x: canvasWidth / 2 + rotated.x * scale,
      y: canvasHeight / 2 - rotated.y * scale,
      depth: rotated.z
    };
  }

  return {
    bounds,
    point: project,
    vectorFrom(point: [number, number, number], vector: [number, number, number], lengthRatio = 0.12) {
      const base = project(point);
      const magnitude = vectorMagnitude(vector) || 1;
      const endPoint: [number, number, number] = [
        point[0] + (vector[0] / magnitude) * bounds.span * lengthRatio,
        point[1] + (vector[1] / magnitude) * bounds.span * lengthRatio,
        point[2] + (vector[2] / magnitude) * bounds.span * lengthRatio
      ];
      const end = project(endPoint);
      return { base, end };
    }
  };
}

function cellCenter(dataset: VtkResultDataset, cell: number[]): [number, number, number] {
  return cell.reduce(
    (sum, index) => {
      const point = dataset.points[index];
      return [sum[0] + point[0] / cell.length, sum[1] + point[1] / cell.length, sum[2] + point[2] / cell.length] as [number, number, number];
    },
    [0, 0, 0] as [number, number, number]
  );
}

function averageVector(dataset: VtkResultDataset, cell: number[], vectors: [number, number, number][], location: "point" | "cell", cellIndex: number): [number, number, number] {
  if (location === "cell") return vectors[cellIndex] ?? [0, 0, 0];
  return cell.reduce(
    (sum, pointIndex) => {
      const vector = vectors[pointIndex] ?? [0, 0, 0];
      return [sum[0] + vector[0] / cell.length, sum[1] + vector[1] / cell.length, sum[2] + vector[2] / cell.length] as [number, number, number];
    },
    [0, 0, 0] as [number, number, number]
  );
}

function drawCameraAxes(context: CanvasRenderingContext2D, projection: ReturnType<typeof makeCameraProjection>, canvasWidth: number, canvasHeight: number) {
  const origin: [number, number, number] = [projection.bounds.min[0], projection.bounds.min[1], projection.bounds.min[2]];
  const length = projection.bounds.span * 0.22;
  const axes: Array<{ label: string; color: string; point: [number, number, number] }> = [
    { label: "X", color: "#ff6a3a", point: [origin[0] + length, origin[1], origin[2]] },
    { label: "Y", color: "#67f3a5", point: [origin[0], origin[1] + length, origin[2]] },
    { label: "Z", color: "#66a3ff", point: [origin[0], origin[1], origin[2] + length] }
  ];
  const projectedOrigin = projection.point(origin);
  context.save();
  context.font = "700 11px Inter, system-ui, sans-serif";
  for (const axis of axes) {
    const projected = projection.point(axis.point);
    context.strokeStyle = axis.color;
    context.fillStyle = axis.color;
    context.lineWidth = 1.8;
    context.beginPath();
    context.moveTo(projectedOrigin.x, projectedOrigin.y);
    context.lineTo(projected.x, projected.y);
    context.stroke();
    context.fillText(axis.label, projected.x + 5, projected.y + 3);
  }
  context.fillStyle = "rgba(238, 248, 255, 0.72)";
  context.fillText("3D", canvasWidth - 48, canvasHeight - 28);
  context.restore();
}

function drawResultDataset(
  context: CanvasRenderingContext2D,
  dataset: VtkResultDataset,
  overlay: OverlayMode,
  resultFieldSelection: ResultFieldSelection | null,
  resultVectorComponent: ResultVectorComponent,
  resultColorMap: ResultColorMap,
  resultViewMode: ResultViewMode,
  resultCamera: ResultCamera,
  canvasWidth: number,
  canvasHeight: number
) {
  const fieldValues = resultFieldSelection ? fieldValuesForSelection(dataset, resultFieldSelection, resultVectorComponent) : fieldValuesForOverlay(dataset, overlay);
  if (!fieldValues) return;
  const vectors = fieldValues.location === "cell" ? dataset.cellData.vectors[fieldValues.field] : dataset.pointData.vectors[fieldValues.field];

  const values = fieldValues.values;
  const maxValue = Math.max(...values.map((value) => Math.abs(value)), 1e-9);
  if (resultViewMode === "3d") {
    const projection = makeCameraProjection(dataset, resultCamera, canvasWidth, canvasHeight);
    const projectedCells = dataset.cells
      .map((cell, cellIndex) => {
        const points = cell.map((index) => projection.point(dataset.points[index]));
        const averageDepth = points.reduce((sum, point) => sum + point.depth / points.length, 0);
        const value =
          fieldValues.location === "cell"
            ? (values[cellIndex] ?? 0)
            : cell.reduce((sum, index) => sum + (values[index] ?? 0), 0) / cell.length;
        return { cell, cellIndex, points, averageDepth, value };
      })
      .sort((left, right) => left.averageDepth - right.averageDepth);

    const depths = projectedCells.map((cell) => cell.averageDepth);
    const minDepth = Math.min(...depths, 0);
    const maxDepth = Math.max(...depths, 1);
    const depthRange = Math.max(maxDepth - minDepth, 1e-9);

    context.save();
    context.globalAlpha = 0.86;
    for (const projectedCell of projectedCells) {
      if (projectedCell.points.length < 3) continue;
      const first = projectedCell.points[0];
      const depthLevel = 0.72 + ((projectedCell.averageDepth - minDepth) / depthRange) * 0.34;
      context.beginPath();
      context.moveTo(first.x, first.y);
      for (const point of projectedCell.points.slice(1)) context.lineTo(point.x, point.y);
      context.closePath();
      context.fillStyle = depthShade(resultValueColor(projectedCell.value, maxValue, resultColorMap), depthLevel);
      context.fill();
      context.strokeStyle = "rgba(238, 248, 255, 0.34)";
      context.lineWidth = 1.1;
      context.stroke();
    }
    context.globalAlpha = 1;

    if (vectors) {
      context.strokeStyle = "rgba(255, 255, 255, 0.86)";
      context.fillStyle = "rgba(255, 255, 255, 0.86)";
      context.lineWidth = 1.5;
      projectedCells.forEach(({ cell, cellIndex }) => {
        const center = cellCenter(dataset, cell);
        const vector = averageVector(dataset, cell, vectors, fieldValues.location, cellIndex);
        if (vectorMagnitude(vector) <= 0) return;
        const arrow = projection.vectorFrom(center, vector);
        context.beginPath();
        context.moveTo(arrow.base.x, arrow.base.y);
        context.lineTo(arrow.end.x, arrow.end.y);
        context.stroke();
        context.beginPath();
        context.arc(arrow.end.x, arrow.end.y, 2.6, 0, Math.PI * 2);
        context.fill();
      });
    }

    drawCameraAxes(context, projection, canvasWidth, canvasHeight);
    context.fillStyle = "rgba(238, 248, 255, 0.86)";
    context.font = "600 12px Inter, system-ui, sans-serif";
    context.fillText(
      `VTK 3D: ${fieldValues.field} ${formatFieldValueKind(fieldValues.kind)} (${fieldValues.location}) · yaw ${Math.round(resultCamera.yaw)} pitch ${Math.round(resultCamera.pitch)}`,
      150,
      canvasHeight - 114
    );
    context.restore();
    return;
  }

  const projection = projectDatasetToCanvas(dataset, canvasWidth, canvasHeight);

  context.save();
  context.globalAlpha = 0.78;
  for (const cell of dataset.cells) {
    if (cell.length < 3) continue;
    const cellIndex = dataset.cells.indexOf(cell);
    const cellValue =
      fieldValues.location === "cell"
        ? (values[cellIndex] ?? 0)
        : cell.reduce((sum, index) => sum + (values[index] ?? 0), 0) / cell.length;
    const first = projection.point(cell[0]);
    context.beginPath();
    context.moveTo(first.x, first.y);
    for (const index of cell.slice(1)) {
      const point = projection.point(index);
      context.lineTo(point.x, point.y);
    }
    context.closePath();
    context.fillStyle = resultValueColor(cellValue, maxValue, resultColorMap);
    context.fill();
    context.strokeStyle = "rgba(238, 248, 255, 0.28)";
    context.lineWidth = 1;
    context.stroke();
  }
  context.globalAlpha = 1;

  if (vectors) {
    context.strokeStyle = "rgba(255, 255, 255, 0.84)";
    context.fillStyle = "rgba(255, 255, 255, 0.84)";
    context.lineWidth = 1.6;
    for (const cell of dataset.cells) {
      const cellIndex = dataset.cells.indexOf(cell);
      const center = cell.reduce(
        (sum, index) => {
          const point = projection.point(index);
          return { x: sum.x + point.x / cell.length, y: sum.y + point.y / cell.length };
        },
        { x: 0, y: 0 }
      );
      const average =
        fieldValues.location === "cell"
          ? { x: (vectors[cellIndex] ?? [0, 0, 0])[0], y: (vectors[cellIndex] ?? [0, 0, 0])[1] }
          : cell.reduce(
              (sum, index) => {
                const vector = vectors[index] ?? [0, 0, 0];
                return { x: sum.x + vector[0] / cell.length, y: sum.y + vector[1] / cell.length };
              },
              { x: 0, y: 0 }
            );
      const length = Math.hypot(average.x, average.y) || 1;
      const arrowLength = 18 + Math.min(length / maxValue, 1) * 28;
      const x2 = center.x + (average.x / length) * arrowLength;
      const y2 = center.y + (average.y / length) * arrowLength;
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.lineTo(x2, y2);
      context.stroke();
      context.beginPath();
      context.arc(x2, y2, 2.8, 0, Math.PI * 2);
      context.fill();
    }
  }

  context.fillStyle = "rgba(238, 248, 255, 0.86)";
  context.font = "600 12px Inter, system-ui, sans-serif";
  context.fillText(`VTK field: ${fieldValues.field} ${formatFieldValueKind(fieldValues.kind)} (${fieldValues.location})`, 150, canvasHeight - 114);
  context.restore();
}

function browserSupportsWebGL() {
  try {
    const probe = document.createElement("canvas");
    const context = probe.getContext("webgl2") ?? probe.getContext("webgl");
    return Boolean(context && typeof context.getParameter === "function");
  } catch {
    return false;
  }
}

export function SimulationCanvas({
  project,
  result,
  resultDataset,
  resultFieldSelection = null,
  resultVectorComponent = "magnitude",
  resultColorMap = "turbo",
  canvasRenderMode = "cinema",
  cinemaCamera = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } },
  resultViewMode = "2d",
  resultCamera = { yaw: -32, pitch: 24, zoom: 1 },
  selectedId,
  selectedKind,
  onSelect,
  onMoveNode,
  onRotateNode = () => {},
  onConnectEdge = () => {},
  onUpdateEdgeEndpoint = () => {},
  onProbePoint = () => {},
  previewPlaying = true,
  onCinemaCameraChange = () => {}
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef(0);
  const dragRef = useRef<DragState | null>(null);
  const viewRef = useRef<ViewTransform>({ scale: 1, offset: { x: 0, y: 0 } });
  const schematicViewportRef = useRef<SchematicViewport>({ ...defaultSchematicViewport, pan: { ...defaultSchematicViewport.pan } });
  const schematicViewportInitializedRef = useRef(false);
  const cinemaRef = useRef<CinemaRuntime | null>(null);
  const previewPlayingRef = useRef(previewPlaying);
  const resultCameraRef = useRef(resultCamera);
  const activePointersRef = useRef(new Map<number, Vec2>());
  const pinchRef = useRef<{ distance: number; center: Vec2; viewport?: SchematicViewport; camera?: CinemaCameraState } | null>(null);

  useEffect(() => {
    previewPlayingRef.current = previewPlaying;
    if (canvasRef.current) canvasRef.current.dataset.previewState = previewPlaying ? "running" : "paused";
  }, [previewPlaying]);

  useEffect(() => {
    resultCameraRef.current = resultCamera;
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (resultDataset) {
      canvas.dataset.resultViewMode = resultViewMode;
      canvas.dataset.resultCameraYaw = String(resultCamera.yaw);
      canvas.dataset.resultCameraPitch = String(resultCamera.pitch);
      canvas.dataset.resultCameraZoom = String(resultCamera.zoom);
    }
  }, [resultCamera, resultDataset, resultViewMode]);

  useEffect(() => {
    schematicViewportInitializedRef.current = false;
  }, [canvasRenderMode]);

  useEffect(() => {
    if (!canvasRef.current) return;
    const canvasElement: HTMLCanvasElement = canvasRef.current;
    cinemaRef.current = null;

    if (canvasRenderMode === "cinema" && browserSupportsWebGL()) {
      const rect = canvasElement.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      let disposed = false;
      let animationId = 0;
      let runtime: CinemaRuntime | null = null;

      function resizeCinema() {
        runtime?.resize();
      }

      canvasElement.dataset.canvasRenderMode = "cinema";
      canvasElement.dataset.cinemaWebgl = "loading";
      delete canvasElement.dataset.cinemaObjectCount;
      delete canvasElement.dataset.cinemaNodePositions;

      import("./cinemaRenderer")
        .then(({ buildCinemaScene }) => {
          if (disposed) return;
          runtime = buildCinemaScene({
            canvas: canvasElement,
            width,
            height,
            project,
            result,
            cinemaCamera,
            resultDataset,
            resultFieldSelection,
            resultVectorComponent,
            resultColorMap,
            selectedId,
            selectedKind
          });
          cinemaRef.current = runtime;
          canvasElement.dataset.canvasRenderMode = "cinema";
          canvasElement.dataset.cinemaWebgl = "true";
          canvasElement.dataset.cinemaObjectCount = String(runtime.pickableCount);
          canvasElement.dataset.viewScale = String(runtime.worldScale);
          canvasElement.dataset.viewOffsetX = String(runtime.center.x);
          canvasElement.dataset.viewOffsetY = String(runtime.center.y);
          canvasElement.dataset.cinemaNodePositions = JSON.stringify(runtime.projectedNodePositions);
          canvasElement.dataset.cinemaCameraYaw = String(cinemaCamera.yaw);
          canvasElement.dataset.cinemaCameraPitch = String(cinemaCamera.pitch);
          canvasElement.dataset.cinemaCameraZoom = String(cinemaCamera.zoom);
          canvasElement.dataset.cinemaCameraPanX = String(cinemaCamera.pan.x);
          canvasElement.dataset.cinemaCameraPanY = String(cinemaCamera.pan.y);
          canvasElement.dataset.engine = runtime.engine;
          if (resultDataset) {
            canvasElement.dataset.resultViewMode = resultViewMode;
            canvasElement.dataset.resultCameraYaw = String(resultCamera.yaw);
            canvasElement.dataset.resultCameraPitch = String(resultCamera.pitch);
            canvasElement.dataset.resultCameraZoom = String(resultCamera.zoom);
          } else {
            delete canvasElement.dataset.resultViewMode;
            delete canvasElement.dataset.resultCameraYaw;
            delete canvasElement.dataset.resultCameraPitch;
            delete canvasElement.dataset.resultCameraZoom;
          }

          function render(time: number) {
            const started = performance.now();
            runtime?.render(time, previewPlayingRef.current);
            recordEditorMetric("cinema-frame", performance.now() - started);
            recordEditorFrame("cinema-frame", time);
            animationId = requestAnimationFrame(render);
          }

          render(0);
          window.addEventListener("resize", resizeCinema);
        })
        .catch(() => {
          if (disposed) return;
          canvasElement.dataset.canvasRenderMode = "schematic";
          canvasElement.dataset.cinemaWebgl = "unavailable";
          delete canvasElement.dataset.cinemaObjectCount;
          delete canvasElement.dataset.cinemaNodePositions;
          delete canvasElement.dataset.cinemaCameraYaw;
          delete canvasElement.dataset.cinemaCameraPitch;
          delete canvasElement.dataset.cinemaCameraZoom;
          delete canvasElement.dataset.cinemaCameraPanX;
          delete canvasElement.dataset.cinemaCameraPanY;
          delete canvasElement.dataset.engine;
        });

      return () => {
        disposed = true;
        window.removeEventListener("resize", resizeCinema);
        cancelAnimationFrame(animationId);
        runtime?.dispose();
        cinemaRef.current = null;
      };
    }

    const maybeContext = canvasElement.getContext("2d");
    if (!maybeContext) return;
    const context: CanvasRenderingContext2D = maybeContext;
    let animationId = 0;

    function resize() {
      const rect = canvasElement.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      canvasElement.width = Math.floor(rect.width * scale);
      canvasElement.height = Math.floor(rect.height * scale);
      context.setTransform(scale, 0, 0, scale, 0, 0);
    }

    function drawEndpointHandle(point: Vec2, active: boolean) {
      context.fillStyle = active ? "#f7d84b" : "#06131b";
      context.strokeStyle = active ? "#fff072" : "rgba(238, 248, 255, 0.72)";
      context.lineWidth = 2;
      context.beginPath();
      context.arc(point.x, point.y, active ? 7 : 5, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }

    function draw() {
      const started = performance.now();
      const rect = canvasElement.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;
      const renderedProject = {
        ...project,
        nodes: Object.fromEntries(Object.values(project.nodes).map((node) => [node.id, draftNode(node, dragRef.current)]))
      } as FluidProject;
      if (!schematicViewportInitializedRef.current) {
        schematicViewportRef.current = fitSchematicViewport(renderedProject, width, height);
        schematicViewportInitializedRef.current = true;
      }
      const view = { scale: schematicViewportRef.current.zoom, offset: schematicViewportRef.current.pan };
      viewRef.current = view;
      canvasElement.dataset.viewScale = String(view.scale);
      canvasElement.dataset.viewOffsetX = String(view.offset.x);
      canvasElement.dataset.viewOffsetY = String(view.offset.y);
      context.clearRect(0, 0, width, height);

      const grd = context.createRadialGradient(width * 0.45, height * 0.35, 40, width * 0.5, height * 0.5, width * 0.75);
      grd.addColorStop(0, "#123349");
      grd.addColorStop(0.55, "#081621");
      grd.addColorStop(1, "#03070d");
      context.fillStyle = grd;
      context.fillRect(0, 0, width, height);

      if (project.visualization.grid) {
        context.strokeStyle = "rgba(160, 190, 210, 0.13)";
        context.lineWidth = 1;
        for (let x = 0; x < width; x += 36) {
          context.beginPath();
          context.moveTo(x, 0);
          context.lineTo(x, height);
          context.stroke();
        }
        for (let y = 0; y < height; y += 36) {
          context.beginPath();
          context.moveTo(0, y);
          context.lineTo(width, y);
          context.stroke();
        }
      }

      if (resultDataset) {
        const activeResultCamera = resultCameraRef.current;
        canvasElement.dataset.resultViewMode = resultViewMode;
        canvasElement.dataset.resultCameraYaw = String(activeResultCamera.yaw);
        canvasElement.dataset.resultCameraPitch = String(activeResultCamera.pitch);
        canvasElement.dataset.resultCameraZoom = String(activeResultCamera.zoom);
        drawResultDataset(context, resultDataset, project.visualization.overlay, resultFieldSelection, resultVectorComponent, resultColorMap, resultViewMode, activeResultCamera, width, height);
      } else {
        delete canvasElement.dataset.resultViewMode;
        delete canvasElement.dataset.resultCameraYaw;
        delete canvasElement.dataset.resultCameraPitch;
        delete canvasElement.dataset.resultCameraZoom;
      }
      canvasElement.dataset.canvasRenderMode = "schematic";
      delete canvasElement.dataset.cinemaWebgl;
      delete canvasElement.dataset.cinemaObjectCount;
      delete canvasElement.dataset.cinemaNodePositions;
      delete canvasElement.dataset.cinemaCameraYaw;
      delete canvasElement.dataset.cinemaCameraPitch;
      delete canvasElement.dataset.cinemaCameraZoom;

      const edgeValues = Object.values(result.edgeResults).map((edge) => {
        if (project.visualization.overlay === "pressure") return edge.pressureDrop;
        if (project.visualization.overlay === "reynolds") return edge.reynolds;
        return edge.velocity;
      });
      const maxEdge = Math.max(...edgeValues, 1);
      if (previewPlayingRef.current) frameRef.current += 0.012;
      canvasElement.dataset.previewPhase = frameRef.current.toFixed(4);

      context.save();
      context.translate(view.offset.x, view.offset.y);
      context.scale(view.scale, view.scale);

      Object.values(renderedProject.edges).forEach((edge) => {
        const from = renderedProject.nodes[edge.from];
        const to = renderedProject.nodes[edge.to];
        const solved: EdgeResult | undefined = result.edgeResults[edge.id];
        if (!from || !to || !solved) return;
        const start = endpointPoint(edge, "from", renderedProject.nodes) ?? from.position;
        const end = endpointPoint(edge, "to", renderedProject.nodes) ?? to.position;
        const widthScale = edge.shape.kind === "circular" ? edge.shape.diameter * 55 : edge.shape.height * 55;
        const metric =
          project.visualization.overlay === "pressure"
            ? solved.pressureDrop
            : project.visualization.overlay === "reynolds"
              ? solved.reynolds
              : solved.velocity;
        const color = overlayValueColor(metric, maxEdge, project.visualization.overlay);

        context.lineCap = "round";
        context.strokeStyle = "rgba(8, 20, 28, 0.95)";
        context.lineWidth = Math.max(18, widthScale + 18);
        context.beginPath();
        context.moveTo(start.x, start.y);
        context.lineTo(end.x, end.y);
        context.stroke();

        context.strokeStyle = edge.id === selectedId ? "#f7d84b" : color;
        context.lineWidth = Math.max(8, widthScale);
        context.shadowColor = color;
        context.shadowBlur = 18;
        context.beginPath();
        context.moveTo(start.x, start.y);
        context.lineTo(end.x, end.y);
        context.stroke();
        context.shadowBlur = 0;

        if (project.visualization.particles) {
          const count = Math.max(7, Math.min(22, Math.floor(Math.abs(solved.velocity) * 4)));
          for (let i = 0; i < count; i += 1) {
            const t = (frameRef.current * Math.sign(solved.flowRate || 1) + i / count) % 1;
            const px = start.x + (end.x - start.x) * (t < 0 ? t + 1 : t);
            const py = start.y + (end.y - start.y) * (t < 0 ? t + 1 : t);
            context.fillStyle = solved.cavitationRisk ? "#ff4d6d" : "#c8f7ff";
            context.beginPath();
            context.arc(px, py, 2.4, 0, Math.PI * 2);
            context.fill();
          }
        }

        const mid = edgeMidpoint(edge, renderedProject.nodes);
        context.fillStyle = "rgba(227, 242, 255, 0.9)";
        context.font = "12px Inter, system-ui, sans-serif";
        context.fillText(`${edge.label} · Re ${Math.round(solved.reynolds).toLocaleString()}`, mid.x + 12, mid.y - 12);
      });

      Object.values(renderedProject.nodes).forEach((node) => {
        const solved = result.nodeResults[node.id];
        const radius = nodeRadius(node);
        const angle = degreesToRadians(node.rotation ?? 0);
        const active = node.id === selectedId;
        context.save();
        context.translate(node.position.x, node.position.y);
        context.rotate(angle);
        context.fillStyle = active ? "#f7d84b" : "#091a24";
        context.strokeStyle = node.type === "pump" ? "#ffb703" : node.type === "mixer" ? "#80ffdb" : "#e8f6ff";
        context.lineWidth = 2;
        context.beginPath();
        context.arc(0, 0, radius, 0, Math.PI * 2);
        context.fill();
        context.stroke();
        context.strokeStyle = active ? "#071019" : "#e8f6ff";
        context.lineWidth = 2;
        context.beginPath();
        context.moveTo(-6, -6);
        context.lineTo(7, 0);
        context.lineTo(-6, 6);
        context.stroke();
        context.restore();

        ports.forEach((port) => {
          const point = portPosition(node, port);
          context.fillStyle = active ? "#f7d84b" : "#071019";
          context.strokeStyle = port === "inlet" || port === "outlet" ? "#9dfbd7" : "rgba(238, 248, 255, 0.45)";
          context.lineWidth = 1.5;
          context.beginPath();
          context.arc(point.x, point.y, port === "inlet" || port === "outlet" ? 4.5 : 3.5, 0, Math.PI * 2);
          context.fill();
          context.stroke();
        });

        if (active && ["pump", "sink", "source", "junction"].includes(node.type)) {
          const handle = aimHandlePosition(node);
          context.strokeStyle = "rgba(247, 216, 75, 0.58)";
          context.lineWidth = 1.5;
          context.beginPath();
          context.moveTo(node.position.x, node.position.y);
          context.lineTo(handle.x, handle.y);
          context.stroke();
          context.fillStyle = "#f7d84b";
          context.beginPath();
          context.arc(handle.x, handle.y, 6, 0, Math.PI * 2);
          context.fill();
        }

        context.fillStyle = "#edf8ff";
        context.font = "600 12px Inter, system-ui, sans-serif";
        context.fillText(node.label, node.position.x + 18, node.position.y + 4);
        if (solved) {
          context.fillStyle = "rgba(206, 227, 239, 0.75)";
          context.font = "11px Inter, system-ui, sans-serif";
          context.fillText(`${Math.round(solved.pressure / 1000)} kPa · ${node.rotation ?? 0}deg`, node.position.x + 18, node.position.y + 19);
        }
      });

      if (selectedKind === "edge" && selectedId) {
        const edge = renderedProject.edges[selectedId];
        if (edge) {
          const from = endpointPoint(edge, "from", renderedProject.nodes);
          const to = endpointPoint(edge, "to", renderedProject.nodes);
          if (from) drawEndpointHandle(from, true);
          if (to) drawEndpointHandle(to, true);
        }
      }

      const draft = dragRef.current;
      if (draft?.kind === "connect") {
        context.strokeStyle = "#f7d84b";
        context.lineWidth = 3;
        context.setLineDash([8, 8]);
        context.beginPath();
        context.moveTo(draft.from.point.x, draft.from.point.y);
        context.lineTo(draft.pointer.x, draft.pointer.y);
        context.stroke();
        context.setLineDash([]);
        drawEndpointHandle(draft.from.point, true);
      } else if (draft?.kind === "endpoint") {
        const edge = renderedProject.edges[draft.edgeId];
        const anchor = edge ? endpointPoint(edge, draft.endpoint === "from" ? "to" : "from", renderedProject.nodes) : null;
        if (anchor) {
          context.strokeStyle = "#f7d84b";
          context.lineWidth = 3;
          context.setLineDash([8, 8]);
          context.beginPath();
          context.moveTo(anchor.x, anchor.y);
          context.lineTo(draft.pointer.x, draft.pointer.y);
          context.stroke();
          context.setLineDash([]);
        }
      }

      context.restore();

      recordEditorMetric("schematic-frame", performance.now() - started);
      recordEditorFrame("schematic-frame");

      animationId = requestAnimationFrame(draw);
    }

    resize();
    draw();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(animationId);
    };
  }, [canvasRenderMode, resultDataset, resultFieldSelection, resultVectorComponent, resultColorMap, selectedId, selectedKind]);

  useEffect(() => {
    if (canvasRenderMode !== "cinema") return;
    const runtime = cinemaRef.current;
    const canvas = canvasRef.current;
    if (!runtime || !canvas) return;
    runtime.updateModel(project, result);
    canvas.dataset.cinemaNodePositions = JSON.stringify(runtime.projectedNodePositions);
  }, [canvasRenderMode, project, result]);

  useEffect(() => {
    if (canvasRenderMode !== "cinema") return;
    cinemaRef.current?.updateCamera(cinemaCamera);
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.dataset.cinemaCameraYaw = String(cinemaCamera.yaw);
    canvas.dataset.cinemaCameraPitch = String(cinemaCamera.pitch);
    canvas.dataset.cinemaCameraZoom = String(cinemaCamera.zoom);
    canvas.dataset.cinemaCameraPanX = String(cinemaCamera.pan.x);
    canvas.dataset.cinemaCameraPanY = String(cinemaCamera.pan.y);
  }, [canvasRenderMode, cinemaCamera]);

  function screenPoint(event: PointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function canvasPoint(event: PointerEvent<HTMLCanvasElement>) {
    const point = screenPoint(event);
    const view = viewRef.current;
    return screenToWorld(point, { pan: view.offset, zoom: view.scale });
  }

  function currentProject(): FluidProject {
    const active = dragRef.current;
    if (active?.kind !== "node" && active?.kind !== "rotate") return project;
    return {
      ...project,
      nodes: {
        ...project.nodes,
        [active.kind === "node" ? active.id : active.nodeId]: draftNode(
          project.nodes[active.kind === "node" ? active.id : active.nodeId],
          active
        )
      }
    };
  }

  function cinemaPick(event: PointerEvent<HTMLCanvasElement>): CinemaPick | null {
    return cinemaRef.current?.pickAt({ clientX: event.clientX, clientY: event.clientY }) ?? null;
  }

  function cinemaPoint(event: PointerEvent<HTMLCanvasElement>) {
    return cinemaRef.current?.pointAt({ clientX: event.clientX, clientY: event.clientY }) ?? null;
  }

  function capturePointer(target: HTMLCanvasElement, pointerId: number) {
    target.setPointerCapture?.(pointerId);
  }

  function portAt(point: Vec2): PortHit | null {
    const activeProject = currentProject();
    for (const node of Object.values(activeProject.nodes)) {
      for (const port of ports) {
        const portPoint = portPosition(node, port);
        if (Math.hypot(portPoint.x - point.x, portPoint.y - point.y) < 18) return { nodeId: node.id, port, point: portPoint };
      }
    }
    return null;
  }

  function selectedEndpointAt(point: Vec2): { edgeId: string; endpoint: "from" | "to" } | null {
    if (selectedKind !== "edge" || !selectedId) return null;
    const activeProject = currentProject();
    const edge = activeProject.edges[selectedId];
    if (!edge) return null;
    const from = endpointPoint(edge, "from", activeProject.nodes);
    const to = endpointPoint(edge, "to", activeProject.nodes);
    if (from && Math.hypot(from.x - point.x, from.y - point.y) < 16) return { edgeId: edge.id, endpoint: "from" };
    if (to && Math.hypot(to.x - point.x, to.y - point.y) < 16) return { edgeId: edge.id, endpoint: "to" };
    return null;
  }

  function rotateHandleAt(point: Vec2): FluidNode | null {
    if (selectedKind !== "node" || !selectedId) return null;
    const node = currentProject().nodes[selectedId];
    if (!node) return null;
    const handle = aimHandlePosition(node);
    return Math.hypot(handle.x - point.x, handle.y - point.y) < 18 ? node : null;
  }

  function nodeAt(point: Vec2) {
    return Object.values(currentProject().nodes).find((candidate) => Math.hypot(candidate.position.x - point.x, candidate.position.y - point.y) < 24);
  }

  function edgeAt(point: Vec2) {
    const activeProject = currentProject();
    return Object.values(activeProject.edges).find((candidate) => {
      const from = endpointPoint(candidate, "from", activeProject.nodes);
      const to = endpointPoint(candidate, "to", activeProject.nodes);
      if (!from || !to) return false;
      const length = Math.hypot(to.x - from.x, to.y - from.y) || 1;
      const t = Math.max(0, Math.min(1, ((point.x - from.x) * (to.x - from.x) + (point.y - from.y) * (to.y - from.y)) / length ** 2));
      const px = from.x + (to.x - from.x) * t;
      const py = from.y + (to.y - from.y) * t;
      return Math.hypot(px - point.x, py - point.y) < 20;
    });
  }

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    const screen = screenPoint(event);
    activePointersRef.current.set(event.pointerId, screen);
    if (event.pointerType === "touch" && activePointersRef.current.size >= 2) {
      const points = [...activePointersRef.current.values()];
      const first = points[0];
      const second = points[1];
      pinchRef.current = {
        distance: Math.max(1, Math.hypot(second.x - first.x, second.y - first.y)),
        center: { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 },
        viewport: canvasRenderMode === "schematic" ? { ...schematicViewportRef.current, pan: { ...schematicViewportRef.current.pan } } : undefined,
        camera: canvasRenderMode === "cinema" ? { ...cinemaCamera, pan: { ...cinemaCamera.pan } } : undefined
      };
      dragRef.current = null;
      return;
    }

    if (canvasRenderMode === "cinema" && cinemaRef.current) {
      const pick = cinemaPick(event);
      const point = cinemaPoint(event);
      const rect = event.currentTarget.getBoundingClientRect();
      if (pick?.kind === "rotate") {
        const node = project.nodes[pick.nodeId];
        dragRef.current = { kind: "rotate", nodeId: pick.nodeId, rotation: node?.rotation ?? 0 };
        capturePointer(event.currentTarget, event.pointerId);
        return;
      }
      if (pick?.kind === "port") {
        onSelect("node", pick.nodeId);
        dragRef.current = { kind: "connect", from: { nodeId: pick.nodeId, port: pick.port, point: pick.point }, pointer: point ?? pick.point };
        capturePointer(event.currentTarget, event.pointerId);
        return;
      }
      if (pick?.kind === "node" && point) {
        const node = currentProject().nodes[pick.id];
        if (node) {
          onSelect("node", node.id);
          dragRef.current = {
            kind: "node",
            id: node.id,
            offsetX: point.x - node.position.x,
            offsetY: point.y - node.position.y,
            position: { ...node.position }
          };
          capturePointer(event.currentTarget, event.pointerId);
          return;
        }
      }
      if (pick?.kind === "edge") {
        onSelect("edge", pick.id);
        return;
      }
      dragRef.current = {
        kind: event.button === 1 || event.shiftKey ? "cinema-pan" : "cinema-orbit",
        startX: event.clientX,
        startY: event.clientY,
        camera: { ...cinemaCamera, pan: { ...cinemaCamera.pan } },
        moved: false
      };
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const point = canvasPoint(event);
    const rect = event.currentTarget.getBoundingClientRect();
    const rotateNode = rotateHandleAt(point);
    if (rotateNode) {
      dragRef.current = { kind: "rotate", nodeId: rotateNode.id, rotation: rotateNode.rotation ?? 0 };
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const port = portAt(point);
    if (port) {
      onSelect("node", port.nodeId);
      dragRef.current = { kind: "connect", from: port, pointer: point };
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

      const endpoint = selectedEndpointAt(point);
    if (endpoint) {
      dragRef.current = { kind: "endpoint", ...endpoint, pointer: point };
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const node = nodeAt(point);
    if (node) {
      onSelect("node", node.id);
      dragRef.current = {
        kind: "node",
        id: node.id,
        offsetX: point.x - node.position.x,
        offsetY: point.y - node.position.y,
        position: { ...node.position }
      };
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }
    const edge = edgeAt(point);
    if (edge) onSelect("edge", edge.id);
    else {
      dragRef.current = { kind: "canvas-pan", start: screen, viewport: { ...schematicViewportRef.current, pan: { ...schematicViewportRef.current.pan } }, moved: false };
      if (resultDataset) onProbePoint(screen, { width: rect.width, height: rect.height });
      capturePointer(event.currentTarget, event.pointerId);
    }
  }

  function handlePointerMove(event: PointerEvent<HTMLCanvasElement>) {
    const started = performance.now();
    try {
      const pointOnScreen = screenPoint(event);
      activePointersRef.current.set(event.pointerId, pointOnScreen);
      if (pinchRef.current && activePointersRef.current.size >= 2) {
      const points = [...activePointersRef.current.values()];
      const first = points[0];
      const second = points[1];
      const distance = Math.max(1, Math.hypot(second.x - first.x, second.y - first.y));
      const center = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
      const pinch = pinchRef.current;
      const ratio = distance / pinch.distance;
      if (canvasRenderMode === "schematic" && pinch.viewport) {
        const zoom = clampViewportZoom(pinch.viewport.zoom * ratio);
        schematicViewportRef.current = {
          zoom,
          pan: {
            x: center.x - (pinch.center.x - pinch.viewport.pan.x) * (zoom / pinch.viewport.zoom),
            y: center.y - (pinch.center.y - pinch.viewport.pan.y) * (zoom / pinch.viewport.zoom)
          }
        };
      } else if (canvasRenderMode === "cinema" && pinch.camera) {
        onCinemaCameraChange({ ...pinch.camera, zoom: clampViewportZoom(pinch.camera.zoom * ratio) });
      }
        return;
      }
      if (!dragRef.current) return;
      const point = canvasRenderMode === "cinema" && cinemaRef.current ? (cinemaPoint(event) ?? canvasPoint(event)) : canvasPoint(event);
      if (dragRef.current.kind === "node") {
      const nextPosition = {
        x: Math.round(point.x - dragRef.current.offsetX),
        y: Math.round(point.y - dragRef.current.offsetY)
      };
      dragRef.current = { ...dragRef.current, position: nextPosition };
      if (canvasRenderMode === "cinema") cinemaRef.current?.updateModel(currentProject(), result);
      } else if (dragRef.current.kind === "rotate") {
      const node = currentProject().nodes[dragRef.current.nodeId];
      if (node) {
        const angle = (Math.atan2(point.y - node.position.y, point.x - node.position.x) * 180) / Math.PI;
        dragRef.current = { ...dragRef.current, rotation: angle };
        if (canvasRenderMode === "cinema") cinemaRef.current?.updateModel(currentProject(), result);
      }
      } else if (dragRef.current.kind === "connect") {
      dragRef.current = { ...dragRef.current, pointer: point };
      } else if (dragRef.current.kind === "endpoint") {
      dragRef.current = { ...dragRef.current, pointer: point };
      } else if (dragRef.current.kind === "canvas-pan") {
      const delta = { x: pointOnScreen.x - dragRef.current.start.x, y: pointOnScreen.y - dragRef.current.start.y };
      dragRef.current = { ...dragRef.current, moved: dragRef.current.moved || Math.hypot(delta.x, delta.y) > 2 };
      schematicViewportRef.current = panSchematicViewport(dragRef.current.viewport, delta);
      } else if (dragRef.current.kind === "cinema-orbit" || dragRef.current.kind === "cinema-pan") {
      const active = dragRef.current;
      const dx = event.clientX - active.startX;
      const dy = event.clientY - active.startY;
      const nextCamera =
        active.kind === "cinema-orbit"
          ? { ...active.camera, yaw: active.camera.yaw + dx * 0.45, pitch: Math.max(-12, Math.min(78, active.camera.pitch - dy * 0.35)) }
          : { ...active.camera, pan: { x: active.camera.pan.x - dx / 74, y: active.camera.pan.y + dy / 74 } };
      dragRef.current = { ...active, moved: active.moved || Math.hypot(dx, dy) > 2 };
      onCinemaCameraChange(nextCamera);
      }
    } finally {
      recordEditorMetric("pointer-update", performance.now() - started);
    }
  }

  function handlePointerEnd(event: PointerEvent<HTMLCanvasElement>, cancelled = false) {
    activePointersRef.current.delete(event.pointerId);
    if (pinchRef.current) {
      pinchRef.current = null;
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      return;
    }
    const point = canvasRenderMode === "cinema" && cinemaRef.current ? (cinemaPoint(event) ?? canvasPoint(event)) : canvasPoint(event);
    const cinemaTarget = canvasRenderMode === "cinema" && cinemaRef.current ? cinemaPick(event) : null;
    const target = cinemaTarget?.kind === "port" ? { nodeId: cinemaTarget.nodeId, port: cinemaTarget.port, point: cinemaTarget.point } : portAt(point);
    const active = dragRef.current;
    if (!cancelled && active?.kind === "node") {
      onMoveNode(active.id, active.position);
    } else if (!cancelled && active?.kind === "rotate") {
      onRotateNode(active.nodeId, active.rotation);
    } else if (!cancelled && active?.kind === "connect" && target && target.nodeId !== active.from.nodeId) {
      onConnectEdge(active.from.nodeId, target.nodeId, active.from.port, target.port);
    } else if (!cancelled && active?.kind === "endpoint" && target) {
      onUpdateEdgeEndpoint(active.edgeId, active.endpoint, target.nodeId, target.port);
    } else if (!cancelled && active?.kind === "canvas-pan" && !active.moved && resultDataset) {
      const rect = event.currentTarget.getBoundingClientRect();
      onProbePoint(screenPoint(event), { width: rect.width, height: rect.height });
    } else if (!cancelled && (active?.kind === "cinema-orbit" || active?.kind === "cinema-pan") && !active.moved && resultDataset) {
      const rect = event.currentTarget.getBoundingClientRect();
      onProbePoint(screenPoint(event), { width: rect.width, height: rect.height });
    }

    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function handleWheel(event: WheelEvent<HTMLCanvasElement>) {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom * Math.exp(-event.deltaY * 0.0012)) });
      return;
    }
    schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, point, event.deltaY);
  }

  function fitViewport() {
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange(cinemaRef.current?.fitCamera(cinemaCamera, project) ?? { ...cinemaCamera, zoom: 1, pan: { x: 0, y: 0 } });
      return;
    }
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    schematicViewportRef.current = fitSchematicViewport(project, rect.width, rect.height);
    schematicViewportInitializedRef.current = true;
  }

  function resetViewport() {
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange({ yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } });
      return;
    }
    schematicViewportRef.current = resetSchematicViewport();
    schematicViewportInitializedRef.current = true;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLCanvasElement>) {
    if (event.key === "Escape") {
      dragRef.current = null;
      pinchRef.current = null;
      activePointersRef.current.clear();
      event.preventDefault();
    } else if (event.key === "ArrowUp" || event.key === "ArrowDown" || event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const node = selectedKind === "node" && selectedId ? project.nodes[selectedId] : null;
      if (!node) return;
      const step = event.shiftKey ? 10 : 1;
      const delta = {
        x: event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0,
        y: event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0
      };
      onMoveNode(node.id, { x: node.position.x + delta.x, y: node.position.y + delta.y });
      event.preventDefault();
    } else if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      fitViewport();
    } else if (event.key === "0") {
      event.preventDefault();
      resetViewport();
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      if (canvasRenderMode === "cinema") onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom * 1.15) });
      else schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, { x: viewRef.current.offset.x, y: viewRef.current.offset.y }, -120);
    } else if (event.key === "-") {
      event.preventDefault();
      if (canvasRenderMode === "cinema") onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom / 1.15) });
      else schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, { x: viewRef.current.offset.x, y: viewRef.current.offset.y }, 120);
    }
  }

  return (
    <>
      <canvas
        key={canvasRenderMode}
        ref={canvasRef}
        data-testid="simulation-canvas"
        className="simulation-canvas"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={(event) => handlePointerEnd(event)}
        onPointerCancel={(event) => handlePointerEnd(event, true)}
        onWheel={handleWheel}
        onKeyDown={handleKeyDown}
        aria-label="Flow simulation canvas"
        aria-describedby="canvas-status"
        role="application"
        tabIndex={0}
        aria-keyshortcuts="F 0 + -"
      />
      <div className="viewport-actions" aria-label="Viewport controls">
        <button type="button" onClick={fitViewport} title="Fit viewport" aria-label="Fit viewport">
          Fit
        </button>
        <button type="button" onClick={resetViewport} title="Reset viewport" aria-label="Reset viewport">
          Reset
        </button>
      </div>
    </>
  );
}
