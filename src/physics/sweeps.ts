import type { FluidProject, SimulationResult, SweepConfig } from "../types";
import { solveHydraulicNetwork } from "./hydraulics";

export type SweepRun = {
  index: number;
  value: number;
  result: SimulationResult;
};

function cloneProject(project: FluidProject): FluidProject {
  return structuredClone(project);
}

export function runSweep(project: FluidProject, sweep: SweepConfig): SweepRun[] {
  const runs: SweepRun[] = [];
  const steps = Math.max(2, sweep.steps);
  for (let i = 0; i < steps; i += 1) {
    const value = sweep.min + ((sweep.max - sweep.min) * i) / (steps - 1);
    const next = cloneProject(project);
    if (sweep.targetKind === "edge") {
      const edge = next.edges[sweep.targetId];
      if (edge) {
        if (sweep.parameter === "diameter" && edge.shape.kind === "circular") {
          edge.shape.diameter = value;
        } else if (sweep.parameter === "height" && edge.shape.kind === "rectangular") {
          edge.shape.height = value;
        } else if (sweep.parameter === "width" && edge.shape.kind === "rectangular") {
          edge.shape.width = value;
        } else if (sweep.parameter === "length") {
          edge.length = value;
        } else if (sweep.parameter === "minorLossK") {
          edge.minorLossK = value;
        } else if (sweep.parameter === "throatDiameter") {
          edge.throatDiameter = value;
        }
      }
    }
    if (sweep.targetKind === "node") {
      const node = next.nodes[sweep.targetId];
      if (node && sweep.parameter === "pressure") node.pressure = value;
      if (node && sweep.parameter === "head") node.head = value;
      if (node && sweep.parameter === "flowDemand") node.flowDemand = value;
    }
    if (sweep.targetKind === "fluid") {
      if (sweep.parameter === "dynamicViscosity") next.fluid.dynamicViscosity = value;
      if (sweep.parameter === "density") next.fluid.density = value;
      if (sweep.parameter === "temperature") next.fluid.temperature = value;
    }
    runs.push({ index: i, value, result: solveHydraulicNetwork(next) });
  }
  return runs;
}
