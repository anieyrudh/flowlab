import { create } from "zustand";
import { produce } from "immer";
import type {
  AdvancedPhysicsMode,
  EdgeId,
  FluidEdge,
  FluidNode,
  FluidProject,
  NodeId,
  OverlayMode,
  PipePortId,
  SimulationResult,
  SolverSettings,
  SolverTier,
  SweepConfig,
  WorkspaceMode
} from "../types";
import { venturiPreset } from "../data/presets";
import { solveHydraulicNetwork } from "../physics/hydraulics";
import { runSweep, type SweepRun } from "../physics/sweeps";
import { normalizeProject } from "../projectSchema";
import { measureEditorMetric } from "../performance/editorProfiler";

const fromPortCandidates: PipePortId[] = ["outlet", "north", "south"];
const toPortCandidates: PipePortId[] = ["inlet", "north", "south"];

export type AddEdgeResult = { ok: true; id: string } | { ok: false; message: string };

const HISTORY_LIMIT = 100;

type FlowStore = {
  project: FluidProject;
  result: SimulationResult;
  sweepRuns: SweepRun[];
  selectedId: string | null;
  selectedKind: "node" | "edge" | null;
  backendOnline: boolean;
  past: FluidProject[];
  future: FluidProject[];
  canUndo: boolean;
  canRedo: boolean;
  setProject: (project: FluidProject) => void;
  setProjectName: (name: string) => void;
  select: (kind: "node" | "edge", id: string) => void;
  setMode: (mode: WorkspaceMode) => void;
  setOverlay: (overlay: OverlayMode) => void;
  updateVisualization: (patch: Partial<Pick<FluidProject["visualization"], "particles" | "streamlines" | "grid">>) => void;
  setSolverTier: (tier: SolverTier) => void;
  setAdvancedMode: (mode: AdvancedPhysicsMode) => void;
  updateSolverSettings: (patch: Partial<SolverSettings>) => void;
  updateSolverMeshControls: (patch: Partial<NonNullable<SolverSettings["meshControls"]>>) => void;
  updateNode: (id: NodeId, patch: Partial<FluidNode>) => void;
  updateEdge: (id: EdgeId, patch: Partial<FluidEdge>) => void;
  moveNode: (id: NodeId, position: FluidNode["position"]) => void;
  rotateNode: (id: NodeId, rotation: number) => void;
  connectEdge: (from: NodeId, to: NodeId, fromPort?: PipePortId, toPort?: PipePortId) => void;
  updateEdgeEndpoint: (id: EdgeId, endpoint: "from" | "to", nodeId: NodeId, port?: PipePortId) => void;
  deleteSelected: () => void;
  undo: () => void;
  redo: () => void;
  addNode: (type: FluidNode["type"]) => void;
  addEdge: (type: FluidEdge["type"]) => AddEdgeResult;
  runInstant: () => void;
  runSweep: (sweep?: SweepConfig) => void;
  setBackendOnline: (online: boolean) => void;
};

function recalc(project: FluidProject): SimulationResult {
  return measureEditorMetric("hydraulic-recalc", () => solveHydraulicNetwork(project));
}

function selectionForProject(project: FluidProject): Pick<FlowStore, "selectedId" | "selectedKind"> {
  const edgeId = Object.keys(project.edges)[0];
  const nodeId = Object.keys(project.nodes)[0];
  return {
    selectedId: edgeId ?? nodeId ?? null,
    selectedKind: edgeId ? "edge" : nodeId ? "node" : null
  };
}

function sameProject(a: FluidProject, b: FluidProject): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function uniqueId(prefix: string, existing: Record<string, unknown>): string {
  let index = Object.keys(existing).length + 1;
  let id = `${prefix}-${index}`;
  while (existing[id]) {
    index += 1;
    id = `${prefix}-${index}`;
  }
  return id;
}

function isPortOccupied(
  edges: Record<EdgeId, FluidEdge>,
  nodeId: NodeId,
  port: PipePortId,
  ignoredEdgeId?: EdgeId
): boolean {
  return Object.values(edges).some((edge) => {
    if (edge.id === ignoredEdgeId) return false;
    return (edge.from === nodeId && (edge.fromPort ?? "outlet") === port) || (edge.to === nodeId && (edge.toPort ?? "inlet") === port);
  });
}

function isValidEndpointPort(endpoint: "from" | "to", port: PipePortId): boolean {
  return endpoint === "from" ? port !== "inlet" : port !== "outlet";
}

function availableEndpointPort(
  edges: Record<EdgeId, FluidEdge>,
  endpoint: "from" | "to",
  nodeId: NodeId,
  ignoredEdgeId?: EdgeId
): PipePortId | null {
  const candidates = endpoint === "from" ? fromPortCandidates : toPortCandidates;
  return candidates.find((port) => !isPortOccupied(edges, nodeId, port, ignoredEdgeId)) ?? null;
}

export const useFlowStore = create<FlowStore>((set, get) => {
  const commitProjectEdit = (recipe: (state: FlowStore) => void) => {
    const before = structuredClone(get().project);
    set(
      produce((state: FlowStore) => {
        recipe(state);
        state.result = recalc(state.project);
      })
    );
    const after = get().project;
    if (sameProject(before, after)) return;
    const past = [...get().past, before].slice(-HISTORY_LIMIT);
    set({ past, future: [], canUndo: past.length > 0, canRedo: false });
  };

  const restoreProject = (project: FluidProject, past: FluidProject[], future: FluidProject[]) => {
    const restored = structuredClone(project);
    set({
      project: restored,
      result: recalc(restored),
      ...selectionForProject(restored),
      past,
      future,
      canUndo: past.length > 0,
      canRedo: future.length > 0,
      sweepRuns: []
    });
  };

  return ({
  project: normalizeProject(structuredClone(venturiPreset)),
  result: recalc(normalizeProject(structuredClone(venturiPreset))),
  sweepRuns: [],
  selectedId: "inlet",
  selectedKind: "edge",
  backendOnline: false,
  past: [],
  future: [],
  canUndo: false,
  canRedo: false,
  setProject: (project) => {
    const next = normalizeProject(structuredClone(project));
    const current = get().project;
    if (sameProject(current, next)) return;
    set({
      project: next,
      result: recalc(next),
      ...selectionForProject(next),
      sweepRuns: [],
      past: [],
      future: [],
      canUndo: false,
      canRedo: false
    });
  },
  undo: () => {
    const state = get();
    const previous = state.past[state.past.length - 1];
    if (!previous) return;
    const current = structuredClone(state.project);
    restoreProject(previous, state.past.slice(0, -1), [...state.future, current]);
  },
  redo: () => {
    const state = get();
    const next = state.future[state.future.length - 1];
    if (!next) return;
    const current = structuredClone(state.project);
    restoreProject(next, [...state.past, current].slice(-HISTORY_LIMIT), state.future.slice(0, -1));
  },
  setProjectName: (name) =>
    set(
      produce((state: FlowStore) => {
        state.project.name = name;
      })
    ),
  select: (kind, id) => set({ selectedKind: kind, selectedId: id }),
  setMode: (mode) =>
    set(
      produce((state: FlowStore) => {
        state.project.visualization.mode = mode;
      })
    ),
  setOverlay: (overlay) =>
    set(
      produce((state: FlowStore) => {
        state.project.visualization.overlay = overlay;
      })
    ),
  updateVisualization: (patch) =>
    set(
      produce((state: FlowStore) => {
        Object.assign(state.project.visualization, patch);
      })
    ),
  setSolverTier: (tier) =>
    set(
      produce((state: FlowStore) => {
        state.project.solver.tier = tier;
      })
    ),
  setAdvancedMode: (mode) =>
    set(
      produce((state: FlowStore) => {
        state.project.solver.advancedMode = mode;
      })
    ),
  updateSolverSettings: (patch) =>
    set(
      produce((state: FlowStore) => {
        Object.assign(state.project.solver, patch);
      })
    ),
  updateSolverMeshControls: (patch) =>
    set(
      produce((state: FlowStore) => {
        state.project.solver.meshControls = {
          ...(state.project.solver.meshControls ?? {}),
          ...patch
        };
      })
    ),
  updateNode: (id, patch) =>
    commitProjectEdit((state) => {
        Object.assign(state.project.nodes[id], patch);
      }),
  updateEdge: (id, patch) =>
    commitProjectEdit((state) => {
        Object.assign(state.project.edges[id], patch);
      }),
  moveNode: (id, position) =>
    commitProjectEdit((state) => {
        if (!state.project.nodes[id]) return;
        state.project.nodes[id].position = position;
      }),
  rotateNode: (id, rotation) =>
    commitProjectEdit((state) => {
        if (!state.project.nodes[id]) return;
        state.project.nodes[id].rotation = Math.round(rotation);
      }),
  connectEdge: (from, to, fromPort = "outlet", toPort = "inlet") =>
    commitProjectEdit((state) => {
        if (!state.project.nodes[from] || !state.project.nodes[to] || from === to) return;
        if (!isValidEndpointPort("from", fromPort) || !isValidEndpointPort("to", toPort)) return;
        if (isPortOccupied(state.project.edges, from, fromPort) || isPortOccupied(state.project.edges, to, toPort)) return;
        const id = uniqueId("pipe", state.project.edges);
        state.project.edges[id] = {
          id,
          type: "pipe",
          label: `Pipe ${Object.keys(state.project.edges).length + 1}`,
          from,
          to,
          fromPort,
          toPort,
          length: 12,
          shape: { kind: "circular", diameter: 0.1 },
          roughness: 0.000045,
          minorLossK: 0.2
        };
        state.selectedKind = "edge";
        state.selectedId = id;
      }),
  updateEdgeEndpoint: (id, endpoint, nodeId, port) =>
    commitProjectEdit((state) => {
        const edge = state.project.edges[id];
        if (!edge || !state.project.nodes[nodeId]) return;
        const otherNodeId = endpoint === "from" ? edge.to : edge.from;
        if (nodeId === otherNodeId) return;
        const nextPort = port ?? (endpoint === "from" ? edge.fromPort ?? "outlet" : edge.toPort ?? "inlet");
        if (!isValidEndpointPort(endpoint, nextPort)) return;
        if (isPortOccupied(state.project.edges, nodeId, nextPort, id)) return;
        if (endpoint === "from") {
          edge.from = nodeId;
          edge.fromPort = nextPort;
        } else {
          edge.to = nodeId;
          edge.toPort = nextPort;
        }
        state.selectedKind = "edge";
        state.selectedId = id;
      }),
  deleteSelected: () =>
    commitProjectEdit((state) => {
        if (!state.selectedId || !state.selectedKind) return;
        if (state.selectedKind === "edge") {
          delete state.project.edges[state.selectedId];
        } else {
          delete state.project.nodes[state.selectedId];
          for (const edge of Object.values(state.project.edges)) {
            if (edge.from === state.selectedId || edge.to === state.selectedId) {
              delete state.project.edges[edge.id];
            }
          }
        }
        const nextEdge = Object.keys(state.project.edges)[0];
        const nextNode = Object.keys(state.project.nodes)[0];
        state.selectedKind = nextEdge ? "edge" : nextNode ? "node" : null;
        state.selectedId = nextEdge ?? nextNode ?? null;
      }),
  addNode: (type) =>
    commitProjectEdit((state) => {
        const id = uniqueId(type, state.project.nodes);
        state.project.nodes[id] = {
          id,
          type,
          label: type[0].toUpperCase() + type.slice(1),
          position: { x: 180 + Object.keys(state.project.nodes).length * 52, y: 180 },
          rotation: type === "source" ? 0 : type === "sink" ? 180 : 0,
          elevation: 0,
          pressure: type === "source" ? 180_000 : type === "sink" ? 101_325 : undefined,
          head: type === "pump" ? 12 : undefined,
          concentration: type === "source" ? 0.5 : undefined
        };
        state.selectedKind = "node";
        state.selectedId = id;
      }),
  addEdge: (type) => {
    const current = get();
    const nodeIds = Object.keys(current.project.nodes);
    if (nodeIds.length < 2) {
      return { ok: false, message: `Add at least two components before adding a ${type}.` };
    }
    const from = nodeIds[0];
    const to = nodeIds[nodeIds.length - 1];
    if (from === to) {
      return { ok: false, message: `Choose two different components before adding a ${type}.` };
    }
    const fromPort = availableEndpointPort(current.project.edges, "from", from);
    const toPort = availableEndpointPort(current.project.edges, "to", to);
    if (!fromPort || !toPort) {
      return {
        ok: false,
        message: `No compatible free ports connect ${current.project.nodes[from].label} to ${current.project.nodes[to].label}. Select endpoints on the canvas or add another component.`
      };
    }
    const id = uniqueId(type, current.project.edges);
    commitProjectEdit((state) => {
        state.project.edges[id] = {
          id,
          type,
          label: type[0].toUpperCase() + type.slice(1),
          from,
          to,
          fromPort,
          toPort,
          length: type === "bend" ? 4 : 12,
          shape: { kind: "circular", diameter: type === "venturi" ? 0.16 : 0.1 },
          throatDiameter: type === "venturi" ? 0.07 : undefined,
          dischargeCoefficient: type === "venturi" ? 0.97 : undefined,
          roughness: 0.000045,
          minorLossK: type === "valve" ? 2.5 : type === "bend" ? 0.9 : 0.2,
          valveOpening: type === "valve" ? 0.75 : undefined
        };
        state.selectedKind = "edge";
        state.selectedId = id;
      });
    return { ok: true, id };
  },
  runInstant: () => set({ result: recalc(get().project) }),
  runSweep: (sweep) => {
    const target = sweep ?? get().project.sweeps[0];
    if (!target) return;
    set({ sweepRuns: runSweep(get().project, target) });
  },
  setBackendOnline: (online) => set({ backendOnline: online })
  });
});
