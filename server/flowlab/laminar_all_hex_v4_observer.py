"""V4 physical observer using the shared staged engine at a common floor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .laminar_all_hex_v3_observer import observe_physical_staged
from .laminar_all_hex_v4_contract import MINIMUM_ITERATIONS, termination_contract
from .open_boundary_laminar_force_benchmark import PlanePoiseuille


def observe_physical_v4(
    root: Path,
    label: str,
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
) -> dict[str, Any]:
    return observe_physical_staged(
        root,
        label,
        n,
        spec,
        axial_cell_aspect_ratio,
        minimum_iterations=MINIMUM_ITERATIONS,
        minimum_is_window_start=False,
        convergence_contract=termination_contract(),
    )
