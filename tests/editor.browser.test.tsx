import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
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
  setLineDash: vi.fn()
};

function renderEditor() {
  return render(<App />);
}

function canvas() {
  return screen.getByTestId("simulation-canvas");
}

function pointer(type: "pointerDown" | "pointerMove" | "pointerUp", x: number, y: number) {
  fireEvent[type](canvas(), {
    clientX: x,
    clientY: y,
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
  useFlowStore.getState().setProject(venturiPreset);
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
    expect(pump.position).toEqual({ x: 396, y: 230 });

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
    expect(edges.at(-1)).toMatchObject({ from: "source", to: "sink", fromPort: "north", toPort: "south" });

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
    useFlowStore.getState().setProject(venturiPreset);

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
    expect(screen.getByText("Inspector")).toBeInTheDocument();
  });

  it("surfaces live topology validation warnings in the analysis dock", async () => {
    renderEditor();

    fireEvent.click(screen.getByTitle("Add sink"));

    expect(await screen.findByText(/disconnected components/i)).toBeInTheDocument();
    expect(screen.getByText(/is not connected to the hydraulic network/i)).toBeInTheDocument();
  });
});
