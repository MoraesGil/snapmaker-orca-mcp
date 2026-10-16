FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv ca-certificates libgtk-3-0t64 libwebkit2gtk-4.1-0 \
    libopengl0 libglu1-mesa libegl1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /venv
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN /venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.txt \
    && rm /tmp/requirements-runtime.txt
COPY dist/snapmaker_slicer_mcp-0.4.0-py3-none-any.whl /tmp/
RUN /venv/bin/pip install --no-cache-dir --no-deps /tmp/snapmaker_slicer_mcp-0.4.0-py3-none-any.whl \
    && rm /tmp/snapmaker_slicer_mcp-0.4.0-py3-none-any.whl
ENV APPDIR=/opt/orca LD_LIBRARY_PATH=/opt/orca/lib/orca-runtime:/opt/orca/bin LC_ALL=C
COPY runtime/squashfs-root/ /opt/orca/
RUN chmod -R a+rX /opt/orca
ARG ENGINE_COMMIT
ARG ENGINE_EXECUTABLE_SHA256
RUN printf '%s  /opt/orca/bin/snapmaker-orca\n' "$ENGINE_EXECUTABLE_SHA256" | sha256sum --check - \
    && /venv/bin/snapmaker-slicer-mcp --engine /opt/slicer-mcp/engine.json bind-engine \
       --binary /opt/orca/bin/snapmaker-orca --resources /opt/orca/resources --commit "$ENGINE_COMMIT"
LABEL org.opencontainers.image.source="https://github.com/MoraesGil/snapmaker-orca-mcp" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="Snapmaker Orca MCP"
ENTRYPOINT ["/venv/bin/snapmaker-slicer-mcp"]
CMD ["--engine", "/opt/slicer-mcp/engine.json", "--artifacts", "/artifacts", "serve"]
