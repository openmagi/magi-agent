# Changelog

Status: ✅ Active — this page tracks user-facing changes; tagged builds are on GitHub Releases.

Open Magi Agent is in early beta. For the authoritative list of tagged builds and
their assets, see [GitHub Releases](https://github.com/openmagi/magi-agent/releases).
This page summarizes notable user-facing changes between releases.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Versions follow the tags published on GitHub Releases.

## Unreleased

### Added
- `magi doctor` now runs real environment diagnostics: provider configuration,
  the `litellm` dependency, config-file readability, and workspace writability.
- Documentation: "What works today" capability page, a "Common tasks → command"
  index, a Telegram/Discord channels guide, a glossary, and this changelog.

### Changed
- Documentation now clearly separates the local CLI's real execution (a provider
  key enables a real model plus first-party tools behind permission prompts) from
  the enforcement/governance layer, which ships default-off (shadow).
- Configuration docs split local CLI setup (one provider key) from deployment
  variables that the local CLI does not need.

> Earlier history predates this changelog. Use `git log` and GitHub Releases for
> a complete record.
