import { describe, expect, it } from "vitest";
import { venturiPreset } from "../data/presets";
import {
  clampViewportZoom,
  defaultSchematicViewport,
  fitSchematicViewport,
  panSchematicViewport,
  resetSchematicViewport,
  screenToWorld,
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
});
