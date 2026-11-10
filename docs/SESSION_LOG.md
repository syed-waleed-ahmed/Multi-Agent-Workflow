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
