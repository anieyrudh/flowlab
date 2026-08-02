import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DUAL_VIEW_WORKSPACE_STORAGE_KEY,
  DualViewWorkspace,
  WORKSPACE_VIEW_MODE_STORAGE_KEY,
  readWorkspaceSplit,
  readWorkspaceViewMode
} from "./DualViewWorkspace";

beforeEach(() => {
  window.localStorage.clear();
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
  HTMLButtonElement.prototype.setPointerCapture = vi.fn();
  HTMLButtonElement.prototype.releasePointerCapture = vi.fn();
  HTMLButtonElement.prototype.hasPointerCapture = vi.fn(() => true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderWorkspace() {
  return render(
    <DualViewWorkspace
      header={<span>Shared result selector</span>}
      schematic={<div data-testid="schematic-canvas">Schematic canvas</div>}
      cinema={<div data-testid="cinema-canvas">3D canvas</div>}
    />
  );
}

describe("DualViewWorkspace", () => {
  it("uses a safe 50/50 default when local storage is malformed", async () => {
    window.localStorage.setItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY, "not JSON");
    renderWorkspace();

    const divider = screen.getByTestId("workspace-divider");
    expect(divider).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByTestId("schematic-canvas")).toBeVisible();
    expect(screen.getByTestId("cinema-canvas")).toBeVisible();

    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY) ?? "{}")).toEqual({
        version: 1,
        schematicRatio: 50
      });
    });
  });

  it("clamps, persists, and exposes keyboard and pointer divider adjustments", async () => {
    window.localStorage.setItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY, JSON.stringify({ version: 1, schematicRatio: 90 }));
    const { container } = renderWorkspace();
    const divider = screen.getByTestId("workspace-divider");

    expect(divider).toHaveAttribute("aria-valuenow", "60");
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(divider).toHaveAttribute("aria-valuenow", "55");
    fireEvent.keyDown(divider, { key: "Home" });
    expect(divider).toHaveAttribute("aria-valuenow", "40");
    fireEvent.pointerDown(divider, { clientX: 560, pointerId: 4 });
    expect(divider).toHaveAttribute("aria-valuenow", "56");

    expect(container.querySelector(".dual-view-workspace")).toHaveStyle({ "--schematic-ratio": "56%" });
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY) ?? "{}")).toEqual({
        version: 1,
        schematicRatio: 56
      });
    });
  });

  it("only accepts the versioned divider preference", () => {
    expect(readWorkspaceSplit(JSON.stringify({ version: 2, schematicRatio: 45 }))).toBe(50);
    expect(readWorkspaceSplit(JSON.stringify({ version: 1, schematicRatio: 39.6 }))).toBe(40);
    expect(readWorkspaceSplit(JSON.stringify({ version: 1, schematicRatio: 59.6 }))).toBe(60);
  });

  it("falls back to split for an unknown or absent view mode", () => {
    expect(readWorkspaceViewMode(null)).toBe("split");
    expect(readWorkspaceViewMode("")).toBe("split");
    expect(readWorkspaceViewMode("cinema-view")).toBe("split");
    expect(readWorkspaceViewMode("schematic")).toBe("schematic");
    expect(readWorkspaceViewMode("cinema")).toBe("cinema");
  });

  it("hides a pane on demand and remembers the choice", async () => {
    renderWorkspace();
    const workspace = screen.getByTestId("dual-view-workspace");
    expect(workspace).toHaveAttribute("data-view-mode", "split");

    expect(screen.getByRole("group", { name: "Workspace view" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Schematic" }));
    expect(workspace).toHaveAttribute("data-view-mode", "schematic");
    expect(screen.queryByTestId("workspace-divider")).toBeInTheDocument();

    await waitFor(() => {
      expect(window.localStorage.getItem(WORKSPACE_VIEW_MODE_STORAGE_KEY)).toBe("schematic");
    });

    fireEvent.click(screen.getByRole("button", { name: "3D view" }));
    expect(workspace).toHaveAttribute("data-view-mode", "cinema");
    await waitFor(() => {
      expect(window.localStorage.getItem(WORKSPACE_VIEW_MODE_STORAGE_KEY)).toBe("cinema");
    });
  });
});
