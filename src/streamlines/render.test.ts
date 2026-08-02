import { describe, expect, it } from "vitest";
import { passiveSpriteLayout, passiveSpritePositions, streamlineFieldExtent } from "./render";
import { STREAMLINE_LIMITS, type StreamlineResult } from "./types";

function result(lineCount = 64, verticesPerLine = 32): StreamlineResult {
  return {
    schema: "flowlab.steady_streamlines.v1",
    terminology: "steady-streamline",
    sourceName: "fixture.vtk",
    sourceIdentity: "artifact-local-unlinked",
    spatialDimension: 3,
    velocityField: "U",
    velocityLocation: "point",
    velocityInterpolation: "barycentric point field",
    fieldInterpolations: {
      velocity: "barycentric point field",
      pressure: "barycentric point field",
      temperature: "barycentric point field",
      phase: "barycentric point field",
      vorticity: "barycentric point field"
    },
    lines: Array.from({ length: lineCount }, (_line, seedIndex) => ({
      seedIndex,
      terminationReason: "max-vertices",
      vertices: Array.from({ length: verticesPerLine }, (_vertex, index) => ({
        position: [index / verticesPerLine, seedIndex / lineCount, 0],
        velocity: [1, 0, 0],
        speed: 1,
        fields: { velocity: index, pressure: seedIndex, temperature: 300, phase: 0.5, vorticity: 2 },
        provenance: {
          renderedCellId: 0,
          sourceCellId: 0,
          pointIds: [0, 1, 2, 3],
          weights: [0.25, 0.25, 0.25, 0.25],
          method: "point-barycentric-tetra-decomposition"
        },
        terminationReason: index === verticesPerLine - 1 ? "max-vertices" : "active"
      }))
    })),
    seedCount: lineCount,
    vertexCount: lineCount * verticesPerLine,
    limits: STREAMLINE_LIMITS
  };
}

describe("passive streamline rendering", () => {
  it("caps passive sprites and keeps reduced-motion positions fixed", () => {
    const layout = passiveSpriteLayout(result(300, 4));
    expect(layout).toHaveLength(256);
    expect(passiveSpritePositions(layout, 0, true)).toEqual(passiveSpritePositions(layout, 10, true));
    expect(passiveSpritePositions(layout, 0, false)).not.toEqual(passiveSpritePositions(layout, 10, false));
  });

  it("supports velocity, pressure, temperature, phase, and explicit vorticity colouring", () => {
    const streamlines = result();
    expect(streamlineFieldExtent(streamlines, "velocity")).toEqual({ min: 0, max: 31 });
    expect(streamlineFieldExtent(streamlines, "pressure")).toEqual({ min: 0, max: 63 });
    expect(streamlineFieldExtent(streamlines, "temperature")).toEqual({ min: 300, max: 300 });
    expect(streamlineFieldExtent(streamlines, "phase")).toEqual({ min: 0.5, max: 0.5 });
    expect(streamlineFieldExtent(streamlines, "vorticity")).toEqual({ min: 2, max: 2 });
  });

  it("keeps 256-sprite position updates well below the 16 ms frame budget", () => {
    const layout = passiveSpriteLayout(result(256, 1_024));
    const samples: number[] = [];
    for (let iteration = 0; iteration < 200; iteration += 1) {
      const started = performance.now();
      passiveSpritePositions(layout, iteration / 60, false);
      samples.push(performance.now() - started);
    }
    samples.sort((left, right) => left - right);
    const p95 = samples[Math.floor(samples.length * 0.95)];
    expect(p95).toBeLessThan(16);
  });
});
