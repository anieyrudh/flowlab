import { describe, expect, it } from "vitest";
import { verifiedResultComponentLink, type ResultSnapshot } from "./App";
import { venturiPreset } from "./data/presets";
import type { JobRecord, SolverCase } from "./types";

const project = structuredClone(venturiPreset);
const edgeId = Object.keys(project.edges)[0];
const projectText = JSON.stringify(project);
const projectSha256 = "a".repeat(64);

function solverCase(overrides: Partial<SolverCase> = {}): SolverCase {
  return {
    id: "case-verified",
    projectName: project.name,
    solver: "openfoam",
    advancedMode: "incompressible-navier-stokes",
    status: "complete",
    files: {
      "flowlab_project.json": projectText,
      "flowlab_case_manifest.json": JSON.stringify({ files: { "flowlab_project.json": { sha256: projectSha256 } } })
    },
    runCommand: [],
    provenance: [],
    evidenceCapability: {} as SolverCase["evidenceCapability"],
    resultComponentMap: {
      version: 1,
      projectSha256,
      artifactBindings: [{ artifactName: "VTK/result.vtk", edgeId, scope: "all-cells" }]
    },
    ...overrides
  };
}

function snapshot(provenance: ResultSnapshot["provenance"]): ResultSnapshot {
  return { id: "snapshot-1", label: "VTK/result.vtk", time: 0, dataset: {} as ResultSnapshot["dataset"], provenance };
}

const job = { id: "job-verified", caseId: "case-verified" } as JobRecord;

describe("verified result component linkage", () => {
  it("links only a matching generated artifact with a matching snapshot hash", () => {
    const link = verifiedResultComponentLink(
      snapshot({ kind: "case-artifact", caseId: "case-verified", jobId: "job-verified", artifactName: "VTK/result.vtk" }),
      project,
      solverCase(),
      job
    );

    expect(link).toMatchObject({ state: "linked", edgeId });
  });

  it("leaves imports, stale projects, and mismatched map hashes as explicit probes", () => {
    expect(verifiedResultComponentLink(snapshot({ kind: "imported" }), project, solverCase(), job).message).toMatch(/Imported result/);

    const changedProject = structuredClone(project);
    changedProject.name = "Changed after queue";
    expect(verifiedResultComponentLink(
      snapshot({ kind: "case-artifact", caseId: "case-verified", jobId: "job-verified", artifactName: "VTK/result.vtk" }),
      changedProject,
      solverCase(),
      job
    ).message).toMatch(/different project snapshot/);

    expect(verifiedResultComponentLink(
      snapshot({ kind: "case-artifact", caseId: "case-verified", jobId: "job-verified", artifactName: "VTK/result.vtk" }),
      project,
      solverCase({ files: { "flowlab_project.json": projectText, "flowlab_case_manifest.json": JSON.stringify({ files: { "flowlab_project.json": { sha256: "b".repeat(64) } } }) } }),
      job
    ).message).toMatch(/snapshot hash/);
  });
});
