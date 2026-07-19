import type { FluidProject, Vec2 } from "../types";

export const MIN_VIEWPORT_ZOOM = 0.25;
export const MAX_VIEWPORT_ZOOM = 4;
export const DEFAULT_VIEWPORT_ZOOM = 1;

export type SchematicViewport = {
  pan: Vec2;
  zoom: number;
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

export function fitSchematicViewport(project: FluidProject, width: number, height: number): SchematicViewport {
  const nodes = Object.values(project.nodes);
  if (nodes.length === 0) return resetSchematicViewport();
  const padding = 96;
  const minX = Math.min(...nodes.map((node) => node.position.x)) - padding;
  const maxX = Math.max(...nodes.map((node) => node.position.x)) + padding;
  const minY = Math.min(...nodes.map((node) => node.position.y)) - padding;
  const maxY = Math.max(...nodes.map((node) => node.position.y)) + padding;
  const worldWidth = Math.max(1, maxX - minX);
  const worldHeight = Math.max(1, maxY - minY);
  const rawZoom = Math.min((width - 48) / worldWidth, (height - 48) / worldHeight);
  if (rawZoom >= 1) return resetSchematicViewport();
  const zoom = clampViewportZoom(rawZoom);
  return {
    zoom,
    pan: {
      x: width / 2 - ((minX + maxX) / 2) * zoom,
      y: height / 2 - ((minY + maxY) / 2) * zoom
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

export function resetCinemaCamera(): CinemaCameraState {
  return { ...defaultCinemaCamera, pan: { ...defaultCinemaCamera.pan } };
}
