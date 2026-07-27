import { type CSSProperties, type ReactNode, useEffect, useState } from "react";

export const DUAL_VIEW_WORKSPACE_STORAGE_KEY = "flowlab.workspace.dual-view.v1";
const DEFAULT_SPLIT = 50;
const MIN_SPLIT = 40;
const MAX_SPLIT = 60;

type StoredWorkspacePreference = {
  version: 1;
  schematicRatio: number;
};

export function clampWorkspaceSplit(value: number) {
  return Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, Math.round(value)));
}

export function readWorkspaceSplit(raw: string | null) {
  if (!raw) return DEFAULT_SPLIT;
  try {
    const value = JSON.parse(raw) as Partial<StoredWorkspacePreference>;
    return value.version === 1 && typeof value.schematicRatio === "number" && Number.isFinite(value.schematicRatio)
      ? clampWorkspaceSplit(value.schematicRatio)
      : DEFAULT_SPLIT;
  } catch {
    return DEFAULT_SPLIT;
  }
}

type Props = {
  header: ReactNode;
  schematic: ReactNode;
  cinema: ReactNode;
};

export function DualViewWorkspace({ header, schematic, cinema }: Props) {
  const [split, setSplit] = useState(DEFAULT_SPLIT);
  const [ready, setReady] = useState(false);
  const [narrowView, setNarrowView] = useState<"schematic" | "cinema">("schematic");

  useEffect(() => {
    setSplit(readWorkspaceSplit(window.localStorage.getItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY)));
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const preference: StoredWorkspacePreference = { version: 1, schematicRatio: split };
    window.localStorage.setItem(DUAL_VIEW_WORKSPACE_STORAGE_KEY, JSON.stringify(preference));
  }, [ready, split]);

  function adjust(delta: number) {
    setSplit((current) => clampWorkspaceSplit(current + delta));
  }

  function pointerRatio(event: React.PointerEvent<HTMLButtonElement>) {
    const workspace = event.currentTarget.closest<HTMLElement>("[data-testid='dual-view-workspace']");
    if (!workspace) return;
    const rect = workspace.getBoundingClientRect();
    if (rect.width <= 0) return;
    setSplit(clampWorkspaceSplit(((event.clientX - rect.left) / rect.width) * 100));
  }

  return (
    <section
      className="canvas-region dual-view-workspace"
      data-testid="dual-view-workspace"
      data-narrow-view={narrowView}
      style={{ "--schematic-ratio": `${split}%` } as CSSProperties}
    >
      <header className="dual-workspace-header">{header}</header>
      <div className="narrow-view-switcher" aria-label="Workspace view">
        <button type="button" className={narrowView === "schematic" ? "active" : ""} aria-pressed={narrowView === "schematic"} onClick={() => setNarrowView("schematic")}>
          Schematic
        </button>
        <button type="button" className={narrowView === "cinema" ? "active" : ""} aria-pressed={narrowView === "cinema"} onClick={() => setNarrowView("cinema")}>
          3D view
        </button>
      </div>
      <div className="dual-view-panes">
        <section className="workspace-view schematic-view" data-testid="schematic-pane" aria-label="Linked schematic view">
          {schematic}
        </section>
        <button
          type="button"
          className="workspace-divider"
          data-testid="workspace-divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize schematic and 3D views"
          aria-valuemin={MIN_SPLIT}
          aria-valuemax={MAX_SPLIT}
          aria-valuenow={split}
          aria-valuetext={`Schematic ${split} percent, 3D view ${100 - split} percent`}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            pointerRatio(event);
          }}
          onPointerMove={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) pointerRatio(event);
          }}
          onPointerUp={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              adjust(-5);
            } else if (event.key === "ArrowRight") {
              event.preventDefault();
              adjust(5);
            } else if (event.key === "Home") {
              event.preventDefault();
              setSplit(MIN_SPLIT);
            } else if (event.key === "End") {
              event.preventDefault();
              setSplit(MAX_SPLIT);
            }
          }}
        >
          <span />
        </button>
        <section className="workspace-view cinema-view" data-testid="cinema-pane" aria-label="Linked 3D and result view">
          {cinema}
        </section>
      </div>
    </section>
  );
}
