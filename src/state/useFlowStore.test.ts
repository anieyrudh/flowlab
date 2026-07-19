import { beforeEach, describe, expect, it } from "vitest";
import { venturiPreset } from "../data/presets";
import { useFlowStore } from "./useFlowStore";

describe("FlowLab editor endpoint validation", () => {
  beforeEach(() => {
    useFlowStore.getState().setProject(venturiPreset);
  });

  it("refuses self-loops after endpoint editing", () => {
    useFlowStore.getState().updateEdgeEndpoint("inlet", "to", "source", "inlet");

    expect(useFlowStore.getState().project.edges.inlet).toMatchObject({ from: "source", to: "throat" });
  });

  it("refuses invalid endpoint port directions", () => {
    useFlowStore.getState().updateEdgeEndpoint("inlet", "from", "source", "inlet");
    useFlowStore.getState().updateEdgeEndpoint("inlet", "to", "throat", "outlet");

    expect(useFlowStore.getState().project.edges.inlet).toMatchObject({ fromPort: "outlet", toPort: "inlet" });
  });

  it("refuses duplicate port occupancy after endpoint editing", () => {
    useFlowStore.getState().updateEdgeEndpoint("inlet", "to", "sink", "inlet");

    expect(useFlowStore.getState().project.edges.inlet).toMatchObject({ to: "throat", toPort: "inlet" });
  });

  it("adds toolbar edges on available ports instead of occupied defaults", () => {
    useFlowStore.getState().addEdge("pipe");

    expect(Object.values(useFlowStore.getState().project.edges)).toHaveLength(3);
    expect(useFlowStore.getState().project.edges["pipe-3"]).toMatchObject({
      from: "source",
      to: "sink",
      fromPort: "north",
      toPort: "north"
    });
  });

  it("persists production mesh control settings in the project model", () => {
    useFlowStore.getState().updateSolverSettings({ meshResolution: "fine" });
    useFlowStore.getState().updateSolverMeshControls({
      longitudinalRefinement: 3,
      boundaryLayerLayers: 4,
      boundaryLayerGrowthRate: 1.35,
      targetYPlus: 5,
      refinementRegions: [{ edgeId: "inlet", factor: 3, reason: "venturi-local-refinement" }],
      featureRefinement: { enabled: true, factor: 2, clusterStrength: 0.6 },
      quality: { maxAspectRatio: 12, minInteriorAngleDeg: 25 }
    });

    expect(useFlowStore.getState().project.solver).toMatchObject({
      meshResolution: "fine",
      meshControls: {
        longitudinalRefinement: 3,
        boundaryLayerLayers: 4,
        boundaryLayerGrowthRate: 1.35,
        targetYPlus: 5,
        refinementRegions: [{ edgeId: "inlet", factor: 3, reason: "venturi-local-refinement" }],
        featureRefinement: { enabled: true, factor: 2, clusterStrength: 0.6 },
        quality: { maxAspectRatio: 12, minInteriorAngleDeg: 25 }
      }
    });
  });

  it("records model edits as one undoable change and supports redo", () => {
    const before = Object.keys(useFlowStore.getState().project.nodes);
    useFlowStore.getState().addNode("pump");
    expect(useFlowStore.getState().canUndo).toBe(true);
    expect(Object.keys(useFlowStore.getState().project.nodes)).toHaveLength(before.length + 1);

    useFlowStore.getState().undo();
    expect(Object.keys(useFlowStore.getState().project.nodes)).toEqual(before);
    expect(useFlowStore.getState().canRedo).toBe(true);

    useFlowStore.getState().redo();
    expect(Object.keys(useFlowStore.getState().project.nodes)).toHaveLength(before.length + 1);
  });
});
