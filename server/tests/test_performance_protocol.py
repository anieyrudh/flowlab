from __future__ import annotations

import json
import math

import pytest

from server.flowlab.performance_protocol import (
    PerformanceProtocolError,
    classify_cpu_set_scope,
    native_compatibility_decision,
    normalize_architecture,
    summarize_numeric_trials,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("aarch64", "arm64"),
        ("armv8l", "arm64"),
        ("riscv64", "riscv64"),
        (None, None),
    ],
)
def test_normalize_architecture_handles_docker_and_host_spellings(
    value: str | None, expected: str | None
) -> None:
    assert normalize_architecture(value) == expected


def test_native_compatibility_accepts_normalized_matching_architectures() -> None:
    decision = native_compatibility_decision(
        requested_platform="linux/amd64",
        engine_os="linux",
        engine_architecture="x86_64",
        image_os="linux",
        image_architecture="amd64",
    )

    assert decision["nativeCompatible"] is True
    assert decision["decision"] == "native-compatible"
    assert decision["emulationRisk"] is False
    assert decision["reasons"] == []
    json.dumps(decision)


def test_native_compatibility_rejects_an_amd64_image_on_an_arm64_engine() -> None:
    decision = native_compatibility_decision(
        requested_platform="linux/amd64",
        engine_os="linux",
        engine_architecture="aarch64",
        image_os="linux",
        image_architecture="amd64",
    )

    assert decision["nativeCompatible"] is False
    assert decision["emulationRisk"] is True
    assert any("Docker Engine architecture arm64" in reason for reason in decision["reasons"])
    assert any("container image architecture amd64" in reason for reason in decision["reasons"])


def test_native_compatibility_rejects_an_invalid_requested_platform() -> None:
    with pytest.raises(PerformanceProtocolError, match="os/architecture"):
        native_compatibility_decision(
            requested_platform="amd64",
            engine_os="linux",
            engine_architecture="amd64",
            image_os="linux",
            image_architecture="amd64",
        )


def test_docker_desktop_cpu_sets_are_never_described_as_physical_core_pinning() -> None:
    classification = classify_cpu_set_scope(
        engine_platform_name="Docker Desktop 4.52.0",
        engine_operating_system="Docker Desktop",
        engine_cpu_set_supported=True,
    )

    assert classification["scope"] == "docker-desktop-vm-vcpu-set"
    assert classification["executionLayer"] == "docker-desktop-linux-vm"
    assert classification["physicalCorePinningClaimed"] is False
    assert "physical-core pinning claim" in classification["caveat"]
    json.dumps(classification)


def test_linux_engine_cpu_sets_remain_logical_cpu_not_physical_core_claims() -> None:
    classification = classify_cpu_set_scope(
        engine_platform_name="moby",
        engine_operating_system="Linux",
        engine_cpu_set_supported=True,
    )

    assert classification["scope"] == "linux-docker-engine-logical-cpu-set"
    assert classification["physicalCorePinningClaimed"] is False
    assert "logical CPUs" in classification["caveat"]


def test_cpu_set_classification_rejects_non_boolean_capability_values() -> None:
    with pytest.raises(PerformanceProtocolError, match="boolean"):
        classify_cpu_set_scope(engine_cpu_set_supported="yes")  # type: ignore[arg-type]


def test_numeric_trial_summary_reports_median_mad_and_range() -> None:
    summary = summarize_numeric_trials([1.0, 3.0, 10.0])

    assert summary == {
        "trialCount": 3,
        "median": 3.0,
        "medianAbsoluteDeviation": 2.0,
        "minimum": 1.0,
        "maximum": 10.0,
    }
    json.dumps(summary)


def test_numeric_trial_summary_handles_even_samples_and_generators() -> None:
    summary = summarize_numeric_trials(value for value in (2, 6))

    assert summary["median"] == 4.0
    assert summary["medianAbsoluteDeviation"] == 2.0
    assert summary["minimum"] == 2.0
    assert summary["maximum"] == 6.0


@pytest.mark.parametrize("values", [[], [1.0, math.nan], [1.0, math.inf], [True]])
def test_numeric_trial_summary_rejects_empty_or_nonfinite_values(values: list[object]) -> None:
    with pytest.raises(PerformanceProtocolError):
        summarize_numeric_trials(values)
