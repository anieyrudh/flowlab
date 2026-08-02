import type { FluidEdge, FluidNode, FluidProject, PipePortId, Vec2 } from "../types";

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

/**
 * World units a pipe runs straight out of a port before it is allowed to turn. Ports sit
 * 24-29 units from a component centre, so a 16-unit stub puts the first corner clear of
 * both the body and the hover ring drawn around it.
 */
export const ROUTE_PORT_STUB = 16;
/** Extra clearance a pipe takes when it has to double back around its own component. */
export const ROUTE_DETOUR = SCHEMATIC_GRID_SIZE;
/**
 * How far a routed run tries to stay from a component centre. The widest body is 19 units
 * across with its port ring at 29, so a cell and a half keeps a run visibly outside the
 * component rather than threading through its ports.
 */
export const ROUTE_CLEARANCE = SCHEMATIC_GRID_SIZE * 1.5;

/** Tolerance for "same coordinate" comparisons on routed geometry. */
const GEOMETRY_EPSILON = 1e-6;

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

/* ------------------------------------------------------------------------------------ *
 * Camera angles
 *
 * Yaw and pitch are bearings, not free numbers. Orbiting adds to them without bound, so
 * after two turns of the drag the stored yaw is something like -572 while every control
 * that shows it - a -180..180 slider, a "N deg" readout, a check for "am I on the XY
 * plane" - is reading an angle it has no way to recognise. Folding both onto their
 * canonical range is what makes a negative or wrapped angle mean the same thing to the
 * camera and to the user interface, so it is done once, here, next to the state's own type.
 * ------------------------------------------------------------------------------------ */

/**
 * Steepest downward and upward pitch.
 *
 * The poles are included: at +90 the camera is directly overhead and the XY plane is seen
 * true, at -90 it is directly underneath. Those are two of the three principal views, so a
 * clamp that stopped short of them was the reason the scene could only ever be looked at
 * from one plane.
 */
export const CINEMA_MIN_PITCH = -90;
export const CINEMA_MAX_PITCH = 90;

/**
 * Yaw folded onto one turn, in (-180, 180].
 *
 * -180 and 180 are the same bearing; the positive one is reported so the value is stable
 * under repeated normalisation and lands exactly on a symmetric slider's own end stop
 * rather than one step outside it.
 */
export function normalizeYawDegrees(yaw: number): number {
  if (!Number.isFinite(yaw)) return 0;
  const wrapped = ((((yaw + 180) % 360) + 360) % 360) - 180;
  return wrapped === -180 ? 180 : wrapped;
}

export function clampCinemaPitch(pitch: number): number {
  if (!Number.isFinite(pitch)) return defaultCinemaCamera.pitch;
  return Math.max(CINEMA_MIN_PITCH, Math.min(CINEMA_MAX_PITCH, pitch));
}

/** The same camera with its angles folded onto the range every control assumes. */
export function normalizeCinemaCamera(camera: CinemaCameraState): CinemaCameraState {
  return {
    ...camera,
    yaw: normalizeYawDegrees(camera.yaw),
    pitch: clampCinemaPitch(camera.pitch),
    pan: { ...camera.pan }
  };
}

/**
 * The three principal planes, plus the isometric three-quarter view.
 *
 * `xy` is the plan the schematic is drawn on; `xz` and `yz` are the two elevations, which
 * are the views that show what `node.elevation` actually does. Naming them by plane rather
 * than by "Top"/"Front" keeps the button and the geometry saying the same thing.
 */
export type CinemaViewPlane = "iso" | "xy" | "xz" | "yz";

export const cinemaPlaneOrientations: Record<CinemaViewPlane, { yaw: number; pitch: number; label: string }> = {
  iso: { yaw: 0, pitch: 38, label: "Iso" },
  xy: { yaw: 0, pitch: CINEMA_MAX_PITCH, label: "XY plan" },
  xz: { yaw: 0, pitch: 0, label: "XZ elevation" },
  yz: { yaw: 90, pitch: 0, label: "YZ elevation" }
};

/**
 * The camera turned onto one plane, keeping whatever the viewer had zoomed and panned to.
 *
 * Carrying zoom and pan across is what makes the plane buttons a turn of the same camera
 * rather than four separate modes the viewer has to re-establish their place in.
 */
export function cinemaCameraForPlane(plane: CinemaViewPlane, camera: CinemaCameraState): CinemaCameraState {
  const orientation = cinemaPlaneOrientations[plane];
  return { ...camera, yaw: orientation.yaw, pitch: orientation.pitch, pan: { ...camera.pan } };
}

/** How close to a plane's own bearing still counts as being on it, in degrees. */
const PLANE_TOLERANCE_DEGREES = 0.5;

/**
 * Which plane the camera is looking along, or null when it is between planes.
 *
 * Comparison is on normalised angles, so a yaw of -270 is recognised as the YZ elevation
 * exactly as a yaw of 90 is - which is the whole point of normalising in the first place.
 */
export function cinemaViewPlaneOf(camera: Pick<CinemaCameraState, "yaw" | "pitch">): CinemaViewPlane | null {
  const yaw = normalizeYawDegrees(camera.yaw);
  const pitch = clampCinemaPitch(camera.pitch);
  // Straight overhead or straight underneath both look along Z, so both see the XY plane
  // true; the compass bearing only decides which way up the plan is printed.
  if (Math.abs(Math.abs(pitch) - CINEMA_MAX_PITCH) <= PLANE_TOLERANCE_DEGREES) return "xy";
  for (const plane of ["xz", "yz", "iso"] as CinemaViewPlane[]) {
    const orientation = cinemaPlaneOrientations[plane];
    if (Math.abs(pitch - orientation.pitch) > PLANE_TOLERANCE_DEGREES) continue;
    const yawGap = Math.abs(normalizeYawDegrees(yaw - orientation.yaw));
    if (yawGap <= PLANE_TOLERANCE_DEGREES) return plane;
    // An elevation shows the same plane from either side of it; a three-quarter view seen
    // from behind is a different three-quarter view, so only the elevations get this.
    if (plane !== "iso" && Math.abs(yawGap - 180) <= PLANE_TOLERANCE_DEGREES) return plane;
  }
  return null;
}

/* ------------------------------------------------------------------------------------ *
 * Orthogonal pipe routing
 *
 * An electronics schematic reads cleanly because every wire is a run of horizontal and
 * vertical segments that leaves its pin along the pin's own axis. These functions are the
 * geometry half of that: they take two port points plus the direction each port faces and
 * return a polyline. Nothing here touches a canvas, so every rule below is unit-testable.
 * ------------------------------------------------------------------------------------ */

export type Segment = { from: Vec2; to: Vec2 };
/** A routed pipe: the polyline plus the id of the edge it belongs to. */
export type WireRoute = { id: string; points: Vec2[] };
/**
 * Where one pipe passes over another. `routeId`/`segmentIndex` is the run that steps over;
 * `overRouteId` is the run it steps over, whose thickness decides how wide the step has to
 * be to stay visible.
 */
export type WireCrossing = { routeId: string; segmentIndex: number; overRouteId: string; point: Vec2 };

/**
 * Nearest axis-aligned unit vector. Component rotation is free, but a schematic run is
 * only ever horizontal or vertical, so a port that is aimed at 40 degrees still leaves
 * along the axis it is closest to.
 */
export function axisDirection(direction: Vec2): Vec2 {
  if (Math.abs(direction.x) >= Math.abs(direction.y)) return { x: direction.x < 0 ? -1 : 1, y: 0 };
  return { x: 0, y: direction.y < 0 ? -1 : 1 };
}

function offsetAlong(point: Vec2, direction: Vec2, distance: number): Vec2 {
  return { x: point.x + direction.x * distance, y: point.y + direction.y * distance };
}

/** True for an axis direction produced by `axisDirection` that runs left/right. */
function runsHorizontally(direction: Vec2): boolean {
  return direction.y === 0;
}

/** A straight run already taken by another pipe: `position` is fixed, `from`/`to` span it. */
export type LaneSpan = { axis: "horizontal" | "vertical"; position: number; from: number; to: number };

export type RouteOptions = {
  /** Straight run out of a port before the first turn is allowed. */
  stub?: number;
  /** Extra escape distance used when the route has to double back. */
  detour?: number;
  grid?: number;
  /** Component centres a run should stay off, normally every node in the project. */
  obstacles?: readonly Vec2[];
  /** How far from an obstacle centre counts as clear. */
  clearance?: number;
  /**
   * Runs already claimed by pipes routed earlier. Two wires sharing a lane draw straight on
   * top of each other, which is worse than a crossing because there is nothing left to see.
   */
  occupiedLanes?: readonly LaneSpan[];
};

type RouteContext = {
  detour: number;
  grid: number;
  clearance: number;
  obstacles: readonly Vec2[];
  occupiedLanes: readonly LaneSpan[];
  laneSeparation: number;
};

/** Every straight run a finished route occupies, ready to feed the next route's options. */
export function routeLaneSpans(points: readonly Vec2[]): LaneSpan[] {
  return polylineSegments(points).map((segment) => {
    const horizontal = Math.abs(segment.from.y - segment.to.y) <= GEOMETRY_EPSILON;
    return horizontal
      ? { axis: "horizontal" as const, position: segment.from.y, from: segment.from.x, to: segment.to.x }
      : { axis: "vertical" as const, position: segment.from.x, from: segment.from.y, to: segment.to.y };
  });
}

/**
 * How much of a candidate route would be drawn straight on top of a pipe already routed.
 *
 * Length rather than a count, because the choice is usually between a short shared stretch
 * and a long one: two runs sharing 40 units is a nick, sharing 200 units is one wire
 * hiding another.
 */
function overlapLength(context: RouteContext, spans: readonly LaneSpan[]): number {
  let total = 0;
  for (const span of spans) {
    const low = Math.min(span.from, span.to);
    const high = Math.max(span.from, span.to);
    for (const lane of context.occupiedLanes) {
      if (lane.axis !== span.axis) continue;
      if (Math.abs(lane.position - span.position) >= context.laneSeparation) continue;
      const shared = Math.min(high, Math.max(lane.from, lane.to)) - Math.max(low, Math.min(lane.from, lane.to));
      if (shared > GEOMETRY_EPSILON) total += shared;
    }
  }
  return total;
}

/**
 * Coordinates of components that would sit on a connector leg.
 *
 * `laneAxis` is the axis the leg's position is measured on, so a vertical leg reports the
 * x of every component whose y falls inside the stretch the leg spans.
 */
function blockedLaneCoordinates(context: RouteContext, laneAxis: "x" | "y", spanStart: number, spanEnd: number): number[] {
  const spanAxis = laneAxis === "x" ? "y" : "x";
  const low = Math.min(spanStart, spanEnd) - context.clearance;
  const high = Math.max(spanStart, spanEnd) + context.clearance;
  return context.obstacles
    .filter((obstacle) => obstacle[spanAxis] >= low && obstacle[spanAxis] <= high)
    .map((obstacle) => obstacle[laneAxis]);
}

/**
 * Best position for a connector leg inside the range it is allowed to occupy.
 *
 * Preference order: share as little of its length with pipes already routed as possible,
 * then stay clear of components, then land on a grid line, then stay near the middle of the
 * run. Clearance is capped, so once a lane is far enough from every component the tidier
 * grid-aligned option wins instead of the emptiest one.
 */
function chooseLane(
  low: number,
  high: number,
  preferred: number,
  context: RouteContext,
  blocked: readonly number[],
  overlapAt: (position: number) => number
): number {
  const step = Math.max(1, context.grid);
  const options: number[] = [preferred];
  const from = Number.isFinite(low) ? low : preferred - step * 8;
  const to = Number.isFinite(high) ? high : preferred + step * 8;
  const firstLine = Math.ceil((from - GEOMETRY_EPSILON) / step);
  const lastLine = Math.floor((to + GEOMETRY_EPSILON) / step);
  for (let line = firstLine; line <= lastLine && options.length < 96; line += 1) options.push(line * step);

  let best = preferred;
  let bestOverlap = Number.POSITIVE_INFINITY;
  let bestClear = Number.NEGATIVE_INFINITY;
  let bestOnGrid = false;
  let bestDrift = Number.POSITIVE_INFINITY;
  for (const option of options) {
    if (option < low - GEOMETRY_EPSILON || option > high + GEOMETRY_EPSILON) continue;
    const overlap = overlapAt(option);
    const clear = Math.min(
      context.clearance,
      blocked.reduce((closest, value) => Math.min(closest, Math.abs(option - value)), Number.POSITIVE_INFINITY)
    );
    const onGrid = Math.abs(option - Math.round(option / step) * step) <= GEOMETRY_EPSILON;
    const drift = Math.abs(option - preferred);
    const sameOverlap = Math.abs(overlap - bestOverlap) <= GEOMETRY_EPSILON;
    const sameClear = sameOverlap && Math.abs(clear - bestClear) <= GEOMETRY_EPSILON;
    const better =
      overlap < bestOverlap - GEOMETRY_EPSILON
      || (sameOverlap && clear > bestClear + GEOMETRY_EPSILON)
      || (sameClear && ((onGrid && !bestOnGrid) || (onGrid === bestOnGrid && drift < bestDrift - GEOMETRY_EPSILON)));
    if (!better) continue;
    best = option;
    bestOverlap = overlap;
    bestClear = clear;
    bestOnGrid = onGrid;
    bestDrift = drift;
  }
  return best;
}

/**
 * Position of the connector leg that joins two parallel runs, or null when there is none.
 *
 * `fromSign` and `toSign` say which side of each stub end the leg may sit on: a pipe that
 * left its port heading right can only turn at an x at or beyond that point. When the two
 * half-lines do not overlap there is no two-corner route and the caller has to detour.
 */
function connectorCoordinate(
  from: number,
  fromSign: number,
  to: number,
  toSign: number,
  context: RouteContext,
  blocked: readonly number[],
  overlapAt: (position: number) => number
): number | null {
  let low = Number.NEGATIVE_INFINITY;
  let high = Number.POSITIVE_INFINITY;
  if (fromSign > 0) low = Math.max(low, from);
  else high = Math.min(high, from);
  if (toSign > 0) low = Math.max(low, to);
  else high = Math.min(high, to);
  if (low > high + GEOMETRY_EPSILON) return null;
  const preferred = Number.isFinite(low) ? (Number.isFinite(high) ? (low + high) / 2 : low) : high;
  return chooseLane(low, high, preferred, context, blocked, overlapAt);
}

/** Lane a detour runs along: between the two escapes, or one grid cell clear when they align. */
function detourLane(
  first: number,
  second: number,
  context: RouteContext,
  blocked: readonly number[],
  overlapAt: (position: number) => number
): number {
  const step = Math.max(1, context.grid);
  if (Math.abs(first - second) <= GEOMETRY_EPSILON) {
    const clearOfBoth = (Math.round(first / step) - 1) * step;
    return chooseLane(clearOfBoth - step * 2, clearOfBoth, clearOfBoth, context, blocked, overlapAt);
  }
  const low = Math.min(first, second);
  const high = Math.max(first, second);
  return chooseLane(low, high, (first + second) / 2, context, blocked, overlapAt);
}

/**
 * Last resort when no one- or two-corner route respects both port axes: push both ends a
 * further cell clear of their components and link the escapes. Always legal, because both
 * escapes are already pointing away from whatever was in the way.
 */
function detourWaypoints(a: Vec2, exit: Vec2, b: Vec2, entry: Vec2, context: RouteContext): Vec2[] {
  const escapeA = offsetAlong(a, exit, context.detour);
  const escapeB = offsetAlong(b, entry, context.detour);
  if (runsHorizontally(exit) !== runsHorizontally(entry)) {
    return runsHorizontally(exit)
      ? [escapeA, { x: escapeA.x, y: escapeB.y }, escapeB]
      : [escapeA, { x: escapeB.x, y: escapeA.y }, escapeB];
  }
  if (runsHorizontally(exit)) {
    const lane = detourLane(
      escapeA.y,
      escapeB.y,
      context,
      blockedLaneCoordinates(context, "y", escapeA.x, escapeB.x),
      (position) =>
        overlapLength(context, [
          { axis: "vertical", position: escapeA.x, from: escapeA.y, to: position },
          { axis: "horizontal", position, from: escapeA.x, to: escapeB.x },
          { axis: "vertical", position: escapeB.x, from: position, to: escapeB.y }
        ])
    );
    return [escapeA, { x: escapeA.x, y: lane }, { x: escapeB.x, y: lane }, escapeB];
  }
  const lane = detourLane(
    escapeA.x,
    escapeB.x,
    context,
    blockedLaneCoordinates(context, "x", escapeA.y, escapeB.y),
    (position) =>
      overlapLength(context, [
        { axis: "horizontal", position: escapeA.y, from: escapeA.x, to: position },
        { axis: "vertical", position, from: escapeA.y, to: escapeB.y },
        { axis: "horizontal", position: escapeB.y, from: position, to: escapeB.x }
      ])
  );
  return [escapeA, { x: lane, y: escapeA.y }, { x: lane, y: escapeB.y }, escapeB];
}

/** Corners between the two stub ends, fewest first: none (straight), one (L), two (Z), else a detour. */
function stubWaypoints(a: Vec2, exit: Vec2, b: Vec2, entry: Vec2, context: RouteContext): Vec2[] {
  const exitHorizontal = runsHorizontally(exit);
  const entryHorizontal = runsHorizontally(entry);

  if (exitHorizontal && entryHorizontal) {
    if (Math.abs(a.y - b.y) <= GEOMETRY_EPSILON) {
      // Both ports on one line and facing each other: the run stays dead straight.
      const straight =
        (b.x - a.x) * exit.x >= -GEOMETRY_EPSILON && (a.x - b.x) * entry.x >= -GEOMETRY_EPSILON;
      return straight ? [] : detourWaypoints(a, exit, b, entry, context);
    }
    const lane = connectorCoordinate(
      a.x,
      exit.x,
      b.x,
      entry.x,
      context,
      blockedLaneCoordinates(context, "x", a.y, b.y),
      (position) =>
        overlapLength(context, [
          { axis: "horizontal", position: a.y, from: a.x, to: position },
          { axis: "vertical", position, from: a.y, to: b.y },
          { axis: "horizontal", position: b.y, from: position, to: b.x }
        ])
    );
    if (lane === null) return detourWaypoints(a, exit, b, entry, context);
    return [{ x: lane, y: a.y }, { x: lane, y: b.y }];
  }

  if (!exitHorizontal && !entryHorizontal) {
    if (Math.abs(a.x - b.x) <= GEOMETRY_EPSILON) {
      const straight =
        (b.y - a.y) * exit.y >= -GEOMETRY_EPSILON && (a.y - b.y) * entry.y >= -GEOMETRY_EPSILON;
      return straight ? [] : detourWaypoints(a, exit, b, entry, context);
    }
    const lane = connectorCoordinate(
      a.y,
      exit.y,
      b.y,
      entry.y,
      context,
      blockedLaneCoordinates(context, "y", a.x, b.x),
      (position) =>
        overlapLength(context, [
          { axis: "vertical", position: a.x, from: a.y, to: position },
          { axis: "horizontal", position, from: a.x, to: b.x },
          { axis: "vertical", position: b.x, from: position, to: b.y }
        ])
    );
    if (lane === null) return detourWaypoints(a, exit, b, entry, context);
    return [{ x: a.x, y: lane }, { x: b.x, y: lane }];
  }

  if (exitHorizontal) {
    const reachesAcross = (b.x - a.x) * exit.x >= -GEOMETRY_EPSILON;
    const arrivesFromTheRightSide = (a.y - b.y) * entry.y >= -GEOMETRY_EPSILON;
    if (reachesAcross && arrivesFromTheRightSide) return [{ x: b.x, y: a.y }];
    return detourWaypoints(a, exit, b, entry, context);
  }

  const reachesDown = (b.y - a.y) * exit.y >= -GEOMETRY_EPSILON;
  const arrivesFromTheRightSide = (a.x - b.x) * entry.x >= -GEOMETRY_EPSILON;
  if (reachesDown && arrivesFromTheRightSide) return [{ x: a.x, y: b.y }];
  return detourWaypoints(a, exit, b, entry, context);
}

/**
 * Routes one pipe as horizontal and vertical runs.
 *
 * `startDirection` and `endDirection` are the outward normals of the two ports; the route
 * always leaves and enters along them, which is what makes a component read as "wired up
 * here" rather than "a line happens to end near this circle".
 */
export function routeOrthogonalPipe(
  start: Vec2,
  startDirection: Vec2,
  end: Vec2,
  endDirection: Vec2,
  options: RouteOptions = {}
): Vec2[] {
  const stub = Math.max(0, options.stub ?? ROUTE_PORT_STUB);
  const grid = Math.max(1, options.grid ?? SCHEMATIC_GRID_SIZE);
  const context: RouteContext = {
    detour: Math.max(1, options.detour ?? ROUTE_DETOUR),
    grid,
    clearance: Math.max(0, options.clearance ?? ROUTE_CLEARANCE),
    obstacles: options.obstacles ?? [],
    occupiedLanes: options.occupiedLanes ?? [],
    // Runs closer together than half a cell read as one thick line at any usable zoom.
    laneSeparation: grid / 2
  };
  const exit = axisDirection(startDirection);
  const entry = axisDirection(endDirection);
  const a = offsetAlong(start, exit, stub);
  const b = offsetAlong(end, entry, stub);
  return simplifyPolyline([start, a, ...stubWaypoints(a, exit, b, entry, context), b, end]);
}

/** Drops repeated points and mid-run points that are not corners. */
export function simplifyPolyline(points: readonly Vec2[]): Vec2[] {
  const simplified: Vec2[] = [];
  for (const point of points) {
    const previous = simplified[simplified.length - 1];
    if (
      previous
      && Math.abs(previous.x - point.x) <= GEOMETRY_EPSILON
      && Math.abs(previous.y - point.y) <= GEOMETRY_EPSILON
    ) {
      continue;
    }
    while (simplified.length >= 2) {
      const last = simplified[simplified.length - 1];
      const beforeLast = simplified[simplified.length - 2];
      const sharedX =
        Math.abs(beforeLast.x - last.x) <= GEOMETRY_EPSILON && Math.abs(last.x - point.x) <= GEOMETRY_EPSILON;
      const sharedY =
        Math.abs(beforeLast.y - last.y) <= GEOMETRY_EPSILON && Math.abs(last.y - point.y) <= GEOMETRY_EPSILON;
      if (!sharedX && !sharedY) break;
      simplified.pop();
    }
    simplified.push({ x: point.x, y: point.y });
  }
  return simplified;
}

export function polylineSegments(points: readonly Vec2[]): Segment[] {
  const segments: Segment[] = [];
  for (let index = 1; index < points.length; index += 1) {
    segments.push({ from: points[index - 1], to: points[index] });
  }
  return segments;
}

export function polylineLength(points: readonly Vec2[]): number {
  return polylineSegments(points).reduce(
    (total, segment) => total + Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y),
    0
  );
}

/** Point a fraction `t` of the way along a polyline; used to walk flow particles down a pipe. */
export function pointOnPolyline(points: readonly Vec2[], t: number): Vec2 {
  if (points.length === 0) return { x: 0, y: 0 };
  if (points.length === 1) return { ...points[0] };
  const total = polylineLength(points);
  if (total <= GEOMETRY_EPSILON) return { ...points[0] };
  let remaining = Math.max(0, Math.min(1, t)) * total;
  for (const segment of polylineSegments(points)) {
    const length = Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y);
    if (remaining <= length || length <= GEOMETRY_EPSILON) {
      const ratio = length <= GEOMETRY_EPSILON ? 0 : remaining / length;
      return {
        x: segment.from.x + (segment.to.x - segment.from.x) * ratio,
        y: segment.from.y + (segment.to.y - segment.from.y) * ratio
      };
    }
    remaining -= length;
  }
  return { ...points[points.length - 1] };
}

/** The run a pipe's label should hang off: the one with the most free space beside it. */
export function longestPolylineSegment(points: readonly Vec2[]): Segment | null {
  let best: Segment | null = null;
  let bestLength = -1;
  for (const segment of polylineSegments(points)) {
    const length = Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y);
    if (length > bestLength) {
      bestLength = length;
      best = segment;
    }
  }
  return best;
}

function distanceToSegment(point: Vec2, segment: Segment): number {
  const dx = segment.to.x - segment.from.x;
  const dy = segment.to.y - segment.from.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared <= GEOMETRY_EPSILON) return Math.hypot(point.x - segment.from.x, point.y - segment.from.y);
  const t = Math.max(0, Math.min(1, ((point.x - segment.from.x) * dx + (point.y - segment.from.y) * dy) / lengthSquared));
  return Math.hypot(segment.from.x + dx * t - point.x, segment.from.y + dy * t - point.y);
}

/** Distance from a point to the routed pipe, so a click picks the pipe the user can see. */
export function distanceToPolyline(point: Vec2, points: readonly Vec2[]): number {
  if (points.length === 0) return Number.POSITIVE_INFINITY;
  if (points.length === 1) return Math.hypot(point.x - points[0].x, point.y - points[0].y);
  return polylineSegments(points).reduce((closest, segment) => Math.min(closest, distanceToSegment(point, segment)), Number.POSITIVE_INFINITY);
}

function segmentAxis(segment: Segment): "horizontal" | "vertical" | "point" {
  const horizontal = Math.abs(segment.from.y - segment.to.y) <= GEOMETRY_EPSILON;
  const vertical = Math.abs(segment.from.x - segment.to.x) <= GEOMETRY_EPSILON;
  if (horizontal && vertical) return "point";
  return horizontal ? "horizontal" : "vertical";
}

function crossingOf(horizontal: Segment, vertical: Segment): Vec2 | null {
  const y = horizontal.from.y;
  const x = vertical.from.x;
  const insideHorizontal =
    x > Math.min(horizontal.from.x, horizontal.to.x) + GEOMETRY_EPSILON
    && x < Math.max(horizontal.from.x, horizontal.to.x) - GEOMETRY_EPSILON;
  const insideVertical =
    y > Math.min(vertical.from.y, vertical.to.y) + GEOMETRY_EPSILON
    && y < Math.max(vertical.from.y, vertical.to.y) - GEOMETRY_EPSILON;
  return insideHorizontal && insideVertical ? { x, y } : null;
}

/**
 * Where two axis-aligned runs genuinely cross, i.e. each passes through the *interior* of
 * the other. A run that merely ends on another one is a junction, not a crossing, and is
 * deliberately excluded so the two can be drawn differently.
 */
export function segmentCrossing(first: Segment, second: Segment): Vec2 | null {
  const firstAxis = segmentAxis(first);
  const secondAxis = segmentAxis(second);
  if (firstAxis === "horizontal" && secondAxis === "vertical") return crossingOf(first, second);
  if (firstAxis === "vertical" && secondAxis === "horizontal") return crossingOf(second, first);
  return null;
}

/**
 * Every place one pipe passes over another. The hop is always reported against the
 * horizontal run, so a crossing looks the same wherever it appears and never changes shape
 * because the user happened to draw the two pipes in a different order.
 */
export function wireCrossings(routes: readonly WireRoute[]): WireCrossing[] {
  const crossings: WireCrossing[] = [];
  const segmentsByRoute = routes.map((route) => polylineSegments(route.points));
  for (let first = 0; first < routes.length; first += 1) {
    for (let second = first + 1; second < routes.length; second += 1) {
      segmentsByRoute[first].forEach((firstSegment, firstIndex) => {
        segmentsByRoute[second].forEach((secondSegment, secondIndex) => {
          const point = segmentCrossing(firstSegment, secondSegment);
          if (!point) return;
          crossings.push(
            segmentAxis(firstSegment) === "horizontal"
              ? { routeId: routes[first].id, segmentIndex: firstIndex, overRouteId: routes[second].id, point }
              : { routeId: routes[second].id, segmentIndex: secondIndex, overRouteId: routes[first].id, point }
          );
        });
      });
    }
  }
  return crossings;
}

/**
 * Points where a pipe *ends on* another pipe. These are connections, so they get a solid
 * dot rather than a hop; without the distinction a reader cannot tell a tee from a
 * crossover.
 */
export function wireJunctions(routes: readonly WireRoute[]): Vec2[] {
  const junctions: Vec2[] = [];
  const seen = new Set<string>();
  routes.forEach((route, index) => {
    const ends = [route.points[0], route.points[route.points.length - 1]].filter(Boolean);
    for (const end of ends) {
      const meetsAnother = routes.some(
        (other, otherIndex) => otherIndex !== index && distanceToPolyline(end, other.points) <= GEOMETRY_EPSILON
      );
      if (!meetsAnother) continue;
      const key = `${Math.round(end.x * 1000)}:${Math.round(end.y * 1000)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      junctions.push({ x: end.x, y: end.y });
    }
  });
  return junctions;
}

/* ------------------------------------------------------------------------------------ *
 * Label placement
 * ------------------------------------------------------------------------------------ */

export type LabelRect = { left: number; top: number; right: number; bottom: number };
export type LabelSide = "below" | "above" | "right" | "left";

export function boxesOverlap(a: LabelRect, b: LabelRect): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/**
 * Keep-out boxes for a routed pipe. A label that clears these cannot land on a wire, which
 * is the whole point: text over a run is the fastest way to make a schematic unreadable.
 */
export function polylineObstacleBoxes(points: readonly Vec2[], halfThickness: number): LabelRect[] {
  const margin = Math.max(0, halfThickness);
  return polylineSegments(points).map((segment) => ({
    left: Math.min(segment.from.x, segment.to.x) - margin,
    right: Math.max(segment.from.x, segment.to.x) + margin,
    top: Math.min(segment.from.y, segment.to.y) - margin,
    bottom: Math.max(segment.from.y, segment.to.y) + margin
  }));
}

const oppositeSide: Record<LabelSide, LabelSide> = {
  below: "above",
  above: "below",
  right: "left",
  left: "right"
};

function labelBoxOn(anchor: Vec2, width: number, height: number, side: LabelSide, offset: number): LabelRect {
  const left =
    side === "right" ? anchor.x + offset : side === "left" ? anchor.x - width - offset : anchor.x - width / 2;
  const top =
    side === "below" ? anchor.y + offset : side === "above" ? anchor.y - height - offset : anchor.y - height / 2;
  return { left, top, right: left + width, bottom: top + height };
}

/**
 * Places a label chip is allowed to take, best first: the preferred side hard against the
 * anchor, then its opposite, then the two flanks, then the same ladder pushed further out.
 * The caller walks the list and takes the first one that is clear.
 */
export function labelPlacementCandidates(
  anchor: Vec2,
  width: number,
  height: number,
  options: { preferred?: LabelSide; step?: number; rings?: number } = {}
): LabelRect[] {
  const preferred = options.preferred ?? "below";
  const step = options.step ?? 14;
  const rings = Math.max(1, options.rings ?? 4);
  const flanks = (["below", "above", "right", "left"] as LabelSide[]).filter(
    (side) => side !== preferred && side !== oppositeSide[preferred]
  );
  const sides: LabelSide[] = [preferred, oppositeSide[preferred], ...flanks];
  const candidates: LabelRect[] = [];
  for (let ring = 0; ring < rings; ring += 1) {
    for (const side of sides) candidates.push(labelBoxOn(anchor, width, height, side, ring * step));
  }
  return candidates;
}

export function firstClearBox(candidates: readonly LabelRect[], obstacles: readonly LabelRect[]): LabelRect | null {
  for (const candidate of candidates) {
    if (!obstacles.some((obstacle) => boxesOverlap(obstacle, candidate))) return candidate;
  }
  return null;
}

/* ------------------------------------------------------------------------------------ *
 * Tidy / auto-arrange
 * ------------------------------------------------------------------------------------ */

export type TidyLayoutOptions = {
  /** Horizontal gap between two layers of the network. */
  columnSpacing?: number;
  /** Vertical gap between two components in the same layer. */
  rowSpacing?: number;
  grid?: number;
  /** Left edge and vertical centre the arrangement is built around. */
  origin?: Vec2;
};

function connectedEdges(project: FluidProject): FluidEdge[] {
  return Object.values(project.edges).filter(
    (edge) => edge.from !== edge.to && project.nodes[edge.from] && project.nodes[edge.to]
  );
}

function orientation(a: Vec2, b: Vec2, c: Vec2): number {
  const value = (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y);
  if (Math.abs(value) <= GEOMETRY_EPSILON) return 0;
  return value > 0 ? 1 : -1;
}

function straightSegmentsCross(a: Vec2, b: Vec2, c: Vec2, d: Vec2): boolean {
  return (
    orientation(a, b, c) * orientation(a, b, d) < 0 && orientation(c, d, a) * orientation(c, d, b) < 0
  );
}

/**
 * How many pipes cross if every component sits at `positions` and pipes ran dead straight
 * between centres. This is the objective the tidy pass minimises; the orthogonal router
 * then follows the arrangement it produces.
 */
export function countEdgeCrossings(project: FluidProject, positions: Record<string, Vec2>): number {
  const edges = connectedEdges(project).filter((edge) => positions[edge.from] && positions[edge.to]);
  let total = 0;
  for (let first = 0; first < edges.length; first += 1) {
    for (let second = first + 1; second < edges.length; second += 1) {
      const a = edges[first];
      const b = edges[second];
      // Pipes that share a component meet there by design; that is not a crossing.
      if (a.from === b.from || a.from === b.to || a.to === b.from || a.to === b.to) continue;
      if (straightSegmentsCross(positions[a.from], positions[a.to], positions[b.from], positions[b.to])) total += 1;
    }
  }
  return total;
}

/** Current positions of every component, in the shape `countEdgeCrossings` expects. */
export function schematicNodePositions(project: FluidProject): Record<string, Vec2> {
  return Object.fromEntries(Object.values(project.nodes).map((node) => [node.id, { ...node.position }]));
}

/**
 * Arranges the network left to right, one column per hop from a source, and orders each
 * column so wires cross as little as possible.
 *
 * Layers come from a longest-path relaxation, capped at one pass per component so a
 * recirculation loop still terminates. Ordering inside a layer is the barycentre
 * heuristic: a component drifts towards the average row of whatever it is wired to, which
 * is the standard way to unpick crossed wires and is what makes the result readable.
 * Spacings are grid multiples, so every result lands on the same lattice a drag does and
 * no two components end up closer than `MIN_NODE_SEPARATION`.
 */
export function tidySchematicLayout(project: FluidProject, options: TidyLayoutOptions = {}): Record<string, Vec2> {
  const grid = Math.max(1, options.grid ?? SCHEMATIC_GRID_SIZE);
  const columnSpacing = options.columnSpacing ?? grid * 5;
  const rowSpacing = options.rowSpacing ?? grid * 3;
  const nodes = Object.values(project.nodes);
  if (nodes.length === 0) return {};
  const edges = connectedEdges(project);

  const layerOf: Record<string, number> = {};
  for (const node of nodes) layerOf[node.id] = 0;
  const maxLayer = Math.max(0, nodes.length - 1);
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const edge of edges) {
      const next = Math.min(maxLayer, layerOf[edge.from] + 1);
      if (next > layerOf[edge.to]) {
        layerOf[edge.to] = next;
        changed = true;
      }
    }
    if (!changed) break;
  }

  const upstream: Record<string, string[]> = {};
  const downstream: Record<string, string[]> = {};
  for (const node of nodes) {
    upstream[node.id] = [];
    downstream[node.id] = [];
  }
  for (const edge of edges) {
    upstream[edge.to].push(edge.from);
    downstream[edge.from].push(edge.to);
  }

  // Seed each column from where the components already are, so a tidy nudges the drawing
  // into shape instead of scrambling an arrangement the user was happy with.
  const columns = new Map<number, string[]>();
  for (const node of [...nodes].sort(
    (left, right) => left.position.y - right.position.y || left.position.x - right.position.x || left.id.localeCompare(right.id)
  )) {
    const column = columns.get(layerOf[node.id]) ?? [];
    column.push(node.id);
    columns.set(layerOf[node.id], column);
  }
  const layerKeys = [...columns.keys()].sort((left, right) => left - right);

  const rowOf: Record<string, number> = {};
  const refreshRows = () => {
    for (const key of layerKeys) (columns.get(key) ?? []).forEach((id, index) => { rowOf[id] = index; });
  };
  refreshRows();

  for (let sweep = 0; sweep < 6; sweep += 1) {
    const downwards = sweep % 2 === 0;
    const keys = downwards ? layerKeys : [...layerKeys].reverse();
    for (const key of keys) {
      const column = columns.get(key);
      if (!column || column.length < 2) continue;
      const weights = new Map<string, number>();
      column.forEach((id, index) => {
        const linked = (downwards ? upstream[id] : downstream[id]).filter((other) => rowOf[other] !== undefined);
        weights.set(id, linked.length === 0 ? index : linked.reduce((sum, other) => sum + rowOf[other], 0) / linked.length);
      });
      column.sort((left, right) => (weights.get(left) ?? 0) - (weights.get(right) ?? 0));
      refreshRows();
    }
  }

  const bounds = schematicContentBounds(project);
  const originX = options.origin?.x ?? snapToGrid({ x: bounds?.minX ?? 0, y: 0 }, grid).x;
  const centreY = options.origin?.y ?? snapToGrid({ x: 0, y: ((bounds?.minY ?? 0) + (bounds?.maxY ?? 0)) / 2 }, grid).y;

  const positions: Record<string, Vec2> = {};
  layerKeys.forEach((key, columnIndex) => {
    const column = columns.get(key) ?? [];
    column.forEach((id, rowIndex) => {
      positions[id] = snapToGrid(
        {
          x: originX + columnIndex * columnSpacing,
          y: centreY + (rowIndex - (column.length - 1) / 2) * rowSpacing
        },
        grid
      );
    });
  });
  return positions;
}

/* ------------------------------------------------------------------------------------ *
 * One model, two views
 *
 * The schematic and the 3D view are supposed to be the same network seen two ways, so the
 * route a pipe follows has to be computed once and consumed twice. Everything below is the
 * geometry the two views share: where a component's ports sit, which way they face, and the
 * ordered set of routes the whole project draws. It is all pure, so the 3D view can be held
 * to the same routes the schematic draws without either view owning the other.
 * ------------------------------------------------------------------------------------ */

/** Extra distance a port ring sits outside the component body it belongs to. */
export const SCHEMATIC_PORT_OFFSET = 10;

function toRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

/** Drawn radius of a component body, in world units. */
export function schematicNodeRadius(node: FluidNode): number {
  if (node.type === "source" || node.type === "sink") return 17;
  if (node.type === "pump") return 19;
  return 14;
}

/** Compass bearing of one port, in degrees, once the component's own rotation is applied. */
export function schematicPortAngle(node: FluidNode, port: PipePortId): number {
  const base = node.rotation ?? 0;
  if (port === "outlet") return base;
  if (port === "inlet") return base + 180;
  if (port === "north") return base - 90;
  return base + 90;
}

/** Where a pipe attaches to a component. */
export function schematicPortPosition(node: FluidNode, port: PipePortId): Vec2 {
  const radius = schematicNodeRadius(node) + SCHEMATIC_PORT_OFFSET;
  const angle = toRadians(schematicPortAngle(node, port));
  return {
    x: node.position.x + Math.cos(angle) * radius,
    y: node.position.y + Math.sin(angle) * radius
  };
}

/** Outward normal of a port: the direction a pipe has to leave along. */
export function schematicPortDirection(node: FluidNode, port: PipePortId): Vec2 {
  const angle = toRadians(schematicPortAngle(node, port));
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

/** Component centres, in the shape `routeOrthogonalPipe` wants for `obstacles`. */
export function schematicComponentCentres(nodes: Record<string, FluidNode>): Vec2[] {
  return Object.values(nodes).map((node) => node.position);
}

/** The orthogonal polyline one pipe is drawn along, in world coordinates. */
export function schematicEdgeRoute(
  edge: FluidEdge,
  nodes: Record<string, FluidNode>,
  obstacles?: readonly Vec2[],
  occupiedLanes?: readonly LaneSpan[]
): Vec2[] | null {
  const from = nodes[edge.from];
  const to = nodes[edge.to];
  if (!from || !to) return null;
  const fromPort = edge.fromPort ?? "outlet";
  const toPort = edge.toPort ?? "inlet";
  return routeOrthogonalPipe(
    schematicPortPosition(from, fromPort),
    schematicPortDirection(from, fromPort),
    schematicPortPosition(to, toPort),
    schematicPortDirection(to, toPort),
    { obstacles: obstacles ?? schematicComponentCentres(nodes), occupiedLanes: occupiedLanes ?? [] }
  );
}

/**
 * Routes every drawable pipe in the project.
 *
 * Pipes are routed one after another and each one hands its runs to the next, so a second
 * pipe picks a different lane instead of being drawn on top of the first. Edge order is the
 * project's own, so the picture is stable frame to frame - and, because this is the only
 * routing pass either view runs, a bend on the schematic is the same bend in 3D.
 */
export function buildSchematicRoutes(project: FluidProject): WireRoute[] {
  const obstacles = schematicComponentCentres(project.nodes);
  const occupiedLanes: LaneSpan[] = [];
  const routes: WireRoute[] = [];
  for (const edge of Object.values(project.edges)) {
    const points = schematicEdgeRoute(edge, project.nodes, obstacles, occupiedLanes);
    if (!points || points.length < 2) continue;
    occupiedLanes.push(...routeLaneSpans(points));
    routes.push({ id: edge.id, points });
  }
  return routes;
}

/** Unit direction of travel a fraction `t` along a polyline; pairs with `pointOnPolyline`. */
export function tangentOnPolyline(points: readonly Vec2[], t: number): Vec2 {
  const segments = polylineSegments(points).filter(
    (segment) => Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y) > GEOMETRY_EPSILON
  );
  if (segments.length === 0) return { x: 1, y: 0 };
  const total = segments.reduce(
    (sum, segment) => sum + Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y),
    0
  );
  let remaining = Math.max(0, Math.min(1, t)) * total;
  for (const segment of segments) {
    const length = Math.hypot(segment.to.x - segment.from.x, segment.to.y - segment.from.y);
    if (remaining <= length) {
      return { x: (segment.to.x - segment.from.x) / length, y: (segment.to.y - segment.from.y) / length };
    }
    remaining -= length;
  }
  const last = segments[segments.length - 1];
  const length = Math.hypot(last.to.x - last.from.x, last.to.y - last.from.y);
  return { x: (last.to.x - last.from.x) / length, y: (last.to.y - last.from.y) / length };
}

/**
 * Replaces every corner of a polyline with a circular fillet of the given radius.
 *
 * A pipe has to turn through a bend, not a knife edge, so the swept 3D geometry is built on
 * this rather than on the raw route. The radius is capped at half of either adjoining run,
 * which is what stops two corners on a short run from eating into each other and inverting
 * the geometry between them. The straight runs either side are untouched, so the rounded
 * path still visits the same lanes the schematic drew.
 */
export function roundPolylineCorners(points: readonly Vec2[], radius: number, segments = 4): Vec2[] {
  const source = simplifyPolyline(points);
  if (source.length < 3 || radius <= GEOMETRY_EPSILON) return source;
  const steps = Math.max(1, Math.round(segments));
  const rounded: Vec2[] = [{ ...source[0] }];
  for (let index = 1; index < source.length - 1; index += 1) {
    const previous = source[index - 1];
    const corner = source[index];
    const next = source[index + 1];
    const inLength = Math.hypot(corner.x - previous.x, corner.y - previous.y);
    const outLength = Math.hypot(next.x - corner.x, next.y - corner.y);
    const tangentLength = Math.min(radius, inLength / 2, outLength / 2);
    if (tangentLength <= GEOMETRY_EPSILON) {
      rounded.push({ ...corner });
      continue;
    }
    const into = { x: (corner.x - previous.x) / inLength, y: (corner.y - previous.y) / inLength };
    const outOf = { x: (next.x - corner.x) / outLength, y: (next.y - corner.y) / outLength };
    const turn = into.x * outOf.y - into.y * outOf.x;
    if (Math.abs(turn) <= GEOMETRY_EPSILON) {
      // Doubling straight back on itself has no inside to turn towards.
      rounded.push({ ...corner });
      continue;
    }
    const start = { x: corner.x - into.x * tangentLength, y: corner.y - into.y * tangentLength };
    const sweep = Math.atan2(turn, into.x * outOf.x + into.y * outOf.y);
    const arcRadius = tangentLength / Math.tan(Math.abs(sweep) / 2);
    const side = Math.sign(turn);
    const centre = {
      x: start.x - into.y * arcRadius * side,
      y: start.y + into.x * arcRadius * side
    };
    const startAngle = Math.atan2(start.y - centre.y, start.x - centre.x);
    for (let step = 0; step <= steps; step += 1) {
      const angle = startAngle + (sweep * step) / steps;
      rounded.push({ x: centre.x + Math.cos(angle) * arcRadius, y: centre.y + Math.sin(angle) * arcRadius });
    }
  }
  rounded.push({ ...source[source.length - 1] });
  return rounded;
}

/** Nearest quarter turn in whole degrees, so every port lands on a horizontal or vertical axis. */
function quarterTurn(radians: number): number {
  const snapped = Math.round((radians * 180) / Math.PI / 90) * 90;
  if (snapped === 0) return 0;
  return snapped === -180 ? 180 : snapped;
}

function averagePoint(points: readonly Vec2[]): Vec2 {
  return {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length
  };
}

/**
 * Aim for every component: outlet towards what it feeds, so a port ring sits on the side
 * of the body the pipe actually leaves from.
 *
 * A component with nothing downstream is turned away from what feeds it, which puts its
 * inlet on the upstream side. Angles are quarter turns because the router only draws
 * horizontal and vertical runs, and a port aimed at 40 degrees would otherwise put its
 * ring somewhere no run can reach cleanly.
 */
export function tidySchematicRotations(
  project: FluidProject,
  positions: Record<string, Vec2> = schematicNodePositions(project)
): Record<string, number> {
  const edges = connectedEdges(project);
  const rotations: Record<string, number> = {};
  for (const node of Object.values(project.nodes)) {
    const here = positions[node.id] ?? node.position;
    const downstream = edges.filter((edge) => edge.from === node.id).map((edge) => positions[edge.to]).filter(Boolean);
    const upstream = edges.filter((edge) => edge.to === node.id).map((edge) => positions[edge.from]).filter(Boolean);
    if (downstream.length > 0) {
      const target = averagePoint(downstream);
      rotations[node.id] = quarterTurn(Math.atan2(target.y - here.y, target.x - here.x));
    } else if (upstream.length > 0) {
      const source = averagePoint(upstream);
      rotations[node.id] = quarterTurn(Math.atan2(here.y - source.y, here.x - source.x));
    } else {
      rotations[node.id] = Math.round(node.rotation ?? 0);
    }
  }
  return rotations;
}
