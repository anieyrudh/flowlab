import { describe, expect, it } from "vitest";
import { maxAbsoluteOf, maximumOf, minimumOf, pointExtent, pointSpans } from "./numeric";

/**
 * Past the argument ceiling a spread hits. The ceiling is engine- and
 * stack-dependent — between 100k and 125k on Node 24 — so the fixtures are
 * sized for margin rather than to sit exactly on it.
 */
const OVERSIZED = 200_000;

/** Extremes planted mid-array, so a fold has to carry them, not read the ends. */
function oversizedValues(): number[] {
  const values: number[] = new Array(OVERSIZED);
  for (let index = 0; index < OVERSIZED; index += 1) values[index] = index % 37;
  values[Math.floor(OVERSIZED / 3)] = -913;
  values[Math.floor((OVERSIZED * 2) / 3)] = 471;
  return values;
}

function oversizedPoints(): [number, number, number][] {
  const points: [number, number, number][] = new Array(OVERSIZED);
  for (let index = 0; index < OVERSIZED; index += 1) {
    points[index] = [(index % 97) / 97, (index % 89) / 89, (index % 83) / 83];
  }
  points[Math.floor(OVERSIZED / 3)] = [-2, -7, -4];
  points[Math.floor((OVERSIZED * 2) / 3)] = [5, 3, 11];
  return points;
}

describe("Folded extremes", () => {
  it("scans an array longer than a spread can pass as arguments", () => {
    const values = oversizedValues();

    expect(() => minimumOf(values)).not.toThrow();
    expect(minimumOf(values)).toBe(-913);
    expect(maximumOf(values)).toBe(471);
    expect(maxAbsoluteOf(values)).toBe(913);
  });

  it("measures a solver-sized point cloud without overflowing the stack", () => {
    const points = oversizedPoints();

    expect(() => pointExtent(points)).not.toThrow();
    expect(pointExtent(points).min).toEqual([-2, -7, -4]);
    expect(pointExtent(points).max).toEqual([5, 3, 11]);
    expect(pointSpans(points)).toEqual([7, 10, 15]);
  });

  it.each([
    { name: "mixed signs", values: [4, -9, 2] },
    { name: "a single value", values: [3] },
    { name: "an empty array", values: [] as number[] },
    { name: "equal values", values: [5, 5, 5] },
    { name: "infinities", values: [Infinity, -Infinity, 0] }
  ])("returns exactly what the spread returned for $name", ({ values }) => {
    expect(minimumOf(values)).toBe(Math.min(...values));
    expect(maximumOf(values)).toBe(Math.max(...values));
    expect(maxAbsoluteOf(values)).toBe(Math.max(...values.map((value) => Math.abs(value))));
  });

  it("treats the seed as the extra argument the spread carried", () => {
    expect(minimumOf([4, 9], 0)).toBe(Math.min(...[4, 9], 0));
    expect(maximumOf([4, 9], 100)).toBe(Math.max(...[4, 9], 100));
    expect(maxAbsoluteOf([1e-12], 1e-9)).toBe(Math.max(...[1e-12].map(Math.abs), 1e-9));
    // Which is what makes the empty case fall out without a special case.
    expect(minimumOf([], 0)).toBe(0);
    expect(maxAbsoluteOf([], 1e-9)).toBe(1e-9);
  });

  it("propagates NaN rather than skipping it, as the spread did", () => {
    // A `<`-based fold would return 1 here: NaN fails every comparison. That
    // divergence is the reason these fold with two-argument Math.min/Math.max.
    expect(minimumOf([1, NaN, 3])).toBeNaN();
    expect(maximumOf([1, NaN, 3])).toBeNaN();
    expect(maxAbsoluteOf([1, NaN, 3])).toBeNaN();
    expect(pointExtent([[1, 1, 1], [NaN, 2, 2]]).min[0]).toBeNaN();
  });

  it("keeps signed zero, as the spread did", () => {
    // `-0 < 0` is false, so a comparison-based fold would return +0 instead.
    expect(Object.is(minimumOf([-0, 0]), -0)).toBe(true);
    expect(Object.is(maximumOf([0, -0]), 0)).toBe(true);
  });

  it("reports an empty point cloud the way the spread did", () => {
    const extent = pointExtent([]);

    expect(extent.min).toEqual([Infinity, Infinity, Infinity]);
    expect(extent.max).toEqual([-Infinity, -Infinity, -Infinity]);
    // Callers floor this (span, EPSILON), so -Infinity has to stay -Infinity.
    expect(pointSpans([])).toEqual([-Infinity, -Infinity, -Infinity]);
  });

  it.each([
    { name: "a unit cube", points: [[0, 0, 0], [1, 1, 1]] as [number, number, number][] },
    { name: "a single point", points: [[3, -4, 5]] as [number, number, number][] },
    { name: "a flat sheet", points: [[0, 0, 2], [4, 6, 2]] as [number, number, number][] },
    { name: "negative coordinates", points: [[-3.5, 10, -0.25], [1.5, -2, 8.75]] as [number, number, number][] }
  ])("matches a per-axis spread for $name", ({ points }) => {
    const axes = [0, 1, 2] as const;

    expect(pointExtent(points).min).toEqual(axes.map((axis) => Math.min(...points.map((point) => point[axis]))));
    expect(pointExtent(points).max).toEqual(axes.map((axis) => Math.max(...points.map((point) => point[axis]))));
  });
});
