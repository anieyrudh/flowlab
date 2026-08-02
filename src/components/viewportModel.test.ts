import { describe, expect, it } from "vitest";
import { venturiPreset } from "../data/presets";
import {
  MAX_FIT_ZOOM,
  MIN_NODE_SEPARATION,
  SCHEMATIC_GRID_SIZE,
  clampViewportZoom,
  defaultSchematicViewport,
  fitSchematicViewport,
  isCellFree,
  panSchematicViewport,
  resetSchematicViewport,
  screenToWorld,
  snapNodeToFreeCell,
  snapToGrid,
  visibleGridRange,
  worldToScreen,
  zoomViewportAtPoint
} from "./viewportModel";

describe("viewport model", () => {
  it("keeps the point under the cursor stable while zooming", () => {
    const viewport = { pan: { x: 40, y: 20 }, zoom: 1 };
    const cursor = { x: 320, y: 220 };
    const worldBefore = screenToWorld(cursor, viewport);
    const next = zoomViewportAtPoint(viewport, cursor, -240);
    const worldAfter = screenToWorld(cursor, next);
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 8);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 8);
    expect(next.zoom).toBeGreaterThan(1);
  });

  it("clamps zoom and preserves pan deltas", () => {
    expect(clampViewportZoom(99)).toBe(4);
    expect(clampViewportZoom(0.01)).toBe(0.25);
    expect(panSchematicViewport(defaultSchematicViewport, { x: 12, y: -8 })).toEqual({ pan: { x: 12, y: -8 }, zoom: 1 });
  });

  it("fits a constrained network and resets to the canonical view", () => {
    const fit = fitSchematicViewport(venturiPreset, 420, 300);
    expect(fit.zoom).toBeLessThan(1);
    expect(fit.pan.x).not.toBe(0);
    expect(resetSchematicViewport()).toEqual({ pan: { x: 0, y: 0 }, zoom: 1 });
  });

  it("round trips screen and world coordinates", () => {
    const viewport = { pan: { x: 80, y: -10 }, zoom: 0.75 };
    const world = { x: 420, y: 180 };
    expect(screenToWorld(worldToScreen(world, viewport), viewport)).toEqual(world);
  });

  it("keeps the fitted drawing clear of the screen regions controls occupy", () => {
    const insets = { top: 20, right: 60, bottom: 80, left: 40 };
    const fit = fitSchematicViewport(venturiPreset, 900, 600, { insets, padding: 0 });
    const bounds = Object.values(venturiPreset.nodes).map((node) => worldToScreen(node.position, fit));
    for (const point of bounds) {
      expect(point.x).toBeGreaterThanOrEqual(insets.left);
      expect(point.x).toBeLessThanOrEqual(900 - insets.right);
      expect(point.y).toBeGreaterThanOrEqual(insets.top);
      expect(point.y).toBeLessThanOrEqual(600 - insets.bottom);
    }
  });

  it("does not magnify a small network past the fit ceiling", () => {
    const fit = fitSchematicViewport(venturiPreset, 6000, 4000);
    expect(fit.zoom).toBeLessThanOrEqual(MAX_FIT_ZOOM);
  });

  it("snaps a dropped component to the grid and to a free cell when the target is taken", () => {
    expect(snapToGrid({ x: 137, y: -22 })).toEqual({ x: 120, y: -40 });

    const free = snapNodeToFreeCell(venturiPreset, "source", { x: 137, y: 501 });
    expect(free).toEqual(snapToGrid({ x: 137, y: 501 }));
    expect(free.x % SCHEMATIC_GRID_SIZE).toBe(0);
    expect(free.y % SCHEMATIC_GRID_SIZE).toBe(0);

    // The throat sits at (420, 260); dropping on top of it must land somewhere else.
    const deflected = snapNodeToFreeCell(venturiPreset, "source", { x: 420, y: 260 });
    expect(isCellFree(venturiPreset, "source", deflected)).toBe(true);
    expect(deflected.x % SCHEMATIC_GRID_SIZE).toBe(0);
    expect(deflected.y % SCHEMATIC_GRID_SIZE).toBe(0);
    for (const node of Object.values(venturiPreset.nodes)) {
      if (node.id === "source") continue;
      expect(Math.hypot(node.position.x - deflected.x, node.position.y - deflected.y)).toBeGreaterThanOrEqual(
        MIN_NODE_SEPARATION
      );
    }
  });

  it("reports a grid range that covers the whole visible canvas", () => {
    const viewport = { pan: { x: -120, y: 40 }, zoom: 0.6 };
    const range = visibleGridRange(viewport, 800, 500);
    const topLeft = screenToWorld({ x: 0, y: 0 }, viewport);
    const bottomRight = screenToWorld({ x: 800, y: 500 }, viewport);
    expect(range.step).toBe(SCHEMATIC_GRID_SIZE);
    expect(range.startX).toBeLessThanOrEqual(topLeft.x);
    expect(range.endX).toBeGreaterThanOrEqual(bottomRight.x);
    expect(range.startY).toBeLessThanOrEqual(topLeft.y);
    expect(range.endY).toBeGreaterThanOrEqual(bottomRight.y);
  });
});
