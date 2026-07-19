"""Small, dependency-free provenance helpers for replicated CFD timing.

This module deliberately does not start Docker or run a solver.  It turns
Docker inspection facts and measured numeric trials into conservative,
JSON-serializable records for a higher-level execution harness.
"""

from __future__ import annotations

from collections.abc import Iterable
import math
import statistics
from numbers import Real
from typing import Any


class PerformanceProtocolError(ValueError):
    """Raised when timing-protocol inputs cannot support a safe record."""


_ARCHITECTURE_ALIASES = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "x86-64": "amd64",
    "x64": "amd64",
    "intel64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "armv8": "arm64",
    "armv8l": "arm64",
    "386": "386",
    "i386": "386",
    "i686": "386",
    "x86": "386",
}


def normalize_architecture(value: str | None) -> str | None:
    """Return a canonical Docker architecture name, preserving unknown values.

    Docker reports both host-style (``x86_64``, ``aarch64``) and OCI-style
    (``amd64``, ``arm64``) names.  Unknown non-empty values are retained in a
    normalized form so the caller can make a conservative compatibility
    decision rather than silently treating them as a known architecture.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise PerformanceProtocolError("architecture must be a string or None")
    normalized = value.strip().lower().replace(" ", "")
    if not normalized:
        return None
    return _ARCHITECTURE_ALIASES.get(normalized, normalized)


def _normalize_os(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PerformanceProtocolError("operating system must be a string or None")
    normalized = value.strip().lower()
    return normalized or None


def _parse_requested_platform(requested_platform: str) -> dict[str, str | None]:
    if not isinstance(requested_platform, str):
        raise PerformanceProtocolError("requested platform must be a string")
    parts = [part.strip() for part in requested_platform.split("/")]
    if len(parts) not in (2, 3) or not all(parts[:2]):
        raise PerformanceProtocolError(
            "requested platform must use Docker os/architecture[/variant] form"
        )
    if len(parts) == 3 and not parts[2]:
        raise PerformanceProtocolError(
            "requested platform must use Docker os/architecture[/variant] form"
        )
    return {
        "os": _normalize_os(parts[0]),
        "architecture": normalize_architecture(parts[1]),
        "variant": parts[2].lower() if len(parts) == 3 else None,
    }


def native_compatibility_decision(
    *,
    requested_platform: str,
    engine_os: str | None,
    engine_architecture: str | None,
    image_os: str | None,
    image_architecture: str | None,
) -> dict[str, Any]:
    """Assess whether a requested container can run natively on its engine.

    ``nativeCompatible`` means only that the requested platform, image, and
    Docker Engine report identical OS/architecture values.  It intentionally
    does *not* make a bare-metal or physical-core-performance claim.
    """

    requested = _parse_requested_platform(requested_platform)
    engine = {
        "os": _normalize_os(engine_os),
        "architecture": normalize_architecture(engine_architecture),
    }
    image = {
        "os": _normalize_os(image_os),
        "architecture": normalize_architecture(image_architecture),
    }

    reasons: list[str] = []
    for source, values in (("Docker Engine", engine), ("container image", image)):
        if values["os"] is None:
            reasons.append(f"{source} operating system is missing")
        if values["architecture"] is None:
            reasons.append(f"{source} architecture is missing")

    if engine["os"] is not None and requested["os"] != engine["os"]:
        reasons.append(
            f"requested OS {requested['os']} does not match Docker Engine OS {engine['os']}"
        )
    if engine["architecture"] is not None and requested["architecture"] != engine["architecture"]:
        reasons.append(
            "requested architecture "
            f"{requested['architecture']} does not match Docker Engine architecture "
            f"{engine['architecture']}"
        )
    if image["os"] is not None and requested["os"] != image["os"]:
        reasons.append(
            f"requested OS {requested['os']} does not match container image OS {image['os']}"
        )
    if image["architecture"] is not None and requested["architecture"] != image["architecture"]:
        reasons.append(
            "requested architecture "
            f"{requested['architecture']} does not match container image architecture "
            f"{image['architecture']}"
        )
    if (
        engine["os"] is not None
        and image["os"] is not None
        and engine["os"] != image["os"]
    ):
        reasons.append(
            f"container image OS {image['os']} does not match Docker Engine OS {engine['os']}"
        )
    if (
        engine["architecture"] is not None
        and image["architecture"] is not None
        and engine["architecture"] != image["architecture"]
    ):
        reasons.append(
            "container image architecture "
            f"{image['architecture']} does not match Docker Engine architecture "
            f"{engine['architecture']}"
        )

    native_compatible = not reasons
    return {
        "requestedPlatform": {
            "os": requested["os"],
            "architecture": requested["architecture"],
            "variant": requested["variant"],
        },
        "engine": engine,
        "image": image,
        "nativeCompatible": native_compatible,
        "decision": "native-compatible" if native_compatible else "native-incompatible",
        "emulationRisk": not native_compatible,
        "reasons": reasons,
        "scope": (
            "container-architecture compatibility only; this record does not establish "
            "bare-metal execution or physical-core allocation"
        ),
    }


def classify_cpu_set_scope(
    *,
    engine_platform_name: str | None = None,
    engine_operating_system: str | None = None,
    engine_cpu_set_supported: bool | None = None,
) -> dict[str, Any]:
    """Classify what Docker ``--cpuset-cpus`` can honestly mean.

    Docker Desktop CPU identifiers belong to its Linux VM.  Native Linux Docker
    identifiers are host logical CPUs, which still are not guaranteed physical
    cores because of SMT and topology.  The returned record therefore always
    keeps ``physicalCorePinningClaimed`` false.
    """

    if engine_cpu_set_supported is not None and not isinstance(engine_cpu_set_supported, bool):
        raise PerformanceProtocolError("engine CPU-set support must be a boolean or None")
    platform_name = _normalize_os(engine_platform_name)
    operating_system = _normalize_os(engine_operating_system)
    desktop = any(
        value is not None and "docker desktop" in value
        for value in (platform_name, operating_system)
    )

    if desktop:
        scope = "docker-desktop-vm-vcpu-set"
        execution_layer = "docker-desktop-linux-vm"
        allocation_term = "Docker Desktop VM vCPU set"
        caveat = (
            "Docker --cpuset-cpus constrains Docker Desktop VM vCPUs; it is not a "
            "physical-core pinning claim."
        )
    elif operating_system == "linux":
        scope = "linux-docker-engine-logical-cpu-set"
        execution_layer = "linux-docker-engine"
        allocation_term = "Linux Docker Engine logical CPU set"
        caveat = (
            "Docker --cpuset-cpus constrains Linux logical CPUs; it is not a "
            "physical-core pinning claim."
        )
    else:
        scope = "unclassified-docker-engine-cpu-set"
        execution_layer = "unclassified-docker-engine"
        allocation_term = "unclassified Docker Engine CPU set"
        caveat = (
            "Docker CPU-set scope could not be classified; do not claim physical-core "
            "pinning."
        )

    return {
        "scope": scope,
        "executionLayer": execution_layer,
        "allocationTerm": allocation_term,
        "engineCpuSetSupported": engine_cpu_set_supported,
        "physicalCorePinningClaimed": False,
        "caveat": caveat,
    }


def summarize_numeric_trials(values: Iterable[Real]) -> dict[str, int | float]:
    """Return a robust, JSON-serializable summary of finite numeric trials."""

    samples: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise PerformanceProtocolError("trial values must be finite real numbers")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise PerformanceProtocolError("trial values must be finite real numbers")
        samples.append(numeric_value)
    if not samples:
        raise PerformanceProtocolError("at least one numeric trial is required")

    median = float(statistics.median(samples))
    median_absolute_deviation = float(
        statistics.median(abs(value - median) for value in samples)
    )
    return {
        "trialCount": len(samples),
        "median": median,
        "medianAbsoluteDeviation": median_absolute_deviation,
        "minimum": float(min(samples)),
        "maximum": float(max(samples)),
    }
