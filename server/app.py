from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse

from .flowlab.adapters import capabilities, generate_case
from .flowlab.execution import (
    JobManager,
    list_case_artifacts,
    read_case_artifact,
    read_case_artifact_chunk,
    read_case_artifact_preview,
    read_case_mesh_quality,
    runtime_diagnostics,
)
from .flowlab.reference_cases import build_reference_case_import_plan, list_reference_cases
from .flowlab.schemas import CaseRequest, JobRecord, SolverCase, SolverRuntimeStatus
from .flowlab.validated_benchmark import promotion_error, validated_benchmark_registry
from .flowlab.validated_preset import OPEN_BOUNDARY_BENCHMARK_ID, build_validated_open_boundary_case

app = FastAPI(title="FlowLab Solver Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CASES: dict[str, SolverCase] = {}
JOB_MANAGER = JobManager()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "flowlab-solver"}


@app.get("/api/solvers")
def solvers():
    return capabilities()


@app.get("/api/runtime", response_model=list[SolverRuntimeStatus])
def runtime():
    return runtime_diagnostics()


@app.get("/api/reference-cases")
def reference_cases():
    try:
        return list_reference_cases()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/benchmarks/validated")
def validated_benchmarks():
    try:
        return validated_benchmark_registry()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Validated benchmark evidence is unavailable: {exc}") from exc


@app.post("/api/benchmarks/validated/{benchmark_id}/jobs")
def run_validated_benchmark(benchmark_id: str):
    """Atomically mint and queue the sole immutable validated desktop preset."""
    if benchmark_id != OPEN_BOUNDARY_BENCHMARK_ID:
        raise HTTPException(status_code=404, detail="Runnable validated preset not found")
    try:
        case = build_validated_open_boundary_case()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=f"Validated preset is unavailable: {exc}") from exc
    CASES[case.id] = case
    job = JOB_MANAGER.queue_job(case)
    return {"case": case, "job": job}


@app.post("/api/reference-cases/{case_id}/import-plan")
def reference_case_import_plan(case_id: str):
    try:
        return build_reference_case_import_plan(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Reference case not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/cases/generate", response_model=SolverCase)
def create_case(request: CaseRequest):
    try:
        case = generate_case(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    CASES[case.id] = case
    return case


@app.post("/api/jobs", response_model=JobRecord)
def queue_job(case: SolverCase):
    stored_case = CASES.get(case.id)
    if not stored_case:
        raise HTTPException(status_code=400, detail="Generate the case through /api/cases/generate before queueing a job.")
    return JOB_MANAGER.queue_job(stored_case)


@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)):
    return {
        "jobs": [
            {"job": job, "case": case}
            for job, case in JOB_MANAGER.list_jobs(limit=limit)
        ]
    }


@app.get("/api/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: str):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/jobs/{job_id}/promote")
def promote_job(job_id: str, claim: str = Query(..., min_length=1)):
    """Fail closed on attempts to promote exploratory results into product claims."""
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    error = promotion_error(job.evidenceCapability, claim)
    if error:
        raise HTTPException(status_code=409, detail=error)
    return {"jobId": job.id, "status": "evidence-link-only", "claim": claim}


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"jobId": job.id, "status": job.status, "logs": job.logs}


@app.get("/api/jobs/{job_id}/artifact")
def get_job_artifact(job_id: str, path: str = Query(..., min_length=1)):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.caseDir:
        raise HTTPException(status_code=404, detail="Job case directory is unavailable")
    try:
        return read_case_artifact(Path(job.caseDir), path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/artifacts")
def get_job_artifacts(
    job_id: str,
    kind: str = Query("result", pattern="^(result|diagnostic|all)$"),
    limit: int = Query(200, ge=1, le=500),
):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.caseDir:
        raise HTTPException(status_code=404, detail="Job case directory is unavailable")
    try:
        return list_case_artifacts(Path(job.caseDir), kind=kind, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/mesh-quality")
def get_job_mesh_quality(job_id: str):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.caseDir:
        raise HTTPException(status_code=404, detail="Job case directory is unavailable")
    try:
        return read_case_mesh_quality(Path(job.caseDir))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/artifact/chunk")
def get_job_artifact_chunk(
    job_id: str,
    path: str = Query(..., min_length=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(262_144, ge=1, le=262_144),
):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.caseDir:
        raise HTTPException(status_code=404, detail="Job case directory is unavailable")
    try:
        return read_case_artifact_chunk(Path(job.caseDir), path, offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/artifact/preview")
def get_job_artifact_preview(
    job_id: str,
    path: str = Query(..., min_length=1),
    pointLimit: int = Query(500, ge=1, le=5_000),
    cellLimit: int = Query(500, ge=0, le=5_000),
):
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.caseDir:
        raise HTTPException(status_code=404, detail="Job case directory is unavailable")
    try:
        return read_case_artifact_preview(Path(job.caseDir), path, point_limit=pointLimit, cell_limit=cellLimit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/logs/stream")
def stream_job_logs(job_id: str):
    if not JOB_MANAGER.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(JOB_MANAGER.stream_log_lines(job_id), media_type="text/plain")


@app.post("/api/jobs/{job_id}/cancel", response_model=JobRecord)
def cancel_job(job_id: str):
    job = JOB_MANAGER.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/cases/{case_id}", response_model=SolverCase)
def get_case(case_id: str):
    case = CASES.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


# The native desktop shell points this at its packaged Vite build. API routes
# are registered first, so the same-origin desktop UI cannot shadow them.
_desktop_dist = Path(os.environ["FLOWLAB_DESKTOP_DIST"]).resolve() if os.environ.get("FLOWLAB_DESKTOP_DIST") else None
if _desktop_dist is not None and _desktop_dist.is_dir():
    app.mount("/", StaticFiles(directory=_desktop_dist, html=True), name="desktop-ui")
