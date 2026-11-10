# Architecture Decision Records

This file is an append-only log of the significant decisions behind Campaign Forge 2.0.

- A decision is never edited away. To change one, add a new ADR that supersedes it.
- **Status values:**
  - **Accepted**: consistent with the master prompt; implementation may proceed.
  - **Proposed**: deviates from the master prompt; awaiting the owner's approval.
  - **Superseded**: replaced by a later ADR.

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| 001 | Evolve v1.1 in place; preserve its public API and tests | Accepted | 2026-11-10 |
| 002 | Deterministic workflow graph and a constrained planner, not autonomous loops | Accepted | 2026-11-10 |
| 003 | One OpenAI-compatible provider implementation plus vendor profiles | Accepted | 2026-11-10 |
| 004 | Agents communicate only through typed, versioned artifacts with claim-level provenance | Accepted | 2026-11-10 |
| 005 | Engine-driven "plan, retrieve, synthesise" research instead of native tool calling | Accepted | 2026-11-10 |
| 006 | Streamlit calls the core in-process; FastAPI is a separate optional service | Accepted | 2026-11-10 |
| 007 | Light core; heavy dependencies behind optional extras | Accepted | 2026-11-10 |
| 008 | The public demo stores no visitor data on the server | Accepted | 2026-11-10 |
| 009 | Threads, not asyncio | Accepted | 2026-11-10 |
| 010 | `FREE_ONLY` semantics, and no silent provider or model substitution | Accepted | 2026-11-10 |
| 011 | Numbers are computed by code, never by the model | Accepted | 2026-11-10 |
| 012 | Consolidate 37 requested agents into 28; build 11 in wave 1 | Accepted | 2026-11-10 |
| 013 | Revised session order | Accepted | 2026-11-10 |
| 014 | Keep v1 (Part I) and the target design (Part II) in one architecture document during migration | Accepted | 2026-11-10 |
| 015 | Workflow definitions in JSON plus Python templates; no YAML dependency | Accepted | 2026-11-10 |
| 016 | Python floor stays at 3.10 | Accepted | 2026-11-10 |
| 017 | Live LLM calls are never part of mandatory CI | Accepted | 2026-11-10 |
| 018 | UI studios are workflow templates over shared views | Accepted | 2026-11-10 |
| 019 | Groq, not Gemini, is the default provider for the public live mode | Accepted | 2026-11-10 |
| 020 | The v1 `LLMClient` facade is outside `FREE_ONLY` until the Session 2 migration | Superseded by 023 | 2026-11-10 |
| 021 | Pin `openai<3` until a deliberate migration to the 3.x SDK | Accepted | 2026-11-10 |
| 022 | Engine semantics left open by the design (ordering, accounting, review, pausing) | Accepted | 2026-11-10 |
| 023 | The v1 `CampaignWorkflow` runs on the engine; `FREE_ONLY` now covers v1 | Accepted | 2026-11-10 |
| 024 | CLI run storage and exit codes | Accepted | 2026-11-10 |
| 025 | Keep `openai<3` after evaluation; widen only after a live smoke test | Accepted | 2026-11-10 |
| 026 | Agent-library design: digests, contracts as code, critic capping, code-rendered report | Accepted | 2026-11-10 |
| 027 | Platform character limits are code configuration (hard versus recommended) | Accepted | 2026-11-10 |
| 028 | Groq default model: qwen/qwen3.8-27b (llama-3.1-8b-instant retired) | Accepted | 2026-11-10 |
| 029 | Research tools: search-only, citations bound to retrieved evidence | Accepted | 2026-11-10 |
| 030 | The free-tier operating envelope: output caps, truncation recovery, generous step timeouts | Accepted | 2026-11-10 |

---

## ADR-001 Evolve v1.1 in place

- **Context.** v1.1 is healthy: every gate passes, it has 82 tests, 94% branch coverage, and accurate documentation (see [PROJECT_AUDIT.md](PROJECT_AUDIT.md)). Rewriting it would throw away proven resilience code and the regression suite.
- **Decision.** Keep the `src/campaign_forge` layout, the v1 public API, the CLI contract and the output format. Build the 2.0 engine alongside them, then turn `CampaignWorkflow` into a facade over the engine.
- **Consequences.**
  - The 82 existing tests must keep passing; any intentional change to a test is recorded in [SESSION_LOG.md](SESSION_LOG.md).
  - Some compatibility code (adapters) is carried until the next major version.

## ADR-002 Deterministic workflow graph and a constrained planner

- **Context.** Open-ended multi-agent conversations are hard to test, can loop and burn tokens, and do not fit free-tier quotas.
- **Decision.**
  - Workflows are validated DAGs.
  - The only loops are revision and repair cycles with hard caps.
  - The planner (rule-based by default; LLM optional) can only select a registered template, toggle its declared optional steps and set declared parameters. Its proposal is validated against the registry, and the default template is used if the proposal is rejected.
- **Consequences.**
  - Runs are reproducible and testable offline.
  - Planning is less "creative" than free-form autonomy. That is intentional.

## ADR-003 One OpenAI-compatible provider plus vendor profiles

- **Context.** All four target vendors (Groq, Gemini, Mistral, Cloudflare Workers AI) expose OpenAI-compatible chat endpoints (see [FREE_TIER_POLICY.md](FREE_TIER_POLICY.md)), and v1 already has a hardened OpenAI-compatible client.
- **Decision.**
  - Generalise `LLMClient` into an `OpenAICompatibleProvider`.
  - Capture vendor differences in data-only `ProviderProfile`s: base URL, key variable, model aliases, JSON support level, rate-limit and quota message patterns, free-tier flag and data-use notice.
  - Add a native SDK only if a required feature is missing from a vendor's compatibility endpoint, and record that as a new ADR.
- **Consequences.**
  - No new dependencies, and one well-tested transport.
  - Vendor quirks (for example Gemini's `json_schema` limitations) are handled in profiles and covered by contract tests.

## ADR-004 Typed artifacts with claim-level provenance

- **Context.** v1 passes positional arguments between agents and does not label assumptions. 2.0 needs pluggable agents and honest research.
- **Decision.**
  - Agents exchange only `Artifact`s with a namespaced, versioned `kind` and a Pydantic payload.
  - Claims carry a `basis` (user-provided, external source, stored memory, model assumption, derived).
  - External claims must cite evidence IDs created by tools.
- **Consequences.**
  - Agents are replaceable without touching the orchestrator.
  - A fabricated citation fails validation.
  - Agents carry slightly more schema code.

## ADR-005 Engine-driven research loop

- **Context.** Native function calling differs across free-tier models and is hard to bound.
- **Decision.**
  1. The research agent emits a structured `ResearchPlan`.
  2. The engine executes the allowlisted tools.
  3. The agent synthesises from the resulting evidence.
  4. The number of rounds is bounded (default: 1).
- **Consequences.**
  - Works on any model that can produce JSON.
  - Easy to test with mocked tools.
  - Fewer adaptive multi-hop searches.

## ADR-006 Streamlit calls the core in-process

- **Context.** Free Streamlit hosting runs one app process, and running a second server there is not supported.
- **Decision.**
  - The Streamlit app imports the library directly.
  - FastAPI is a separate optional service for local or Docker use.
  - Both use the same core, and neither contains business logic.
- **Consequences.**
  - The free public demo depends only on Streamlit Community Cloud.
  - The API is demonstrable locally, but is not part of the hosted demo.

## ADR-007 Dependency policy

- **Decision.** The core keeps the current 5 runtime dependencies, plus an explicit `httpx`. Everything else lives behind extras: `[ui]`, `[api]`, `[db]`, `[research]`, `[otel]`, `[all]`. Each new dependency is justified in [TECH_STACK.md](TECH_STACK.md) before it is added.
- **Consequences.** Library users get a small install. The UI and API live in extras.

## ADR-008 No server-side visitor data in the public demo

- **Context.**
  - The Streamlit Cloud filesystem is not persistent.
  - Some free LLM tiers allow providers to use prompts to improve their products.
  - Storing visitor data creates privacy obligations.
- **Decision.** Public demo state lives in the browser session only. Persistence (SQLite or Supabase) is for local and self-hosted deployments.
- **Consequences.**
  - The demo's "history" lasts only as long as the browser session.
  - Brand Memory is demonstrated with session data or sample data.

## ADR-009 Threads, not asyncio

- **Decision.** Keep the synchronous transport with thread pools, both across campaigns (v1) and across independent steps (2.0).
- **Rationale.**
  - The work is I/O-bound.
  - The `openai` sync client is thread-safe.
  - Streamlit is synchronous.
- **Consequences.** An async client stays a possible later optimisation, not a requirement.

## ADR-010 `FREE_ONLY` and no silent substitution

- **Decision.**
  - `FREE_ONLY=true` by default. Only profiles and models marked free-eligible can run.
  - A fallback provider is used only when explicitly listed, and the provider and model that actually ran are recorded on every artifact and shown in the UI.
  - The documentation states clearly that Campaign Forge cannot see account billing, and advises using keys from accounts without billing attached.
- **Consequences.** A quota error pauses a run instead of quietly moving it to another model.

## ADR-011 Numbers are computed by code

- **Decision.** The following are computed in Python, never by the model:
  - budget allocations
  - character-limit checks
  - dates and calendars
  - sample sizes
  - performance ratios
  - ICE scores
  - SEO on-page measurements

  Models supply qualitative judgments and explanations.
- **Consequences.** Analytics outputs are verifiable and testable.

## ADR-012 Agent catalog consolidation (Proposed)

- **Context.** The master prompt lists 37 agent names, and several overlap: CopywriterAgent / AdCopyAgent / LandingPageAgent, ArtDirectorAgent / VisualPromptAgent, ContentQualityAgent / CreativeReviewAgent / Critic. The prompt also forbids empty or duplicated agents.
- **Decision.**
  - Build 28 agents, each with a distinct output contract. The full mapping is in [AGENT_CATALOG.md](AGENT_CATALOG.md).
  - Wave 1 (Session 3, Milestone 1) has 11 agents.
- **Deviation.** The master prompt's agent names are kept as aliases in the catalog, but not as separate classes. **Accepted by the owner on 2026-11-10** (instruction to continue with the plan as proposed).

## ADR-013 Revised session order (Proposed)

- **Context.** The master prompt orders Memory (S4) before Tools (S5), and puts Security (S9) and Evaluation (S8) near the end.
- **Decision.**
  - Run Tools and Research as Session 4, then Memory as Session 5. Evidence-backed research is the largest credibility gap, and wave-1 research agents need it.
  - Build security primitives (untrusted-content framing, redaction, budgets) into Session 1, and eval coverage into every agent session. Sessions 8 and 9 deepen these; they do not introduce them.
  - Deploy demo mode as early as Session 6, to surface hosting constraints before Session 10.
- **Deviation.** Session numbering stays 0-10, but the content of Sessions 4 and 5 is swapped. **Accepted by the owner on 2026-11-10** (instruction to continue with the plan as proposed). See [ROADMAP.md](ROADMAP.md).

## ADR-014 One architecture document during migration

- **Decision.** [ARCHITECTURE.md](ARCHITECTURE.md) keeps the accurate v1 design (Part I) and adds the target design (Part II). Part I is folded into Part II as components land.
- **Consequences.** The README's architecture link stays truthful at every point of the migration.

## ADR-015 JSON workflow definitions

- **Decision.**
  - Built-in templates are Python.
  - User-defined workflows are JSON, validated by Pydantic.
  - No PyYAML in the core.
- **Consequences.** Definitions are machine-edited by the Workflow Builder rather than hand-written YAML.

## ADR-016 Python floor 3.10

- **Decision.** Keep `requires-python >= 3.10`, which matches the current venv and CI. Add 3.13 to CI in Session 1.
- **Context.** Streamlit Community Cloud supports every Python version that still receives security updates.

## ADR-017 No live LLM calls in mandatory CI

- **Decision.** CI uses `FakeProvider` and the offline eval only. Live evals are manual, or run as an opt-in scheduled workflow using repository secrets. Their results are recorded with date, provider and model, and never invented.

## ADR-018 Studios are templates over shared views (Proposed)

- **Context.** The master prompt lists 16 UI pages. Seven of them are "studios" that would duplicate the same new-run, progress and output components.
- **Decision.**
  - Keep all 16 navigation entries.
  - Implement the studios as thin pages that pre-select workflow templates and render shared views.
  - The real view set is about 9: Dashboard, New Run, Run Inspector, History, Brand Memory, Agents, Workflow Builder, Evaluation Lab, and Settings and Health.
- **Deviation.** Same navigation as requested, less duplicated code. **Accepted by the owner on 2026-11-10** (instruction to continue with the plan as proposed).

## ADR-019 Groq as the public live-mode default (Proposed)

- **Context.** The master prompt names Gemini as the primary LLM. Google's own terms, checked on 2026-11-10, say two things:
  - Free-tier data is used to improve Google products and may be reviewed by humans.
  - Only paid services may be offered to users in the EEA, Switzerland and the UK.

  A public demo serves anyone, from anywhere.
- **Decision.**
  - Groq is the default provider for the public live mode. It is already integrated, needs no card, and no regional restriction was found.
  - Gemini, Mistral and Cloudflare Workers AI are first-class configurable providers, for local use and for visitors' own keys.
- **Deviation.** **Accepted by the owner on 2026-11-10** (instruction to continue with the plan as proposed). Gemini remains fully supported; only the public default changes.

## ADR-020 The v1 `LLMClient` facade is outside `FREE_ONLY` until Session 2

- **Context.** `FREE_ONLY` is enforced by the 2.0 `ProviderRouter`. The v1 `LLMClient` still calls whatever `CF_BASE_URL` points at, which `.env.example` documents for OpenAI. Applying the policy there in Session 1 would break existing v1 setups mid-migration, before the engine that replaces them exists.
- **Decision.** In Session 1 the facade keeps v1 behaviour exactly. In Session 2, `CampaignWorkflow` moves onto the engine and router, so `FREE_ONLY` applies to the v1 entry points too. A paid endpoint will then need `CF_FREE_ONLY=false`, and that change is announced in `CHANGELOG.md`.
- **Consequences.** Until Session 2, the v1 CLI path can reach a paid endpoint if it is configured to. This is documented in `llm.py` and in [FREE_TIER_POLICY.md](FREE_TIER_POLICY.md).

## ADR-021 Pin `openai<3` until a deliberate migration

- **Context.** v1.1 declared `openai>=1.30` with no upper bound. Session 1 ran the suite in a fresh Python 3.11 environment, which resolved `openai` 3.49.0. That major version replaced its `httpx` transport with a new `httpx2` package, and test collection failed because the tests build SDK errors from `httpx` objects (defect D10). Any fresh install, including CI, picks up the same version. The package code still type-checks against 3.x, but it has never been tested against it.
- **Decision.** Constrain the dependency to `openai>=1.30,<3` in `pyproject.toml` and `requirements.txt`. With the constraint, a fresh install resolves 2.54.0, and the full suite, mypy strict and the offline eval pass on Python 3.11 (and on 3.10 with 2.44.0).
- **Consequences.** We stay on the 2.x line until a planned migration (task S2-17). That migration must cover the transport change, the error constructors and every provider contract test.

## ADR-022 Engine semantics left open by the design

- **Context.** The T5 and T6 design did not settle several details that matter for correctness and for honest reporting.
- **Decision.**
  - **`after` versus inputs.** `after` is ordering only: wait until the named steps finish, whatever their outcome. An input binding is a data dependency. A required input whose source did not succeed skips the step, with reason `dependency`. An optional input from such a source is left out.
  - **Call accounting.** `llm_calls` counts every admitted call, including calls the provider refused. They still used up provider capacity.
  - **Review.** If a reviewed step's score is still below the threshold after `max_revisions`, the output is accepted with a note and the score is recorded. A bounded loop must end, and quality is reported, not hidden.
  - **Pausing.** Quota exhaustion, a rate limit too long to wait for, and an exhausted run budget all pause the run. The affected step returns to pending, every finished step is kept, and each resume gets a fresh budget.
  - **Extra transitions.** `FAILED -> RUNNING` (retry) and `FAILED -> CANCELLED` (abandon) are allowed in addition to the T6 diagram.
  - **Timeouts.** A step that times out is abandoned. Its cancel token is set, so its thread stops at its next model call.
- **Consequences.** These rules are encoded in `campaign_forge.orchestration` and tested in `tests/test_engine.py`.

## ADR-023 The v1 `CampaignWorkflow` runs on the engine (supersedes ADR-020)

- **Decision.**
  - The four v1 agents are wrapped as registered `legacy.*` agents, and `CampaignWorkflow` runs the `classic-campaign` template on the engine.
  - An injected v1 client, such as the tests' `FakeLLM` or an `LLMClient`, is adapted with `LegacyClientProvider`.
  - The v1 override keys (`manager`, `research`, ...) also match the `legacy.*` ids.
- **Consequences.**
  - The v1 signature, results and exception types are unchanged, and all 82 v1 test functions pass unmodified.
  - **Behaviour change:** `FREE_ONLY` now applies to `campaign-forge run` and `batch`. A paid endpoint such as OpenAI needs `CF_FREE_ONLY=false`. This is recorded in `CHANGELOG.md`.
  - Provider overrides in `CF_AGENT_OVERRIDES` now work for the v1 agents too.

## ADR-024 CLI run storage and exit codes

- **Decision.**
  - `campaign-forge execute` stores runs as JSON files under `CF_RUNS_DIR` (default `outputs/runs`, which is git-ignored with `outputs/`), checkpointed after every step.
  - Exit codes: `0` completed, `1` failed or cancelled, `2` configuration or validation error (as in v1), `3` needs action (paused, or waiting for approval).
  - The v1 `run` and `batch` commands keep no run history.
- **Consequences.** Paused or approval-gated runs survive the process and can be continued with `campaign-forge runs resume` or `runs approve`.

## ADR-025 Keep `openai<3` after evaluation; widen only after a live smoke test

- **Context.** Task S2-17 evaluated the 3.x SDK (3.49.0) in a fresh Python 3.11 environment:
  - the full test suite and mypy strict pass;
  - the real 3.x client constructs;
  - genuine `httpx2` rate-limit, timeout and auth errors map correctly through `OpenAICompatibleProvider` (including the `retry-after-ms` header).

  Nothing has been verified against a live API on 3.x, because live calls are not yet approved.
- **Decision.**
  - Keep `openai>=1.30,<3` for now.
  - The new provider tests import the SDK's HTTP module through `tests/sdk_http.py`, so they run on both majors (verified on 3.49 with `httpx` uninstalled).
  - Widen the pin to `<4` once `python scripts/provider_smoke.py --live` passes on 3.x. At that point, also switch the v1 test `tests/test_llm.py` to `tests/sdk_http.py`.
- **Consequences.** Migrating is a two-line change backed by evidence, not a guess.

## ADR-026 Agent-library design (wave 1)

- **Context.** Wave 1 has to produce trustworthy, checkable output on small free-tier models, within tight tokens-per-minute limits.
- **Decision.**
  - **Contracts.** Every agent returns a Pydantic contract whose validators hold the content rules (counts, limits, single-line prompts, calendar bounds). A violation is fed back through the bounded repair loop.
  - **Digests.** Upstream artifacts reach downstream prompts as compact digests, not full JSON.
  - **The critic** applies the deterministic checks first and caps its score at 0.40 on any critical failure. The model scores a kind-specific rubric 1-5, and code maps the scores to 0-1.
  - **The reporter** asks the model only for a six-section Markdown narrative, validated with one repair. Code appends the exact deliverables, the model assumptions, and what was not produced. The model never re-types the deliverables.
  - **`full-campaign`** reviews the core copy and the ads (threshold 0.7, one revision). Ads, social, email and visuals are optional steps, so one of them failing does not sink the campaign. The report lists what is missing.
  - **The default planner template** is now `full-campaign`. The v1 API keeps `classic-campaign`.
  - **CLI demo.** With `CF_PROVIDER=fake`, the CLI answers every agent with the deterministic offline responder and labels the output SIMULATED.
- **Consequences.**
  - Output is verifiable and cheap: an offline `full-campaign` run is 12 calls.
  - Live quality must still be measured (S3-05).

## ADR-027 Platform character limits are code configuration

- **Decision.** `campaign_forge.agents.schemas.PLATFORM_LIMITS` holds per-platform limits, each marked "hard" or "recommended":
  - hard: Google Search headline 30 and description 90; X post 280;
  - recommended: Meta, LinkedIn and TikTok guidance.

  Hard limits are enforced by the contract. Recommended limits only produce warnings on the artifact.
- **Consequences.**
  - Platforms change their rules, so the values are documented as commonly published limits, to verify before publishing.
  - Updating one is a one-line change, covered by tests.

## ADR-028 Groq default model: `qwen/qwen3.8-27b`

- **Context.** The first live provider check (2026-11-10) found that Groq has retired `llama-3.1-8b-instant`, the default since v1. Every live run failed with HTTP 404 (defect D11). Offline tests could not catch this.
- **Evidence.** A live probe of the remaining text models showed that all of them support JSON mode, report usage and return `finish_reason: stop`. The response headers show the free-tier limits are 8,000 tokens per minute and 1,000 requests per day per model. The `gpt-oss` models return hidden reasoning that counts as completion tokens; `qwen/qwen3.8-27b` does not and used about a quarter of the tokens for the same answer.
- **Decision.**
  - `DEFAULT_MODEL` and the Groq profile default are now `qwen/qwen3.8-27b`.
  - Groq aliases: `small` and `standard` map to `qwen/qwen3.8-27b`; `strong` maps to `openai/gpt-oss-120b`.
  - Provider profiles can attach per-model request parameters. `gpt-oss` models get `reasoning_effort: low`.
  - A 404 `model_not_found` error now tells the user how to list the available models.
- **Consequences.** `scripts/provider_smoke.py --live --models` is the routine check whenever a provider changes its catalog.

## ADR-029 Research tools: search-only, citations bound to retrieved evidence

- **Decision.**
  - **No model-chosen URLs.** Agents may only send model-proposed search *queries* to allowlisted search tools: Wikipedia (keyless, the default) and Tavily (when `TAVILY_API_KEY` is set). A model can never make the system fetch a URL.
  - **User URLs only.** URL fetching happens only for URLs the user supplies with `--sources`, before a run, through `SafeFetcher`. It re-checks every redirect for SSRF, honours robots.txt (RFC 9309 semantics), and caps size, time and content type.
  - **Citations are validated.** Citations are evidence ids. The output contract validates every cited id against the evidence actually retrieved in this run, so an invented citation is a repair error. Artifacts embed the evidence they cite, so they can be verified on their own; the `citations_verified` check does this.
  - **Named competitors only.** Competitor research may name only competitors that appear in the brief, the user's sources or the evidence.
  - **Fact checking.** The fact checker judges each externally sourced claim against its cited excerpts. The model returns only per-index verdicts, and code maps them back to the claims.
  - **Fallback.** With no evidence available, research agents work in labeled assumptions mode, where external claims are impossible.
- **Consequences.**
  - Research costs one extra planning call per research agent when tools are configured.
  - The public demo gets research without exposing a URL-fetching surface to visitors' prompts.
  - DNS rebinding between the URL check and the connection is a documented residual risk for user-supplied URLs (SECURITY.md).

## ADR-030 The free-tier operating envelope

- **Context.** The first live evals on Groq's free tier (2026-11-10) failed completely: v1 completed 0 of 10 briefs and `full-campaign` 0 of 2. The causes were measured, not guessed:
  - **Output-token cap.** `qwen/qwen3.8-27b` allows 1,000 output tokens per minute, and a request's `max_tokens` counts against it up front. A request above 1,000 is rejected outright as "Request too large". Throughput is therefore roughly one 800-token call per minute.
  - **Truncation.** Responses truncated at 800 tokens failed whole steps.
  - **Step timeouts.** Each 429 meant a wait of about 45 s, which pushed steps past their 120 s limit.
- **Decision.**
  1. **Clamp to the cap.** Provider profiles hold per-model `max_output_tokens` caps (Groq `qwen/qwen3.8`: 1,000), and requests are clamped to them.
  2. **Treat "Request too large" as a permanent error.** It is a `ProviderBadRequestError` that fails fast with advice, not a rate limit that retries and then pauses.
  3. **Recover from truncation once.** A truncated answer gets one concise re-ask. The brevity instruction is added to the last user message, so v1 clients still see two messages. The re-ask counts as a repair and is budgeted.
  4. **Generous step timeouts.** The default step timeout is now 900 s, and template wall-clock budgets were raised. Waiting out a rate limit is normal, not a hang.
  5. **Shorter long-output prompts.** The social calendar, email sequence and report narrative prompts now ask for less.
- **Consequences.**
  - A free-tier full campaign is slow (roughly 15 calls at about one per minute) but bounded.
  - Higher-throughput tiers, or other providers, simply skip the waits.

## ADR-031 Memory on stdlib SQLite; Postgres deferred until it can be tested

- **Context.** Session 5 needs brand profiles, campaign history and notes that persist between runs, are searchable, belong to exactly one tenant, and can be deleted completely. The roadmap names Supabase Postgres as the hosted option. No Postgres database is available to test against, and a Supabase project needs the owner's account.
- **Decision.**
  - **Storage.** `campaign_forge.memory` (layer 2) defines a scoped `MemoryStore` protocol. `SqliteMemoryStore` implements it with the stdlib `sqlite3` module, so there is no new dependency and no server. A file database uses WAL. `":memory:"` gives a store that lives only as long as the object, for tests and the public demo (ADR-008).
  - **Scope on every call.** Every query filters on `org_id` and `user_id`. No method reads across scopes. `delete_scope` removes a tenant's brands, documents and index entries in one transaction.
  - **Redaction before storage.** Every string written is redacted first: brand fields, titles, text and metadata values. That covers configured secret values and key-shaped strings. A test reads every table, the FTS shadow tables included, and asserts that no secret is present.
  - **Search.** FTS5 with BM25 ranking, title weighted above body, when the SQLite build has FTS5. Otherwise a LIKE scan ranked by term counts. Queries are reduced to quoted word tokens, so FTS5 syntax and LIKE wildcards in a query are inert. Writes made without FTS5 mark the index stale, and the next FTS5-capable open rebuilds it.
  - **Limits.** Per scope: 100 brands and 2,000 documents of at most 20,000 characters each.
  - **Runs.** `SqliteRunStore` (`CF_RUN_STORE=sqlite`) can share the memory file. It passes the same contract tests as the in-memory and JSON-file stores. JSON files stay the default.
  - **History.** A completed CLI run is remembered as a `campaign` document: the brief and a digest of each output, keyed by run id, so remembering it again replaces it. `CF_MEMORY_HISTORY=false` turns this off.
  - **Memory is data.** A brand profile reaches prompts only inside untrusted-data framing (label `brand profile`), like every other input.
  - **Postgres is deferred.** It will not ship until it can be tested against a real database. The protocol is the seam it will implement.
- **Consequences.**
  - Local and single-instance deployments get durable, searchable memory with zero setup.
  - Multi-instance hosting needs the Postgres store first.
  - LIKE-fallback search matches substrings, so it is less precise than FTS5. A test runs every store behaviour on both paths.

## ADR-032 Free-tier throughput: serialised requests, rate-limit auto-resume, long outputs on the strong model

- **Context.** The live runs after ADR-030 (2026-11-10) found three more limits:
  - **Parallel requests.** Two requests in flight under the 1,000 output-token/minute cap displaced each other until both exhausted their retries, and the run paused.
  - **Daily quota.** `qwen/qwen3.8-27b` also has a rolling daily quota of 200,000 tokens (from the 429 message). A day of development runs used it up: "Used 198869". Its retry hint (about 77 s) frees room for about one call, so resuming crawled.
  - **Long outputs.** The v1 manager's final brief overflowed 1,000 tokens even after the concise re-ask (D17). A live probe of `openai/gpt-oss-120b` (the `strong` class) returned 2,500 completion tokens in one request, with 11 reasoning tokens at `reasoning_effort: low`, at 8,000 tokens/minute and 1,000 requests/day. No 1,000-token output cap was hit.
- **Decision.**
  1. **Concurrency limit (D16).** Provider profiles carry `max_concurrency`, which bounds requests in flight (each attempt, not the backoff sleeps). Groq is 1. `CF_PROVIDER_CONCURRENCY` overrides it per provider, for example `{"groq": 4}` on a paid tier.
  2. **Bounded auto-resume (D18).** `WorkflowEngine.run_until_settled` resumes runs paused by a *rate limit*, waiting out the provider's hint. It stops after `max_resumes`, on a hint longer than `max_wait_s`, or on any other pause reason. Exhausted quotas and budget pauses are never auto-resumed. The CLI exposes it as `execute --auto-resume N`, and the live eval uses 3.
  3. **Long outputs on the strong class (D17).** `legacy.manager` (`max_tokens` 2,000) and `campaign_reporter` declare `ModelClass.STRONG`. On Groq that is `openai/gpt-oss-120b`, which is also a separate per-model daily quota, so a run's load is spread over two quotas. An explicit `CF_MODEL` still wins for every agent (v1 behaviour).
- **Consequences.**
  - A Groq free-tier day supports a handful of live full campaigns, not dozens.
  - The live acceptance runs (S1-02, S3-05) wait for the daily quota to refill, and are not marked passed until they do.
