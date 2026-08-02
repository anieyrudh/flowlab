import { describe, expect, it } from "vitest";
import { venturiPreset } from "../data/presets";
import type { FluidProject, Vec2 } from "../types";
import {
  CINEMA_MAX_PITCH,
  CINEMA_MIN_PITCH,
  MAX_FIT_ZOOM,
  MIN_NODE_SEPARATION,
  ROUTE_CLEARANCE,
  SCHEMATIC_GRID_SIZE,
  SCHEMATIC_PORT_OFFSET,
  axisDirection,
  boxesOverlap,
  buildSchematicRoutes,
  cinemaCameraForPlane,
  cinemaPlaneOrientations,
  cinemaViewPlaneOf,
  clampCinemaPitch,
  clampViewportZoom,
  countEdgeCrossings,
  defaultSchematicViewport,
  distanceToPolyline,
  firstClearBox,
  fitSchematicViewport,
  isCellFree,
  labelPlacementCandidates,
  longestPolylineSegment,
  normalizeCinemaCamera,
  normalizeYawDegrees,
  panSchematicViewport,
  pointOnPolyline,
  polylineLength,
  polylineObstacleBoxes,
  polylineSegments,
  resetSchematicViewport,
  roundPolylineCorners,
  routeLaneSpans,
  routeOrthogonalPipe,
  schematicEdgeRoute,
  schematicNodePositions,
  schematicNodeRadius,
  schematicPortDirection,
  schematicPortPosition,
  screenToWorld,
  segmentCrossing,
  simplifyPolyline,
  snapNodeToFreeCell,
  snapToGrid,
  tangentOnPolyline,
  tidySchematicLayout,
  tidySchematicRotations,
  visibleGridRange,
  wireCrossings,
  wireJunctions,
  worldToScreen,
  zoomViewportAtPoint
} from "./viewportModel";

const RIGHT = { x: 1, y: 0 };
const LEFT = { x: -1, y: 0 };
const UP = { x: 0, y: -1 };
const DOWN = { x: 0, y: 1 };

/** x of every vertical run in a route, which is the lane the router picked. */
function verticalLanes(points: Vec2[]): number[] {
  return polylineSegments(points)
    .filter((segment) => segment.from.x === segment.to.x && segment.from.y !== segment.to.y)
    .map((segment) => segment.from.x);
}

/** Math.sign with -0 folded onto 0, so a flat axis compares equal whichever way it is written. */
function axisSign(value: number): number {
  return Math.sign(value) === 0 ? 0 : Math.sign(value);
}

/**
 * A layout whose two pipes have to swap rows: `a` sits above `b` on the left, but wires to
 * the lower component on the right, so the two runs cross.
 */
function crossedProject(): FluidProject {
  return {
    ...venturiPreset,
    nodes: {
      a: { id: "a", type: "source", label: "A", position: { x: 100, y: 100 }, elevation: 0 },
      b: { id: "b", type: "source", label: "B", position: { x: 100, y: 300 }, elevation: 0 },
      upper: { id: "upper", type: "sink", label: "Upper", position: { x: 500, y: 100 }, elevation: 0 },
      lower: { id: "lower", type: "sink", label: "Lower", position: { x: 500, y: 300 }, elevation: 0 }
    },
    edges: {
      first: { ...venturiPreset.edges.inlet, id: "first", from: "a", to: "lower" },
      second: { ...venturiPreset.edges.inlet, id: "second", from: "b", to: "upper" }
    },
    sweeps: []
  };
}

describe("viewport model", () => {
  it("keeps the point under the cursor stable while zooming", () => {
    const viewport = { pan: { x: 40, y: 20 }, zoom: 1 };
    const cursor = { x: 320, y: 220 };
    const worldBefore = screenToWorld(cursor, viewport);
    const next = zoomViewportAtPoint(viewport, cursor, -240);
    const worldAfter = screenToWorld(cursor, next);
    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 8);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 8);
    expect(next.zoom).toBeGreaterThan(1);
  });

  it("clamps zoom and preserves pan deltas", () => {
    expect(clampViewportZoom(99)).toBe(4);
    expect(clampViewportZoom(0.01)).toBe(0.25);
    expect(panSchematicViewport(defaultSchematicViewport, { x: 12, y: -8 })).toEqual({ pan: { x: 12, y: -8 }, zoom: 1 });
  });

  it("fits a constrained network and resets to the canonical view", () => {
    const fit = fitSchematicViewport(venturiPreset, 420, 300);
    expect(fit.zoom).toBeLessThan(1);
    expect(fit.pan.x).not.toBe(0);
    expect(resetSchematicViewport()).toEqual({ pan: { x: 0, y: 0 }, zoom: 1 });
  });

  it("round trips screen and world coordinates", () => {
    const viewport = { pan: { x: 80, y: -10 }, zoom: 0.75 };
    const world = { x: 420, y: 180 };
    expect(screenToWorld(worldToScreen(world, viewport), viewport)).toEqual(world);
  });

  it("keeps the fitted drawing clear of the screen regions controls occupy", () => {
    const insets = { top: 20, right: 60, bottom: 80, left: 40 };
    const fit = fitSchematicViewport(venturiPreset, 900, 600, { insets, padding: 0 });
    const bounds = Object.values(venturiPreset.nodes).map((node) => worldToScreen(node.position, fit));
    for (const point of bounds) {
      expect(point.x).toBeGreaterThanOrEqual(insets.left);
      expect(point.x).toBeLessThanOrEqual(900 - insets.right);
      expect(point.y).toBeGreaterThanOrEqual(insets.top);
      expect(point.y).toBeLessThanOrEqual(600 - insets.bottom);
    }
  });

  it("does not magnify a small network past the fit ceiling", () => {
    const fit = fitSchematicViewport(venturiPreset, 6000, 4000);
    expect(fit.zoom).toBeLessThanOrEqual(MAX_FIT_ZOOM);
  });

  it("snaps a dropped component to the grid and to a free cell when the target is taken", () => {
    expect(snapToGrid({ x: 137, y: -22 })).toEqual({ x: 120, y: -40 });

    const free = snapNodeToFreeCell(venturiPreset, "source", { x: 137, y: 501 });
    expect(free).toEqual(snapToGrid({ x: 137, y: 501 }));
    expect(free.x % SCHEMATIC_GRID_SIZE).toBe(0);
    expect(free.y % SCHEMATIC_GRID_SIZE).toBe(0);

    // The throat sits at (420, 260); dropping on top of it must land somewhere else.
    const deflected = snapNodeToFreeCell(venturiPreset, "source", { x: 420, y: 260 });
    expect(isCellFree(venturiPreset, "source", deflected)).toBe(true);
    expect(deflected.x % SCHEMATIC_GRID_SIZE).toBe(0);
    expect(deflected.y % SCHEMATIC_GRID_SIZE).toBe(0);
    for (const node of Object.values(venturiPreset.nodes)) {
      if (node.id === "source") continue;
      expect(Math.hypot(node.position.x - deflected.x, node.position.y - deflected.y)).toBeGreaterThanOrEqual(
        MIN_NODE_SEPARATION
      );
    }
  });

  it("reports a grid range that covers the whole visible canvas", () => {
    const viewport = { pan: { x: -120, y: 40 }, zoom: 0.6 };
    const range = visibleGridRange(viewport, 800, 500);
    const topLeft = screenToWorld({ x: 0, y: 0 }, viewport);
    const bottomRight = screenToWorld({ x: 800, y: 500 }, viewport);
    expect(range.step).toBe(SCHEMATIC_GRID_SIZE);
    expect(range.startX).toBeLessThanOrEqual(topLeft.x);
    expect(range.endX).toBeGreaterThanOrEqual(bottomRight.x);
    expect(range.startY).toBeLessThanOrEqual(topLeft.y);
    expect(range.endY).toBeGreaterThanOrEqual(bottomRight.y);
  });
});

describe("orthogonal pipe routing", () => {
  it("snaps a port aimed off-axis onto the axis it is closest to", () => {
    expect(axisDirection({ x: 1, y: 0 })).toEqual(RIGHT);
    expect(axisDirection({ x: Math.cos(Math.PI), y: Math.sin(Math.PI) })).toEqual(LEFT);
    // The north port is drawn at -90 degrees, whose cosine is 6.1e-17 rather than 0.
    expect(axisDirection({ x: Math.cos(-Math.PI / 2), y: Math.sin(-Math.PI / 2) })).toEqual(UP);
    expect(axisDirection({ x: Math.cos(Math.PI / 2), y: Math.sin(Math.PI / 2) })).toEqual(DOWN);
    // 30 degrees is nearer the horizontal, 60 degrees nearer the vertical.
    expect(axisDirection({ x: Math.cos(Math.PI / 6), y: Math.sin(Math.PI / 6) })).toEqual(RIGHT);
    expect(axisDirection({ x: Math.cos(Math.PI / 3), y: Math.sin(Math.PI / 3) })).toEqual(DOWN);
  });

  it("leaves a run between two facing ports dead straight", () => {
    // The venturi source sits at (120, 260) with a 17-unit body, so its outlet ring is at
    // 120 + 27 = 147. The throat at (420, 260) has a 14-unit body, so its inlet is at
    // 420 - 24 = 396. Same row, facing each other: one segment, no corners.
    const route = routeOrthogonalPipe({ x: 147, y: 260 }, RIGHT, { x: 396, y: 260 }, LEFT);
    expect(route).toEqual([
      { x: 147, y: 260 },
      { x: 396, y: 260 }
    ]);
  });

  it("turns a row change into two corners on a grid line", () => {
    // Stubs put the free ends at 147 + 16 = 163 and 396 - 16 = 380, so the connector may
    // sit anywhere in [163, 380] and the midpoint is 271.5. The nearest grid line inside
    // that range is 280, which is where the two corners land.
    const route = routeOrthogonalPipe({ x: 147, y: 260 }, RIGHT, { x: 396, y: 100 }, LEFT);
    expect(route).toEqual([
      { x: 147, y: 260 },
      { x: 280, y: 260 },
      { x: 280, y: 100 },
      { x: 396, y: 100 }
    ]);
    expect(route[1].x % SCHEMATIC_GRID_SIZE).toBe(0);
  });

  it("routes around the back when the two ports face away from each other", () => {
    // The outlet at 400 faces right and the inlet at 100 faces left, so neither can be
    // reached head on: both escape a further cell (456 and 44) and meet on a lane above.
    const route = routeOrthogonalPipe({ x: 400, y: 260 }, RIGHT, { x: 100, y: 260 }, LEFT);
    expect(route).toEqual([
      { x: 400, y: 260 },
      { x: 456, y: 260 },
      { x: 456, y: 240 },
      { x: 44, y: 240 },
      { x: 44, y: 260 },
      { x: 100, y: 260 }
    ]);
  });

  it("always leaves and enters along the port axis and never runs at a diagonal", () => {
    const cases: Array<[Vec2, Vec2, Vec2, Vec2]> = [
      [{ x: 147, y: 260 }, RIGHT, { x: 396, y: 260 }, LEFT],
      [{ x: 147, y: 260 }, RIGHT, { x: 396, y: 100 }, LEFT],
      [{ x: 120, y: 233 }, UP, { x: 720, y: 287 }, DOWN],
      [{ x: 120, y: 233 }, UP, { x: 720, y: 233 }, UP],
      [{ x: 147, y: 260 }, RIGHT, { x: 300, y: 60 }, UP],
      [{ x: 120, y: 287 }, DOWN, { x: 500, y: 200 }, LEFT],
      [{ x: 400, y: 260 }, RIGHT, { x: 100, y: 260 }, LEFT],
      [{ x: 100, y: 100 }, LEFT, { x: 400, y: 400 }, RIGHT]
    ];
    for (const [start, exit, end, entry] of cases) {
      const route = routeOrthogonalPipe(start, exit, end, entry);
      expect(route[0]).toEqual(start);
      expect(route[route.length - 1]).toEqual(end);
      for (const segment of polylineSegments(route)) {
        const horizontal = segment.from.y === segment.to.y;
        const vertical = segment.from.x === segment.to.x;
        expect(horizontal || vertical).toBe(true);
      }
      const first = polylineSegments(route)[0];
      expect(axisSign(first.to.x - first.from.x)).toBe(axisSign(exit.x));
      expect(axisSign(first.to.y - first.from.y)).toBe(axisSign(exit.y));
      // The last leg runs into the port, so it travels against the port's outward normal.
      const last = polylineSegments(route).at(-1)!;
      expect(axisSign(last.to.x - last.from.x)).toBe(axisSign(-entry.x));
      expect(axisSign(last.to.y - last.from.y)).toBe(axisSign(-entry.y));
    }
  });

  it("keeps a run clear of the components it is told about", () => {
    const throat = { x: 420, y: 260 };
    const ports = [{ x: 120, y: 233 }, UP, { x: 720, y: 287 }, DOWN] as const;
    const naive = routeOrthogonalPipe(ports[0], ports[1], ports[2], ports[3]);
    const guided = routeOrthogonalPipe(ports[0], ports[1], ports[2], ports[3], {
      obstacles: [{ x: 120, y: 260 }, throat, { x: 720, y: 260 }]
    });
    // Told nothing, the detour lane lands at 400: 20 units from the throat, straight
    // through its port rings. Told where the components are, it moves out to 360.
    expect(verticalLanes(naive)).toEqual([120, 400, 720]);
    expect(verticalLanes(guided)).toEqual([120, 360, 720]);
    expect(Math.min(...verticalLanes(naive).map((lane) => Math.abs(lane - throat.x)))).toBeLessThan(ROUTE_CLEARANCE);
    // The two stubs sit on their own components by definition; the free lane clears the
    // component nobody asked it to pass through.
    for (const lane of verticalLanes(guided)) {
      expect(Math.abs(lane - throat.x)).toBeGreaterThanOrEqual(ROUTE_CLEARANCE);
    }
  });

  it("moves a pipe off a lane another pipe has already claimed", () => {
    const obstacles = [
      { x: 100, y: 100 },
      { x: 100, y: 300 },
      { x: 500, y: 100 },
      { x: 500, y: 300 }
    ];
    const first = routeOrthogonalPipe({ x: 127, y: 100 }, RIGHT, { x: 473, y: 300 }, LEFT, { obstacles });
    const unaware = routeOrthogonalPipe({ x: 127, y: 300 }, RIGHT, { x: 473, y: 100 }, LEFT, { obstacles });
    const aware = routeOrthogonalPipe({ x: 127, y: 300 }, RIGHT, { x: 473, y: 100 }, LEFT, {
      obstacles,
      occupiedLanes: routeLaneSpans(first)
    });
    // Both pipes prefer the same lane, which would draw 200 units of one straight on top
    // of the other; knowing the lane is taken moves the second pipe off it.
    expect(verticalLanes(first)).toEqual([280]);
    expect(verticalLanes(unaware)).toEqual([280]);
    expect(verticalLanes(aware)).toEqual([300]);
  });

  it("drops duplicate and mid-run points when simplifying", () => {
    expect(
      simplifyPolyline([
        { x: 0, y: 0 },
        { x: 0, y: 0 },
        { x: 50, y: 0 },
        { x: 100, y: 0 },
        { x: 100, y: 40 }
      ])
    ).toEqual([
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 40 }
    ]);
    expect(simplifyPolyline([])).toEqual([]);
  });

  it("measures, walks, and probes a routed polyline", () => {
    const route = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 300 }
    ];
    expect(polylineLength(route)).toBe(400);
    expect(pointOnPolyline(route, 0)).toEqual({ x: 0, y: 0 });
    // A quarter of 400 units is 100 along, exactly the corner.
    expect(pointOnPolyline(route, 0.25)).toEqual({ x: 100, y: 0 });
    expect(pointOnPolyline(route, 0.5)).toEqual({ x: 100, y: 100 });
    expect(pointOnPolyline(route, 1)).toEqual({ x: 100, y: 300 });
    expect(longestPolylineSegment(route)).toEqual({ from: { x: 100, y: 0 }, to: { x: 100, y: 300 } });
    // A click near the corner hits the route; the same point is 94 units from the straight
    // line between the two ends, so hit testing has to follow the run the user can see.
    expect(distanceToPolyline({ x: 90, y: 10 }, route)).toBe(10);
  });
});

describe("crossings and junctions", () => {
  const across = { id: "across", points: [{ x: 0, y: 100 }, { x: 400, y: 100 }] };
  const down = { id: "down", points: [{ x: 200, y: 0 }, { x: 200, y: 300 }] };

  it("marks a crossing on the horizontal run whichever pipe is drawn first", () => {
    const marker = { routeId: "across", segmentIndex: 0, overRouteId: "down", point: { x: 200, y: 100 } };
    expect(wireCrossings([across, down])).toEqual([marker]);
    expect(wireCrossings([down, across])).toEqual([marker]);
  });

  it("treats a run that ends on another run as a junction, not a crossing", () => {
    const tee = { id: "tee", points: [{ x: 200, y: 100 }, { x: 200, y: 300 }] };
    expect(segmentCrossing({ from: across.points[0], to: across.points[1] }, { from: tee.points[0], to: tee.points[1] })).toBeNull();
    expect(wireCrossings([across, tee])).toEqual([]);
    expect(wireJunctions([across, tee])).toEqual([{ x: 200, y: 100 }]);
    // Two runs that merely cross share no endpoint, so nothing is marked as connected.
    expect(wireJunctions([across, down])).toEqual([]);
  });

  it("ignores parallel runs and finds every crossing on a multi-corner route", () => {
    const parallel = { id: "parallel", points: [{ x: 0, y: 100 }, { x: 400, y: 100 }] };
    expect(wireCrossings([across, parallel])).toEqual([]);
    const zigzag = {
      id: "zigzag",
      points: [{ x: 100, y: 0 }, { x: 100, y: 200 }, { x: 300, y: 200 }, { x: 300, y: 0 }]
    };
    expect(wireCrossings([across, zigzag]).map((crossing) => crossing.point)).toEqual([
      { x: 100, y: 100 },
      { x: 300, y: 100 }
    ]);
  });

  it("reports every straight run of a route so the next route can avoid them", () => {
    expect(routeLaneSpans([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 300 }])).toEqual([
      { axis: "horizontal", position: 0, from: 0, to: 100 },
      { axis: "vertical", position: 100, from: 0, to: 300 }
    ]);
  });
});

describe("label placement", () => {
  it("boxes a routed run so a chip can be tested against it", () => {
    expect(polylineObstacleBoxes([{ x: 0, y: 100 }, { x: 400, y: 100 }], 10)).toEqual([
      { left: -10, right: 410, top: 90, bottom: 110 }
    ]);
    expect(boxesOverlap({ left: 0, top: 0, right: 10, bottom: 10 }, { left: 9, top: 9, right: 20, bottom: 20 })).toBe(true);
    expect(boxesOverlap({ left: 0, top: 0, right: 10, bottom: 10 }, { left: 10, top: 0, right: 20, bottom: 10 })).toBe(false);
  });

  it("offers the preferred side first and then walks outwards", () => {
    const candidates = labelPlacementCandidates({ x: 200, y: 100 }, 60, 20, { preferred: "below", step: 13, rings: 2 });
    expect(candidates).toHaveLength(8);
    expect(candidates[0]).toEqual({ left: 170, top: 100, right: 230, bottom: 120 });
    expect(candidates[1]).toEqual({ left: 170, top: 80, right: 230, bottom: 100 });
    expect(candidates[4]).toEqual({ left: 170, top: 113, right: 230, bottom: 133 });
  });

  it("pushes a chip off a routed run instead of stamping it on the pipe", () => {
    const wire = polylineObstacleBoxes([{ x: 0, y: 100 }, { x: 400, y: 100 }], 10);
    const candidates = labelPlacementCandidates({ x: 200, y: 100 }, 60, 20, { preferred: "below", step: 13, rings: 4 });
    // Hard against the wire the chip would sit on it, so the first clear place is one
    // step below: the box top moves to 100 + 13 = 113, clear of the run's 110 edge.
    expect(firstClearBox(candidates, [])).toEqual({ left: 170, top: 100, right: 230, bottom: 120 });
    expect(firstClearBox(candidates, wire)).toEqual({ left: 170, top: 113, right: 230, bottom: 133 });
    for (const box of wire) expect(boxesOverlap(box, firstClearBox(candidates, wire)!)).toBe(false);
    // A chip with nowhere to go is reported as unplaceable rather than drawn over a pipe.
    expect(firstClearBox(candidates, [{ left: -1e4, top: -1e4, right: 1e4, bottom: 1e4 }])).toBeNull();
  });
});

describe("tidy layout", () => {
  it("lays a chain out left to right on the grid", () => {
    const arranged = tidySchematicLayout(venturiPreset);
    // Three components on one chain: one column each, 5 grid cells apart, on the row the
    // drawing already occupies. minX 120 snaps to 120; the vertical centre 260 snaps to 280.
    expect(arranged).toEqual({
      source: { x: 120, y: 280 },
      throat: { x: 320, y: 280 },
      sink: { x: 520, y: 280 }
    });
    expect(arranged.throat.x - arranged.source.x).toBe(SCHEMATIC_GRID_SIZE * 5);
  });

  it("unpicks crossed wires and keeps every component on a free grid cell", () => {
    const project = crossedProject();
    const before = schematicNodePositions(project);
    expect(countEdgeCrossings(project, before)).toBe(1);

    const arranged = tidySchematicLayout(project);
    expect(countEdgeCrossings(project, arranged)).toBe(0);
    // `a` wires to the lower sink, so the barycentre sweep lifts that sink to the top row
    // and the two pipes run parallel instead of over one another.
    expect(arranged).toEqual({
      a: { x: 120, y: 160 },
      b: { x: 120, y: 280 },
      lower: { x: 320, y: 160 },
      upper: { x: 320, y: 280 }
    });

    const placed = Object.values(arranged);
    for (const position of placed) {
      expect(position.x % SCHEMATIC_GRID_SIZE).toBe(0);
      expect(position.y % SCHEMATIC_GRID_SIZE).toBe(0);
    }
    for (let first = 0; first < placed.length; first += 1) {
      for (let second = first + 1; second < placed.length; second += 1) {
        expect(Math.hypot(placed[first].x - placed[second].x, placed[first].y - placed[second].y)).toBeGreaterThanOrEqual(
          MIN_NODE_SEPARATION
        );
      }
    }
  });

  it("survives an empty project and a recirculating loop", () => {
    expect(tidySchematicLayout({ ...venturiPreset, nodes: {}, edges: {} })).toEqual({});
    const looped: FluidProject = {
      ...venturiPreset,
      edges: {
        ...venturiPreset.edges,
        back: { ...venturiPreset.edges.inlet, id: "back", from: "sink", to: "source", fromPort: "north", toPort: "south" }
      }
    };
    const arranged = tidySchematicLayout(looped);
    expect(Object.keys(arranged).sort()).toEqual(["sink", "source", "throat"]);
    for (const position of Object.values(arranged)) {
      expect(Number.isFinite(position.x) && Number.isFinite(position.y)).toBe(true);
      expect(position.x % SCHEMATIC_GRID_SIZE).toBe(0);
    }
  });

  it("aims every component along the flow so its ports face the runs that reach it", () => {
    // The preset arrives with the throat and the outlet turned to 180 degrees, which puts
    // the throat's inlet on its downstream side and forces the router to loop a run all
    // the way round the body. Tidying points each outlet at what it feeds instead.
    expect(venturiPreset.nodes.throat.rotation ?? 0).toBe(0);
    const misaimed: FluidProject = {
      ...venturiPreset,
      nodes: {
        ...venturiPreset.nodes,
        throat: { ...venturiPreset.nodes.throat, rotation: 180 },
        sink: { ...venturiPreset.nodes.sink, rotation: 180 }
      }
    };
    // source -> throat -> sink all run left to right, so every component faces 0 degrees:
    // the sink has nothing downstream and is turned away from the throat that feeds it.
    expect(tidySchematicRotations(misaimed)).toEqual({ source: 0, throat: 0, sink: 0 });

    const stacked: FluidProject = {
      ...misaimed,
      nodes: {
        source: { ...venturiPreset.nodes.source, position: { x: 200, y: 100 } },
        throat: { ...venturiPreset.nodes.throat, position: { x: 200, y: 300 } },
        sink: { ...venturiPreset.nodes.sink, position: { x: 200, y: 500 } }
      }
    };
    // The same chain drawn top to bottom aims every component straight down.
    expect(tidySchematicRotations(stacked)).toEqual({ source: 90, throat: 90, sink: 90 });
  });

  it("counts only pipes that genuinely cross, not pipes that share a component", () => {
    const project = crossedProject();
    // Both pipes leave the same component, so they meet by design rather than crossing.
    const shared: FluidProject = {
      ...project,
      edges: {
        first: { ...project.edges.first, from: "a", to: "lower" },
        second: { ...project.edges.second, from: "a", to: "upper", fromPort: "north" }
      }
    };
    expect(countEdgeCrossings(shared, schematicNodePositions(shared))).toBe(0);
  });
});

/**
 * A network the router has to bend: the source feeds a sink two rows down, so no run
 * between them can be a single straight line.
 */
function bentProject(): FluidProject {
  return {
    ...venturiPreset,
    nodes: {
      source: { ...venturiPreset.nodes.source, position: { x: 120, y: 120 }, rotation: 0 },
      sink: { ...venturiPreset.nodes.sink, position: { x: 520, y: 400 }, rotation: 0 }
    },
    edges: {
      only: { ...venturiPreset.edges.inlet, id: "only", from: "source", to: "sink" }
    },
    sweeps: []
  };
}

describe("geometry shared by the schematic and the 3D view", () => {
  it("puts a port on the component's own edge, facing the way the component is aimed", () => {
    const node = { ...venturiPreset.nodes.source, position: { x: 100, y: 100 }, rotation: 0 };

    // A source body is 17 units across and the ring stands SCHEMATIC_PORT_OFFSET clear of it.
    expect(schematicNodeRadius(node)).toBe(17);
    expect(schematicPortPosition(node, "outlet")).toEqual({ x: 100 + 17 + SCHEMATIC_PORT_OFFSET, y: 100 });
    expect(schematicPortDirection(node, "outlet").x).toBeCloseTo(1, 9);
    expect(schematicPortDirection(node, "inlet").x).toBeCloseTo(-1, 9);
    // Turning the component turns its ports with it.
    expect(schematicPortPosition({ ...node, rotation: 90 }, "outlet").y).toBeCloseTo(127, 9);
  });

  it("gives the 3D view the very routes the schematic draws", () => {
    const project = bentProject();
    const routes = buildSchematicRoutes(project);

    expect(routes.map((route) => route.id)).toEqual(["only"]);
    // Rebuilt from the same project, the answer is identical - so two views cannot drift.
    expect(buildSchematicRoutes(project)).toEqual(routes);
    // And it is the same polyline the single-edge helper produces.
    expect(routes[0].points).toEqual(schematicEdgeRoute(project.edges.only, project.nodes));
    // The route genuinely turns: a straight line would be two points.
    expect(routes[0].points.length).toBeGreaterThan(2);
  });

  it("routes each pipe against the lanes the pipes before it already took", () => {
    const project = crossedProject();
    const routes = buildSchematicRoutes(project);
    const lanes = routes.map((route) => verticalLanes(route.points));

    expect(routes).toHaveLength(2);
    // Two runs sharing a lane would draw one on top of the other.
    for (const lane of lanes[0]) expect(lanes[1]).not.toContain(lane);
  });

  it("skips an edge whose components are missing rather than inventing a route", () => {
    const project = bentProject();
    const dangling: FluidProject = {
      ...project,
      edges: { ...project.edges, ghost: { ...project.edges.only, id: "ghost", from: "source", to: "nowhere" } }
    };

    expect(schematicEdgeRoute(dangling.edges.ghost, dangling.nodes)).toBeNull();
    expect(buildSchematicRoutes(dangling).map((route) => route.id)).toEqual(["only"]);
  });

  it("reports the direction of travel along a route, corner by corner", () => {
    const route = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 }
    ];

    expect(tangentOnPolyline(route, 0)).toEqual({ x: 1, y: 0 });
    expect(tangentOnPolyline(route, 0.9)).toEqual({ x: 0, y: 1 });
    expect(tangentOnPolyline(route, 1)).toEqual({ x: 0, y: 1 });
    // Degenerate input still yields a usable direction rather than a NaN.
    expect(tangentOnPolyline([{ x: 5, y: 5 }], 0.5)).toEqual({ x: 1, y: 0 });
    expect(tangentOnPolyline([], 0.5)).toEqual({ x: 1, y: 0 });
  });

  it("turns a right-angle corner into a constant-radius bend", () => {
    const rounded = roundPolylineCorners(
      [
        { x: 0, y: 0 },
        { x: 100, y: 0 },
        { x: 100, y: 100 }
      ],
      20,
      4
    );

    // The corner point itself is gone, replaced by an arc that starts and ends on the runs.
    expect(rounded).not.toContainEqual({ x: 100, y: 0 });
    expect(rounded[0]).toEqual({ x: 0, y: 0 });
    expect(rounded.at(-1)).toEqual({ x: 100, y: 100 });
    // A quarter turn has tan(45) = 1, so the arc radius equals the tangent length.
    const centre = { x: 80, y: 20 };
    for (const point of rounded.slice(1, -1)) {
      expect(Math.hypot(point.x - centre.x, point.y - centre.y)).toBeCloseTo(20, 6);
    }
    // The bend stays inside the corner it replaced.
    for (const point of rounded) {
      expect(point.x).toBeLessThanOrEqual(100 + 1e-9);
      expect(point.y).toBeGreaterThanOrEqual(-1e-9);
    }
  });

  it("never lets a bend eat more than half of either run it joins", () => {
    const tight = roundPolylineCorners(
      [
        { x: 0, y: 0 },
        { x: 30, y: 0 },
        { x: 30, y: 200 }
      ],
      100,
      4
    );

    // The 30-unit run caps the fillet at 15, so the arc still starts on the first run
    // rather than doubling back past its start.
    for (const point of tight) expect(point.x).toBeGreaterThanOrEqual(-1e-9);
    expect(Math.min(...tight.map((point) => point.x))).toBeCloseTo(0, 9);
    expect(polylineLength(tight)).toBeGreaterThan(0);
  });

  it("leaves a straight run, and a route with nothing to round, exactly as it found it", () => {
    const straight = [
      { x: 0, y: 0 },
      { x: 100, y: 0 }
    ];

    expect(roundPolylineCorners(straight, 20)).toEqual(straight);
    expect(roundPolylineCorners(straight, 0)).toEqual(straight);
    expect(roundPolylineCorners([{ x: 4, y: 4 }], 20)).toEqual([{ x: 4, y: 4 }]);
    expect(roundPolylineCorners([], 20)).toEqual([]);
  });

  it("keeps a rounded route on the lanes the schematic routed it down", () => {
    const project = bentProject();
    const route = buildSchematicRoutes(project)[0].points;
    const rounded = roundPolylineCorners(route, 12, 5);

    expect(rounded[0]).toEqual(route[0]);
    expect(rounded.at(-1)).toEqual(route.at(-1));
    // Every rounded point stays on or inside the routed path, so the 3D pipe cannot
    // wander off the run the schematic drew.
    for (const point of rounded) expect(distanceToPolyline(point, route)).toBeLessThanOrEqual(12 + 1e-6);
  });
});

describe("Camera angles", () => {
  it("folds a negative yaw onto the turn every control reads, unchanged when it is already there", () => {
    expect(normalizeYawDegrees(-32)).toBe(-32);
    expect(normalizeYawDegrees(-179)).toBe(-179);
    expect(normalizeYawDegrees(0)).toBe(0);
    expect(normalizeYawDegrees(179)).toBe(179);
  });

  it("wraps a yaw that an orbit drag has run past a full turn", () => {
    // The orbit adds 0.45 degrees per pixel dragged and never wraps, so two turns of
    // the wrist really do leave the stored yaw here. -572 is one such reading taken
    // off the running app; the slider it feeds only goes to -180.
    expect(normalizeYawDegrees(-572)).toBe(148);
    expect(normalizeYawDegrees(400)).toBe(40);
    expect(normalizeYawDegrees(360)).toBe(0);
    expect(normalizeYawDegrees(-360)).toBe(0);
    expect(normalizeYawDegrees(1080 + 37)).toBe(37);
    expect(normalizeYawDegrees(-1080 - 37)).toBe(-37);
  });

  it("reports the two ends of the turn as the same bearing, and settles on the positive one", () => {
    expect(normalizeYawDegrees(180)).toBe(180);
    expect(normalizeYawDegrees(-180)).toBe(180);
    expect(normalizeYawDegrees(540)).toBe(180);
    // Idempotent, so normalising twice can never walk the camera round.
    expect(normalizeYawDegrees(normalizeYawDegrees(-180))).toBe(180);
  });

  it("never returns a yaw a symmetric slider cannot represent", () => {
    for (const yaw of [-1000, -572, -180.0001, -0.5, 0, 12.25, 180, 359, 721]) {
      const normalized = normalizeYawDegrees(yaw);
      expect(normalized).toBeGreaterThan(-180);
      expect(normalized).toBeLessThanOrEqual(180);
      // Same bearing: the difference is a whole number of turns.
      expect(Math.abs(((yaw - normalized) % 360) % 360)).toBeLessThan(1e-9);
    }
  });

  it("falls back rather than letting a broken angle through", () => {
    expect(normalizeYawDegrees(Number.NaN)).toBe(0);
    expect(normalizeYawDegrees(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it("lets pitch reach both poles, which is what puts the plan view in reach", () => {
    expect(CINEMA_MAX_PITCH).toBe(90);
    expect(CINEMA_MIN_PITCH).toBe(-90);
    expect(clampCinemaPitch(90)).toBe(90);
    expect(clampCinemaPitch(-90)).toBe(-90);
    expect(clampCinemaPitch(140)).toBe(90);
    expect(clampCinemaPitch(-140)).toBe(-90);
  });

  it("canonicalises a whole camera without touching what the viewer framed", () => {
    const drifted = { yaw: -572, pitch: 140, zoom: 1.4, pan: { x: 2, y: -3 } };
    const settled = normalizeCinemaCamera(drifted);

    expect(settled.yaw).toBe(148);
    expect(settled.pitch).toBe(90);
    expect(settled.zoom).toBe(1.4);
    expect(settled.pan).toEqual({ x: 2, y: -3 });
    // A copy, so a normalised camera cannot alias the one it came from.
    expect(settled.pan).not.toBe(drifted.pan);
  });
});

describe("Principal planes", () => {
  const framed = { yaw: -572, pitch: 11, zoom: 1.35, pan: { x: 4, y: -2 } };

  it("offers the plan and both elevations, not just the plane the schematic is drawn on", () => {
    expect(cinemaPlaneOrientations.xy.pitch).toBe(CINEMA_MAX_PITCH);
    expect(cinemaPlaneOrientations.xz).toMatchObject({ yaw: 0, pitch: 0 });
    expect(cinemaPlaneOrientations.yz).toMatchObject({ yaw: 90, pitch: 0 });
  });

  it("turns the camera onto a plane while keeping the viewer's own zoom and pan", () => {
    const plan = cinemaCameraForPlane("xy", framed);

    expect(plan.pitch).toBe(CINEMA_MAX_PITCH);
    expect(plan.zoom).toBe(1.35);
    expect(plan.pan).toEqual({ x: 4, y: -2 });
    expect(plan.pan).not.toBe(framed.pan);
  });

  it("recognises the plane it is on, however far round the yaw has wound", () => {
    expect(cinemaViewPlaneOf(cinemaCameraForPlane("xy", framed))).toBe("xy");
    expect(cinemaViewPlaneOf(cinemaCameraForPlane("xz", framed))).toBe("xz");
    expect(cinemaViewPlaneOf(cinemaCameraForPlane("yz", framed))).toBe("yz");
    expect(cinemaViewPlaneOf(cinemaCameraForPlane("iso", framed))).toBe("iso");
    // A negative and a wound-up bearing name the same plane as the positive one.
    expect(cinemaViewPlaneOf({ yaw: -270, pitch: 0 })).toBe("yz");
    expect(cinemaViewPlaneOf({ yaw: 450, pitch: 0 })).toBe("yz");
    expect(cinemaViewPlaneOf({ yaw: -180, pitch: 0 })).toBe("xz");
    // Underneath is still the plane the schematic is drawn on, seen from below.
    expect(cinemaViewPlaneOf({ yaw: -572, pitch: CINEMA_MIN_PITCH })).toBe("xy");
  });

  it("says nothing rather than guessing when the camera is between planes", () => {
    expect(cinemaViewPlaneOf({ yaw: 27, pitch: 13 })).toBeNull();
    expect(cinemaViewPlaneOf({ yaw: 45, pitch: 0 })).toBeNull();
  });
});
