# Pinned runtime for FlowLab's narrow straight-pipe verification benchmark.
# The base is the Foundation OpenFOAM v11 image used by the existing adapter.
FROM openfoam/openfoam11-graphical-apps@sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gmsh \
    && rm -rf /var/lib/apt/lists/*

# Preserve the non-root OpenFOAM account from the upstream image.
USER 98765
