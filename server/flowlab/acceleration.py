"""Safety contract for performance features and future learned CFD accelerators.

This module intentionally does not perform neural inference. It decides only
whether an input is eligible for a bounded estimate and makes authoritative CFD
the mandatory fallback for any missing evidence or out-of-envelope input.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


ACCELERATION_POLICY_SCHEMA = "flowlab.acceleration-policy.v1"
PARALLEL_PLAN_SCHEMA = "flowlab.openfoam-parallel-plan.v1"
_REQUIRED_PROVENANCE_FIELDS = (
    "solver",
    "solverVersion",
    "containerImageDigest",
    "meshRecipeHash",
    "datasetManifestHash",
)
_REQUIRED_PHYSICS_FIELDS = (
    "flowState",
    "phase",
    "fluidModel",
    "regime",
)
# FlowLab deliberately ships with no registered learned-model release. A release
# must be added through a separately reviewed, immutable artifact registry; a
# caller-supplied policy object is not a trusted release record.
_ISSUED_SURROGATE_RELEASES: dict[str, Mapping[str, Any]] = {}


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def build_openfoam_parallel_plan(*, ranks: int, decomposition: str) -> dict[str, Any]:
    """Return a benchmark-required parallel execution plan with no speedup claim."""

    if not isinstance(ranks, int) or isinstance(ranks, bool) or ranks < 2:
        raise ValueError("ranks must be an integer of at least 2")
    if decomposition != "scotch":
        raise ValueError("unsupported OpenFOAM decomposition method")
    return {
        "schema": PARALLEL_PLAN_SCHEMA,
        "execution": "parallel-candidate",
        "ranks": ranks,
        "decomposition": decomposition,
        "scientificStatus": "does-not-change-physics-evidence",
        "performanceStatus": "benchmark-required",
        "speedupClaim": None,
        "requiredMeasurements": [
            "wallClockSeconds",
            "cpuSeconds",
            "peakResidentMemoryBytes",
            "cellsPerRank",
            "iterationCount",
            "solverVersionAndContainerDigest",
        ],
        "guardrails": [
            "compare the same mesh, numerics, and stopping criteria against serial baseline",
            "retain mesh-quality, residual, conservation, and QoI checks",
            "do not infer a faster or scientifically equivalent result before benchmark review",
        ],
    }


def build_bounded_surrogate_policy(
    *,
    geometry_families: Sequence[str],
    qois: Sequence[str],
    reynolds_range: Sequence[float],
    validated_benchmark_ids: Sequence[str],
    training_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a policy for a future screening/warm-start model, not a CFD solver."""

    if len(reynolds_range) != 2 or not all(_is_finite_number(value) for value in reynolds_range):
        raise ValueError("reynolds_range must contain two finite values")
    reynolds_min, reynolds_max = (float(value) for value in reynolds_range)
    if reynolds_min < 0.0 or reynolds_max <= reynolds_min:
        raise ValueError("reynolds_range must be ordered, non-negative, and non-empty")
    missing_provenance = [
        field for field in _REQUIRED_PROVENANCE_FIELDS if not training_provenance.get(field)
    ]
    if missing_provenance:
        raise ValueError(f"training_provenance missing: {', '.join(missing_provenance)}")
    if not geometry_families or not qois or not validated_benchmark_ids:
        raise ValueError("geometry_families, qois, and validated_benchmark_ids are required")
    return {
        "schema": ACCELERATION_POLICY_SCHEMA,
        "mode": "bounded-surrogate-screening-or-warm-start",
        "releaseId": None,
        "releaseStatus": "candidate-not-released",
        "authoritativeSolver": "OpenFOAM",
        "envelope": {
            "geometryFamilies": list(geometry_families),
            "qois": list(qois),
            "reynoldsRange": [reynolds_min, reynolds_max],
            "physics": {
                "flowState": "steady",
                "phase": "single",
                "fluidModel": "incompressible-newtonian",
                "regime": "laminar",
            },
        },
        "validatedBenchmarkIds": list(validated_benchmark_ids),
        "trainingProvenance": dict(training_provenance),
        "outputContract": {
            "allowedLabels": ["surrogate-estimate", "warm-start-candidate"],
            "forbiddenLabels": ["CFD-converged", "validated-CFD", "experimental-validation"],
            "requiresAuthoritativeSpotCheck": True,
        },
    }


def _reynolds_number_is_in_envelope(value: Any, reynolds_range: Any) -> bool:
    if not _is_finite_number(value) or not isinstance(reynolds_range, Sequence):
        return False
    if isinstance(reynolds_range, (str, bytes)) or len(reynolds_range) != 2:
        return False
    try:
        lower, upper = (float(item) for item in reynolds_range)
    except (TypeError, ValueError):
        return False
    return math.isfinite(lower) and math.isfinite(upper) and lower <= float(value) <= upper


def evaluate_surrogate_eligibility(
    request: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Return an explicit CFD fallback unless every bounded-policy gate is met."""

    violations: list[str] = []
    if policy.get("schema") != ACCELERATION_POLICY_SCHEMA:
        violations.append("unrecognized-policy-schema")
    envelope = policy.get("envelope")
    if not isinstance(envelope, Mapping):
        violations.append("missing-envelope")
        envelope = {}
    physics = envelope.get("physics") if isinstance(envelope, Mapping) else {}
    request_physics = request.get("physics")
    if not isinstance(request_physics, Mapping):
        violations.append("missing-request-physics")
        request_physics = {}
    if isinstance(physics, Mapping):
        for field in _REQUIRED_PHYSICS_FIELDS:
            if request_physics.get(field) != physics.get(field):
                violations.append(f"physics-{field}-outside-envelope")
    geometry_family = request.get("geometryFamily")
    permitted_geometry_families = envelope.get("geometryFamilies", [])
    if not isinstance(permitted_geometry_families, Sequence) or isinstance(
        permitted_geometry_families, (str, bytes)
    ):
        permitted_geometry_families = []
    if geometry_family not in permitted_geometry_families:
        violations.append("geometry-outside-envelope")
    requested_qois = request.get("qois")
    permitted_qois = envelope.get("qois", [])
    if not isinstance(permitted_qois, Sequence) or isinstance(permitted_qois, (str, bytes)):
        permitted_qois = []
    if (
        not isinstance(requested_qois, Sequence)
        or isinstance(requested_qois, (str, bytes))
        or not requested_qois
        or not all(isinstance(qoi, str) for qoi in requested_qois)
    ):
        violations.append("missing-request-qois")
    else:
        unavailable_qois = sorted(set(requested_qois) - set(permitted_qois))
        if unavailable_qois:
            violations.append("qoi-outside-envelope:" + ",".join(map(str, unavailable_qois)))
    reynolds_number = request.get("reynoldsNumber")
    reynolds_range = envelope.get("reynoldsRange", [])
    if not _reynolds_number_is_in_envelope(reynolds_number, reynolds_range):
        violations.append("reynolds-number-outside-envelope")
    validated_benchmark_ids = policy.get("validatedBenchmarkIds")
    if not isinstance(validated_benchmark_ids, Sequence) or isinstance(
        validated_benchmark_ids, (str, bytes)
    ) or not validated_benchmark_ids or not all(isinstance(item, str) for item in validated_benchmark_ids):
        violations.append("no-validated-benchmark-provenance")
    else:
        from .benchmark_fixtures import trusted_validated_benchmark_ids

        trusted_benchmark_ids = trusted_validated_benchmark_ids()
        if not set(validated_benchmark_ids).issubset(trusted_benchmark_ids):
            violations.append("untrusted-validated-benchmark-provenance")
    training_provenance = policy.get("trainingProvenance")
    if not isinstance(training_provenance, Mapping) or any(
        not training_provenance.get(field) for field in _REQUIRED_PROVENANCE_FIELDS
    ):
        violations.append("incomplete-training-provenance")
    release_id = policy.get("releaseId")
    release = _ISSUED_SURROGATE_RELEASES.get(release_id) if isinstance(release_id, str) else None
    if not isinstance(release, Mapping) or release.get("status") != "released-and-verified":
        violations.append("no-registered-surrogate-release")

    if violations:
        return {
            "eligible": False,
            "status": "authoritative-cfd-required",
            "execution": "openfoam-authoritative-cfd",
            "violations": violations,
            "surrogateResultPermitted": False,
        }
    return {
        "eligible": True,
        "status": "eligible-for-bounded-estimate",
        "execution": "surrogate-estimate-or-warm-start-candidate",
        "surrogateResultPermitted": True,
        "scientificStatus": "not-cfd-converged",
        "requiresAuthoritativeSpotCheck": True,
        "forbiddenLabels": ["CFD-converged", "validated-CFD"],
    }
