# Docker

Run the Janito **web UI** as a self-hosted container — headless, without
opening a browser. It is a slim runtime image built on **Python 3.14** with the
entrypoint baked in, published to the GitHub Container Registry and usable with
`docker run`.

## Image

Images are published to **GitHub Container Registry** (GHCR) by the
`.github/workflows/docker.yaml` workflow:

- `ghcr.io/<owner>/<repo>:latest` — rolling build from `main`
- `ghcr.io/<owner>/<repo>:<version>` and `:latest` — cut on every `v*` tag
- Multi-arch: `linux/amd64` and `linux/arm64`

Unlike the CLI, the container **always** starts the server with
`--no-web-open` (a container has no desktop browser) and binds `0.0.0.0` so
the UI is reachable from the host.

```bash
docker pull "ghcr.io/albertominetti/janito4:latest"
```

## Quick Start

```bash
docker run --rm -p 8080:8080 \
  -v janito-config:/home/janito/.janito \
  -v "$PWD:/workspace" \
  ghcr.io/albertominetti/janito4:latest
```

- Web UI: <http://localhost:8080>
- `janito-config` volume keeps config + API keys + secrets (`~/.janito`)
  across restarts.
- `-v "$PWD:/workspace"` lets the agent work on your project. Session
  history is persisted under `<workspace>/.janito/sessions/`.

### First run: set an API key

The server is read-only out of the box, exactly like the CLI. On first run,
open the **Settings drawer** in the UI and set a provider API key ("Set API
Key" + "Set Default" + Save) — it is written to the `janito-config` volume
(`~/.janito/`), so it survives restarts.

Alternatively, mount an existing local Janito config instead of a brand-new
volume:

```bash
docker run --rm -p 8080:8080 \
  -v "$HOME/.janito:/home/janito/.janito" \
  -v "$PWD:/workspace" \
  ghcr.io/albertominetti/janito4:latest
```

### Auth token

Set `JANITO_WEB_TOKEN` to require a bearer token on all `/api` endpoints:

```bash
docker run --rm -p 8080:8080 \
  -e JANITO_WEB_TOKEN=my-secret-token \
  ...
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_HOST` | `0.0.0.0` | Bind address of the web server |
| `WEB_PORT` | `8080` | Port of the web server |
| `JANITO_WEB_TOKEN` | *(unset)* | Optional bearer-token auth for the web UI |

Any other Janito CLI flag can be appended to the `docker run` command:

```bash
# read+write privileges, thinking enabled, verbose logging
docker run --rm -p 8080:8080 ghcr.io/albertominetti/janito4:latest -r -w -t -v
```

The container runs as a non-root `janito` user (UID/GID `1000`). When binding
a host directory into `/workspace`, make sure the container user can write to
it (e.g. `-u "$(id -u):$(id -g)"` or `chown` the directory).

## Build locally

```bash
# 0.0.0 is used as version when no git metadata is available:
docker build -t janito .
# Pin the version (PEP 440, no leading "v"):
docker build -t janito:4.39.0 --build-arg JANITO_VERSION=4.39.0 .
# Build for the current platform only; use buildx for multi-arch:
docker buildx build --platform linux/amd64,linux/arm64 -t janito .
```

The image is self-tested with a `HEALTHCHECK` against `/api/health`.

## Publishing to GHCR

`.github/workflows/docker.yaml` builds and pushes the image on:

- pushes to `main` (only when `Dockerfile`, `.dockerignore`,
  `docker/entrypoint.sh` or the workflow itself change)
- `v*` tags (in addition to `latest`, `:semver` and `:major.minor` tags)
- manual `workflow_dispatch` runs

The workflow computes the `JANITO_VERSION` build argument from the git
checkout with `setuptools-scm` (the image build context has no `.git`), uses
`buildx` + `docker/setup-qemu-action` for cross-arch builds, and pushes to
GHCR with the `GITHUB_TOKEN` (`packages: write` permission).