import { describe, expect, it } from "vitest";
import { selectPreviewAuthority } from "./previewAuthority";

describe("preview authority", () => {
  it("keeps Define and Estimate on the concept surface", () => {
    for (const stage of ["design", "simulate"] as const) {
      expect(selectPreviewAuthority({
        stage,
        hasGeneratedCaseMesh: true,
        snapshot: { provenance: { kind: "case-artifact", caseId: "case", jobId: "job", artifactName: "result.vtk" } }
      }).label).toBe("Concept preview");
    }
  });

  it("promotes a generated mesh and then a full solver mesh in CFD", () => {
    expect(selectPreviewAuthority({ stage: "sweep", snapshot: null, hasGeneratedCaseMesh: true }).label)
      .toBe("Generated-case mesh preview");
    expect(selectPreviewAuthority({
      stage: "sweep",
      hasGeneratedCaseMesh: true,
      snapshot: { provenance: { kind: "case-artifact", caseId: "case", jobId: "job", artifactName: "result.vtk" } }
    }).label).toBe("Solver-produced mesh");
  });

  it("labels thinned, imported, and fixture results with fail-closed capabilities", () => {
    const thinned = selectPreviewAuthority({
      stage: "analyze",
      hasGeneratedCaseMesh: false,
      snapshot: {
        preview: true,
        provenance: { kind: "case-artifact", caseId: "case", jobId: "job", artifactName: "preview.vtk" }
      }
    });
    expect(thinned.label).toBe("Thinned artifact preview — surface only");
    expect(thinned.flowPathsAllowed).toBe(false);

    const imported = selectPreviewAuthority({
      stage: "analyze",
      hasGeneratedCaseMesh: false,
      snapshot: { provenance: { kind: "imported" } }
    });
    expect(imported.label).toBe("Imported result — probe only");
    expect(imported.probeOnly).toBe(true);

    const fixture = selectPreviewAuthority({
      stage: "analyze",
      hasGeneratedCaseMesh: false,
      snapshot: { provenance: { kind: "fixture" } }
    });
    expect(fixture.label).toBe("Fixture result — developer example · probe only");
    expect(fixture.probeOnly).toBe(true);
  });
});
