"""Run and aggregate the frozen laminar-all-hex-v4 scientific matrix."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .laminar_all_hex_campaign_runner import run_execution
from .laminar_all_hex_v4_contract import (
    CAMPAIGN_ID,
    build_manifest,
    validate_manifest,
)


def run_v4(output: Path, *, capacity: int | None = None) -> dict:
    return run_execution(
        output,
        capacity=capacity,
        campaign_id=CAMPAIGN_ID,
        manifest_builder=build_manifest,
        manifest_validator=validate_manifest,
        worker_module="server.flowlab.laminar_all_hex_v4_worker",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity", type=int)
    args = parser.parse_args()
    report = run_v4(args.output.resolve(), capacity=args.capacity)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "numerical-lanes-accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
