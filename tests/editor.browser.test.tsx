import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { buildEdgeRoutes, portPosition } from "../src/components/SimulationCanvas";
import { MIN_NODE_SEPARATION, SCHEMATIC_GRID_SIZE } from "../src/components/viewportModel";
import { venturiPreset } from "../src/data/presets";
import { useFlowStore } from "../src/state/useFlowStore";

const canvasContext = {
  setTransform: vi.fn(),
  clearRect: vi.fn(),
  createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  fillRect: vi.fn(),
  beginPath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  closePath: vi.fn(),
  quadraticCurveTo: vi.fn(),
  stroke: vi.fn(),
  fill: vi.fn(),
  arc: vi.fn(),
  rect: vi.fn(),
  save: vi.fn(),
  restore: vi.fn(),
  translate: vi.fn(),
  scale: vi.fn(),
  rotate: vi.fn(),
  fillText: vi.fn(),
  measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
  setLineDash: vi.fn()
};

function renderEditor() {
  return render(<App />);
}

function canvas() {
  return screen.getByTestId("schematic-canvas");
}

/**
 * Drives the editor in schematic world coordinates. The canvas publishes its own
 * pan/zoom, so the test stays correct when the auto-fit framing changes.
 */
function pointer(
  type: "pointerDown" | "pointerMove" | "pointerUp",
  x: number,
  y: number,
  modifiers: { shiftKey?: boolean; altKey?: boolean } = {}
) {
  const element = canvas();
  const scale = Number(element.dataset.viewScale ?? 1);
  const offsetX = Number(element.dataset.viewOffsetX ?? 0);
  const offsetY = Number(element.dataset.viewOffsetY ?? 0);
  fireEvent[type](element, {
    clientX: x * scale + offsetX,
    clientY: y * scale + offsetY,
    pointerId: 1,
    button: 0,
    shiftKey: false,
    altKey: false,
    ...modifiers
  });
}

/** Selects a component the way a user does, and reads back where its rotate handle is drawn. */
function selectAndGrabHandle(nodeId: string) {
  const node = useFlowStore.getState().project.nodes[nodeId];
  pointer("pointerDown", node.position.x, node.position.y);
  pointer("pointerUp", node.position.x, node.position.y);
  const element = canvas();
  expect(element.dataset.rotateHandle).toBe(nodeId);
  return {
    element,
    node,
    scale: Number(element.dataset.viewScale ?? 1),
    handle: { x: Number(element.dataset.rotateHandleX), y: Number(element.dataset.rotateHandleY) }
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
  vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(canvasContext as unknown as CanvasRenderingContext2D);
  HTMLCanvasElement.prototype.setPointerCapture = vi.fn();
  HTMLCanvasElement.prototype.releasePointerCapture = vi.fn();
  HTMLCanvasElement.prototype.hasPointerCapture = vi.fn(() => true);
  Element.prototype.getBoundingClientRect = vi.fn(() => ({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 1000,
    bottom: 700,
    width: 1000,
    height: 700,
    toJSON: () => ({})
  }));
  useFlowStore.getState().setProject({ ...venturiPreset, visualization: { ...venturiPreset.visualization, mode: "design" } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FlowLab editor browser workflows", () => {
  it("creates, drags, rotates, and deletes a component through the rendered editor", async () => {
    renderEditor();

    fireEvent.click(screen.getByTitle("Add pump"));
    let pump = useFlowStore.getState().project.nodes["pump-4"];
    expect(pump).toBeDefined();

    await screen.findByRole("heading", { name: "Pump" });
    fireEvent.change(await screen.findByTestId("rotation-degrees-input"), { target: { value: "45" } });
    expect(useFlowStore.getState().project.nodes["pump-4"].rotation).toBe(45);

    pointer("pointerDown", pump.position.x, pump.position.y);
    pointer("pointerMove", pump.position.x + 60, pump.position.y + 50);
    pointer("pointerUp", pump.position.x + 60, pump.position.y + 50);
    pump = useFlowStore.getState().project.nodes["pump-4"];
    // The drop snaps to the grid, and to the closest free cell when the requested one is
    // too near another component: (396, 230) rounds to (400, 240), which crowds the
    // throat at (420, 260), so the component lands on the neighbouring free cell.
    expect(pump.position).toEqual({ x: 360, y: 200 });
    expect(pump.position.x % SCHEMATIC_GRID_SIZE).toBe(0);
    expect(pump.position.y % SCHEMATIC_GRID_SIZE).toBe(0);
    for (const other of Object.values(useFlowStore.getState().project.nodes)) {
      if (other.id === pump.id) continue;
      expect(Math.hypot(other.position.x - pump.position.x, other.position.y - pump.position.y)).toBeGreaterThanOrEqual(
        MIN_NODE_SEPARATION
      );
    }

    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(useFlowStore.getState().project.nodes["pump-4"]).toBeUndefined());
  });

  it("rotates a component by dragging its canvas handle, snapping to 15 degrees", () => {
    renderEditor();
    // The handle belongs to the selection: a pipe is selected on load, so there is none.
    expect(canvas().dataset.rotateHandle).toBeUndefined();

    const { element, node, scale, handle } = selectAndGrabHandle("throat");
    expect(useFlowStore.getState().project.nodes.throat.rotation).toBe(0);

    // The handle stands one screen-pixel gap beyond the outlet port ring, on the outlet
    // axis, so it reads as the component's aim and never sits on a port at any zoom.
    const port = portPosition(node, "outlet");
    expect(Math.hypot(handle.x - port.x, handle.y - port.y) * scale).toBeCloseTo(26, 6);
    const acrossAxis =
      (handle.y - node.position.y) * (port.x - node.position.x)
      - (handle.x - node.position.x) * (port.y - node.position.y);
    expect(Math.abs(acrossAxis)).toBeLessThan(1e-6);

    pointer("pointerDown", handle.x, handle.y);
    // Grabbing the handle does not snatch the component round to the pointer.
    expect(element.dataset.rotateNode).toBe("throat");
    expect(element.dataset.rotateAngle).toBe("0");
    expect(element.dataset.rotateSnap).toBe("15");

    // A bearing of 62.2 degrees lands on the 60 degree tick, not on the raw angle.
    pointer("pointerMove", node.position.x + 100, node.position.y + 190);
    expect(element.dataset.rotateAngle).toBe("60");
    pointer("pointerUp", node.position.x + 100, node.position.y + 190);

    expect(useFlowStore.getState().project.nodes.throat.rotation).toBe(60);
    // Committed through onRotateNode, so it is on the undo stack like any other edit.
    expect(useFlowStore.getState().canUndo).toBe(true);
    useFlowStore.getState().undo();
    expect(useFlowStore.getState().project.nodes.throat.rotation).toBe(0);
    expect(element.dataset.rotateNode).toBeUndefined();
    expect(element.dataset.rotateAngle).toBeUndefined();
  });

  it("takes 45 degree steps with Shift and a free angle with Alt", () => {
    renderEditor();
    const { element, node, handle } = selectAndGrabHandle("throat");
    const to = { x: node.position.x + 100, y: node.position.y + 190 };

    pointer("pointerDown", handle.x, handle.y);
    pointer("pointerMove", to.x, to.y);
    expect(element.dataset.rotateAngle).toBe("60");
    expect(element.dataset.rotateSnap).toBe("15");

    pointer("pointerMove", to.x, to.y, { shiftKey: true });
    expect(element.dataset.rotateAngle).toBe("45");
    expect(element.dataset.rotateSnap).toBe("45");

    pointer("pointerMove", to.x, to.y, { altKey: true });
    expect(element.dataset.rotateAngle).toBe("62");
    expect(element.dataset.rotateSnap).toBe("free");

    pointer("pointerUp", to.x, to.y, { altKey: true });
    expect(useFlowStore.getState().project.nodes.throat.rotation).toBe(62);
  });

  it("leaves the ports, the body, and the undo stack alone when the handle is only pressed", () => {
    renderEditor();
    const { element, node, handle } = selectAndGrabHandle("throat");
    const port = portPosition(node, "outlet");

    // Hovering walks the three affordances: body, port ring, rotate handle. Each has its
    // own cursor, and the handle's is neither of the other two.
    pointer("pointerMove", node.position.x, node.position.y);
    expect(element.dataset.hoverKind).toBe("node");
    expect(element.style.cursor).toBe("move");

    pointer("pointerMove", port.x, port.y);
    expect(element.dataset.hoverKind).toBe("port");
    expect(element.style.cursor).toBe("crosshair");

    pointer("pointerMove", handle.x, handle.y);
    expect(element.dataset.hoverKind).toBe("rotate");
    expect(element.style.cursor).not.toBe("crosshair");
    expect(element.style.cursor).not.toBe("move");
    // A turn-arrow glyph that falls back to the canvas's own grab cursor.
    expect(element.style.cursor).toMatch(/^url\("data:image\/svg\+xml/);
    expect(element.style.cursor).toMatch(/, grab$/);

    // Pressing the port still starts a connection, never a rotation.
    pointer("pointerDown", port.x, port.y);
    expect(element.dataset.rotateNode).toBeUndefined();
    pointer("pointerUp", port.x, port.y);

    // A press on the handle that turns nothing is a selection, not an empty undo step.
    pointer("pointerDown", handle.x, handle.y);
    pointer("pointerUp", handle.x, handle.y);
    expect(useFlowStore.getState().project.nodes.throat.rotation).toBe(0);
    expect(useFlowStore.getState().canUndo).toBe(false);
  });

  it("re-routes the pipes of a rotated component off its moved ports", () => {
    renderEditor();
    const { node, handle } = selectAndGrabHandle("throat");

    pointer("pointerDown", handle.x, handle.y);
    // 87.1 degrees below the centre snaps to a quarter turn.
    pointer("pointerMove", node.position.x + 10, node.position.y + 200);
    pointer("pointerUp", node.position.x + 10, node.position.y + 200);

    const project = useFlowStore.getState().project;
    const throat = project.nodes.throat;
    expect(throat.rotation).toBe(90);

    // Turning the component turns its ports: the outlet is now south, the inlet north.
    const outletPort = portPosition(throat, "outlet");
    const inletPort = portPosition(throat, "inlet");
    expect(outletPort.x).toBeCloseTo(throat.position.x, 6);
    expect(outletPort.y).toBeGreaterThan(throat.position.y);
    expect(inletPort.x).toBeCloseTo(throat.position.x, 6);
    expect(inletPort.y).toBeLessThan(throat.position.y);

    const routes = new Map(buildEdgeRoutes(project).map((route) => [route.id, route.points]));
    const downstream = routes.get("outlet");
    const upstream = routes.get("inlet");
    if (!downstream || !upstream) throw new Error("both pipes must still be routed");

    // Each run starts and ends on the moved port, and the segment touching the port lies
    // on the port axis rather than cutting across the component.
    expect(downstream[0].x).toBeCloseTo(outletPort.x, 6);
    expect(downstream[0].y).toBeCloseTo(outletPort.y, 6);
    expect(downstream[1].x).toBeCloseTo(outletPort.x, 6);
    expect(downstream[1].y).toBeGreaterThan(outletPort.y);

    const arrival = upstream[upstream.length - 1];
    const approach = upstream[upstream.length - 2];
    expect(arrival.x).toBeCloseTo(inletPort.x, 6);
    expect(arrival.y).toBeCloseTo(inletPort.y, 6);
    expect(approach.x).toBeCloseTo(inletPort.x, 6);
    expect(approach.y).toBeLessThan(inletPort.y);
  });

  it("creates a pipe by dragging from one rendered port to another", () => {
    renderEditor();
    expect(Object.keys(useFlowStore.getState().project.edges)).toHaveLength(2);

    pointer("pointerDown", 120, 260);
    pointer("pointerUp", 120, 260);
    pointer("pointerDown", 120, 233);
    pointer("pointerMove", 720, 233);
    pointer("pointerUp", 720, 233);

    const edges = Object.values(useFlowStore.getState().project.edges);
    expect(edges).toHaveLength(3);
    // The drop point is above the sink. It resolved to "south" only because the
    // sink was inferred backwards; with the component aimed along the flow, a
    // point above it is its north port.
    expect(edges.at(-1)).toMatchObject({ from: "source", to: "sink", fromPort: "north", toPort: "north" });

    pointer("pointerDown", 120, 233);
    pointer("pointerMove", 720, 233);
    pointer("pointerUp", 720, 233);
    expect(Object.values(useFlowStore.getState().project.edges)).toHaveLength(3);
  });

  it("edits a selected pipe endpoint from the inspector", () => {
    renderEditor();

    fireEvent.change(screen.getByLabelText("To port"), { target: { value: "south" } });
    fireEvent.change(screen.getByLabelText("To node"), { target: { value: "sink" } });

    expect(useFlowStore.getState().project.edges.inlet.to).toBe("sink");
    expect(useFlowStore.getState().project.edges.inlet.toPort).toBe("south");
    expect(useFlowStore.getState().result.edgeResults.inlet).toBeDefined();
  });

  it("persists project edits to localStorage and restores them on reload", async () => {
    const first = renderEditor();
    fireEvent.click(screen.getByTitle("Add sink"));

    await waitFor(() => expect(localStorage.getItem("flowlab.project.v1")).toContain("sink-4"));
    first.unmount();
    useFlowStore.getState().setProject({ ...venturiPreset, visualization: { ...venturiPreset.visualization, mode: "design" } });

    renderEditor();
    await waitFor(() => expect(useFlowStore.getState().project.nodes["sink-4"]).toBeDefined());
  });

  it("shows a validation error when an imported project is invalid", async () => {
    renderEditor();
    const file = new File(["{}"], "invalid.flowlab.json", { type: "application/json" });

    fireEvent.change(screen.getByTestId("project-import-file"), { target: { files: [file] } });

    expect(await screen.findByText(/Invalid project:/)).toBeInTheDocument();
  });

  it("renders the desktop workspace shell", () => {
    const width = 1440;
    const height = 900;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
    window.dispatchEvent(new Event("resize"));

    const { container } = renderEditor();

    expect(container.querySelector(".workspace-shell")).toBeInTheDocument();
    expect(canvas()).toBeInTheDocument();
    expect(screen.getByText("Components")).toBeInTheDocument();
    expect(screen.getAllByText("Inspector").length).toBeGreaterThan(0);
  });

  it("surfaces live topology validation warnings in the analysis dock", async () => {
    renderEditor();

    fireEvent.click(screen.getByTitle("Add sink"));

    expect(await screen.findByText(/disconnected components/i)).toBeInTheDocument();
    expect(screen.getByText(/is not connected to the hydraulic network/i)).toBeInTheDocument();
  });
});
