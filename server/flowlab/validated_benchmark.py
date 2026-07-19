"""Pinned immutable registry for FlowLab's bounded validated benchmarks.

Each entry is restricted to its stated evidence envelope and never becomes a
generic production claim.  General generated cases remain experimental.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schemas import EvidenceCapability

SCHEMA = "flowlab.validated_benchmark_registry.v1"
BENCHMARK_ID = "periodic-all-hex-straight-pipe-v1"
OPEN_BOUNDARY_BENCHMARK_ID = "laminar-open-boundary-all-hex-v1"
_ROOT = Path(__file__).resolve().parents[2]
_RUN = _ROOT / "benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-diagnostics-v2"
_EVIDENCE = (
    "artifacts/candidate-report.json",
    "artifacts/evidence-package.json",
    "serial/fine/runtime/periodic-diagnostics.json",
)
_OPEN_BOUNDARY_EVIDENCE = (
    (
        "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v29-affine-grid-invariance/artifacts/affine-grid-invariance.json",
        "flowlab.open-boundary-affine-grid-invariance.v1",
    ),
    (
        "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v36-non-affine-mms/artifacts/non-affine-mms-report.json",
        "flowlab.open-boundary-non-affine-mms.v1",
    ),
    (
        "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v40-laminar-force-benchmark/artifacts/laminar-force-benchmark.json",
        "flowlab.open-boundary-laminar-force-benchmark.v1",
    ),
)
_ALL_HEX_CAMPAIGN_POINTER = (
    _ROOT
    / "benchmarks/cases/open-boundary/campaigns/"
    "validated-campaign-pointer.json"
)
_ALL_HEX_REQUIRED_GATES = (
    "everyScientificCellAccountedFor",
    "noInfrastructureGaps",
    "affineAccepted",
    "nonAffineMmsAccepted",
    "physicalEnvelopeAccepted",
    "experimentalDatasetPinned",
    "reproducibilityAccepted",
    "negativeControlsAccepted",
    "productContractAccepted",
)
_ALL_HEX_ACCEPTED_CAMPAIGN_IDS = {
    "laminar-all-hex-v2",
    "laminar-all-hex-v3",
    "laminar-all-hex-v4",
}
EXPERIMENTAL_REASONS = [
    "This CFD output is outside FlowLab's bounded validated benchmark envelopes.",
    "CAD, hybrid, transient, turbulent, and materially different open-boundary formulations require their own accepted validation evidence.",
]
EXPERIMENTAL_PATH = [
    "Run the applicable immutable mesh and checkMesh gates.",
    "Pass exact initialization, analytical or manufactured-solution QoIs, and three-grid GCI for this formulation.",
    "For any performance claim, repeat timings on native AMD64 hardware.",
]
PROHIBITED_CLAIMS = ["validated", "production-ready", "production CFD", "validated for production use"]


def experimental_capability() -> EvidenceCapability:
    return EvidenceCapability(
        status="experimental",
        promotionBlocked=True,
        blockingReasons=EXPERIMENTAL_REASONS,
        validationPath=EXPERIMENTAL_PATH,
        allowedClaims=["Experimental CFD output — not validated for production use"],
        prohibitedClaims=PROHIBITED_CLAIMS,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_reference(relative: str) -> dict[str, str]:
    path = (_RUN / relative).resolve()
    if not path.is_relative_to(_RUN.resolve()) or not path.is_file():
        raise ValueError(f"Pinned benchmark evidence is unavailable: {relative}")
    return {"path": str(Path("benchmarks") / path.relative_to(_ROOT / "benchmarks")), "sha256": _sha256(path)}


def _accepted_report(relative: str, schema: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = (_ROOT / relative).resolve()
    if not path.is_relative_to(_ROOT.resolve()) or not path.is_file():
        raise ValueError(f"Pinned open-boundary evidence is unavailable: {relative}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != schema or report.get("status") != "accepted":
        raise ValueError(f"Pinned open-boundary evidence is not accepted: {relative}")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError(f"Pinned open-boundary evidence has failed checks: {relative}")
    return report, {"path": str(path.relative_to(_ROOT)), "sha256": _sha256(path)}


def all_hex_campaign_promotion_decision(report: dict[str, Any]) -> dict[str, Any]:
    """Apply the complete desktop promotion contract to one campaign report."""
    checks = report.get("checks")
    valid_identity = (
        report.get("schema")
        in {
            "flowlab.laminar-all-hex-campaign-report.v1",
            "flowlab.laminar-all-hex-final-assessment.v1",
            "flowlab.laminar-all-hex-final-assessment.v2",
        }
        and report.get("campaignId") in _ALL_HEX_ACCEPTED_CAMPAIGN_IDS
        and isinstance(checks, dict)
    )
    passed = (
        valid_identity
        and report.get("promotionAuthorized") is True
        and all(checks.get(name) is True for name in _ALL_HEX_REQUIRED_GATES)
    )
    if not valid_identity:
        reasons = ["All-hex campaign report identity or check structure is invalid."]
    else:
        reasons = [
            f"Campaign gate `{name}` has not passed."
            for name in _ALL_HEX_REQUIRED_GATES
            if checks.get(name) is not True
        ]
        if not reasons and report.get("promotionAuthorized") is not True:
            reasons = ["All-hex campaign has not authorized desktop promotion."]
    return {
        "status": "accepted" if passed else "blocked",
        "campaignStatus": report.get("status"),
        "promotionAuthorized": passed,
        "checks": checks if isinstance(checks, dict) else {},
        "blockingReasons": reasons,
    }


def _all_hex_campaign_gate() -> tuple[dict[str, Any], dict[str, str] | None]:
    """Read the promotion gate without ever falling back to older evidence."""
    try:
        pointer = json.loads(
            _ALL_HEX_CAMPAIGN_POINTER.read_text(encoding="utf-8")
        )
        if pointer.get("schema") != "flowlab.validated-campaign-pointer.v1":
            raise ValueError("validated campaign pointer schema is invalid")
        report_path = (_ROOT / str(pointer["report"])).resolve()
        if not report_path.is_relative_to(_ROOT.resolve()) or not report_path.is_file():
            raise ValueError("validated campaign pointer escapes the workspace or is missing")
        if pointer.get("sha256") != _sha256(report_path):
            raise ValueError("validated campaign report digest does not match its pointer")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "unavailable",
            "promotionAuthorized": False,
            "checks": {},
            "blockingReasons": [f"All-hex campaign report is unavailable: {exc}"],
        }, None
    gate = all_hex_campaign_promotion_decision(report)
    reference = {
        "path": str(report_path.relative_to(_ROOT)),
        "sha256": _sha256(report_path),
    }
    return gate, reference


def validated_benchmark_registry() -> dict[str, Any]:
    references = [_evidence_reference(relative) for relative in _EVIDENCE]
    candidate = json.loads((_RUN / _EVIDENCE[0]).read_text(encoding="utf-8"))
    accepted = [
        _accepted_report(relative, schema)
        for relative, schema in _OPEN_BOUNDARY_EVIDENCE
    ]
    affine, mms, physical = [item[0] for item in accepted]
    open_references = [item[1] for item in accepted]
    if physical.get("validated") is not True or physical.get("nextStage", {}).get(
        "desktopValidatedRegimePromotion"
    ) != "authorized":
        raise ValueError("Pinned physical evidence does not authorize desktop promotion")
    campaign_gate, campaign_reference = _all_hex_campaign_gate()
    open_promotion_authorized = campaign_gate["promotionAuthorized"] is True
    if campaign_reference is not None:
        open_references.append(campaign_reference)
    return {
        "schema": SCHEMA,
        "benchmarks": [
            {
                "id": BENCHMARK_ID,
                "label": "Validated benchmark — periodic all-hex straight pipe",
                "scientificStatus": "analysis-only-narrow-envelope",
                "capabilityStatus": "validated-benchmark",
                "promotionBlocked": True,
                "applicability": [
                    "Steady incompressible laminar Poiseuille flow in the retained periodic all-hex O-grid straight-pipe case.",
                    "Exact initialization and the pinned numerical formulation only.",
                ],
                "limits": [
                    "Not validated for open boundaries, CAD geometry, frozen surfaces, hybrid/prism-tet interfaces, transients, turbulence, or multiphase flow.",
                    "Docker evidence was produced in an AMD64 image under emulation and is excluded from native-performance claims.",
                    "This benchmark is analysis-only; it does not make FlowLab production-ready.",
                ],
                "metrics": candidate.get("serialGates", {}),
                "evidence": references,
            },
            {
                "id": OPEN_BOUNDARY_BENCHMARK_ID,
                "label": "Candidate bounded regime — laminar open-boundary all-hex",
                "scientificStatus": (
                    "validated-bounded-regime"
                    if open_promotion_authorized
                    else "campaign-promotion-blocked"
                ),
                "capabilityStatus": (
                    "validated-benchmark" if open_promotion_authorized else "experimental"
                ),
                "promotionBlocked": not open_promotion_authorized,
                "blockingReasons": campaign_gate["blockingReasons"],
                "applicability": [
                    "Steady incompressible laminar flow on the structured Cartesian all-hex 12/24/48 refinement family with exact fixed inlet/outlet kinematic pressure traces.",
                    "OpenFOAM 11 incompressibleFluid with pressureInletOutletVelocity on inlet/outlet, fixed analytic tangential velocity, PBiCGStab/DILU, and the pinned schemes and tolerances.",
                    "The independent physical envelope is plane Poiseuille flow at height-based Reynolds number 16.67 with the pinned analytic and face-integration force definitions.",
                ],
                "limits": [
                    "Validated only for this bounded laminar, structured all-hex pressure/velocity contract; it is not a general open-boundary, geometry, or production-CFD claim.",
                    "Not validated for CAD or curved geometry, hybrid/prism-tet meshes, transients, turbulence, multiphase flow, compressibility, or materially different boundary conditions.",
                    "Container evidence under AMD64 emulation supports scientific correctness, not native performance claims.",
                ],
                "metrics": {
                    "affineGridChecks": affine["checks"],
                    "nonAffineConvergence": mms["convergence"],
                    "physicalFineGrid": physical["observations"][-1],
                    "allHexCampaignGate": campaign_gate,
                },
                "evidence": open_references,
            },
        ],
    }


def promotion_error(capability: EvidenceCapability, requested_claim: str) -> str | None:
    claim = requested_claim.strip().lower()
    if capability.status != "validated-benchmark":
        return "Experimental CFD outputs cannot be exported, labelled, or summarized as validated or production CFD."
    if claim in {"validated", "production-ready", "production cfd", "production"}:
        return "The validated benchmark is analysis-only and cannot be promoted to a production CFD claim."
    return None
