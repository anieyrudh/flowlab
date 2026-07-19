from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

SolverTier = Literal["instant-1d", "openfoam", "su2", "code-saturne", "mujoco"]
JobStatus = Literal["generated", "queued", "running", "complete", "failed", "blocked", "cancelled"]
AdvancedMode = Literal[
    "incompressible-navier-stokes",
    "compressible-flow",
    "heat-transfer",
    "conjugate-heat-transfer",
    "water-hammer",
    "multiphase-vof",
    "cavitation",
    "rigid-body-fluid-forces",
]


class SolverCapability(BaseModel):
    id: SolverTier
    label: str
    installed: bool
    execution: Literal["browser", "docker", "native"]
    notes: list[str] = Field(default_factory=list)


class SolverRuntimeStatus(BaseModel):
    solver: SolverTier
    runnable: bool
    preferredExecution: Literal["docker", "native", "browser", "none"]
    dockerImage: str | None = None
    dockerAvailable: bool | None = None
    nativeCommand: str | None = None
    nativeAvailable: bool | None = None
    pythonModule: str | None = None
    pythonModuleAvailable: bool | None = None
    blockers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CaseRequest(BaseModel):
    project: dict[str, Any]
    solver: SolverTier
    advancedMode: AdvancedMode


EvidenceStatus = Literal["validated-benchmark", "experimental"]


class EvidenceCapability(BaseModel):
    """Scientific-claim boundary carried by every generated case and job."""

    status: EvidenceStatus
    promotionBlocked: bool
    blockingReasons: list[str] = Field(default_factory=list)
    validationPath: list[str] = Field(default_factory=list)
    allowedClaims: list[str] = Field(default_factory=list)
    prohibitedClaims: list[str] = Field(default_factory=list)
    evidenceId: str | None = None
    immutableEvidence: list[dict[str, str]] = Field(default_factory=list)


def default_experimental_capability() -> EvidenceCapability:
    """Safe default for internal constructors and backwards-compatible jobs."""
    return EvidenceCapability(
        status="experimental",
        promotionBlocked=True,
        blockingReasons=["No formulation-specific accepted validation evidence is attached to this output."],
        validationPath=["Pass the applicable mesh, analytical or MMS, and three-grid GCI gates before any promotion claim."],
        allowedClaims=["Experimental CFD output — not validated for production use"],
        prohibitedClaims=["validated", "production-ready", "production CFD", "validated for production use"],
    )


class SolverCase(BaseModel):
    id: str = Field(default_factory=lambda: f"case-{uuid4().hex[:10]}")
    projectName: str
    solver: SolverTier
    advancedMode: AdvancedMode
    status: JobStatus = "generated"
    files: dict[str, str] = Field(default_factory=dict)
    runCommand: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    evidenceCapability: EvidenceCapability = Field(default_factory=default_experimental_capability)


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"job-{uuid4().hex[:10]}")
    caseId: str
    solver: SolverTier
    status: JobStatus
    createdAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    updatedAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    finishedAt: str | None = None
    caseDir: str | None = None
    execution: Literal["docker", "native", "browser", "none"] = "none"
    command: list[str] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    exitCode: int | None = None
    result: dict[str, Any] | None = None
    evidenceCapability: EvidenceCapability = Field(default_factory=default_experimental_capability)
