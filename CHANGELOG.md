# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.40.0...HEAD)

Changes since `v4.40.0` (2026-09-10).

### Added
- Docker container for the web UI (features/container): a slim image that
  starts `janito --web --no-web-open` headless on `0.0.0.0`.
  `.github/workflows/docker.yaml` builds
  `linux/amd64` + `linux/arm64` with buildx and publishes to GHCR on every
  `v*` tag and on `main` (`:latest`). See `docs/usage/docker.md`.

### Fixed
- Fixed `import janito` on Python <= 3.13: the class-level annotation
  `set[str]` in `JsonFileStore.list_keys` shadowed the builtin `set` with the
  class's own `set` method and raised `TypeError: 'function' object is not
  subscriptable` (deferred in 3.14). Added `from __future__ import
  annotations` to `janito/json_store.py`.
