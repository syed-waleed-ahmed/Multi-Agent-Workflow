# Session Log

One entry per session, newest last. Each entry records what changed, what was verified, what is still open, and how the next session starts.

---

## Session 0 - Audit and architectural planning (2026-11-10)

**Goal:** establish the baseline, verify the free-tier landscape, and design the 2.0 architecture and plan. No implementation.

### What was done

- Read every source module, test fixture, eval file and doc, plus the CI configuration and the packaging setup.
- Measured the baseline gates. All pass: 82 tests, 94% branch coverage, mypy strict clean, ruff clean, offline eval 10/10, wheel and sdist build.
- Wrote an offline probe script (kept outside the repository) and reproduced defects D1-D7. D8 is recorded as a risk and D9 comes from code reading. See [PROJECT_AUDIT.md](PROJECT_AUDIT.md).
- Validated all 14 weaknesses listed in the master prompt: 13 confirmed, and 1 partial (observability).
- Verified free-tier facts against official documentation:
  - LLM providers
  - hosting
  - databases
  - research sources

  Material findings:
  - Gemini's free-tier terms (data use; EEA/CH/UK restriction)
  - Hugging Face Spaces is no longer free for Docker apps
  - Cloudflare Workers' 10 ms CPU limit
  - Google Custom Search is closed to new customers
  - Bing Search API is retired
  - Brave Search requires a card
- Designed the target architecture and wrote the planning documents.

### Files created

- `docs/PROJECT_AUDIT.md`
- `docs/ROADMAP.md`
- `docs/TECH_STACK.md`
- `docs/FREE_TIER_POLICY.md`
- `docs/SECURITY.md`
- `docs/AGENT_CATALOG.md`
- `docs/DECISIONS.md`
- `docs/BUILD_STATUS.md`
- `docs/SESSION_LOG.md`
- `docs/TASK_BOARD.md`

### Files modified

- `docs/ARCHITECTURE.md`: added a document-status section and Part II (the target architecture). The v1 content (Part I) is unchanged.
- `README.md`: the Roadmap section now points to `docs/ROADMAP.md`.
- `CHANGELOG.md`: added an `Unreleased` entry for the planning docs.

No source code, tests or configuration were changed.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (29 files) |
| `mypy src` | Pass (17 files) |
| `pytest` (+ coverage) | 82 passed, 94% |
| `python evals/run_eval.py --offline` | 10/10 |
| `python -m build` | OK |
| `campaign-forge --version` | `campaign-forge 1.1.0` |

### Not done (deliberately)

- **Live eval baseline.** It would spend the owner's Groq quota, so it needs approval first (task S1-02).
- **Commits.** None were made; the master prompt says to commit only "when authorized".
- **Implementation of any 2.0 component.** Waiting for the owner's review, per the master prompt.

### Open issues and risks

- Several provider facts are marked UNVERIFIED in [FREE_TIER_POLICY.md](FREE_TIER_POLICY.md): the exact Groq free limits and its data-use terms, the exact Gemini RPD per model, conflicting Mistral training terms, and Supabase's card requirement. Session 1 re-checks the provider facts.
- Four ADRs (012, 013, 018, 019) deviate from the master prompt and need the owner's approval.
- How Streamlit Community Cloud installs a local package with extras has not been verified yet (Session 6).

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions". In short: confirm the ADR decisions, re-run the gates, then start Session 1 at task S1-01.

---

## Session 1 - Core foundation, providers, defect fixes (2026-11-10)

**Goal:** build the extensible 2.0 foundation, fix the audited defects, and keep v1 behaviour intact.

**Owner input at start:** "Continue". This was read as approval of the Session 0 plan, including ADR-012, 013, 018 and 019, which are now Accepted. It was **not** read as approval to spend API quota or to commit; both remain open.

### What was done

- **Domain** (`campaign_forge.domain`): `Artifact` (JSON payload with typed round-trip), `ProducedBy`, `Claim`/`Basis`, tool-only `Evidence` with `unknown_evidence_ids()`, `TokenUsage`, `Scope`.
- **Core** (`campaign_forge.core`):
  - `AgentSpec`, `AgentLimits`, `InputSpec`, `AgentOutput`, and the `Agent` protocol
  - `AgentRegistry` with decorator registration, enable/disable, and entry-point discovery (strict or lenient)
  - a thread-safe `Budget` that projects worst-case token use
  - `EventBus` with subscriber isolation
  - `RunContext`, the single budgeted, routed and observable gateway for agent LLM calls
  - `CancellationToken`
- **Providers** (`campaign_forge.providers`):
  - the `Provider` protocol and `BaseProvider`
  - `OpenAICompatibleProvider`, refactored from v1 `LLMClient`, with normalised errors, quota versus rate-limit classification, usage capture, redacted messages, and long retry hints surfaced instead of slept through
  - profiles for Groq, Gemini, Mistral, Cloudflare, OpenAI (paid) and the fake
  - `FakeProvider`
  - `run_structured` with conversational repair
  - `ProviderRouter` with precedence rules, `FREE_ONLY`, and explicit, recorded fallback
- **Security** (`campaign_forge.security`): untrusted-content framing with content-hash tags and a system-prompt policy; `RedactingFilter` installed on the package log handler; keys moved to `SecretStr`.
- **v1 changes:**
  - `LLMClient` is now a facade over the new provider, with `for_agent()` (D8) and `complete_with_usage()`.
  - Every v1 agent frames its inputs (D6, first step).
  - Art Director repair (D3); Manager structural check and repair (D4).
  - `run_batch` callback isolation and worker validation (D1, D5).
  - The CLI validates `--workers` (D1).
  - `apply_overrides()` and `reload_settings()` (D1, D2).
  - The eval grounding fix (D7).
- **Tooling:**
  - `tests/security/test_architecture.py`, enforcing import layers and forbidden calls
  - `scripts/provider_smoke.py`, a manual live check that defaults to a dry run
  - Python 3.13 in the CI matrix
- **Found during the session:**
  - **D10:** fresh installs resolve `openai` 3.x, whose new transport breaks test collection. Pinned `openai>=1.30,<3` (ADR-021) and verified on a fresh Python 3.11 venv.
  - **A router bug, caught by its own test and fixed:** a model configured for one provider leaked into a run override that switched provider.

### Files created

- `src/campaign_forge/domain/`: `__init__.py`, `artifacts.py`, `provenance.py`, `scope.py`, `usage.py`
- `src/campaign_forge/core/`: `__init__.py`, `agent.py`, `budget.py`, `context.py`, `events.py`, `registry.py`
- `src/campaign_forge/providers/`: `__init__.py`, `base.py`, `fake.py`, `openai_compat.py`, `profiles.py`, `retry.py`, `router.py`, `structured.py`
- `src/campaign_forge/security/`: `__init__.py`, `redaction.py`, `untrusted.py`
- `tests/`: `test_core.py`, `test_defects.py`, `test_domain.py`, `test_providers.py`, `test_router.py`
- `tests/security/`: `__init__.py`, `test_architecture.py`, `test_prompt_hygiene.py`
- `scripts/provider_smoke.py`

### Files modified

- **Source:**
  - `src/campaign_forge/`: `__init__.py`, `config.py`, `exceptions.py`, `llm.py`, `logging_config.py`, `workflow.py`, `cli.py`
  - `src/campaign_forge/agents/`: `base.py`, `research.py`, `copywriter.py`, `art_director.py`, `manager.py`
- **Tests and evals:** `tests/conftest.py`, `evals/run_eval.py`.
- **Configuration:** `pyproject.toml`, `requirements.txt`, `.env.example`, `.github/workflows/ci.yml`.
- **Documentation:**
  - root: `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`
  - `docs/`: `ARCHITECTURE.md`, `DECISIONS.md`, `FREE_TIER_POLICY.md`, `SECURITY.md`, `TASK_BOARD.md`, `BUILD_STATUS.md`, this log

### Intentional test changes (ADR-001)

- `tests/conftest.py`: `MANAGER_TEXT` changed from a two-section stub to a realistic six-section brief. The new Manager check (D4) correctly rejects the stub as degenerate. **No test assertion changed;** all 82 v1 test functions pass unmodified.
- `evals/run_eval.py`: the offline fake's Manager output gained the six sections for the same reason, and `_grounded` was fixed (D7).

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass |
| `mypy src` (strict) | Pass, 39 files (Python 3.10 and 3.11) |
| `pytest --cov=campaign_forge` | 291 passed, 98% branch coverage (Python 3.10) |
| `pytest` in a fresh Python 3.11 venv, `openai` 2.54.0 | All passed |
| `python evals/run_eval.py --offline --min-pass-rate 1.0` | 10/10 (Python 3.10 and 3.11) |
| `python scripts/provider_smoke.py` (dry run) | Only Groq is configured; no network calls |
| `campaign-forge batch ... --workers -1` | Clean argparse error, exit code 2 |

### Not done

- **S1-02 live eval baseline:** needs owner approval to spend Groq quota.
- **S1-01 live alias check** (`scripts/provider_smoke.py --live --models`): needs approval. The free-tier facts themselves were verified the same day, in Session 0.
- **Python 3.12 and 3.13 test runs:** CI only (not installed locally).
- **Commits:** none.

### Open issues

- The v1 `LLMClient` path is not yet covered by `FREE_ONLY` (ADR-020). Session 2 closes this.
- Model ids for Gemini, Mistral and Cloudflare are deliberately not hard-coded. Users must set `CF_PROVIDER_MODELS`.
- Mistral's base URL and JSON mode, and Gemini's JSON-mode behaviour, are untested against the live services. The smoke script covers them once keys exist.

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 2 - Orchestration engine (2026-11-10)

**Goal:** declarative, bounded, resumable multi-agent execution, with the v1 API moved onto it.

**Owner input at start:** "Continue and start while fixing anything you find as error." This was read as: start Session 2 and fix defects found along the way. It was **not** read as approval to spend API quota or to commit; both are still pending.

### What was done

- **Definitions** (`orchestration/definition.py`):
  - JSON-serialisable `WorkflowDefinition`, `StepDefinition`, `Condition` (declarative, never `eval`-ed) and `ReviewSpec`
  - structural validation (references, self-dependencies, cycles with the cycle path, disabled-but-required steps)
  - registry validation (agents, bound inputs, artifact kinds, review wiring) that reports every problem at once
  - `load_definition()`
- **State** (`orchestration/state.py`): run and step states; a checked lifecycle (the T6 diagram plus `FAILED -> RUNNING`/`CANCELLED`); `RunRecord` with artifacts, events (capped), usage, outputs and summary.
- **Stores** (`orchestration/store.py`): `InMemoryRunStore` and `JsonFileRunStore`. The file store writes atomically, encodes scope directories, and rejects run ids that could traverse paths.
- **Engine** (`orchestration/engine.py`):
  - ready-set scheduling, with parallel independent steps (up to `max_parallel`)
  - failure isolation and partial results
  - conditions, retries, per-step timeouts, and the per-invocation `max_calls` limit
  - the bounded critic loop
  - approval gates (approve, edit, reject)
  - cooperative cancellation
  - pause on quota, rate-limit or budget errors
  - fingerprint-based idempotent resume, with forced re-runs
  - engine-wide event stream
- **Planners and templates:** `RuleBasedPlanner`; `LLMPlanner`, which may only choose a template and disable optional steps, is validated, and falls back with a recorded reason; the `classic-campaign` template; `default_registry()`.
- **v1 on the engine:**
  - `agents/legacy.py` wraps the four v1 agents as `legacy.*`, with every call going through `RunContext`.
  - `CampaignWorkflow` is a facade over the engine.
  - `providers/compat.py` adapts injected v1 clients.
  - v1 override keys apply to the legacy ids.
  - `FREE_ONLY` now covers the v1 commands (ADR-023, superseding ADR-020).
- **CLI:**
  - new commands in `cli_runs.py`: `agents list`, `workflows list|show|validate`, `execute`, `runs list|show|resume|approve|cancel`
  - runs checkpointed to `CF_RUNS_DIR`; exit code `3` means the run needs action
  - D9 fixed: `batch` now saves incrementally
- **Supporting changes:** `RunContext.for_step()` and `for_invocation()`; child cancellation tokens; `CallCounter`; `ProviderRouter.add_provider()`; the `quality.review@1` contract (`domain/review.py`); and new exceptions (`WorkflowValidationError`, `RunStateError`, `RunNotFoundError`, `StepTimeoutError`).
- **S2-17 `openai` 3.x evaluation** (ADR-025):
  - the suite and mypy pass on 3.49.0;
  - genuine `httpx2` errors map correctly;
  - the pin is kept until a live smoke test;
  - the provider tests are now transport-agnostic (`tests/sdk_http.py`).

### Errors found and fixed during the session

1. **mypy (strict):** a method named `list` shadowed the builtin inside the engine's annotations. Renamed to `list_runs` in the engine and stores.
2. **Rich markup in the CLI:** dynamic text such as `[copywriter] ...` was treated as markup and silently removed. The v1 batch summary had the same problem. All dynamic text is now escaped, which also prevents terminal-markup injection from model output.
3. **Truncated ids in CLI tables:** Rich truncated agent and run ids at narrow widths. Id columns no longer wrap; descriptive columns fold instead.
4. **Ambiguous semantics surfaced by tests:** list `contains` (element equality) and call accounting (refused calls count). Both are clarified and recorded in ADR-022.
5. **Tooling slip:** a scripted edit duplicated a docstring line in `core/budget.py`. Caught and fixed.

### Files created

- `src/campaign_forge/orchestration/`: `__init__.py`, `definition.py`, `engine.py`, `planner.py`, `state.py`, `store.py`, `templates.py`
- `src/campaign_forge/`: `agents/legacy.py`, `domain/review.py`, `providers/compat.py`, `cli_runs.py`
- `tests/`: `orchestration_helpers.py`, `sdk_http.py`, `test_orchestration.py`, `test_engine.py`, `test_cli_engine.py`, `test_workflow_facade.py`

### Files modified

- **Source:**
  - `src/campaign_forge/`: `exceptions.py`, `config.py` (`runs_dir`), `workflow.py`, `cli.py`
  - subpackages: `core/context.py`, `core/budget.py`, `providers/router.py`, `agents/base.py`, `domain/__init__.py`
- **Tests:** `tests/test_defects.py` (D9), `tests/test_providers.py` (transport-agnostic), `tests/security/test_architecture.py` (orchestration layer).
- **Documentation:**
  - root: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `.env.example`
  - `docs/`: `ARCHITECTURE.md`, `DECISIONS.md`, `FREE_TIER_POLICY.md`, `SECURITY.md`, `TASK_BOARD.md`, `BUILD_STATUS.md`, this log

### v1 compatibility

All 82 v1 test functions pass unmodified through the engine-backed facade. No v1 test or fixture changed in this session.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass (77 files) |
| `mypy src` (strict) | Pass, 50 files (Python 3.10 and 3.11; also with `openai` 3.49) |
| `pytest --cov=campaign_forge --cov-fail-under=90` | 387 passed, 97.9% branch coverage (Python 3.10) |
| `pytest` on Python 3.11, fresh resolve (`openai` 2.54.0) | 387 passed |
| `pytest` on Python 3.11 with `openai` 3.49.0 | All passed (the provider tests also pass without `httpx`) |
| `python evals/run_eval.py --offline --min-pass-rate 1.0` | 10/10 (Python 3.10 and 3.11) |
| CLI smoke test with the `fake` provider (`execute`, `runs list/show/resume/cancel`, path-traversal id) | Behaved as specified |
| `python -m build` | wheel and sdist OK |

### Not done

- **Tagging `2.0.0a1`:** needs commits, which are not authorised.
- **Live tasks S1-01, S1-02 and the ADR-025 smoke test on `openai` 3.x:** need approval to spend Groq quota.

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 3 - Agent library, wave 1 (2026-11-10)

**Goal:** 11 functional agents, the critic loop, and the wave-1 workflow templates. This completes Milestone M1.

**Owner input at start:** "Continue and start". This was read as: start Session 3. It was **not** read as approval to spend API quota or to commit; both are still pending.

### What was done

- **Output contracts** (`agents/schemas.py`): 11 artifact kinds, with validators that hold the content rules.
  - Code computes the effort shares (largest remainder, summing to 100), calendar weeks, ordering, hashtag normalisation, and platform-limit checks (hard limits enforced, recommended limits as warnings).
  - Every contract has `digest()`, and the deliverables also have `to_markdown()`.
- **Agent library** (`agents/library/`):
  - `StructuredAgent` base: framed context, feedback section, structured call with bounded repair, provenance and claims.
  - Research and strategy: `audience_research`, `market_research` (assumptions mode with labeled claims), `positioning`, `marketing_strategist`.
  - Content and creative: `copywriter`, `ad_copy` (code-chosen channels), `social_media`, `email_marketing`, `art_director`.
  - `critic`: deterministic checks, a cap on critical failure, and rubric scores mapped by code.
  - `campaign_reporter`: validated narrative, plus deliverables, assumptions and missing items rendered by code.
- **Evaluation** (`evaluation/`): deterministic checks (schema, injection echo, grounding, labeled claims, report sections, limit warnings), rubrics, and a deterministic offline responder for every agent.
- **Templates:**
  - `full-campaign`: parallel research, reviewed copy and ads, email conditional on channels, optional content steps;
  - `ad-pack`, `social-calendar`, `email-sequence`;
  - `full-campaign` is now the planner default.
- **Engine:** a critic now also receives the reviewed step's inputs it declares (for example the brief). The validator checks critic input wiring.
- **Evals:** `--workflow <template>` mode with per-output checks, failed optional steps and low review scores reported as warnings, and call and token metrics. The CI runs the offline `full-campaign` eval.
- **CLI:** `CF_PROVIDER=fake` runs any workflow key-free on the offline responder, labeled SIMULATED, and the report renders as the final brief.

### Errors found and fixed during the session

1. **Eval live mode crash (introduced in Session 2).** `evals/run_eval.py` read `workflow._llm.model`, which the engine-backed facade no longer has. Offline runs never reached that line. Fixed: the model is read from settings, and `apply_overrides` is used for `--model`.
2. **Many-channel briefs** (found by the eval corpus). A brief may list 25 channels, but the plan contract capped them at 8, and ads would exceed 12 variants. The plan now accepts up to `MAX_CHANNELS`, and code picks at most 6 ad channels (ad-capable first) for both the prompt and the offline responder.
3. **Eval blind spot.** A failed optional step went unreported. It now appears as a warning.
4. **Test and tooling slips, all caught by the gates:**
   - a test helper built research artifacts without claims;
   - two test expectations were wrong (an email step skipped by its condition; the template list order);
   - a few scripted edits missed their anchors after reformatting and were re-applied;
   - non-ASCII test literals were re-escaped.

### Files created

- `src/campaign_forge/agents/schemas.py`
- `src/campaign_forge/agents/library/`: `__init__.py`, `common.py`, `research.py`, `strategy.py`, `content.py`, `creative.py`, `quality.py`, `reporting.py`
- `src/campaign_forge/evaluation/`: `__init__.py`, `checks.py`, `offline.py`
- `tests/`: `test_library_agents.py`, `test_full_campaign.py`

### Files modified

- **Source:**
  - `src/campaign_forge/orchestration/`: `templates.py`, `engine.py` (critic context), `definition.py` (critic wiring validation)
  - `src/campaign_forge/agents/legacy.py` (shared `BRIEF_KIND`)
  - `src/campaign_forge/cli_runs.py` (fake mode, report rendering)
- **Evals and CI:** `evals/run_eval.py`, `evals/README.md`, `.github/workflows/ci.yml`.
- **Tests:** `tests/test_cli_engine.py`, `tests/test_orchestration.py` (new default template), `tests/security/test_architecture.py` (evaluation layer).
- **Documentation:**
  - root: `README.md`, `CHANGELOG.md`
  - `docs/`: `AGENT_CATALOG.md`, `ARCHITECTURE.md`, `DECISIONS.md` (ADR-026, ADR-027), `ROADMAP.md`, `SECURITY.md`, `TASK_BOARD.md`, `BUILD_STATUS.md`, this log

### v1 compatibility

All 82 v1 test functions still pass unmodified.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .` and `ruff format --check .` | Pass (91 files) |
| `mypy src` (strict) | Pass, 62 files (Python 3.10 and 3.11) |
| `pytest --cov=campaign_forge --cov-fail-under=90` | 464 passed, 98.1% branch coverage (Python 3.10); 464 passed on Python 3.11 |
| Offline evals: v1 pipeline, `full-campaign`, `ad-pack`, `social-calendar`, `email-sequence` | 10/10 each |
| `CF_PROVIDER=fake campaign-forge execute full-campaign --brief ...` | Completed in 12 calls, labeled SIMULATED, exit 0 |
| `python -m build` | wheel and sdist OK |

### Not done

- **S3-05:** the live `full-campaign` run on the Groq free tier, and the `2.0.0b1` tag. They need approval to spend quota and to commit.
- **Real model quality is unmeasured.** Everything above was measured offline with the simulated responder.

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 4 - Tools and evidence-backed research (2026-11-10)

**Goal:** research grounded in retrieved evidence, with citations that can be checked, and no way for a model to make the system fetch a URL.

**Owner input:** "do it yourself and continue", in reply to the requests to approve live Groq runs and commits. Read as approval for both, on a feature branch.

### What was done

- **Tools** (`tools/`):
  - the tool protocol, per-agent allowlists, a tool-call budget, and a run-level evidence store;
  - Wikipedia search (keyless, the default), optional Tavily, `SafeFetcher` for user-supplied URLs only, and user sources.
- **SSRF guard** (`security/url_safety.py`): http(s) only; no embedded credentials; ports 80/443; every resolved address must be public (IPv4-mapped IPv6 included). It is re-checked on every redirect, and robots.txt and the size, time and type caps apply.
- **Evidence research.**
  - The research agents plan queries, retrieve evidence, then synthesise. Citations are evidence ids, validated against the evidence actually retrieved, and artifacts embed what they cite.
  - New agents: `competitor_research` (named competitors only) and `fact_checker`.
  - New `competitor-report` template.
- **Live verification, first runs.**
  - Run 1 found D11 (retired default model, ADR-028) and D12-D15: output cap, "Request too large", truncation, step timeouts (ADR-030).
  - Run 2 found that v1 answers need about 940 tokens. The legacy `max_tokens` was raised from 800 to 1,000. Run 2 also found that parallel requests starve each other under the output cap (D16, fixed in Session 5).
- **Commits:**
  - `87e071c` (Sessions 1-3) and `fc0398e` (Session 4) on `feature/campaign-forge-2.0`, fast-forwarded to `main` and pushed at the owner's request.
  - `b15c8f7`: the repository URLs pointed at a repository that does not exist.
  - `45c7401` and `335226a`: two CI-only failures, from an unpinned ruff and a timestamp comparison that depends on clock resolution. CI is green on Python 3.10-3.13.

### Tests run

532 tests at the Session 4 commit, plus the CI matrix. Measured gates and live results are in [BUILD_STATUS.md](BUILD_STATUS.md).

---

## Session 5 - Memory and persistence (2026-11-10)

**Goal:** brand profiles, campaign history and notes that persist, stay in one tenant's scope, can be searched and deleted, and never store a secret. Plus the brand agents, which use them.

**Owner input:** "Continue with the commit and push to the main branch and start the next phases".

### What was done

- **Memory** (`memory/`, layer 2):
  - the `MemoryStore` protocol (scope on every call) and `SqliteMemoryStore` on the stdlib `sqlite3`;
  - FTS5 with BM25 ranking, and a LIKE fallback with a stale-index rebuild;
  - redaction of every string before it is written;
  - per-scope limits, `delete_scope`, and a schema version guard (ADR-031).
- **Runs and history.** `SqliteRunStore` (`CF_RUN_STORE=sqlite`) passes the run-store contract tests. `orchestration/history.py` remembers completed runs as `campaign` documents keyed by run id.
- **Brand.**
  - `domain/brand.py`: `BrandProfile` (`brand.profile@1`), `brand_slug`, and whole-word `term_counts`.
  - New agents: `brand_strategist` and `brand_consistency` (terms counted by code; the model judges only the tone; code decides pass).
  - The copywriter accepts a brand: banned terms become contract violations through validation context, so they are repaired. The critic sees the brand.
- **Templates.**
  - New `brand-kit`.
  - Every content template accepts an optional `brand`. `full-campaign` adds `brand_check`, which runs only when a brand is given, and its report renders the profile and the check (they are never listed as "not produced").
  - Validation now lets a required agent input bind an optional workflow input only behind an `exists` condition on it.
  - The scheduler evaluates conditions before missing inputs, so the skip reason is accurate.
- **CLI:**
  - `memory brand save|list|show|delete`, `memory note`, `memory search`, `memory list`;
  - `memory forget` (refuses without `--yes`; `--runs` also deletes runs);
  - `execute --brand`;
  - completed runs are remembered after `execute`, `resume` and `approve`.
- **Free-tier fixes from live run 3 (ADR-032):**
  - D16: Groq requests are serialised (`max_concurrency` 1, `CF_PROVIDER_CONCURRENCY`).
  - D17: the v1 manager (2,000 tokens) and the reporter use the `strong` class (`openai/gpt-oss-120b`), verified by a live probe that returned 2,500 tokens.
  - D18: auto-resume (`run_until_settled`, `execute --auto-resume N`) waits out rate limits only, never an exhausted quota.

### Live run 3 (measured, not passed)

- **v1 eval** (2 briefs): `baseline-ecommerce` failed. The research answer truncated at 1,000 tokens and recovered with the concise re-ask, then the manager truncated, even after its re-ask (D17). `prompt-injection` hit the daily quota: 200,000 tokens per day, 198,869 used.
- **`full-campaign`:** paused, quota exhausted, after 11 admitted calls. Only one call was answered (961 tokens), and the rest were refused by the quota. Three auto-resumes crawled (D18).

### Errors found and fixed during the session

1. **Heredoc escaping.** The Bash heredoc trap from Session 3 struck twice: `\n` arrived as a real newline. The affected edits were redone with the Edit tool or with scripts written to files.
2. **A LIKE-fallback test assumption.** Substring matching made `or` match inside "words". The behaviour is now documented, and the test text changed.
3. **CI failure on Python 3.13 only.** Python 3.13 warns about sqlite connections that are garbage-collected unclosed, and the suite treats warnings as errors. The leaks surfaced in unrelated tests.
   - Fix: `SqliteRunStore` now opens a connection per operation, and `SqliteMemoryStore` closes its connection when its constructor fails.
   - An autouse fixture now fails any test that leaves a package connection open. It catches leaks on every Python version, and a deliberate leak was confirmed to fail it.
4. **Test expectations.**
   - `full-campaign` now has a `brand_check` step that is skipped without a brand.
   - The registry has 15 agents.
   - The kind-to-responder map excludes agents whose responder is not their payload.
   - A completed run cannot move to failed, so the test copies the record instead.
   - capsys buffers had to be drained between CLI calls.

### Files created

- `src/campaign_forge/memory/` (`__init__.py`, `base.py`, `sqlite.py`)
- `src/campaign_forge/domain/brand.py`, `src/campaign_forge/agents/library/brand.py`
- `src/campaign_forge/orchestration/history.py`, `src/campaign_forge/cli_memory.py`
- `tests/test_memory.py`, `tests/test_brand.py`

### Tests run

| Command | Result |
| --- | --- |
| `ruff check`, `ruff format --check` (src, tests, evals) | Pass |
| `mypy src` (strict, 78 files) | Pass |
| `pytest --cov=campaign_forge` | 594 passed, 98% branch coverage |
| Offline evals: v1, `full-campaign`, `brand-kit`, `competitor-report` | 10/10 each |

### Not done

- **The Postgres (Supabase) memory store:** deferred. No test database is available (ADR-031).
- **Live acceptance (S1-02, S3-05):** waits for the daily quota to refill.

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 6 - Streamlit application (2026-11-10)

**Goal:** a web app over the engine that starts with no keys, previews and approves every plan, shows each run step by step, and downloads the results. Deployment comes later.

**Owner input:** "start the next phases" (the same instruction as Session 5).

### What was done

- **Exports** (`exports/`): a run as Markdown (the report, the v1 final brief, or every output), as JSON (the full record with a `simulated` flag), and as a standalone HTML page (markdown-it-py with raw HTML disabled; `javascript:` links refused).
- **No chain of thought.** `parsing.strip_reasoning` removes inline `<think>`, `<thinking>` and `<reasoning>` blocks, closed or cut off, from every provider response before parsing or storage.
- **App logic without Streamlit** (`ui/session.py`, `ui/views.py`):
  - demo and local sessions;
  - background runs with error capture;
  - the workflow graph as Graphviz DOT (inputs, steps coloured by status, conditional edges);
  - plan, step, run, claim, evidence and agent rows;
  - plan customisation, input parsers, and health rows that show keys only as set or not set.
- **Pages** (`ui/pages.py`, plus `app/streamlit_app.py` and 16 three-line files in `app/views/`), covering all of ADR-018:
  - New Run: form or upload, brand, notes and files, plan graph and table, skip optional steps, budget, "Approve and run".
  - Run Inspector: live refresh in local mode; graph, steps, outputs with claims, evidence and provenance; approvals, resume, cancel, downloads; save a run's brand.
  - History: runs, campaign history search, notes. Brand Memory: list, show, download, delete, create from a form, a file or a run.
  - Agents, Workflow Builder (template or upload, validate, download, hand over to New Run), Evaluation Lab placeholder, Settings and Health.
  - Seven studios over the shared New Run view. SEO and Analytics say plainly that they arrive in Session 8.
- **Packaging and CI:**
  - the `[ui]` extra; `app/requirements.txt` (the app also imports from `src/` without installation);
  - `.streamlit/config.toml` (2 MB uploads, no error details, no usage stats); `.streamlit/secrets.toml` git-ignored;
  - CI installs `[dev,ui]`.
- **Architecture rule:** only `campaign_forge.ui.pages` may import `streamlit`.

### Verified in a real browser

`streamlit run app/streamlit_app.py`, started with no provider keys:
- The dashboard rendered.
- New Run showed the plan graph.
- "Approve and run" completed a simulated `full-campaign` run in 14 calls.
- The inspector showed the SIMULATED notice and the coloured run graph.

Because every step reads the brief, input edges crowded the graph, so they are now drawn lighter.

### Errors found and fixed during the session

1. **The report export used the report's digest** (title and word count) instead of its Markdown. `artifact_markdown` now prefers a `markdown`/`text` field over `digest()`. Found by a test.
2. **The graph omitted inputs that only a condition reads.** Fixed, with a test.
3. **AppTest page tracking.** After a programmatic `st.switch_page`, AppTest keeps rerunning the previous page. Tests re-select the page with a documented `_follow` helper. In a browser the URL follows the switch.
4. **A wrong test assumption:** a Streamlit form keeps its values after submit. That is kept on purpose, so a validation error does not wipe the user's input.
5. **The heredoc trap again:** `\1` in a regex became a control character. The edit was redone with the Edit tool.
6. **CI failures on Python 3.11-3.13 only.** CI resolved Streamlit 1.67.0 and numpy 2.4.6, which do not install on 3.10, so the local 3.10 checks could not see them:
   - Streamlit 1.67 types a select box as possibly `None`, and `st.columns(n)` as an n-tuple. All select boxes now go through `_choose()`, which returns `str | None`.
   - numpy 2.4's stubs use the 3.12 `type` statement, which mypy rejects when checking for 3.10. mypy no longer follows numpy, pandas or pyarrow. `follow_imports_for_stubs` is needed for stub files: with it, 0 numpy stubs are parsed, down from 109.
   - Both fixes were reproduced and verified in a local Python 3.11 environment that matches CI. CI is green on 3.10-3.13 at `f353cc2`.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | Pass |
| `mypy src` (strict, 83 files, Streamlit included) | Pass |
| `pytest --cov=campaign_forge` | 653 passed, 97% branch coverage. `ui/pages.py` is at 88%: upload and error branches are not driven by AppTest. |
| Offline evals: v1, `full-campaign` | 10/10 each |
| Wheel build | OK; includes `ui`, `exports`, `memory` |

### Not done

- **S6-12, deployment:** done on 2026-11-11 at the owner's request, through the owner's signed-in Streamlit account. The settings were: repository `syed-waleed-ahmed/Multi-Agent-Workflow`, branch `main`, entry `app/streamlit_app.py`, Python 3.13 (the newest version CI tests; Streamlit's default was 3.14), no secrets. **Live at https://campaign-forge.streamlit.app/.** Smoke test: a simulated `full-campaign` run completed in 14 calls, 11/11 steps, with the report, deliverables, sources and downloads shown.
- **Recorded fixtures for the demo:** replaced by the offline responder until a live run passes (ADR-033).

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 7 - FastAPI service and developer experience (2026-11-11)

**Goal:** the master prompt's HTTP API, with typed models, authentication, limits and Docker, and no business logic in the API layer.

**Owner input:** "Go on and start, do everything with browser, no need of me".

### What was done

- **Service layer** (`services/`, layer 5):
  - `RunService` (background threads, owner scopes, errors raised outside the engine's record, history, a cap on active runs);
  - `Platform` (agents, workflows, runs, providers, status);
  - `WorkflowCatalog` (built-in plus per-scope custom definitions, validated, capped);
  - plan adjustments (skip optional steps; limits can only be lowered).

  `build_engine` moved to `orchestration/factory.py`. The web app's session now delegates to `RunService`.
- **API** (`api/`, layer 6):
  - the 13 master-prompt endpoints, plus approval, workflow and execution listings, with typed models;
  - `X-API-Key` authentication, one tenant per key, closed by default;
  - per-key request and run-start limits and a per-process run cap;
  - one redacted error shape;
  - `python -m campaign_forge.api`.
- **Architecture rules:** the API may import only the service layer and plain types. FastAPI, Starlette and uvicorn stay inside `api/`.
- **Docker:** a non-root image with a healthcheck, and `compose.yaml` (SQLite volume, localhost bind). A CI job builds the image and runs `scripts/api_smoke.py` against the container.
- **Docs:**
  - `docs/API.md`;
  - `docs/openapi.json`, generated by `scripts/export_openapi.py` and checked by a test;
  - `examples/api_client.py`.

### Verified

- **Locally,** `python -m campaign_forge.api` with the simulated provider passed `scripts/api_smoke.py`: health, 401 without a key, status, and a background campaign to completion.
- **In the browser,** the Swagger UI at `/docs` authorized with a test key, and `GET /api/v1/system/status` returned 200.
- **Docker** is not installed on the development machine, so the CI `docker` job is the Docker check.

### Live acceptance (Groq free tier)

- **Run 4:**
  - `full-campaign` **passed** (21 calls, 26,535 tokens, 622 s), with two warnings: the optional social calendar was dropped after truncating at qwen's 1,000-token cap, and the ads review scored 0.69.
  - v1: 1/2. `prompt-injection` passed; `baseline-ecommerce`'s research answer overflowed the cap even after the concise re-ask.
- **D19 fix:** cap-aware routing (ADR-035). Agents whose answer may exceed the default model's per-request cap use the provider's strong model.
- **Run 5:** **v1 2/2, and `full-campaign` 1/1 with every check passing and no warnings** (19 calls, 27,129 tokens, 375 s). This completes S1-02 and S3-05.

### Errors found and fixed during the session

1. **A layering violation.** `services.platform` imported the package root for the version. The API adds the version itself.
2. **Background race.** A background run that crashed before its first checkpoint returned 404. It is now reported as `failed`, with its error.
3. **Inaccurate status.** System status listed the configured research tools even in simulated mode. It now reports `offline_search`, which is what actually runs. Found in the browser.
4. **Test client deprecation.** Starlette 1.7's TestClient warns that it prefers `httpx2`, and installing it was refused. That one deprecation notice is ignored in the pytest config.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | Pass |
| `mypy src` (strict, 94 files) | Pass |
| `pytest --cov=campaign_forge` | 696 passed, 97% branch coverage |
| Offline evals: v1, `full-campaign` | 10/10 each |
| Live evals (run 5) | v1 2/2; `full-campaign` 1/1, no warnings |

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 8 - Agent library wave 2 and Evaluation Lab (2026-11-11)

**Goal:** the remaining catalog agents and templates, plus an Evaluation Lab whose benchmarks come only from real runs.

**Owner input:** "done now start, and use claude in chrome".

### What was done

- **Release `v2.0.0b1`.** The tag is pushed. Creating the GitHub release page was blocked by the session's permission policy, so it is left to the owner.
- **Wave-2 contracts** (`agents/schemas_wave2.py`; shared field types moved to `agents/fields.py`). Code computes:
  - ICE scores and ranks;
  - sample sizes (two-sided, alpha 0.05, power 0.8);
  - CTR, CVR, CPC, CPA and ROAS from a user CSV;
  - SEO and CRO checklists;
  - word counts and calendar weeks.
- **Honesty rules in the contracts:**
  - landing-page testimonials and statistics must be bracketed placeholders;
  - keyword volumes come only from a user CSV;
  - experiment baselines are labeled assumptions unless code takes them from measured rates;
  - a model's safety flag is kept only when its quote is in the deliverable it names.
- **13 agents:**
  - strategy and analytics: `funnel_strategist`, `growth_strategist`, `experiment_designer`, `performance_analyst`, `cro_advisor`;
  - content: `landing_page`, `product_description`, `repurposer`, `blog_writer` (outline-first, then one call per section);
  - SEO: `keyword_research`, `seo_content_planner`, `seo_optimizer`;
  - quality: `safety_reviewer`.

  The library now has 28 agents. Deterministic offline responders were added for all of them.
- **Seven templates:** `seo-content-pack`, `performance-review`, `product-launch`, `landing-page`, `content-repurpose`, `ecommerce-campaign`, `b2b-campaign`. Every studio now opens real templates.
- **Evaluation Lab** (`services/lab.py`):
  - datasets ship in the package: the corpus moved there, and an 8-case injection set was added;
  - reports record date, mode, provider, class defaults and the models that answered;
  - baselines are compared on stable metrics only;
  - `campaign-forge bench` and an app page; CI checks three offline baselines.
- **Benchmarks:** `docs/BENCHMARKS.md` lists real runs only, with their JSON reports in `docs/benchmarks/`.

### Verified

- **In the browser:** the Evaluation Lab ran a simulated corpus benchmark at 10/10 and showed "No regressions against the baseline".
- **Live on Groq's free tier,** one case each, both passing every check:
  - `landing-page`: 12 calls, 13,577 tokens, review score 0.78;
  - `seo-content-pack`: 11 calls, 11,792 tokens.

### Errors found and fixed during the session

1. **Re-validation erased code-set values.** Re-reading a stored artifact re-ran the contract validator without the agent's context, which reset measured baselines and CSV keyword volumes. Each rule now takes its authority from a context key that the agent always passes. Without that key, the stored values are kept, and a model can never set them.
2. **D20: a crashed live benchmark.** Windows refused the JSON store's atomic replace ("Access is denied") while another process held the fresh checkpoint file. The replace now retries briefly and stays atomic. The Lab records any crash as a failed case instead of stopping.
3. **Mislabelled models in benchmark reports.** The report resolved models with the `.env` default `max_tokens`, which triggered cap-aware routing and listed the strong model for every class. Reports now show class defaults (resolved for a short answer) and the models that actually answered.
4. **Wrong baseline accepted.** A baseline for another workflow or dataset was compared silently. It is now refused.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | Pass |
| `mypy src` (strict, 103 files) | Pass |
| `pytest --cov=campaign_forge` | 758 passed, 97% branch coverage |
| Offline evals and benchmarks: every template, corpus and injection | 10/10 and 8/8 |
| Live benchmarks: `landing-page`, `seo-content-pack` | 1/1 each |

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 9 - Security hardening and live mode (2026-11-11)

**Goal:** the public demo's live mode within caps that protect a free quota, keys that never leave the session, the remaining injection hardening (D6), and a dependency audit.

**Owner input:** "done now start, and use claude in chrome". The `v2.0.0rc1` tag was confirmed on GitHub at `4c8b726`.

### What was done

- **Live mode** (ADR-037, `services/live.py`, `ui/session.py`):
  - Groq with a visitor's own key, or with the deployment's key when the owner opts in (`CF_LIVE_OWNER_KEY`);
  - live sessions use **isolated settings** that read no environment, so the deployment's other keys, fallbacks and overrides are unreachable;
  - caps: 25 calls, 30,000 tokens and 20 minutes per run (tightening the plan, and applied again at start); 2 live runs at once per process, through one `RunSlots` shared by every browser session; 3 owner-key runs per session and 5 per UTC day; refused starts are not counted;
  - visitor keys must be Groq-shaped, so key-pattern redaction always matches; they are held only by the live session; "Forget my key" drops it;
  - the Evaluation Lab stays simulated in the public app.
- **Redaction wherever a message is recorded:**
  - the engine (step errors, pause details, events);
  - the run service (background errors);
  - the Lab (case errors, since reports may be committed);
  - tool-failure notes;
  - the app now configures the redacting log handler.
- **D6 hardening.**
  - Before framing, the text loses invisible and bidirectional characters (including Unicode tag characters), bare "BEGIN/END UNTRUSTED DATA" phrases and chat-template tokens.
  - The canary check removes invisible characters and NFKC-normalises before matching.
  - Six new injection cases (14 in all), and the offline baseline was refreshed.
- **`pip-audit` in CI** over the pinned runtime dependencies of every extra.
- **Not done: OpenTelemetry** (optional). It needs new dependencies and an exporter with nowhere free to send it (ADR-037).

### Verified

- **In a real browser, locally, on the owner's key** (the app read it from `.env`; it was never typed or displayed):
  - "Go live on the demo's key" showed the caps and "Live runs left: 3".
  - The `ad-pack` plan showed 30,000 tokens (the template allows 60,000).
  - The run completed on Groq: 4/4 steps, 11 calls, 16,902 tokens. The sidebar then showed 2 runs left.
  - An invalid key was refused without being echoed, and the field cleared.
  - The server log held no key-shaped string. Its rate-limit warnings went through the redacting handler.
- **Public demo after the push:**
  - rebooted;
  - the dashboard links to live mode;
  - Settings offers only the bring-your-own-key form, as expected with no secrets configured.

  A live run on the demo's key there waits for the owner's Streamlit secrets.
- **CI** green at `1d42c3f`: Python 3.10-3.13, Docker, and `pip-audit` ("No known vulnerabilities found").

### Errors found and fixed during the session

1. **A key inside an unexpected error reached the run record.** Found by the new no-leak test. The provider redacted errors from the OpenAI SDK, but the engine stored any other exception's text as it was. That record is shown in the inspector, exported, and saved in the CLI's checkpoints. The engine, the run service, the Lab and tool notes now redact every message they record.
2. **A report quoting the user's own brand profile tripped the injection canaries.** Found by the new brand-memory case. The report's deliverables appendix renders the brand profile verbatim, so injected text in the profile looked like an obeyed instruction. The canary check now reads only the report's narrative. The appendices are checked as their own artifacts.
3. **Passing the deployment's model through pinned it.** Found in the browser: the health page showed qwen for the strong class. Setting `model` explicitly marks it as the user's choice, which turns off cap-aware routing (ADR-035), so long live answers would truncate again (D19). Only settings the deployment set explicitly now carry over.
4. **The health page showed the strong model for every class.** It resolved models with the configured `max_tokens`, which triggers cap-aware routing. It now shows class defaults, as the Lab has since Session 8.
5. **The app never configured logging.** Package warnings reached the server log through Python's last-resort handler, unredacted. The app now configures the redacting handler once per process.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | Pass |
| `mypy src` (strict, 104 files; also on Python 3.11 with Streamlit 1.67) | Pass |
| `pytest --cov=campaign_forge` | 792 passed, 97% branch coverage |
| Offline evals and benchmarks | 10/10, 10/10, and against baselines: corpus 10/10, injection 14/14, SEO 10/10 |
| Live, local browser, owner's key: `ad-pack` | Completed, 11 calls, 16,902 tokens |

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".

---

## Session 10 - Release and GitHub presentation (2026-11-11)

**Goal:** a repository and demo a recruiter can clone, understand, test and try; a final live smoke test; release `2.0.0`.

**Owner input:** "Do everything for me, I will click submit only if needed".

### What was done

- **README rewritten** around the 20 required sections. It has six Mermaid diagrams: the system, the session lifecycle, providers, deployment, agent orchestration, and campaign execution. All six render on GitHub (checked in the browser).
- **Example gallery** (`docs/EXAMPLES.md`):
  - an unedited live campaign brief from Groq's free tier, including its failed optional step;
  - the simulated version of the same brief;
  - the planner's live choices;
  - six screenshots of the public demo.

  Typographic characters in the live output were written as ASCII, and the file says so.
- **`docs/DEPLOYMENT.md`:** Streamlit Community Cloud step by step, including secrets (written as strings) and the reboot after a code push; free-plan limitations; local and Docker alternatives.
- **Community files:** issue templates (bug, feature) and a pull-request template; `CODE_OF_CONDUCT.md` (adapted from the Contributor Covenant 2.1); a root `SECURITY.md`; an updated `CONTRIBUTING.md`.
- **The planner reaches the CLI:** `campaign-forge plan` and `execute auto`. The planners existed and were tested, but no interface used them, so "Planner assigns tasks" was only partly met.
- **Release `2.0.0`:** version, the Production/Stable classifier, the changelog and the OpenAPI schema.
- **Definition-of-done audit:** recorded in BUILD_STATUS, with evidence for each criterion.

### Verified

- **Final live smoke test:** `campaign-forge execute full-campaign` on Groq's free tier, run `run-a4ab55169a58`.
  - The run completed, and every critical check passed.
  - 18 calls, 28,592 tokens, 234 s; critic review 0.75.
  - Warning: the optional ads step failed, because a Google Ads headline was 37 characters (limit 30) after one repair.
- **Live planner:**
  - the four-channel gallery brief got `full-campaign`;
  - an email-only win-back brief got `email-sequence`;
  - each plan came with a stated reason.
- **Public demo after the release push:** rebooted. It shows "Campaign Forge 2.0.0", and a simulated `full-campaign` run completed (11/11 steps).
- **CI** green at `74dd7f2`.

### Errors found and fixed during the session

1. **D21: the CLI crashed after a successful live run.** On Windows with output redirected to a file, the console encoding is cp1252. The model's brief contained a non-breaking hyphen (U+2011), and printing it raised `UnicodeEncodeError`, so `execute` exited 1. The run record was safe in its checkpoint. The CLI now replaces unencodable characters in the terminal copy only. A regression test reproduces the crash without the fix.
2. **The planner was unreachable** from every interface (see "What was done").

### Blocked, and left to the owner

The session's permission policy blocks tags, GitHub releases and repository-settings changes. These were prepared in the browser for the owner's click:
- the `v2.0.0` release, which creates the tag on publish;
- the `v2.0.0rc1` and `v2.0.0b1` pre-release pages;
- the repository's About text.

Entering an API key into a field is also prohibited for the assistant. So the Streamlit secrets were pre-filled except the key, which the owner pastes before choosing Save.

### Tests run

| Command | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | Pass |
| `mypy src` (strict, 104 files; also on Python 3.11 with Streamlit 1.67) | Pass |
| `pytest --cov=campaign_forge` | 794 passed, 97% branch coverage |
| Offline evals and Lab baselines | 10/10, 10/10; corpus 10/10, injection 14/14, SEO 10/10 |
| Live: `full-campaign` (CLI), planner on two briefs | Completed (1 warning); 2 plans |

### Next session: starting instructions

See [BUILD_STATUS.md](BUILD_STATUS.md), "Next session: starting instructions".
