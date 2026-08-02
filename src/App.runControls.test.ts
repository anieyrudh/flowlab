import { describe, expect, it } from "vitest";
import { describeSweep, formatElapsed, runProgressModel } from "./App";
import { channelPreset, laminarStarterPreset, mixerPreset, pipeLossPreset, presets, venturiPreset } from "./data/presets";
import type { JobRecord } from "./types";

function job(patch: Partial<JobRecord> = {}): JobRecord {
  return {
    id: "job-1",
    caseId: "case-1",
    solver: "openfoam",
    status: "running",
    createdAt: "2026-06-11T00:00:00Z",
    updatedAt: "2026-06-11T00:00:30Z",
    execution: "native",
    command: ["bash", "Allrun"],
    logs: [],
    evidenceCapability: { status: "experimental" },
    ...patch
  } as JobRecord;
}

describe("sweep labelling", () => {
  it("names the parameter the laminar starter actually sweeps and gives its values a unit", () => {
    const description = describeSweep(laminarStarterPreset, laminarStarterPreset.sweeps[0]);

    // The header used to read "Sweep: inlet flow rate" for every preset. These
    // six values are pipe diameters in metres.
    expect(description.title).toBe("Sweep: diameter (mm)");
    expect(description.parameterLabel).toBe("diameter");
    expect(description.formatValue(0.012)).toBe("12 mm");
    expect(description.formatValue(0.032)).toBe("32 mm");
    expect(description.targetLabel).toBe("Straight tube 20 mm");
    expect(description.rangeLabel).toBe("12 mm to 32 mm in 6 steps");
    expect(description.targetMissing).toBe(false);
  });

  it("names the venturi throat sweep and keeps one unit across the whole range", () => {
    const description = describeSweep(venturiPreset, venturiPreset.sweeps[0]);

    expect(description.title).toBe("Sweep: throat diameter (m)");
    expect(description.formatValue(0.045)).toBe("0.045 m");
    expect(description.formatValue(0.14)).toBe("0.14 m");
    expect(description.targetLabel).toBe("Converging Venturi");
  });

  it("names the channel height sweep", () => {
    const description = describeSweep(channelPreset, channelPreset.sweeps[0]);

    expect(description.title).toBe("Sweep: height (m)");
    expect(description.targetLabel).toBe("Rectangular channel");
  });

  it("reports a project with no sweep instead of naming a parameter that is not there", () => {
    const description = describeSweep(laminarStarterPreset, undefined);

    expect(description.configured).toBe(false);
    expect(description.title).toBe("Sweep: none configured");
    expect(description.rangeLabel).toBeNull();
  });

  it("flags a sweep whose target is not in the project", () => {
    const description = describeSweep(mixerPreset, {
      id: "stale",
      targetKind: "edge",
      targetId: "not-an-edge",
      parameter: "diameter",
      min: 0.1,
      max: 0.2,
      steps: 3
    });

    expect(description.targetMissing).toBe(true);
  });

  it("gives every shipped preset a sweep that points at a component it owns", () => {
    for (const preset of presets) {
      for (const sweep of preset.sweeps) {
        const description = describeSweep(preset, sweep);
        expect(description.targetMissing, `${preset.name} sweep ${sweep.id}`).toBe(false);
      }
    }
  });

  it("keeps the pipe head-loss sweep id honest about its parameter", () => {
    expect(pipeLossPreset.sweeps[0].parameter).toBe("diameter");
    expect(pipeLossPreset.sweeps[0].id).toContain("diameter");
  });
});

describe("run progress", () => {
  it("formats elapsed time without inventing precision", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(45_000)).toBe("45s");
    expect(formatElapsed(125_000)).toBe("2m 5s");
    expect(formatElapsed(7_600_000)).toBe("2h 6m");
  });

  it("reports state and elapsed time for a run still in flight", () => {
    const model = runProgressModel(job(), Date.parse("2026-06-11T00:02:10Z"));

    expect(model?.stateLabel).toBe("Running");
    expect(model?.terminal).toBe(false);
    expect(model?.tone).toBe("running");
    expect(model?.elapsedLabel).toBe("2m 10s");
  });

  it("shows the OpenFOAM simulation time when the log parser has one", () => {
    const model = runProgressModel(
      job({ result: { logSummary: { solver: "openfoam", lineCount: 5, lastLines: [], latestTime: 0.002 } } }),
      Date.parse("2026-06-11T00:00:05Z")
    );

    expect(model?.advanceLabel).toBe("Time 0.002");
  });

  it("shows an iteration count for solvers that report one", () => {
    const model = runProgressModel(
      job({ solver: "su2", result: { logSummary: { solver: "su2", lineCount: 9, lastLines: [], latestIteration: 120 } } }),
      Date.parse("2026-06-11T00:00:05Z")
    );

    expect(model?.advanceLabel).toBe("Iteration 120");
  });

  it("falls back to log lines rather than inventing progress", () => {
    const model = runProgressModel(
      job({ result: { logSummary: { solver: "openfoam", lineCount: 3, lastLines: [] } } }),
      Date.parse("2026-06-11T00:00:05Z")
    );

    expect(model?.advanceLabel).toBe("3 log lines");
  });

  it("freezes elapsed time at the finish and marks the terminal state", () => {
    const model = runProgressModel(
      job({ status: "complete", finishedAt: "2026-06-11T00:01:00Z", exitCode: 0 }),
      Date.parse("2026-06-11T09:00:00Z")
    );

    expect(model?.terminal).toBe(true);
    expect(model?.stateLabel).toBe("Complete");
    expect(model?.tone).toBe("done");
    expect(model?.elapsedLabel).toBe("1m 0s");
    expect(model?.detail).toBe("exit 0");
  });

  it("surfaces the failure reason on a failed run", () => {
    const model = runProgressModel(
      job({ status: "failed", finishedAt: "2026-06-11T00:00:40Z", error: "Solver exited with code 1." }),
      Date.parse("2026-06-11T00:00:40Z")
    );

    expect(model?.tone).toBe("failed");
    expect(model?.detail).toBe("Solver exited with code 1.");
  });

  it("reports nothing when there is no run", () => {
    expect(runProgressModel(null, Date.now())).toBeNull();
  });
});
