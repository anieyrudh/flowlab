# Isolated experimental runtime for immutable-surface meshing probes.
#
# This must never replace the accepted OpenFOAM v11/Gmsh 4.4 evidence image.
# It is Linux/AMD64 only because the upstream Gmsh SDK artifact is Linux64.
FROM --platform=linux/amd64 openfoam/openfoam11-graphical-apps@sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3

ARG GMSH_VERSION=4.15.2
ARG GMSH_ARCHIVE_SHA256=2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        python3 \
    && curl -fsSL "https://gmsh.info/bin/Linux/gmsh-${GMSH_VERSION}-nox-Linux64-sdk.tgz" -o /tmp/gmsh-sdk.tgz \
    && echo "${GMSH_ARCHIVE_SHA256}  /tmp/gmsh-sdk.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gmsh-sdk.tgz -C /opt \
    && mv "/opt/gmsh-${GMSH_VERSION}-nox-Linux64-sdk" /opt/gmsh \
    && rm -f /tmp/gmsh-sdk.tgz \
    && rm -rf /var/lib/apt/lists/*

ENV GMSH_HOME=/opt/gmsh
ENV PATH=/opt/gmsh/bin:${PATH}
ENV LD_LIBRARY_PATH=/opt/gmsh/lib:${LD_LIBRARY_PATH}
ENV PYTHONPATH=/opt/gmsh/lib:${PYTHONPATH}

LABEL org.flowlab.runtime.role="experimental-immutable-surface-probe" \
      org.flowlab.gmsh.version="4.15.2" \
      org.flowlab.gmsh.archive.sha256="2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db"

# Preserve the non-root OpenFOAM account from the upstream image.
USER 98765
