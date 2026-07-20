FROM ubuntu:20.04@sha256:c664f8f86ed5a386b0a340d981b8f81714e21a8b9c73f658c4bea56aa179d54a

USER root

ARG OPENFOAM_VERSION=20240612
ARG OPENFOAM_KEY_SHA256=fbaacfe37e704494f6e65ab424e67b031191769e84c31185acb701f9e74fe0a9
ARG GMSH_VERSION=4.15.2
ARG GMSH_ARCHIVE_SHA256=2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg python3 python3-pip \
    && curl -fsSL https://dl.openfoam.org/gpg.key -o /tmp/openfoam.gpg \
    && echo "${OPENFOAM_KEY_SHA256}  /tmp/openfoam.gpg" | sha256sum -c - \
    && gpg --dearmor --output /usr/share/keyrings/openfoam.gpg /tmp/openfoam.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/openfoam.gpg] http://dl.openfoam.org/ubuntu focal main" > /etc/apt/sources.list.d/openfoam.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "openfoam11=${OPENFOAM_VERSION}" \
    && curl -fsSL "https://gmsh.info/bin/Linux/gmsh-${GMSH_VERSION}-nox-Linux64-sdk.tgz" -o /tmp/gmsh-sdk.tgz \
    && echo "${GMSH_ARCHIVE_SHA256}  /tmp/gmsh-sdk.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gmsh-sdk.tgz -C /opt \
    && mv "/opt/gmsh-${GMSH_VERSION}-nox-Linux64-sdk" /opt/gmsh \
    && python3 -m pip install --no-cache-dir "huggingface-hub==0.34.4" \
    && groupadd --gid 1000 openfoam \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/openfoam openfoam \
    && mkdir -p /opt/flowlab-hf \
    && chown -R 1000:1000 /home/openfoam /opt/flowlab-hf \
    && rm -f /tmp/openfoam.gpg /tmp/gmsh-sdk.tgz \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/home/openfoam \
    GMSH_HOME=/opt/gmsh \
    PATH=/opt/gmsh/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/gmsh/lib \
    PYTHONPATH=/opt/gmsh/lib

LABEL org.flowlab.runtime.role="fda-re500-hf-nonpromotional-image-recovery-r3" \
      org.flowlab.runtime.architecture="linux/amd64" \
      org.flowlab.runtime.uid="1000" \
      org.flowlab.openfoam.version="11" \
      org.flowlab.openfoam.package.version="20240612" \
      org.flowlab.ubuntu.base.digest="sha256:c664f8f86ed5a386b0a340d981b8f81714e21a8b9c73f658c4bea56aa179d54a" \
      org.flowlab.openfoam.key.sha256="fbaacfe37e704494f6e65ab424e67b031191769e84c31185acb701f9e74fe0a9" \
      org.flowlab.gmsh.version="4.15.2" \
      org.flowlab.gmsh.archive.sha256="2f81d19efb0dd94426bdab010131da59b7dac1939a3073890dc32c86ecbaa8db"

COPY --chown=1000:1000 remote_runner.py /opt/flowlab-hf/remote_runner.py

USER 1000:1000
WORKDIR /home/openfoam
EXPOSE 7860
CMD ["python3", "/opt/flowlab-hf/remote_runner.py", "serve"]
