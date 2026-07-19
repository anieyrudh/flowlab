# Native ARM64 functional runtime for the immutable-surface campaign.
#
# This is intentionally a separate image from the pinned Linux/AMD64 evidence
# image.  Its OpenFOAM and Gmsh versions are held equal, but its architecture,
# base image and build provenance are independently recorded by every run.
FROM --platform=linux/arm64 ubuntu@sha256:ecd3706b6b5587d1318e1777359b8563f9db6e8e5a81841f04dc3c7edbefbdc1

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENFOAM_VERSION=11
ARG OPENFOAM_SOURCE_SHA256=ebc0f86ead699abba61290ba8aac5b730aa93256e675d1d93a5d5f116d51e0c0
ARG GMSH_VERSION=4.15.2
ARG GMSH_SOURCE_SHA256=be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e
# Docker Desktop currently supplies roughly 8 GiB to the native Linux VM.
# Keep source builds below that envelope; ``nproc`` reports 16 and causes the
# C++ compiler to be killed before Gmsh can finish.
ARG BUILD_JOBS=2

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bison \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        flex \
        libboost-system-dev \
        libboost-thread-dev \
        liblapack-dev \
        libopenmpi-dev \
        libreadline-dev \
        libscotch-dev \
        libz-dev \
        make \
        mpi-default-bin \
        python3 \
        python3-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# The official Gmsh binary SDK is Linux/AMD64 only.  Build the exact release
# source natively so its Python API and the Netgen optimizer are available to
# the unchanged volume builder.
RUN curl -fsSL https://gmsh.info/src/gmsh-${GMSH_VERSION}-source.tgz -o /tmp/gmsh-source.tgz \
    && echo "${GMSH_SOURCE_SHA256}  /tmp/gmsh-source.tgz" | sha256sum -c - \
    && mkdir -p /tmp/gmsh-source \
    && tar -xzf /tmp/gmsh-source.tgz -C /tmp/gmsh-source --strip-components=1 \
    && cmake -S /tmp/gmsh-source -B /tmp/gmsh-build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/gmsh \
        -DENABLE_BUILD_SHARED=ON \
        -DENABLE_BUILD_DYNAMIC=ON \
        -DENABLE_FLTK=OFF \
        -DENABLE_OCC=OFF \
        -DENABLE_PYTHON=ON \
    && cmake --build /tmp/gmsh-build --parallel "${BUILD_JOBS}" \
    && cmake --install /tmp/gmsh-build \
    && rm -rf /tmp/gmsh-source /tmp/gmsh-build /tmp/gmsh-source.tgz

# ``flex`` supplies the generator; this development package supplies the
# FlexLexer.h header consumed by OpenFOAM's generated STL reader.  The
# SYSTEMOPENMPI route additionally requires the parallel Scotch headers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfl-dev libptscotch-dev \
    && rm -rf /var/lib/apt/lists/*

# Foundation OpenFOAM distributes v11 source for compilation on GNU/Linux.
# System OpenMPI/Scotch avoid an unpinned ThirdParty build while preserving
# the v11 solver and utilities used by the campaign.
RUN curl --fail --location --silent --show-error --retry 5 --retry-all-errors --retry-delay 2 https://dl.openfoam.org/source/${OPENFOAM_VERSION} -o /tmp/openfoam-source.tgz \
    && echo "${OPENFOAM_SOURCE_SHA256}  /tmp/openfoam-source.tgz" | sha256sum -c - \
    && mkdir -p /opt/OpenFOAM \
    && tar -xzf /tmp/openfoam-source.tgz -C /opt/OpenFOAM \
    && mv /opt/OpenFOAM/OpenFOAM-${OPENFOAM_VERSION}-version-${OPENFOAM_VERSION} /opt/OpenFOAM/OpenFOAM-${OPENFOAM_VERSION} \
    && rm -f /tmp/openfoam-source.tgz \
    && /bin/bash -lc 'cd /opt/OpenFOAM/OpenFOAM-11 && source etc/bashrc && export WM_MPLIB=SYSTEMOPENMPI && ./Allwmake -j"${BUILD_JOBS}"'

ENV FOAM_INST_DIR=/opt/OpenFOAM \
    GMSH_HOME=/opt/gmsh \
    PATH=/opt/gmsh/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/gmsh/lib \
    PYTHONPATH=/opt/gmsh/lib

LABEL org.flowlab.runtime.role="experimental-immutable-surface-probe-arm64-functional" \
      org.flowlab.runtime.architecture="linux/arm64" \
      org.flowlab.openfoam.version="11" \
      org.flowlab.openfoam.source.sha256="ebc0f86ead699abba61290ba8aac5b730aa93256e675d1d93a5d5f116d51e0c0" \
      org.flowlab.gmsh.version="4.15.2" \
      org.flowlab.gmsh.source.sha256="be3f66f225d27ba9fa014f07e83169285da8a051b0e8ab7103d88066b39bdd3e"

RUN groupadd --gid 98765 openfoam \
    && useradd --uid 98765 --gid 98765 --create-home --shell /bin/bash openfoam \
    && mkdir -p /home/openfoam/OpenFOAM/openfoam-11 \
    && chown -R openfoam:openfoam /home/openfoam

USER 98765
WORKDIR /home/openfoam
