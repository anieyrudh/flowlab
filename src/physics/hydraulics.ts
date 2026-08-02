import type {
  ChannelShape,
  ControlVolumeResult,
  EdgeResult,
  FluidEdge,
  FluidNode,
  FluidParams,
  FluidProject,
  NodeResult,
  PipePortId,
  SimulationResult,
  SimulationWarning,
  Vec2,
  WaterHammerResult
} from "../types";

const G = 9.80665;
const MIN_EFFECTIVE_LENGTH = 0.05;

export function area(shape: ChannelShape): number {
  if (shape.kind === "circular") return Math.PI * (shape.diameter / 2) ** 2;
  return shape.width * shape.height;
}

export function wettedPerimeter(shape: ChannelShape): number {
  if (shape.kind === "circular") return Math.PI * shape.diameter;
  return 2 * (shape.width + shape.height);
}

export function hydraulicDiameter(shape: ChannelShape): number {
  return (4 * area(shape)) / wettedPerimeter(shape);
}

export function velocity(flowRate: number, shape: ChannelShape): number {
  return flowRate / area(shape);
}

export function reynoldsNumber(flowRate: number, shape: ChannelShape, fluid: FluidParams): number {
  return Math.abs((fluid.density * velocity(flowRate, shape) * hydraulicDiameter(shape)) / fluid.dynamicViscosity);
}

export function flowRegime(reynolds: number): EdgeResult["regime"] {
  if (reynolds < 2300) return "laminar";
  if (reynolds <= 4000) return "transitional";
  return "turbulent";
}

export function frictionFactor(reynolds: number, roughness: number, diameter: number): number {
  if (reynolds <= 0) return 0;
  const laminar = 64 / reynolds;
  const turbulent =
    0.25 / Math.log10(roughness / (3.7 * diameter) + 5.74 / Math.max(reynolds, 1) ** 0.9) ** 2;
  if (reynolds < 2300) return laminar;
  if (reynolds > 4000) return turbulent;
  const t = (reynolds - 2300) / 1700;
  return laminar * (1 - t) + turbulent * t;
}

function edgeDiameter(edge: FluidEdge): number {
  return hydraulicDiameter(edge.shape);
}

function degreesToRadians(degrees: number): number {
  return (degrees * Math.PI) / 180;
}

function radiansToDegrees(radians: number): number {
  return (radians * 180) / Math.PI;
}

function nodeRadius(node: FluidNode): number {
  if (node.type === "source" || node.type === "sink") return 17;
  if (node.type === "pump") return 19;
  return 14;
}

function portAngle(node: FluidNode, port: PipePortId): number {
  const base = node.rotation ?? 0;
  if (port === "outlet") return base;
  if (port === "inlet") return base + 180;
  if (port === "north") return base - 90;
  return base + 90;
}

function unitFromAngle(degrees: number): Vec2 {
  const angle = degreesToRadians(degrees);
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function portPosition(node: FluidNode, port: PipePortId): Vec2 {
  const radius = nodeRadius(node) + 10;
  const direction = unitFromAngle(portAngle(node, port));
  return {
    x: node.position.x + direction.x * radius,
    y: node.position.y + direction.y * radius
  };
}

function vectorBetween(from: Vec2, to: Vec2): Vec2 {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  return { x: dx / length, y: dy / length };
}

function dot(a: Vec2, b: Vec2): number {
  return a.x * b.x + a.y * b.y;
}

function angleBetween(a: Vec2, b: Vec2): number {
  return Math.acos(Math.max(-1, Math.min(1, dot(a, b))));
}

function fittingLossFromBend(edge: FluidEdge, bendAngleRadians: number): number {
  const angle90 = bendAngleRadians / (Math.PI / 2);
  const base = edge.type === "bend" ? 0.65 : 0.16;
  const fittingMultiplier =
    edge.type === "valve" ? 1.4 : edge.type === "contraction" || edge.type === "expansion" ? 1.2 : 1;
  const valvePenalty =
    edge.type === "valve" && typeof edge.valveOpening === "number"
      ? Math.max(0, (1 / Math.max(edge.valveOpening, 0.05) - 1) ** 2)
      : 0;
  return Math.max(0, base * fittingMultiplier * angle90 + valvePenalty);
}

export type EdgeGeometry = {
  start: Vec2;
  end: Vec2;
  direction: Vec2;
  flowDirection: Vec2;
  effectiveLength: number;
  centerLengthPx: number;
  portLengthPx: number;
  elevationDelta: number;
  bendAngle: number;
  bendAngleDegrees: number;
  geometryMinorLossK: number;
  totalMinorLossK: number;
};

export function resolveEdgeGeometry(edge: FluidEdge, nodes: Record<string, FluidNode>, flowRate = 1): EdgeGeometry | null {
  const from = nodes[edge.from];
  const to = nodes[edge.to];
  if (!from || !to) return null;

  const fromPort = edge.fromPort ?? "outlet";
  const toPort = edge.toPort ?? "inlet";
  const start = portPosition(from, fromPort);
  const end = portPosition(to, toPort);
  const portLengthPx = Math.hypot(end.x - start.x, end.y - start.y);
  const centerLengthPx = Math.hypot(to.position.x - from.position.x, to.position.y - from.position.y) || portLengthPx || 1;
  const effectiveLength = Math.max(MIN_EFFECTIVE_LENGTH, edge.length * (portLengthPx || centerLengthPx) / centerLengthPx);
  const direction = vectorBetween(start, end);
  const fromPortDirection = unitFromAngle(portAngle(from, fromPort));
  const toPortInwardDirection = unitFromAngle(portAngle(to, toPort) + 180);
  const bendAngle = angleBetween(fromPortDirection, direction) + angleBetween(direction, toPortInwardDirection);
  const geometryMinorLossK = fittingLossFromBend(edge, bendAngle);
  const signed = Math.sign(flowRate || 1);
  const flowDirection = signed >= 0 ? direction : { x: -direction.x, y: -direction.y };

  return {
    start,
    end,
    direction,
    flowDirection,
    effectiveLength,
    centerLengthPx,
    portLengthPx,
    elevationDelta: to.elevation - from.elevation,
    bendAngle,
    bendAngleDegrees: radiansToDegrees(bendAngle),
    geometryMinorLossK,
    totalMinorLossK: edge.minorLossK + geometryMinorLossK
  };
}

function pressureHead(pressure: number, fluid: FluidParams): number {
  return pressure / (fluid.density * G);
}

function nodePressure(node: FluidNode, fluid: FluidParams): number {
  if (typeof node.pressure === "number") return node.pressure;
  return 101_325 + fluid.density * G * node.elevation;
}

/** Iteration limit for the flow/friction fixed point. It settles in far fewer. */
const FLOW_SOLVE_MAX_ITERATIONS = 60;
const FLOW_SOLVE_RELATIVE_TOLERANCE = 1e-12;

export type EdgeFlowSolution = { flow: number; converged: boolean; iterations: number };

/**
 * Solves one edge for a flow that agrees with its own friction factor.
 *
 * The friction factor depends on the Reynolds number, which depends on the
 * flow this equation produces, so a single pass cannot be self-consistent.
 * This previously seeded a turbulent factor at Re = 100,000 and returned that
 * first pass, which is wrong wherever the flow is not near that Reynolds
 * number. In laminar flow the error is large: at Re = 200 the true factor is
 * 64/200 = 0.32 against the seed's 0.0183, a factor of 17.5, and flow scales
 * with the inverse square root of it. Laminar flow is the one regime the
 * accuracy evidence covers, so this is where correctness matters most.
 */
function edgeBaseFlow(edge: FluidEdge, from: FluidNode, to: FluidNode, fluid: FluidParams): EdgeFlowSolution {
  const fromHead = pressureHead(nodePressure(from, fluid), fluid) + from.elevation + (from.head ?? 0);
  const toHead = pressureHead(nodePressure(to, fluid), fluid) + to.elevation;
  const deltaHead = fromHead - toHead;
  const diameter = edgeDiameter(edge);
  const pipeArea = area(edge.shape);
  const geometry = resolveEdgeGeometry(edge, { [from.id]: from, [to.id]: to });
  const effectiveLength = geometry?.effectiveLength ?? edge.length;
  const totalMinorLossK = geometry?.totalMinorLossK ?? edge.minorLossK;
  const signed = Math.sign(deltaHead || 1);

  // Seed with the old turbulent guess, then correct it against the Reynolds
  // number each pass produces. The factor falls as Reynolds rises, so the
  // feedback is negative and the fixed point is stable.
  let friction = frictionFactor(100_000, edge.roughness, diameter);
  let flow = 0;
  let converged = false;
  let iterations = 0;

  for (let step = 1; step <= FLOW_SOLVE_MAX_ITERATIONS; step += 1) {
    iterations = step;
    const resistance = Math.max(friction * (effectiveLength / diameter) + totalMinorLossK, 0.001);
    const next = signed * pipeArea * Math.sqrt(Math.abs((2 * G * deltaHead) / resistance));
    if (!Number.isFinite(next)) return { flow: 0, converged: false, iterations: step };

    const change = Math.abs(next - flow);
    flow = next;
    if (step > 1 && change <= FLOW_SOLVE_RELATIVE_TOLERANCE * Math.max(Math.abs(flow), Number.EPSILON)) {
      converged = true;
      break;
    }
    friction = frictionFactor(reynoldsNumber(flow, edge.shape, fluid), edge.roughness, diameter);
  }

  return { flow, converged, iterations };
}

function topologyWarnings(project: FluidProject): SimulationWarning[] {
  const warnings: SimulationWarning[] = [];
  const parent = new Map<string, string>();
  const occupiedPorts = new Map<string, string>();

  function find(nodeId: string): string {
    const current = parent.get(nodeId) ?? nodeId;
    if (current === nodeId) return current;
    const root = find(current);
    parent.set(nodeId, root);
    return root;
  }

  function union(a: string, b: string) {
    const rootA = find(a);
    const rootB = find(b);
    if (rootA !== rootB) parent.set(rootB, rootA);
  }

  for (const nodeId of Object.keys(project.nodes)) {
    parent.set(nodeId, nodeId);
  }

  for (const edge of Object.values(project.edges)) {
    const from = project.nodes[edge.from];
    const to = project.nodes[edge.to];
    if (!from || !to) continue;

    if (edge.from === edge.to) {
      warnings.push({
        id: `self-loop-${edge.id}`,
        severity: "error",
        targetId: edge.id,
        message: `${edge.label} cannot connect ${from.label} to itself.`
      });
      continue;
    }

    if ((edge.fromPort ?? "outlet") === "inlet") {
      warnings.push({
        id: `invalid-from-port-${edge.id}`,
        severity: "error",
        targetId: edge.id,
        message: `${edge.label} cannot use inlet as a source-side port.`
      });
    }
    if ((edge.toPort ?? "inlet") === "outlet") {
      warnings.push({
        id: `invalid-to-port-${edge.id}`,
        severity: "error",
        targetId: edge.id,
        message: `${edge.label} cannot use outlet as a target-side port.`
      });
    }

    union(edge.from, edge.to);

    for (const [node, port] of [
      [from, edge.fromPort ?? "outlet"],
      [to, edge.toPort ?? "inlet"]
    ] as const) {
      if (node.type === "mixer" || node.type === "junction") continue;
      const key = `${node.id}:${port}`;
      const previous = occupiedPorts.get(key);
      if (previous) {
        warnings.push({
          id: `duplicate-port-${node.id}-${port}`,
          severity: "error",
          targetId: node.id,
          message: `${node.label} ${port} port is already occupied by ${previous}.`
        });
      } else {
        occupiedPorts.set(key, edge.label);
      }
    }
  }

  const components = new Map<string, FluidNode[]>();
  for (const node of Object.values(project.nodes)) {
    const root = find(node.id);
    components.set(root, [...(components.get(root) ?? []), node]);
  }

  if (components.size > 1) {
    warnings.push({
      id: "network-disconnected",
      severity: "warning",
      message: `Network has ${components.size} disconnected components.`
    });
  }

  for (const nodes of components.values()) {
    const connectedEdgeCount = Object.values(project.edges).filter((edge) => nodes.some((node) => node.id === edge.from || node.id === edge.to)).length;
    if (connectedEdgeCount === 0) {
      const node = nodes[0];
      warnings.push({
        id: `isolated-${node.id}`,
        severity: "warning",
        targetId: node.id,
        message: `${node.label} is not connected to the hydraulic network.`
      });
      continue;
    }

    const hasSource = nodes.some((node) => node.type === "source");
    const hasSink = nodes.some((node) => node.type === "sink");
    if (!hasSource || !hasSink) {
      warnings.push({
        id: `ambiguous-${nodes.map((node) => node.id).sort().join("-")}`,
        severity: "warning",
        targetId: nodes[0].id,
        message: `${nodes.map((node) => node.label).join(", ")} component needs at least one source and one sink boundary.`
      });
    }
  }

  return warnings;
}

export function solveHydraulicNetwork(project: FluidProject): SimulationResult {
  const warnings: SimulationWarning[] = topologyWarnings(project);
  const edgeResults: SimulationResult["edgeResults"] = {};
  const nodeResults: Record<string, NodeResult> = {};
  const massIn: Record<string, number> = {};
  const massOut: Record<string, number> = {};
  const concentrationsIn: Record<string, number> = {};

  for (const node of Object.values(project.nodes)) {
    massIn[node.id] = 0;
    massOut[node.id] = 0;
    concentrationsIn[node.id] = 0;
    nodeResults[node.id] = {
      pressure: nodePressure(node, project.fluid),
      head: pressureHead(nodePressure(node, project.fluid), project.fluid) + node.elevation,
      massResidual: 0,
      concentration: node.concentration
    };
  }

  let allEdgesConverged = true;
  for (const edge of Object.values(project.edges)) {
    const from = project.nodes[edge.from];
    const to = project.nodes[edge.to];
    if (!from || !to) {
      warnings.push({
        id: `missing-${edge.id}`,
        severity: "error",
        targetId: edge.id,
        message: `${edge.label} is disconnected.`
      });
      continue;
    }

    const flowSolution = edgeBaseFlow(edge, from, to, project.fluid);
    const flow = flowSolution.flow;
    if (!flowSolution.converged) allEdgesConverged = false;
    const geometry = resolveEdgeGeometry(edge, project.nodes, flow);
    const v = velocity(flow, edge.shape);
    const re = reynoldsNumber(flow, edge.shape, project.fluid);
    const diameter = edgeDiameter(edge);
    const f = frictionFactor(re, edge.roughness, diameter);
    const effectiveLength = geometry?.effectiveLength ?? edge.length;
    const totalMinorLossK = geometry?.totalMinorLossK ?? edge.minorLossK;
    const geometryMinorLossK = geometry?.geometryMinorLossK ?? 0;
    const majorHeadLoss = f * (effectiveLength / diameter) * (v ** 2 / (2 * G));
    const minorHeadLoss = totalMinorLossK * (v ** 2 / (2 * G));
    const pressureDrop = project.fluid.density * G * (majorHeadLoss + minorHeadLoss);
    const throatArea = edge.type === "venturi" && edge.throatDiameter ? Math.PI * (edge.throatDiameter / 2) ** 2 : null;
    const throatVelocity = throatArea ? flow / throatArea : v;
    const localPressure =
      nodePressure(from, project.fluid) +
      0.5 * project.fluid.density * (v ** 2 - throatVelocity ** 2) -
      pressureDrop;
    const cavitationRisk = localPressure <= project.fluid.vaporPressure + 5_000;

    if (cavitationRisk) {
      warnings.push({
        id: `cavitation-${edge.id}`,
        severity: "warning",
        targetId: edge.id,
        message: `${edge.label} is near or below cavitation threshold.`
      });
    }

    if (geometry && geometry.bendAngleDegrees > 135) {
      warnings.push({
        id: `port-aim-${edge.id}`,
        severity: "info",
        targetId: edge.id,
        message: `${edge.label} has ${Math.round(geometry.bendAngleDegrees)}deg of port misalignment contributing to minor losses.`
      });
    }

    edgeResults[edge.id] = {
      flowRate: flow,
      velocity: v,
      reynolds: re,
      frictionFactor: f,
      effectiveLength,
      bendAngle: geometry?.bendAngleDegrees ?? 0,
      geometryMinorLossK,
      majorHeadLoss,
      minorHeadLoss,
      pressureDrop,
      regime: flowRegime(re),
      cavitationRisk
    };

    if (flow >= 0) {
      massOut[from.id] += flow;
      massIn[to.id] += flow;
      concentrationsIn[to.id] += flow * (from.concentration ?? 0);
    } else {
      massOut[to.id] += Math.abs(flow);
      massIn[from.id] += Math.abs(flow);
      concentrationsIn[from.id] += Math.abs(flow) * (to.concentration ?? 0);
    }
  }

  for (const node of Object.values(project.nodes)) {
    const prescribed = node.flowDemand ?? 0;
    const residual = massIn[node.id] - massOut[node.id] - prescribed;
    nodeResults[node.id].massResidual = residual;
    if (node.type === "mixer" && massIn[node.id] > 0) {
      nodeResults[node.id].concentration = concentrationsIn[node.id] / massIn[node.id];
    }
    if (Math.abs(residual) > 1e-4 && node.type !== "source" && node.type !== "sink") {
      warnings.push({
        id: `mass-${node.id}`,
        severity: "info",
        targetId: node.id,
        message: `${node.label} mass residual is ${residual.toExponential(2)} m3/s.`
      });
    }
  }

  const controlVolumes = computeControlVolumes(project, edgeResults, nodeResults);
  const waterHammer = computeWaterHammer(project, edgeResults);

  return {
    stable: !warnings.some((warning) => warning.severity === "error"),
    converged: allEdgesConverged,
    timestep: Date.now(),
    edgeResults,
    nodeResults,
    controlVolumes,
    waterHammer,
    warnings
  };
}

export function computeControlVolumes(
  project: FluidProject,
  edgeResults: Record<string, EdgeResult>,
  nodeResults: Record<string, NodeResult>
): Record<string, ControlVolumeResult> {
  const results: Record<string, ControlVolumeResult> = {};

  for (const edge of Object.values(project.edges)) {
    const from = project.nodes[edge.from];
    const to = project.nodes[edge.to];
    const solved = edgeResults[edge.id];
    if (!from || !to || !solved) continue;
    const geometry = resolveEdgeGeometry(edge, project.nodes, solved.flowRate);
    const direction = geometry?.flowDirection ?? vectorBetween(from.position, to.position);
    const mdot = project.fluid.density * solved.flowRate;
    const momentumFlux = mdot * solved.velocity;
    const inletForce = nodeResults[from.id].pressure * area(edge.shape);
    const outletForce = nodeResults[to.id].pressure * area(edge.shape);
    const pressureForce = inletForce - outletForce;
    results[edge.id] = {
      targetId: edge.id,
      massResidual: Math.abs(mdot) * 1e-6,
      momentumFlux,
      pressureForce,
      reactionForce: {
        x: pressureForce * direction.x - momentumFlux * direction.x,
        y: pressureForce * direction.y - momentumFlux * direction.y
      }
    };
  }

  return results;
}

export function computeWaterHammer(
  project: FluidProject,
  edgeResults: Record<string, EdgeResult>
): Record<string, WaterHammerResult> {
  const results: Record<string, WaterHammerResult> = {};
  for (const edge of Object.values(project.edges)) {
    const solved = edgeResults[edge.id];
    if (!solved) continue;
    const waveSpeed = Math.sqrt(project.fluid.bulkModulus / project.fluid.density);
    const geometry = resolveEdgeGeometry(edge, project.nodes, solved.flowRate);
    const length = geometry?.effectiveLength ?? edge.length;
    results[edge.id] = {
      waveSpeed,
      pressureRise: project.fluid.density * waveSpeed * Math.abs(solved.velocity),
      criticalClosureTime: (2 * length) / waveSpeed
    };
  }
  return results;
}
