/**
 * Extremes folded in a single pass.
 *
 * Spreading an array into `Math.min`/`Math.max` passes one argument per
 * element, so it throws `RangeError: Maximum call stack size exceeded` past a
 * ceiling that is engine- and stack-dependent: measured between 100k and 125k
 * on Node 24, and as low as ~65k on other engines. Solver output clears that
 * easily, so anything sized by a mesh — points, cells, or one value per point
 * — has to fold instead of spread.
 *
 * Every helper folds with the two-argument `Math.min`/`Math.max` rather than
 * `<`/`>` comparisons, which keeps NaN propagation and signed zero identical
 * to the spread each one replaced. A comparison would differ on both counts:
 * `NaN < x` is false, so a NaN the spread propagated would instead be skipped.
 *
 * The `seed` arguments stand in for the extra arguments a spread carried, so
 * `Math.max(...values, 1e-9)` becomes `maxAbsoluteOf(values, 1e-9)` and an
 * empty input still returns the floor without a special case. Left at their
 * defaults the seeds are what argument-less `Math.min()`/`Math.max()` return,
 * so empty-input behaviour is preserved for free.
 */

export function minimumOf(values: readonly number[], seed = Number.POSITIVE_INFINITY): number {
  let minimum = seed;
  for (let index = 0; index < values.length; index += 1) {
    minimum = Math.min(minimum, values[index]);
  }
  return minimum;
}

export function maximumOf(values: readonly number[], seed = Number.NEGATIVE_INFINITY): number {
  let maximum = seed;
  for (let index = 0; index < values.length; index += 1) {
    maximum = Math.max(maximum, values[index]);
  }
  return maximum;
}

/** Largest magnitude, for fields that are signed but scaled by their extent. */
export function maxAbsoluteOf(values: readonly number[], seed = Number.NEGATIVE_INFINITY): number {
  let maximum = seed;
  for (let index = 0; index < values.length; index += 1) {
    maximum = Math.max(maximum, Math.abs(values[index]));
  }
  return maximum;
}

export type PointExtent = {
  min: [number, number, number];
  max: [number, number, number];
};

/**
 * Per-axis extent of a point cloud, in one pass rather than one per axis.
 *
 * The callers this replaced walked the points up to six times over — once per
 * axis, per bound — building a full copy each time. A mesh is the largest
 * array in the app, so that is worth doing once.
 */
export function pointExtent(points: readonly [number, number, number][]): PointExtent {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let minZ = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  let maxZ = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < points.length; index += 1) {
    const point = points[index];
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minY = Math.min(minY, point[1]);
    maxY = Math.max(maxY, point[1]);
    minZ = Math.min(minZ, point[2]);
    maxZ = Math.max(maxZ, point[2]);
  }
  return { min: [minX, minY, minZ], max: [maxX, maxY, maxZ] };
}

/** Per-axis width of a point cloud. Empty input gives -Infinity, as the spread did. */
export function pointSpans(points: readonly [number, number, number][]): [number, number, number] {
  const { min, max } = pointExtent(points);
  return [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
}
