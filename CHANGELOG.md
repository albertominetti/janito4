# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.45.0...HEAD)

Changes since `v4.45.0` (2026-09-30).

### Fixed

- Make `--login` skip the browser flow when ChatGPT OAuth credentials are already stored.

- Refuse `--set-api-key` for OpenAI while OAuth details are stored; require
  logout first and preserve existing credentials (#154).

- ChatGPT OAuth Responses: send the system prompt via top-level
  `instructions` instead of a `system` input item (fixes
  `400 {'detail': 'System messages are not allowed'}`), with `store:false`
  and no `temperature` on that path. Regular API-key behavior unchanged.
