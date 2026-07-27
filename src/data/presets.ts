import type { FluidProject } from "../types";

const water20C = {
  density: 998,
  dynamicViscosity: 1.002e-3,
  vaporPressure: 2_340,
  bulkModulus: 2.2e9,
  temperature: 293.15
};

const baseSolver = {
  tier: "instant-1d" as const,
  advancedMode: "incompressible-navier-stokes" as const,
  turbulence: "rans-sst" as const,
  meshResolution: "coarse" as const,
  maxIterations: 1200,
  tolerance: 1e-6
};

const baseVisualization = {
  mode: "design" as const,
  overlay: "velocity" as const,
  particles: true,
  streamlines: true,
  grid: true
};

export const venturiPreset: FluidProject = {
  version: 1,
  name: "Venturi Cavitation Lab",
  fluid: water20C,
  solver: baseSolver,
  visualization: baseVisualization,
  viewport: { x: 0, y: 0, zoom: 1 },
  nodes: {
    source: {
      id: "source",
      type: "source",
      label: "Inlet reservoir",
      position: { x: 120, y: 260 },
      elevation: 2,
      pressure: 260_000,
      concentration: 0.1,
      boundary: "pressure"
    },
    throat: {
      id: "throat",
      type: "junction",
      label: "Venturi throat",
      position: { x: 420, y: 260 },
      elevation: 0.5
    },
    sink: {
      id: "sink",
      type: "sink",
      label: "Outlet",
      position: { x: 720, y: 260 },
      elevation: 0,
      pressure: 101_325,
      boundary: "pressure"
    }
  },
  edges: {
    inlet: {
      id: "inlet",
      type: "venturi",
      label: "Converging Venturi",
      from: "source",
      to: "throat",
      length: 6,
      shape: { kind: "circular", diameter: 0.18 },
      throatDiameter: 0.075,
      outletDiameter: 0.16,
      dischargeCoefficient: 0.97,
      roughness: 0.000045,
      minorLossK: 0.15
    },
    outlet: {
      id: "outlet",
      type: "expansion",
      label: "Diffuser outlet",
      from: "throat",
      to: "sink",
      length: 7,
      shape: { kind: "circular", diameter: 0.16 },
      outletDiameter: 0.18,
      roughness: 0.000045,
      minorLossK: 0.28
    }
  },
  sweeps: [
    {
      id: "throat-sweep",
      targetKind: "edge",
      targetId: "inlet",
      parameter: "throatDiameter",
      min: 0.045,
      max: 0.14,
      steps: 8
    }
  ]
};

export const pipeLossPreset: FluidProject = {
  ...venturiPreset,
  name: "Pipe Head-Loss Bench",
  nodes: {
    source: { ...venturiPreset.nodes.source, label: "High-pressure inlet", position: { x: 120, y: 210 } },
    sink: { ...venturiPreset.nodes.sink, label: "Demand sink", position: { x: 760, y: 330 }, flowDemand: 0.02 }
  },
  edges: {
    pipe: {
      id: "pipe",
      type: "pipe",
      label: "Rough steel pipe",
      from: "source",
      to: "sink",
      length: 44,
      shape: { kind: "circular", diameter: 0.1 },
      roughness: 0.00015,
      minorLossK: 1.9
    }
  },
  sweeps: [
    { id: "roughness-sweep", targetKind: "edge", targetId: "pipe", parameter: "diameter", min: 0.06, max: 0.18, steps: 7 }
  ]
};

export const mixerPreset: FluidProject = {
  ...venturiPreset,
  name: "Mixer Conservation Lab",
  nodes: {
    a: {
      id: "a",
      type: "source",
      label: "Cold feed",
      position: { x: 100, y: 180 },
      elevation: 0,
      pressure: 220_000,
      concentration: 0.2,
      boundary: "pressure"
    },
    b: {
      id: "b",
      type: "source",
      label: "Hot feed",
      position: { x: 100, y: 340 },
      elevation: 0,
      pressure: 180_000,
      concentration: 0.85,
      boundary: "pressure"
    },
    mix: { id: "mix", type: "mixer", label: "Static mixer", position: { x: 430, y: 260 }, elevation: 0 },
    sink: { ...venturiPreset.nodes.sink, label: "Blend outlet", position: { x: 760, y: 260 } }
  },
  edges: {
    feedA: {
      id: "feedA",
      type: "pipe",
      label: "Feed A",
      from: "a",
      to: "mix",
      length: 16,
      shape: { kind: "circular", diameter: 0.09 },
      roughness: 0.000045,
      minorLossK: 0.4
    },
    feedB: {
      id: "feedB",
      type: "pipe",
      label: "Feed B",
      from: "b",
      to: "mix",
      length: 16,
      shape: { kind: "circular", diameter: 0.07 },
      roughness: 0.000045,
      minorLossK: 0.5
    },
    outlet: {
      id: "outlet",
      type: "pipe",
      label: "Mixed outlet",
      from: "mix",
      to: "sink",
      length: 20,
      shape: { kind: "circular", diameter: 0.12 },
      roughness: 0.000045,
      minorLossK: 0.7
    }
  }
};

export const channelPreset: FluidProject = {
  ...venturiPreset,
  name: "Rectangular Channel Bench",
  nodes: {
    source: {
      ...venturiPreset.nodes.source,
      label: "Channel inlet",
      position: { x: 120, y: 245 },
      pressure: 180_000
    },
    sink: {
      ...venturiPreset.nodes.sink,
      label: "Tailwater outlet",
      position: { x: 760, y: 285 },
      pressure: 101_325
    }
  },
  edges: {
    channel: {
      id: "channel",
      type: "pipe",
      label: "Rectangular channel",
      from: "source",
      to: "sink",
      length: 18,
      shape: { kind: "rectangular", width: 0.32, height: 0.08 },
      roughness: 0.00003,
      minorLossK: 0.35
    }
  },
  sweeps: [{ id: "channel-height-sweep", targetKind: "edge", targetId: "channel", parameter: "height", min: 0.05, max: 0.16, steps: 6 }]
};

export const presets = [venturiPreset, pipeLossPreset, channelPreset, mixerPreset];
