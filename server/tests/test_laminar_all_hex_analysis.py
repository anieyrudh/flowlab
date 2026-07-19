from __future__ import annotations

from server.flowlab.laminar_all_hex_analysis import (
    failure_rates,
    main_effects,
    nested_claims,
    two_factor_interactions,
)


def _rows() -> list[dict]:
    rows = []
    for reynolds in (4.17, 16.67):
        for direction in ("forward", "reverse"):
            for length in (1.0, 4.0):
                for aspect in (1.0, 2.0):
                    for level in ("coarse", "medium", "fine"):
                        value = reynolds * length * aspect
                        rows.append(
                            {
                                "cellId": f"{len(rows)}",
                                "status": "accepted",
                                "reynoldsNumberHeightBased": reynolds,
                                "flowDirection": direction,
                                "lengthToHeightRatio": length,
                                "axialCellAspectRatio": aspect,
                                "level": level,
                                "metric": value,
                            }
                        )
    return rows


def test_balanced_effects_and_interactions_are_machine_readable() -> None:
    rows = _rows()
    effects = main_effects(rows, "metric")
    interactions = two_factor_interactions(rows, "metric")

    assert effects["reynoldsNumberHeightBased"]["effectRangeDecades"] > 0.0
    assert len(interactions) == 10
    assert interactions[0]["maximumAbsoluteAdditiveResidualDecades"] >= 0.0


def test_failure_rates_and_nested_claims_fail_closed() -> None:
    rows = _rows()
    rows[-1]["status"] = "rejected-scientific"

    assert failure_rates(rows)["level"]["fine"]["failed"] == 1
    claims = {row["claim"]: row for row in nested_claims(rows)}
    assert claims["full-predeclared-envelope"]["status"] == "rejected"
