import type { FluidNode, FluidProject, Vec2 } from "../types";

export const MIN_VIEWPORT_ZOOM = 0.25;
export const MAX_VIEWPORT_ZOOM = 4;
export const DEFAULT_VIEWPORT_ZOOM = 1;

/** World units between two schematic grid lines. Every drag lands on a multiple of this. */
export const SCHEMATIC_GRID_SIZE = 40;
/** Grid lines are emphasised every N cells so the eye can count cells while dragging. */
export const SCHEMATIC_GRID_MAJOR = 5;
/**
 * Minimum centre-to-centre distance between two components. Two grid cells clears both
 * the bodies and the port rings drawn around them, so no two components can overlap.
 */
export const MIN_NODE_SEPARATION = SCHEMATIC_GRID_SIZE * 2;
/** How close a dragged pipe end has to come, in world units, before it locks onto a port. */
export const PORT_SNAP_RADIUS = 34;
/** Auto-fit never magnifies past this, so a two-node sketch does not fill the panel with one pipe. */
export const MAX_FIT_ZOOM = 1.6;

export type SchematicViewport = {
  pan: Vec2;
  zoom: number;
};

export type ViewportInsets = {
  top: number;
  right: number;
  bottom: number;
  left: number;
};

export type FitOptions = {
  /** World-unit halo kept around the drawing. */
  padding?: number;
  /** Screen-space regions of the canvas that are covered by controls and must stay clear. */
  insets?: Partial<ViewportInsets>;
  maxZoom?: number;
};

export type CinemaCameraState = {
  yaw: number;
  pitch: number;
  zoom: number;
  pan: Vec2;
};

export const defaultSchematicViewport: SchematicViewport = {
  pan: { x: 0, y: 0 },
  zoom: DEFAULT_VIEWPORT_ZOOM
};

export const defaultCinemaCamera: CinemaCameraState = {
  yaw: 0,
  pitch: 38,
  zoom: 1,
  pan: { x: 0, y: 0 }
};

export function clampViewportZoom(zoom: number): number {
  return Math.max(MIN_VIEWPORT_ZOOM, Math.min(MAX_VIEWPORT_ZOOM, zoom));
}

export function zoomViewportAtPoint(viewport: SchematicViewport, screenPoint: Vec2, delta: number): SchematicViewport {
  const nextZoom = clampViewportZoom(viewport.zoom * Math.exp(-delta * 0.0012));
  const ratio = nextZoom / viewport.zoom;
  return {
    zoom: nextZoom,
    pan: {
      x: screenPoint.x - (screenPoint.x - viewport.pan.x) * ratio,
      y: screenPoint.y - (screenPoint.y - viewport.pan.y) * ratio
    }
  };
}

export function resetSchematicViewport(): SchematicViewport {
  return { pan: { ...defaultSchematicViewport.pan }, zoom: DEFAULT_VIEWPORT_ZOOM };
}

/** Rounds a world point onto the nearest grid intersection. */
export function snapToGrid(point: Vec2, grid: number = SCHEMATIC_GRID_SIZE): Vec2 {
  const step = Math.max(1, grid);
  return { x: Math.round(point.x / step) * step, y: Math.round(point.y / step) * step };
}

function nodesExcept(project: FluidProject, nodeId: string | null): FluidNode[] {
  return Object.values(project.nodes).filter((node) => node.id !== nodeId);
}

/** True when no other component sits close enough to `point` to read as an overlap. */
export function isCellFree(
  project: FluidProject,
  nodeId: string | null,
  point: Vec2,
  separation: number = MIN_NODE_SEPARATION
): boolean {
  return nodesExcept(project, nodeId).every(
    (node) => Math.hypot(node.position.x - point.x, node.position.y - point.y) >= separation - 1e-6
  );
}

/**
 * Snaps `desired` onto the grid and, when that cell is already taken, walks outwards
 * ring by ring to the closest free cell. Placement is therefore always predictable and
 * never stacks two components on the same spot.
 */
export function snapNodeToFreeCell(
  project: FluidProject,
  nodeId: string | null,
  desired: Vec2,
  grid: number = SCHEMATIC_GRID_SIZE,
  separation: number = MIN_NODE_SEPARATION
): Vec2 {
  const step = Math.max(1, grid);
  const anchor = snapToGrid(desired, step);
  if (isCellFree(project, nodeId, anchor, separation)) return anchor;

  const maxRing = Math.max(3, Math.ceil(separation / step) + 6);
  for (let ring = 1; ring <= maxRing; ring += 1) {
    let best: Vec2 | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let dx = -ring; dx <= ring; dx += 1) {
      for (let dy = -ring; dy <= ring; dy += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
        const candidate = { x: anchor.x + dx * step, y: anchor.y + dy * step };
        if (!isCellFree(project, nodeId, candidate, separation)) continue;
        const distance = Math.hypot(candidate.x - desired.x, candidate.y - desired.y);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = candidate;
        }
      }
    }
    if (best) return best;
  }
  return anchor;
}

export type SchematicBounds = { minX: number; minY: number; maxX: number; maxY: number };

/** Axis-aligned world bounds of every component in the project. */
export function schematicContentBounds(project: FluidProject): SchematicBounds | null {
  const nodes = Object.values(project.nodes);
  if (nodes.length === 0) return null;
  return {
    minX: Math.min(...nodes.map((node) => node.position.x)),
    maxX: Math.max(...nodes.map((node) => node.position.x)),
    minY: Math.min(...nodes.map((node) => node.position.y)),
    maxY: Math.max(...nodes.map((node) => node.position.y))
  };
}

/**
 * Frames the whole drawing inside the drawable area of the canvas.
 *
 * `insets` describes screen-space strips that anchored controls occupy, so the fitted
 * drawing is centred in what is actually visible rather than behind a button cluster.
 * A single-row network still gets a sensible zoom because both axes are given a floor.
 */
export function fitSchematicViewport(
  project: FluidProject,
  width: number,
  height: number,
  options: FitOptions = {}
): SchematicViewport {
  const bounds = schematicContentBounds(project);
  if (!bounds) return resetSchematicViewport();

  const padding = options.padding ?? SCHEMATIC_GRID_SIZE * 0.75;
  const maxZoom = options.maxZoom ?? MAX_FIT_ZOOM;
  const insets: ViewportInsets = {
    top: options.insets?.top ?? 0,
    right: options.insets?.right ?? 0,
    bottom: options.insets?.bottom ?? 0,
    left: options.insets?.left ?? 0
  };

  const frameWidth = Math.max(80, width - insets.left - insets.right);
  const frameHeight = Math.max(80, height - insets.top - insets.bottom);
  const frameCenter = {
    x: insets.left + frameWidth / 2,
    y: insets.top + frameHeight / 2
  };

  // A perfectly horizontal or vertical network has zero extent on one axis. Give both
  // axes a floor so the fit is driven by the drawing, not by a divide-by-nothing.
  const worldWidth = Math.max(bounds.maxX - bounds.minX + padding * 2, SCHEMATIC_GRID_SIZE * 6);
  const worldHeight = Math.max(bounds.maxY - bounds.minY + padding * 2, SCHEMATIC_GRID_SIZE * 6);

  const zoom = clampViewportZoom(Math.min(maxZoom, Math.min(frameWidth / worldWidth, frameHeight / worldHeight)));
  return {
    zoom,
    pan: {
      x: frameCenter.x - ((bounds.minX + bounds.maxX) / 2) * zoom,
      y: frameCenter.y - ((bounds.minY + bounds.maxY) / 2) * zoom
    }
  };
}

export function panSchematicViewport(viewport: SchematicViewport, delta: Vec2): SchematicViewport {
  return {
    zoom: viewport.zoom,
    pan: { x: viewport.pan.x + delta.x, y: viewport.pan.y + delta.y }
  };
}

export function screenToWorld(point: Vec2, viewport: SchematicViewport): Vec2 {
  return {
    x: (point.x - viewport.pan.x) / viewport.zoom,
    y: (point.y - viewport.pan.y) / viewport.zoom
  };
}

export function worldToScreen(point: Vec2, viewport: SchematicViewport): Vec2 {
  return {
    x: point.x * viewport.zoom + viewport.pan.x,
    y: point.y * viewport.zoom + viewport.pan.y
  };
}

/** World-space range of grid lines that is currently on screen. */
export function visibleGridRange(
  viewport: SchematicViewport,
  width: number,
  height: number,
  grid: number = SCHEMATIC_GRID_SIZE
) {
  const step = Math.max(1, grid);
  const topLeft = screenToWorld({ x: 0, y: 0 }, viewport);
  const bottomRight = screenToWorld({ x: width, y: height }, viewport);
  return {
    step,
    startX: Math.floor(topLeft.x / step) * step,
    endX: Math.ceil(bottomRight.x / step) * step,
    startY: Math.floor(topLeft.y / step) * step,
    endY: Math.ceil(bottomRight.y / step) * step
  };
}

export function resetCinemaCamera(): CinemaCameraState {
  return { ...defaultCinemaCamera, pan: { ...defaultCinemaCamera.pan } };
}
