import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
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
function pointer(type: "pointerDown" | "pointerMove" | "pointerUp", x: number, y: number) {
  const element = canvas();
  const scale = Number(element.dataset.viewScale ?? 1);
  const offsetX = Number(element.dataset.viewOffsetX ?? 0);
  const offsetY = Number(element.dataset.viewOffsetY ?? 0);
  fireEvent[type](element, {
    clientX: x * scale + offsetX,
    clientY: y * scale + offsetY,
    pointerId: 1,
    button: 0
  });
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
