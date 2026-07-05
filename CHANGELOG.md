# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-05

The project was re-engineered from an initial prototype into a production-grade,
installable package (`campaign_forge`).

### Added

- Installable package with a `campaign-forge` console entry point and
  `python -m campaign_forge` support.
- Typed, validated domain models (`CampaignBrief`, `CopyContent`,
  `CampaignResult`) built on Pydantic.
- A resilient `LLMClient` with retries, exponential backoff with jitter,
  `Retry-After` awareness, per-request timeouts, and empty-response detection.
- `CampaignWorkflow.run_batch()` for concurrent, order-preserving batch
  processing with per-item error isolation.
- A `BaseAgent` abstraction shared by the research, copywriter, art-director,
  and manager agents.
- A command-line interface with `run` and `batch` subcommands, Rich rendering,
  and machine-readable `--json` output.
- Output persistence: each result is saved as Markdown and JSON.
- Configuration via `pydantic-settings` (environment variables and `.env`) with
  no import-time side effects and lazy API-key resolution.
- Structured logging to stderr on a dedicated namespace.
- A typed exception hierarchy (`CampaignForgeError` and subclasses).
- A fully mocked test suite (no network or API key required) with continuous
  integration on Python 3.10-3.12.
- Project documentation: `README.md`, `docs/ARCHITECTURE.md`, `CONTRIBUTING.md`,
  `.env.example`, and an MIT `LICENSE`.
- Tooling: `pyproject.toml`, Ruff (lint and format), `mypy --strict`, pytest with
  coverage, and pre-commit hooks.

### Changed

- Replaced the ad-hoc `src/` module layout and dictionary-passing pipeline with a
  typed, validated, dependency-injected architecture.
- Configuration no longer raises at import time; a missing key is reported only
  when a model call is attempted.

[1.0.0]: https://github.com/syed-waleed-ahmed/multi_agent_workflow/releases/tag/v1.0.0
