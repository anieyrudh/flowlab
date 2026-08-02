import type { WorkspaceMode } from "./types";

export type LoadedResultProvenance =
  | { kind: "imported" }
  | { kind: "fixture" }
  | { kind: "case-artifact"; caseId: string; jobId: string; artifactName: string };

export type PreviewSnapshotDescriptor = {
  preview?: boolean;
  provenance?: LoadedResultProvenance;
};

export type PreviewAuthorityKind =
  | "concept"
  | "generated-case"
  | "solver-mesh"
  | "thinned"
  | "imported"
  | "fixture"
  | "inspect-empty";

export type PreviewAuthority = {
  kind: PreviewAuthorityKind;
  label: string;
  description: string;
  surfaceOnly: boolean;
  probeOnly: boolean;
  flowPathsAllowed: boolean;
};

const conceptPreview: PreviewAuthority = {
  kind: "concept",
  label: "Concept preview",
  description: "Stylized physical interpretation; the schematic and instant estimate remain authoritative.",
  surfaceOnly: false,
  probeOnly: false,
  flowPathsAllowed: false
};

function resultAuthority(snapshot: PreviewSnapshotDescriptor): PreviewAuthority {
  if (snapshot.preview) {
    return {
      kind: "thinned",
      label: "Thinned artifact preview — surface only",
      description: "Bounded geometry and field samples for inspection; streamlines and pathlines stay unavailable.",
      surfaceOnly: true,
      probeOnly: false,
      flowPathsAllowed: false
    };
  }
  if (snapshot.provenance?.kind === "fixture") {
    return {
      kind: "fixture",
      label: "Fixture result — developer example · probe only",
      description: "Bundled example data; it is not solver evidence for this project.",
      surfaceOnly: false,
      probeOnly: true,
      flowPathsAllowed: false
    };
  }
  if (snapshot.provenance?.kind !== "case-artifact") {
    return {
      kind: "imported",
      label: "Imported result — probe only",
      description: "No generated-case linkage is verified; schematic selection and derived flow paths stay disabled.",
      surfaceOnly: false,
      probeOnly: true,
      flowPathsAllowed: false
    };
  }
  return {
    kind: "solver-mesh",
    label: "Solver-produced mesh",
    description: "Loaded solver artifact with fields, probes, and provenance linkage shown independently.",
    surfaceOnly: false,
    probeOnly: false,
    flowPathsAllowed: true
  };
}

export function selectPreviewAuthority({
  stage,
  snapshot,
  hasGeneratedCaseMesh
}: {
  stage: WorkspaceMode;
  snapshot: PreviewSnapshotDescriptor | null;
  hasGeneratedCaseMesh: boolean;
}): PreviewAuthority {
  if (stage === "design" || stage === "simulate") return conceptPreview;

  if (stage === "sweep") {
    if (snapshot?.provenance?.kind === "case-artifact") return resultAuthority(snapshot);
    if (hasGeneratedCaseMesh) {
      return {
        kind: "generated-case",
        label: "Generated-case mesh preview",
        description: "Deterministic pre-solve mesh from the current generated case; not solver or validation evidence.",
        surfaceOnly: false,
        probeOnly: false,
        flowPathsAllowed: false
      };
    }
    return conceptPreview;
  }

  if (snapshot) return resultAuthority(snapshot);
  return {
    kind: "inspect-empty",
    label: "No result loaded",
    description: "Import VTK/VTU or open a completed local job. Concept geometry is not result evidence.",
    surfaceOnly: false,
    probeOnly: true,
    flowPathsAllowed: false
  };
}

