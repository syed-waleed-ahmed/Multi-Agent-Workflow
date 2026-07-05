# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-07-05

Robustness pass: harden the pipeline against real-world, adversarial, and
non-English input, and add a measurement layer for it.

### Added

- **Input bounds** on `CampaignBrief`: every field now has a maximum length and
  the channel list is capped in both count and per-item length, so oversized
  input is rejected at the boundary before it inflates prompt size and cost.
- **Copywriter self-repair**: a malformed JSON response is re-requested once with
  the parse error fed back, so a single bad response no longer aborts the whole
  campaign.
- **Truncation detection** in `LLMClient`: a response cut off at the token limit
  (`finish_reason == "length"`) now raises `LLMError` instead of silently
  returning a partial brief.
- **Evaluation harness** (`evals/`): a diverse, deliberately adversarial brief
  corpus plus a scorer that measures real-model completion, grounding, and
  prompt-injection resistance. Runs offline (deterministic fake model) or against
  a live provider, and exits non-zero below a configurable pass-rate gate.

### Changed

- `CampaignResult.save()` writes output files with an atomic, collision-safe name
  (exclusive create plus a numeric suffix) and now accepts a string path as well
  as a `Path`.

### Fixed

- **Silent data loss** when two results shared a timestamp-second and slug
  (routine in a concurrent batch, and universal for non-Latin product names,
  which all slugify to the same fallback): the previous `save()` overwrote the
  earlier file. Output files are now guaranteed distinct.

[1.1.0]: https://github.com/syed-waleed-ahmed/multi_agent_workflow/releases/tag/v1.1.0

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
