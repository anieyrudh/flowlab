export type EditorPerfMetric = "pointer-update" | "hydraulic-recalc" | "cinema-build" | "cinema-frame" | "schematic-frame";

export type EditorPerfSnapshot = {
  counts: Record<EditorPerfMetric, number>;
  p50: Record<EditorPerfMetric, number>;
  p95: Record<EditorPerfMetric, number>;
  max: Record<EditorPerfMetric, number>;
  droppedFrames: number;
};

const metrics: Record<EditorPerfMetric, number[]> = {
  "pointer-update": [],
  "hydraulic-recalc": [],
  "cinema-build": [],
  "cinema-frame": [],
  "schematic-frame": []
};

let droppedFrames = 0;
let lastFrameAt = 0;

function percentile(values: number[], fraction: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] ?? 0;
}

export function recordEditorMetric(metric: EditorPerfMetric, durationMs: number) {
  if (!Number.isFinite(durationMs)) return;
  metrics[metric].push(durationMs);
  if (metrics[metric].length > 2_000) metrics[metric].shift();
}

export function recordEditorFrame(metric: "cinema-frame" | "schematic-frame", now = performance.now()) {
  // Treat a sustained scheduler stall as a dropped frame. Headless desktop
  // browsers can legitimately cadence WebGL rAF at ~30 Hz; counting every
  // 33 ms interval would report a false regression when render work is < 1 ms.
  if (lastFrameAt > 0 && now - lastFrameAt > 50) droppedFrames += 1;
  lastFrameAt = now;
  void metric;
}

export function measureEditorMetric<T>(metric: EditorPerfMetric, operation: () => T): T {
  const start = performance.now();
  try {
    return operation();
  } finally {
    recordEditorMetric(metric, performance.now() - start);
  }
}

export function resetEditorPerformance() {
  for (const values of Object.values(metrics)) values.length = 0;
  droppedFrames = 0;
  lastFrameAt = 0;
}

export function getEditorPerformanceSnapshot(): EditorPerfSnapshot {
  return {
    counts: Object.fromEntries(Object.entries(metrics).map(([metric, values]) => [metric, values.length])) as EditorPerfSnapshot["counts"],
    p50: Object.fromEntries(Object.entries(metrics).map(([metric, values]) => [metric, percentile(values, 0.5)])) as EditorPerfSnapshot["p50"],
    p95: Object.fromEntries(Object.entries(metrics).map(([metric, values]) => [metric, percentile(values, 0.95)])) as EditorPerfSnapshot["p95"],
    max: Object.fromEntries(Object.entries(metrics).map(([metric, values]) => [metric, Math.max(...values, 0)])) as EditorPerfSnapshot["max"],
    droppedFrames
  };
}

if (typeof window !== "undefined") {
  window.__flowlabEditorPerformance = {
    get: getEditorPerformanceSnapshot,
    reset: resetEditorPerformance
  };
}

declare global {
  interface Window {
    __flowlabEditorPerformance?: {
      get: () => EditorPerfSnapshot;
      reset: () => void;
    };
  }
}
