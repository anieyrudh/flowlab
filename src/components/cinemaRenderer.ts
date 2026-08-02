import * as THREE from "three";
import type { DecodedDerivedVisualization } from "../results/derived";
import { datasetBounds, fieldValuesForOverlay, fieldValuesForSelection, type ResultFieldSelection, type ResultVectorComponent } from "../results/vtk";
import { maxAbsoluteOf } from "../numeric";
import type {
  ChannelShape,
  FluidEdge,
  FluidNode,
  FluidProject,
  OverlayMode,
  PipePortId,
  ResultColorMap,
  SimulationResult,
  Vec2,
  VtkResultDataset
} from "../types";
import { hydraulicDiameter } from "../physics/hydraulics";
import {
  SCHEMATIC_GRID_SIZE,
  buildSchematicRoutes,
  clampCinemaPitch,
  normalizeCinemaCamera,
  normalizeYawDegrees,
  pointOnPolyline,
  polylineLength,
  roundPolylineCorners,
  schematicPortPosition,
  tangentOnPolyline,
  type CinemaCameraState,
  type WireRoute
} from "./viewportModel";
import { recordEditorMetric } from "../performance/editorProfiler";
import { addStreamlineScene } from "../streamlines/render";
import type { StreamlineDisplayOptions, StreamlineResult } from "../streamlines/types";
import { buildDerivedPresentation, type DerivedPresentationOptions } from "./derivedRenderer";

export type CinemaPick =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | { kind: "port"; nodeId: string; port: PipePortId; point: Vec2 }
  | { kind: "rotate"; nodeId: string };

export type CinemaResultProbe = {
  point: [number, number, number];
  ownerCellIndex: number;
  nearestPointIndex: number;
  trianglePointIndices: [number, number, number];
  barycentricWeights: [number, number, number];
};

export type CinemaRuntime = {
  center: Vec2;
  worldScale: number;
  pickableCount: number;
  projectedNodePositions: Record<string, { x: number; y: number }>;
  engine: string;
  derivedFallback: "none" | "webgl2-required";
  render: (time: number, advancePreview?: boolean) => void;
  updateModel: (project: FluidProject, result: SimulationResult) => void;
  fitCamera: (settings: CinemaCameraState, project: FluidProject) => CinemaCameraState;
  updateCamera: (settings: CinemaCameraState) => void;
  resize: () => void;
  dispose: () => void;
  pickAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => CinemaPick | null;
  probeAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => CinemaResultProbe | null;
  pointAt: (event: Pick<PointerEvent, "clientX" | "clientY">) => Vec2 | null;
};

const ports: PipePortId[] = ["inlet", "outlet", "north", "south"];

const paletteByOverlay: Record<OverlayMode, string[]> = {
  velocity: ["#0ad7ff", "#62f3bd", "#ffe15c", "#ff6f3d"],
  pressure: ["#284dff", "#04c6ff", "#f7d84b", "#ff4d5e"],
  reynolds: ["#57e389", "#ffd166", "#ff7a45", "#ff4d6d"],
  temperature: ["#4cc9f0", "#f7e733", "#ff8c42", "#ff365e"],
  phase: ["#3a86ff", "#80ffdb", "#f4d35e", "#ee6055"],
  residuals: ["#8b5cf6", "#c4b5fd", "#fb7185", "#f43f5e"],
  geometry: ["#8aa0b8", "#b9c7d8", "#ffffff", "#f5c542"]
};

const resultColorPalettes: Record<ResultColorMap, string[]> = {
  turbo: ["#2b4cff", "#00c2ff", "#67f3a5", "#ffe15c", "#ff6a3a", "#c5164f"],
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  thermal: ["#18206f", "#1954d2", "#1eb6ff", "#f7e733", "#ff8c42", "#d62839"],
  grayscale: ["#17212b", "#4b5d70", "#8da1b5", "#d6e2ec", "#ffffff"]
};

/* --- One subject at a time ----------------------------------------------------
 *
 * Two unrelated things used to share this scene:
 *   * the SOLVED DOMAIN - the VTK dataset the solver actually produced. Under
 *     the default `planar-2d` mesh mode that domain genuinely is a one-cell-thick
 *     channel, so a flat coloured slab is the honest picture of it.
 *   * the SCHEMATIC NETWORK - the pipe layout the user drew. It illustrates the
 *     layout; it is never a solved field, and no solver ever meshed it.
 *
 * Styling them differently was not enough. Drawn together they invited the only
 * reading a viewer can take from two objects sharing one space: that they are
 * the same object seen two ways, at one scale, in one frame of reference. For a
 * `planar-2d` case that reading is simply false - a flat slab is not the round
 * bent pipe beside it - and the slab was additionally *stretched to the drawing's
 * bounding box*, which manufactured the correspondence rather than merely
 * implying it.
 *
 * So the scene now has one subject at a time. Before a result exists the drawn
 * network is all there is and it is the subject. The moment a solved surface
 * exists the network is not drawn at all: the data is the subject, it is sized
 * to what the camera frames rather than to the drawing, and an in-scene caption
 * says what the domain is and what it is not. Nothing is lost by dropping the
 * network here - `SimulationCanvas` already refuses to pick schematic geometry
 * once a result is loaded, so by then it was decoration that could not even be
 * clicked. The layout is still one click away in the Schematic and Split views.
 */

/**
 * World-space Z the solved surface is lifted to. `probeAt` and the derived
 * overlay reuse it so physical coordinates round-trip exactly, and it keeps the
 * data clear of the ground reference drawn beneath the schematic.
 */
export const RESULT_SURFACE_Z_OFFSET = -0.24;

/** Above this triangle count the boundary outline is skipped to protect frame time. */
const MAX_OUTLINE_TRIANGLES = 120_000;

/** Crease angle above which the solved domain's outline draws an interior edge. */
const OUTLINE_CREASE_DEGREES = 30;

const flowParticleVertexShader = `
  uniform float uTime;
  attribute float aPhase;
  attribute vec3 aColor;
  varying vec3 vColor;
  void main() {
    vColor = aColor;
    vec3 p = position;
    p.z += sin(uTime * 2.6 + aPhase * 6.28318) * 0.035;
    vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = 6.6 * (1.0 + 0.38 * sin(uTime * 4.0 + aPhase * 8.0));
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const flowParticleFragmentShader = `
  varying vec3 vColor;
  void main() {
    vec2 uv = gl_PointCoord - vec2(0.5);
    // Held well below full alpha: these ticks illustrate flow direction on the
    // drawn network, they are not sampled field data, so they must not read as
    // brightly as the solved surface.
    float alpha = smoothstep(0.5, 0.16, length(uv)) * 0.5;
    gl_FragColor = vec4(vColor, alpha);
  }
`;

function valueColor(value: number, max: number, colors: string[]): string {
  const t = Math.max(0, Math.min(0.999, Math.abs(value) / Math.max(max, 1e-9)));
  return colors[Math.floor(t * colors.length)];
}

function overlayValueColor(value: number, max: number, overlay: OverlayMode): string {
  return valueColor(value, max, paletteByOverlay[overlay]);
}

function resultValueColor(value: number, max: number, colorMap: ResultColorMap): string {
  return valueColor(value, max, resultColorPalettes[colorMap]);
}

/* --- Constant shading ---------------------------------------------------------
 *
 * This view is a diagram of a geometry, not a photograph of one, and every
 * lighting model that describes form by orientation lies about that geometry:
 * two identical pipes pointing different ways pick up different amounts of key
 * light, so they read as different pipes. A directional/hemispherical rig makes
 * the hue consistent but keeps that orientation dependence; only constant
 * shading removes it.
 *
 * So: the drawn network is unlit throughout, and the one lit material in the
 * scene that this module does not own - the presentation-only derived iso
 * surface built by `derivedRenderer` - is lit by a single ambient light. Ambient
 * irradiance has no direction, so even a `MeshStandardMaterial` under this rig
 * shades uniformly across every face. Depth is carried instead by outlines,
 * wireframe edges and the discrete tone ladder below.
 */

/**
 * Ambient intensity that renders a lit material at its own albedo.
 *
 * three.js feeds ambient light straight through as irradiance and the physical
 * BRDF divides diffuse by PI, so PI is the intensity at which a surface comes
 * back the colour it was authored as - no brighter, no dimmer, and the same on
 * every face.
 */
export const CINEMA_AMBIENT_INTENSITY = Math.PI;

/**
 * The scene's whole lighting model: one omnidirectional light, so no surface's
 * colour can depend on which way it faces or where it sits.
 */
export function createCinemaAmbientLight(): THREE.AmbientLight {
  const ambient = new THREE.AmbientLight(0xffffff, CINEMA_AMBIENT_INTENSITY);
  ambient.name = "Cinema constant light";
  return ambient;
}

/**
 * The tone ladder that replaces specular falloff.
 *
 * Depth in a constant-shaded drawing comes from deliberate, quantised value
 * differences between *parts* - a casing against its core, a body against its
 * trim - never from where a part happens to be pointing. Four steps is enough
 * to separate near from far without anyone mistaking a step for a measurement.
 */
export const CINEMA_TONE_STEPS = { recessed: 0.52, shadow: 0.74, base: 1, raised: 1.28 } as const;

export type CinemaToneStep = keyof typeof CINEMA_TONE_STEPS;

/**
 * A colour moved one rung up or down the ladder. Pure and position-free: the
 * same input colour and step always produce the same output, which is what lets
 * two identical pipes be drawn identically wherever they sit.
 */
export function steppedTone(color: THREE.ColorRepresentation, step: CinemaToneStep): THREE.Color {
  const factor = CINEMA_TONE_STEPS[step];
  const shaded = new THREE.Color(color);
  return shaded.setRGB(
    Math.min(1, shaded.r * factor),
    Math.min(1, shaded.g * factor),
    Math.min(1, shaded.b * factor)
  );
}

/**
 * Base for any surface of the drawn network.
 *
 * Unlit and un-fogged, so the pixel depends only on the material - not on the
 * surface normal, not on distance from the camera, not on where in the scene
 * the object was placed.
 */
export function createConstantSurfaceMaterial(options: {
  color: THREE.ColorRepresentation;
  opacity?: number;
  depthWrite?: boolean;
  side?: THREE.Side;
  wireframe?: boolean;
}): THREE.MeshBasicMaterial {
  const opacity = options.opacity ?? 1;
  const material = new THREE.MeshBasicMaterial({
    color: options.color,
    wireframe: options.wireframe ?? false,
    transparent: opacity < 1,
    opacity,
    side: options.side ?? THREE.FrontSide,
    depthWrite: options.depthWrite ?? false
  });
  material.fog = false;
  return material;
}

/** Outline stroke: the drawing's own way of saying where a solid stops. */
export function createConstantOutlineMaterial(color: THREE.ColorRepresentation, opacity: number): THREE.LineBasicMaterial {
  const material = new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: false });
  material.fog = false;
  return material;
}

/**
 * Material for the solved surface.
 *
 * Deliberately unlit, fully opaque, un-fogged and un-tone-mapped: the pixel a
 * reader sees has to be the colour-map colour for the sampled value. The
 * previous material was translucent (opacity 0.82) and picked up the scene fog,
 * so every value was blended toward the dark background before it reached the
 * eye and the picture quietly disagreed with the legend. Shading it would do the
 * same thing by another route.
 */
export function createResultSurfaceMaterial(): THREE.MeshBasicMaterial {
  const material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, depthWrite: true });
  material.fog = false;
  material.toneMapped = false;
  material.polygonOffset = true; // let the boundary outline win the depth test
  material.polygonOffsetFactor = 1;
  material.polygonOffsetUnits = 1;
  return material;
}

/** Crisp outline around the solved domain, so the slab reads as a measured extent. */
export function createResultBoundaryMaterial(): THREE.LineBasicMaterial {
  const material = new THREE.LineBasicMaterial({ color: 0xf2f8ff, transparent: true, opacity: 0.92 });
  material.fog = false;
  material.toneMapped = false;
  return material;
}

export type SchematicPipeMaterials = {
  cage: THREE.MeshBasicMaterial;
  core: THREE.MeshBasicMaterial;
  outline: THREE.LineBasicMaterial;
};

/**
 * Materials for one drawn pipe.
 *
 * The outer surface is a ghosted wireframe cage - a treatment no solver ever
 * outputs - so the drawn network reads as a diagram at a glance. The core
 * carries the network overlay colour but stays translucent, so it can never look
 * more authoritative than the opaque solved surface beneath it.
 *
 * All three are unlit. The core used to be a `MeshStandardMaterial`, which meant
 * a pipe running north picked up a different amount of key light than an
 * identical pipe running east and the two read as different diameters. Cage and
 * core sit one tone step apart so the tube still has an inside and an outside
 * without any of that depending on which way it points.
 */
export function createSchematicPipeMaterials(color: THREE.Color, active: boolean): SchematicPipeMaterials {
  const cage = createConstantSurfaceMaterial({
    color: active ? 0xffd98a : 0x7ea8c2,
    wireframe: true,
    opacity: active ? 0.36 : 0.2
  });
  const core = createConstantSurfaceMaterial({
    color: steppedTone(color, active ? "base" : "shadow"),
    opacity: active ? 0.5 : 0.34
  });
  const outline = createConstantOutlineMaterial(steppedTone(color, "raised"), active ? 0.7 : 0.44);
  return { cage, core, outline };
}

function degreesToRadians(degrees: number) {
  return (degrees * Math.PI) / 180;
}

function aimHandlePosition(node: FluidNode): Vec2 {
  const angle = degreesToRadians(node.rotation ?? 0);
  return {
    x: node.position.x + Math.cos(angle) * 46,
    y: node.position.y + Math.sin(angle) * 46
  };
}

function projectCenter(project: FluidProject): Vec2 {
  const nodes = Object.values(project.nodes);
  if (!nodes.length) return { x: 0, y: 0 };
  const minX = Math.min(...nodes.map((node) => node.position.x));
  const maxX = Math.max(...nodes.map((node) => node.position.x));
  const minY = Math.min(...nodes.map((node) => node.position.y));
  const maxY = Math.max(...nodes.map((node) => node.position.y));
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
}

function worldFromNetwork(point: Vec2, center: Vec2, worldScale: number, z = 0) {
  return new THREE.Vector3((point.x - center.x) / worldScale, -(point.y - center.y) / worldScale, z);
}

function networkFromWorld(point: THREE.Vector3, center: Vec2, worldScale: number): Vec2 {
  return {
    x: point.x * worldScale + center.x,
    y: -point.y * worldScale + center.y
  };
}

/* --- One route, drawn twice --------------------------------------------------
 *
 * `buildSchematicRoutes` is the schematic's own router. Running it here, rather
 * than drawing a straight tube between the two port positions, is the whole
 * point: a pipe that turns two corners on the schematic turns the same two
 * corners in 3D, because both views are reading the same polyline.
 */

/** Corner radius of a bend, as a multiple of the pipe's own radius. */
export const PIPE_BEND_RADIUS_SCALE = 2.6;
/** Points sampled around each bend. Enough that a right angle reads as a swept elbow. */
const PIPE_BEND_SEGMENTS = 6;

/** Every pipe route in the project, keyed by edge id, exactly as the schematic draws them. */
function routesByEdge(project: FluidProject): Map<string, Vec2[]> {
  return new Map(buildSchematicRoutes(project).map((route: WireRoute) => [route.id, route.points]));
}

/* --- What a schematic pixel is worth ------------------------------------------
 *
 * The drawn plan is the schematic's, and the schematic is laid out in pixels on a
 * 40-unit grid. A pixel is not a length: two components sit 200 apart because
 * that is where the user dragged them, not because the pipe between them is 200
 * of anything. So the plan distance between two components genuinely cannot be
 * read as the pipe's physical length, and nothing here pretends otherwise.
 *
 * What every other physical quantity needs, though, is a *rate*: how many metres
 * a pixel stands for, so that a bore and a rise can be drawn in the same units
 * the plan is drawn in. That rate is taken from the model rather than invented -
 *
 *     metresPerPixel = (sum of every edge's length) / (sum of every routed run)
 *
 * - which is the one scale that makes the network's whole drawn run equal its
 * whole specified length. Elevation is then a true height against that plan, and
 * changing any edge's length moves it, because every edge is in that sum.
 *
 * A single pipe's *run* still cannot be honoured, because its two ends are pinned
 * to two components the user placed. What is honoured per pipe is the ratio that
 * actually governs it: `pipeWorldRadius` draws each pipe at its own true
 * length-to-bore ratio, so a pipe told it is ten times longer is drawn ten times
 * more slender. That is the only way length can show on a tube whose path is
 * fixed, and it is the quantity friction is proportional to.
 */

/**
 * Metres a schematic pixel stands for when no edge carries a usable length: one
 * grid cell to the metre. Only reachable by a project with no routable pipe, and
 * stated so the scene still has a defined scale rather than a hidden zero.
 */
export const FALLBACK_METRES_PER_PIXEL = 1 / SCHEMATIC_GRID_SIZE;

export type NetworkMetricScale = {
  /** Metres one schematic pixel stands for. */
  metresPerPixel: number;
  /** World units one metre is drawn at. */
  worldPerMetre: number;
  /** False when no edge had both a route and a length, so the fallback was used. */
  fromModel: boolean;
};

/**
 * The project's own metres-per-pixel, and the world-per-metre it implies.
 *
 * Pure, so the scale the whole scene hangs off can be pinned down in a test
 * without a renderer.
 */
export function networkMetricScale(
  project: FluidProject,
  routes: Map<string, Vec2[]>,
  worldScale: number
): NetworkMetricScale {
  let drawnPixels = 0;
  let physicalMetres = 0;
  for (const edge of Object.values(project.edges)) {
    const route = routes.get(edge.id);
    if (!route || route.length < 2) continue;
    const drawn = polylineLength(route);
    if (!(drawn > 0) || !Number.isFinite(edge.length) || !(edge.length > 0)) continue;
    drawnPixels += drawn;
    physicalMetres += edge.length;
  }
  const fromModel = drawnPixels > 0 && physicalMetres > 0;
  const metresPerPixel = fromModel ? physicalMetres / drawnPixels : FALLBACK_METRES_PER_PIXEL;
  return { metresPerPixel, worldPerMetre: 1 / (metresPerPixel * Math.max(worldScale, 1e-9)), fromModel };
}

/** The bore the solver sees: a diameter for round pipe, the hydraulic diameter for a duct. */
export function channelDrawnDiameter(shape: ChannelShape): number {
  return shape.kind === "circular" ? shape.diameter : hydraulicDiameter(shape);
}

/**
 * Smallest radius a pipe is swept at, in world units.
 *
 * Purely a guard against degenerate geometry - a zero-radius sweep has no
 * surface and no normals - and deliberately far below anything a real bore
 * reaches, so it can never quietly become the scale. The 0.065 it replaces was
 * the opposite: it drew every pipe under a 76 mm bore at exactly the same size,
 * which is most of a plant's pipework, so half the diameter response was being
 * clamped away before it reached the screen.
 *
 * Nothing is lost visually at this size: the pipe's centre line is a `Line`, one
 * pixel wide whatever the bore, so an extremely slender run still reads as a run.
 */
export const MIN_PIPE_WORLD_RADIUS = 1e-4;

/**
 * World-space radius of one drawn pipe.
 *
 * `drawnWorldLength` is how long the routed path is in world units. Dividing the
 * true half-bore by the true length and multiplying by that is what makes the
 * ratio on screen the model's own: radius / run == (diameter / 2) / length.
 */
export function pipeWorldRadius(options: {
  shape: ChannelShape;
  physicalLength: number;
  drawnWorldLength: number;
}): number {
  const diameter = channelDrawnDiameter(options.shape);
  const usable =
    Number.isFinite(diameter)
    && diameter > 0
    && Number.isFinite(options.physicalLength)
    && options.physicalLength > 0
    && options.drawnWorldLength > 0;
  if (!usable) return MIN_PIPE_WORLD_RADIUS;
  return Math.max(MIN_PIPE_WORLD_RADIUS, (diameter / 2) * (options.drawnWorldLength / options.physicalLength));
}

/** World-space height of a component, from its elevation in metres. */
export function nodeWorldZ(node: Pick<FluidNode, "elevation">, worldPerMetre: number): number {
  return Number.isFinite(node.elevation) ? node.elevation * worldPerMetre : 0;
}

/*
 * Drawing lifts, in world units, applied on top of a component's own elevation.
 * They separate the symbol from the pipe it sits on and the handle from both;
 * they are stacking order, not height, which is why elevation is added to them
 * rather than replaced by them.
 */
const NODE_BODY_Z = 0.08;
const NODE_PORT_Z = 0.1;
const NODE_HANDLE_Z = 0.16;

/** Where a pipe starts and ends in height, so the run between them can ramp. */
export function edgeWorldElevations(
  edge: Pick<FluidEdge, "from" | "to">,
  nodes: Record<string, FluidNode>,
  worldPerMetre: number
): { startZ: number; endZ: number } {
  const from = nodes[edge.from];
  const to = nodes[edge.to];
  return {
    startZ: from ? nodeWorldZ(from, worldPerMetre) : 0,
    endZ: to ? nodeWorldZ(to, worldPerMetre) : 0
  };
}

/**
 * The routed polyline lifted into world space, with its corners filleted so the
 * swept tube turns through a bend instead of a knife edge, and its height ramped
 * from the component it leaves to the component it reaches. `worldFromNetwork`
 * mirrors y, which is a reflection, so the fillets stay circular.
 *
 * The ramp is by travelled distance rather than by point index, so a run that
 * turns two corners still climbs evenly instead of doing all its climbing in
 * whichever segment happened to be sampled most.
 */
function routeWorldPath(
  routePoints: readonly Vec2[],
  pipeRadius: number,
  center: Vec2,
  worldScale: number,
  startZ = 0,
  endZ = 0
): THREE.Vector3[] {
  const bendRadius = pipeRadius * PIPE_BEND_RADIUS_SCALE * worldScale;
  const rounded = roundPolylineCorners(routePoints, bendRadius, PIPE_BEND_SEGMENTS);
  const total = polylineLength(rounded);
  let travelled = 0;
  return rounded.map((point, index) => {
    if (index > 0) {
      const previous = rounded[index - 1];
      travelled += Math.hypot(point.x - previous.x, point.y - previous.y);
    }
    const t = total > 0 ? Math.min(1, travelled / total) : 0;
    return worldFromNetwork(point, center, worldScale, startZ + (endZ - startZ) * t);
  });
}

/**
 * Sweeps a circular section along an open path.
 *
 * Frames are parallel-transported from a seed normal rather than taken from the
 * Frenet frame, so a straight run has a defined cross-section instead of an
 * undefined one, and the tube does not twist as it turns. The network lies in a
 * plane, so seeding with the plane normal gives every pipe in the scene the same
 * facet orientation - part of why two identical pipes read as identical however
 * they are pointing.
 *
 * Kept free of any renderer state so it can be unit-tested without WebGL.
 */
export function buildSweptTubeGeometry(
  path: readonly THREE.Vector3[],
  radius: number,
  radialSegments = 12
): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  const points: THREE.Vector3[] = [];
  for (const point of path) {
    const previous = points[points.length - 1];
    if (previous && previous.distanceToSquared(point) <= 1e-14) continue;
    points.push(point.clone());
  }
  if (points.length < 2) return geometry;

  const sides = Math.max(3, Math.round(radialSegments));
  const tangents = points.map((point, index) => {
    const before = points[Math.max(0, index - 1)];
    const after = points[Math.min(points.length - 1, index + 1)];
    const tangent = after.clone().sub(before);
    return tangent.lengthSq() <= 1e-20 ? new THREE.Vector3(1, 0, 0) : tangent.normalize();
  });

  // Seed off the network plane's own normal; fall back only if a run is somehow
  // perpendicular to the plane, which an orthogonal 2D route never is.
  let normal = new THREE.Vector3(0, 0, 1);
  if (Math.abs(normal.dot(tangents[0])) > 0.99) normal = new THREE.Vector3(0, 1, 0);

  const positions: number[] = [];
  const vertexNormals: number[] = [];
  points.forEach((point, index) => {
    const tangent = tangents[index];
    // Parallel transport: keep as much of the previous frame as this tangent allows.
    normal = normal.clone().sub(tangent.clone().multiplyScalar(normal.dot(tangent)));
    if (normal.lengthSq() <= 1e-12) {
      normal = new THREE.Vector3(0, 0, 1).sub(tangent.clone().multiplyScalar(tangent.z));
      if (normal.lengthSq() <= 1e-12) normal = new THREE.Vector3(0, 1, 0);
    }
    normal.normalize();
    const binormal = tangent.clone().cross(normal).normalize();
    for (let side = 0; side < sides; side += 1) {
      const angle = (side / sides) * Math.PI * 2;
      const offset = normal
        .clone()
        .multiplyScalar(Math.cos(angle))
        .add(binormal.clone().multiplyScalar(Math.sin(angle)));
      positions.push(point.x + offset.x * radius, point.y + offset.y * radius, point.z + offset.z * radius);
      vertexNormals.push(offset.x, offset.y, offset.z);
    }
  });

  const indices: number[] = [];
  for (let ring = 0; ring < points.length - 1; ring += 1) {
    for (let side = 0; side < sides; side += 1) {
      const nextSide = (side + 1) % sides;
      const a = ring * sides + side;
      const b = ring * sides + nextSide;
      const c = (ring + 1) * sides + side;
      const d = (ring + 1) * sides + nextSide;
      indices.push(a, c, b, b, c, d);
    }
  }

  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(vertexNormals, 3));
  geometry.setIndex(indices);
  return geometry;
}

function orientBetweenPoints(object: THREE.Object3D, start: THREE.Vector3, end: THREE.Vector3) {
  const midpoint = start.clone().add(end).multiplyScalar(0.5);
  const direction = end.clone().sub(start);
  object.position.copy(midpoint);
  object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
}

function createPipeMesh(start: THREE.Vector3, end: THREE.Vector3, radius: number, material: THREE.Material, radialSegments = 36) {
  const length = Math.max(start.distanceTo(end), 0.01);
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, radialSegments, 1, true), material);
  mesh.userData.pipeBaseLength = length;
  orientBetweenPoints(mesh, start, end);
  return mesh;
}

/* --- Saying what the domain is ------------------------------------------------
 *
 * The caption is measured off the dataset, never off `project.solver.meshMode`.
 * A loaded result is not always the project's own mesh - it can be an import or
 * the bundled fixture - so a caption written from the *settings* would confidently
 * describe geometry that is not on screen. Counting the dataset's own layers and
 * extent can only ever describe what is actually being drawn, and for a genuine
 * `planar-2d` run it arrives at the same sentence the mesh mode would have:
 * one cell thick, and not the round pipe in the drawing.
 */

export type SolvedDomainShape =
  /** Zero extent across one axis: a surface mesh with no thickness at all. */
  | "sheet"
  /** One cell across its thinnest axis: the default `planar-2d` channel. */
  | "slab"
  /** Resolved across all three axes. */
  | "volume";

export type SolvedDomainDescription = {
  shape: SolvedDomainShape;
  /** Distinct point coordinates along the thinnest axis. 1 is flat, 2 is one cell thick. */
  layers: number;
  /** Extent along each axis, in the dataset's own coordinates. */
  extent: [number, number, number];
  /** Index of the thinnest axis: 0 = x, 1 = y, 2 = z. */
  thinAxis: 0 | 1 | 2;
  /** Caption lines, most significant first. */
  lines: string[];
};

/** Three significant figures, with no trailing zeroes and no invented precision. */
function formatDatasetLength(value: number): string {
  if (!Number.isFinite(value) || value === 0) return "0";
  return String(Number(value.toPrecision(3)));
}

/**
 * Distinct coordinates along one axis, counted up to `limit`.
 *
 * Stops early because the only question asked of it is "one layer, two, or more
 * than two", and a solved dataset can carry hundreds of thousands of points.
 */
function distinctAxisValues(dataset: VtkResultDataset, axis: 0 | 1 | 2, tolerance: number, limit = 3): number {
  const seen: number[] = [];
  for (const point of dataset.points) {
    const value = point[axis];
    if (seen.some((existing) => Math.abs(existing - value) <= tolerance)) continue;
    seen.push(value);
    if (seen.length >= limit) break;
  }
  return seen.length;
}

/**
 * What the solved domain actually is, measured from the dataset itself.
 *
 * Everything here is an observation about the points on screen, so the caption
 * it produces cannot overstate what the solver did.
 */
export function describeSolvedDomain(dataset: VtkResultDataset): SolvedDomainDescription {
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (const point of dataset.points) {
    for (let axis = 0; axis < 3; axis += 1) {
      if (point[axis] < min[axis]) min[axis] = point[axis];
      if (point[axis] > max[axis]) max[axis] = point[axis];
    }
  }
  const extent = ([0, 1, 2] as const).map((axis) =>
    Number.isFinite(min[axis]) && Number.isFinite(max[axis]) ? max[axis] - min[axis] : 0
  ) as [number, number, number];
  const widest = Math.max(...extent);
  const thinAxis = (extent[0] <= extent[1] && extent[0] <= extent[2] ? 0 : extent[1] <= extent[2] ? 1 : 2) as 0 | 1 | 2;
  // Relative tolerance: a mesh authored in millimetres and one authored in metres
  // describe the same shape, so "distinct" has to mean distinct at this domain's
  // own scale rather than at a fixed absolute one.
  const layers = dataset.points.length === 0 ? 0 : distinctAxisValues(dataset, thinAxis, Math.max(widest, 1e-12) * 1e-9);

  const shape: SolvedDomainShape = extent[thinAxis] === 0 ? "sheet" : layers <= 2 ? "slab" : "volume";
  const headline =
    shape === "sheet"
      ? "Flat 2-D domain — a surface with no thickness"
      : shape === "slab"
        ? "Flat 2-D domain — one cell thick"
        : "3-D volume domain";
  const disclaimer =
    shape === "volume"
      ? "Solver mesh · the schematic pipe drawing is not shown"
      : "This is not the round pipe drawn in the schematic";

  return {
    shape,
    layers,
    extent,
    thinAxis,
    lines: [
      "SOLVED DOMAIN",
      headline,
      disclaimer,
      `${extent.map(formatDatasetLength).join(" × ")} in dataset units · ${dataset.cells.length.toLocaleString("en-US")} cell${dataset.cells.length === 1 ? "" : "s"}`
    ]
  };
}

export type ExteriorCellFace = {
  pointIndices: number[];
  ownerCellIndex: number;
};

function cellFaces(cell: number[], cellType: number): number[][] {
  const face = (...indices: number[]) => indices.map((index) => cell[index]);
  if (cellType === 5 && cell.length === 3) return [face(0, 1, 2)];
  if ((cellType === 7 || cellType === 9) && cell.length >= 3) return [[...cell]];
  if (cellType === 10 && cell.length === 4) {
    return [face(0, 2, 1), face(0, 1, 3), face(1, 2, 3), face(2, 0, 3)];
  }
  if (cellType === 12 && cell.length === 8) {
    return [face(0, 3, 2, 1), face(4, 5, 6, 7), face(0, 1, 5, 4), face(1, 2, 6, 5), face(2, 3, 7, 6), face(3, 0, 4, 7)];
  }
  if (cellType === 13 && cell.length === 6) {
    return [face(0, 2, 1), face(3, 4, 5), face(0, 1, 4, 3), face(1, 2, 5, 4), face(2, 0, 3, 5)];
  }
  if (cellType === 14 && cell.length === 5) {
    return [face(0, 3, 2, 1), face(0, 1, 4), face(1, 2, 4), face(2, 3, 4), face(3, 0, 4)];
  }
  return [];
}

function orientedAwayFromCell(face: number[], cell: number[], points: VtkResultDataset["points"]): number[] {
  const unique = Array.from(new Set(face));
  if (unique.length < 3) return [];
  const a = new THREE.Vector3(...points[face[0]]);
  const b = new THREE.Vector3(...points[face[1]]);
  const c = new THREE.Vector3(...points[face[2]]);
  const normal = b.clone().sub(a).cross(c.clone().sub(a));
  if (normal.lengthSq() <= 1e-20) return [];
  const faceCenter = face.reduce((sum, index) => sum.add(new THREE.Vector3(...points[index])), new THREE.Vector3()).multiplyScalar(1 / face.length);
  const cellCenter = cell.reduce((sum, index) => sum.add(new THREE.Vector3(...points[index])), new THREE.Vector3()).multiplyScalar(1 / cell.length);
  return normal.dot(cellCenter.sub(faceCenter)) > 0 ? [...face].reverse() : face;
}

export function extractExteriorCellFaces(dataset: VtkResultDataset): ExteriorCellFace[] {
  const byKey = new Map<string, { count: number; face: ExteriorCellFace }>();
  dataset.cells.forEach((cell, ownerCellIndex) => {
    const cellType = dataset.cellTypes[ownerCellIndex];
    cellFaces(cell, cellType).forEach((candidate) => {
      const pointIndices = orientedAwayFromCell(candidate, cell, dataset.points);
      if (pointIndices.length < 3) return;
      const key = [...pointIndices].sort((left, right) => left - right).join(":");
      const existing = byKey.get(key);
      if (existing) {
        existing.count += 1;
      } else {
        byKey.set(key, { count: 1, face: { pointIndices, ownerCellIndex } });
      }
    });
  });
  return Array.from(byKey.values())
    .filter((entry) => entry.count === 1)
    .map((entry) => entry.face);
}

export function exteriorTriangleCount(dataset: VtkResultDataset): number {
  return extractExteriorCellFaces(dataset).reduce((count, face) => count + Math.max(0, face.pointIndices.length - 2), 0);
}

export type ResultSurfaceTriangle = {
  pointIndices: [number, number, number];
  ownerCellIndex: number;
};

export function resultSurfaceTriangles(dataset: VtkResultDataset): ResultSurfaceTriangle[] {
  return extractExteriorCellFaces(dataset).flatMap(({ pointIndices, ownerCellIndex }) => {
    const triangles: ResultSurfaceTriangle[] = [];
    for (let index = 1; index < pointIndices.length - 1; index += 1) {
      triangles.push({
        pointIndices: [pointIndices[0], pointIndices[index], pointIndices[index + 1]],
        ownerCellIndex
      });
    }
    return triangles;
  });
}

function fieldValueForDatasetPoint(values: number[], location: "point" | "cell", pointIndex: number, cellIndex: number, cell: number[]) {
  if (location === "point") return values[pointIndex] ?? 0;
  return values[cellIndex] ?? cell.reduce((sum, index) => sum + (values[index] ?? 0), 0) / Math.max(cell.length, 1);
}

function addResultSurfaceMesh(
  scene: THREE.Scene,
  dataset: VtkResultDataset,
  overlay: OverlayMode,
  resultFieldSelection: ResultFieldSelection | null,
  resultVectorComponent: ResultVectorComponent,
  resultColorMap: ResultColorMap,
  worldSpan: number
): {
  surface: THREE.Mesh;
  triangles: ResultSurfaceTriangle[];
  bounds: ReturnType<typeof datasetBounds>;
  meshScale: number;
} | null {
  const fieldValues = resultFieldSelection ? fieldValuesForSelection(dataset, resultFieldSelection, resultVectorComponent) : fieldValuesForOverlay(dataset, overlay);
  if (!fieldValues || dataset.cells.length === 0 || dataset.points.length === 0) return null;
  const bounds = datasetBounds(dataset);
  const maxValue = maxAbsoluteOf(fieldValues.values, 1e-9);
  const positions: number[] = [];
  const colors: number[] = [];
  const meshScale = worldSpan / bounds.span;
  const triangles = resultSurfaceTriangles(dataset);

  triangles.forEach(({ pointIndices, ownerCellIndex }) => {
    const cell = dataset.cells[ownerCellIndex];
    pointIndices.forEach((pointIndex) => {
      const point = dataset.points[pointIndex];
      const color = new THREE.Color(
        resultValueColor(fieldValueForDatasetPoint(fieldValues.values, fieldValues.location, pointIndex, ownerCellIndex, cell), maxValue, resultColorMap)
      );
      positions.push(
        (point[0] - bounds.center[0]) * meshScale,
        (point[1] - bounds.center[1]) * meshScale,
        (point[2] - bounds.center[2]) * meshScale + RESULT_SURFACE_Z_OFFSET
      );
      colors.push(color.r, color.g, color.b);
    });
  });

  if (positions.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const surface = new THREE.Mesh(geometry, createResultSurfaceMaterial());
  surface.name = `Solved domain: VTK ${fieldValues.field} exterior surface`;

  // Outline the solved domain's own boundary. `EdgesGeometry` emits unshared
  // edges plus creases sharper than the threshold, so for the default
  // one-cell-thick `planar-2d` channel this traces the real extent of the solved
  // region - the flat slab reads as a measured patch instead of a stray
  // backdrop - while a curved axisymmetric domain keeps its smooth faceting
  // undrawn rather than being covered in a mesh wireframe.
  if (triangles.length <= MAX_OUTLINE_TRIANGLES) {
    const boundary = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, OUTLINE_CREASE_DEGREES), createResultBoundaryMaterial());
    boundary.name = "Solved domain boundary";
    boundary.renderOrder = 2;
    surface.add(boundary);
  }
  scene.add(surface);
  return { surface, triangles, bounds, meshScale };
}

/** Supersampling factor for the caption texture, so its type stays crisp when zoomed in. */
const CAPTION_SUPERSAMPLE = 3;

/**
 * Type ramp for the caption, in CSS pixels. Ordered to match
 * `SolvedDomainDescription.lines`: an eyebrow that names the object, the headline
 * that says what shape it is, then two lines of qualification.
 */
const CAPTION_LINE_STYLES: readonly { size: number; weight: number; color: string; tracking: number; gap: number }[] = [
  { size: 11, weight: 700, color: "#79a4c0", tracking: 1.8, gap: 8 },
  { size: 19, weight: 600, color: "#f2f8ff", tracking: 0, gap: 6 },
  { size: 13, weight: 500, color: "#c6d8e6", tracking: 0, gap: 3 },
  { size: 12, weight: 400, color: "#8ba4b6", tracking: 0, gap: 0 }
];

const CAPTION_FONT_STACK = `"Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`;

/** Fraction of the canvas the caption may occupy before it is scaled down to fit. */
const CAPTION_MAX_WIDTH_FRACTION = 0.52;
/** Gap between the caption and the bottom-left corner of the canvas, in CSS pixels. */
const CAPTION_SCREEN_MARGIN = 18;

export type SolvedDomainCaption = {
  sprite: THREE.Sprite;
  texture: THREE.Texture;
  /** Authored size in CSS pixels, which `layoutCaption` reproduces on screen 1:1. */
  cssWidth: number;
  cssHeight: number;
};

/**
 * The caption, drawn to a texture and parented to the camera.
 *
 * A `Sprite` because it must stay square to the reader at every yaw and pitch: a
 * label that shears with the orbit stops being readable exactly when the viewer
 * is trying hardest to work out what they are looking at. Parented to the camera
 * rather than placed in the scene because "just under the domain" is only under
 * it at one yaw - at any other the label lands across the data it is describing.
 *
 * Returns null rather than throwing when no 2D context is available, so a
 * headless or canvas-less environment loses the label and keeps the data.
 */
export function createSolvedDomainCaption(lines: readonly string[]): SolvedDomainCaption | null {
  if (lines.length === 0) return null;
  const canvas = document.createElement("canvas");
  const measuring = canvas.getContext("2d");
  if (!measuring) return null;

  const styled = lines.map((text, index) => ({ text, ...(CAPTION_LINE_STYLES[index] ?? CAPTION_LINE_STYLES[CAPTION_LINE_STYLES.length - 1]) }));
  const fontFor = (line: (typeof styled)[number]) => `${line.weight} ${line.size}px ${CAPTION_FONT_STACK}`;
  const widthOf = (line: (typeof styled)[number]) => {
    measuring.font = fontFor(line);
    return measuring.measureText(line.text).width + line.tracking * Math.max(0, line.text.length - 1);
  };

  // Wide enough for the halo below to fall inside the texture rather than being
  // clipped at its edge, which would leave a hard corner on the glow.
  const padding = 7;
  const contentWidth = Math.max(...styled.map(widthOf));
  const contentHeight = styled.reduce((total, line) => total + line.size * 1.2 + line.gap, 0);
  const cssWidth = Math.max(1, Math.ceil(contentWidth + padding * 2));
  const cssHeight = Math.max(1, Math.ceil(contentHeight + padding * 2));
  canvas.width = cssWidth * CAPTION_SUPERSAMPLE;
  canvas.height = cssHeight * CAPTION_SUPERSAMPLE;

  const context = canvas.getContext("2d");
  if (!context) return null;
  context.scale(CAPTION_SUPERSAMPLE, CAPTION_SUPERSAMPLE);
  context.textBaseline = "top";
  // A halo rather than a plate. Wherever the domain reaches into the corner the
  // caption has to stay readable, but a filled backdrop would hide the very data
  // it is annotating - a glow off the background colour costs no pixels of it.
  context.shadowColor = "rgba(2, 7, 13, 0.92)";
  context.shadowBlur = 5;
  let y = padding;
  for (const line of styled) {
    context.font = fontFor(line);
    context.fillStyle = line.color;
    if (line.tracking === 0) {
      context.fillText(line.text, padding, y);
    } else {
      // `letterSpacing` is not universal, so tracking is applied by hand.
      let x = padding;
      for (const character of line.text) {
        context.fillText(character, x, y);
        x += context.measureText(character).width + line.tracking;
      }
    }
    y += line.size * 1.2 + line.gap;
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false });
  material.fog = false;
  material.toneMapped = false;
  const sprite = new THREE.Sprite(material);
  sprite.name = "Solved domain caption";
  sprite.renderOrder = 6;
  return { sprite, texture, cssWidth, cssHeight };
}

/**
 * Where the caption sits in the camera's own space, so that it renders at its
 * authored pixel size in the bottom-left corner whatever the zoom or canvas size.
 *
 * Pure and free of renderer state: the caption's placement is arithmetic on the
 * orthographic frustum, which is the part worth pinning down in a test.
 */
export function solvedDomainCaptionPlacement(options: {
  cssWidth: number;
  cssHeight: number;
  viewWidth: number;
  viewHeight: number;
  zoom: number;
}): { width: number; height: number; x: number; y: number } {
  const { left, bottom, top } = cinemaOrthographicFrustum(options.viewWidth, options.viewHeight, options.zoom);
  // World units per CSS pixel for this frustum, which is what makes the caption
  // hold its size on screen while the scene around it zooms.
  const perPixel = (top - bottom) / Math.max(options.viewHeight, 1);
  const fit = Math.min(1, (Math.max(options.viewWidth, 1) * CAPTION_MAX_WIDTH_FRACTION) / Math.max(options.cssWidth, 1));
  const width = options.cssWidth * fit * perPixel;
  const height = options.cssHeight * fit * perPixel;
  const margin = CAPTION_SCREEN_MARGIN * perPixel;
  return { width, height, x: left + margin + width / 2, y: bottom + margin + height / 2 };
}

function canvasNdc(canvas: HTMLCanvasElement, event: Pick<PointerEvent, "clientX" | "clientY">) {
  const rect = canvas.getBoundingClientRect();
  return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -(((event.clientY - rect.top) / rect.height) * 2 - 1));
}

function projectedNodePositions(
  project: FluidProject,
  center: Vec2,
  worldScale: number,
  worldPerMetre: number,
  camera: THREE.Camera,
  width: number,
  height: number
) {
  return Object.fromEntries(
    Object.values(project.nodes).map((node) => {
      // Through the component's own elevation, so an overlay pinned to a raised
      // component lands on it rather than on the plan below it.
      const projected = worldFromNetwork(node.position, center, worldScale, NODE_BODY_Z + nodeWorldZ(node, worldPerMetre)).project(camera);
      return [
        node.id,
        {
          x: Math.round(((projected.x + 1) / 2) * width),
          y: Math.round(((-projected.y + 1) / 2) * height)
        }
      ];
    })
  );
}

/* --- Orthographic projection -------------------------------------------------
 *
 * A perspective camera makes parallel pipes converge and draws the near half of
 * a run wider than the far half, so two pipes of equal diameter measure
 * differently on screen purely because of where they sit. On a view whose job is
 * to show geometry that is not a stylistic preference, it is wrong. Orthographic
 * projection keeps a diameter a diameter and parallel runs parallel.
 */

/** World-space height the camera frames at zoom 1: the extent the old 40-degree lens saw. */
export const CINEMA_VIEW_HEIGHT = 6.48;
/**
 * How far back the camera sits. Orthographic scale is independent of distance,
 * so this only has to clear the scene; zoom drives the frustum instead.
 */
export const CINEMA_CAMERA_DISTANCE = 16;

/**
 * Fraction of the framed height the solved domain's longest axis fills at zoom 1.
 *
 * The domain used to be scaled to the drawn network's bounding box, which made a
 * measured extent depend on how far apart the user happened to drag two icons.
 * Sizing it against what the camera frames instead means the data arrives already
 * centred and already the subject. It stays under 1 so that a domain seen square
 * on still has margin around it, and so an orbit cannot swing a corner out of
 * frame; the caption needs no share of this, being pinned to the canvas instead.
 */
export const SOLVED_DOMAIN_VIEW_FILL = 0.92;

/** Longest world extent of the solved domain. Set by the camera, not by the drawing. */
export const SOLVED_DOMAIN_WORLD_SPAN = CINEMA_VIEW_HEIGHT * SOLVED_DOMAIN_VIEW_FILL;

/** Usable zoom range. Unchanged from the perspective rig, so the controls still feel the same. */
export function clampCinemaZoom(zoom: number): number {
  return Math.max(0.45, Math.min(1.8, zoom));
}

/** Half-extents of the orthographic frustum for a canvas of this size at this zoom. */
export function cinemaOrthographicFrustum(width: number, height: number, zoom: number) {
  const aspect = Math.max(width, 1) / Math.max(height, 1);
  const halfHeight = CINEMA_VIEW_HEIGHT / (2 * clampCinemaZoom(zoom));
  const halfWidth = halfHeight * aspect;
  return { left: -halfWidth, right: halfWidth, top: halfHeight, bottom: -halfHeight };
}

/**
 * Zoom that frames a network of this world extent.
 *
 * Under perspective this was a distance calculation against the field of view;
 * under orthographic it is the ratio of what the camera frames to what has to
 * fit, which is the same question with the lens taken out of it. The floor keeps
 * a two-component sketch from being magnified until one pipe fills the panel.
 */
export function cinemaFitZoom(worldSpan: number): number {
  const required = Math.max(CINEMA_VIEW_HEIGHT / 2, Math.max(worldSpan, 0) * 1.3);
  return Math.max(0.25, Math.min(4, CINEMA_VIEW_HEIGHT / required));
}

/* --- Every plane, including the poles ------------------------------------------
 *
 * The basis used to be handed to `lookAt` as world +Z and left to it to build the
 * image plane from. That works right up to the moment the view direction lines up
 * with +Z - a plan view - where `up x depth` is the zero vector and the framing
 * becomes whatever the fallback happens to be. The pitch clamp of 78 degrees
 * existed to stay away from that, and the cost was that the XY plane, the plane
 * the schematic is actually drawn on, could never be seen square on; the scene
 * looked permanently locked to one three-quarter view.
 *
 * Writing the basis out in closed form removes the degeneracy instead of avoiding
 * it. `up` below is world +Z with the view direction projected out of it,
 * renormalised - the same vector `lookAt` derives for every pitch it could handle
 * - and it stays defined at the pole, where it becomes the plan's own north
 * turned by the yaw. So the camera now reaches both poles and the yaw keeps
 * meaning something there: on a plan view it spins the drawing rather than
 * doing nothing.
 */

/**
 * The camera's own axes for a yaw and pitch, matching `applyCinemaCamera`.
 *
 * `depth` runs from the target back towards the eye; `right` and `up` span the
 * image plane. Angles are normalised first, so a yaw of -572 and a yaw of 148
 * return the identical basis.
 */
export function cinemaViewBasis(settings: Pick<CinemaCameraState, "yaw" | "pitch">) {
  const yaw = degreesToRadians(normalizeYawDegrees(settings.yaw));
  const pitch = degreesToRadians(clampCinemaPitch(settings.pitch));
  const sinYaw = Math.sin(yaw);
  const cosYaw = Math.cos(yaw);
  const sinPitch = Math.sin(pitch);
  const cosPitch = Math.cos(pitch);
  const depth = new THREE.Vector3(sinYaw * cosPitch, -cosYaw * cosPitch, sinPitch);
  const up = new THREE.Vector3(-sinPitch * sinYaw, sinPitch * cosYaw, cosPitch);
  const right = new THREE.Vector3(cosYaw, sinYaw, 0);
  return { right, up, depth };
}

/**
 * Zoom that frames a box of these half-extents, seen from this yaw and pitch.
 *
 * `cinemaFitZoom` fits a single number and so has to assume the worst case in
 * every direction, which for the solved domain means fitting a cube around a
 * strip and leaving most of the frame empty. Projecting the box's own corners
 * onto the image plane instead measures how much room the domain actually needs
 * from where the viewer is standing, so Fit fills the frame with the data.
 */
export function cinemaFitZoomForBox(
  halfExtents: readonly [number, number, number],
  settings: Pick<CinemaCameraState, "yaw" | "pitch">,
  width: number,
  height: number,
  fill = SOLVED_DOMAIN_VIEW_FILL
): number {
  const { right, up } = cinemaViewBasis(settings);
  // A box's silhouette half-width along any screen axis is the sum of its
  // half-extents projected onto that axis, whichever corner happens to be furthest.
  const project = (axis: THREE.Vector3) =>
    halfExtents[0] * Math.abs(axis.x) + halfExtents[1] * Math.abs(axis.y) + halfExtents[2] * Math.abs(axis.z);
  const halfScreenWidth = project(right);
  const halfScreenHeight = project(up);
  const aspect = Math.max(width, 1) / Math.max(height, 1);
  const byWidth = (CINEMA_VIEW_HEIGHT * aspect * fill) / Math.max(2 * halfScreenWidth, 1e-9);
  const byHeight = (CINEMA_VIEW_HEIGHT * fill) / Math.max(2 * halfScreenHeight, 1e-9);
  return clampCinemaZoom(Math.min(byWidth, byHeight));
}

export function createCinemaCamera(width: number, height: number, settings: CinemaCameraState): THREE.OrthographicCamera {
  const frustum = cinemaOrthographicFrustum(width, height, settings.zoom);
  const camera = new THREE.OrthographicCamera(frustum.left, frustum.right, frustum.top, frustum.bottom, 0.1, 60);
  applyCinemaCamera(camera, settings, width, height);
  return camera;
}

export function applyCinemaCamera(
  camera: THREE.OrthographicCamera,
  settings: CinemaCameraState,
  width: number,
  height: number
) {
  const { depth, up } = cinemaViewBasis(settings);
  const target = new THREE.Vector3(settings.pan.x, settings.pan.y, 0);
  // The network lies in the XY plane with +Z up, so screen-up is world +Z for
  // every view except the two plan views, where it is the plan's own north turned
  // by the yaw. `cinemaViewBasis` returns exactly that, continuously, so handing
  // it to the camera is what lets a plan view be reached at all.
  camera.up.copy(up);
  camera.position.copy(target).addScaledVector(depth, CINEMA_CAMERA_DISTANCE);
  camera.lookAt(target);
  const frustum = cinemaOrthographicFrustum(width, height, settings.zoom);
  camera.left = frustum.left;
  camera.right = frustum.right;
  camera.top = frustum.top;
  camera.bottom = frustum.bottom;
  camera.updateProjectionMatrix();
}

export function buildCinemaScene(options: {
  canvas: HTMLCanvasElement;
  width: number;
  height: number;
  project: FluidProject;
  result: SimulationResult;
  cinemaCamera?: CinemaCameraState;
  resultDataset?: VtkResultDataset | null;
  derivedVisualization?: DecodedDerivedVisualization | null;
  derivedPresentationOptions?: DerivedPresentationOptions;
  resultFieldSelection: ResultFieldSelection | null;
  resultVectorComponent: ResultVectorComponent;
  resultColorMap: ResultColorMap;
  streamlines?: StreamlineResult | null;
  streamlineDisplay?: StreamlineDisplayOptions;
  selectedId: string | null;
  selectedKind?: "node" | "edge" | null;
}): CinemaRuntime {
  const buildStarted = performance.now();
  const {
    canvas,
    width,
    height,
    project,
    result,
    cinemaCamera = { yaw: 0, pitch: 38, zoom: 1, pan: { x: 0, y: 0 } },
    resultDataset,
    resultFieldSelection,
    resultVectorComponent,
    resultColorMap,
    streamlines,
    streamlineDisplay = {
      colorField: "velocity",
      colorMap: resultColorMap,
      showLines: true,
      showSprites: true,
      reducedMotion: false
    },
    derivedVisualization,
    derivedPresentationOptions,
    selectedId,
    selectedKind
  } = options;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height, false);
  renderer.setClearColor(0x02070d, 1);

  const scene = new THREE.Scene();
  // No fog. Distance fog is another way of making a surface's colour depend on
  // where it sits rather than on what it is, which is exactly what constant
  // shading is here to remove; the solved surface already opted out of it.
  const camera = createCinemaCamera(width, height, cinemaCamera);
  camera.updateMatrixWorld();

  let currentProject = project;
  let currentCamera = cinemaCamera;
  let viewWidth = width;
  let viewHeight = height;
  let center = projectCenter(project);
  // Schematic pixels per world unit. Fixed, because the plan on screen is the
  // drawing's own shape and nothing physical may stretch it; every physical
  // quantity is converted through `worldPerMetre` below instead.
  const worldScale = 74;
  const pickables: THREE.Object3D[] = [];
  const raycaster = new THREE.Raycaster();
  /**
   * The plane a click in the 3D view is read back onto, to give a schematic
   * coordinate. The schematic is a plan, so this is the ground plane - except
   * that a raised component is no longer on it, and reading a drag on a raised
   * component off the ground would put the pointer a long way from the thing it
   * is dragging. The plane therefore sits at the selected component's own height,
   * which is the only one a drag can currently be about.
   */
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  const particleUniforms = { uTime: { value: 0 } };
  const edgeVisuals = new Map<
    string,
    {
      outerPipe: THREE.Mesh;
      innerPipe: THREE.Mesh;
      outline: THREE.Line;
      rings: THREE.Mesh[];
      collars: THREE.Mesh[];
      collarGeometry: THREE.BufferGeometry;
      collarMaterial: THREE.Material;
      throat?: THREE.Mesh;
      valve?: THREE.Mesh;
      bend?: THREE.Mesh;
      particleStart: number;
      particleCount: number;
      coreMaterial: THREE.Material;
      radius: number;
      /** The bore the fittings' primitive geometries were built at, so they can be rescaled. */
      baseRadius: number;
      startZ: number;
      endZ: number;
      /** The route this pipe was last built from, so an unchanged route is never rebuilt. */
      routeKey: string;
      /**
       * Everything about the pipe that is not its route: bore and the two heights
       * it runs between. Without this an edit to `shape` or to a component's
       * elevation left the tube exactly as it was built, because the route had not
       * moved - which is precisely why diameter did nothing to the 3D view.
       */
      formKey: string;
    }
  >();
  const nodeVisuals = new Map<string, { group: THREE.Group; ports: Map<PipePortId, THREE.Mesh>; handle?: THREE.Mesh; handleLine?: THREE.Line }>();
  const particleRanges: Array<{ edgeId: string; start: number; count: number }> = [];
  let particleGeometry: THREE.BufferGeometry | null = null;

  function updatePipeMesh(mesh: THREE.Mesh, start: THREE.Vector3, end: THREE.Vector3, boreScale = 1) {
    const baseLength = Number(mesh.userData.pipeBaseLength ?? 1);
    const length = Math.max(start.distanceTo(end), 0.01);
    // The cylinder runs along its own +Y, so length is the y scale and the bore is
    // the other two - which is what lets a throat follow a changed diameter.
    mesh.scale.set(boreScale, length / baseLength, boreScale);
    orientBetweenPoints(mesh, start, end);
  }

  /** Cheap identity for a route, so `updateModel` only rebuilds tubes that actually moved. */
  function routeKeyOf(points: readonly Vec2[]): string {
    return points.map((point) => `${Math.round(point.x * 100)},${Math.round(point.y * 100)}`).join(";");
  }

  /** Cheap identity for a pipe's bore and the heights it spans. */
  function formKeyOf(radius: number, startZ: number, endZ: number): string {
    return `${radius.toFixed(6)}:${startZ.toFixed(6)}:${endZ.toFixed(6)}`;
  }

  /**
   * A point a fraction along a route, lifted to the height the pipe has reached
   * there plus `lift`, which is the drawing offset that keeps a fitting clear of
   * the tube it sits on.
   */
  function routeWorldPoint(
    points: readonly Vec2[],
    t: number,
    lift: number,
    elevation: { startZ: number; endZ: number }
  ): THREE.Vector3 {
    const clamped = Math.max(0, Math.min(1, t));
    const z = lift + elevation.startZ + (elevation.endZ - elevation.startZ) * clamped;
    return worldFromNetwork(pointOnPolyline(points, t), center, worldScale, z);
  }

  /** Direction of travel a fraction along a route, in world space. */
  function routeWorldTangent(points: readonly Vec2[], t: number): THREE.Vector3 {
    const tangent = tangentOnPolyline(points, t);
    // `worldFromNetwork` mirrors y, so the direction has to be mirrored with it.
    return new THREE.Vector3(tangent.x, -tangent.y, 0).normalize();
  }

  /** Where a pipe's interior corners land in world space: one collar per bend. */
  function routeWorldCorners(
    points: readonly Vec2[],
    lift: number,
    elevation: { startZ: number; endZ: number }
  ): THREE.Vector3[] {
    const total = polylineLength(points);
    let travelled = 0;
    const corners: THREE.Vector3[] = [];
    for (let index = 1; index < points.length; index += 1) {
      travelled += Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y);
      if (index >= points.length - 1) break;
      const t = total > 0 ? Math.min(1, travelled / total) : 0;
      const z = lift + elevation.startZ + (elevation.endZ - elevation.startZ) * t;
      corners.push(worldFromNetwork(points[index], center, worldScale, z));
    }
    return corners;
  }

  scene.add(createCinemaAmbientLight());

  const resultSurface = resultDataset
    ? addResultSurfaceMesh(
        scene,
        resultDataset,
        project.visualization.overlay,
        resultFieldSelection,
        resultVectorComponent,
        resultColorMap,
        SOLVED_DOMAIN_WORLD_SPAN
      )
    : null;

  /**
   * Whether the drawn network is the subject. It is, right up until there is
   * measured data to look at; after that it is not drawn at all rather than
   * drawn quietly, because a second object in the frame is read as a second view
   * of the same thing however faintly it is rendered.
   */
  const schematicIsSubject = resultSurface === null;

  if (schematicIsSubject) {
    // The ground reference belongs to the drawing. Its squares are world units,
    // which measure nothing in the dataset, so leaving it under a solved domain
    // would offer a scale cue that means nothing - the caption's measured extent
    // is the honest replacement.
    const grid = new THREE.GridHelper(12, 24, 0x2b4d63, 0x16293a);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.62;
    grid.name = "Schematic ground reference";
    scene.add(grid);
  }

  // Says what the domain is, in the corner of the frame, for as long as there is
  // a domain to describe. Parented to the camera below, so orbiting the data
  // never swings the label across it.
  const caption = resultSurface && resultDataset ? createSolvedDomainCaption(describeSolvedDomain(resultDataset).lines) : null;
  if (caption) {
    scene.add(camera);
    camera.add(caption.sprite);
  }

  /** Holds the caption at its authored pixel size in the corner, at any zoom or canvas size. */
  function layoutCaption() {
    if (!caption) return;
    const placement = solvedDomainCaptionPlacement({
      cssWidth: caption.cssWidth,
      cssHeight: caption.cssHeight,
      viewWidth,
      viewHeight,
      zoom: currentCamera.zoom
    });
    caption.sprite.scale.set(placement.width, placement.height, 1);
    // Just inside the near plane, so the label is never clipped by it.
    caption.sprite.position.set(placement.x, placement.y, -1);
  }
  layoutCaption();

  const streamlineScene = streamlines && resultSurface
    ? addStreamlineScene(scene, streamlines, resultSurface.bounds, resultSurface.meshScale, streamlineDisplay)
    : null;
  const derivedPresentation = derivedVisualization
    ? buildDerivedPresentation(renderer, derivedVisualization, derivedPresentationOptions)
    : null;
  if (derivedPresentation) {
    const physicalBounds = resultDataset ? datasetBounds(resultDataset) : null;
    if (physicalBounds) {
      // Share the solved surface's own scale and lift, so derived overlays stay
      // registered with the data they were derived from.
      const meshScale = resultSurface?.meshScale ?? SOLVED_DOMAIN_WORLD_SPAN / physicalBounds.span;
      derivedPresentation.group.scale.setScalar(meshScale);
      derivedPresentation.group.position.set(
        -physicalBounds.center[0] * meshScale,
        -physicalBounds.center[1] * meshScale,
        -physicalBounds.center[2] * meshScale + RESULT_SURFACE_Z_OFFSET
      );
    }
    scene.add(derivedPresentation.group);
  }

  const edgeValues = Object.values(result.edgeResults).map((edge) => {
    if (project.visualization.overlay === "pressure") return edge.pressureDrop;
    if (project.visualization.overlay === "reynolds") return edge.reynolds;
    return edge.velocity;
  });
  const maxEdge = Math.max(...edgeValues, 1);
  const particlePositions: number[] = [];
  const particleColors: number[] = [];
  const particlePhases: number[] = [];

  function registerPickable(object: THREE.Object3D, userData: CinemaPick) {
    object.userData = { ...object.userData, cinemaPick: userData };
    pickables.push(object);
  }

  const initialRoutes = routesByEdge(project);
  /**
   * The project's own metres-per-pixel. Rebuilt on every `updateModel`, because a
   * length or a route can change under us and this is the number every physical
   * quantity in the scene is measured against.
   */
  let metricScale = networkMetricScale(project, initialRoutes, worldScale);
  // `THREE.Plane` stores z = -constant, and the scene is rebuilt whenever the
  // selection changes, so this only has to be set once.
  const selectedNode = selectedKind === "node" && selectedId ? project.nodes[selectedId] : undefined;
  plane.constant = selectedNode ? -nodeWorldZ(selectedNode, metricScale.worldPerMetre) : 0;

  /** The world radius, and the two heights, one pipe should currently be drawn at. */
  function pipeFormOf(edge: FluidEdge, route: readonly Vec2[], nodes: Record<string, FluidNode>) {
    const radius = pipeWorldRadius({
      shape: edge.shape,
      physicalLength: edge.length,
      drawnWorldLength: polylineLength(route) / worldScale
    });
    return { radius, ...edgeWorldElevations(edge, nodes, metricScale.worldPerMetre) };
  }

  // Empty once the data is the subject, which is what silences the drawn network:
  // no pipes, no fittings, no vessels, no flow ticks, and nothing left in
  // `edgeVisuals`/`nodeVisuals` for `updateModel` to keep in step.
  const schematicEdges = schematicIsSubject ? Object.values(project.edges) : [];
  const schematicNodes = schematicIsSubject ? Object.values(project.nodes) : [];

  schematicEdges.forEach((edge) => {
    const route = initialRoutes.get(edge.id);
    const solved = result.edgeResults[edge.id];
    if (!route || !solved) return;
    const metric = project.visualization.overlay === "pressure" ? solved.pressureDrop : project.visualization.overlay === "reynolds" ? solved.reynolds : solved.velocity;
    const color = new THREE.Color(overlayValueColor(metric, maxEdge, project.visualization.overlay));
    // Bore, and the heights the run spans, both read from the model. The old
    // constant-ish `max(0.065, diameter * 0.86)` drew every pipe under 76 mm at
    // exactly one size and ignored elevation entirely.
    const { radius, startZ, endZ } = pipeFormOf(edge, route, project.nodes);
    const elevation = { startZ, endZ };
    const active = selectedKind === "edge" && selectedId === edge.id;
    const { cage: cageMaterial, core: coreMaterial, outline: outlineMaterial } = createSchematicPipeMaterials(color, active);
    // A coarse, visibly faceted cage: few enough segments that the wireframe
    // reads as a drawing rather than as a shaded tube, and sparse enough that it
    // never stripes the solved surface behind it.
    const cageRadius = radius * 1.42;
    const coreRadius = radius * 0.62;
    const outerPipe = new THREE.Mesh(
      buildSweptTubeGeometry(routeWorldPath(route, cageRadius, center, worldScale, startZ, endZ), cageRadius, 8),
      cageMaterial
    );
    const innerPipe = new THREE.Mesh(
      buildSweptTubeGeometry(routeWorldPath(route, coreRadius, center, worldScale, startZ, endZ), coreRadius, 16),
      coreMaterial
    );
    outerPipe.name = `Schematic pipe cage ${edge.id}`;
    innerPipe.name = `Schematic pipe core ${edge.id}`;
    registerPickable(outerPipe, { kind: "edge", id: edge.id });
    registerPickable(innerPipe, { kind: "edge", id: edge.id });
    scene.add(outerPipe, innerPipe);

    // The centre line is the drawing's outline: it traces the routed path itself,
    // so where the pipe turns is legible without asking a highlight to say so.
    const outline = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(routeWorldPath(route, coreRadius, center, worldScale, startZ, endZ)),
      outlineMaterial
    );
    outline.name = `Schematic pipe centre line ${edge.id}`;
    scene.add(outline);

    const ringGeometry = new THREE.TorusGeometry(radius * 1.62, 0.015, 10, 42);
    // Unlit like everything else in the drawing, so an end ring is the same
    // brightness whichever way its pipe happens to run.
    const ringMaterial = createConstantSurfaceMaterial({
      color: steppedTone(active ? 0xffd98a : 0x9fb8c8, "base"),
      opacity: active ? 0.62 : 0.36
    });
    const rings: THREE.Mesh[] = [];
    [0.08, 0.92].forEach((t) => {
      const ring = new THREE.Mesh(ringGeometry.clone(), ringMaterial);
      ring.position.copy(routeWorldPoint(route, t, 0, elevation));
      ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), routeWorldTangent(route, t));
      registerPickable(ring, { kind: "edge", id: edge.id });
      scene.add(ring);
      rings.push(ring);
    });

    // A collar on every bend. Corners are the thing the 3D view used to drop, so
    // they get a marker that says "the pipe turns here" at any viewing angle.
    const collarGeometry = new THREE.SphereGeometry(cageRadius * 1.12, 14, 10);
    const collarMaterial = createConstantSurfaceMaterial({
      color: steppedTone(active ? 0xffd98a : 0x9fb8c8, "shadow"),
      opacity: active ? 0.5 : 0.3
    });
    const collars: THREE.Mesh[] = routeWorldCorners(route, 0, elevation).map((corner) => {
      const collar = new THREE.Mesh(collarGeometry, collarMaterial);
      collar.position.copy(corner);
      collar.name = `Schematic pipe bend ${edge.id}`;
      registerPickable(collar, { kind: "edge", id: edge.id });
      scene.add(collar);
      return collar;
    });

    let throat: THREE.Mesh | undefined;
    let valve: THREE.Mesh | undefined;
    let bend: THREE.Mesh | undefined;
    // Fittings stay part of the illustration: flat, translucent, and never
    // brighter than the opaque solved surface.
    if (edge.type === "venturi") {
      throat = createPipeMesh(
        routeWorldPoint(route, 0.42, 0, elevation),
        routeWorldPoint(route, 0.58, 0, elevation),
        radius * 0.35,
        createConstantSurfaceMaterial({ color: steppedTone(0xe8c775, "base"), opacity: 0.58 }),
        20
      );
      registerPickable(throat, { kind: "edge", id: edge.id });
      scene.add(throat);
    } else if (edge.type === "valve") {
      valve = new THREE.Mesh(
        new THREE.OctahedronGeometry(radius * 1.65, 0),
        createConstantSurfaceMaterial({ color: steppedTone(0xe0a266, "base"), opacity: 0.62 })
      );
      valve.position.copy(routeWorldPoint(route, 0.5, radius * 1.7, elevation));
      registerPickable(valve, { kind: "edge", id: edge.id });
      scene.add(valve);
    } else if (edge.type === "bend") {
      bend = new THREE.Mesh(
        new THREE.TorusGeometry(radius * 1.3, 0.02, 8, 40),
        createConstantSurfaceMaterial({ color: steppedTone(0x8fc6dc, "base"), opacity: 0.6 })
      );
      bend.position.copy(routeWorldPoint(route, 0.5, radius * 1.4, elevation));
      registerPickable(bend, { kind: "edge", id: edge.id });
      scene.add(bend);
    }

    const particleStart = particlePositions.length / 3;
    const particleCount = Math.max(16, Math.min(56, Math.round(Math.abs(solved.velocity) * 9)));
    for (let index = 0; index < particleCount; index += 1) {
      const t = index / particleCount;
      const point = routeWorldPoint(route, t, 0, elevation);
      particlePositions.push(point.x, point.y, point.z + 0.07 + (index % 4) * 0.014);
      particleColors.push(color.r, color.g, color.b);
      particlePhases.push(t + edge.id.length * 0.07);
    }
    particleRanges.push({ edgeId: edge.id, start: particleStart, count: particleCount });
    edgeVisuals.set(edge.id, {
      outerPipe,
      innerPipe,
      outline,
      rings,
      collars,
      collarGeometry,
      collarMaterial,
      throat,
      valve,
      bend,
      particleStart,
      particleCount,
      coreMaterial,
      radius,
      baseRadius: radius,
      startZ,
      endZ,
      routeKey: routeKeyOf(route),
      formKey: formKeyOf(radius, startZ, endZ)
    });
  });

  if (particlePositions.length > 0 && project.visualization.particles) {
    particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute("position", new THREE.Float32BufferAttribute(particlePositions, 3));
    particleGeometry.setAttribute("aColor", new THREE.Float32BufferAttribute(particleColors, 3));
    particleGeometry.setAttribute("aPhase", new THREE.Float32BufferAttribute(particlePhases, 1));
    const particleMaterial = new THREE.ShaderMaterial({
      uniforms: particleUniforms,
      vertexShader: flowParticleVertexShader,
      fragmentShader: flowParticleFragmentShader,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    });
    const particlePoints = new THREE.Points(particleGeometry, particleMaterial);
    particlePoints.name = "Schematic flow ticks (illustrative)";
    scene.add(particlePoints);
  }

  schematicNodes.forEach((node) => {
    const elevationZ = nodeWorldZ(node, metricScale.worldPerMetre);
    const position = worldFromNetwork(node.position, center, worldScale, NODE_BODY_Z + elevationZ);
    const active = selectedKind === "node" && selectedId === node.id;
    const nodeGroup = new THREE.Group();
    const nodePortMeshes = new Map<PipePortId, THREE.Mesh>();
    let nodeHandle: THREE.Mesh | undefined;
    let nodeHandleLine: THREE.Line | undefined;
    nodeGroup.position.copy(position);
    nodeGroup.rotation.z = -degreesToRadians(node.rotation ?? 0);
    nodeGroup.name = `Schematic component ${node.id}`;
    // Equipment symbols: flat and translucent so they stay part of the drawing.
    // Unlit, so a component reads the same whether it faces the camera or away
    // from it - form is carried by the outline added below and by the tone step
    // between a body and its trim, not by a highlight rolling across a curve.
    const material = createConstantSurfaceMaterial({
      color: steppedTone(active ? 0xf0c563 : node.type === "pump" ? 0x8ba0b1 : node.type === "mixer" ? 0x3f939a : 0x2b556d, "base"),
      opacity: active ? 0.82 : 0.66,
      depthWrite: true
    });

    let body: THREE.Mesh;
    if (node.type === "pump") {
      body = new THREE.Mesh(new THREE.CylinderGeometry(0.32, 0.32, 0.3, 40), material);
      body.rotation.x = Math.PI / 2;
      const impeller = new THREE.Mesh(
        new THREE.TorusGeometry(0.22, 0.03, 14, 42),
        createConstantSurfaceMaterial({ color: steppedTone(0xc2d3de, "raised"), opacity: 0.72 })
      );
      impeller.position.z = 0.03;
      nodeGroup.add(impeller);
    } else if (node.type === "source" || node.type === "sink") {
      // Ghosted vessel: a plainly translucent shell that behaves identically
      // wherever the vessel sits.
      body = new THREE.Mesh(
        new THREE.CylinderGeometry(0.42, 0.42, 0.66, 48, 1, true),
        createConstantSurfaceMaterial({
          color: steppedTone(0x7fc8e0, "base"),
          opacity: 0.24,
          side: THREE.DoubleSide
        })
      );
      body.rotation.x = Math.PI / 2;
      body.position.z = 0.14;
    } else if (node.type === "mixer") {
      body = new THREE.Mesh(new THREE.SphereGeometry(0.34, 36, 20), material);
      const swirl = new THREE.Mesh(
        new THREE.TorusKnotGeometry(0.22, 0.015, 84, 8),
        createConstantSurfaceMaterial({ color: steppedTone(0x8ed2e6, "raised"), opacity: 0.42 })
      );
      nodeGroup.add(swirl);
    } else {
      body = new THREE.Mesh(new THREE.SphereGeometry(0.2, 30, 16), material);
    }
    registerPickable(body, { kind: "node", id: node.id });
    nodeGroup.add(body);

    // Outline the body's own silhouette and creases. On a constant-shaded
    // drawing this is what tells one solid from the one behind it.
    const bodyOutline = new THREE.LineSegments(
      new THREE.EdgesGeometry(body.geometry, 24),
      createConstantOutlineMaterial(steppedTone(active ? 0xffe3a1 : 0xa8c4d8, "raised"), active ? 0.85 : 0.5)
    );
    bodyOutline.name = `Schematic component outline ${node.id}`;
    body.add(bodyOutline);
    const pickVolume = new THREE.Mesh(
      new THREE.SphereGeometry(node.type === "source" || node.type === "sink" ? 0.28 : 0.24, 16, 10),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.001, depthWrite: false })
    );
    registerPickable(pickVolume, { kind: "node", id: node.id });
    nodeGroup.add(pickVolume);

    ports.forEach((port) => {
      const portPoint = worldFromNetwork(schematicPortPosition(node, port), center, worldScale, NODE_PORT_Z + elevationZ);
      const portMesh = new THREE.Mesh(new THREE.SphereGeometry(port === "inlet" || port === "outlet" ? 0.064 : 0.048, 18, 12), new THREE.MeshBasicMaterial({ color: port === "inlet" || port === "outlet" ? 0x9dfbd7 : 0x8aa0b8 }));
      portMesh.position.copy(portPoint);
      registerPickable(portMesh, { kind: "port", nodeId: node.id, port, point: schematicPortPosition(node, port) });
      scene.add(portMesh);
      nodePortMeshes.set(port, portMesh);
    });

    if (active) {
      const handlePoint = worldFromNetwork(aimHandlePosition(node), center, worldScale, NODE_HANDLE_Z + elevationZ);
      nodeHandle = new THREE.Mesh(new THREE.SphereGeometry(0.08, 20, 14), new THREE.MeshBasicMaterial({ color: 0xffd54f }));
      nodeHandle.position.copy(handlePoint);
      registerPickable(nodeHandle, { kind: "rotate", nodeId: node.id });
      scene.add(nodeHandle);
      const handleLineGeometry = new THREE.BufferGeometry().setFromPoints([position.clone().setZ(0.13 + elevationZ), handlePoint]);
      nodeHandleLine = new THREE.Line(handleLineGeometry, new THREE.LineBasicMaterial({ color: 0xffd54f, transparent: true, opacity: 0.72 }));
      scene.add(nodeHandleLine);
    }

    scene.add(nodeGroup);
    nodeVisuals.set(node.id, { group: nodeGroup, ports: nodePortMeshes, handle: nodeHandle, handleLine: nodeHandleLine });
  });

  function pickAt(event: Pick<PointerEvent, "clientX" | "clientY">): CinemaPick | null {
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const hits = raycaster.intersectObjects(pickables, false);
    const picks = hits.map((hit) => hit.object.userData.cinemaPick as CinemaPick | undefined).filter((pick): pick is CinemaPick => Boolean(pick));
    const priority = ["rotate", "port", "node", "edge"] satisfies CinemaPick["kind"][];
    for (const kind of priority) {
      const pick = picks.find((candidate) => candidate.kind === kind);
      if (pick) return pick;
    }
    return null;
  }

  function probeAt(event: Pick<PointerEvent, "clientX" | "clientY">): CinemaResultProbe | null {
    if (!resultDataset || !resultSurface) return null;
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const hit = raycaster.intersectObject(resultSurface.surface, false)[0];
    const triangleIndex = hit?.faceIndex;
    if (!hit || triangleIndex === undefined || triangleIndex === null || triangleIndex < 0) return null;
    const triangle = resultSurface.triangles[triangleIndex];
    if (!triangle) return null;
    const physicalPoint: [number, number, number] = [
      hit.point.x / resultSurface.meshScale + resultSurface.bounds.center[0],
      hit.point.y / resultSurface.meshScale + resultSurface.bounds.center[1],
      (hit.point.z - RESULT_SURFACE_Z_OFFSET) / resultSurface.meshScale + resultSurface.bounds.center[2]
    ];
    const [firstIndex, secondIndex, thirdIndex] = triangle.pointIndices;
    const barycentric = new THREE.Triangle(
      new THREE.Vector3(...resultDataset.points[firstIndex]),
      new THREE.Vector3(...resultDataset.points[secondIndex]),
      new THREE.Vector3(...resultDataset.points[thirdIndex])
    ).getBarycoord(new THREE.Vector3(...physicalPoint), new THREE.Vector3());
    if (!barycentric) return null;
    let nearestPointIndex = triangle.pointIndices[0];
    let nearestDistance = Number.POSITIVE_INFINITY;
    triangle.pointIndices.forEach((pointIndex) => {
      const point = resultDataset.points[pointIndex];
      const distance = Math.hypot(
        point[0] - physicalPoint[0],
        point[1] - physicalPoint[1],
        point[2] - physicalPoint[2]
      );
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestPointIndex = pointIndex;
      }
    });
    return {
      point: physicalPoint,
      ownerCellIndex: triangle.ownerCellIndex,
      nearestPointIndex,
      trianglePointIndices: triangle.pointIndices,
      barycentricWeights: [barycentric.x, barycentric.y, barycentric.z]
    };
  }

  function pointAt(event: Pick<PointerEvent, "clientX" | "clientY">): Vec2 | null {
    raycaster.setFromCamera(canvasNdc(canvas, event), camera);
    const world = new THREE.Vector3();
    const hit = raycaster.ray.intersectPlane(plane, world);
    if (!hit) return null;
    return networkFromWorld(world, center, worldScale);
  }

  function fitCamera(settings: CinemaCameraState, nextProject: FluidProject): CinemaCameraState {
    // Fit frames whatever the subject is. With a result loaded that is the solved
    // domain, which is already centred on the origin, so fitting it is a zoom
    // against its own silhouette rather than a search through the drawing.
    if (resultSurface) {
      const halfExtents = ([0, 1, 2] as const).map(
        (axis) => ((resultSurface.bounds.max[axis] - resultSurface.bounds.min[axis]) / 2) * resultSurface.meshScale
      ) as [number, number, number];
      return normalizeCinemaCamera({
        ...settings,
        zoom: cinemaFitZoomForBox(halfExtents, settings, viewWidth, viewHeight),
        pan: { x: 0, y: 0 }
      });
    }
    const nodes = Object.values(nextProject.nodes);
    if (nodes.length === 0) return normalizeCinemaCamera({ ...settings, zoom: 1, pan: { x: 0, y: 0 } });
    const minX = Math.min(...nodes.map((node) => node.position.x));
    const maxX = Math.max(...nodes.map((node) => node.position.x));
    const minY = Math.min(...nodes.map((node) => node.position.y));
    const maxY = Math.max(...nodes.map((node) => node.position.y));
    const worldSpan = Math.max(maxX - minX, maxY - minY, 1) / worldScale;
    // Fit is the one moment the renderer hands a camera back to the application,
    // so it is where a yaw that has been orbited to -572 is folded back onto the
    // turn every control assumes. It changes nothing about what is on screen.
    return normalizeCinemaCamera({ ...settings, zoom: cinemaFitZoom(worldSpan), pan: { x: 0, y: 0 } });
  }

  /**
   * Re-sweeps one pipe along its current route, at its current bore and height.
   *
   * A route can gain or lose corners when a component moves, so the tube has to
   * be rebuilt rather than stretched - which is precisely the thing the old
   * fixed-length cylinder could not do, and why bends never reached this view.
   * Bore and height are rebuilt here too: they are baked into the swept vertices,
   * so there is no cheaper way to change them and no way at all to change them by
   * moving the mesh.
   */
  function rebuildPipeGeometry(visual: NonNullable<ReturnType<typeof edgeVisuals.get>>, route: readonly Vec2[]) {
    const { radius, startZ, endZ } = visual;
    const elevation = { startZ, endZ };
    const cageRadius = radius * 1.42;
    const coreRadius = radius * 0.62;
    const cagePath = routeWorldPath(route, cageRadius, center, worldScale, startZ, endZ);
    const corePath = routeWorldPath(route, coreRadius, center, worldScale, startZ, endZ);
    visual.outerPipe.geometry.dispose();
    visual.outerPipe.geometry = buildSweptTubeGeometry(cagePath, cageRadius, 8);
    visual.innerPipe.geometry.dispose();
    visual.innerPipe.geometry = buildSweptTubeGeometry(corePath, coreRadius, 16);
    visual.outline.geometry.dispose();
    visual.outline.geometry = new THREE.BufferGeometry().setFromPoints(corePath);

    // One collar per corner, so a route that gains a bend gains a marker for it.
    const corners = routeWorldCorners(route, 0, elevation);
    while (visual.collars.length > corners.length) {
      const collar = visual.collars.pop();
      if (!collar) continue;
      scene.remove(collar);
      // Off the drawing means off the pick list, or a click would still land on
      // a bend that is no longer there.
      const picked = pickables.indexOf(collar);
      if (picked >= 0) pickables.splice(picked, 1);
    }
    while (visual.collars.length < corners.length) {
      const collar = new THREE.Mesh(visual.collarGeometry, visual.collarMaterial);
      collar.userData = { ...visual.outerPipe.userData };
      pickables.push(collar);
      scene.add(collar);
      visual.collars.push(collar);
    }
    // Fittings are primitives sized off the bore when they were built, so the one
    // thing that keeps them honest about a changed bore is a uniform scale.
    const boreScale = visual.baseRadius > 0 ? radius / visual.baseRadius : 1;
    visual.collars.forEach((collar, index) => {
      collar.position.copy(corners[index]);
      collar.scale.setScalar(boreScale);
    });
    visual.rings.forEach((ring) => ring.scale.setScalar(boreScale));
    visual.valve?.scale.setScalar(boreScale);
    visual.bend?.scale.setScalar(boreScale);
  }

  function updateModel(nextProject: FluidProject, nextResult: SimulationResult) {
    currentProject = nextProject;
    const nextCenter = projectCenter(nextProject);
    center.x = nextCenter.x;
    center.y = nextCenter.y;
    const routes = routesByEdge(nextProject);
    // Re-measured every update: an edit to any edge's length changes what a
    // schematic pixel is worth, and every elevation in the scene is drawn against
    // that. Doing this once at build time was half of why the 3D view ignored the
    // model it was supposed to be showing.
    metricScale = networkMetricScale(nextProject, routes, worldScale);

    Object.values(nextProject.nodes).forEach((node) => {
      const visual = nodeVisuals.get(node.id);
      if (!visual) return;
      const elevationZ = nodeWorldZ(node, metricScale.worldPerMetre);
      visual.group.position.copy(worldFromNetwork(node.position, center, worldScale, NODE_BODY_Z + elevationZ));
      visual.group.rotation.z = -degreesToRadians(node.rotation ?? 0);
      ports.forEach((port) => {
        const portMesh = visual.ports.get(port);
        if (portMesh) {
          portMesh.position.copy(worldFromNetwork(schematicPortPosition(node, port), center, worldScale, NODE_PORT_Z + elevationZ));
        }
      });
      const handlePoint = worldFromNetwork(aimHandlePosition(node), center, worldScale, NODE_HANDLE_Z + elevationZ);
      if (visual.handle) visual.handle.position.copy(handlePoint);
      if (visual.handleLine) {
        const nodePoint = worldFromNetwork(node.position, center, worldScale, 0.13 + elevationZ);
        visual.handleLine.geometry.dispose();
        visual.handleLine.geometry = new THREE.BufferGeometry().setFromPoints([nodePoint, handlePoint]);
      }
    });

    Object.values(nextProject.edges).forEach((edge) => {
      const visual = edgeVisuals.get(edge.id);
      const route = routes.get(edge.id);
      if (!visual || !route) return;
      const form = pipeFormOf(edge, route, nextProject.nodes);
      const elevation = { startZ: form.startZ, endZ: form.endZ };
      const routeKey = routeKeyOf(route);
      const formKey = formKeyOf(form.radius, form.startZ, form.endZ);
      if (routeKey !== visual.routeKey || formKey !== visual.formKey) {
        visual.radius = form.radius;
        visual.startZ = form.startZ;
        visual.endZ = form.endZ;
        rebuildPipeGeometry(visual, route);
        visual.routeKey = routeKey;
        visual.formKey = formKey;
      }
      visual.rings.forEach((ring, index) => {
        const t = index === 0 ? 0.08 : 0.92;
        ring.position.copy(routeWorldPoint(route, t, 0, elevation));
        ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), routeWorldTangent(route, t));
      });
      const boreScale = visual.baseRadius > 0 ? visual.radius / visual.baseRadius : 1;
      if (visual.throat) {
        updatePipeMesh(
          visual.throat,
          routeWorldPoint(route, 0.42, 0, elevation),
          routeWorldPoint(route, 0.58, 0, elevation),
          boreScale
        );
      }
      if (visual.valve) visual.valve.position.copy(routeWorldPoint(route, 0.5, visual.radius * 1.7, elevation));
      if (visual.bend) visual.bend.position.copy(routeWorldPoint(route, 0.5, visual.radius * 1.4, elevation));
    });

    const particlePosition = particleGeometry?.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (particlePosition) {
      particleRanges.forEach(({ edgeId, start, count }) => {
        const route = routes.get(edgeId);
        const visual = edgeVisuals.get(edgeId);
        if (!route || !visual) return;
        const elevation = { startZ: visual.startZ, endZ: visual.endZ };
        for (let index = 0; index < count; index += 1) {
          const point = routeWorldPoint(route, index / count, 0, elevation);
          particlePosition.setXYZ(start + index, point.x, point.y, point.z + 0.07 + (index % 4) * 0.014);
        }
      });
      particlePosition.needsUpdate = true;
    }

    camera.updateMatrixWorld();
    Object.assign(
      projectedPositions,
      projectedNodePositions(nextProject, center, worldScale, metricScale.worldPerMetre, camera, viewWidth, viewHeight)
    );
    renderer.render(scene, camera);
    void nextResult;
  }

  function resize() {
    const nextRect = canvas.getBoundingClientRect();
    viewWidth = Math.max(1, nextRect.width);
    viewHeight = Math.max(1, nextRect.height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(viewWidth, viewHeight, false);
    // Orthographic scale lives in the frustum, so a resize has to re-derive it
    // from the current zoom rather than just changing an aspect ratio.
    applyCinemaCamera(camera, currentCamera, viewWidth, viewHeight);
    layoutCaption();
    camera.updateMatrixWorld();
  }

  function dispose() {
    derivedPresentation?.dispose();
    // The caption's texture is owned by the caption, not by its material, so
    // disposing the material alone would leave the glyph atlas on the GPU.
    caption?.texture.dispose();
    scene.traverse((object: THREE.Object3D) => {
      const mesh = object as THREE.Mesh;
      // Every `Sprite` in three.js shares one module-level quad; disposing it
      // here would pull that quad out from under every other scene on the page.
      if (mesh.geometry && !(object as THREE.Object3D as THREE.Sprite).isSprite) mesh.geometry.dispose();
      const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
      else material?.dispose();
    });
    renderer.dispose();
  }

  const projectedPositions = projectedNodePositions(
    project,
    center,
    worldScale,
    metricScale.worldPerMetre,
    camera,
    viewWidth,
    viewHeight
  );
  const runtime: CinemaRuntime = {
    center,
    worldScale,
    pickableCount: pickables.length,
    projectedNodePositions: projectedPositions,
    engine: `three.js r${THREE.REVISION}`,
    derivedFallback: derivedPresentation?.fallback ?? "none",
    render(time: number, advancePreview = true) {
      if (advancePreview) particleUniforms.uTime.value = time / 1000;
      streamlineScene?.update(time, advancePreview);
      if (advancePreview) derivedPresentation?.render(time / 1000);
      renderer.render(scene, camera);
    },
    updateModel,
    fitCamera,
    updateCamera(settings: CinemaCameraState) {
      currentCamera = settings;
      applyCinemaCamera(camera, settings, viewWidth, viewHeight);
      layoutCaption();
      camera.updateMatrixWorld();
      Object.assign(
        projectedPositions,
        projectedNodePositions(currentProject, center, worldScale, metricScale.worldPerMetre, camera, viewWidth, viewHeight)
      );
      renderer.render(scene, camera);
    },
    resize,
    dispose,
    pickAt,
    probeAt,
    pointAt
  };
  recordEditorMetric("cinema-build", performance.now() - buildStarted);
  return runtime;
}
