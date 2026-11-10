# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Agent library, wave 1 (Session 3, `campaign_forge.agents.library`).** Eleven agents:
  - research: `audience_research`, `market_research`;
  - strategy: `positioning`, `marketing_strategist`;
  - content: `copywriter`, `ad_copy`, `social_media`, `email_marketing`;
  - creative: `art_director`;
  - quality: `critic`;
  - synthesis: `campaign_reporter`.

  Each has a validated output contract in `campaign_forge.agents.schemas`. Numbers are computed by code: effort shares, calendar weeks and platform-limit checks.
- **Labeled research.** Research findings are labeled as user-provided or model assumption. The final report lists every assumption to validate.
- **Templates:** `full-campaign` (with critic review), `ad-pack`, `social-calendar` and `email-sequence`. `full-campaign` is now the planner's default.
- **`campaign_forge.evaluation`:** deterministic checks (schema, injection echo, grounding, labeled claims, report sections, platform limits), rubrics, and a deterministic offline responder.
- **Eval `--workflow <template>` mode,** with per-output checks plus call and token metrics. CI now also runs the offline `full-campaign` eval.
- **Key-free demo.** `CF_PROVIDER=fake` runs any workflow from the CLI on the offline responder, labeled SIMULATED.
- **Workflow engine (Session 2, `campaign_forge.orchestration`).**
  - Declarative, JSON-serialisable workflow definitions, validated on structure (references, cycles) and against the agent registry (agents, bound inputs, artifact kinds, review wiring).
  - An engine with ready-set scheduling and parallel independent steps.
  - Declarative `when` conditions.
  - Per-step retries and timeouts; a per-invocation `max_calls` limit.
  - A bounded critic review loop.
  - Approval gates with approve, edit and reject.
  - Cooperative cancellation.
  - Failure isolation with partial results.
  - Pause on quota, rate-limit or budget errors, with idempotent fingerprint-based resume.
- **Run storage and planning.**
  - A checked run state machine.
  - In-memory and atomic JSON-file run stores, scoped per tenant.
  - A rule-based planner and a constrained LLM planner with a recorded fallback.
  - The `classic-campaign` template.
- **New CLI commands:** `agents list`, `workflows list|show|validate`, `execute`, and `runs list|show|resume|approve|cancel`. Runs are checkpointed to `CF_RUNS_DIR` (default `outputs/runs`). Exit code `3` means the run needs action.
- **Legacy agents.** The four v1 agents are registered as `legacy.*` agents, so every v1 call is budgeted, routed and recorded.
- **2.0 core foundation (Session 1).**
  - `campaign_forge.domain`: typed, versioned `Artifact`s; `Claim`s with an explicit provenance `Basis`; tool-created `Evidence` with a validator for fabricated citations; `TokenUsage`; tenant `Scope`.
  - `campaign_forge.core`: declarative `AgentSpec`, an `Agent` protocol, and an `AgentRegistry` with enable/disable and third-party discovery through the `campaign_forge.agents` entry-point group.
  - Also in `campaign_forge.core`: a thread-safe run `Budget` (LLM calls, tokens, wall time, revisions, repairs), a fault-isolated `EventBus`, and a `RunContext` through which every 2.0 agent call is budgeted, routed and recorded.
- **Provider layer (`campaign_forge.providers`).**
  - A `Provider` protocol.
  - A single `OpenAICompatibleProvider` with profiles for Groq, Gemini, Mistral and Cloudflare Workers AI.
  - A deterministic `FakeProvider`.
  - Structured output with bounded conversational self-repair.
  - A `ProviderRouter` with per-agent configuration (`CF_AGENT_OVERRIDES`), per-provider models (`CF_PROVIDER_MODELS`), `CF_FREE_ONLY` policy (default on), and explicit, recorded fallback (`CF_FALLBACK_PROVIDERS`).
- **Normalised provider errors.** `RateLimitedError`, `QuotaExhaustedError`, `ProviderAuthError` and others are all subclasses of `LLMError`. A daily or monthly quota is never retried, and a server hint longer than the maximum backoff is surfaced instead of slept through. Token usage is captured from every response.
- **Security primitives.**
  - Untrusted-content framing, plus a system-prompt policy, in every v1 agent.
  - Secret redaction in logs and provider error messages.
  - An architecture test that enforces downward-only imports and forbids `eval`/`exec`/`subprocess`/`pickle`.
- **Supporting changes.**
  - `scripts/provider_smoke.py`: a manual live check of configured providers (dry run by default).
  - `apply_overrides()` and `reload_settings()` in `campaign_forge.config`.
  - Python 3.13 in the CI matrix.
- **Campaign Forge 2.0 planning documents (Session 0).** No code changes. They cover:
  - the project audit with reproduced defects
  - the target architecture (Part II of `docs/ARCHITECTURE.md`)
  - the roadmap and task board
  - the architecture decision records
  - the agent catalog
  - the security model
  - the technology stack
  - the free-tier policy
  - build status tracking and a session log

### Changed

- **Behaviour change:** `CampaignWorkflow` (and therefore `campaign-forge run` / `batch`) now runs on the engine through the provider router. `CF_FREE_ONLY` applies to it: a paid endpoint such as OpenAI now needs `CF_FREE_ONLY=false` (ADR-023). Its signature, results and exception types are unchanged.
- `CF_AGENT_OVERRIDES` entries keyed by v1 agent names (`manager`, `research`, ...) now also accept a `provider` override.
- **Not yet adopted:** `openai` 3.x was evaluated and passes the offline suite. The `<3` pin stays until a live smoke test passes (ADR-025).
- API keys in `Settings` are now `SecretStr`, so they never appear in reprs or tracebacks. Use `settings.api_key` or `settings.secret(name)` to read them.
- `LLMClient` is now a compatibility facade over `OpenAICompatibleProvider`. Its signature and behaviour are unchanged; it adds `complete_with_usage()` and `for_agent()`.
- The "try again in" retry hint now also parses durations with minutes and hours (`7m12.5s`, `1h2m`) and the `retry-after-ms` header.

### Fixed

- **Eval live mode crashed.** The eval harness's live mode read an attribute the Session 2 `CampaignWorkflow` facade no longer has. It now reads the model from settings.
- **Many-channel briefs could never get a plan.** A brief may list up to 25 channels, but the plan contract capped channels at 8, and ads would have exceeded their variant limit. Found by the `many-channels` eval brief.
- **D9:** `campaign-forge batch` now saves each campaign as soon as it finishes, so an interrupted batch keeps its completed results. A failed save is reported and the command exits `1`.
- CLI output no longer swallows text in square brackets (for example `[copywriter]`) as Rich markup. All dynamic text is escaped, which also stops model output from injecting terminal markup.
- **D1:** CLI overrides skipped validation. `--workers -1` crashed with a traceback, and `--workers 500` bypassed the cap. Both now exit with code 2 and a clear message. `run_batch()` validates `max_workers`.
- **D2:** cached settings ignored later environment changes. Added `reload_settings()`; long-lived processes build `Settings` explicitly.
- **D3:** an empty Art Director result counted as success. It is now re-requested once, then raises `OutputParsingError`.
- **D4:** the Manager's output was never validated. A degenerate brief (no headings, or under 60 words) is re-requested once, then raises. A brief that is only missing a section is accepted, with a warning.
- **D5:** a raising `on_result` callback aborted the batch and lost the completed results. It is now logged and the batch continues. The docstring now correctly says the callback runs on the calling thread.
- **D7:** the eval grounding check passed on stop-words. It now requires the product's first distinctive name token.
- **D10:** the dependency `openai>=1.30` had no upper bound. Fresh installs resolved the 3.x SDK, which uses a different HTTP transport (`httpx2`), and the test suite failed to collect. It is now constrained to `openai>=1.30,<3` (ADR-021).
- **D8:** every agent shared one `max_tokens`. Per-agent overrides now reach the request; for example, `CF_AGENT_OVERRIDES={"manager": {"max_tokens": 1500}}`.

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
