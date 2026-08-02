import { useEffect, useRef, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import type { DecodedDerivedVisualization } from "../results/derived";
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
import type { CinemaPick, CinemaResultProbe, CinemaRuntime } from "./cinemaRenderer";
import type { DerivedPresentationOptions } from "./derivedRenderer";
import { recordEditorFrame, recordEditorMetric } from "../performance/editorProfiler";
import type { StreamlineDisplayOptions, StreamlineResult } from "../streamlines/types";
import {
  PORT_SNAP_RADIUS,
  SCHEMATIC_GRID_MAJOR,
  SCHEMATIC_GRID_SIZE,
  clampViewportZoom,
  defaultSchematicViewport,
  distanceToPolyline,
  firstClearBox,
  fitSchematicViewport,
  isCellFree,
  labelPlacementCandidates,
  longestPolylineSegment,
  panSchematicViewport,
  pointOnPolyline,
  polylineObstacleBoxes,
  polylineSegments,
  resetSchematicViewport,
  routeLaneSpans,
  routeOrthogonalPipe,
  screenToWorld,
  snapNodeToFreeCell,
  snapToGrid,
  tidySchematicLayout,
  tidySchematicRotations,
  wireCrossings,
  wireJunctions,
  worldToScreen,
  zoomViewportAtPoint,
  type CinemaCameraState,
  type LabelRect,
  type LabelSide,
  type LaneSpan,
  type SchematicViewport,
  type WireRoute,
  visibleGridRange
} from "./viewportModel";

type Props = {
  project: FluidProject;
  result: SimulationResult;
  resultDataset?: VtkResultDataset | null;
  derivedVisualization?: DecodedDerivedVisualization | null;
  derivedPresentationOptions?: DerivedPresentationOptions;
  resultFieldSelection?: ResultFieldSelection | null;
  resultVectorComponent?: ResultVectorComponent;
  resultColorMap?: ResultColorMap;
  streamlines?: StreamlineResult | null;
  streamlineDisplay?: StreamlineDisplayOptions;
  canvasRenderMode?: CanvasRenderMode;
  cinemaCamera?: CinemaCameraState;
  resultViewMode?: ResultViewMode;
  resultCamera?: ResultCamera;
  force2dProjection?: boolean;
  selectedId: string | null;
  selectedKind?: "node" | "edge" | null;
  onSelect: (kind: "node" | "edge", id: string) => void;
  onMoveNode: (id: string, position: Vec2) => void;
  onRotateNode?: (id: string, rotation: number) => void;
  onConnectEdge?: (from: string, to: string, fromPort: PipePortId, toPort: PipePortId) => void;
  onUpdateEdgeEndpoint?: (edgeId: string, endpoint: "from" | "to", nodeId: string, port: PipePortId) => void;
  onProbePoint?: (
    point: Vec2,
    size: { width: number; height: number },
    surfaceProbe?: CinemaResultProbe | null
  ) => void;
  previewPlaying?: boolean;
  onCinemaCameraChange?: (camera: CinemaCameraState) => void;
  onRenderBackendChange?: (backend: "webgl" | "2d") => void;
  testId?: string;
  ariaLabel?: string;
  statusId?: string;
};

type PortHit = { nodeId: string; port: PipePortId; point: Vec2 };
/** A snap candidate plus whether the store will actually accept a connection there. */
type PortSnap = PortHit & { free: boolean };
type ViewTransform = { scale: number; offset: Vec2 };
type DragState =
  | {
      kind: "node";
      id: string;
      offsetX: number;
      offsetY: number;
      /** Grid-locked position the component will be committed at. */
      position: Vec2;
      /** Unsnapped pointer position, used only to draw the snap hint. */
      pointer: Vec2;
      /** True when the raw grid cell was taken and the component was pushed to a free one. */
      deflected: boolean;
      /** A press that never moves is a selection, not a drag, and must not reposition anything. */
      moved: boolean;
    }
  | { kind: "connect"; from: PortHit; pointer: Vec2; snap: PortSnap | null }
  | { kind: "endpoint"; edgeId: string; endpoint: "from" | "to"; pointer: Vec2; snap: PortSnap | null }
  | { kind: "rotate"; nodeId: string; rotation: number }
  | { kind: "canvas-pan"; start: Vec2; viewport: SchematicViewport; moved: boolean }
  | { kind: "cinema-orbit"; startX: number; startY: number; camera: CinemaCameraState; moved: boolean }
  | { kind: "cinema-pan"; startX: number; startY: number; camera: CinemaCameraState; moved: boolean };

type HoverTarget =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | { kind: "port"; nodeId: string; port: PipePortId }
  | { kind: "endpoint"; edgeId: string; endpoint: "from" | "to" }
  | { kind: "rotate"; nodeId: string };

type LabelBox = LabelRect;

const ports: PipePortId[] = ["inlet", "outlet", "north", "south"];
const ignoreRenderBackendChange = () => {};

/** Screen-pixel halo kept clear around every routed run when a label looks for a home. */
const LABEL_WIRE_CLEARANCE = 5;

/**
 * Screen-space margin auto-fit keeps clear. The bottom strip is the anchored Fit/Reset
 * cluster; the side and top margins are room for the label chips, which are drawn at a
 * fixed screen size and therefore need screen-space allowance rather than world padding.
 */
const schematicFitInsets = { top: 28, right: 50, bottom: 62, left: 50 };

const cursorForHover: Record<HoverTarget["kind"], string> = {
  node: "move",
  edge: "pointer",
  port: "crosshair",
  endpoint: "crosshair",
  rotate: "crosshair"
};

function roundedRectPath(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.lineTo(x + width - r, y);
  context.quadraticCurveTo(x + width, y, x + width, y + r);
  context.lineTo(x + width, y + height - r);
  context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  context.lineTo(x + r, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - r);
  context.lineTo(x, y + r);
  context.quadraticCurveTo(x, y, x + r, y);
  context.closePath();
}

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

/** Outward normal of a port: the direction a pipe has to leave along. */
function portDirection(node: FluidNode, port: PipePortId): Vec2 {
  const angle = degreesToRadians(portAngle(node, port));
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

/** The orthogonal polyline one pipe is drawn along, in world coordinates. */
function edgeRoutePoints(
  edge: FluidEdge,
  nodes: Record<string, FluidNode>,
  obstacles?: Vec2[],
  occupiedLanes?: LaneSpan[]
): Vec2[] | null {
  const from = nodes[edge.from];
  const to = nodes[edge.to];
  if (!from || !to) return null;
  const fromPort = edge.fromPort ?? "outlet";
  const toPort = edge.toPort ?? "inlet";
  return routeOrthogonalPipe(
    portPosition(from, fromPort),
    portDirection(from, fromPort),
    portPosition(to, toPort),
    portDirection(to, toPort),
    { obstacles: obstacles ?? componentCentres(nodes), occupiedLanes: occupiedLanes ?? [] }
  );
}

function componentCentres(nodes: Record<string, FluidNode>): Vec2[] {
  return Object.values(nodes).map((node) => node.position);
}

/**
 * Routes every drawable pipe. Pipes are routed one after another and each one hands its
 * runs to the next, so a second pipe picks a different lane instead of being drawn on top
 * of the first. Edge order is the project's own, so the picture is stable frame to frame.
 */
function buildEdgeRoutes(project: FluidProject): WireRoute[] {
  const obstacles = componentCentres(project.nodes);
  const occupiedLanes: LaneSpan[] = [];
  const routes: WireRoute[] = [];
  for (const edge of Object.values(project.edges)) {
    const points = edgeRoutePoints(edge, project.nodes, obstacles, occupiedLanes);
    if (!points || points.length < 2) continue;
    occupiedLanes.push(...routeLaneSpans(points));
    routes.push({ id: edge.id, points });
  }
  return routes;
}

/** A crossing marker on one run: where it is, and how far the run steps aside for it. */
type RouteHop = Vec2 & { radius: number };

/**
 * Traces a routed pipe, stepping the pen aside wherever it crosses another pipe. The step
 * is wider than the pipe being crossed, so what the reader sees is a clean break with
 * rounded shoulders: the schematic convention for "these two do not connect". Without it a
 * crossover and a tee look identical once both runs are the same colour.
 */
function traceRouteWithHops(context: CanvasRenderingContext2D, points: Vec2[], hopsBySegment: Map<number, RouteHop[]>) {
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  polylineSegments(points).forEach((segment, index) => {
    const hops = hopsBySegment.get(index);
    const horizontal = Math.abs(segment.from.y - segment.to.y) < 1e-6;
    const along = (point: Vec2) => (horizontal ? point.x : point.y);
    const start = along(segment.from);
    const finish = along(segment.to);
    const forward = finish >= start ? 1 : -1;
    // A step needs its own width of run on both sides, otherwise it would swallow a corner
    // and the route would visibly kink instead of stepping over.
    const usable = (hops ?? []).filter(
      (hop) => hop.radius > 0 && Math.abs(along(hop) - start) > hop.radius && Math.abs(finish - along(hop)) > hop.radius
    );
    if (usable.length === 0) {
      context.lineTo(segment.to.x, segment.to.y);
      return;
    }
    for (const hop of usable.sort((left, right) => (along(left) - along(right)) * forward)) {
      const entry = along(hop) - hop.radius * forward;
      if (horizontal) {
        context.lineTo(entry, segment.from.y);
        // The step is always drawn towards -y so every crossover on the sheet looks alike.
        context.arc(hop.x, segment.from.y, hop.radius, forward > 0 ? Math.PI : 0, forward > 0 ? 0 : Math.PI, forward < 0);
      } else {
        context.lineTo(segment.from.x, entry);
        context.arc(segment.from.x, hop.y, hop.radius, forward > 0 ? -Math.PI / 2 : Math.PI / 2, forward > 0 ? Math.PI / 2 : -Math.PI / 2, forward > 0);
      }
    }
    context.lineTo(segment.to.x, segment.to.y);
  });
}

/** Outer width a pipe is stroked at, casing included. */
function edgeCasingWidth(edge: FluidEdge | undefined): number {
  if (!edge) return 18;
  const widthScale = edge.shape.kind === "circular" ? edge.shape.diameter * 55 : edge.shape.height * 55;
  return Math.max(18, widthScale + 18);
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

function draftNode(node: FluidNode, drag: DragState | null): FluidNode {
  if (drag?.kind === "node" && drag.id === node.id) return drag.moved ? { ...node, position: drag.position } : node;
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
    span: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1e-9)
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
  derivedVisualization = null,
  derivedPresentationOptions,
  resultFieldSelection = null,
  resultVectorComponent = "magnitude",
  resultColorMap = "turbo",
  streamlines = null,
  streamlineDisplay,
  canvasRenderMode = "cinema",
  cinemaCamera = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } },
  resultViewMode = "2d",
  resultCamera = { yaw: -32, pitch: 24, zoom: 1 },
  force2dProjection = false,
  selectedId,
  selectedKind,
  onSelect,
  onMoveNode,
  onRotateNode = () => {},
  onConnectEdge = () => {},
  onUpdateEdgeEndpoint = () => {},
  onProbePoint = () => {},
  previewPlaying = true,
  onCinemaCameraChange = () => {},
  onRenderBackendChange = ignoreRenderBackendChange,
  testId = "simulation-canvas",
  ariaLabel = "Flow simulation canvas",
  statusId = "canvas-status"
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef(0);
  const dragRef = useRef<DragState | null>(null);
  const hoverRef = useRef<HoverTarget | null>(null);
  const viewRef = useRef<ViewTransform>({ scale: 1, offset: { x: 0, y: 0 } });
  const schematicViewportRef = useRef<SchematicViewport>({ ...defaultSchematicViewport, pan: { ...defaultSchematicViewport.pan } });
  const schematicViewportInitializedRef = useRef(false);
  /** Set once the user zooms on purpose; auto-refit then stops overriding their framing. */
  const userZoomedRef = useRef(false);
  const canvasSizeRef = useRef<{ width: number; height: number }>({ width: 0, height: 0 });
  const placedNodeIdsRef = useRef<Set<string> | null>(null);
  const cinemaRef = useRef<CinemaRuntime | null>(null);
  const previewPlayingRef = useRef(previewPlaying);
  const resultCameraRef = useRef(resultCamera);
  const projectRef = useRef(project);
  const resultRef = useRef(result);
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
    projectRef.current = project;
    resultRef.current = result;
  }, [project, result]);

  useEffect(() => {
    schematicViewportInitializedRef.current = false;
    userZoomedRef.current = false;
  }, [canvasRenderMode]);

  // A component that arrives on top of an existing one (for example repeated clicks in the
  // component palette) is pushed to the nearest free grid cell exactly once, so the schematic
  // never starts out as a stack. Existing components are never moved behind the user's back.
  useEffect(() => {
    if (canvasRenderMode !== "schematic") return;
    const known = placedNodeIdsRef.current;
    const currentIds = Object.keys(project.nodes);
    if (!known) {
      placedNodeIdsRef.current = new Set(currentIds);
      return;
    }
    const inserted = currentIds.filter((id) => !known.has(id));
    placedNodeIdsRef.current = new Set(currentIds);
    const collided = inserted.find((id) => {
      const node = project.nodes[id];
      return node && !isCellFree(project, id, node.position);
    });
    if (!collided) return;
    const node = project.nodes[collided];
    const resolved = snapNodeToFreeCell(project, collided, node.position);
    if (resolved.x !== node.position.x || resolved.y !== node.position.y) onMoveNode(collided, resolved);
  }, [canvasRenderMode, onMoveNode, project]);

  useEffect(() => {
    if (!canvasRef.current) return;
    const canvasElement: HTMLCanvasElement = canvasRef.current;
    cinemaRef.current = null;

    if (canvasRenderMode === "cinema" && !force2dProjection && browserSupportsWebGL()) {
      const rect = canvasElement.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      let disposed = false;
      let animationId = 0;
      let runtime: CinemaRuntime | null = null;

      function resizeCinema() {
        runtime?.resize();
      }

      const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resizeCinema);
      resizeObserver?.observe(canvasElement);

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
            derivedVisualization,
            derivedPresentationOptions,
            resultFieldSelection,
            resultVectorComponent,
            resultColorMap,
            streamlines,
            streamlineDisplay,
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
          onRenderBackendChange("webgl");
          canvasElement.dataset.derivedFallback = runtime.derivedFallback;
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
          onRenderBackendChange("2d");
          delete canvasElement.dataset.derivedFallback;
        });

      return () => {
        disposed = true;
        window.removeEventListener("resize", resizeCinema);
        resizeObserver?.disconnect();
        cancelAnimationFrame(animationId);
        runtime?.dispose();
        cinemaRef.current = null;
      };
    }

    const maybeContext = canvasElement.getContext("2d");
    if (!maybeContext) return;
    if (canvasRenderMode === "cinema") onRenderBackendChange("2d");
    const context: CanvasRenderingContext2D = maybeContext;
    let animationId = 0;

    function resize() {
      const rect = canvasElement.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      const scale = window.devicePixelRatio || 1;
      const width = Math.floor(rect.width * scale);
      const height = Math.floor(rect.height * scale);
      if (canvasElement.width !== width || canvasElement.height !== height) {
        canvasElement.width = width;
        canvasElement.height = height;
        context.setTransform(scale, 0, 0, scale, 0, 0);
      }
      // The panel can be resized by the workspace divider, the window, or the action bar
      // appearing. Re-frame the drawing unless the user has chosen their own zoom.
      const changed = Math.abs(canvasSizeRef.current.width - rect.width) > 1 || Math.abs(canvasSizeRef.current.height - rect.height) > 1;
      if (changed) {
        canvasSizeRef.current = { width: rect.width, height: rect.height };
        if (schematicViewportInitializedRef.current && !userZoomedRef.current) {
          schematicViewportRef.current = fitSchematicViewport(projectRef.current, rect.width, rect.height, {
            insets: schematicFitInsets
          });
        }
      }
      return true;
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
      if (!resize()) {
        animationId = requestAnimationFrame(draw);
        return;
      }
      const rect = canvasElement.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;
      const activeProject = projectRef.current;
      const activeResult = resultRef.current;
      const renderedProject = {
        ...activeProject,
        nodes: Object.fromEntries(Object.values(activeProject.nodes).map((node) => [node.id, draftNode(node, dragRef.current)]))
      } as FluidProject;
      if (!schematicViewportInitializedRef.current) {
        canvasSizeRef.current = { width, height };
        schematicViewportRef.current = fitSchematicViewport(renderedProject, width, height, { insets: schematicFitInsets });
        schematicViewportInitializedRef.current = true;
      }
      const view = { scale: schematicViewportRef.current.zoom, offset: schematicViewportRef.current.pan };
      viewRef.current = view;
      canvasElement.dataset.viewScale = String(view.scale);
      canvasElement.dataset.viewOffsetX = String(view.offset.x);
      canvasElement.dataset.viewOffsetY = String(view.offset.y);
      canvasElement.dataset.snapGrid = String(SCHEMATIC_GRID_SIZE);
      canvasElement.dataset.viewFitted = userZoomedRef.current ? "user" : "auto";
      canvasElement.dataset.tidyShortcut = "t";
      context.clearRect(0, 0, width, height);

      const grd = context.createRadialGradient(width * 0.45, height * 0.35, 40, width * 0.5, height * 0.5, width * 0.75);
      grd.addColorStop(0, "#123349");
      grd.addColorStop(0.55, "#081621");
      grd.addColorStop(1, "#03070d");
      context.fillStyle = grd;
      context.fillRect(0, 0, width, height);

      // The grid lives in world space, so a snapped component visibly lands on a line the
      // user can already see. A screen-space grid would drift under pan and zoom and make
      // snapping look arbitrary.
      if (activeProject.visualization.grid) {
        const range = visibleGridRange({ pan: view.offset, zoom: view.scale }, width, height);
        const minorSpacing = range.step * view.scale;
        context.save();
        context.translate(view.offset.x, view.offset.y);
        context.scale(view.scale, view.scale);
        context.lineWidth = 1 / view.scale;
        for (let x = range.startX; x <= range.endX; x += range.step) {
          const major = Math.round(x / range.step) % SCHEMATIC_GRID_MAJOR === 0;
          if (!major && minorSpacing < 9) continue;
          context.strokeStyle = major ? "rgba(150, 194, 224, 0.2)" : "rgba(150, 194, 224, 0.082)";
          context.beginPath();
          context.moveTo(x, range.startY);
          context.lineTo(x, range.endY);
          context.stroke();
        }
        for (let y = range.startY; y <= range.endY; y += range.step) {
          const major = Math.round(y / range.step) % SCHEMATIC_GRID_MAJOR === 0;
          if (!major && minorSpacing < 9) continue;
          context.strokeStyle = major ? "rgba(150, 194, 224, 0.2)" : "rgba(150, 194, 224, 0.082)";
          context.beginPath();
          context.moveTo(range.startX, y);
          context.lineTo(range.endX, y);
          context.stroke();
        }
        context.restore();
      }

      if (resultDataset) {
        const activeResultCamera = resultCameraRef.current;
        canvasElement.dataset.resultViewMode = resultViewMode;
        canvasElement.dataset.resultCameraYaw = String(activeResultCamera.yaw);
        canvasElement.dataset.resultCameraPitch = String(activeResultCamera.pitch);
        canvasElement.dataset.resultCameraZoom = String(activeResultCamera.zoom);
        drawResultDataset(context, resultDataset, activeProject.visualization.overlay, resultFieldSelection, resultVectorComponent, resultColorMap, resultViewMode, activeResultCamera, width, height);
      } else {
        delete canvasElement.dataset.resultViewMode;
        delete canvasElement.dataset.resultCameraYaw;
        delete canvasElement.dataset.resultCameraPitch;
        delete canvasElement.dataset.resultCameraZoom;
      }
      const resultProjection = canvasRenderMode === "cinema" && Boolean(resultDataset);
      canvasElement.dataset.canvasRenderMode = resultProjection ? "projection" : "schematic";
      delete canvasElement.dataset.cinemaWebgl;
      delete canvasElement.dataset.cinemaObjectCount;
      delete canvasElement.dataset.cinemaNodePositions;
      delete canvasElement.dataset.cinemaCameraYaw;
      delete canvasElement.dataset.cinemaCameraPitch;
      delete canvasElement.dataset.cinemaCameraZoom;

      if (resultProjection) {
        recordEditorMetric("schematic-frame", performance.now() - started);
        recordEditorFrame("schematic-frame");
        animationId = requestAnimationFrame(draw);
        return;
      }

      const edgeValues = Object.values(activeResult.edgeResults).map((edge) => {
        if (activeProject.visualization.overlay === "pressure") return edge.pressureDrop;
        if (activeProject.visualization.overlay === "reynolds") return edge.reynolds;
        return edge.velocity;
      });
      const maxEdge = Math.max(...edgeValues, 1);
      if (previewPlayingRef.current) frameRef.current += 0.012;
      canvasElement.dataset.previewPhase = frameRef.current.toFixed(4);

      const draft = dragRef.current;
      const hover = hoverRef.current;
      /** Converts a screen-pixel size into world units, so affordances stay legible at any zoom. */
      const px = (value: number) => value / view.scale;

      if (draft) canvasElement.dataset.dragKind = draft.kind;
      else delete canvasElement.dataset.dragKind;
      if (draft?.kind === "node") {
        canvasElement.dataset.snapTargetX = String(draft.position.x);
        canvasElement.dataset.snapTargetY = String(draft.position.y);
        canvasElement.dataset.snapDeflected = draft.deflected ? "true" : "false";
      } else {
        delete canvasElement.dataset.snapTargetX;
        delete canvasElement.dataset.snapTargetY;
        delete canvasElement.dataset.snapDeflected;
      }
      if (draft?.kind === "connect" || draft?.kind === "endpoint") {
        canvasElement.dataset.snapPort = draft.snap ? `${draft.snap.nodeId}:${draft.snap.port}` : "";
        canvasElement.dataset.snapPortFree = draft.snap ? String(draft.snap.free) : "";
      } else {
        delete canvasElement.dataset.snapPort;
        delete canvasElement.dataset.snapPortFree;
      }

      context.save();
      context.translate(view.offset.x, view.offset.y);
      context.scale(view.scale, view.scale);

      // Snap preview: the target cell is painted under the geometry so the user can see
      // exactly where the component is going to land before releasing the pointer.
      if (draft?.kind === "node" && draft.moved) {
        const range = visibleGridRange({ pan: view.offset, zoom: view.scale }, width, height);
        const accent = draft.deflected ? "#ffc65c" : "#3ee0ff";
        context.save();
        context.lineWidth = px(1.5);
        context.strokeStyle = draft.deflected ? "rgba(255, 198, 92, 0.5)" : "rgba(62, 224, 255, 0.46)";
        context.setLineDash([px(7), px(6)]);
        context.beginPath();
        context.moveTo(range.startX, draft.position.y);
        context.lineTo(range.endX, draft.position.y);
        context.moveTo(draft.position.x, range.startY);
        context.lineTo(draft.position.x, range.endY);
        context.stroke();
        context.setLineDash([]);

        const cell = Math.max(SCHEMATIC_GRID_SIZE * 1.5, px(52));
        context.fillStyle = draft.deflected ? "rgba(255, 198, 92, 0.14)" : "rgba(62, 224, 255, 0.13)";
        context.strokeStyle = accent;
        context.lineWidth = px(2);
        roundedRectPath(context, draft.position.x - cell / 2, draft.position.y - cell / 2, cell, cell, px(9));
        context.fill();
        context.stroke();

        if (draft.deflected) {
          // Show the cell the pointer asked for and the free cell it was pushed to, so a
          // refused position never looks like the editor ignoring the drag.
          const requested = snapToGrid(draft.pointer);
          context.strokeStyle = "rgba(255, 122, 122, 0.78)";
          context.lineWidth = px(1.5);
          context.setLineDash([px(5), px(4)]);
          roundedRectPath(context, requested.x - cell / 2, requested.y - cell / 2, cell, cell, px(9));
          context.stroke();
          context.beginPath();
          context.moveTo(requested.x, requested.y);
          context.lineTo(draft.position.x, draft.position.y);
          context.stroke();
          context.setLineDash([]);
        }
        context.restore();
      }

      // Every pipe is routed as horizontal and vertical runs before anything is drawn, so
      // the crossing markers come from the finished picture rather than being guessed at
      // per pipe. Crossings are resolved once here and shared by the strokes below.
      const routes = buildEdgeRoutes(renderedProject);
      const routeById = new Map(routes.map((route) => [route.id, route.points]));
      const crossings = wireCrossings(routes);
      const hopsByRoute = new Map<string, Map<number, RouteHop[]>>();
      for (const crossing of crossings) {
        const bySegment = hopsByRoute.get(crossing.routeId) ?? new Map<number, RouteHop[]>();
        // Step wide enough to clear the pipe being crossed, whatever its bore, so the
        // break stays readable instead of shrinking inside a fat run.
        const radius = Math.max(px(5), edgeCasingWidth(renderedProject.edges[crossing.overRouteId]) / 2 + px(3));
        const hop: RouteHop = { ...crossing.point, radius };
        bySegment.set(crossing.segmentIndex, [...(bySegment.get(crossing.segmentIndex) ?? []), hop]);
        hopsByRoute.set(crossing.routeId, bySegment);
      }
      const junctions = wireJunctions(routes);
      const connectedPorts = new Set(
        Object.values(renderedProject.edges).flatMap((edge) => [
          `${edge.from}:${edge.fromPort ?? "outlet"}`,
          `${edge.to}:${edge.toPort ?? "inlet"}`
        ])
      );
      canvasElement.dataset.wireRouting = "orthogonal";
      canvasElement.dataset.wireCrossings = String(crossings.length);

      const noHops = new Map<number, RouteHop[]>();
      Object.values(renderedProject.edges).forEach((edge) => {
        const from = renderedProject.nodes[edge.from];
        const to = renderedProject.nodes[edge.to];
        const solved: EdgeResult | undefined = activeResult.edgeResults[edge.id];
        const points = routeById.get(edge.id);
        if (!from || !to || !solved || !points) return;
        const widthScale = edge.shape.kind === "circular" ? edge.shape.diameter * 55 : edge.shape.height * 55;
        const metric =
          activeProject.visualization.overlay === "pressure"
            ? solved.pressureDrop
            : activeProject.visualization.overlay === "reynolds"
              ? solved.reynolds
              : solved.velocity;
        const color = overlayValueColor(metric, maxEdge, activeProject.visualization.overlay);
        const hovered = hover?.kind === "edge" && hover.id === edge.id;
        const hops = hopsByRoute.get(edge.id) ?? noHops;
        const casing = edgeCasingWidth(edge);

        // Corners are mitred rather than rounded off, which is what makes a run read as a
        // deliberate right angle instead of a slack hose.
        context.lineCap = "butt";
        context.lineJoin = "miter";
        context.miterLimit = 4;
        if (hovered && edge.id !== selectedId) {
          context.strokeStyle = "rgba(62, 224, 255, 0.34)";
          context.lineWidth = casing + 10;
          traceRouteWithHops(context, points, hops);
          context.stroke();
        }
        // The casing is drawn with the same steps as the core, so the break a crossing
        // leaves is not filled back in by the dark outline underneath.
        context.strokeStyle = "rgba(8, 20, 28, 0.95)";
        context.lineWidth = casing;
        traceRouteWithHops(context, points, hops);
        context.stroke();

        context.strokeStyle = edge.id === selectedId ? "#f7d84b" : color;
        context.lineWidth = Math.max(8, widthScale);
        context.shadowColor = color;
        context.shadowBlur = 18;
        traceRouteWithHops(context, points, hops);
        context.stroke();
        context.shadowBlur = 0;

        if (activeProject.visualization.particles) {
          const count = Math.max(7, Math.min(22, Math.floor(Math.abs(solved.velocity) * 4)));
          for (let i = 0; i < count; i += 1) {
            const t = (frameRef.current * Math.sign(solved.flowRate || 1) + i / count) % 1;
            const along = pointOnPolyline(points, t < 0 ? t + 1 : t);
            context.fillStyle = solved.cavitationRisk ? "#ff4d6d" : "#c8f7ff";
            context.beginPath();
            context.arc(along.x, along.y, 2.4, 0, Math.PI * 2);
            context.fill();
          }
        }
      });
      context.lineCap = "round";
      context.lineJoin = "round";

      // A pipe that ends on another pipe is a connection, so it gets a solid dot. Nothing
      // else on the sheet is a filled disc on a run, which is what tells it apart from the
      // hop drawn where two pipes merely cross.
      for (const junction of junctions) {
        context.beginPath();
        context.arc(junction.x, junction.y, Math.max(4, px(4.5)), 0, Math.PI * 2);
        context.fillStyle = "#9dfbd7";
        context.fill();
      }

      Object.values(renderedProject.nodes).forEach((node) => {
        const radius = nodeRadius(node);
        const angle = degreesToRadians(node.rotation ?? 0);
        const active = node.id === selectedId;
        const hovered = hover?.kind === "node" && hover.id === node.id;
        const grabbed = draft?.kind === "node" && draft.id === node.id;

        if (hovered || active || grabbed) {
          context.beginPath();
          context.arc(node.position.x, node.position.y, radius + 9, 0, Math.PI * 2);
          context.fillStyle = active ? "rgba(247, 216, 75, 0.16)" : "rgba(62, 224, 255, 0.16)";
          context.fill();
          context.lineWidth = 1.6;
          context.strokeStyle = active ? "rgba(247, 216, 75, 0.72)" : "rgba(62, 224, 255, 0.66)";
          context.stroke();
        }

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
          const flowPort = port === "inlet" || port === "outlet";
          const portHovered = hover?.kind === "port" && hover.nodeId === node.id && hover.port === port;
          const snapped =
            (draft?.kind === "connect" || draft?.kind === "endpoint")
            && draft.snap?.nodeId === node.id
            && draft.snap.port === port;
          const snapRefused = snapped && (draft.kind === "connect" || draft.kind === "endpoint") && draft.snap?.free === false;
          if (portHovered || snapped) {
            context.beginPath();
            context.arc(point.x, point.y, Math.max(11, px(13)), 0, Math.PI * 2);
            context.fillStyle = snapRefused
              ? "rgba(255, 122, 122, 0.26)"
              : snapped
                ? "rgba(157, 251, 215, 0.3)"
                : "rgba(62, 224, 255, 0.22)";
            context.fill();
            context.lineWidth = px(2);
            context.strokeStyle = snapRefused ? "#ff7a7a" : snapped ? "#9dfbd7" : "rgba(62, 224, 255, 0.85)";
            context.stroke();
          }
          // Filled disc means a pipe terminates here, hollow ring means the port is free.
          // That is the electronics convention, and it is what keeps "two pipes meet at
          // this component" from looking like "two pipes happen to cross here".
          const wired = connectedPorts.has(`${node.id}:${port}`);
          context.fillStyle = wired ? (active ? "#f7d84b" : "#9dfbd7") : active ? "#f7d84b" : "#071019";
          context.strokeStyle = flowPort ? "#9dfbd7" : "rgba(238, 248, 255, 0.45)";
          context.lineWidth = 1.5;
          context.beginPath();
          context.arc(point.x, point.y, wired ? 5 : flowPort ? 4.5 : 3.5, 0, Math.PI * 2);
          context.fill();
          context.stroke();
        });

        if (active && ["pump", "sink", "source", "junction"].includes(node.type)) {
          const handle = aimHandlePosition(node);
          const handleHovered = hover?.kind === "rotate" && hover.nodeId === node.id;
          context.strokeStyle = "rgba(247, 216, 75, 0.58)";
          context.lineWidth = 1.5;
          context.beginPath();
          context.moveTo(node.position.x, node.position.y);
          context.lineTo(handle.x, handle.y);
          context.stroke();
          context.fillStyle = handleHovered ? "#fff0a6" : "#f7d84b";
          context.beginPath();
          context.arc(handle.x, handle.y, handleHovered ? 8 : 6, 0, Math.PI * 2);
          context.fill();
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

      if (draft?.kind === "connect" || draft?.kind === "endpoint") {
        const anchor =
          draft.kind === "connect"
            ? draft.from.point
            : (() => {
                const edge = renderedProject.edges[draft.edgeId];
                return edge ? endpointPoint(edge, draft.endpoint === "from" ? "to" : "from", renderedProject.nodes) : null;
              })();
        // The rubber band terminates on the snapped port, not the raw pointer, so the
        // connection the user is about to make is the one they can see. Once it has a port
        // to land on it is previewed through the same router that will draw the finished
        // pipe, so the drop never rearranges the run the user was promised.
        const target = draft.snap?.point ?? draft.pointer;
        const anchorNode =
          draft.kind === "connect"
            ? renderedProject.nodes[draft.from.nodeId]
            : (() => {
                const edge = renderedProject.edges[draft.edgeId];
                if (!edge) return undefined;
                return renderedProject.nodes[draft.endpoint === "from" ? edge.to : edge.from];
              })();
        const anchorPort: PipePortId | null =
          draft.kind === "connect"
            ? draft.from.port
            : (() => {
                const edge = renderedProject.edges[draft.edgeId];
                if (!edge) return null;
                return draft.endpoint === "from" ? (edge.toPort ?? "inlet") : (edge.fromPort ?? "outlet");
              })();
        const snapNode = draft.snap ? renderedProject.nodes[draft.snap.nodeId] : undefined;
        const preview =
          anchor && anchorNode && anchorPort && draft.snap && snapNode
            ? routeOrthogonalPipe(
                anchor,
                portDirection(anchorNode, anchorPort),
                draft.snap.point,
                portDirection(snapNode, draft.snap.port),
                { obstacles: componentCentres(renderedProject.nodes) }
              )
            : anchor
              ? [anchor, target]
              : null;
        if (anchor && preview) {
          const tracePreview = () => {
            context.beginPath();
            context.moveTo(preview[0].x, preview[0].y);
            for (const point of preview.slice(1)) context.lineTo(point.x, point.y);
          };
          context.save();
          context.lineJoin = "miter";
          context.strokeStyle = "rgba(4, 12, 19, 0.85)";
          context.lineWidth = px(7);
          tracePreview();
          context.stroke();
          context.strokeStyle = draft.snap ? (draft.snap.free ? "#9dfbd7" : "#ff7a7a") : "#f7d84b";
          context.lineWidth = px(3);
          context.setLineDash(draft.snap?.free ? [] : [px(9), px(7)]);
          tracePreview();
          context.stroke();
          context.setLineDash([]);
          context.restore();
          if (draft.kind === "connect") drawEndpointHandle(anchor, true);
        }
      }

      context.restore();

      // Labels are drawn last, in screen space, at a fixed size. Anything that would land
      // on top of a label already placed is dropped, so the schematic never turns into
      // overlapping text no matter how far the user zooms out.
      const viewport = { pan: view.offset, zoom: view.scale };
      const placedLabels: LabelBox[] = [];
      // Regions no label may cover, whatever its priority: the snap markers must stay
      // readable while a component is being dragged, and no chip may sit on a routed run.
      const reservedBoxes: LabelBox[] = [];
      for (const route of routes) {
        const half = Math.max(4, (edgeCasingWidth(renderedProject.edges[route.id]) * view.scale) / 2 + LABEL_WIRE_CLEARANCE);
        reservedBoxes.push(...polylineObstacleBoxes(route.points.map((point) => worldToScreen(point, viewport)), half));
      }
      const snapMarkerHalf = (Math.max(SCHEMATIC_GRID_SIZE * 1.5, px(52)) * view.scale) / 2;
      if (draft?.kind === "node" && draft.moved) {
        const centre = worldToScreen(draft.position, viewport);
        reservedBoxes.push({
          left: centre.x - snapMarkerHalf,
          top: centre.y - snapMarkerHalf,
          right: centre.x + snapMarkerHalf,
          bottom: centre.y + snapMarkerHalf
        });
        if (draft.deflected) {
          const requested = worldToScreen(snapToGrid(draft.pointer), viewport);
          reservedBoxes.push({
            left: requested.x - snapMarkerHalf,
            top: requested.y - snapMarkerHalf,
            right: requested.x + snapMarkerHalf,
            bottom: requested.y + snapMarkerHalf
          });
        }
      }

      function drawLabelChip(
        anchor: Vec2,
        lines: string[],
        tone: "node" | "edge",
        emphasis: boolean,
        placement: LabelSide = "below"
      ) {
        const padding = 7;
        const lineHeight = 14;
        context.save();
        context.font = emphasis ? "700 11.5px Inter, system-ui, sans-serif" : "600 11.5px Inter, system-ui, sans-serif";
        const textWidth = Math.max(...lines.map((line) => context.measureText(line).width));
        const boxWidth = textWidth + padding * 2;
        const boxHeight = lines.length * lineHeight + padding * 2 - 3;
        // The chip walks a ladder of placements around its anchor and takes the first that
        // is clear of every routed run, every snap marker, and every chip already drawn.
        // A pipe with its own name sitting on top of it is unreadable, so a chip that
        // cannot find room anywhere is dropped rather than stamped over the wire.
        const candidates = labelPlacementCandidates(anchor, boxWidth, boxHeight, { preferred: placement, step: 13, rings: 4 });
        const box =
          firstClearBox(candidates, [...reservedBoxes, ...placedLabels])
          ?? (emphasis ? firstClearBox(candidates, reservedBoxes) : null);
        if (!box) {
          context.restore();
          return;
        }
        const left = Math.round(box.left);
        const top = Math.round(box.top);
        if (box.right < -40 || box.left > width + 40 || box.bottom < -40 || box.top > height + 40) {
          context.restore();
          return;
        }
        placedLabels.push(box);
        roundedRectPath(context, left, top, boxWidth, boxHeight, 6);
        context.fillStyle = emphasis ? "rgba(38, 32, 8, 0.88)" : "rgba(4, 12, 19, 0.82)";
        context.fill();
        context.lineWidth = 1;
        context.strokeStyle = emphasis
          ? "rgba(247, 216, 75, 0.66)"
          : tone === "node"
            ? "rgba(150, 194, 224, 0.24)"
            : "rgba(150, 194, 224, 0.18)";
        context.stroke();
        context.textAlign = "center";
        context.textBaseline = "alphabetic";
        lines.forEach((line, index) => {
          context.font =
            index === 0
              ? emphasis
                ? "700 11.5px Inter, system-ui, sans-serif"
                : "600 11.5px Inter, system-ui, sans-serif"
              : "500 10.5px Inter, system-ui, sans-serif";
          context.fillStyle = index === 0 ? (emphasis ? "#fff3c4" : "#e9f6ff") : "rgba(200, 222, 238, 0.78)";
          context.fillText(line, left + boxWidth / 2, top + padding + lineHeight * index + 9);
        });
        context.restore();
      }

      // Selected first, so the item the user is working on always keeps its label.
      const labelledNodes = Object.values(renderedProject.nodes).sort((left, right) =>
        Number(right.id === selectedId) - Number(left.id === selectedId)
      );
      for (const node of labelledNodes) {
        const solved = activeResult.nodeResults[node.id];
        const emphasis = node.id === selectedId || (hover?.kind === "node" && hover.id === node.id);
        const centre = worldToScreen(node.position, viewport);
        // The component being dragged keeps its name, pushed clear of the snap marker.
        const anchor =
          draft?.kind === "node" && draft.moved && draft.id === node.id
            ? { x: centre.x, y: centre.y + snapMarkerHalf + 8 }
            : worldToScreen({ x: node.position.x, y: node.position.y + nodeRadius(node) + 12 }, viewport);
        const lines = [node.label];
        if (solved) lines.push(`${Math.round(solved.pressure / 1000)} kPa · ${Math.round(node.rotation ?? 0)}°`);
        drawLabelChip(anchor, lines, "node", emphasis);
      }

      for (const edge of Object.values(renderedProject.edges)) {
        const solved = activeResult.edgeResults[edge.id];
        if (!solved) continue;
        const points = routeById.get(edge.id);
        if (!points) continue;
        const emphasis = edge.id === selectedId || (hover?.kind === "edge" && hover.id === edge.id);
        // The chip hangs off the longest straight run of the route rather than the straight
        // line between the two components, which after routing may not be on the pipe at all.
        const run = longestPolylineSegment(points);
        if (!run) continue;
        const mid = worldToScreen({ x: (run.from.x + run.to.x) / 2, y: (run.from.y + run.to.y) / 2 }, viewport);
        const runIsHorizontal = Math.abs(run.from.y - run.to.y) <= Math.abs(run.from.x - run.to.x);
        drawLabelChip(
          mid,
          [edge.label, `Re ${Math.round(solved.reynolds).toLocaleString()}`],
          "edge",
          emphasis,
          runIsHorizontal ? "above" : "right"
        );
      }

      recordEditorMetric("schematic-frame", performance.now() - started);
      recordEditorFrame("schematic-frame");

      animationId = requestAnimationFrame(draw);
    }

    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    resizeObserver?.observe(canvasElement);
    draw();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      resizeObserver?.disconnect();
      cancelAnimationFrame(animationId);
    };
  }, [canvasRenderMode, force2dProjection, onRenderBackendChange, resultDataset, derivedVisualization, derivedPresentationOptions, resultFieldSelection, resultVectorComponent, resultColorMap, selectedId, selectedKind, streamlineDisplay, streamlines]);

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
    const runtime = cinemaRef.current;
    runtime?.updateCamera(cinemaCamera);
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (runtime) canvas.dataset.cinemaNodePositions = JSON.stringify(runtime.projectedNodePositions);
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

  /**
   * Hit tolerances are authored in screen pixels and converted to world units, so a
   * component stays just as easy to grab when the drawing is zoomed out.
   */
  function tolerance(screenPixels: number) {
    return screenPixels / Math.max(0.05, viewRef.current.scale);
  }

  /**
   * A port already carrying a pipe end cannot take another; the store silently refuses.
   * Knowing this up front lets the snap target say so instead of the drop doing nothing.
   */
  function portIsFree(nodeId: string, port: PipePortId, ignoredEdgeId?: string) {
    return !Object.values(project.edges).some(
      (edge) =>
        edge.id !== ignoredEdgeId
        && ((edge.from === nodeId && (edge.fromPort ?? "outlet") === port)
          || (edge.to === nodeId && (edge.toPort ?? "inlet") === port))
    );
  }

  function portAt(point: Vec2, radius = tolerance(18), excludeNodeId?: string, ignoredEdgeId?: string): PortSnap | null {
    const activeProject = currentProject();
    let best: PortSnap | null = null;
    let bestDistance = radius;
    for (const node of Object.values(activeProject.nodes)) {
      if (node.id === excludeNodeId) continue;
      for (const port of ports) {
        const portPoint = portPosition(node, port);
        const distance = Math.hypot(portPoint.x - point.x, portPoint.y - point.y);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = { nodeId: node.id, port, point: portPoint, free: portIsFree(node.id, port, ignoredEdgeId) };
        }
      }
    }
    return best;
  }

  function selectedEndpointAt(point: Vec2): { edgeId: string; endpoint: "from" | "to" } | null {
    if (selectedKind !== "edge" || !selectedId) return null;
    const activeProject = currentProject();
    const edge = activeProject.edges[selectedId];
    if (!edge) return null;
    const reach = tolerance(16);
    const from = endpointPoint(edge, "from", activeProject.nodes);
    const to = endpointPoint(edge, "to", activeProject.nodes);
    if (from && Math.hypot(from.x - point.x, from.y - point.y) < reach) return { edgeId: edge.id, endpoint: "from" };
    if (to && Math.hypot(to.x - point.x, to.y - point.y) < reach) return { edgeId: edge.id, endpoint: "to" };
    return null;
  }

  function rotateHandleAt(point: Vec2): FluidNode | null {
    if (selectedKind !== "node" || !selectedId) return null;
    const node = currentProject().nodes[selectedId];
    if (!node) return null;
    const handle = aimHandlePosition(node);
    return Math.hypot(handle.x - point.x, handle.y - point.y) < tolerance(18) ? node : null;
  }

  /**
   * Resolves a body-versus-port hit by distance rather than by fixed priority. Both hit
   * zones are screen-constant, so when the drawing is zoomed out a fixed priority would
   * let the port zones swallow the node body and make components undraggable.
   */
  function nodeOrPortAt(point: Vec2): { kind: "node"; node: FluidNode } | { kind: "port"; hit: PortHit } | null {
    const activeProject = currentProject();
    const nodeReach = tolerance(24);
    const portReach = tolerance(18);
    let best: { kind: "node"; node: FluidNode } | { kind: "port"; hit: PortHit } | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const node of Object.values(activeProject.nodes)) {
      const bodyDistance = Math.hypot(node.position.x - point.x, node.position.y - point.y);
      if (bodyDistance < nodeReach && bodyDistance < bestDistance) {
        bestDistance = bodyDistance;
        best = { kind: "node", node };
      }
      for (const port of ports) {
        const portPoint = portPosition(node, port);
        const portDistance = Math.hypot(portPoint.x - point.x, portPoint.y - point.y);
        if (portDistance < portReach && portDistance < bestDistance) {
          bestDistance = portDistance;
          best = { kind: "port", hit: { nodeId: node.id, port, point: portPoint } };
        }
      }
    }
    return best;
  }

  /**
   * Picks the pipe nearest the pointer, measured against the routed run the user can see.
   * The routes are rebuilt exactly as the renderer builds them, so a click lands on the
   * pipe under the cursor rather than on the straight line it used to be drawn as.
   */
  function edgeAt(point: Vec2) {
    const activeProject = currentProject();
    const reach = tolerance(20);
    let best: FluidEdge | undefined;
    let bestDistance = reach;
    for (const route of buildEdgeRoutes(activeProject)) {
      const distance = distanceToPolyline(point, route.points);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = activeProject.edges[route.id];
      }
    }
    return best;
  }

  /** What the pointer is currently over, in the same priority order the click handler uses. */
  function hoverTargetAt(point: Vec2): HoverTarget | null {
    const rotateTarget = rotateHandleAt(point);
    if (rotateTarget) return { kind: "rotate", nodeId: rotateTarget.id };
    const endpoint = selectedEndpointAt(point);
    if (endpoint) return { kind: "endpoint", ...endpoint };
    const component = nodeOrPortAt(point);
    if (component?.kind === "port") return { kind: "port", nodeId: component.hit.nodeId, port: component.hit.port };
    if (component?.kind === "node") return { kind: "node", id: component.node.id };
    const edge = edgeAt(point);
    if (edge) return { kind: "edge", id: edge.id };
    return null;
  }

  function applyCursor(canvas: HTMLCanvasElement, value: string, hoverKind: string) {
    canvas.style.cursor = value;
    canvas.dataset.hoverKind = hoverKind;
  }

  /** Snapped drop position plus whether a collision pushed it off the requested cell. */
  function resolveNodeDrop(nodeId: string, desired: Vec2) {
    const requested = snapToGrid(desired);
    const position = snapNodeToFreeCell(project, nodeId, desired);
    return { position, deflected: position.x !== requested.x || position.y !== requested.y };
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
      // Once a result surface is present, a canvas click may select a schematic
      // edge only through verified result-cell provenance in App. Picking the
      // schematic preview hidden behind the field would infer ownership from
      // display geometry.
      const pick = resultDataset ? null : cinemaPick(event);
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
        dragRef.current = { kind: "connect", from: { nodeId: pick.nodeId, port: pick.port, point: pick.point }, pointer: point ?? pick.point, snap: null };
        capturePointer(event.currentTarget, event.pointerId);
        return;
      }
      if (pick?.kind === "node" && point) {
        const node = currentProject().nodes[pick.id];
        if (node) {
          onSelect("node", node.id);
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

    if (canvasRenderMode === "cinema" && resultDataset) {
      const rect = event.currentTarget.getBoundingClientRect();
      dragRef.current = {
        kind: "canvas-pan",
        start: screenPoint(event),
        viewport: { ...schematicViewportRef.current, pan: { ...schematicViewportRef.current.pan } },
        moved: false
      };
      onProbePoint(screen, { width: rect.width, height: rect.height });
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

    // The selected pipe draws grab handles on its own two ends. Those handles win over a
    // fresh connection so the affordance the user can see is the one they get; a port that
    // already carries a pipe end could not start a new one anyway.
    const selectedHandle = selectedEndpointAt(point);
    if (selectedHandle) {
      dragRef.current = { kind: "endpoint", ...selectedHandle, pointer: point, snap: null };
      applyCursor(event.currentTarget, "crosshair", "endpoint");
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const component = nodeOrPortAt(point);
    if (component?.kind === "port") {
      onSelect("node", component.hit.nodeId);
      dragRef.current = { kind: "connect", from: component.hit, pointer: point, snap: null };
      applyCursor(event.currentTarget, "crosshair", "port");
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    if (component?.kind === "node") {
      const node = component.node;
      onSelect("node", node.id);

      const offsetX = point.x - node.position.x;
      const offsetY = point.y - node.position.y;
      const drop = resolveNodeDrop(node.id, { x: point.x - offsetX, y: point.y - offsetY });
      dragRef.current = {
        kind: "node",
        id: node.id,
        offsetX,
        offsetY,
        position: drop.position,
        pointer: { x: point.x - offsetX, y: point.y - offsetY },
        deflected: drop.deflected,
        moved: false
      };
      applyCursor(event.currentTarget, "grabbing", "node");
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const endpoint = selectedEndpointAt(point);
    if (endpoint) {
      dragRef.current = { kind: "endpoint", ...endpoint, pointer: point, snap: null };
      applyCursor(event.currentTarget, "crosshair", "endpoint");
      capturePointer(event.currentTarget, event.pointerId);
      return;
    }

    const edge = edgeAt(point);
    if (edge) onSelect("edge", edge.id);
    else {
      dragRef.current = { kind: "canvas-pan", start: screen, viewport: { ...schematicViewportRef.current, pan: { ...schematicViewportRef.current.pan } }, moved: false };
      applyCursor(event.currentTarget, "grabbing", "canvas");
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
        userZoomedRef.current = true;
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
      if (!dragRef.current) {
        // Idle hover: keep the cursor and the highlight in step with what is under it,
        // so what is draggable is obvious before the user presses anything.
        if (canvasRenderMode === "schematic") {
          const target = hoverTargetAt(canvasPoint(event));
          hoverRef.current = target;
          applyCursor(event.currentTarget, target ? cursorForHover[target.kind] : "grab", target?.kind ?? "canvas");
        }
        return;
      }
      const point = canvasRenderMode === "cinema" && cinemaRef.current ? (cinemaPoint(event) ?? canvasPoint(event)) : canvasPoint(event);
      if (dragRef.current.kind === "node") {
      const desired = { x: point.x - dragRef.current.offsetX, y: point.y - dragRef.current.offsetY };
      const drop = resolveNodeDrop(dragRef.current.id, desired);
      const travelled = Math.hypot(desired.x - dragRef.current.pointer.x, desired.y - dragRef.current.pointer.y);
      dragRef.current = {
        ...dragRef.current,
        position: drop.position,
        pointer: desired,
        deflected: drop.deflected,
        moved: dragRef.current.moved || travelled > 0.5
      };
      if (canvasRenderMode === "cinema") cinemaRef.current?.updateModel(currentProject(), result);
      } else if (dragRef.current.kind === "rotate") {
      const node = currentProject().nodes[dragRef.current.nodeId];
      if (node) {
        const angle = (Math.atan2(point.y - node.position.y, point.x - node.position.x) * 180) / Math.PI;
        dragRef.current = { ...dragRef.current, rotation: angle };
        if (canvasRenderMode === "cinema") cinemaRef.current?.updateModel(currentProject(), result);
      }
      } else if (dragRef.current.kind === "connect") {
      dragRef.current = {
        ...dragRef.current,
        pointer: point,
        snap: portAt(point, tolerance(PORT_SNAP_RADIUS), dragRef.current.from.nodeId)
      };
      } else if (dragRef.current.kind === "endpoint") {
      dragRef.current = {
        ...dragRef.current,
        pointer: point,
        snap: portAt(point, tolerance(PORT_SNAP_RADIUS), undefined, dragRef.current.edgeId)
      };
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
    const active = dragRef.current;
    // The port shown as the snap target during the drag is the one that gets connected.
    const dragSnap = active?.kind === "connect" || active?.kind === "endpoint" ? active.snap : null;
    const target =
      cinemaTarget?.kind === "port"
        ? { nodeId: cinemaTarget.nodeId, port: cinemaTarget.port, point: cinemaTarget.point }
        : (dragSnap ?? portAt(point, tolerance(PORT_SNAP_RADIUS)));
    if (!cancelled && active?.kind === "node" && active.moved) {
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
      onProbePoint(
        screenPoint(event),
        { width: rect.width, height: rect.height },
        cinemaRef.current?.probeAt(event) ?? null
      );
    }

    dragRef.current = null;
    if (canvasRenderMode === "schematic") {
      const hovered = hoverTargetAt(point);
      hoverRef.current = hovered;
      applyCursor(event.currentTarget, hovered ? cursorForHover[hovered.kind] : "grab", hovered?.kind ?? "canvas");
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function handlePointerLeave() {
    hoverRef.current = null;
  }

  function handleWheel(event: WheelEvent<HTMLCanvasElement>) {
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom * Math.exp(-event.deltaY * 0.0012)) });
      return;
    }
    userZoomedRef.current = true;
    schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, point, event.deltaY);
  }

  function fitViewport() {
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange(cinemaRef.current?.fitCamera(cinemaCamera, project) ?? { ...cinemaCamera, zoom: 1, pan: { x: 0, y: 0 } });
      return;
    }
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    // Fit hands framing back to the app, so later resizes keep the drawing framed.
    userZoomedRef.current = false;
    schematicViewportRef.current = fitSchematicViewport(project, rect.width, rect.height, { insets: schematicFitInsets });
    schematicViewportInitializedRef.current = true;
  }

  function resetViewport() {
    if (canvasRenderMode === "cinema") {
      onCinemaCameraChange({ yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } });
      return;
    }
    // Reset is a deliberate 1:1 framing choice, so auto-fit stops taking it back.
    userZoomedRef.current = true;
    schematicViewportRef.current = resetSchematicViewport();
    schematicViewportInitializedRef.current = true;
  }

  /**
   * Lays the network out left to right on the grid with the wire crossings unpicked, aims
   * every component along the flow so its ports face the runs that reach them, and lets
   * the router redraw. It goes through the same `onMoveNode`/`onRotateNode` the drag
   * handlers use, so a tidy is undoable exactly like any other edit.
   */
  function tidyLayout() {
    if (canvasRenderMode !== "schematic") return 0;
    const arranged = tidySchematicLayout(project);
    const aimed = tidySchematicRotations(project, arranged);
    let changed = 0;
    for (const [id, position] of Object.entries(arranged)) {
      const node = project.nodes[id];
      if (!node || (node.position.x === position.x && node.position.y === position.y)) continue;
      onMoveNode(id, position);
      changed += 1;
    }
    for (const [id, rotation] of Object.entries(aimed)) {
      const node = project.nodes[id];
      if (!node || Math.round(node.rotation ?? 0) === rotation) continue;
      onRotateNode(id, rotation);
      changed += 1;
    }
    const canvas = canvasRef.current;
    if (canvas) canvas.dataset.tidyChanged = String(changed);
    return changed;
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
      // Keyboard nudges move by whole grid cells so they land on the same lattice as drags.
      const step = SCHEMATIC_GRID_SIZE * (event.shiftKey ? 3 : 1);
      const delta = {
        x: event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0,
        y: event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0
      };
      onMoveNode(node.id, snapNodeToFreeCell(project, node.id, { x: node.position.x + delta.x, y: node.position.y + delta.y }));
      event.preventDefault();
    } else if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      fitViewport();
    } else if (event.key === "t" || event.key === "T") {
      event.preventDefault();
      tidyLayout();
    } else if (event.key === "0") {
      event.preventDefault();
      resetViewport();
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      if (canvasRenderMode === "cinema") onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom * 1.15) });
      else {
        userZoomedRef.current = true;
        schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, { x: viewRef.current.offset.x, y: viewRef.current.offset.y }, -120);
      }
    } else if (event.key === "-") {
      event.preventDefault();
      if (canvasRenderMode === "cinema") onCinemaCameraChange({ ...cinemaCamera, zoom: clampViewportZoom(cinemaCamera.zoom / 1.15) });
      else {
        userZoomedRef.current = true;
        schematicViewportRef.current = zoomViewportAtPoint(schematicViewportRef.current, { x: viewRef.current.offset.x, y: viewRef.current.offset.y }, 120);
      }
    }
  }

  return (
    <>
      <canvas
        key={`${canvasRenderMode}-${force2dProjection ? "projection" : "auto"}`}
        ref={canvasRef}
        data-testid={testId}
        data-selected-id={selectedId ?? ""}
        data-selected-kind={selectedKind ?? ""}
        className="simulation-canvas"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={(event) => handlePointerEnd(event)}
        onPointerCancel={(event) => handlePointerEnd(event, true)}
        onPointerLeave={handlePointerLeave}
        onWheel={handleWheel}
        onKeyDown={handleKeyDown}
        aria-label={ariaLabel}
        aria-describedby={statusId}
        role="application"
        tabIndex={0}
        aria-keyshortcuts="F T 0 + -"
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
