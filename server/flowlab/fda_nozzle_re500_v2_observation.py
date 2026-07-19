"""Prospective observation operators for the FDA nozzle Re=500 v2 preflight.

This module is deliberately separate from the completed v1 campaign.  It
implements two diagnostics that were frozen before the v2 preflight matrix:

* a CFD-to-PIV rectangular interrogation-window operator based on the three
  published legacy laboratory configurations; and
* pressure differences whose common x=0 reference offset cancels exactly.

Nothing in this module can authorize product promotion.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
from typing import Any, Sequence

from .fda_nozzle_re500 import (
    DEFAULT_IMAGE,
    FdaNozzleDefinition,
    PRESSURE_CODES,
    PRIMARY_PROFILE_STATIONS_M,
    _container_command,
    _expanded_mean_uncertainty,
    _gci,
    _header,
    _parse_probe_output,
    _validation_row,
    _write,
    _write_json,
    experimental_summary,
    run_command,
)


OBSERVATION_SCHEMA = "flowlab.fda-nozzle-re500-v2-observation.v1"
PRESSURE_SCHEMA = "flowlab.fda-nozzle-re500-v2-pressure-differences.v1"

# Hariharan et al. (2011), Table 3.  Lab-1 used 9-11 um/pixel;
# its prospective nominal value is the midpoint, 10 um/pixel.
PIV_KERNELS = (
    {
        "name": "legacy-lab-1-nominal",
        "axialWidthM": 32.0 * 10.0e-6,
        "radialWidthM": 32.0 * 10.0e-6,
        "source": "Hariharan et al. 2011 Table 3; 32x32 px at midpoint 10 um/px",
    },
    {
        "name": "legacy-lab-2",
        "axialWidthM": 32.0 * 11.0e-6,
        "radialWidthM": 32.0 * 11.0e-6,
        "source": "Hariharan et al. 2011 Table 3; 32x32 px at 11 um/px",
    },
    {
        "name": "legacy-lab-3",
        "axialWidthM": 32.0 * 13.7e-6,
        "radialWidthM": 16.0 * 13.7e-6,
        "source": "Hariharan et al. 2011 Table 3; 32 axial x 16 radial px at 13.7 um/px",
    },
)

GAUSS_NODES = (-math.sqrt(3.0 / 5.0), 0.0, math.sqrt(3.0 / 5.0))
GAUSS_WEIGHTS = (5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0)

NAMED_PRESSURE_DIFFERENCES = (
    ("overall-pressure-drop", -0.09032, 0.032),
    ("contraction-pressure-drop", -0.06299, -0.04001),
    ("throat-pressure-drop", -0.04001, -0.00203),
    ("expansion-pressure-recovery", 0.0, 0.032),
)


def observation_contract() -> dict[str, Any]:
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-observation-contract.v1",
        "status": "prospective-preflight-diagnostic-not-promotion",
        "piv": {
            "kernels": list(PIV_KERNELS),
            "quadrature": "3x3 Gauss-Legendre area average per rectangular interrogation window",
            "pooledPrediction": "arithmetic mean of the three laboratory-kernel predictions",
            "operatorUncertainty": "half range of the three laboratory-kernel predictions",
            "supportRule": "a validation point is eligible only when every published interrogation rectangle lies wholly inside the analytic fluid domain",
            "stepConvention": "the x=0 profile is downstream-sided; centred windows that cross solid at the step are ineligible",
        },
        "pressure": {
            "adjacentTapDifferences": True,
            "namedDifferences": [
                {"name": name, "leftM": left, "rightM": right}
                for name, left, right in NAMED_PRESSURE_DIFFERENCES
            ],
            "signConvention": "P(left)-P(right)",
            "experimentalUncertainty": "paired-trial 95% Student-t uncertainty of each difference",
            "covariance": "sample covariance and covariance of the mean across the 16 adjacent paired differences",
            "referenceOffset": "cancels exactly; no fitted or post-hoc offset is allowed",
        },
        "promotionAuthorized": False,
    }


def _support_inside_fluid(
    spec: FdaNozzleDefinition,
    axial: float,
    radial: float,
    kernel: dict[str, Any],
) -> bool:
    half_x = 0.5 * float(kernel["axialWidthM"])
    half_r = 0.5 * float(kernel["radialWidthM"])
    for x in (axial - half_x, axial + half_x):
        if x < spec.inlet_x_m or x > spec.outlet_x_m:
            return False
        for r in (radial - half_r, radial + half_r):
            sample_x = x if abs(x) > 1.0e-15 else 1.0e-15
            if abs(r) > spec.radius(sample_x) + 1.0e-12:
                return False
    return True


def _source_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    spec = FdaNozzleDefinition()
    records: list[dict[str, Any]] = []
    for station in PRIMARY_PROFILE_STATIONS_M:
        station_text = f"{station:.6f}"
        physical_radius = spec.radius(station if station != 0.0 else 1.0e-10)
        for row in summary["axialVelocityProfiles"][station_text]["axial"]:
            radial = float(row["coordinateM"])
            if abs(radial) > physical_radius + 1.0e-12:
                continue
            records.append(
                {
                    "recordId": f"profile:{station_text}:{radial:.12g}",
                    "qoi": "axialVelocityProfile",
                    "stationM": station,
                    "radialCoordinateM": radial,
                    "experimental": row,
                }
            )
        # The legacy radial component is explicitly nonpromotional, but it is
        # still passed through the same frozen interrogation-window operator so
        # the required radial-profile report does not mix ideal point samples
        # with finite-window PIV measurements.
        for row in summary["axialVelocityProfiles"][station_text]["radial"]:
            radial = float(row["coordinateM"])
            if abs(radial) > physical_radius + 1.0e-12:
                continue
            records.append(
                {
                    "recordId": f"radial-profile:{station_text}:{radial:.12g}",
                    "qoi": "radialVelocityProfile",
                    "stationM": station,
                    "radialCoordinateM": radial,
                    "experimental": row,
                }
            )
    for row in summary["centrelineAxialVelocity"]:
        axial = float(row["coordinateM"])
        records.append(
            {
                "recordId": f"centreline:{axial:.12g}",
                "qoi": "centrelineAxialVelocity",
                "coordinateM": axial,
                "experimental": row,
            }
        )
    return records


def piv_probe_plan(summary: dict[str, Any]) -> dict[str, Any]:
    spec = FdaNozzleDefinition()
    points: list[list[float]] = []
    point_index: dict[tuple[float, float, float], int] = {}
    logical_records: list[dict[str, Any]] = []

    def index_for(point: Sequence[float]) -> int:
        key = tuple(round(float(value), 14) for value in point)
        if key not in point_index:
            point_index[key] = len(points)
            points.append([float(value) for value in point])
        return point_index[key]

    for source in _source_records(summary):
        axial = float(source.get("stationM", source.get("coordinateM")))
        radial = float(source.get("radialCoordinateM", 0.0))
        kernels: list[dict[str, Any]] = []
        support_valid = all(
            _support_inside_fluid(spec, axial, radial, kernel)
            for kernel in PIV_KERNELS
        )
        if support_valid:
            for kernel in PIV_KERNELS:
                samples: list[dict[str, Any]] = []
                for axial_node, axial_weight in zip(GAUSS_NODES, GAUSS_WEIGHTS):
                    for radial_node, radial_weight in zip(GAUSS_NODES, GAUSS_WEIGHTS):
                        point = (
                            axial + 0.5 * float(kernel["axialWidthM"]) * axial_node,
                            radial + 0.5 * float(kernel["radialWidthM"]) * radial_node,
                            0.0,
                        )
                        samples.append(
                            {
                                "probeIndex": index_for(point),
                                "weight": axial_weight * radial_weight / 4.0,
                            }
                        )
                kernels.append(
                    {
                        "name": kernel["name"],
                        "axialWidthM": kernel["axialWidthM"],
                        "radialWidthM": kernel["radialWidthM"],
                        "samples": samples,
                    }
                )
        logical_records.append(
            {
                **source,
                "supportValid": support_valid,
                "kernels": kernels,
            }
        )
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-piv-probe-plan.v1",
        "points": points,
        "records": logical_records,
        "probeCount": len(points),
        "logicalRecordCount": len(logical_records),
        "supportValidCount": sum(record["supportValid"] for record in logical_records),
    }


def write_piv_probe_dictionary(case: Path, plan: dict[str, Any]) -> None:
    locations = "\n".join(
        "    (" + " ".join(f"{value:.17g}" for value in point) + ")"
        for point in plan["points"]
    )
    content = _header("system", "fdaPivProbes") + f"""type probes;
libs ("libsampling.so");
writeControl timeStep;
writeInterval 1;
fields (U);
fixedLocations true;
interpolationScheme cellPoint;
probeLocations
(
{locations}
);
"""
    _write(case / "system" / "fdaPivProbes", content)
    _write_json(case / "piv-probe-plan.json", plan)


def _piv_output(case: Path) -> Path:
    candidates = sorted((case / "postProcessing" / "fdaPivProbes").glob("**/U"))
    if not candidates:
        raise ValueError(f"missing fdaPivProbes output under {case}")
    return candidates[-1]


def _weighted_vector(
    values: Sequence[Sequence[float]], samples: Sequence[dict[str, Any]]
) -> list[float]:
    return [
        sum(
            float(sample["weight"])
            * float(values[int(sample["probeIndex"])][component])
            for sample in samples
        )
        for component in range(3)
    ]


def run_piv_observation(
    *,
    case: Path,
    result: Path,
    summary: dict[str, Any],
    workspace: Path,
    log: Path,
    image: str = DEFAULT_IMAGE,
) -> dict[str, Any]:
    result.mkdir(parents=True, exist_ok=True)
    plan = piv_probe_plan(summary)
    write_piv_probe_dictionary(case, plan)
    code = run_command(
        _container_command(
            image,
            workspace,
            case,
            "foamPostProcess -func fdaPivProbes -time '750,800'",
        ),
        case,
        log,
    )
    if code != 0:
        report = {
            "schema": OBSERVATION_SCHEMA,
            "status": "postprocessing-failed",
            "exitCode": code,
            "promotionAuthorized": False,
        }
        _write_json(result / "piv-observation.json", report)
        return report
    values_by_time = _parse_probe_output(_piv_output(case), True)
    times: dict[str, list[dict[str, Any]]] = {}
    for time in ("750", "800"):
        values = values_by_time.get(time, [])
        if len(values) != int(plan["probeCount"]):
            raise ValueError(
                f"PIV probe count mismatch at {time}: {len(values)} != {plan['probeCount']}"
            )
        observations: list[dict[str, Any]] = []
        for record in plan["records"]:
            output = {
                key: value
                for key, value in record.items()
                if key not in {"kernels"}
            }
            if not record["supportValid"]:
                observations.append(output)
                continue
            kernel_values = [
                {
                    "name": kernel["name"],
                    "velocityMPerS": _weighted_vector(values, kernel["samples"]),
                }
                for kernel in record["kernels"]
            ]
            pooled = [
                statistics.fmean(
                    item["velocityMPerS"][component] for item in kernel_values
                )
                for component in range(3)
            ]
            half_range = [
                0.5
                * (
                    max(item["velocityMPerS"][component] for item in kernel_values)
                    - min(item["velocityMPerS"][component] for item in kernel_values)
                )
                for component in range(3)
            ]
            output.update(
                {
                    "kernelPredictions": kernel_values,
                    "pooledVelocityMPerS": pooled,
                    "operatorHalfRangeMPerS": half_range,
                }
            )
            observations.append(output)
        times[time] = observations
    report = {
        "schema": OBSERVATION_SCHEMA,
        "status": "observed",
        "contract": observation_contract()["piv"],
        "probeCount": plan["probeCount"],
        "logicalRecordCount": plan["logicalRecordCount"],
        "supportValidCount": plan["supportValidCount"],
        "times": times,
        "promotionAuthorized": False,
    }
    _write_json(result / "piv-observation.json", report)
    return report


def _pressure_trials(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for item in experiment["files"]:
        if int(item["dataset-code"]) not in PRESSURE_CODES:
            continue
        plot = item["plots"].get("plot-wall-distribution-pressure")
        if not plot or plot.get("deleted"):
            continue
        trials.append(
            {
                "code": int(item["dataset-code"]),
                "values": {
                    round(float(x), 12): float(value)
                    for x, value in plot["rows"]
                },
            }
        )
    if len(trials) != 3:
        raise ValueError(f"expected three eligible pressure trials, found {len(trials)}")
    return trials


def _sample_covariance_matrix(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(vectors) < 2:
        raise ValueError("sample covariance requires at least two vectors")
    means = [statistics.fmean(row[index] for row in vectors) for index in range(len(vectors[0]))]
    return [
        [
            sum(
                (row[left] - means[left]) * (row[right] - means[right])
                for row in vectors
            )
            / (len(vectors) - 1)
            for right in range(len(means))
        ]
        for left in range(len(means))
    ]


def pressure_experimental_packet(experiment: dict[str, Any]) -> dict[str, Any]:
    summary = experimental_summary(experiment)
    coordinates = [
        float(row["coordinateM"])
        for row in summary["wallPressureRelativeToExpansion"]
    ]
    trials = _pressure_trials(experiment)

    def packet(name: str, left: float, right: float) -> dict[str, Any]:
        values = [
            trial["values"][round(left, 12)]
            - trial["values"][round(right, 12)]
            for trial in trials
        ]
        return {
            "name": name,
            "leftM": left,
            "rightM": right,
            "trialValuesPa": values,
            **_expanded_mean_uncertainty(values),
        }

    adjacent = [
        packet(f"adjacent-{index:02d}", left, right)
        for index, (left, right) in enumerate(zip(coordinates, coordinates[1:]))
    ]
    named = [packet(name, left, right) for name, left, right in NAMED_PRESSURE_DIFFERENCES]
    vectors = [
        [row["trialValuesPa"][trial_index] for row in adjacent]
        for trial_index in range(len(trials))
    ]
    covariance = _sample_covariance_matrix(vectors)
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-pressure-experiment.v1",
        "eligibleCodes": [trial["code"] for trial in trials],
        "adjacent": adjacent,
        "named": named,
        "adjacentSampleCovariancePa2": covariance,
        "adjacentMeanCovariancePa2": [
            [value / len(trials) for value in row] for row in covariance
        ],
        "commonReferenceOffsetCancels": True,
    }


def _wall_pressure_index(observation: dict[str, Any], time: str) -> dict[float, float]:
    return {
        round(float(row["requestedCoordinateM"]), 12): float(row["pressurePa"])
        for row in observation["times"][time]["wallSamples"]
    }


def _centreline_pressure_index(observation: dict[str, Any], time: str) -> dict[float, float]:
    return {
        round(float(row["coordinateM"]), 12): float(row["pressurePa"])
        for row in observation["times"][time]["probes"]
        if row["qoi"] == "centrelinePressure"
    }


def pressure_diagnostic(
    experiment: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    packet = pressure_experimental_packet(experiment)

    def comparison(source: dict[str, Any], kind: str) -> dict[str, Any]:
        getter = _wall_pressure_index if kind == "wall" else _centreline_pressure_index
        current = getter(observation, "800")
        previous = getter(observation, "750")
        left = round(float(source["leftM"]), 12)
        right = round(float(source["rightM"]), 12)
        simulated = current[left] - current[right]
        simulated_previous = previous[left] - previous[right]
        iterative = abs(simulated - simulated_previous)
        uncertainty = math.sqrt(float(source["u95"]) ** 2 + iterative**2)
        error = simulated - float(source["mean"])
        return {
            "name": source["name"],
            "leftM": source["leftM"],
            "rightM": source["rightM"],
            "experiment": {
                key: source[key]
                for key in ("mean", "sampleStd", "n", "u95", "trialValuesPa")
            },
            "simulationPa": simulated,
            "simulationPreviousPa": simulated_previous,
            "comparisonErrorPa": error,
            "iterativeUncertaintyPa": iterative,
            "diagnosticUncertaintyPa": uncertainty,
            "passesDiagnostic": abs(error) <= uncertainty,
        }

    result: dict[str, Any] = {
        "schema": PRESSURE_SCHEMA,
        "experimentalPacket": packet,
        "wall": {},
        "centreline": {},
        "promotionAuthorized": False,
    }
    for kind in ("wall", "centreline"):
        for group in ("adjacent", "named"):
            rows = [comparison(source, kind) for source in packet[group]]
            result[kind][group] = {
                "rows": rows,
                "passed": sum(row["passesDiagnostic"] for row in rows),
                "count": len(rows),
                "passFraction": sum(row["passesDiagnostic"] for row in rows)
                / len(rows),
                "rmsePa": math.sqrt(
                    statistics.fmean(row["comparisonErrorPa"] ** 2 for row in rows)
                ),
            }
    return result


def velocity_diagnostic(piv_observation: dict[str, Any]) -> dict[str, Any]:
    previous = {
        row["recordId"]: row for row in piv_observation["times"]["750"]
    }
    rows: list[dict[str, Any]] = []
    for current in piv_observation["times"]["800"]:
        experimental = current["experimental"]
        experimental_eligible = (
            current["supportValid"]
            and int(experimental.get("n", 0)) >= 3
            and experimental.get("u95") is not None
        )
        row = {
            key: current[key]
            for key in (
                "recordId",
                "qoi",
                "supportValid",
            )
        }
        for key in ("stationM", "radialCoordinateM", "coordinateM"):
            if key in current:
                row[key] = current[key]
        row["experimental"] = experimental
        row["experimentalEligible"] = experimental_eligible
        row["passesDiagnostic"] = False
        if experimental_eligible:
            component = 1 if current["qoi"] == "radialVelocityProfile" else 0
            simulated = float(current["pooledVelocityMPerS"][component])
            simulated_previous = float(
                previous[current["recordId"]]["pooledVelocityMPerS"][component]
            )
            operator = float(current["operatorHalfRangeMPerS"][component])
            iterative = abs(simulated - simulated_previous)
            uncertainty = math.sqrt(
                float(experimental["u95"]) ** 2 + operator**2 + iterative**2
            )
            error = simulated - float(experimental["mean"])
            row.update(
                {
                    "simulationMPerS": simulated,
                    "comparisonErrorMPerS": error,
                    "operatorUncertaintyMPerS": operator,
                    "iterativeUncertaintyMPerS": iterative,
                    "diagnosticUncertaintyMPerS": uncertainty,
                    "passesDiagnostic": abs(error) <= uncertainty,
                }
            )
        rows.append(row)

    def summarize(selected: Sequence[dict[str, Any]]) -> dict[str, Any]:
        eligible = [row for row in selected if row["experimentalEligible"]]
        passed = [row for row in eligible if row["passesDiagnostic"]]
        peak = max(
            (abs(float(row["experimental"]["mean"])) for row in eligible),
            default=0.0,
        )
        return {
            "reported": len(selected),
            "experimentalEligible": len(eligible),
            "passed": len(passed),
            "passFraction": len(passed) / len(eligible) if eligible else 0.0,
            "normalizedRmseByExperimentalPeak": (
                math.sqrt(
                    statistics.fmean(
                        float(row["comparisonErrorMPerS"]) ** 2 for row in eligible
                    )
                )
                / peak
                if eligible and peak > 0.0
                else None
            ),
        }

    profiles = [row for row in rows if row["qoi"] == "axialVelocityProfile"]
    centreline = [row for row in rows if row["qoi"] == "centrelineAxialVelocity"]
    stations = {
        f"{station:.6f}": summarize(
            [row for row in profiles if float(row["stationM"]) == station]
        )
        for station in PRIMARY_PROFILE_STATIONS_M
    }
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-velocity-diagnostic.v1",
        "profiles": {"summary": summarize(profiles), "stations": stations},
        "centreline": {"summary": summarize(centreline)},
        "rows": rows,
        "promotionAuthorized": False,
    }


def v1_spatial_validation(
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    indices = {
        label: {
            time: {row["recordId"]: row for row in observation["times"][time]}
            for time in ("750", "800")
        }
        for label, observation in observations.items()
    }
    rows: list[dict[str, Any]] = []
    for record_id, fine in indices["fine"]["800"].items():
        experimental = fine["experimental"]
        eligible = (
            fine["supportValid"]
            and int(experimental.get("n", 0)) >= 3
            and experimental.get("u95") is not None
        )
        base = {
            key: fine[key]
            for key in ("recordId", "qoi", "supportValid")
        }
        for key in ("stationM", "radialCoordinateM", "coordinateM"):
            if key in fine:
                base[key] = fine[key]
        if not eligible:
            rows.append(
                {
                    **base,
                    "experimental": experimental,
                    "experimentalEligible": False,
                    "qualified": False,
                    "passesVv20": False,
                }
            )
            continue

        def value(label: str, time: str) -> float:
            return float(indices[label][time][record_id]["pooledVelocityMPerS"][0])

        validation = _validation_row(
            experimental=experimental,
            coarse=value("coarse", "800"),
            medium=value("medium", "800"),
            fine=value("fine", "800"),
            fine_previous=value("fine", "750"),
            input_minus=value("input-minus-5pct", "800"),
            input_plus=value("input-plus-5pct", "800"),
        )
        operator = float(fine["operatorHalfRangeMPerS"][0])
        validation["uncertainty"]["observationOperator"] = operator
        if validation["qualified"]:
            updated = math.sqrt(
                float(validation["validationUncertainty"]) ** 2 + operator**2
            )
            validation["validationUncertainty"] = updated
            validation["errorToValidationUncertaintyRatio"] = abs(
                float(validation["comparisonError"])
            ) / max(updated, 1.0e-300)
            validation["passesVv20"] = abs(
                float(validation["comparisonError"])
            ) <= updated
        rows.append({**base, **validation})

    def counts(selected: Sequence[dict[str, Any]]) -> dict[str, Any]:
        eligible = [row for row in selected if row["experimentalEligible"]]
        qualified = [row for row in eligible if row["qualified"]]
        passed = [row for row in eligible if row["passesVv20"]]
        return {
            "reported": len(selected),
            "experimentalEligible": len(eligible),
            "gciQualified": len(qualified),
            "vv20Passed": len(passed),
            "gciQualifiedFraction": len(qualified) / len(eligible) if eligible else 0.0,
            "vv20PassFraction": len(passed) / len(eligible) if eligible else 0.0,
        }

    profiles = [row for row in rows if row["qoi"] == "axialVelocityProfile"]
    centreline = [row for row in rows if row["qoi"] == "centrelineAxialVelocity"]
    return {
        "schema": "flowlab.fda-nozzle-re500-v2-v1-spatial-reanalysis.v1",
        "status": "diagnostic-only-v1-verdict-unchanged",
        "profiles": {"counts": counts(profiles)},
        "centreline": {"counts": counts(centreline)},
        "rows": rows,
        "promotionAuthorized": False,
    }
