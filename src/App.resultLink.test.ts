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

function cellSnapshot(
  artifactName = "postProcessing/flowlabNative/time_1.vtk",
  sourceCellIndices = [0, 1, 2],
  sourceCellCount = 3
): ResultSnapshot {
  return {
    id: "snapshot-cells",
    label: artifactName,
    time: 1,
    dataset: {
      format: "legacy-vtk-ascii-v1",
      points: [],
      cells: sourceCellIndices.map(() => []),
      cellTypes: sourceCellIndices.map(() => 9),
      pointData: { scalars: {}, vectors: {} },
      cellData: { scalars: {}, vectors: {} },
      fields: [],
      sourceCellIndices,
      sourceCellCount
    },
    provenance: { kind: "case-artifact", caseId: "case-verified", jobId: "job-verified", artifactName }
  };
}

function multiEdgeSolverCase(overrides: Partial<SolverCase> = {}): SolverCase {
  return solverCase({
    resultComponentMap: {
      version: 2,
      projectSha256,
      artifactBindings: [
        {
          artifactName: "postProcessing/flowlabNative/*.vtk",
          scope: "cell-ranges",
          sourceCellCount: 3,
          cellRanges: [
            { edgeId: "inlet", cellStart: 0, cellCount: 1 },
            { edgeId: "outlet", cellStart: 2, cellCount: 1 }
          ],
          unownedCellRanges: [
            {
              artifactId: "generated:y-junction:junction-core:v1",
              cellStart: 1,
              cellCount: 1,
              schematicOwner: null
            }
          ]
        }
      ]
    },
    ...overrides
  });
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

  it("resolves multi-edge selection only from explicit source-cell ranges", () => {
    expect(verifiedResultComponentLink(cellSnapshot(), project, multiEdgeSolverCase(), job)).toMatchObject({
      state: "linked",
      message: expect.stringMatching(/probe a result cell/i)
    });
    expect(verifiedResultComponentLink(cellSnapshot(), project, multiEdgeSolverCase(), job, 0)).toMatchObject({
      state: "linked",
      edgeId: "inlet"
    });
    expect(verifiedResultComponentLink(cellSnapshot(), project, multiEdgeSolverCase(), job, 2)).toMatchObject({
      state: "linked",
      edgeId: "outlet"
    });
    expect(verifiedResultComponentLink(cellSnapshot(), project, multiEdgeSolverCase(), job, 1)).toMatchObject({
      state: "unlinked",
      message: expect.stringMatching(/generated junction cell has no schematic edge owner/i)
    });
  });

  it("maps sampled preview cells through their retained source indices", () => {
    expect(verifiedResultComponentLink(cellSnapshot(undefined, [2], 3), project, multiEdgeSolverCase(), job, 0)).toMatchObject({
      state: "linked",
      edgeId: "outlet"
    });
  });

  it("allows presentation-only stage changes while keeping the queued model immutable", () => {
    const inspectProject = structuredClone(project);
    inspectProject.visualization.mode = "analyze";
    inspectProject.viewport = { x: 120, y: -40, zoom: 1.5 };

    expect(verifiedResultComponentLink(cellSnapshot(), inspectProject, multiEdgeSolverCase(), job, 2)).toMatchObject({
      state: "linked",
      edgeId: "outlet"
    });
  });

  it("fails closed for unmatched artifacts and source-cell counts", () => {
    expect(
      verifiedResultComponentLink(cellSnapshot("VTK/inlet/inlet_1.vtk"), project, multiEdgeSolverCase(), job, 0)
    ).toMatchObject({ state: "unlinked", message: expect.stringMatching(/No matching schematic component/i) });
    expect(verifiedResultComponentLink(cellSnapshot(undefined, [0], 4), project, multiEdgeSolverCase(), job, 0)).toMatchObject({
      state: "unlinked",
      message: expect.stringMatching(/does not match the generated case/i)
    });
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
