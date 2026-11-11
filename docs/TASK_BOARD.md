# Task Board

Granular tasks for each session.

- **Status markers:**
  - `[x]` done (verified)
  - `[ ]` to do
  - `[~]` in progress
  - `[-]` dropped (the reason is recorded in [SESSION_LOG.md](SESSION_LOG.md))
- **Level of detail:** tasks for the next two sessions are detailed. Later sessions are refined at their start, against the actual repository state.

## Session 0 - Audit and architectural planning

- [x] S0-01 Inspect the full source tree, tests, evals, CI, docs and packaging
- [x] S0-02 Measure the baseline: lint, format, types, tests, coverage, offline eval, build, CLI
- [x] S0-03 Reproduce each suspected defect with an offline probe (D1-D7 reproduced; D8 recorded as a risk; D9 found by code reading)
- [x] S0-04 Validate the master prompt's list of 14 known weaknesses against the code
- [x] S0-05 Verify the free-tier terms of LLM providers, hosting and research sources (official sources, dated)
- [x] S0-06 Design the target architecture (ARCHITECTURE.md Part II, with Mermaid diagrams)
- [x] S0-07 Write the ADRs, the agent catalog, the security model, the tech stack, the free-tier policy and the roadmap
- [x] S0-08 Create the progress-tracking files (BUILD_STATUS, SESSION_LOG, TASK_BOARD)
- [x] S0-09 Owner review: the plan and ADR-012, 013, 018 and 019 were approved (instruction to continue), 2026-11-10

## Session 1 - Core foundation, providers, defect fixes

### Baseline

- [~] S1-01 Provider facts were verified on 2026-11-10 (Session 0, same day). Resolving model aliases from live `/models` needs keys and owner approval: run `python scripts/provider_smoke.py --live --models`
- [x] S1-02 Run a live eval baseline on Groq; record the date, model, pass rate, duration and any 429s. **Passed on 2026-11-11 (run 5): 2/2**, after runs 1-4 found and fixed D11-D19 (BUILD_STATUS.md, "Live runs").

### Defect fixes, each with a regression test

- [x] S1-03 D1: validate CLI overrides (re-validate `Settings`; argparse range checks); map invalid values to exit code 2
- [x] S1-04 D2: build `Settings` explicitly for long-lived contexts; keep cached `get_settings()` for CLI compatibility; document both
- [x] S1-05 D3: treat an empty Art Director result as a step failure, with one repair attempt
- [x] S1-06 D4: check the required sections of the Manager output, with one repair attempt
- [x] S1-07 D5: guard the `on_result` callback in `run_batch`; correct its docstring
- [x] S1-08 D7: strengthen the eval grounding check (ignore stop-words; use the longest distinctive token)
- [x] S1-09 D8: per-agent `max_tokens`

### Domain and core

- [x] S1-10 `domain/`: `Artifact`, `Claim`, `Provenance`, `Evidence`, `Usage`, `Scope` (Pydantic, frozen where appropriate)
- [x] S1-11 `core/agent.py`: `AgentSpec`, `Agent` protocol, `AgentOutput`
- [x] S1-12 `core/registry.py`: register, lookup, list, enable/disable; entry-point group `campaign_forge.agents`; duplicate-ID protection
- [x] S1-13 `core/budget.py`: thread-safe `Budget` (calls, tokens, wall time, revisions, repairs) with a clear exhaustion error
- [x] S1-14 `core/context.py` and `core/events.py`: `RunContext` and typed events

### Providers

- [x] S1-15 `providers/base.py`: `Provider` protocol, request and response models, normalised errors
- [x] S1-16 `providers/openai_compat.py`: refactor of `LLMClient` transport; usage capture; quota-versus-rate-limit classification; multi-message support
- [x] S1-17 `providers/profiles.py`: Groq, Gemini, Mistral, Cloudflare (data only; `free_tier` flag; data-use notice)
- [x] S1-18 `providers/fake.py`: deterministic `FakeProvider` (routing by agent ID; scripted failures)
- [x] S1-19 `providers/router.py`: configuration precedence, `FREE_ONLY` guard, explicit fallback recorded on output
- [x] S1-20 Keep `LLMClient` as a facade over the new provider; all v1 LLM tests pass unchanged
- [x] S1-21 Provider contract test suite (offline), plus a documented manual live smoke script

### Security primitives

- [x] S1-22 `security/untrusted.py`: framing of untrusted content; apply it to `CampaignBrief.as_prompt_context` consumers (D6, first step)
- [x] S1-23 `security/redaction.py`: log filter that redacts key patterns and configured key values
- [x] S1-24 Architecture test: downward-only imports; no `eval`, `exec` or `subprocess` in the package

### Wrap-up

- [x] S1-25 Add Python 3.13 to the CI matrix
- [x] S1-26 Update the docs (ARCHITECTURE status markers, CHANGELOG `Unreleased`, BUILD_STATUS, SESSION_LOG)

## Session 2 - Orchestration engine

- [x] S2-01 `WorkflowDefinition` model and JSON (de)serialisation
- [x] S2-02 Validator: agents registered and enabled, acyclic graph, satisfiable inputs, tool allowlists, budget ceilings
- [x] S2-03 Run and step state models with the state machine and allowed transitions
- [x] S2-04 Engine core: ready-set scheduling, parallel dispatch, failure isolation, partial results
- [x] S2-05 Declarative `when` conditions (no `eval`)
- [x] S2-06 Bounded review loop (critic hook) and repair loop
- [x] S2-07 Approval gates: `WAITING_FOR_APPROVAL` and the approve/reject/edit round trip
- [x] S2-08 Cooperative cancellation and per-step timeouts
- [x] S2-09 Budget enforcement across parallel steps
- [x] S2-10 Checkpoint stores (in-memory, JSON file); idempotent resume by step ID and input fingerprint
- [x] S2-11 Pause on quota exhaustion with a `resume_after` hint
- [x] S2-12 Rule-based planner; optional constrained LLM planner with validation and fallback
- [x] S2-13 Wrap the v1 agents as registry agents (`legacy.*`); `classic-campaign` template
- [x] S2-14 Rebuild `CampaignWorkflow.run` and `run_batch` on the engine, with incremental batch saves (D9)
- [x] S2-15 CLI: `agents`, `workflows`, `execute`, `runs show|resume|approve|cancel`
- [~] S2-16 Tests for every engine rule (done; coverage 98%). Tagging `2.0.0a1` waits for commit authorisation
- [x] S2-17 Evaluated `openai` 3.49.0: the suite, mypy and the offline SDK error mapping pass. The pin is kept until a live smoke test (ADR-025); the tests are now transport-agnostic

## Session 3 - Agent library, wave 1

- [x] S3-01 `campaign_forge.evaluation`: deterministic checks, rubrics, and an offline responder (shared by the critic, evals, tests and the CLI demo)
- [x] S3-02 Eleven agents per [AGENT_CATALOG.md](AGENT_CATALOG.md), each with spec, prompt version, output contract, validation, tests and rubric
- [x] S3-03 Templates: `full-campaign`, `ad-pack`, `social-calendar`, `email-sequence`
- [x] S3-04 Eval harness `--workflow` mode (per-output deterministic checks, call and token metrics), with a CI step for `full-campaign`
- [x] S3-05 Live `full-campaign` run on the Groq free tier; record the measured usage; tag `2.0.0b1`. **Passed on 2026-11-11 (run 5): every check green, 19 calls, 27,129 tokens, 375 s.**

## Session 4 - Tools and evidence-backed research

- [x] S4-01 `security/url_safety.py`: http(s) only, no embedded credentials, ports 80/443, refuse private, loopback, link-local, multicast, reserved and metadata addresses (IPv4 and IPv6, including IPv4-mapped)
- [x] S4-02 `tools/`: tool protocol, per-agent allowlist (`AgentSpec.allowed_tools`), tool-call budget, run-level evidence store
- [x] S4-03 Safe URL fetcher: re-check every redirect, `robots.txt` (RFC 9309 semantics), size, time and content-type caps, HTML to text with the stdlib
- [x] S4-04 User-supplied sources (text, Markdown, CSV, JSON) loaded as evidence, with size caps
- [x] S4-05 Wikipedia adapter (policy-compliant User-Agent, no key) and optional Tavily adapter (`TAVILY_API_KEY`)
- [x] S4-06 Citations: artifacts carry the evidence they cite; the contract rejects unknown evidence ids, so invented citations trigger repair; the checks flag fabricated citations
- [x] S4-07 Evidence mode for `audience_research` and `market_research` (plan, retrieve, synthesise), falling back to labeled assumptions mode
- [x] S4-08 New agents `competitor_research` (only competitors named in the brief or the evidence) and `fact_checker`
- [x] S4-09 Optional workflow inputs (`sources`); `competitor-report` template; research templates accept user sources
- [x] S4-10 CLI `--sources`; the reporter renders a code-built Sources section
- [x] S4-11 Tests: SSRF, robots, caps, fabricated citations, evidence and assumptions modes, new agents; offline responder; evals
- [x] S4-12 Docs: ARCHITECTURE status, SECURITY (T2-T4), AGENT_CATALOG, FREE_TIER_POLICY, ADRs, CHANGELOG, BUILD_STATUS, SESSION_LOG

## Session 5 - Memory and persistence

- [x] S5-01 `domain/brand.py`: `BrandProfile` (`brand.profile@1`): voice, tone, do/don't, preferred and banned terms, visual style
- [x] S5-02 `memory/`: scoped store protocol and a SQLite implementation (stdlib `sqlite3`): brands, documents (campaign history and notes), FTS5 search with a LIKE fallback, and `delete_scope`
- [x] S5-03 Secrets never reach storage: everything written is redacted (key-shaped strings and configured secret values)
- [x] S5-04 SQLite `RunStore` (`CF_RUN_STORE=sqlite`), passing the same contract tests as the JSON-file store
- [x] S5-05 Agents `brand_strategist` (`brand.profile@1`) and `brand_consistency` (code-checked terms, plus a model tone score); `copywriter` accepts an optional brand profile
- [x] S5-06 Templates: `brand-kit`; `full-campaign` gains an optional `brand` input and a conditional brand check (a required agent input may bind an optional workflow input only behind an `exists` condition)
- [x] S5-07 CLI: `memory brand save|list|show|delete`, `memory search`, `memory forget --yes`, `execute --brand`; completed runs are recorded as campaign history
- [x] S5-08 Retrieved memory is framed as data: brand profiles reach prompts only inside `brand profile` framing (writer, critic, brand check)
- [~] S5-09 Postgres (Supabase) backend: **deferred**. Blocker: no test database is available, and a Supabase project needs the owner's account. It will not ship untested (ADR-031).
- [x] S5-10 Tests: tenant isolation, deletion, secret redaction, FTS ranking and fallback, run-store parity, agents, templates, CLI
- [x] S5-11 Docs and ADR-031
- [x] S5-12 Free-tier fixes found by the live runs (ADR-032): serialised Groq requests (D16), the v1 manager and the reporter on the strong model (D17), and no auto-resume on an exhausted quota (D18)

## Session 6 - Streamlit application and early demo deployment

- [x] S6-01 `exports/`: Markdown, JSON and HTML downloads for a run. HTML comes from Markdown with raw HTML disabled, so model text cannot inject markup or scripts.
- [x] S6-02 Hidden reasoning never surfaces: `<think>` blocks are stripped from provider responses before parsing or storage
- [x] S6-03 `ui/` view models, with no Streamlit import, so they can be unit-tested:
  - session modes: demo (simulated, in-memory, nothing stored on the server, ADR-008) and local (your configured provider and stores);
  - plan preview;
  - background runs with progress;
  - DAG (Graphviz DOT), step rows, artifacts, evidence, usage, errors;
  - health without secrets;
  - the studio map.
- [x] S6-04 `app/streamlit_app.py`: the 16 navigation entries from ADR-018:
  - Dashboard, New Run, Run Inspector, History;
  - 7 studios;
  - Brand Memory, Agents, Workflow Builder, Evaluation Lab (a placeholder until S8);
  - Settings and Health.
- [x] S6-05 New Run: the brief form or a JSON upload, an optional brand and sources, a plan preview with optional-step toggles and budget, and an explicit "Approve and run"
- [x] S6-06 Run Inspector: DAG, step status, artifacts, evidence, usage, errors, approval gates and downloads; resume and cancel
- [x] S6-07 Brand Memory and History pages: save and delete brands, save a brand from a run, search campaign history, notes
- [x] S6-08 Workflow Builder: start from a template, toggle optional steps, set limits, validate, download JSON; upload a definition to validate or run
- [x] S6-09 Demo mode labeled SIMULATED everywhere. The app starts with no keys.
- [x] S6-10 Tests: view models and exports (unit), plus Streamlit `AppTest` smoke tests for every page and a full demo run (skipped when Streamlit is not installed; CI installs `[ui]`)
- [x] S6-11 Packaging: the `[ui]` extra; `app/requirements.txt` for Streamlit Community Cloud; `.streamlit/config.toml`
- [x] S6-12 Deploy demo mode to Streamlit Community Cloud: **live at https://campaign-forge.streamlit.app/** (deployed and smoke-tested 2026-11-11). **Needs the owner:** deploying requires their Streamlit account connected to GitHub. Until it is deployed and smoke-tested, nothing claims a live URL.
- [x] S6-13 Docs: README (running the app), ARCHITECTURE status, ADR, CHANGELOG, BUILD_STATUS, SESSION_LOG

## Session 7 - FastAPI service and developer experience

- [x] S7-01 `services/` (layer 5), one application layer shared by every interface:
  - `RunService` starts, tracks and controls runs (background threads, history, a cap on active runs);
  - `Platform` covers agents, workflows (built-in plus per-tenant custom), runs, providers and status.

  The app's session delegates to it, so background execution exists in one place.
- [x] S7-02 `api/` (layer 6): the 13 endpoints from the master prompt (`/health`, plus `/api/v1` agents, campaigns, workflows, executions, providers, system status), plus execution approval and a workflow list; typed request and response models
- [x] S7-03 API-key authentication (`X-API-Key`, `CF_API_KEYS`, constant-time comparison). Each key is its own tenant scope, so clients cannot see each other's runs. `/health` is open.
- [x] S7-04 Limits: requests per minute per key, run starts per minute per key, active background runs per instance. A limited request gets 429 with `Retry-After`.
- [x] S7-05 Errors: one JSON error shape, mapped from the package's exceptions (400/403/404/409/422/429/503). Unexpected errors return a generic 500 with no internals, and messages are redacted.
- [x] S7-06 Architecture rule: the API imports only the service layer and plain types, so no business logic lives in it
- [x] S7-07 Dockerfile (non-root, healthcheck) and `compose.yaml` (SQLite on a volume). A CI job builds the image and checks `/health` and an authenticated call in the container.
- [x] S7-08 The OpenAPI schema is published as `docs/openapi.json`; a test fails if it drifts. Plus `docs/API.md` and `examples/api_client.py`.
- [x] S7-09 Tests with `TestClient`: every endpoint, auth, tenant isolation, limits, errors, background runs to completion
- [x] S7-10 Docs: ADR-034, README, ARCHITECTURE status, SECURITY, CHANGELOG, BUILD_STATUS, SESSION_LOG

- [x] S7-11 D19 (found by live run 4): cap-aware routing sends long answers to the provider's strong model instead of truncating them (ADR-035)

## Session 8 - Agent library wave 2 and Evaluation Lab

- [x] S8-01 Wave-2 contracts (`agents/schemas_wave2.py`). Numbers are computed by code, never by the model:
  - ICE scores and ranks;
  - experiment sample sizes;
  - CTR, CVR, CPC, CPA and ROAS;
  - SEO and CRO check scores;
  - word counts and calendar weeks.
- [x] S8-02 Strategy and analytics agents:
  - `funnel_strategist`;
  - `growth_strategist` (ICE by code);
  - `experiment_designer` (sample size by code, baselines labeled as assumptions);
  - `performance_analyst` (metrics from user CSV only; refuses without data).
- [x] S8-03 Content agents:
  - `landing_page` (no fabricated testimonials);
  - `product_description` (length limits by code);
  - `repurposer` (from copy or the user's own text; platform limits by code);
  - `blog_writer` (outline first, then section by section).
- [x] S8-04 SEO agents:
  - `keyword_research` (volumes only from a user CSV, never invented);
  - `seo_content_planner`;
  - `seo_optimizer` (on-page checks by code, plus model recommendations).
- [x] S8-05 Quality agents:
  - `safety_reviewer` (rule flags by code; model flags kept only when they quote the content; no legal advice);
  - `cro_advisor` (checklist by code, plus recommendations).
- [x] S8-06 Templates: `seo-content-pack`, `performance-review`, `product-launch`, `landing-page`, `content-repurpose`, `ecommerce-campaign`, `b2b-campaign`. The SEO and Analytics studios open their templates.
- [x] S8-07 Offline responders for every new agent; the offline eval runs every template
- [x] S8-08 Evaluation Lab (`services/lab.py`):
  - runs a dataset through a workflow, offline or live;
  - reports success and failure rates, duration, calls, tokens, retries, scores and estimated cost;
  - records the date, mode, provider and models;
  - compares against a stored baseline and flags regressions.
- [x] S8-09 Datasets and baselines:
  - `evals/datasets/injection.json` (direct and indirect injection, with canaries);
  - offline regression baselines in `evals/baselines/`, checked in CI.
- [x] S8-10 Interfaces: `campaign-forge bench` CLI; the Evaluation Lab page in the app (replaces the placeholder)
- [x] S8-11 Benchmarks only from real runs: `docs/BENCHMARKS.md`, each run recorded with its date, provider and models
- [x] S8-12 Tests, docs (catalog, ADR, changelog, status), and release `2.0.0rc1`
- [x] S8-13 D20 (found by a live benchmark): the JSON run store waits out a brief Windows file lock on replace; the Lab records any crash as a failed case; reports name the models that answered

## Session 9 - Security hardening and live mode

- [ ] S9-01 Release bookkeeping: confirm the `v2.0.0rc1` tag (at `4c8b726`) and record it in the status docs
- [ ] S9-02 Live-mode settings (`CF_LIVE_*`), and isolated settings for public live sessions: they never read the deployment's other keys, fallbacks or routing overrides
- [ ] S9-03 Process-wide live accounting (`services/live.py`):
  - run slots shared by every browser session (concurrency);
  - a daily cap on owner-key runs and a per-session cap;
  - per-run caps on calls, tokens and wall time, applied by tightening the plan.
- [ ] S9-04 Live mode in the app:
  - the owner's key from Streamlit secrets, offered only when the owner opts in;
  - a visitor's own Groq key: masked input, held in session memory only, never logged, with a "Forget my key" control;
  - remaining runs shown; the Evaluation Lab runs live only in local mode.
- [ ] S9-05 Safe errors and logs:
  - the app configures redacting logging;
  - background-run errors are redacted;
  - a test proves no key reaches logs, errors, run records or exports.
- [ ] S9-06 Injection hardening (D6):
  - framing neutralises marker phrases, chat-template tokens, and invisible or bidirectional characters;
  - canary checks normalise text first;
  - the injection dataset grows with new attacks (brand memory, hidden characters, chat tokens, bare marker phrases, an exfiltration link, another language);
  - the offline baseline is refreshed.
- [ ] S9-07 `pip-audit` in CI (T13)
- [ ] S9-08 Tests: caps enforced, concurrency across sessions, visitor-key handling, secrets kept out of logs, injection framing
- [ ] S9-09 Docs: SECURITY (section 5 and the threat table), an ADR, deployment secrets, `.env.example`, README, CHANGELOG, status and session log
- [ ] S9-10 Push, reboot the public demo, and verify demo mode and the live-mode controls in the browser. A live run on the owner's key needs the owner to add the key to the app's Streamlit secrets.
- [ ] S9-11 OpenTelemetry (optional in the roadmap): decide, and record the decision

## Session 10

Scope and acceptance criteria are in [ROADMAP.md](ROADMAP.md). Granular tasks are written at the start of the session.
