# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# janito — self-hosted web UI container.
#
# A slim runtime that runs the agent headless, with the server entrypoint
# baked in. Instead of a prebuilt binary
# we install the janito Python package (with the [web] extra) into a minimal
# Debian slim image using uv, and ship a small entrypoint script that turns a
# Docker-friendly environment into Janito CLI flags.
#
# The web server always starts with --no-web-open (a container has no desktop
# browser) and binds 0.0.0.0 so the UI is reachable from the Docker host.
#
# Build (version defaults to 0.0.0 when no git metadata is available):
#   docker build -t janito . --build-arg JANITO_VERSION=4.39.0
#
# Run:
#   docker run --rm -p 8080:8080 -v janito-config:/home/janito/.janito \
#     -v "$PWD:/workspace" -e JANITO_WEB_TOKEN=secret janito
# -----------------------------------------------------------------------------

# ---- Build stage ------------------------------------------------------------
# Resolve/build the janito wheel so the runtime stage can install a clean copy
# (no build toolchain, no source tree).
FROM python:3.14-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /src
COPY . .

# Without a git checkout setuptools-scm cannot detect the version, so it is
# passed explicitly (.github/workflows/docker.yaml computes it from tag/HEAD).
ARG JANITO_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_JANITO=${JANITO_VERSION}

RUN uv build --wheel --no-sources

# ---- Runtime stage ------------------------------------------------------------
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Do not run as root: the runtime runs as an unprivileged user.
RUN groupadd --gid 1000 janito \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash janito

# Use the uv binary from the official image instead of a pip install.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=build /src/dist/*.whl /tmp/janito/janito.whl

RUN uv pip install --system /tmp/janito/janito.whl[web] \
    && rm -rf /tmp/janito \
    && janito --version

WORKDIR /workspace
COPY docker/entrypoint.sh /usr/local/bin/janito-entrypoint
RUN chmod +x /usr/local/bin/janito-entrypoint

# Data volumes:
#   /home/janito/.janito  -> config + auth + secrets (survive restarts)
#   /workspace            -> the directory the agent works on
VOLUME ["/home/janito/.janito"]

# Reachable from the Docker host / other containers.
EXPOSE 8080

# Container smoke-test: the web server exposes /api/health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/usr/local/bin/janito-entrypoint", "--healthcheck"]

USER janito

ENTRYPOINT ["/usr/local/bin/janito-entrypoint"]