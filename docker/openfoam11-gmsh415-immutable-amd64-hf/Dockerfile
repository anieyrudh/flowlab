# Linux/AMD64 runtime for FlowLab's bounded Hugging Face qualification.
#
# OpenFOAM and Gmsh match the retained ARM64 runtime by version. The upstream
# OpenFOAM image and Gmsh SDK archive are content-pinned; the resulting Space
# revision and OCI image digest are recorded separately by the qualification.
FROM openfoam/openfoam11-graphical-apps@sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3

ARG GMSH_VERSION=4.15.2
ARG GMSH_ARCHIVE_SHA256=2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db
ARG HUGGINGFACE_HUB_VERSION=0.34.4

USER root

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        python3 \
        python3-pip \
    && curl -fsSL "https://gmsh.info/bin/Linux/gmsh-${GMSH_VERSION}-nox-Linux64-sdk.tgz" -o /tmp/gmsh-sdk.tgz \
    && echo "${GMSH_ARCHIVE_SHA256}  /tmp/gmsh-sdk.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gmsh-sdk.tgz -C /opt \
    && mv "/opt/gmsh-${GMSH_VERSION}-nox-Linux64-sdk" /opt/gmsh \
    && rm -f /tmp/gmsh-sdk.tgz \
    && python3 -m pip install --no-cache-dir "huggingface-hub==${HUGGINGFACE_HUB_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

ENV GMSH_HOME=/opt/gmsh \
    PATH=/opt/gmsh/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/gmsh/lib \
    PYTHONPATH=/opt/gmsh/lib

LABEL org.flowlab.runtime.role="fda-re500-hf-nonpromotional-qualification" \
      org.flowlab.runtime.architecture="linux/amd64" \
      org.flowlab.openfoam.version="11" \
      org.flowlab.openfoam.base.digest="sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3" \
      org.flowlab.gmsh.version="4.15.2" \
      org.flowlab.gmsh.archive.sha256="2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db" \
      org.flowlab.huggingface-hub.version="0.34.4"

COPY --chown=98765:98765 remote_runner.py /opt/flowlab-hf/remote_runner.py

USER 98765
WORKDIR /home/openfoam
EXPOSE 7860
CMD ["python3", "/opt/flowlab-hf/remote_runner.py", "serve"]
