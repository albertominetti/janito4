# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.46.0...HEAD)

Changes since `v4.46.0` (2026-10-08).

## [v4.46.0](https://github.com/joaompinto/janito/compare/v4.45.0...v4.46.0) - 2026-10-08

Changes since `v4.45.0` (2026-09-30).

### Added

- Add `--delete-api-key` for the explicit or configured default provider, respecting
  the selected config location and preserving OAuth credentials and global fallbacks.

### Changed

- Change the built-in OpenAI default model from `gpt-6-luna` to `gpt-6.1-sol`.

### Fixed

- Keep the interactive shell running after provider API or connection errors,
  printing the error and rolling back the failed conversation turn.

- Do not load `RunBashCode` on Windows, even when Git Bash or WSL is installed; use `RunPowerShellCode` instead.

- Run GitHub CLI commands directly with an argument list so Windows executable paths containing spaces are handled correctly.

- Make `--login` skip the browser flow when ChatGPT OAuth credentials are already stored.

- Refuse `--set-api-key` for OpenAI while OAuth details are stored; require
  logout first and preserve existing credentials (#154).

- ChatGPT OAuth Responses: send the system prompt via top-level
  `instructions` instead of a `system` input item (fixes
  `400 {'detail': 'System messages are not allowed'}`), with `store:false`
  and no `temperature` on that path. Regular API-key behavior unchanged.
