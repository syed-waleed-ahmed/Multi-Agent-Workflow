# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0b1] - 2026-11-11

The first Campaign Forge 2.0 pre-release, covering Sessions 1-7 of the 2.0 build:
- the workflow engine and provider router;
- a 15-agent library with evidence-backed research;
- scoped memory and brand profiles;
- the Streamlit app, live demo at https://campaign-forge.streamlit.app/;
- the FastAPI service, with Docker.

Live acceptance on Groq's free tier passed on 2026-11-11: the v1 pipeline 2/2, and `full-campaign` 1/1 with every check green (19 model calls, 27,129 tokens). The v1 `run`/`batch` CLI and the `CampaignWorkflow` API are unchanged. One behaviour change: `CF_FREE_ONLY` now applies to them (ADR-023).

### Added

- **HTTP API (Session 7, ADR-034).** `python -m campaign_forge.api`, or `uvicorn campaign_forge.api.app:create_app --factory`. Install the `[api]` extra.
  - The 13 endpoints from the master prompt, plus run approval and listings, with typed models.
  - Authentication with `X-API-Key` (`CF_API_KEYS`); each key is its own tenant, and the API is closed by default.
  - Per-key request and run-start limits, and a per-process cap on active runs.
  - One redacted JSON error shape.
  - The schema is published in `docs/openapi.json` and checked by a test; usage is documented in `docs/API.md`, with `examples/api_client.py`.
  - A `Dockerfile` (non-root, healthcheck) and `compose.yaml` (SQLite on a volume). A CI job builds the image and smoke-tests it (`scripts/api_smoke.py`).
- **Service layer** (`campaign_forge.services`): `RunService` and `Platform` are shared by the API and the web app. An architecture rule keeps business logic out of the API.
- **Cap-aware routing (ADR-035).** An agent whose answer may exceed the default model's per-request output cap uses the provider's strong model instead of being truncated.
- **Streamlit app (Session 6, ADR-033).**
  - `streamlit run app/streamlit_app.py` opens the 16 navigation entries of ADR-018: Dashboard, New Run, Run Inspector, History, seven studios, Brand Memory, Agents, Workflow Builder, Evaluation Lab (a placeholder until Session 8), and Settings and Health.
  - Demo mode (the default) is simulated, labeled SIMULATED, and keeps everything in the browser session.
  - New Run previews the plan and starts only on "Approve and run". The Run Inspector shows the live graph, steps, outputs, claims, evidence, approvals, resume and cancel.
  - New `[ui]` extra; `app/requirements.txt` for Streamlit Community Cloud. The demo is live at https://campaign-forge.streamlit.app/.
- **Exports (`campaign_forge.exports`):** a run as Markdown, JSON or a standalone HTML page. Raw HTML is disabled, so model text cannot inject markup.
- **Inline reasoning is stripped.** `<think>` (and similar) blocks are removed from provider responses before parsing or storage, so chain of thought is never shown or saved.
- **Memory and persistence (Session 5, ADR-031).**
  - `campaign_forge.memory`: a scoped `MemoryStore`, and `SqliteMemoryStore` on the stdlib `sqlite3` module. It stores brand profiles, campaign history and notes.
  - FTS5 search with BM25 ranking, plus a LIKE fallback when the SQLite build lacks FTS5.
  - Every string is redacted before it is stored.
  - `delete_scope` removes everything a tenant stored.
  - `SqliteRunStore` (`CF_RUN_STORE=sqlite`) keeps runs in the same file.
  - Completed CLI runs are remembered as searchable campaign history (`CF_MEMORY_HISTORY`).
- **Brand profiles and brand agents.**
  - `brand.profile@1`: voice, tone words, values, always/never guidelines, preferred and banned terms, and visual style.
  - New agents: `brand_strategist` writes a profile; `brand_consistency` counts banned and preferred terms by code and has a model judge only the tone.
  - The copywriter (and its critic) follows an optional brand. With a brand bound, banned terms are contract violations, so they are repaired.
- **New `brand-kit` template.** Every content template accepts an optional `brand` input. `full-campaign` adds a brand check when one is given, and its report includes the profile and the check.
- **CLI:**
  - `memory brand save|list|show|delete`, `memory note`, `memory search`, `memory list`;
  - `memory forget --yes [--runs]` (refuses without `--yes`);
  - `execute --brand <name or file.json>`;
  - `execute --auto-resume N` waits out rate-limit pauses (ADR-032).
- **`CF_PROVIDER_CONCURRENCY`:** a per-provider limit on requests in flight (Groq defaults to 1).
- **Evidence-backed research (Session 4, ADR-029).**
  - `campaign_forge.tools`: Wikipedia search (keyless, the default), optional Tavily search (`TAVILY_API_KEY`), a guarded `SafeFetcher` for user-supplied URLs, and user sources from `.txt`, `.md`, `.csv` and `.json` files.
  - SSRF defence (`security/url_safety.py`), re-checked on every redirect, plus robots.txt handling and size, time and content-type caps.
  - The research agents plan queries, retrieve evidence, then synthesise. Every citation is validated against the evidence actually retrieved, so invented citations trigger repair.
  - Artifacts embed the evidence they cite, and the `citations_verified` check uses it. The report gains a Sources section.
- **New agents:** `competitor_research` (it may name only competitors that appear in the brief, the sources or the evidence) and `fact_checker` (claim-by-claim verdicts mapped by code).
- **New `competitor-report` template.** Workflows can declare optional inputs; the research templates accept `sources`.
- **CLI:** `execute --sources <files or URLs>`. With `CF_PROVIDER=fake`, the CLI and evals use a simulated offline search. New eval option `--only <ids>`.
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

- **D19: long answers were truncated on Groq's free tier.** A live run lost its social calendar, and the v1 research answer overflowed. Both needed more than qwen3.8's 1,000 tokens per request. Routing is now cap-aware (ADR-035).
- **D16: parallel requests starved each other on Groq's free tier.** Under the 1,000 output-token/minute cap, two requests in flight exhausted their retries and paused the run. Groq requests are now serialised (ADR-032).
- **D17: the v1 final brief overflowed the output cap.** It needs more than 1,000 tokens even when asked to be concise. The v1 manager (2,000 tokens) and the campaign reporter now use the `strong` model class, `openai/gpt-oss-120b` on Groq, which returned 2,500 tokens in one request in a live probe.
- **D18: auto-resume crawled on an exhausted daily quota.** A daily quota's short retry hint frees room for about one call. Only rate-limit pauses are auto-resumed now.
- **D11: retired default model.** Groq retired `llama-3.1-8b-instant`, the default since v1, so every live run returned a 404. The default is now `qwen/qwen3.8-27b`, verified live (ADR-028). A `model_not_found` error now explains how to list the available models.
- **D12: requests above the output cap.** Groq's free tier rejects requests whose `max_tokens` exceeds its 1,000-token output limit. Profiles now carry per-model output caps, and requests are clamped to them (ADR-030).
- **D13: "Request too large" treated as a rate limit.** It was retried and then paused the run. It is now a non-retryable bad request with advice.
- **D14: truncation failed the whole step.** A truncated answer now gets one bounded, concise re-ask.
- **D15: step timeouts too short.** The 120-second step limit was shorter than free-tier rate-limit waits. The default is now 900 s, and template budgets were raised.
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

[Unreleased]: https://github.com/syed-waleed-ahmed/Multi-Agent-Workflow/compare/v2.0.0b1...HEAD
[2.0.0b1]: https://github.com/syed-waleed-ahmed/Multi-Agent-Workflow/releases/tag/v2.0.0b1
[1.1.0]: https://github.com/syed-waleed-ahmed/Multi-Agent-Workflow/releases/tag/v1.1.0

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

[1.0.0]: https://github.com/syed-waleed-ahmed/Multi-Agent-Workflow/releases/tag/v1.0.0
