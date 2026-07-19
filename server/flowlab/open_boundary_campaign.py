"""Immutable acceptance logic for the open-boundary MMS and pipe campaign.

This module intentionally owns no mesh-quality or performance policy.  It
evaluates retained observations from the fixed structured all-hex formulation,
then emits a reproducible report which either unlocks the frozen-v2 campaign or
names the first defensible fault class.  The caller must retain raw fields and
solver logs referenced by each observation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Literal, Sequence


SCHEMA = "flowlab.open-boundary-campaign.v1"
MMS_SCHEMA = "flowlab.open-boundary-mms-stage.v1"
PIPE_SCHEMA = "flowlab.open-boundary-pipe-stage.v1"
GCI_LIMIT = 0.01
ORDER_LIMIT = 1.5
MASS_LIMIT = 1.0e-6
LINEAR_RESIDUAL_LIMIT = 1.0e-8
PIPE_PRESSURE_LIMIT = 0.01


@dataclass(frozen=True)
class MmsDefinition:
    """A divergence-free, forced Stokes solution on 0<=x,y,z<=1.

    ``U=(1, 0, 0)`` is divergence free.  With kinematic pressure
    ``p=G(1-x)``, the matching body acceleration is ``S=(-G, 0, 0)`` under
    ``U.grad(U)-nu laplacian(U)+grad(p)=S``.  Exact U and p values are applied
    on the open x boundaries; all analytic fields and source terms are retained
    in the campaign manifest rather than inferred from a solver log.
    """

    viscosity_m2_s: float = 1.0e-6
    pressure_gradient_m2_s2_per_m: float = 1.0e-3
    domain: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def velocity(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        del x, y, z
        return (1.0, 0.0, 0.0)

    def pressure(self, x: float, y: float, z: float) -> float:
        del y, z
        return self.pressure_gradient_m2_s2_per_m * (1.0 - x)

    def momentum_source(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        del x, y, z
        return (-self.pressure_gradient_m2_s2_per_m, 0.0, 0.0)

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "flowlab.forced-divergence-free-mms.v1",
            "strongForm": "U.grad(U) - nu*laplacian(U) + grad(p) = S",
            "velocity": "(1, 0, 0)",
            "kinematicPressure": "G*(1-x)",
            "momentumSource": "(-G, 0, 0)",
            "divergence": "0",
            "boundaryTreatment": {
                "inlet": "analytic velocity and kinematic pressure",
                "outlet": "analytic velocity and kinematic pressure",
                "sidePlanes": "symmetry planes; the analytic normal gradients vanish",
            },
            "parameters": asdict(self),
        }


@dataclass(frozen=True)
class RefinementObservation:
    level: str
    effective_spacing: float
    check_mesh_passed: bool
    velocity_l2_error: float
    pressure_l2_error: float
    axial_velocity_l2_error: float
    transverse_velocity_l2_error: float
    mass_relative_imbalance: float
    final_linear_residual: float
    boundary_traction_relative_imbalance: float
    artifacts: dict[str, str]


@dataclass(frozen=True)
class PipeObservation(RefinementObservation):
    static_pressure_drop_relative_error: float = math.inf
    wall_force_relative_imbalance: float = math.inf
    plateau_detected: bool = False


def _observed_order(coarse: float, medium: float, fine: float, ratio: float) -> float | None:
    if min(coarse, medium, fine) <= 0.0 or ratio <= 1.0:
        return None
    numerator = coarse - medium
    denominator = medium - fine
    if numerator <= 0.0 or denominator <= 0.0:
        return None
    return math.log(numerator / denominator) / math.log(ratio)


def _fine_gci(medium: float, fine: float, ratio: float, order: float | None) -> float | None:
    if order is None or fine <= 0.0 or ratio <= 1.0:
        return None
    # The retained norms are relative to their analytic field norm.  In that
    # form the fine-grid Richardson error estimate is
    # |e_m-e_f|/(r^p-1), and the GCI is that estimate times 1.25.  Dividing by
    # the fine *error* again would turn a legitimate second-order sequence
    # into a scale-free O(1) value and make the requested 1% gate impossible.
    return 1.25 * abs(medium - fine) / (ratio**order - 1.0)


def _levels_are_fixed_ratio(levels: Sequence[RefinementObservation], ratio: float) -> bool:
    if len(levels) != 3 or ratio <= 1.0:
        return False
    spacing = [level.effective_spacing for level in levels]
    return all(value > 0.0 for value in spacing) and all(math.isclose(spacing[index] / spacing[index + 1], ratio, rel_tol=1e-9, abs_tol=1e-12) for index in range(2))


def _evaluate_common(levels: Sequence[RefinementObservation], *, ratio: float = 2.0) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    if not _levels_are_fixed_ratio(levels, ratio):
        reasons.append("three serial refinements with fixed ratio 2 are required")
    if any(not level.check_mesh_passed for level in levels):
        reasons.append("at least one refinement failed checkMesh")
    if any(level.mass_relative_imbalance > MASS_LIMIT for level in levels):
        reasons.append(f"mass relative imbalance exceeds {MASS_LIMIT:g}")
    if any(level.final_linear_residual > LINEAR_RESIDUAL_LIMIT for level in levels):
        reasons.append(f"final linear residual exceeds {LINEAR_RESIDUAL_LIMIT:g}")
    if any(level.boundary_traction_relative_imbalance > MASS_LIMIT for level in levels):
        reasons.append(f"boundary traction relative imbalance exceeds {MASS_LIMIT:g}")
    velocity = [level.velocity_l2_error for level in levels]
    pressure = [level.pressure_l2_error for level in levels]
    velocity_order = _observed_order(*velocity, ratio)
    pressure_order = _observed_order(*pressure, ratio)
    velocity_gci = _fine_gci(velocity[1], velocity[2], ratio, velocity_order)
    pressure_gci = _fine_gci(pressure[1], pressure[2], ratio, pressure_order)
    if velocity_order is None or pressure_order is None or velocity_order < ORDER_LIMIT or pressure_order < ORDER_LIMIT:
        reasons.append(f"observed velocity and pressure order must each be at least {ORDER_LIMIT:g}")
    if velocity_gci is None or pressure_gci is None or velocity_gci > GCI_LIMIT or pressure_gci > GCI_LIMIT:
        reasons.append(f"fine-grid velocity and pressure GCI must each be at most {GCI_LIMIT:.0%}")
    return reasons, {
        "refinementRatio": ratio,
        "velocity": {"errors": velocity, "observedOrder": velocity_order, "fineGci": velocity_gci},
        "pressure": {"errors": pressure, "observedOrder": pressure_order, "fineGci": pressure_gci},
    }


def evaluate_mms_stage(levels: Sequence[RefinementObservation], *, definition: MmsDefinition | None = None) -> dict[str, Any]:
    if len(levels) != 3:
        raise ValueError("MMS stage requires exactly coarse, medium, and fine observations")
    reasons, convergence = _evaluate_common(levels)
    diagnosis: Literal["operator-source-implementation", "bc-coupling", "accepted"]
    if not reasons:
        diagnosis = "accepted"
    elif any("traction" in reason or "mass" in reason for reason in reasons):
        diagnosis = "bc-coupling"
    else:
        diagnosis = "operator-source-implementation"
    return {
        "schema": MMS_SCHEMA,
        "status": "accepted" if not reasons else "rejected",
        "rejectionReasons": reasons,
        "diagnosis": diagnosis,
        "definition": (definition or MmsDefinition()).manifest(),
        "levels": [asdict(level) for level in levels],
        "convergence": convergence,
        "requiredEvidence": ["exact initialization", "cellwise velocity and pressure field-error norms", "component residual histories", "flux balance", "boundary traction balance"],
    }


def evaluate_open_pipe_stage(levels: Sequence[PipeObservation]) -> dict[str, Any]:
    if len(levels) != 3:
        raise ValueError("open-pipe stage requires exactly coarse, medium, and fine observations")
    reasons, convergence = _evaluate_common(levels)
    if any(level.static_pressure_drop_relative_error > PIPE_PRESSURE_LIMIT for level in levels):
        reasons.append(f"open-pipe static pressure-drop error exceeds {PIPE_PRESSURE_LIMIT:.0%}")
    if any(level.wall_force_relative_imbalance > MASS_LIMIT for level in levels):
        reasons.append(f"wall-force relative imbalance exceeds {MASS_LIMIT:g}")
    if any(level.plateau_detected for level in levels):
        reasons.append("pressure or momentum QoI plateau was detected")
    diagnosis: Literal["open-pipe-formulation", "bc-coupling", "accepted"]
    if not reasons:
        diagnosis = "accepted"
    elif any("mass" in reason or "traction" in reason or "wall-force" in reason for reason in reasons):
        diagnosis = "bc-coupling"
    else:
        diagnosis = "open-pipe-formulation"
    return {
        "schema": PIPE_SCHEMA,
        "status": "accepted" if not reasons else "rejected",
        "rejectionReasons": reasons,
        "diagnosis": diagnosis,
        "formulation": {
            "mesh": "structured all-hex open pipe",
            "numericalFormulation": "fixed current formulation; no mesh redesign, solver-tolerance sweep, or boundary-condition substitution",
            "pressureReference": "corrected kinematic pressure reference",
        },
        "levels": [asdict(level) for level in levels],
        "convergence": convergence,
        "requiredEvidence": ["static pressure drop", "mass balance", "axial/transverse velocity error", "final linear residuals", "wall-force balance", "plateau history"],
    }


def evaluate_open_boundary_campaign(mms_levels: Sequence[RefinementObservation], pipe_levels: Sequence[PipeObservation]) -> dict[str, Any]:
    mms = evaluate_mms_stage(mms_levels)
    pipe = evaluate_open_pipe_stage(pipe_levels)
    accepted = mms["status"] == "accepted" and pipe["status"] == "accepted"
    if mms["status"] != "accepted":
        diagnosis = mms["diagnosis"]
    elif pipe["status"] != "accepted":
        diagnosis = pipe["diagnosis"]
    else:
        diagnosis = "accepted"
    return {
        "schema": SCHEMA,
        "status": "accepted" if accepted else "rejected",
        "diagnosis": diagnosis,
        "stages": {"forcedMms": mms, "openPipe": pipe},
        "frozenSurfaceContinuation": {
            "status": "unlocked" if accepted else "blocked",
            "requiredSchedule": [64, 96, 128],
            "reason": "Both BC stages passed." if accepted else "Both BC stages must pass before frozen-v2 exact-init, V&V/GCI, or MPI timing.",
        },
    }


def write_campaign_report(path: Path, report: dict[str, Any]) -> None:
    if report.get("schema") != SCHEMA:
        raise ValueError("refusing to write a non-campaign report")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
