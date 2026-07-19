import type {
  AdvancedPhysicsMode,
  FluidProject,
  JobArtifactChunk,
  JobArtifactFile,
  JobArtifactIndex,
  JobArtifactPreview,
  JobRecord,
  RecentJobsResponse,
  MeshQualitySummary,
  ReferenceCaseImportPlan,
  ReferenceCaseRegistry,
  ValidatedBenchmarkRegistry,
  ValidatedPresetLaunch,
  SolverCapability,
  SolverCase,
  SolverRuntimeStatus,
  SolverTier
} from "../types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed: ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      message = parsed.detail || message;
    } catch {
      // Keep the raw response body when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function fetchHealth(): Promise<boolean> {
  try {
    await request<{ status: string }>("/api/health");
    return true;
  } catch {
    return false;
  }
}

export function fetchSolvers(): Promise<SolverCapability[]> {
  return request<SolverCapability[]>("/api/solvers");
}

export function fetchRuntimeDiagnostics(): Promise<SolverRuntimeStatus[]> {
  return request<SolverRuntimeStatus[]>("/api/runtime");
}

export function fetchReferenceCases(): Promise<ReferenceCaseRegistry> {
  return request<ReferenceCaseRegistry>("/api/reference-cases");
}

export function fetchValidatedBenchmarks(): Promise<ValidatedBenchmarkRegistry> {
  return request<ValidatedBenchmarkRegistry>("/api/benchmarks/validated");
}

export function runValidatedPreset(benchmarkId: string): Promise<ValidatedPresetLaunch> {
  return request<ValidatedPresetLaunch>(`/api/benchmarks/validated/${encodeURIComponent(benchmarkId)}/jobs`, { method: "POST" });
}

export function fetchReferenceCaseImportPlan(caseId: string): Promise<ReferenceCaseImportPlan> {
  return request<ReferenceCaseImportPlan>(`/api/reference-cases/${encodeURIComponent(caseId)}/import-plan`, { method: "POST" });
}

export function generateSolverCase(
  project: FluidProject,
  solver: SolverTier,
  advancedMode: AdvancedPhysicsMode
): Promise<SolverCase> {
  return request<SolverCase>("/api/cases/generate", {
    method: "POST",
    body: JSON.stringify({ project, solver, advancedMode })
  });
}

export function queueJob(solverCase: SolverCase): Promise<JobRecord> {
  return request<JobRecord>("/api/jobs", {
    method: "POST",
    body: JSON.stringify(solverCase)
  });
}

export function fetchJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(`/api/jobs/${jobId}`);
}

export function fetchRecentJobs(limit = 20): Promise<RecentJobsResponse> {
  return request<RecentJobsResponse>(`/api/jobs?limit=${encodeURIComponent(String(limit))}`);
}

export function fetchJobArtifact(jobId: string, path: string): Promise<JobArtifactFile> {
  return request<JobArtifactFile>(`/api/jobs/${jobId}/artifact?path=${encodeURIComponent(path)}`);
}

export function fetchJobArtifacts(jobId: string, kind: "result" | "diagnostic" | "all" = "result", limit = 200): Promise<JobArtifactIndex> {
  const params = new URLSearchParams({ kind, limit: String(limit) });
  return request<JobArtifactIndex>(`/api/jobs/${jobId}/artifacts?${params.toString()}`);
}

export function fetchJobMeshQuality(jobId: string): Promise<MeshQualitySummary> {
  return request<MeshQualitySummary>(`/api/jobs/${jobId}/mesh-quality`);
}

export function fetchJobArtifactChunk(jobId: string, path: string, offset = 0, limit = 262144): Promise<JobArtifactChunk> {
  const params = new URLSearchParams({ path, offset: String(offset), limit: String(limit) });
  return request<JobArtifactChunk>(`/api/jobs/${jobId}/artifact/chunk?${params.toString()}`);
}

export function fetchJobArtifactPreview(jobId: string, path: string, pointLimit = 500, cellLimit = 500): Promise<JobArtifactPreview> {
  const params = new URLSearchParams({ path, pointLimit: String(pointLimit), cellLimit: String(cellLimit) });
  return request<JobArtifactPreview>(`/api/jobs/${jobId}/artifact/preview?${params.toString()}`);
}

export function cancelJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}
