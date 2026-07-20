FROM openfoam/openfoam11-graphical-apps@sha256:573afef69e3f91a634f7e4caadb6617616d9e713f6ce5656efd70fd0cfdca7b3

USER root

ARG GMSH_VERSION=4.15.2
ARG GMSH_ARCHIVE_SHA256=2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl python3 python3-pip \
    && curl -fsSL "https://gmsh.info/bin/Linux/gmsh-${GMSH_VERSION}-nox-Linux64-sdk.tgz" -o /tmp/gmsh-sdk.tgz \
    && echo "${GMSH_ARCHIVE_SHA256}  /tmp/gmsh-sdk.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gmsh-sdk.tgz -C /opt \
    && mv "/opt/gmsh-${GMSH_VERSION}-nox-Linux64-sdk" /opt/gmsh \
    && rm -f /tmp/gmsh-sdk.tgz \
    && python3 -m pip install --no-cache-dir "huggingface-hub==0.34.4" \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/home/openfoam \
    GMSH_HOME=/opt/gmsh \
    PATH=/opt/gmsh/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/gmsh/lib \
    PYTHONPATH=/opt/gmsh/lib

LABEL org.flowlab.runtime.role="fda-re500-hf-nonpromotional-qualification-r2" \
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
