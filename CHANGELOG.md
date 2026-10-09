# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/joaompinto/janito/compare/v4.46.0...HEAD)

Changes since `v4.46.0` (2026-10-08).

### Fixed

- ChatGPT-plan login no longer strands users on "Already signed in" with an
  expired/revoked token: `janito --login` refreshes transparently when
  expired and re-authenticates when refresh fails; `-f/--force` forces
  re-authentication even when fresh.
- ChatGPT-plan `401 token_expired` failures now explain re-authentication
  (`--logout` + `--login`, or `--login -f`) instead of "verify your API key".
