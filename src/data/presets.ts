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

// Light mineral oil at 20 C (ISO VG 32 grade, kinematic viscosity 1.0e-4 m2/s
// = 100 cSt). A viscous fluid keeps a normal 20 mm line laminar at a normal
// 1 m/s, which is why the starter case uses oil and not water.
const lightMineralOil20C = {
  density: 870,
  dynamicViscosity: 0.087,
  vaporPressure: 100,
  bulkModulus: 1.5e9,
  temperature: 293.15
};

// --- Laminar starter case -------------------------------------------------
//
// Design point (all SI):
//   diameter   D = 0.02 m
//   length     L = 2.0 m            (100 diameters, so the flow develops)
//   fluid      rho = 870 kg/m3, mu = 0.087 Pa.s  -> nu = 1.0e-4 m2/s
//   velocity   U = 1.0 m/s
//   Reynolds   Re = rho*U*D/mu = 870*1.0*0.02/0.087 = 200
//
// Re = 200 is 11.5 times below the laminar limit of 2300, so the case sits
// inside the only regime FlowLab has accuracy evidence for. No FlowLab result
// is validated against a physical experiment.
//
// Flow rate  Q = U * pi*D^2/4 = 1.0 * 3.14159265e-4 = 3.14159265e-4 m3/s
//              = 18.85 litres per minute.
// The sink carries that flow as `flowDemand` because the OpenFOAM case takes
// its inlet velocity from flowDemand / area. Instant-1D and CFD therefore run
// the same operating point.
//
// Source pressure. The instant-1D solver sizes the flow from the boundary
// pressures with a turbulent friction guess, f(Re=1e5) = 0.0183099 for this
// tube. Its port-to-port length is 1.8 m, so
//   resistance = 0.0183099 * (1.8/0.02) = 1.6478873
//   dp         = U^2 * rho * resistance / 2 = 1.0 * 870 * 1.6478873 / 2
//              = 716.83 Pa
// Rounding to 717 Pa gives U = 1.00012 m/s and Re = 200.02.
//
// Analytic pressure loss at this design point:
//   round pipe   (Hagen-Poiseuille) dp = 32*mu*U*L/D^2 = 13 920 Pa
//   flat channel (plane-Poiseuille) dp = 12*mu*U*L/H^2 =  5 220 Pa, H = D
// The two differ by 32/12. The `planar-2d` mesh mode below builds the flat
// channel, not the round pipe. GuidedFirstCase shows the matching law.
const laminarStarterDiameterM = 0.02;
const laminarStarterLengthM = 2;
const laminarStarterMeanVelocityMPerS = 1;
const laminarStarterAreaM2 = (Math.PI * laminarStarterDiameterM ** 2) / 4;
const laminarStarterFlowM3S = laminarStarterAreaM2 * laminarStarterMeanVelocityMPerS;

export const laminarStarterPreset: FluidProject = {
  version: 1,
  name: "Laminar Starter Pipe (Experimental)",
  fluid: lightMineralOil20C,
  solver: {
    tier: "instant-1d",
    advancedMode: "incompressible-navier-stokes",
    turbulence: "laminar",
    meshResolution: "medium",
    runMode: "steady",
    meshMode: "planar-2d",
    meshControls: {
      // Uniform transverse spacing resolves the parabolic core. Wall-clustered
      // spacing leaves one coarse cell where a laminar profile peaks.
      transverseDistribution: "uniform",
      boundaryLayerLayers: 6,
      longitudinalRefinement: 4
    },
    maxIterations: 2000,
    tolerance: 1e-7
  },
  visualization: {
    mode: "design",
    overlay: "velocity",
    particles: true,
    streamlines: true,
    grid: true
  },
  viewport: { x: 0, y: 0, zoom: 1 },
  nodes: {
    supply: {
      id: "supply",
      type: "source",
      label: "Oil supply",
      position: { x: 150, y: 300 },
      // Explicit rotations keep the run straight: both ports stay on the
      // centreline, so the estimate adds no port-misalignment minor loss.
      rotation: 0,
      elevation: 0,
      pressure: 102_042,
      boundary: "pressure"
    },
    tank: {
      id: "tank",
      type: "sink",
      label: "Return tank",
      position: { x: 690, y: 300 },
      rotation: 0,
      elevation: 0,
      pressure: 101_325,
      flowDemand: laminarStarterFlowM3S,
      boundary: "pressure"
    }
  },
  edges: {
    tube: {
      id: "tube",
      type: "pipe",
      label: "Straight tube 20 mm",
      from: "supply",
      to: "tank",
      length: laminarStarterLengthM,
      shape: { kind: "circular", diameter: laminarStarterDiameterM },
      roughness: 0.0000015,
      minorLossK: 0
    }
  },
  sweeps: [
    {
      id: "starter-diameter-sweep",
      targetKind: "edge",
      targetId: "tube",
      parameter: "diameter",
      min: 0.012,
      max: 0.032,
      steps: 6
    }
  ]
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

const canonicalElbowDiameterM = 0.01;
const canonicalElbowCentrelineRadiusM = 0.03;
const canonicalElbowLegM = 0.1;
const canonicalElbowFlowM3S = Math.PI * canonicalElbowDiameterM ** 2 / 4 * 0.01;

export const canonicalElbowPreset: FluidProject = {
  version: 1,
  name: "Canonical 90° Elbow (Experimental)",
  fluid: {
    density: 1000,
    dynamicViscosity: 0.001,
    vaporPressure: 2_340,
    bulkModulus: 2.2e9,
    temperature: 293.15
  },
  solver: {
    tier: "openfoam",
    advancedMode: "incompressible-navier-stokes",
    turbulence: "laminar",
    meshResolution: "coarse",
    runMode: "steady",
    meshMode: "curved-elbow-ogrid",
    curvedElbowVerification: {
      contractId: "canonical-circular-elbow-re100-v2",
      boundaryCondition: "fully-developed-parabolic-inlet-pressure-outlet",
      diameterM: canonicalElbowDiameterM,
      centrelineRadiusM: canonicalElbowCentrelineRadiusM,
      inletLegLengthM: canonicalElbowLegM,
      outletLegLengthM: canonicalElbowLegM,
      bendAngleDegrees: 90,
      volumetricFlowRateM3PerS: canonicalElbowFlowM3S,
      qoiHistoryWriteIntervalIterations: 1
    },
    meshControls: {
      curvedElbowInletAxialCells: 28,
      curvedElbowBendAxialCells: 16,
      curvedElbowOutletAxialCells: 28,
      curvedElbowAnnularRadialCells: 2,
      curvedElbowCircumferentialCells: 16,
      curvedElbowCoreCellsPerSide: 4
    },
    maxIterations: 3000,
    tolerance: 1e-8
  },
  visualization: {
    mode: "simulate",
    overlay: "pressure",
    particles: false,
    streamlines: true,
    grid: true
  },
  viewport: { x: 0, y: 0, zoom: 1 },
  nodes: {
    source: {
      id: "source",
      type: "source",
      label: "10D inlet",
      position: { x: 180, y: 460 },
      elevation: 0,
      pressure: 101_325,
      boundary: "pressure"
    },
    sink: {
      id: "sink",
      type: "sink",
      label: "10D outlet",
      position: { x: 620, y: 120 },
      elevation: 0,
      pressure: 101_325,
      flowDemand: canonicalElbowFlowM3S,
      boundary: "pressure"
    }
  },
  edges: {
    "canonical-elbow": {
      id: "canonical-elbow",
      type: "bend",
      label: "90° elbow Rc/D=3",
      from: "source",
      to: "sink",
      length: canonicalElbowLegM * 2 + canonicalElbowCentrelineRadiusM * Math.PI / 2,
      shape: { kind: "circular", diameter: canonicalElbowDiameterM },
      roughness: 0,
      minorLossK: 0
    }
  },
  sweeps: []
};

/**
 * The case a new user must see first. Its Reynolds number is 200, which is
 * inside the laminar regime that FlowLab has accuracy evidence for. The showy
 * `venturiPreset` stays available from the Preset list, but it runs far outside
 * that regime, so it must not be the case the application opens with.
 */
export const defaultPreset = laminarStarterPreset;

export const presets = [
  laminarStarterPreset,
  venturiPreset,
  pipeLossPreset,
  canonicalElbowPreset,
  channelPreset,
  mixerPreset
];
